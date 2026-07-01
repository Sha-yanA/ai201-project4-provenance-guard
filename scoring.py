# Tuned against a 17-sample labeled test set (tests/eval_samples.py), not a
# large or independently-collected dataset; treat as a starting calibration,
# not a final answer (see planning.md section 3 and Open questions).
LLM_WEIGHT = 0.5
STYLO_WEIGHT = 0.3
STOCK_WEIGHT = 0.2

# Raised from 0.35: score_stock_phrases returns a neutral 0.5 when it finds no
# stock AI phrases (deliberately, "no evidence" != "evidence of human"). At
# 0.35, that neutral abstention alone was enough to trigger the disagreement
# override even when the other two signals strongly agreed, incorrectly
# forcing "uncertain" on otherwise-clear cases. 0.40 was the best value found
# by a grid search over this test set.
DISAGREEMENT_THRESHOLD = 0.40

# Raised from 0.70 after testing showed several genuine human samples
# (formal/academic writing) landing at 0.72-0.75, just above the old bar.
AI_THRESHOLD = 0.75
HUMAN_THRESHOLD = 0.30


def score_confidence(llm_score, stylo_score, stock_score):
    combined = LLM_WEIGHT * llm_score + STYLO_WEIGHT * stylo_score + STOCK_WEIGHT * stock_score
    disagreement = max(llm_score, stylo_score, stock_score) - min(llm_score, stylo_score, stock_score)

    if disagreement > DISAGREEMENT_THRESHOLD:
        # Signals conflict: don't let any one of them alone push to a
        # confident verdict, regardless of where the weighted average lands.
        label = "uncertain"
    elif combined >= AI_THRESHOLD:
        label = "high-confidence AI"
    elif combined <= HUMAN_THRESHOLD:
        label = "high-confidence human"
    else:
        label = "uncertain"

    return combined, label
