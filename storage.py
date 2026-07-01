import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "provenance_guard.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                submission_id TEXT PRIMARY KEY,
                creator_id TEXT NOT NULL,
                text TEXT NOT NULL,
                llm_score REAL NOT NULL,
                confidence REAL NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id TEXT NOT NULL,
                event TEXT NOT NULL,
                details TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_submission(submission_id, creator_id, text, llm_score, confidence, label):
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO submissions "
            "(submission_id, creator_id, text, llm_score, confidence, label, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (submission_id, creator_id, text, llm_score, confidence, label, "classified", now),
        )
        conn.execute(
            "INSERT INTO audit_log (submission_id, event, details, timestamp) VALUES (?, ?, ?, ?)",
            (
                submission_id,
                "classified",
                json.dumps({
                    "creator_id": creator_id,
                    "llm_score": llm_score,
                    "confidence": confidence,
                    "label": label,
                }),
                now,
            ),
        )


def get_log(limit=20):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT submission_id, event, details, timestamp FROM audit_log "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "submission_id": row["submission_id"],
            "event": row["event"],
            "details": json.loads(row["details"]),
            "timestamp": row["timestamp"],
        }
        for row in rows
    ]
