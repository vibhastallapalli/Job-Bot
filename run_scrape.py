"""
Standalone scrape runner.

Run with:  py -3.13 run_scrape.py

Opens a visible browser so you can sign in to LinkedIn, then scrapes jobs
matching your config and saves them to the database.
"""

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright

from utils.utils_helpers import load_config
from database.database_db import DB_PATH, init_db
from modules.jobs.jobs_linkedin import LinkedInScraper, _db_log


def do_manual_login(config: dict) -> None:
    """Open a visible browser, let the user log in, then save the session."""
    li         = config.get("linkedin", {})
    state_file = os.path.join(ROOT, li.get("state_file", "linkedin_state.json"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        try:
            page.goto(
                "https://www.linkedin.com/login",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except Exception as exc:
            browser.close()
            raise RuntimeError(f"Could not open LinkedIn login page: {exc}") from exc

        try:
            page.evaluate("window.focus()")
        except Exception:
            pass

        input("\nPress Enter in this terminal when you are logged in and can see your LinkedIn feed: ")

        print("\n  Saving session...", flush=True)
        ctx.storage_state(path=state_file)
        print(f"  Session saved to {os.path.basename(state_file)}")
        print("  Starting scrape...\n", flush=True)

        try:
            ctx.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass


def main():
    print("Loading config...")
    config = load_config()

    li = config.get("linkedin", {})
    if not li.get("email") or not li.get("password"):
        print("ERROR: linkedin.email and linkedin.password must be set in config.json")
        print("       Go to http://localhost:5000/settings to fill them in.")
        sys.exit(1)

    js        = config.get("job_search", {})
    keywords  = js.get("keywords",  [])
    locations = js.get("locations", [])

    if not keywords or not locations:
        print("ERROR: job_search.keywords and job_search.locations are empty in config.json")
        sys.exit(1)

    print(f"Keywords : {keywords}")
    print(f"Locations: {locations}")

    init_db()

    # Always do a fresh manual login so the session is guaranteed to be valid.
    # This overwrites any stale linkedin_state.json from a previous run.
    do_manual_login(config)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        scraper  = LinkedInScraper(config)
        new_jobs = scraper.scrape(keywords=keywords, locations=locations, db_conn=conn)

        conn.execute(
            "INSERT INTO logs (level, module, message) VALUES ('INFO', 'scraper', ?)",
            (f"Scrape complete — {len(new_jobs)} new jobs saved",),
        )
        conn.commit()
        print(f"\nDone — {len(new_jobs)} new jobs saved to the database.")
        print("  Open http://localhost:5000/jobs to review them.\n")

    except KeyboardInterrupt:
        print("\nScrape cancelled.")
    except Exception as exc:
        try:
            conn.execute(
                "INSERT INTO logs (level, module, message) VALUES ('ERROR', 'scraper', ?)",
                (f"Scrape failed: {exc}",),
            )
            conn.commit()
        except Exception:
            pass
        print(f"\nERROR: {exc}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
