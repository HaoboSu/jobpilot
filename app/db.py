"""SQLite persistence layer.

Design notes:
- One connection per thread (check_same_thread=True) with WAL so the monitor
  thread and HTTP threads can work at the same time.
- resume_versions and documents are append-only: original content is never
  overwritten, which is what makes "keep the pre-edit original" verifiable.
- recommendations are keyed by (job, resume_version) so a new resume version
  creates a fresh evaluation without destroying historical results.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "jobpilot.db")

_local = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_versions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    label             TEXT NOT NULL,
    raw_text          TEXT NOT NULL,
    raw_filename      TEXT,
    raw_format        TEXT,
    structured_json   TEXT NOT NULL,
    source            TEXT NOT NULL,
    note              TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id     TEXT,
    source          TEXT NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT,
    location        TEXT,
    url             TEXT,
    description     TEXT,
    posted_at       TEXT,
    fetched_at      TEXT NOT NULL,
    employment_type TEXT,
    remote          INTEGER DEFAULT 0,
    salary_text     TEXT,
    raw_json        TEXT,
    UNIQUE(source, external_id, title, company)
);

CREATE TABLE IF NOT EXISTS recommendations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id               INTEGER NOT NULL REFERENCES jobs(id),
    resume_version_id    INTEGER NOT NULL REFERENCES resume_versions(id),
    score                REAL NOT NULL,
    breakdown_json       TEXT NOT NULL,
    matched_skills_json  TEXT,
    missing_skills_json  TEXT,
    reasons_json         TEXT,
    status               TEXT DEFAULT 'new',
    docs_suppressed      INTEGER DEFAULT 0,
    created_at           TEXT NOT NULL,
    UNIQUE(job_id, resume_version_id)
);

CREATE TABLE IF NOT EXISTS documents (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id         INTEGER NOT NULL REFERENCES recommendations(id),
    job_id                    INTEGER NOT NULL REFERENCES jobs(id),
    resume_version_id         INTEGER NOT NULL REFERENCES resume_versions(id),
    doc_type                  TEXT NOT NULL,
    title                     TEXT,
    content                   TEXT NOT NULL,
    engine                    TEXT NOT NULL,
    original_snapshot         TEXT NOT NULL,
    original_structured_json  TEXT,
    validation_json           TEXT,
    created_at                TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitor_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT,
    finished_at   TEXT,
    trigger       TEXT,
    source        TEXT,
    jobs_fetched  INTEGER DEFAULT 0,
    jobs_new      INTEGER DEFAULT 0,
    recs_new      INTEGER DEFAULT 0,
    status        TEXT,
    message       TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_id   INTEGER NOT NULL,
    job_id   INTEGER NOT NULL,
    score    REAL NOT NULL,
    email    TEXT NOT NULL,
    subject  TEXT,
    status   TEXT NOT NULL,
    message  TEXT,
    sent_at  TEXT NOT NULL,
    UNIQUE(rec_id, score, email)
);

CREATE INDEX IF NOT EXISTS idx_rec_score ON recommendations(score DESC);
CREATE INDEX IF NOT EXISTS idx_rec_job ON recommendations(job_id);
CREATE INDEX IF NOT EXISTS idx_doc_rec ON documents(recommendation_id);
CREATE INDEX IF NOT EXISTS idx_jobs_fetched ON jobs(fetched_at DESC);
"""


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    # Lightweight migration for databases created before docs_suppressed existed.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(recommendations)")}
    if "docs_suppressed" not in cols:
        conn.execute(
            "ALTER TABLE recommendations ADD COLUMN docs_suppressed INTEGER DEFAULT 0"
        )
    conn.commit()


def q(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in connect().execute(sql, params).fetchall()]


def q1(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    row = connect().execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: tuple | list = ()) -> int:
    conn = connect()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid


def executemany(sql: str, rows: list[tuple]) -> None:
    conn = connect()
    conn.executemany(sql, rows)
    conn.commit()


# ---------------------------------------------------------------- settings
DEFAULT_SETTINGS: dict[str, Any] = {
    "keywords": "数据分析师, Data Analyst, BI, Python, SQL",
    "location": "上海",
    "region_mode": "prefer",
    "query_keywords": ["数据分析", "Python", "SQL"],
    "interval_minutes": 60,
    "auto_monitor": False,
    "time_range": "r604800",
    "remote_only": False,
    "min_score": 55.0,
    "max_results": 30,
    "exclude_companies": [],
    "required_skills": [],
    "use_linkedin": True,
    "linkedin_proxy": "",
    "demo_fallback": True,
    "auto_generate_docs": True,
    "llm_enabled": False,
    "llm_base_url": "https://api.openai.com/v1",
    "llm_api_key": "",
    "llm_model": "gpt-4o-mini",
    "language": "zh",
    "letter_language": "auto",
    "notify_enabled": True,
    "notify_email": "you@example.com",
    "notify_threshold": 80.0,
    "smtp_provider": "auto",
    "smtp_host": "smtp.office365.com",
    "smtp_port": 587,
    "smtp_user": "you@example.com",
    "smtp_password": "",
    "smtp_use_tls": True,
    "smtp_last_ok": 0,
    "smtp_last_message": "",
}


def get_settings() -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    for row in q("SELECT key, value FROM settings"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except (ValueError, TypeError):
            out[row["key"]] = row["value"]
    return out


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if key not in DEFAULT_SETTINGS:
            continue
        execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
    return get_settings()


# ------------------------------------------------------------ resume versions
def create_resume_version(
    label: str,
    raw_text: str,
    structured: dict,
    source: str,
    raw_filename: str | None = None,
    raw_format: str | None = None,
    note: str | None = None,
) -> int:
    return execute(
        "INSERT INTO resume_versions(label,raw_text,raw_filename,raw_format,"
        "structured_json,source,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            label,
            raw_text,
            raw_filename,
            raw_format,
            json.dumps(structured, ensure_ascii=False),
            source,
            note,
            now(),
        ),
    )


def list_resume_versions() -> list[dict[str, Any]]:
    rows = q(
        "SELECT id,label,raw_filename,raw_format,source,note,created_at,"
        "LENGTH(raw_text) AS raw_length FROM resume_versions ORDER BY id DESC"
    )
    return rows


def get_resume_version(version_id: int | None = None) -> dict[str, Any] | None:
    if version_id is None:
        row = q1("SELECT * FROM resume_versions ORDER BY id DESC LIMIT 1")
    else:
        row = q1("SELECT * FROM resume_versions WHERE id=?", (version_id,))
    if not row:
        return None
    row["structured"] = json.loads(row.pop("structured_json"))
    return row


# ---------------------------------------------------------------------- jobs
def upsert_job(job: dict[str, Any]) -> tuple[int, bool]:
    """Return (job_id, is_new). Existing rows keep their original fetched_at."""
    key = (job.get("source") or "manual", job.get("external_id") or "",
           job.get("title") or "", job.get("company") or "")
    existing = q1(
        "SELECT id FROM jobs WHERE source=? AND external_id=? AND title=? AND company=?", key
    )
    if existing:
        return existing["id"], False
    new_id = execute(
        "INSERT INTO jobs(external_id,source,title,company,location,url,description,"
        "posted_at,fetched_at,employment_type,remote,salary_text,raw_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            key[1], key[0], key[2], key[3], job.get("location"),
            job.get("url"), job.get("description"), job.get("posted_at"), now(),
            job.get("employment_type"), 1 if job.get("remote") else 0,
            job.get("salary_text"), json.dumps(job.get("raw", {}), ensure_ascii=False),
        ),
    )
    return new_id, True


def get_job(job_id: int) -> dict[str, Any] | None:
    return q1("SELECT * FROM jobs WHERE id=?", (job_id,))


def list_jobs(limit: int = 200) -> list[dict[str, Any]]:
    return q(
        "SELECT j.*, r.id AS rec_id, r.score, r.status AS rec_status "
        "FROM jobs j LEFT JOIN recommendations r "
        "ON r.job_id=j.id AND r.resume_version_id="
        "(SELECT MAX(id) FROM resume_versions) "
        "ORDER BY j.fetched_at DESC, j.id DESC LIMIT ?",
        (limit,),
    )


# ----------------------------------------------------------- recommendations
def upsert_recommendation(
    job_id: int, resume_version_id: int, score: float,
    breakdown: dict, matched: list, missing: list, reasons: list,
) -> tuple[int, bool]:
    row = q1(
        "SELECT id FROM recommendations WHERE job_id=? AND resume_version_id=?",
        (job_id, resume_version_id),
    )
    payload = (
        round(score, 1),
        json.dumps(breakdown, ensure_ascii=False),
        json.dumps(matched, ensure_ascii=False),
        json.dumps(missing, ensure_ascii=False),
        json.dumps(reasons, ensure_ascii=False),
    )
    if row:
        execute(
            "UPDATE recommendations SET score=?,breakdown_json=?,matched_skills_json=?,"
            "missing_skills_json=?,reasons_json=? WHERE id=?",
            (*payload, row["id"]),
        )
        return row["id"], False
    new_id = execute(
        "INSERT INTO recommendations(job_id,resume_version_id,score,breakdown_json,"
        "matched_skills_json,missing_skills_json,reasons_json,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,'new',?)",
        (job_id, resume_version_id, *payload, now()),
    )
    return new_id, True


def list_recommendations(
    min_score: float = 0, status: str | None = None, limit: int = 300,
    latest_resume_only: bool = True,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT r.*, j.title, j.company, j.location, j.url, j.source AS job_source, "
        "j.posted_at, j.description, j.remote, "
        "(SELECT COUNT(*) FROM documents d WHERE d.recommendation_id=r.id) AS doc_count "
        "FROM recommendations r JOIN jobs j ON j.id=r.job_id WHERE r.score>=?"
    )
    params: list[Any] = [min_score]
    if status and status != "all":
        sql += " AND r.status=?"
        params.append(status)
    if latest_resume_only:
        sql += " AND r.resume_version_id=(SELECT MAX(id) FROM resume_versions)"
    sql += " ORDER BY r.score DESC, r.id DESC LIMIT ?"
    params.append(limit)
    rows = q(sql, tuple(params))
    for r in rows:
        r["breakdown"] = json.loads(r.pop("breakdown_json", "{}") or "{}")
        r["matched_skills"] = json.loads(r.pop("matched_skills_json", "[]") or "[]")
        r["missing_skills"] = json.loads(r.pop("missing_skills_json", "[]") or "[]")
        r["reasons"] = json.loads(r.pop("reasons_json", "[]") or "[]")
        r["description_preview"] = (r.get("description") or "")[:320]
        r.pop("description", None)
    return rows


def get_recommendation(rec_id: int) -> dict[str, Any] | None:
    row = q1(
        "SELECT r.*, j.title, j.company, j.location, j.url, j.description, j.posted_at, "
        "j.source AS job_source, j.remote, j.salary_text "
        "FROM recommendations r JOIN jobs j ON j.id=r.job_id WHERE r.id=?",
        (rec_id,),
    )
    if not row:
        return None
    for k, default in (
        ("breakdown_json", "{}"), ("matched_skills_json", "[]"),
        ("missing_skills_json", "[]"), ("reasons_json", "[]"),
    ):
        target = k.replace("_json", "")
        row[target] = json.loads(row.pop(k, default) or default)
    return row


def set_recommendation_status(rec_id: int, status: str) -> None:
    execute("UPDATE recommendations SET status=? WHERE id=?", (status, rec_id))


# ----------------------------------------------------------------- documents
def create_document(
    rec_id: int, job_id: int, version_id: int, doc_type: str, title: str,
    content: str, engine: str, original_snapshot: str,
    original_structured: dict, validation: dict,
) -> int:
    return execute(
        "INSERT INTO documents(recommendation_id,job_id,resume_version_id,doc_type,"
        "title,content,engine,original_snapshot,original_structured_json,"
        "validation_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            rec_id, job_id, version_id, doc_type, title, content, engine,
            original_snapshot, json.dumps(original_structured, ensure_ascii=False),
            json.dumps(validation, ensure_ascii=False), now(),
        ),
    )


def list_documents(rec_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    if rec_id:
        rows = q(
            "SELECT id,recommendation_id,job_id,resume_version_id,doc_type,title,engine,"
            "LENGTH(content) AS content_length,created_at FROM documents "
            "WHERE recommendation_id=? ORDER BY id DESC", (rec_id,)
        )
    else:
        rows = q(
            "SELECT d.id,d.recommendation_id,d.job_id,d.resume_version_id,d.doc_type,"
            "d.title,d.engine,LENGTH(d.content) AS content_length,d.created_at,"
            "j.title AS job_title,j.company FROM documents d "
            "JOIN jobs j ON j.id=d.job_id ORDER BY d.id DESC LIMIT ?", (limit,)
        )
    return rows


def get_document(doc_id: int) -> dict[str, Any] | None:
    row = q1("SELECT * FROM documents WHERE id=?", (doc_id,))
    if row:
        row["validation"] = json.loads(row.pop("validation_json", "{}") or "{}")
        row["original_structured"] = json.loads(
            row.pop("original_structured_json", "{}") or "{}"
        )
    return row


def delete_document(doc_id: int) -> dict[str, Any] | None:
    """Delete one generated document; resume versions are never touched."""
    row = q1(
        "SELECT d.*, j.title AS job_title, j.company FROM documents d "
        "JOIN jobs j ON j.id=d.job_id WHERE d.id=?", (doc_id,)
    )
    if not row:
        return None
    execute("DELETE FROM documents WHERE id=?", (doc_id,))
    # Do not let the monitor silently recreate what the user just removed.
    execute("UPDATE recommendations SET docs_suppressed=1 WHERE id=?",
            (row["recommendation_id"],))
    return row


def set_docs_suppressed(rec_id: int, value: int) -> None:
    execute("UPDATE recommendations SET docs_suppressed=? WHERE id=?",
            (1 if value else 0, rec_id))


# -------------------------------------------------------------- monitor runs
def start_run(trigger: str, source: str) -> int:
    return execute(
        "INSERT INTO monitor_runs(started_at,trigger,source,status) VALUES(?,?,?,'running')",
        (now(), trigger, source),
    )


def finish_run(run_id: int, status: str, message: str, fetched: int,
               jobs_new: int, recs_new: int) -> None:
    execute(
        "UPDATE monitor_runs SET finished_at=?,status=?,message=?,jobs_fetched=?,"
        "jobs_new=?,recs_new=? WHERE id=?",
        (now(), status, message, fetched, jobs_new, recs_new, run_id),
    )


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    return q("SELECT * FROM monitor_runs ORDER BY id DESC LIMIT ?", (limit,))


# ------------------------------------------------------------ notifications
def notification_exists(rec_id: int, score: float, email: str) -> bool:
    return q1(
        "SELECT id FROM notifications WHERE rec_id=? AND score=? AND email=?",
        (rec_id, score, email),
    ) is not None


def record_notification(rec_id: int, job_id: int, score: float, email: str,
                        subject: str, status: str, message: str) -> int:
    return execute(
        "INSERT OR IGNORE INTO notifications(rec_id,job_id,score,email,subject,"
        "status,message,sent_at) VALUES(?,?,?,?,?,?,?,?)",
        (rec_id, job_id, score, email, subject, status, message, now()),
    )


def list_notifications(limit: int = 50) -> list[dict[str, Any]]:
    return q(
        "SELECT n.*, j.title, j.company, j.location FROM notifications n "
        "JOIN jobs j ON j.id=n.job_id ORDER BY n.id DESC LIMIT ?", (limit,)
    )


def overview() -> dict[str, Any]:
    def scalar(sql: str, params: tuple = ()) -> int:
        row = q1(sql, params)
        return int(list(row.values())[0] or 0) if row else 0

    latest = q1("SELECT MAX(id) AS v FROM resume_versions") or {}
    vid = latest.get("v") or 0
    return {
        "resume_version_id": vid,
        "resume_versions": scalar("SELECT COUNT(*) FROM resume_versions"),
        "jobs": scalar("SELECT COUNT(*) FROM jobs"),
        "recommendations": scalar("SELECT COUNT(*) FROM recommendations WHERE resume_version_id=?", (vid,)),
        "top_matches": scalar(
            "SELECT COUNT(*) FROM recommendations WHERE resume_version_id=? AND score>=70", (vid,)
        ),
        "documents": scalar("SELECT COUNT(*) FROM documents"),
        "runs": scalar("SELECT COUNT(*) FROM monitor_runs"),
        "last_run": q1("SELECT * FROM monitor_runs ORDER BY id DESC LIMIT 1"),
    }
