import uuid

from flask import Flask, jsonify, request

from scoring import score_confidence
from signals import classify_with_llm, score_stock_phrases, score_stylometry
from storage import create_submission, get_log, init_db

app = Flask(__name__)
init_db()


@app.route("/submit", methods=["POST"])
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
        "confidence": confidence,
        "signals": {
            "llm_score": llm_score,
            "stylo_score": stylo_score,
            "stock_score": stock_score,
        },
        "status": "classified",
    }), 200


@app.route("/log", methods=["GET"])
def log():
    limit = request.args.get("limit", default=20, type=int)
    return jsonify({"entries": get_log(limit)}), 200


if __name__ == "__main__":
    app.run(debug=True)
