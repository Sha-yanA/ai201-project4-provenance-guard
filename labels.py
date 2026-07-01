# Exact transparency label copy from planning.md section 7. Kept in one place
# so the README can quote this verbatim and it can't drift from what the API
# actually returns.
_LABEL_TEMPLATES = {
    "high-confidence AI": (
        "This content shows strong signals of AI generation ({percent}% confidence). "
        "If you're the creator and believe this is wrong, you can appeal this "
        "classification."
    ),
    "high-confidence human": (
        "This content shows no strong signals of AI generation ({percent}% confidence)."
    ),
    "uncertain": (
        "We can't confidently tell whether this content is AI-generated or "
        "human-written ({percent}% confidence). This isn't an accusation, it means "
        "our signals didn't agree. If you're the creator, you can appeal for a "
        "human review."
    ),
}


def generate_label_text(label, confidence):
    percent = round(confidence * 100)
    return _LABEL_TEMPLATES[label].format(percent=percent)
