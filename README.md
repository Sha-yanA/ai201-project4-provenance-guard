# Provenance Guard

A backend system that classifies submitted text as likely AI-generated, likely
human-written, or uncertain, scores its own confidence, surfaces a plain-language
transparency label, and gives creators a way to appeal a classification they
disagree with. Built for AI201 Project 4.

Full design history, architecture diagrams, and the spec used to prompt AI tools
during implementation live in [`planning.md`](planning.md). This README is the
canonical record of what was actually built and why.

## Getting started

```
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Mac/Linux
pip install -r requirements.txt
```

Create a `.env` file in the repo root:

```
GROQ_API_KEY=your_key_here
```

Run the app:

```
python app.py
```

The server starts on `http://localhost:5000`. SQLite storage (`provenance_guard.db`)
is created automatically on first run.

## Architecture

Provenance Guard is a Flask API with three independent detection signals feeding a
single confidence scorer, backed by SQLite for both current-state storage
(`submissions`) and an append-only audit trail (`audit_log`). The full Mermaid
diagrams for the submission and appeal flows are in
[`planning.md`](planning.md#architecture); in short: a submission runs through all
three signals, the confidence scorer combines them into one score and label, and
the result is persisted before being returned. An appeal skips re-classification
entirely: it looks up the existing submission, flips its status to `under_review`,
and appends an appeal event to the same audit log.

## Detection signals

The system uses three signals, each capturing a genuinely different property of the
text, combined into one confidence score (see below). Two is the required minimum;
the third was added deliberately as an ensemble check after testing exposed a real
blind spot shared by the first two (see Known limitations).

**Signal 1: LLM-based classification** (`signals.py::classify_with_llm`, Groq
`llama-3.3-70b-versatile`). Prompts the model to judge whether text reads as
human-written or AI-generated based on holistic semantic and stylistic coherence,
returning a score in `[0,1]`. Chosen because it's the only signal capable of judging
meaning and voice rather than surface structure, which is where most genuinely
convincing AI text gives itself away. Its weakness is that it's a black box: it
offers no visibility into *why* it scored something a certain way, and it's fooled
by very formal or ESL-style human writing, which reads as "generic" to it.

**Signal 2: Stylometric heuristics** (`signals.py::score_stylometry`, pure Python).
Averages four normalized sub-metrics: sentence-length variance, punctuation
idiosyncrasy, average word length, and (for texts over 60 words) type-token ratio.
Chosen as a structural counterweight to Signal 1: it measures *how* something is
written rather than *what* it means, so when the two agree, that agreement is
earned from genuinely independent evidence. Its constants were revised twice during
Milestone 4 testing after being found actively wrong, not just noisy; see Spec
reflection below.

**Signal 3: Stock AI-phrase density** (`signals.py::score_stock_phrases`, pure
Python, ensemble/stretch feature). Counts density of phrases widely documented as
overused LLM "tells" ("it is important to note," "furthermore," "delve into,"
"leverage," etc.) per 100 words. Chosen after Signal 1 and Signal 2 were both found
to be fooled by formal/academic writing in the same direction; this signal targets
specific phrasing patterns instead of general formality, so it isn't subject to the
same confound. Presence of these phrases counts as real evidence of AI phrasing;
*absence* returns a neutral `0.5` rather than `0.0`, since not using stock phrases is
weak evidence at best of a human author (this was a real bug found and fixed during
testing, not the original design).

**If deploying this for real**, the first thing to change would be Signal 1's
prompt and Signal 3's phrase lexicon, both of which are hand-written and validated
against only a 17-sample test set. A production system would need a much larger,
independently-labeled dataset, ideally with per-domain calibration (academic,
casual, marketing, etc.), since the confirmed failure mode below shows that a single
global threshold cannot separate every case.

## Confidence scoring

```
combined = 0.5 * llm_score + 0.3 * stylo_score + 0.2 * stock_score
disagreement = max(llm_score, stylo_score, stock_score) - min(llm_score, stylo_score, stock_score)

if disagreement > 0.40:
    label = "uncertain"
elif combined >= 0.75:
    label = "high-confidence AI"
elif combined <= 0.30:
    label = "high-confidence human"
else:
    label = "uncertain"
```

Weights favor Signal 1 as the most holistic check. Thresholds are asymmetric on
purpose: 0.75 to call something confidently AI vs. 0.30 to call it confidently
human, because a false positive (human work branded as AI) is worse than a false
negative on a creative-writing platform. The disagreement check means no single
signal can force a confident verdict on its own: all three have to broadly agree.
These exact values were tuned against a labeled test set, not guessed; the full
methodology, every configuration tried, and why each rejected alternative was
worse, is documented in `planning.md` section 3.

**Two real examples showing meaningful variation** (not a constant regardless of
input):

- **High-confidence case:** submitting `"Our platform leverages cutting-edge
  technology to deliver a seamless, comprehensive solution for modern businesses.
  By utilizing a robust, multifaceted approach, we empower organizations."`
  returned `llm_score: 0.9, stylo_score: 0.94, stock_score: 1.0`, combined
  **confidence 0.93**, label **`high-confidence AI`**.
- **Lower-confidence case:** submitting `"The relationship between monetary policy
  and asset price inflation has been extensively studied in the literature. Central
  banks face a fundamental tension between their mandate for price stability and
  the unintended consequences of prolonged low interest rates."` returned
  `llm_score: 0.8, stylo_score: 0.77, stock_score: 0.5`, combined
  **confidence 0.73**, label **`uncertain`**: despite two of three signals reading
  fairly high, the system correctly refuses to call this confidently AI (this text
  is, in fact, human-written; see Known limitations).

A `0.51` and a `0.95` are never treated the same: the confidence score is always
returned as a precise number alongside the label, so the label communicates the
verdict while the score communicates how sure the system actually is.

## Transparency label

Exact text returned to the caller for each of the three label variants
(`{confidence}` is the combined score as a percentage):

**High-confidence AI:**
> "This content shows strong signals of AI generation ({confidence}% confidence).
> If you're the creator and believe this is wrong, you can appeal this
> classification."

**High-confidence human:**
> "This content shows no strong signals of AI generation ({confidence}% confidence)."

**Uncertain:**
> "We can't confidently tell whether this content is AI-generated or
> human-written ({confidence}% confidence). This isn't an accusation, it means our
> signals didn't agree. If you're the creator, you can appeal for a human review."

Implemented in [`labels.py`](labels.py) and returned under the `transparency_label`
field of every `POST /submit` response, so the copy here can never drift from what
the API actually sends.

## Appeals workflow

`POST /appeal` accepts `{ "submission_id", "creator_id", "reasoning" }`. The
`creator_id` must match the one on the original submission (403 if not, 404 if the
submission doesn't exist), and `reasoning` must be at least 10 characters. On
success, the submission's status flips from `classified` to `under_review` (no
automated re-classification), and an `appeal_filed` event is appended to the audit
log alongside a full snapshot of the original decision, so a human reviewer has
complete context in one place.

Example:

```
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"submission_id": "<id from /submit>", "creator_id": "u6", "reasoning": "I wrote this myself after visiting the restaurant last night."}'
```

```json
{"submission_id": "710e575a-d7fe-4a76-8e1a-a8c62b002d4f", "status": "under_review", "appeal_logged": true}
```

## Rate limiting

`POST /submit` is limited to **10 requests per minute and 100 per day**, keyed by
client IP via Flask-Limiter (`storage_uri="memory://"`). Reasoning: a writer
revising and resubmitting drafts in a burst might realistically hit the endpoint
several times a minute, but not more than ~10; a scripted abuser would try far
more. The daily cap is generous for even a prolific writer submitting many pieces a
day, while still bounding sustained abuse.

Verified by sending 12 rapid requests: the first 10 returned `200`, the last 2
returned `429`:

```
200
200
200
200
200
200
200
200
200
200
429
429
```

## Audit log

Every submission and appeal writes a structured JSON entry to `audit_log`
(SQLite), never `print()` output. Sample from live testing (`GET /log?limit=3`),
showing a `classified` event and its later `appeal_filed` event for the same
submission:

```json
{
  "entries": [
    {
      "submission_id": "710e575a-d7fe-4a76-8e1a-a8c62b002d4f",
      "event": "appeal_filed",
      "details": {
        "creator_id": "u6",
        "appeal_reasoning": "I wrote this myself after visiting the restaurant last night.",
        "status": "under_review",
        "original_decision": {
          "llm_score": 0.2, "stylo_score": 0.32, "stock_score": 0.5,
          "confidence": 0.3, "label": "high-confidence human"
        }
      },
      "timestamp": "2026-07-01T11:18:27.602719+00:00"
    },
    {
      "submission_id": "710e575a-d7fe-4a76-8e1a-a8c62b002d4f",
      "event": "classified",
      "details": {
        "creator_id": "u6", "llm_score": 0.2, "stylo_score": 0.32,
        "stock_score": 0.5, "confidence": 0.3, "label": "high-confidence human"
      },
      "timestamp": "2026-07-01T11:17:37.016579+00:00"
    },
    {
      "submission_id": "f9398480-32b5-4e8b-bd47-8f0fa58f35e3",
      "event": "classified",
      "details": {
        "creator_id": "u5", "llm_score": 0.1, "stylo_score": 0.57,
        "stock_score": 0.5, "confidence": 0.32, "label": "uncertain"
      },
      "timestamp": "2026-07-01T11:17:25.466024+00:00"
    }
  ]
}
```

`GET /log?limit=n` returns the most recent entries via `return jsonify({"entries": get_log(limit)})`.

## Known limitations

All three cases below are measured results from the 17-sample labeled test set in
`tests/eval_samples.py`, evaluated with `tests/eval_weights.py`, not hypothetical
scenarios.

### 1. Corporate/marketing buzzword vocabulary (unresolved false positive)

A genuinely human-written paragraph using ordinary corporate vocabulary
("leverage," "utilize," "robust," "seamless," "comprehensive") scores a combined
confidence of **0.864** and is labeled `high-confidence AI`. This is not a
threshold-tuning gap: that score is statistically indistinguishable from real AI
samples in the same test set (0.88-0.90 combined), meaning no single threshold
could exclude it without also excluding genuine true positives. All three signals
share the same underlying confound here: Signal 1 reads formal/polished register as
"AI-like" regardless of authorship, Signal 2's sentence-structure and word-length
metrics correlate with formality rather than authorship, and Signal 3's phrase
lexicon directly contains words real human corporate writers genuinely use
unironically. None of the three signals isolates *authorship* independent of
*register*. This remains wrong after all tuning done in Milestone 4; the appeals
workflow is the only mitigation.

### 2. Formal academic/legal writing (mitigated, but by a fragile margin)

Before threshold tuning (`AI_THRESHOLD = 0.70`), three more human samples were also
confidently misclassified as AI: an economics paragraph (combined 0.716), a legal
lease clause (0.725), and a literary-analysis student essay (0.749). Raising the
threshold to 0.75 moves all three into the safe "uncertain" band, but the margins
are thin, and thinnest for the exact case that matters most: the student essay
lands at 0.749, just **0.001** below the current cutoff. This is not the system
"understanding" that these are human; it's a calibration line that happens to sit
just above where these particular examples fall. Differently-worded formal human
writing could easily land back on the wrong side, and there is no guarantee the
current threshold generalizes beyond this 17-sample set.

### 3. Casual-register AI-generated text (a structural false-negative ceiling)

The system was tested against AI-generated text deliberately written in a casual,
unpolished style (a gym-update post, a "thinking about switching jobs" post). Both
score in the 0.32-0.34 combined range and land on "uncertain," across every
configuration tested, never on "high-confidence AI." This is a direct consequence
of the design's asymmetric priority (false positives on humans are treated as worse
than false negatives on AI): nothing in the current scoring can confidently flag
AI text that avoids stock phrasing and doesn't have extreme stylometric uniformity.
That's a deliberate tradeoff, not an oversight, but it means well-edited or
casually-prompted AI text has a real, structural path to evading confident
detection entirely, not just landing in a cautious middle ground by chance.

## Spec reflection

**How the spec helped:** writing the exact confidence formula and label thresholds
into `planning.md` *before* implementing Signal 2 gave concrete, falsifiable
expectations to test against. That's precisely what caught two real bugs during
Milestone 4: the type-token-ratio sub-metric was providing zero discriminative
signal (saturating near 1.0 for all short texts, AI or human), and the word-length
sub-metric had its assumed direction backwards, scoring casual human writing as
AI-like and formal AI writing as human-like. Neither would have been obvious
without a documented, precise formula to compute actual numbers against and compare
to stated expectations.

**Where the implementation diverged from the spec:** the original planning.md spec
(Milestones 1-2) called for exactly two signals with a pairwise disagreement check
(`|llm_score - stylo_score|`). Implementation diverged by adding a third signal
(stock-phrase density) after testing revealed a shared blind spot the two-signal
design couldn't catch, which required generalizing the disagreement check from a
pairwise difference to a range across all three signals, and recalibrating every
weight and threshold in section 3 against a purpose-built labeled test set rather
than the original hand-picked guesses. The spec's five-question structure stayed
intact, it was extended, not replaced, once real testing produced evidence the
original two-signal design didn't have.

## AI usage

This project was built with an AI coding tool (Claude Code) as an active
implementation partner, not just an autocomplete. Two specific instances where its
output was reviewed and changed rather than accepted as-is:

1. **Stylometric heuristics (Signal 2).** Directed the AI tool to implement
   `score_stylometry` per the planning.md spec: four sub-metrics normalized to
   `[0,1]` and averaged. It produced a plausible-looking implementation with
   reasonable-sounding reference constants. Testing against real known-AI and
   known-human samples showed two of those sub-metrics were actively wrong, not
   just imprecise: type-token ratio saturated to ~0.86-0.90 for every sample
   regardless of authorship (no signal at all), and the word-length metric was
   inverted, scoring a casual human sample as AI-like and a formal AI sample as
   human-like, on the two clearest test cases. Both were revised with new,
   data-grounded constants rather than kept as generated.

2. **Confidence-score weight tuning.** Directed the AI tool to propose initial
   combine weights and thresholds. Rather than accepting the first proposal, it was
   validated (and then revised) by building a 17-sample labeled test set and a grid
   search comparing multiple configurations, which is what surfaced the real
   AI_THRESHOLD and DISAGREEMENT_THRESHOLD values ultimately used, along with the
   discovery that a neutral "no evidence" score from Signal 3 was incorrectly
   triggering the disagreement override, a bug that wouldn't have surfaced from
   code review alone, only from running real numbers through it.
