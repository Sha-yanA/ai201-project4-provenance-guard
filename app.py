import uuid

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from labels import generate_label_text
from scoring import score_confidence
from signals import classify_with_llm, score_stock_phrases, score_stylometry
from storage import create_submission, file_appeal, get_log, get_submission, init_db

app = Flask(__name__)
init_db()

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

MIN_APPEAL_REASONING_LENGTH = 10

# A writer revising and resubmitting drafts in a burst might hit this endpoint
# several times a minute, but not more than ~10; a scripted abuser would try
# far more. The daily cap is generous for even a prolific writer submitting
# many pieces a day, while still bounding sustained abuse. See planning.md
# Open questions.
SUBMIT_RATE_LIMIT = "10 per minute;100 per day"


@app.route("/submit", methods=["POST"])
@limiter.limit(SUBMIT_RATE_LIMIT)
def submit():
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    creator_id = body.get("creator_id")

    if not text or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "text is required"}), 400
    if not creator_id or not isinstance(creator_id, str):
        return jsonify({"error": "creator_id is required"}), 400

    llm_score = round(classify_with_llm(text), 2)
    stylo_score = round(score_stylometry(text), 2)
    stock_score = round(score_stock_phrases(text), 2)
    confidence, label = score_confidence(llm_score, stylo_score, stock_score)
    confidence = round(confidence, 2)
    submission_id = str(uuid.uuid4())
    create_submission(submission_id, creator_id, text, llm_score, stylo_score, stock_score, confidence, label)

    return jsonify({
        "submission_id": submission_id,
        "label": label,
        "transparency_label": generate_label_text(label, confidence),
        "confidence": confidence,
        "signals": {
            "llm_score": llm_score,
            "stylo_score": stylo_score,
            "stock_score": stock_score,
        },
        "status": "classified",
    }), 200


@app.route("/appeal", methods=["POST"])
def appeal():
    body = request.get_json(silent=True) or {}
    submission_id = body.get("submission_id")
    creator_id = body.get("creator_id")
    reasoning = body.get("reasoning")

    if not submission_id or not isinstance(submission_id, str):
        return jsonify({"error": "submission_id is required"}), 400
    if not creator_id or not isinstance(creator_id, str):
        return jsonify({"error": "creator_id is required"}), 400
    if not reasoning or not isinstance(reasoning, str) or len(reasoning.strip()) < MIN_APPEAL_REASONING_LENGTH:
        return jsonify({"error": f"reasoning must be at least {MIN_APPEAL_REASONING_LENGTH} characters"}), 400

    submission = get_submission(submission_id)
    if submission is None:
        return jsonify({"error": "submission not found"}), 404
    if submission["creator_id"] != creator_id:
        return jsonify({"error": "creator_id does not match this submission"}), 403

    original_decision = {
        "llm_score": submission["llm_score"],
        "stylo_score": submission["stylo_score"],
        "stock_score": submission["stock_score"],
        "confidence": submission["confidence"],
        "label": submission["label"],
    }
    file_appeal(submission_id, creator_id, reasoning, original_decision)

    return jsonify({
        "submission_id": submission_id,
        "status": "under_review",
        "appeal_logged": True,
    }), 200


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=20, type=int)
    return jsonify({"entries": get_log(limit)}), 200


if __name__ == "__main__":
    app.run(debug=True)
