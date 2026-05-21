"""
Job scraper coordinator.

JobScraper is the single entry point for all job sources. It reads keywords,
locations, and source list from config["job_search"], dispatches to a
per-source handler, applies the company blacklist, and returns a unified
list of JobListing objects.

Currently implemented sources:
  linkedin — LinkedInScraper (Playwright, Easy Apply only)

Stub sources (NotImplementedError, ignored gracefully):
  indeed, greenhouse
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class JobListing:
    url: str
    title: str
    company: str
    location: str
    source: str
    description: str = ""
    applicant_count: int | None = None
    easy_apply: bool = False
    discovered_at: datetime = field(default_factory=datetime.utcnow)


class JobScraper:
    def __init__(self, config: dict, db_conn):
        """
        config   — the full JOB_BOT config dict (not just job_search sub-key)
        db_conn  — an open sqlite3.Connection (created in the caller's thread)
        """
        self._config  = config
        js = config.get("job_search", {})
        self.keywords  = js.get("keywords", [])
        self.locations = js.get("locations", [])
        self.sources   = js.get("sources", [])
        self.blacklist = set(js.get("blacklisted_companies", []))
        self.db = db_conn

    # ── Public API ──────────────────────────────────────────────────────────

    def fetch_all(self) -> list[JobListing]:
        """
        Scrape every configured source.  Unimplemented sources are skipped
        silently; errors in any one source are logged and do not abort others.
        Returns deduplicated new listings after applying the company blacklist.
        """
        results: list[JobListing] = []
        for source in self.sources:
            handler = getattr(self, f"_fetch_{source}", None)
            if handler is None:
                logger.warning("No handler for source '%s' — skipping", source)
                continue
            try:
                results.extend(handler())
            except NotImplementedError:
                logger.info("Source '%s' not yet implemented — skipping", source)
            except Exception as exc:
                logger.error("Source '%s' failed: %s", source, exc)
                self.db.execute(
                    "INSERT INTO logs (level, module, message) VALUES ('ERROR', 'scraper', ?)",
                    (f"Source '{source}' error: {exc}",),
                )
                self.db.commit()

        return [j for j in results if j.company not in self.blacklist]

    # ── LinkedIn ─────────────────────────────────────────────────────────────

    def _fetch_linkedin(self) -> list[JobListing]:
        from modules.jobs.jobs_linkedin import LinkedInScraper

        scraper = LinkedInScraper(self._config)
        scraped = scraper.scrape(
            keywords=self.keywords,
            locations=self.locations,
            db_conn=self.db,
        )
        return [
            JobListing(
                url=j.url,
                title=j.title,
                company=j.company,
                location=j.location,
                source="linkedin",
                description=j.description,
                applicant_count=j.applicant_count,
                easy_apply=j.easy_apply,
            )
            for j in scraped
        ]

    # ── Stubs (future sources) ────────────────────────────────────────────────

    def _fetch_indeed(self) -> list[JobListing]:
        raise NotImplementedError

    def _fetch_greenhouse(self) -> list[JobListing]:
        raise NotImplementedError
