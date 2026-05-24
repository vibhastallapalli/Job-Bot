"""
SQLite schema.  All CREATE TABLE statements use IF NOT EXISTS so init_db()
is safe to call on an already-populated database.

New columns added after initial release (description, applicant_count,
easy_apply) are handled by migrate_db() in db.py for existing installs.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT    NOT NULL UNIQUE,
    title           TEXT,
    company         TEXT,
    location        TEXT,
    source          TEXT,
    status          TEXT    DEFAULT 'discovered',
    description     TEXT,
    applicant_count INTEGER,
    easy_apply      INTEGER DEFAULT 0,
    discovered_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       INTEGER NOT NULL REFERENCES jobs(id),
    resume_id    INTEGER REFERENCES resumes(id),
    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    notes        TEXT,
    outcome      TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS resumes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pdf_path      TEXT NOT NULL,
    template_path TEXT,
    compiled_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    level      TEXT     DEFAULT 'INFO',
    module     TEXT,
    message    TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date    TEXT    PRIMARY KEY,
    applied INTEGER DEFAULT 0
);
"""
