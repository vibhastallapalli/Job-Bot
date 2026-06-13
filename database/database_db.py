import sqlite3
import os
from flask import g

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.environ.get("JOB_BOT_DB", os.path.join(BASE_DIR, "job_bot.db"))


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    from database.database_models import SCHEMA
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    migrate_db()


def migrate_db():
    """
    Add columns introduced after the initial schema so existing databases
    are upgraded automatically on app start.  Each ALTER TABLE is wrapped
    in a try/except because SQLite has no IF NOT EXISTS for columns.
    """
    new_columns = [
        "ALTER TABLE jobs ADD COLUMN description     TEXT",
        "ALTER TABLE jobs ADD COLUMN applicant_count INTEGER",
        "ALTER TABLE jobs ADD COLUMN easy_apply      INTEGER DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN notes           TEXT",
        "ALTER TABLE jobs ADD COLUMN interview_date  TEXT",
    ]
    conn = sqlite3.connect(DB_PATH)
    for sql in new_columns:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass   # Column already exists
    conn.commit()
    conn.close()


def seed_demo_data() -> int:
    """Insert demo jobs and logs. Skips URLs already in the DB. Returns count inserted."""
    from datetime import datetime, timedelta

    conn = sqlite3.connect(DB_PATH)
    existing_urls = {r[0] for r in conn.execute("SELECT url FROM jobs").fetchall()}

    now = datetime.utcnow()

    jobs = [
        ("https://stripe.com/jobs/listing/senior-software-engineer",   "Senior Software Engineer",  "Stripe",    "Remote",            "linkedin",   "interview", now - timedelta(days=5)),
        ("https://vercel.com/careers/backend-engineer",                 "Backend Engineer",           "Vercel",    "San Francisco, CA", "greenhouse", "applied",   now - timedelta(days=4)),
        ("https://openai.com/careers/software-engineer",                 "Software Engineer",          "OpenAI",    "Remote",            "linkedin",   "applied",   now - timedelta(days=3)),
        ("https://linear.app/careers/full-stack-engineer",              "Full Stack Engineer",        "Linear",    "Remote",            "greenhouse", "discovered",now - timedelta(days=2)),
        ("https://figma.com/careers/software-engineer-ii",              "Software Engineer II",       "Figma",     "New York, NY",      "indeed",     "rejected",  now - timedelta(days=6)),
        ("https://notion.so/careers/staff-engineer",                    "Staff Engineer",             "Notion",    "Remote",            "linkedin",   "discovered",now - timedelta(days=1)),
        ("https://supabase.com/careers/platform-engineer",              "Platform Engineer",          "Supabase",  "Remote",            "indeed",     "discovered",now - timedelta(hours=12)),
        ("https://fly.io/careers/infrastructure-engineer",              "Infrastructure Engineer",    "Fly.io",    "Remote",            "greenhouse", "applied",   now - timedelta(days=3, hours=2)),
    ]

    inserted = 0
    for url, title, company, location, source, status, ts in jobs:
        if url not in existing_urls:
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO jobs (url,title,company,location,source,status,discovered_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (url, title, company, location, source, status, ts_str, ts_str),
            )
            inserted += 1

    if conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 0:
        logs = [
            ("INFO",    "scraper",    "Scraped 8 new jobs across LinkedIn, Greenhouse, and Indeed",           now - timedelta(days=6, hours=2)),
            ("INFO",    "resume",     "Compiled resume: resume_2026-05-02.pdf",                               now - timedelta(days=6, hours=3)),
            ("INFO",    "automation", "Applied to Backend Engineer at Vercel",                                now - timedelta(days=4)),
            ("INFO",    "automation", "Applied to Software Engineer at OpenAI",                              now - timedelta(days=3)),
            ("INFO",    "automation", "Applied to Infrastructure Engineer at Fly.io",                         now - timedelta(days=3, hours=1)),
            ("WARNING", "scraper",    "Indeed rate limit reached, retrying in 60s",                           now - timedelta(days=1, hours=3)),
            ("ERROR",   "automation", "Failed to parse application form on Linear job board",                 now - timedelta(days=2, hours=1)),
            ("INFO",    "automation", "Interview scheduled — Senior Software Engineer at Stripe",             now - timedelta(days=2)),
        ]
        for level, module, message, ts in logs:
            conn.execute(
                "INSERT INTO logs (level,module,message,created_at) VALUES (?,?,?,?)",
                (level, module, message, ts.strftime("%Y-%m-%d %H:%M:%S")),
            )

    conn.commit()
    conn.close()
    return inserted
