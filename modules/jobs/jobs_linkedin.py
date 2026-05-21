"""
LinkedIn Easy Apply job scraper using Playwright.

Auth flow:
  - First run: opens the login page, fills credentials, saves browser storage
    state to linkedin_state.json so subsequent runs skip login entirely.
  - If saved state is expired, falls back to a fresh login automatically.
  - 2FA / CAPTCHA checkpoint: only resolvable in headed mode (headless=false).
    The scraper pauses and waits up to 2 minutes for the user to complete it,
    then saves the resulting session.

Scrape flow (per search query × location pair):
  1. Build search queries: keyword + job_type + term_length combinations.
  2. Build a LinkedIn Jobs search URL with f_AL=true (Easy Apply only).
  3. Load up to max_pages pages (25 jobs each).
  4. For each job card, click it to load the right-side detail panel.
  5. Extract: title, company, location, applicant count, full description.
  6. Verify the Easy Apply button is present in the detail panel.
  7. Post-filter by applicant_count if applicant_filter is set (under_10/25/50/100).
  8. Skip jobs already in the DB (checked by canonical URL).
  9. Insert new jobs into the DB and log progress in real time.
"""

import json
import logging
import os
import re
import time
import random
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse, urlunparse

from playwright.sync_api import (
    sync_playwright,
    Page,
    BrowserContext,
    TimeoutError as PlaywrightTimeout,
)

logger = logging.getLogger(__name__)

# ── Selector bundles ───────────────────────────────────────────────────────
# LinkedIn redesigns their DOM regularly. Each list is tried in order;
# the first matching, non-empty result wins.

_CARD_SEL        = "li.jobs-search-results__list-item"

_CARD_LINK       = [
    "a.job-card-list__title--link",
    "a.job-card-container__link",
    ".job-card-list__title a",
    "a[data-tracking-control-name*='job-card']",
]
_CARD_COMPANY    = [
    ".job-card-container__primary-description",
    ".job-card-container__company-name",
    "span.artdeco-entity-lockup__subtitle",
]
_CARD_LOCATION   = [
    "li.job-card-container__metadata-item",
    ".job-card-container__metadata-item",
    ".artdeco-entity-lockup__caption li",
]

_DETAIL_TITLE    = [
    "h2.job-details-jobs-unified-top-card__job-title",
    ".job-details-jobs-unified-top-card__job-title",
    ".jobs-unified-top-card__job-title",
    "h1.t-24.t-bold",
]
_DETAIL_COMPANY  = [
    "a.job-details-jobs-unified-top-card__company-name-link",
    ".job-details-jobs-unified-top-card__company-name",
    ".jobs-unified-top-card__company-name a",
    "a.topcard__org-name-link",
]
_DETAIL_LOCATION = [
    ".job-details-jobs-unified-top-card__bullet",
    ".job-details-jobs-unified-top-card__primary-description-without-tagline span",
    ".jobs-unified-top-card__bullet",
    ".topcard__flavor--bullet",
]
_DETAIL_APPLICANTS = [
    "span.tvm__text",
    ".job-details-jobs-unified-top-card__applicant-count",
    ".jobs-unified-top-card__applicant-count",
    "span.jobs-unified-top-card__subtitle-secondary-grouping span",
]
_DETAIL_DESC     = [
    "#job-details",
    ".job-description",
    ".jobs-description-content__text",
    ".jobs-description__content",
    "article.job-view-layout",
]
_EASY_APPLY_BTN  = [
    "button.jobs-apply-button",
    "button[aria-label*='Easy Apply']",
    ".jobs-apply-button--top-card",
    "button.jobs-s-apply",
]


# ── Data class ─────────────────────────────────────────────────────────────

@dataclass
class ScrapedJob:
    url: str
    title: str
    company: str
    location: str
    description: str
    applicant_count: int | None
    easy_apply: bool


# ── Main scraper ───────────────────────────────────────────────────────────

class LinkedInScraper:
    BASE_URL   = "https://www.linkedin.com"
    SEARCH_URL = "https://www.linkedin.com/jobs/search/"

    _DATE_MAP = {
        "past_day":   "r86400",
        "past_week":  "r604800",
        "past_month": "r2592000",
    }

    # Maps UI job_type values → search term to append
    _JOB_TYPE_TERMS = {
        "Co-op":      "coop",
        "Internship":  "internship",
        "Full-time":   "full-time",
        "Part-time":   "part-time",
    }

    # Maps UI applicant_filter values → max applicant count (post-scrape filter)
    _APPLICANT_FILTER_LIMITS = {
        "under_10":  10,
        "under_25":  25,
        "under_50":  50,
        "under_100": 100,
    }

    # LinkedIn f_WT parameter codes for work arrangement
    _WORK_TYPE_CODES = {
        "On-site": "1",
        "Remote":  "2",
        "Hybrid":  "3",
    }

    def __init__(self, config: dict):
        li = config.get("linkedin", {})
        br = config.get("browser", {})
        js = config.get("job_search", {})

        self.email    = li.get("email", "")
        self.password = li.get("password", "")
        self.max_pages       = int(li.get("max_pages", 3))
        self.applicant_filter = li.get("applicant_filter", "no_filter")
        self.date_posted     = li.get("date_posted", "past_week")

        self.job_types    = js.get("job_types", [])    # e.g. ["Co-op", "Internship"]
        self.term_lengths = js.get("term_lengths", []) # e.g. ["4-month", "8-month"]
        self.work_types   = js.get("work_types", [])   # e.g. ["Remote", "Hybrid"]

        # State file at project root (next to config.json)
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._state_file = os.path.join(_root, li.get("state_file", "linkedin_state.json"))

        self.headless = br.get("headless", True)
        self.slow_mo  = br.get("slow_mo_ms", 50)
        self.timeout  = br.get("timeout_ms", 30000)

    # ── Public API ──────────────────────────────────────────────────────────

    def scrape(
        self,
        keywords: list[str],
        locations: list[str],
        db_conn,
    ) -> list[ScrapedJob]:
        """
        Launch Playwright, log in (or reuse saved session), iterate every
        query × location pair, persist new jobs to db_conn, and return
        the inserted ScrapedJob list.

        Queries are built by combining each keyword with the configured
        job_type, term_length, and applicant_filter suffixes so that
        LinkedIn's search engine surfaces the most relevant results.

        First run (no saved session file): forces a visible browser window
        so the user can log in and complete any 2FA/CAPTCHA manually.
        """
        queries = self._build_search_queries(keywords)
        results: list[ScrapedJob] = []

        # No saved session → open a visible browser for manual login first.
        first_run = not os.path.exists(self._state_file)
        headless  = False if first_run else self.headless

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=headless,
                slow_mo=self.slow_mo,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = self._build_context(browser)
            page = ctx.new_page()

            # Mask webdriver flag to reduce bot detection
            page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )

            try:
                if first_run:
                    self._manual_login(page, ctx, db_conn)
                else:
                    self._ensure_logged_in(page, ctx, db_conn)

                for query in queries:
                    for location in locations:
                        _db_log(db_conn, "INFO", "linkedin",
                                f"Searching: '{query}' in '{location}'")
                        jobs = self._scrape_search(page, query, location, db_conn)
                        results.extend(jobs)
                        _jitter(3, 6)

            except PlaywrightTimeout as exc:
                _db_log(db_conn, "ERROR", "linkedin", f"Playwright timeout: {exc}")
                raise
            except Exception as exc:
                _db_log(db_conn, "ERROR", "linkedin", f"Scrape error: {exc}")
                raise
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass

        return results

    def _build_search_queries(self, keywords: list[str]) -> list[str]:
        """
        Expand each keyword into one or more search strings by appending
        job_type and term_length tokens.  Applicant count filtering happens
        after scraping (post-filter on the scraped applicant_count field).

        If a keyword already contains a job-type token (e.g. keyword is
        "mechanical engineering coop" and job_types includes "Co-op"), that
        token is NOT appended again — prevents "mechanical engineering coop coop".

        Example — keyword without embedded type:
          keywords=["mechanical engineering"], job_types=["Co-op","Internship"],
          term_lengths=["4-month"]
        Returns:
          ["mechanical engineering coop 4-month",
           "mechanical engineering internship 4-month"]

        Example — keyword with embedded type (user's current setup):
          keywords=["mechanical engineering coop"], job_types=["Co-op"],
          term_lengths=["4-month"]
        Returns:
          ["mechanical engineering coop 4-month"]
        """
        type_tokens = (
            [self._JOB_TYPE_TERMS[jt] for jt in self.job_types
             if jt in self._JOB_TYPE_TERMS]
            if self.job_types else []
        )
        term_tokens = (
            [tl for tl in self.term_lengths if tl and tl.lower() != "any"]
            if self.term_lengths else [""]
        )

        seen: set[str] = set()
        queries: list[str] = []

        for kw in keywords:
            kw_lower = kw.lower()

            # If the keyword already contains ANY configured job-type token,
            # skip type expansion entirely for this keyword.
            kw_has_type = any(t in kw_lower for t in type_tokens)
            applicable_types = [""] if (kw_has_type or not type_tokens) else type_tokens

            for jt in applicable_types:
                for tl in term_tokens:
                    parts = [kw]
                    if jt:
                        parts.append(jt)
                    if tl:
                        parts.append(tl)
                    query = " ".join(parts)
                    if query not in seen:
                        seen.add(query)
                        queries.append(query)

        return queries or keywords

    # ── Context / authentication ─────────────────────────────────────────────

    def _build_context(self, browser) -> BrowserContext:
        kwargs: dict = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        if os.path.exists(self._state_file):
            try:
                return browser.new_context(storage_state=self._state_file, **kwargs)
            except Exception:
                pass  # Corrupt or incompatible state — fall through
        return browser.new_context(**kwargs)

    def _manual_login(self, page: Page, ctx: BrowserContext, db_conn) -> None:
        """
        Open LinkedIn login page in a visible browser and poll the URL every
        2 seconds until the user reaches the feed.  No terminal interaction
        needed — just log in in the browser window and the scraper continues
        automatically.  Waits up to 5 minutes before giving up.
        """
        _db_log(db_conn, "INFO", "linkedin",
                "First-time login: browser opened. Sign in to LinkedIn, then scraping starts automatically.")

        page.goto(f"{self.BASE_URL}/login", wait_until="domcontentloaded")

        # Bring the browser window to the foreground
        try:
            page.evaluate("window.focus()")
        except Exception:
            pass

        print(
            "\n"
            "============================================================\n"
            "  LINKEDIN FIRST-TIME LOGIN\n"
            "============================================================\n"
            "  A browser window is now open on the LinkedIn login page.\n"
            "\n"
            "  Sign in to LinkedIn (complete any 2FA if prompted).\n"
            "  The scraper will detect when you reach your feed and\n"
            "  continue automatically -- no key press needed.\n"
            "\n"
            "  Waiting up to 5 minutes...\n"
            "============================================================\n",
            flush=True,
        )

        # Poll until the browser reaches /feed or /home
        deadline      = time.monotonic() + 300
        last_reminder = time.monotonic()
        while True:
            try:
                current = page.url
            except Exception:
                raise RuntimeError(
                    "Browser was closed before login completed. "
                    "Please run the scraper again."
                )
            if "/feed" in current or "/home" in current:
                break
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Timed out waiting for LinkedIn login (5 minutes). "
                    "Please run the scraper again and sign in within 5 minutes."
                )
            now = time.monotonic()
            if now - last_reminder >= 30:
                print(f"  Still waiting for login... ({int(deadline - now)}s left)", flush=True)
                last_reminder = now
            time.sleep(1)

        ctx.storage_state(path=self._state_file)
        _db_log(db_conn, "INFO", "linkedin",
                "Session saved to linkedin_state.json — future scrapes will be automatic.")
        print("  Logged in! Session saved. Starting scrape...\n", flush=True)

    def _ensure_logged_in(self, page: Page, ctx: BrowserContext, db_conn) -> None:
        try:
            page.goto(f"{self.BASE_URL}/feed/",
                      wait_until="domcontentloaded", timeout=15000)
            _jitter(1, 2)
        except Exception:
            pass

        if "/feed" in page.url or "/home" in page.url:
            _db_log(db_conn, "INFO", "linkedin", "Reusing saved LinkedIn session")
            return

        # Need to log in
        if not self.email or not self.password:
            raise RuntimeError(
                "LinkedIn credentials missing. "
                "Add linkedin.email and linkedin.password to config.json."
            )
        self._login(page)
        ctx.storage_state(path=self._state_file)
        _db_log(db_conn, "INFO", "linkedin",
                "Logged in to LinkedIn; session saved to linkedin_state.json")

    def _login(self, page: Page) -> None:
        page.goto(f"{self.BASE_URL}/login", wait_until="domcontentloaded")
        _jitter(0.8, 1.5)

        page.fill("#username", self.email)
        _jitter(0.4, 0.8)
        page.fill("#password", self.password)
        _jitter(0.3, 0.6)
        page.click("button[type=submit]")

        # Wait for feed, checkpoint, or the post-submit redirect
        page.wait_for_url(
            lambda url: any(s in url for s in
                            ("/feed", "/checkpoint", "/home", "/uas/login-submit",
                             "/authwall", "/login-submit")),
            timeout=30000,
        )

        if "/checkpoint" in page.url or "/challenge" in page.url:
            if self.headless:
                raise RuntimeError(
                    "LinkedIn requires 2FA / CAPTCHA verification. "
                    "Set browser.headless to false in config.json, run again, "
                    "complete the challenge in the browser window, then switch "
                    "headless back to true for future runs."
                )
            logger.warning(
                "LinkedIn checkpoint — complete the challenge in the browser window. "
                "Waiting up to 2 minutes…"
            )
            page.wait_for_url(
                lambda url: "/feed" in url or "/home" in url,
                timeout=120_000,
            )

        if "/feed" not in page.url and "/home" not in page.url:
            raise RuntimeError(
                f"LinkedIn login failed. Current URL: {page.url}"
            )

    # ── Search & pagination ──────────────────────────────────────────────────

    def _scrape_search(
        self, page: Page, query: str, location: str, db_conn
    ) -> list[ScrapedJob]:
        results: list[ScrapedJob] = []
        tpr = self._DATE_MAP.get(self.date_posted, "r604800")

        # Build f_WT value once (comma-separated LinkedIn work-type codes)
        wt_codes = [
            self._WORK_TYPE_CODES[wt]
            for wt in self.work_types
            if wt in self._WORK_TYPE_CODES
        ]

        for page_num in range(self.max_pages):
            params = {
                "keywords": query,
                "location": location,
                "f_AL":     "true",   # Easy Apply filter
                "f_TPR":    tpr,
                "start":    page_num * 25,
            }
            if wt_codes:
                params["f_WT"] = ",".join(wt_codes)
            url = f"{self.SEARCH_URL}?{urlencode(params)}"

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            except PlaywrightTimeout:
                _db_log(db_conn, "WARNING", "linkedin",
                        f"Page load timeout (p{page_num+1}) for '{query}' / '{location}'")
                break

            _jitter(2, 4)
            self._dismiss_modals(page)

            # Wait for the job list container
            try:
                page.wait_for_selector(
                    ".jobs-search-results-list, "
                    ".scaffold-layout__list-container, "
                    "ul[class*='jobs-search-results__list']",
                    timeout=12_000,
                )
            except PlaywrightTimeout:
                _db_log(db_conn, "WARNING", "linkedin",
                        f"No job list on page {page_num+1} for '{query}' / '{location}'")
                break

            self._scroll_list(page)
            _jitter(0.8, 1.5)

            cards = page.query_selector_all(_CARD_SEL)
            if not cards:
                _db_log(db_conn, "INFO", "linkedin",
                        f"No cards found on page {page_num+1} — stopping pagination")
                break

            _db_log(db_conn, "INFO", "linkedin",
                    f"Page {page_num+1}: found {len(cards)} cards")

            for idx, card in enumerate(cards):
                try:
                    job = self._extract_job(page, card, db_conn)
                except Exception as exc:
                    _db_log(db_conn, "WARNING", "linkedin",
                            f"Card {idx+1} error: {exc}")
                    continue

                if job is None:
                    continue

                # Post-filter: skip if applicant_count exceeds configured limit.
                # Jobs where count is unknown (None) are always kept.
                limit = self._APPLICANT_FILTER_LIMITS.get(self.applicant_filter)
                if limit is not None and job.applicant_count is not None:
                    if job.applicant_count > limit:
                        _db_log(db_conn, "INFO", "linkedin",
                                f"Skipped (>{limit} applicants): {job.title} @ {job.company}"
                                f" ({job.applicant_count})")
                        continue

                if _db_exists(db_conn, job.url):
                    continue

                _db_insert(db_conn, job)
                results.append(job)
                _db_log(db_conn, "INFO", "linkedin",
                        f"Saved: {job.title} @ {job.company}"
                        f" ({job.applicant_count if job.applicant_count is not None else '?'} applicants)")

            _jitter(3, 6)

        return results

    # ── Card extraction ──────────────────────────────────────────────────────

    def _extract_job(
        self, page: Page, card, db_conn
    ) -> ScrapedJob | None:
        # ── Step 1: get URL from card link ─────────────────────────────────
        url = None
        for sel in _CARD_LINK:
            link = card.query_selector(sel)
            if link:
                url = _normalize_url(link.get_attribute("href") or "")
                if url:
                    break
        if not url:
            return None

        # Fast dedup before spending time on a click
        if _db_exists(db_conn, url):
            return None

        # ── Step 2: quick card-level data (used as fallback) ───────────────
        card_title    = _sel_text(card, _CARD_LINK)   or ""
        card_company  = _sel_text(card, _CARD_COMPANY) or ""
        card_location = _sel_text(card, _CARD_LOCATION) or ""

        # ── Step 3: click card, wait for detail panel ──────────────────────
        old_url = page.url
        try:
            card.scroll_into_view_if_needed()
            _jitter(0.3, 0.6)
            card.click()
        except Exception:
            return None

        # Wait for URL to change (currentJobId param updates)
        try:
            page.wait_for_function(
                f"() => window.location.href !== {json.dumps(old_url)}",
                timeout=6_000,
            )
        except PlaywrightTimeout:
            pass  # URL may not change on first card

        # Then wait for the detail panel title element
        try:
            page.wait_for_selector(
                "h2.job-details-jobs-unified-top-card__job-title, "
                ".job-details-jobs-unified-top-card__job-title, "
                ".jobs-unified-top-card__job-title",
                state="visible",
                timeout=9_000,
            )
        except PlaywrightTimeout:
            return None   # Detail panel never loaded

        _jitter(0.8, 1.5)

        # ── Step 4: verify Easy Apply button is present ────────────────────
        if not _is_easy_apply(page, _EASY_APPLY_BTN):
            return None

        # ── Step 5: extract detail panel data ─────────────────────────────
        title       = _sel_text(page, _DETAIL_TITLE)    or card_title    or "Unknown"
        company     = _sel_text(page, _DETAIL_COMPANY)  or card_company  or "Unknown"
        location    = _sel_text(page, _DETAIL_LOCATION) or card_location or ""
        description = _sel_inner_text(page, _DETAIL_DESC) or ""
        applicants  = _parse_applicants(page, _DETAIL_APPLICANTS)

        return ScrapedJob(
            url=url,
            title=title.strip(),
            company=company.strip(),
            location=location.strip(),
            description=description.strip(),
            applicant_count=applicants,
            easy_apply=True,
        )

    # ── Page helpers ─────────────────────────────────────────────────────────

    def _scroll_list(self, page: Page) -> None:
        """Scroll job list container to trigger lazy-rendered cards."""
        try:
            page.evaluate(
                """() => {
                    const el = document.querySelector(
                        '.jobs-search-results-list, .scaffold-layout__list-container'
                    );
                    if (el) el.scrollTop = el.scrollHeight;
                }"""
            )
            time.sleep(0.7)
        except Exception:
            pass

    def _dismiss_modals(self, page: Page) -> None:
        """Close overlay modals that block interaction."""
        dismiss_sels = [
            "button[aria-label='Dismiss']",
            ".artdeco-modal__dismiss",
            "button[data-tracking-control-name*='modal_dismiss']",
            "button[data-test-modal-close-btn]",
        ]
        for sel in dismiss_sels:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    _jitter(0.2, 0.5)
            except Exception:
                pass


# ── Module-level helpers ───────────────────────────────────────────────────

def _jitter(min_s: float = 0.5, max_s: float = 1.5) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _sel_text(root, selectors: list[str]) -> str | None:
    """Try each CSS selector on root; return stripped text of first match."""
    for sel in selectors:
        try:
            el = root.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if text:
                    return text
        except Exception:
            continue
    return None


def _sel_inner_text(root, selectors: list[str]) -> str | None:
    """Like _sel_text but only returns if content is substantial (>30 chars)."""
    for sel in selectors:
        try:
            el = root.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if len(text) > 30:
                    return text
        except Exception:
            continue
    return None


def _is_easy_apply(page: Page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if btn and "easy apply" in btn.inner_text().lower():
                return True
        except Exception:
            continue
    return False


def _parse_applicants(page: Page, selectors: list[str]) -> int | None:
    """
    Parse applicant counts from LinkedIn's metadata spans.

    Handles:
      "47 applicants"               → 47
      "Over 200 applicants"         → 200
      "Be among the first 25 applicants" → 25  (low-competition signal)
    """
    for sel in selectors:
        try:
            for el in page.query_selector_all(sel):
                text = el.inner_text().strip().lower()
                # "over N applicants"
                m = re.search(r'over\s+(\d+)\s+applicant', text)
                if m:
                    return int(m.group(1))
                # "be among the first N applicants"
                m = re.search(r'first\s+(\d+)\s+applicant', text)
                if m:
                    return int(m.group(1))
                # "N applicants"
                m = re.search(r'(\d+)\s+applicant', text)
                if m:
                    return int(m.group(1))
        except Exception:
            continue
    return None


def _normalize_url(href: str) -> str | None:
    """
    Produce a canonical LinkedIn job URL with no query params.

    "/jobs/view/1234567890/?refId=abc"
      → "https://www.linkedin.com/jobs/view/1234567890/"
    """
    if not href:
        return None
    if href.startswith("/jobs/view/"):
        path = re.sub(r'\?.*$', '', href).rstrip("/") + "/"
        return f"https://www.linkedin.com{path}"
    if "linkedin.com/jobs/view/" in href:
        parsed = urlparse(href)
        path = parsed.path.rstrip("/") + "/"
        return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return None


def _db_exists(conn, url: str) -> bool:
    return conn.execute("SELECT 1 FROM jobs WHERE url=?", (url,)).fetchone() is not None


def _db_insert(conn, job: ScrapedJob) -> None:
    conn.execute(
        """INSERT INTO jobs
               (url, title, company, location, source, status,
                description, applicant_count, easy_apply)
           VALUES (?, ?, ?, ?, 'linkedin', 'discovered', ?, ?, 1)""",
        (job.url, job.title, job.company, job.location,
         job.description, job.applicant_count),
    )
    conn.commit()


def _db_log(conn, level: str, module: str, message: str) -> None:
    try:
        conn.execute(
            "INSERT INTO logs (level, module, message) VALUES (?, ?, ?)",
            (level, module, message),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("DB log failed: %s — %s", message, exc)
