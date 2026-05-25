import atexit
import json
import os
import sqlite3
import threading

from flask import Flask
from web.web_routes import main_bp, _automate_worker, _find_latest_resume, _AUTOMATION_IN_PROGRESS
from database.database_db import init_db, close_db, DB_PATH


def create_app():
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")

    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path) as f:
        app.config["JOB_BOT"] = json.load(f)

    app.secret_key = app.config["JOB_BOT"].get("secret_key", "dev-secret-change-me")

    init_db()
    app.teardown_appcontext(close_db)
    app.register_blueprint(main_bp)

    @app.errorhandler(404)
    def page_not_found(e):
        from flask import render_template
        return render_template("templates_404.html"), 404

    @app.template_filter("fmtdt")
    def fmtdt(s):
        if not s:
            return "—"
        return str(s)[:16].replace("T", " ")

    # Start queue scheduler — only in the child process when debug reloader is active
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _start_scheduler(app)

    return app


def _queue_tick(app):
    """Scheduler tick: pick the oldest queued job and run auto-apply, enforcing the 12/day cap."""
    from datetime import date

    if _AUTOMATION_IN_PROGRESS:
        return

    today = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT applied FROM daily_stats WHERE date=?", (today,)).fetchone()
        applied_today = row["applied"] if row else 0
        if applied_today >= 12:
            return

        job = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY updated_at ASC LIMIT 1"
        ).fetchone()
        if not job:
            return

        with app.app_context():
            config = app.config["JOB_BOT"]

        resume_path = _find_latest_resume(config)

        conn.execute(
            "INSERT INTO daily_stats (date, applied) VALUES (?, 1) "
            "ON CONFLICT(date) DO UPDATE SET applied = applied + 1",
            (today,),
        )
        conn.commit()

        _AUTOMATION_IN_PROGRESS.add(job["id"])
        t = threading.Thread(
            target=_automate_worker,
            args=(config, dict(job), resume_path),
            daemon=True,
        )
        t.start()
    finally:
        conn.close()


def _start_scheduler(app):
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    # Fire every ~6.5 min ±2.5 min, giving a 4–9 min random window
    scheduler.add_job(
        lambda: _queue_tick(app),
        "interval",
        seconds=390,
        jitter=150,
    )
    scheduler.start()
    atexit.register(scheduler.shutdown)
    return scheduler


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
