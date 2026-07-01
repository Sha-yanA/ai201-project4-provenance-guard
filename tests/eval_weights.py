import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval_samples import SAMPLES

from signals import classify_with_llm, score_stock_phrases, score_stylometry

# Cached from a prior run (temperature=0, so classify_with_llm is deterministic).
# Delete this and set RECOMPUTE=True to regenerate from scratch.
RECOMPUTE = False
CACHED_RAW_SCORES = [
    ("clearly_ai_essay", "AI", 0.80, 0.77, 1.00),
    ("ai_listicle", "AI", 0.80, 0.92, 1.00),
    ("ai_news_summary", "AI", 0.80, 0.79, 0.82),
    ("ai_product_blurb", "AI", 0.90, 0.84, 1.00),
    ("ai_casual_attempt", "AI", 0.20, 0.46, 0.50),
    ("ai_edited_casual", "AI", 0.20, 0.41, 0.50),
    ("ai_travel_blurb", "AI", 0.90, 0.86, 0.50),
    ("clearly_human_review", "human", 0.20, 0.32, 0.50),
    ("human_dev_notes", "human", 0.20, 0.33, 0.50),
    ("human_recipe_fail", "human", 0.20, 0.66, 0.50),
    ("human_text_rant", "human", 0.10, 0.57, 0.50),
    ("human_academic_econ", "human", 0.80, 0.72, 0.50),
    ("human_academic_bio", "human", 0.80, 0.91, 0.50),
    ("human_legal_style", "human", 0.80, 0.75, 0.50),
    ("human_corporate_buzzword", "human", 0.80, 0.88, 1.00),
    ("human_student_essay", "human", 0.80, 0.83, 0.50),
    ("human_history_academic", "human", 0.40, 0.52, 0.50),
]


def label_from_thresholds(combined, ai_threshold, human_threshold):
    if combined >= ai_threshold:
        return "AI"
    if combined <= human_threshold:
        return "human"
    return "uncertain"


def score(llm, stylo, stock, weights, disagreement_threshold, ai_threshold, human_threshold):
    w_llm, w_stylo, w_stock = weights
    combined = w_llm * llm + w_stylo * stylo + w_stock * stock
    disagreement = max(llm, stylo, stock) - min(llm, stylo, stock)
    if disagreement > disagreement_threshold:
        return combined, "uncertain"
    return combined, label_from_thresholds(combined, ai_threshold, human_threshold)


def evaluate(raw_scores, weights, disagreement_threshold, ai_threshold, human_threshold):
    correct = wrong = uncertain = 0
    rows = []
    for name, true, llm, stylo, stock in raw_scores:
        combined, label = score(llm, stylo, stock, weights, disagreement_threshold, ai_threshold, human_threshold)
        if label == "uncertain":
            uncertain += 1
        elif label == true:
            correct += 1
        else:
            wrong += 1
        rows.append((name, true, label, combined))
    return correct, wrong, uncertain, rows


def main():
    if RECOMPUTE:
        print("Computing raw signal scores for all samples (one Groq call each)...")
        raw_scores = []
        for name, true, text in SAMPLES:
            llm = classify_with_llm(text)
            stylo = score_stylometry(text)
            stock = score_stock_phrases(text)
            raw_scores.append((name, true, llm, stylo, stock))
            print(f"  {name}: llm={llm:.2f} stylo={stylo:.2f} stock={stock:.2f}")
    else:
        raw_scores = CACHED_RAW_SCORES

    configs = [
        ("current (0.5/0.3/0.2, disagree=0.35, 0.70/0.30)", (0.5, 0.3, 0.2), 0.35, 0.70, 0.30),
        ("adopted: stricter AI threshold (0.5/0.3/0.2, AI>=0.75)", (0.5, 0.3, 0.2), 0.35, 0.75, 0.30),
        ("equal thirds + tighter disagreement (0.34/0.33/0.33, disagree=0.25, AI>=0.70)",
         (0.34, 0.33, 0.33), 0.25, 0.70, 0.30),
    ]

    print("\n--- Config comparison ---")
    for label, weights, dthresh, ai_t, human_t in configs:
        correct, wrong, uncertain, rows = evaluate(raw_scores, weights, dthresh, ai_t, human_t)
        print(f"\n{label}: correct={correct} wrong={wrong} uncertain={uncertain}")
        for name, true, got, combined in rows:
            if got != true and got != "uncertain":
                print(f"    WRONG  {name}: true={true} got={got} combined={combined:.2f}")

    print("\n--- Grid search around the adopted config (weights fixed at 0.5/0.3/0.2) ---")
    best = None
    for ai_t in [0.70, 0.72, 0.75, 0.78, 0.80, 0.85, 0.90]:
        for human_t in [0.20, 0.25, 0.30, 0.35]:
            for dthresh in [0.25, 0.30, 0.35, 0.40, 0.45]:
                correct, wrong, uncertain, rows = evaluate(
                    raw_scores, (0.5, 0.3, 0.2), dthresh, ai_t, human_t
                )
                key = (-correct, wrong)  # prioritize more correct, then fewer wrong
                if best is None or key < best[0]:
                    best = (key, ai_t, human_t, dthresh, correct, wrong, uncertain)

    _, ai_t, human_t, dthresh, correct, wrong, uncertain = best
    print(f"Best found: AI>={ai_t}, human<={human_t}, disagree={dthresh} "
          f"-> correct={correct} wrong={wrong} uncertain={uncertain}")
    rows = evaluate(raw_scores, (0.5, 0.3, 0.2), dthresh, ai_t, human_t)[3]
    for name, true, got, combined in rows:
        if got != true and got != "uncertain":
            print(f"    WRONG  {name}: true={true} got={got} combined={combined:.2f}")


if __name__ == "__main__":
    main()
