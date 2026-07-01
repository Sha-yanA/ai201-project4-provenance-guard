# Provenance Guard - Planning

## Milestone 1: Understanding the System and Defining the Architecture

### 1. Architecture narrative: Path of a submission

A creator submits a piece of text-based content (poem, story excerpt, blog post) to
`POST /submit`. The request flows through:

1. **API layer (Flask)**: validates the request body (non-empty text, within a max
   length), checks rate limit for the caller (Flask-Limiter), assigns a `submission_id`.
2. **Signal 1, LLM classifier (Groq, `llama-3.3-70b-versatile`)**: the raw text is
   sent to Groq with a prompt asking it to judge whether the text reads as
   human-written or AI-generated, returning a score in `[0,1]` (1 = confident AI).
3. **Signal 2, Stylometric heuristics (pure Python)**: the raw text is analyzed
   locally for sentence-length variance, type-token ratio (vocabulary diversity), and
   punctuation density, producing an independent score in `[0,1]` on the same scale.
4. **Confidence scorer**: combines the two signal scores into one combined confidence
   score using a weighted average with an asymmetric disagreement penalty (see below),
   and buckets the result into one of three labels.
5. **Transparency label generator**: maps the label bucket and combined score into the
   exact user-facing text (see README for the three variants).
6. **Persistence**: the submission's current state (id, text metadata, signal scores,
   combined score, label, status) is written to a `submissions` table, and a
   corresponding immutable event is appended to the `audit_log` table (submission id,
   signal scores, combined score, label, timestamp), *before* returning.
7. **Response**: the API returns the label text, combined confidence score, and the
   individual signal scores back to the caller.

Data model note: `submissions` is the mutable record of current state (status moves
from `classified` to `under_review`, etc). `audit_log` is an append-only trail of
events (submission created, appeal filed) and is never mutated, only added to. This
keeps "what is true right now" and "what happened, in order" cleanly separated.

Separately, a creator who disagrees with their classification calls `POST /appeal`
with their `submission_id` and reasoning text. This:

1. Looks up the original submission in the `submissions` table.
2. Updates that submission's status to `under_review` (no automated re-classification).
3. Appends an appeal event (submission id, creator reasoning, timestamp) to the
   `audit_log`.
4. Returns the updated status to the caller.

### 2. Detection signals

**Signal 1: LLM-based classification (Groq, `llama-3.3-70b-versatile`)**
- *What it measures:* holistic semantic and stylistic coherence: does the text read
  the way a human naturally writes (idiosyncrasy, imperfection, lived specificity), or
  does it show the smoothed-out, generically coherent patterns typical of LLM output?
- *Why it differs between human/AI:* the model has been trained on huge amounts of
  both human and AI text and can pick up on subtle global patterns (word choice,
  argument structure, "AI tells") that are hard to reduce to a simple formula.
- *Blind spot:* it's a black box, with no visibility into *why* it scored something a
  certain way. It can be fooled by heavily edited/paraphrased AI text or stilted human
  writing (e.g. non-native speakers, ESL writers, very formal writing), and it costs an
  API call per submission (latency + rate limits on Groq's side).

**Signal 2: Stylometric heuristics (pure Python, no external libraries)**
- *What it measures:* measurable statistical structure of the text: sentence-length
  variance, type-token ratio (vocabulary diversity), punctuation density, and average
  sentence complexity.
- *Why it differs between human/AI:* AI-generated text tends toward more *uniform*
  sentence lengths and more "average" vocabulary choices (regression to the mean of
  training data), while human writing tends to be more variable, mixing short and
  long sentences, using idiosyncratic punctuation, repeating pet words/phrases.
- *Blind spot:* purely structural, it has no notion of meaning or coherence, so a
  human writer with a very uniform, controlled style (or an AI prompted to "write with
  varied sentence length") can easily fall on the wrong side. It's a weak signal alone.

These two signals are genuinely independent: one is semantic/holistic (LLM), the other
is structural/statistical (stylometry). When they agree, confidence is well earned;
when they disagree, that disagreement itself is informative (see confidence scoring).

### 3. Confidence scoring approach

Each signal returns a score in `[0,1]` (1 = confident AI, 0 = confident human).

```
combined = 0.6 * llm_score + 0.4 * stylo_score
disagreement = |llm_score - stylo_score|

if disagreement > 0.35:
    label = "uncertain"   # signals conflict: don't let either one alone push
                          # to a confident verdict, regardless of where combined lands
else:
    label = label_from_thresholds(combined)   # see table below
```

Label thresholds (asymmetric, biased against false AI accusations per the hint that a
false positive, human mislabeled as AI, is worse than a false negative), applied only
when the signals agree (disagreement <= 0.35):

| combined score | label               |
|-----------------|---------------------|
| >= 0.70         | high-confidence AI  |
| <= 0.30         | high-confidence human |
| otherwise       | uncertain           |

The `combined` score itself is always returned to the caller as the numeric
confidence, even when disagreement forces the label to "uncertain": the label
communicates the verdict, the score communicates how it was computed. Forcing the
label directly (rather than clamping the score into the uncertain range) avoids an
edge case where a clamped score could still land exactly on a threshold boundary and
get bucketed into a confident label despite the disagreement.

We only ever land in "high-confidence AI" when *both* signals independently agree the
text looks AI-generated. A single strong signal can't brand a human creator on its own.

### 4. False-positive trace (human misclassified as AI)

Scenario: a human writer submits a tightly-edited, very uniform blog post. The LLM
signal is uncertain (0.55, mildly polished but plausible either way) but the
stylometry signal spikes high (0.85, unusually uniform sentence lengths, since this
writer edits ruthlessly).

- `disagreement = |0.55 - 0.85| = 0.30`, below the 0.35 threshold, so the label is
  taken from the threshold table rather than forced.
- `combined = 0.6*0.55 + 0.4*0.85 = 0.67`, which falls in the **uncertain** band, not
  high-confidence AI. Good: the writer isn't branded, but they're also not fully
  cleared.
- The label shown is the "uncertain" variant: plain language, explicitly flags that
  the system isn't sure, and does not assert AI authorship.
- The audit log records both signal scores and the combined score, so if the creator
  files an appeal, the human reviewer can see why the system landed on uncertain
  (stylometry drove it, LLM was ambivalent), giving them concrete grounds to contest.
- On `POST /appeal`, the creator's reasoning ("I always edit my sentences to be
  tight and uniform") is logged next to the original decision, status flips to
  `under_review`, and a human makes the final call. No auto re-classification.

This shows the system's designed failure mode is "flag as uncertain and invite an
appeal," not "confidently misclassify," which matches the stated priority that false
positives are the worse failure.

### 5. API surface (sketch, contract only, no implementation yet)

**`POST /submit`**
- Accepts: `{ "text": string, "creator_id": string }`
- Returns: `{ "submission_id": string, "label": string, "confidence": float, "signals": { "llm_score": float, "stylo_score": float }, "status": "classified" }`
- Rate-limited per creator/IP.

**`POST /appeal`**
- Accepts: `{ "submission_id": string, "reasoning": string }`
- Returns: `{ "submission_id": string, "status": "under_review", "appeal_logged": true }`

**`GET /log`** (audit log visibility)
- Accepts: none (maybe `?limit=n` query param)
- Returns: array of structured audit entries (submission id, signals, combined score,
  label, appeals if any, timestamps).

**`GET /submissions/{id}`** *(likely needed to support appeals looking up original decision, confirm during Milestone 2)*
- Accepts: path param `id`
- Returns: the stored submission record (text metadata, label, confidence, status).

### 6. Architecture diagram

**Submission flow:**

```
Creator
  |
  |  POST /submit { text, creator_id }
  v
[Flask API] --rate limit check (Flask-Limiter)--> (429 if exceeded)
  |
  |  raw text
  +--------------------------+
  v                          v
[Signal 1: Groq LLM]   [Signal 2: Stylometric heuristics]
  |  llm_score [0,1]         |  stylo_score [0,1]
  +--------------------------+
  v
[Confidence Scorer]
  |  combined score + disagreement check
  v
[Transparency Label Generator]
  |  label text + confidence
  v
[Persistence] --writes--> (submissions table: current state)
  |          --appends--> (audit_log table: created event)
  v
Response --> { submission_id, label, confidence, signals, status }
  |
  v
Creator
```

**Appeal flow:**

```
Creator
  |
  |  POST /appeal { submission_id, reasoning }
  v
[Flask API]
  |  look up original submission
  v
(submissions table) --read--> [Appeal Handler]
  |
  |  reasoning + original decision
  v
[Appeal Handler] --update status: under_review--> (submissions table)
  |
  |  append appeal event
  v
(audit_log table)
  |
  v
Response --> { submission_id, status: "under_review", appeal_logged: true }
  |
  v
Creator
```


## Open questions / to revisit in Milestone 2

- Exact Groq prompt wording for the LLM signal (needs testing against known
  human/AI samples to calibrate score meaning).
- Exact stylometric formula/weights within `stylo_score` (sentence-length variance vs.
  type-token ratio vs. punctuation density: how are these three sub-metrics combined
  into one score?).
- Rate limit specific numbers (requests per minute/hour per creator): needs reasoning
  about realistic usage on a writing platform.
- Exact transparency label copy (three variants): needs user-testing per the hint
  ("show your label to someone who hasn't seen your project").
- Whether `GET /submissions/{id}` is truly required or whether appeal lookup can be
  folded into the audit log directly.
- Whether rate-limited (429) submission attempts should also get a lightweight
  audit_log entry (creator_id, timestamp, reason), for abuse-pattern visibility, even
  though they never reach the signals.
- Fallback behavior when the Groq API call fails or times out: fail the whole request
  (503) vs. fail open to stylometry-only scoring with the label forced to "uncertain"
  (leaning toward the latter, consistent with the "uncertain is safe" design).
