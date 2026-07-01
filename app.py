import uuid

from flask import Flask, jsonify, request

from signals import classify_with_llm
from storage import create_submission, get_log, init_db

app = Flask(__name__)
init_db()


def _label_from_llm_score(llm_score):
    # Placeholder until Signal 2 lands in Milestone 4: uses llm_score alone
    # against the final three-band thresholds from planning.md section 3.
    if llm_score >= 0.70:
        return "high-confidence AI"
    if llm_score <= 0.30:
        return "high-confidence human"
    return "uncertain"


@app.route("/submit", methods=["POST"])
def submit():
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    creator_id = body.get("creator_id")

    if not text or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "text is required"}), 400
    if not creator_id or not isinstance(creator_id, str):
        return jsonify({"error": "creator_id is required"}), 400

    llm_score = classify_with_llm(text)
    confidence = round(llm_score, 2)
    label = _label_from_llm_score(llm_score)
    submission_id = str(uuid.uuid4())
    create_submission(submission_id, creator_id, text, llm_score, confidence, label)

    return jsonify({
        "submission_id": submission_id,
        "label": label,
        "confidence": confidence,
        "llm_score": llm_score,
        "status": "classified",
    }), 200


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=20, type=int)
    return jsonify({"entries": get_log(limit)}), 200


if __name__ == "__main__":
    app.run(debug=True)
