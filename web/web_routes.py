import os
import threading
import sqlite3

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, current_app, flash, jsonify,
)
from database.database_db import get_db, DB_PATH

main_bp = Blueprint("main", __name__)

_VALID_STATUSES    = {"discovered", "applied", "interview", "offer", "rejected", "ignored", "queued"}
_SCRAPE_IN_PROGRESS = False   # guarded by GIL; single scrape at a time


# ── Dashboard ──────────────────────────────────────────────────────────────

@main_bp.route("/")
def dashboard():
    db = get_db()
    applied    = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','interview','offer','rejected')").fetchone()[0]
    interviews = db.execute("SELECT COUNT(*) FROM jobs WHERE status='interview'").fetchone()[0]
    offers     = db.execute("SELECT COUNT(*) FROM jobs WHERE status='offer'").fetchone()[0]
    stats = {
        "total":        db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
        "applied":      applied,
        "interviews":   interviews,
        "rejected":     db.execute("SELECT COUNT(*) FROM jobs WHERE status='rejected'").fetchone()[0],
        "success_rate": round((interviews + offers) / applied * 100) if applied else 0,
    }
    recent_jobs = db.execute("SELECT * FROM jobs ORDER BY discovered_at DESC LIMIT 8").fetchall()
    recent_logs = db.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT 6").fetchall()

    from modules.email.email_outlook import TOKEN_CACHE_PATH
    outlook_connected = os.path.exists(TOKEN_CACHE_PATH)

    return render_template(
        "templates_index.html",
        stats=stats,
        recent_jobs=recent_jobs,
        recent_logs=recent_logs,
        outlook_connected=outlook_connected,
    )


# ── Jobs list ──────────────────────────────────────────────────────────────

@main_bp.route("/jobs")
def jobs():
    status_filter = request.args.get("status", "all")
    db = get_db()

    if status_filter == "all":
        job_rows = db.execute("SELECT * FROM jobs ORDER BY discovered_at DESC").fetchall()
    else:
        job_rows = db.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY discovered_at DESC", (status_filter,)
        ).fetchall()

    counts = {s: db.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (s,)).fetchone()[0]
              for s in ("discovered", "applied", "interview", "rejected", "queued")}
    counts["all"] = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    dow_rows = db.execute(
        "SELECT CAST(strftime('%w', submitted_at) AS INTEGER) AS dow, COUNT(*) AS cnt "
        "FROM applications GROUP BY dow"
    ).fetchall()
    dow_map = {r["dow"]: r["cnt"] for r in dow_rows}
    # strftime('%w'): 0=Sun, 1=Mon … 6=Sat — display Mon through Sun
    _dow_order  = [1, 2, 3, 4, 5, 6, 0]
    _dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    _max = max((dow_map.get(d, 0) for d in _dow_order), default=0) or 1
    heatmap = [
        {"label": lbl, "count": dow_map.get(d, 0), "weight": round(dow_map.get(d, 0) / _max, 3)}
        for d, lbl in zip(_dow_order, _dow_labels)
    ]

    return render_template("templates_jobs.html", jobs=job_rows, status=status_filter,
                           counts=counts, heatmap=heatmap)


@main_bp.route("/jobs/search")
def jobs_search():
    q  = request.args.get("q", "").strip()
    db = get_db()
    if q:
        pattern = f"%{q}%"
        rows = db.execute(
            "SELECT id, title, company, status FROM jobs "
            "WHERE title LIKE ? OR company LIKE ? "
            "ORDER BY discovered_at DESC LIMIT 20",
            (pattern, pattern),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, title, company, status FROM jobs "
            "ORDER BY discovered_at DESC LIMIT 20"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@main_bp.route("/jobs/scrape", methods=["POST"])
def scrape_jobs():
    global _SCRAPE_IN_PROGRESS
    if _SCRAPE_IN_PROGRESS:
        flash("A scrape is already running — check the live log below for progress.", "warning")
        return redirect(url_for("main.jobs"))

    config = current_app.config["JOB_BOT"]
    li_cfg = config.get("linkedin", {})

    if not li_cfg.get("email") or not li_cfg.get("password"):
        flash(
            "LinkedIn credentials are not configured. "
            "Enter your email and password in Settings first.",
            "warning",
        )
        return redirect(url_for("main.settings"))

    # Warn if this is the first run (no saved session yet)
    state_file = os.path.join(
        os.path.dirname(DB_PATH),
        li_cfg.get("state_file", "linkedin_state.json"),
    )
    if not os.path.exists(state_file):
        flash(
            "No LinkedIn session found — a browser window will open so you can log in. "
            "Complete sign-in and any 2FA in that window; scraping starts automatically afterwards.",
            "warning",
        )

    t = threading.Thread(target=_scrape_worker, args=(config,), daemon=True)
    t.start()

    flash("Scrape started — watch the live log below for progress.", "info")
    return redirect(url_for("main.jobs"))


def _scrape_worker(config: dict) -> None:
    """Background thread: open own DB connection, run scraper, log result."""
    global _SCRAPE_IN_PROGRESS
    _SCRAPE_IN_PROGRESS = True
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        from modules.jobs.jobs_scraper import JobScraper
        scraper  = JobScraper(config, conn)
        new_jobs = scraper.fetch_all()
        conn.execute(
            "INSERT INTO logs (level, module, message) VALUES ('INFO', 'scraper', ?)",
            (f"Scrape complete — {len(new_jobs)} new LinkedIn jobs saved",),
        )
        conn.commit()
    except Exception as exc:
        conn.execute(
            "INSERT INTO logs (level, module, message) VALUES ('ERROR', 'scraper', ?)",
            (f"Scrape failed: {exc}",),
        )
        conn.commit()
    finally:
        _SCRAPE_IN_PROGRESS = False
        conn.close()


@main_bp.route("/jobs/scrape/status")
def scrape_status():
    db   = get_db()
    rows = db.execute(
        "SELECT level, message, created_at FROM logs "
        "WHERE module IN ('scraper', 'linkedin') "
        "ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    return jsonify({
        "in_progress": _SCRAPE_IN_PROGRESS,
        "logs": [
            {"level": r["level"], "message": r["message"], "time": str(r["created_at"])}
            for r in rows
        ],
    })


# ── Queue ──────────────────────────────────────────────────────────────────

@main_bp.route("/jobs/queue", methods=["POST"])
def queue_jobs():
    job_ids = request.form.getlist("job_ids")
    if not job_ids:
        flash("No jobs selected.", "warning")
        return redirect(url_for("main.jobs"))

    db = get_db()
    queued_count = 0
    for raw_id in job_ids:
        try:
            job_id = int(raw_id)
        except ValueError:
            continue
        job = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job and job["status"] not in ("applied", "interview", "offer", "queued"):
            db.execute(
                "UPDATE jobs SET status='queued', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,),
            )
            queued_count += 1

    db.commit()
    flash(f"{queued_count} job(s) added to the queue.", "success")
    return redirect(url_for("main.jobs"))


@main_bp.route("/jobs/queue/stats")
def queue_stats():
    from datetime import date
    db = get_db()
    queued = db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]
    today = date.today().isoformat()
    row = db.execute("SELECT applied FROM daily_stats WHERE date=?", (today,)).fetchone()
    applied_today = row["applied"] if row else 0
    return jsonify({
        "queued": queued,
        "applied_today": applied_today,
        "remaining_today": max(0, 12 - applied_today),
        "cap": 12,
    })


# ── Job detail ─────────────────────────────────────────────────────────────

@main_bp.route("/jobs/<int:job_id>")
def job_detail(job_id):
    db = get_db()
    job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("main.jobs"))
    return render_template("templates_job_detail.html", job=job)


@main_bp.route("/jobs/<int:job_id>/status", methods=["POST"])
def update_job_status(job_id):
    new_status = request.form.get("status", "").strip()
    if new_status not in _VALID_STATUSES:
        flash("Invalid status.", "error")
        return redirect(url_for("main.job_detail", job_id=job_id))
    db = get_db()
    job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("main.jobs"))
    db.execute("UPDATE jobs SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_status, job_id))
    db.execute("INSERT INTO logs (level,module,message) VALUES ('INFO','ui',?)",
               (f"Status → '{new_status}': {job['title']} at {job['company']}",))
    db.commit()
    flash(f"Status updated to '{new_status}'.", "success")
    return redirect(url_for("main.job_detail", job_id=job_id))


@main_bp.route("/jobs/<int:job_id>/apply", methods=["POST"])
def apply_job(job_id):
    db = get_db()
    job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("main.jobs"))
    db.execute("UPDATE jobs SET status='applied', updated_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
    db.execute("INSERT INTO logs (level,module,message) VALUES ('INFO','ui',?)",
               (f"Marked as applied: {job['title']} at {job['company']}",))
    db.commit()
    flash(f"'{job['title']}' marked as applied.", "success")
    next_url = request.form.get("next") or url_for("main.jobs")
    return redirect(next_url)


# ── Auto-apply ─────────────────────────────────────────────────────────────

_AUTOMATION_IN_PROGRESS: set = set()   # job IDs currently being automated


@main_bp.route("/jobs/<int:job_id>/automate", methods=["POST"])
def automate_job(job_id):
    db = get_db()
    job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("main.jobs"))

    if job_id in _AUTOMATION_IN_PROGRESS:
        flash("Automation already running for this job.", "warning")
        return redirect(url_for("main.job_detail", job_id=job_id))

    config = current_app.config["JOB_BOT"]
    li_cfg = config.get("linkedin", {})
    if not li_cfg.get("email") or not li_cfg.get("password"):
        flash(
            "LinkedIn credentials are not configured. "
            "Enter your email and password in Settings first.",
            "warning",
        )
        return redirect(url_for("main.settings"))

    resume_path = _find_latest_resume(config)

    _AUTOMATION_IN_PROGRESS.add(job_id)
    t = threading.Thread(
        target=_automate_worker,
        args=(config, dict(job), resume_path),
        daemon=True,
    )
    t.start()

    flash(
        "Easy Apply automation started in the background. "
        "Watch the Logs page for progress.",
        "info",
    )
    return redirect(url_for("main.job_detail", job_id=job_id))


def _find_latest_resume(config: dict):
    """Return path to the most-recently-modified PDF in the resume output dir, or None."""
    import glob as _glob
    output_dir = config.get("resume", {}).get("output_dir", "output/resumes")
    pattern = os.path.join(output_dir, "*.pdf")
    pdfs = _glob.glob(pattern)
    return max(pdfs, key=os.path.getmtime) if pdfs else None


def _automate_worker(config: dict, job: dict, resume_path) -> None:
    """Background thread: run Easy Apply automation for a single job."""
    job_id = job["id"]
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        from modules.browser.browser_automation import ApplicationBot
        bot    = ApplicationBot(config)
        result = bot.apply_job(job, conn, resume_path=resume_path)
        if result.success:
            conn.execute(
                "INSERT INTO logs (level, module, message) VALUES ('INFO', 'automation', ?)",
                (f"Applied to {job['title']} at {job['company']}",),
            )
        else:
            conn.execute(
                "INSERT INTO logs (level, module, message) VALUES ('WARNING', 'automation', ?)",
                (f"Auto-apply failed for {job['title']} at {job['company']}: {result.error}",),
            )
            if job.get("status") == "queued":
                conn.execute(
                    "UPDATE jobs SET status='discovered', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (job_id,),
                )
        conn.commit()
    except Exception as exc:
        conn.execute(
            "INSERT INTO logs (level, module, message) VALUES ('ERROR', 'automation', ?)",
            (f"Auto-apply error for job {job_id}: {exc}",),
        )
        conn.commit()
    finally:
        _AUTOMATION_IN_PROGRESS.discard(job_id)
        conn.close()


# ── Resume ─────────────────────────────────────────────────────────────────

@main_bp.route("/resume")
def resume():
    return render_template("templates_resume.html")


@main_bp.route("/resume/compile", methods=["POST"])
def compile_resume():
    flash("Resume compilation is not yet implemented.", "warning")
    return redirect(url_for("main.resume"))


# ── Settings ───────────────────────────────────────────────────────────────

@main_bp.route("/settings", methods=["GET", "POST"])
def settings():
    from utils.utils_helpers import load_config, save_config

    if request.method == "POST":
        cfg = load_config()

        # Guarantee every top-level section exists before writing into it
        cfg.setdefault("applicant", {})
        cfg.setdefault("job_search", {})
        cfg.setdefault("linkedin", {})
        cfg.setdefault("microsoft", {})
        cfg.setdefault("browser", {})
        cfg.setdefault("answers", {})
        cfg.setdefault("ai", {})

        # AI
        cfg["ai"]["enabled"] = "ai_enabled" in request.form
        ai_key = request.form.get("ai_api_key", "").strip()
        if ai_key:
            cfg["ai"]["anthropic_api_key"] = ai_key

        # Applicant info
        cfg["applicant"]["name"]     = request.form.get("applicant_name", "").strip()
        cfg["applicant"]["email"]    = request.form.get("applicant_email", "").strip()
        cfg["applicant"]["phone"]    = request.form.get("applicant_phone", "").strip()
        cfg["applicant"]["linkedin"] = request.form.get("applicant_linkedin", "").strip()
        cfg["applicant"]["github"]   = request.form.get("applicant_github", "").strip()
        cfg["applicant"]["city"]     = request.form.get("applicant_city", "").strip()
        cfg["applicant"]["state"]    = request.form.get("applicant_state", "").strip()
        cfg["applicant"]["country"]  = request.form.get("applicant_country", "United States").strip()
        cfg["applicant"]["bio"]      = request.form.get("applicant_bio", "").strip()

        # Job search
        raw_kw  = request.form.get("keywords", "")
        raw_loc = request.form.get("locations", "")
        cfg["job_search"]["keywords"]             = [k.strip() for k in raw_kw.split(",")  if k.strip()]
        cfg["job_search"]["locations"]            = [l.strip() for l in raw_loc.split(",") if l.strip()]
        cfg["job_search"]["blacklisted_companies"] = [
            c.strip() for c in request.form.get("blacklist", "").split(",") if c.strip()
        ]
        cfg["job_search"]["job_types"]  = request.form.getlist("job_types")
        cfg["job_search"]["work_types"] = request.form.getlist("work_types")

        # LinkedIn
        cfg["linkedin"]["email"] = request.form.get("li_email", "").strip()
        li_pw = request.form.get("li_password", "").strip()
        if li_pw:
            cfg["linkedin"]["password"] = li_pw
        cfg["linkedin"]["applicant_filter"] = request.form.get("li_applicant_filter", "no_filter")
        raw_pages = request.form.get("li_max_pages", "3").strip()
        cfg["linkedin"]["max_pages"]   = int(raw_pages) if raw_pages.isdigit() else 3
        cfg["linkedin"]["date_posted"] = request.form.get("li_date_posted", "past_week")

        # Microsoft / Outlook
        cfg["microsoft"]["client_id"]    = request.form.get("ms_client_id", "").strip()
        ms_secret = request.form.get("ms_client_secret", "").strip()
        if ms_secret:
            cfg["microsoft"]["client_secret"] = ms_secret
        cfg["microsoft"]["tenant_id"]    = request.form.get("ms_tenant_id", "common").strip()
        cfg["microsoft"]["redirect_uri"] = request.form.get(
            "ms_redirect_uri", "http://localhost:5000/email/auth/callback"
        ).strip()

        # Application answers
        cfg["answers"]["work_authorization"]   = request.form.get("ans_work_authorization", "Yes")
        cfg["answers"]["requires_sponsorship"] = request.form.get("ans_requires_sponsorship", "No")
        cfg["answers"]["willing_to_relocate"]  = request.form.get("ans_willing_to_relocate", "No")
        cfg["answers"]["years_experience"]     = request.form.get("ans_years_experience", "").strip()
        cfg["answers"]["desired_salary"]       = request.form.get("ans_desired_salary", "").strip()
        cfg["answers"]["earliest_start_date"]  = request.form.get("ans_earliest_start_date", "2 weeks").strip()
        cfg["answers"]["cover_letter"]         = request.form.get("ans_cover_letter", "").strip()

        # Browser
        cfg["browser"]["headless"] = "headless" in request.form

        save_config(cfg)
        current_app.config["JOB_BOT"] = cfg
        flash("Settings saved.", "success")
        return redirect(url_for("main.settings"))

    # Always read from disk so the form reflects the true persisted state
    return render_template("templates_settings.html", config=load_config())


# ── Applications history ───────────────────────────────────────────────────

@main_bp.route("/applications")
def applications():
    db = get_db()
    rows = db.execute("""
        SELECT a.id, a.submitted_at, a.notes, a.outcome,
               j.title, j.company, j.url, j.id AS job_id
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        ORDER BY a.submitted_at DESC
    """).fetchall()
    return render_template("templates_applications.html", applications=rows)


# ── Logs ───────────────────────────────────────────────────────────────────

@main_bp.route("/logs")
def logs():
    db = get_db()
    log_rows = db.execute("SELECT * FROM logs ORDER BY created_at DESC LIMIT 200").fetchall()
    return render_template("templates_logs.html", logs=log_rows)


# ── Outlook email sync ─────────────────────────────────────────────────────

@main_bp.route("/email/auth")
def email_auth():
    config = current_app.config["JOB_BOT"]
    ms = config.get("microsoft", {})
    if not ms.get("client_id") or not ms.get("client_secret"):
        flash(
            "Microsoft credentials not configured. "
            "Fill in client_id, client_secret, and tenant_id in Settings first.",
            "warning",
        )
        return redirect(url_for("main.settings"))

    from modules.email.email_outlook import OutlookMonitor
    auth_url = OutlookMonitor(config).get_auth_url()
    return redirect(auth_url)


@main_bp.route("/email/auth/callback")
def email_auth_callback():
    error = request.args.get("error")
    if error:
        flash(
            f"Microsoft auth error: {request.args.get('error_description', error)}",
            "error",
        )
        return redirect(url_for("main.dashboard"))

    code = request.args.get("code")
    if not code:
        flash("No authorization code received from Microsoft.", "error")
        return redirect(url_for("main.dashboard"))

    config = current_app.config["JOB_BOT"]
    from modules.email.email_outlook import OutlookMonitor
    result = OutlookMonitor(config).exchange_code(code)

    if "access_token" in result:
        flash("Outlook connected. You can now sync your inbox from the dashboard.", "success")
    else:
        flash(
            f"Outlook auth failed: {result.get('error_description', result.get('error', 'unknown error'))}",
            "error",
        )
    return redirect(url_for("main.dashboard"))


@main_bp.route("/email/disconnect", methods=["POST"])
def email_disconnect():
    from modules.email.email_outlook import TOKEN_CACHE_PATH
    if os.path.exists(TOKEN_CACHE_PATH):
        os.remove(TOKEN_CACHE_PATH)
    flash("Outlook disconnected.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/email/sync", methods=["POST"])
def email_sync():
    config = current_app.config["JOB_BOT"]
    from modules.email.email_outlook import OutlookMonitor
    if not OutlookMonitor(config).is_authenticated():
        flash("Outlook not connected — click Connect Outlook first.", "warning")
        return redirect(url_for("main.dashboard"))

    t = threading.Thread(target=_email_sync_worker, args=(config,), daemon=True)
    t.start()
    flash("Email sync started. Check the Logs page for results.", "info")
    return redirect(url_for("main.dashboard"))


def _email_sync_worker(config: dict) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        from modules.email.email_outlook import OutlookMonitor
        result = OutlookMonitor(config).sync(conn)
        conn.execute(
            "INSERT INTO logs (level, module, message) VALUES ('INFO', 'email', ?)",
            (
                f"Sync complete — {result['processed']} emails scanned, "
                f"{result['updated']} job(s) updated",
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.execute(
            "INSERT INTO logs (level, module, message) VALUES ('ERROR', 'email', ?)",
            (f"Email sync failed: {exc}",),
        )
        conn.commit()
    finally:
        conn.close()


# ── Demo seed ──────────────────────────────────────────────────────────────

@main_bp.route("/demo/seed", methods=["POST"])
def seed_demo():
    from database.database_db import seed_demo_data
    count = seed_demo_data()
    flash(f"Demo data loaded ({count} jobs added).", "success")
    return redirect(url_for("main.dashboard"))
