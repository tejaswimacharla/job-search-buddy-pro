from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date

from .config import DATA_DIR
from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    ats           TEXT NOT NULL,
    company       TEXT NOT NULL,
    ext_id        TEXT NOT NULL,
    title         TEXT NOT NULL,
    location      TEXT,
    url           TEXT,
    apply_url     TEXT,
    description   TEXT,
    posted_at     TEXT,
    remote        INTEGER,
    compensation  TEXT,
    extra         TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    closed        INTEGER DEFAULT 0,
    score         REAL,
    score_detail  TEXT,
    llm_score     REAL,
    llm_detail    TEXT,
    -- new -> shortlisted -> tailored -> prefilled -> submitted ; or skipped
    status        TEXT NOT NULL DEFAULT 'new',
    resume_output TEXT,   -- output/<folder> of the tailored resume
    notes         TEXT
);
CREATE TABLE IF NOT EXISTS scrape_runs (
    ts        TEXT NOT NULL,
    boards    INTEGER,
    scanned   INTEGER,
    kept      INTEGER,
    new       INTEGER,
    errors    INTEGER
);
CREATE INDEX IF NOT EXISTS jobs_score ON jobs(score);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
"""

STATUSES = ("new", "shortlisted", "tailored", "prefilled", "submitted", "skipped")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / "jobs.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "submitted_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN submitted_at TEXT")
    if "rta_output" in cols:   # databases from before resumes were tailored in this project
        conn.execute("ALTER TABLE jobs RENAME COLUMN rta_output TO resume_output")
    return conn


def upsert(conn: sqlite3.Connection, job: Job) -> bool:
    """Insert or refresh a job. Returns True if it was new."""
    today = date.today().isoformat()
    existing = conn.execute("SELECT id, description FROM jobs WHERE id = ?", (job.id,)).fetchone()
    d = asdict(job)
    d["extra"] = json.dumps(d["extra"])
    d["remote"] = int(job.remote)
    if existing:
        # Don't clobber a hydrated description with an empty list-view one.
        desc = job.description or existing["description"]
        conn.execute(
            """UPDATE jobs SET title=?, location=?, url=?, apply_url=?, description=?, posted_at=COALESCE(?, posted_at),
               remote=?, compensation=?, extra=?, last_seen=?, closed=0 WHERE id=?""",
            (job.title, job.location, job.url, job.apply_url, desc, job.posted_at, d["remote"],
             job.compensation, d["extra"], today, job.id))
        return False
    conn.execute(
        """INSERT INTO jobs (id, ats, company, ext_id, title, location, url, apply_url, description,
           posted_at, remote, compensation, extra, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (job.id, job.ats, job.company, job.ext_id, job.title, job.location, job.url, job.apply_url,
         job.description, job.posted_at, d["remote"], job.compensation, d["extra"], today, today))
    return True


def row_to_job(row: sqlite3.Row) -> Job:
    return Job(ats=row["ats"], company=row["company"], ext_id=row["ext_id"], title=row["title"],
               location=row["location"] or "", url=row["url"], apply_url=row["apply_url"],
               description=row["description"] or "", posted_at=row["posted_at"],
               remote=bool(row["remote"]), compensation=row["compensation"] or "",
               extra=json.loads(row["extra"] or "{}"))


def get(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    """Look up by full id or by unique prefix / numeric rowid shown in listings."""
    if key.isdigit():
        row = conn.execute("SELECT * FROM jobs WHERE rowid = ?", (int(key),)).fetchone()
        if row:
            return row
    rows = conn.execute("SELECT * FROM jobs WHERE id = ? OR id LIKE ?", (key, key + "%")).fetchall()
    return rows[0] if len(rows) == 1 else None


def set_status(conn: sqlite3.Connection, job_id: str, status: str, **fields) -> None:
    assert status in STATUSES, status
    if status == "submitted":
        fields.setdefault("submitted_at", date.today().isoformat())
    sets = ", ".join(["status = ?"] + [f"{k} = ?" for k in fields])
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (status, *fields.values(), job_id))
    conn.commit()
