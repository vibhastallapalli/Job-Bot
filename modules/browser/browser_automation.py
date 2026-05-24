"""
LinkedIn Easy Apply automation.

Flow per job:
  1. Open the job URL in a browser context authenticated with the saved
     LinkedIn session (linkedin_state.json, produced by the scraper).
  2. Click the "Easy Apply" button to open the modal.
  3. Loop over modal steps (up to MAX_STEPS):
       a. Detect the current step type (contact info, resume, questions, review).
       b. Fill every visible input / select / radio / textarea using a
          label-matching strategy against config["applicant"] and config["answers"].
       c. Handle the resume section: upload the PDF if a path is provided,
          otherwise select the most-recent existing resume on the account.
       d. Detect the footer action: Next → Review → Submit application.
       e. Click it and advance.
  4. Detect the "Application sent" confirmation banner.
  5. On success: insert an applications row, insert a resumes row (if PDF
     was uploaded), update jobs.status → "applied", write an INFO log.
  6. On any failure: take a screenshot to output/screenshots/, write an
     ERROR/WARNING log, and return a result object with details.

Headed vs. headless:
  - headless=true  → fully automated; unknown required fields are left as-is
    and the bot will bail with a validation-error result if LinkedIn rejects
    the form.
  - headless=false → same automation, but the browser is visible so you can
    watch it and intervene manually if needed.
"""

import logging
import os
import re
import time
import random
from dataclasses import dataclass, field as dc_field
from datetime import datetime

from playwright.sync_api import (
    sync_playwright,
    Page,
    BrowserContext,
    TimeoutError as PlaywrightTimeout,
)

logger = logging.getLogger(__name__)

# ── Selector constants ─────────────────────────────────────────────────────
# Every list is tried in order; first visible match wins.

_EASY_APPLY_OPEN = [
    "button.jobs-apply-button",
    "button[aria-label*='Easy Apply']",
    ".jobs-apply-button--top-card",
    "button.jobs-s-apply",
]

# Modal container
_MODAL = ".artdeco-modal__content"

# Resume section detection
_RESUME_SECTION_HEADERS = ["resume", "cv", "upload resume", "upload your resume"]
_RESUME_FILE_INPUT      = f"{_MODAL} input[type='file']"

# Footer buttons  (action-bar inside the modal)
_NEXT_ARIA    = ["Continue to next step", "Next"]
_REVIEW_ARIA  = ["Review your application", "Review"]
_SUBMIT_ARIA  = ["Submit application", "Submit"]
_DISCARD_ARIA = ["Dismiss", "Discard"]

# Post-submission success indicators
_SUCCESS_SELS = [
    ".artdeco-inline-feedback--success",
    ".jobs-post-apply-confirmation",
    "h3.t-20:has-text('Application sent')",
    "h3:has-text('Your application was sent')",
    "div.jobs-easy-apply-content h3:has-text('sent')",
    "[data-test-job-apply-success]",
]

# Already-applied detection
_ALREADY_APPLIED_SELS = [
    "button[aria-label*='Applied'][disabled]",
    ".jobs-applied-banner",
    "span:has-text('Applied')",
]


# ── Result dataclass ───────────────────────────────────────────────────────

@dataclass
class ApplyResult:
    success: bool
    job_id: int
    notes: str
    steps_completed: int = 0
    screenshot_path: str | None = None
    resume_path: str | None = None
    error: str | None = None    # machine-readable code


# ── Main class ─────────────────────────────────────────────────────────────

class ApplicationBot:

    MAX_STEPS = 12   # hard cap on modal steps before giving up

    def __init__(self, config: dict):
        self._config    = config
        self._browser   = config.get("browser", {})
        self._applicant = config.get("applicant", {})
        self._answers   = config.get("answers", {})
        self._li        = config.get("linkedin", {})

        self.headless = self._browser.get("headless", True)
        self.slow_mo  = self._browser.get("slow_mo_ms", 50)
        self.timeout  = self._browser.get("timeout_ms", 30000)

        _root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self._state_file    = os.path.join(_root, self._li.get("state_file", "linkedin_state.json"))
        self._screenshot_dir = os.path.join(_root, "output", "screenshots")
        os.makedirs(self._screenshot_dir, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────

    def apply_job(
        self,
        job: dict,            # sqlite3.Row or plain dict with id/url/title/company
        db_conn,
        resume_path: str | None = None,
    ) -> ApplyResult:
        """
        Attempt to submit a LinkedIn Easy Apply application for *job*.
        All DB side-effects (status update, application record, logs) are
        written via *db_conn* which must be an open sqlite3.Connection owned
        by the calling thread.
        """
        job_id  = job["id"]
        job_url = job["url"]
        title   = job.get("title",   "Unknown")
        company = job.get("company", "Unknown")
        result  = ApplyResult(success=False, job_id=job_id, notes="")
        self._current_job = dict(job)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx  = self._build_context(browser)
            page = ctx.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            # Intercept new popups/tabs and close them — Easy Apply stays in-page
            ctx.on("page", lambda p: p.close())

            try:
                self._ensure_logged_in(page, ctx, db_conn)

                # Navigate to the job page
                page.goto(job_url, wait_until="domcontentloaded", timeout=self.timeout)
                _jitter(2, 3)
                self._dismiss_modals(page)

                # Check if already applied
                if self._already_applied(page):
                    result.success = True
                    result.notes   = "Already applied (detected on job page)"
                    result.error   = "already_applied"
                    _db_log(db_conn, "INFO", "automation",
                            f"Already applied: {title} @ {company}")
                    return result

                # Open the Easy Apply modal
                if not self._open_modal(page):
                    result.notes = "Easy Apply button not found or not clickable"
                    result.error = "no_button"
                    result.screenshot_path = self._screenshot(page, f"no_btn_{job_id}")
                    _db_log(db_conn, "WARNING", "automation",
                            f"No Easy Apply button: {title} @ {company}")
                    return result

                # Work through all modal steps
                steps, outcome = self._fill_form(page, resume_path, db_conn)
                result.steps_completed = steps
                result.notes           = outcome

                if outcome.startswith("submitted"):
                    result.success     = True
                    result.resume_path = resume_path
                    _update_db_applied(db_conn, job_id, resume_path, outcome)
                    _db_log(db_conn, "INFO", "automation",
                            f"Applied: {title} @ {company} ({steps} steps)")
                else:
                    result.error           = "form_incomplete"
                    result.screenshot_path = self._screenshot(page, f"incomplete_{job_id}")
                    _db_log(db_conn, "WARNING", "automation",
                            f"Apply incomplete: {title} @ {company} — {outcome}")

            except PlaywrightTimeout as exc:
                result.error           = "timeout"
                result.notes           = f"Timeout: {exc}"
                result.screenshot_path = self._screenshot(page, f"timeout_{job_id}")
                _db_log(db_conn, "ERROR", "automation",
                        f"Timeout applying to {title} @ {company}: {exc}")

            except Exception as exc:
                result.error           = "exception"
                result.notes           = str(exc)
                result.screenshot_path = self._screenshot(page, f"error_{job_id}")
                _db_log(db_conn, "ERROR", "automation",
                        f"Exception applying to {title} @ {company}: {exc}")

            finally:
                ctx.close()
                browser.close()

        return result

    # ── Auth (mirrors modules/jobs/linkedin.py) ────────────────────────────

    def _build_context(self, browser) -> BrowserContext:
        kwargs: dict = {
            "viewport":    {"width": 1280, "height": 900},
            "user_agent":  (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "locale":      "en-US",
            "timezone_id": "America/New_York",
        }
        if os.path.exists(self._state_file):
            try:
                return browser.new_context(storage_state=self._state_file, **kwargs)
            except Exception:
                pass
        return browser.new_context(**kwargs)

    def _ensure_logged_in(self, page: Page, ctx: BrowserContext, db_conn) -> None:
        try:
            page.goto("https://www.linkedin.com/feed/",
                      wait_until="domcontentloaded", timeout=15_000)
            _jitter(1, 2)
        except Exception:
            pass

        if "/feed" in page.url or "/home" in page.url:
            return

        email    = self._li.get("email", "")
        password = self._li.get("password", "")
        if not email or not password:
            raise RuntimeError(
                "LinkedIn credentials not configured. "
                "Add linkedin.email / linkedin.password in Settings."
            )
        self._login(page)
        ctx.storage_state(path=self._state_file)
        _db_log(db_conn, "INFO", "automation", "Re-logged in to LinkedIn")

    def _login(self, page: Page) -> None:
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        _jitter(0.8, 1.5)
        page.fill("#username", self._li["email"])
        _jitter(0.4, 0.8)
        page.fill("#password", self._li["password"])
        _jitter(0.3, 0.6)
        page.click("button[type=submit]")
        page.wait_for_url(
            lambda u: any(s in u for s in
                          ("/feed", "/checkpoint", "/home", "/uas/login-submit")),
            timeout=30_000,
        )
        if "/checkpoint" in page.url or "/challenge" in page.url:
            if self.headless:
                raise RuntimeError(
                    "LinkedIn 2FA required. Set browser.headless=false in Settings, "
                    "complete the challenge, then set it back to true."
                )
            page.wait_for_url(
                lambda u: "/feed" in u or "/home" in u, timeout=120_000
            )
        if "/feed" not in page.url and "/home" not in page.url:
            raise RuntimeError(f"Login failed. URL: {page.url}")

    # ── Modal opening ──────────────────────────────────────────────────────

    def _open_modal(self, page: Page) -> bool:
        """Click the Easy Apply button and wait for the modal to appear."""
        btn = _find_visible(page, _EASY_APPLY_OPEN)
        if not btn:
            return False
        btn.scroll_into_view_if_needed()
        _jitter(0.5, 1)
        btn.click()
        try:
            page.wait_for_selector(
                _MODAL + ", .jobs-easy-apply-content",
                state="visible",
                timeout=10_000,
            )
            _jitter(1, 1.5)
            return True
        except PlaywrightTimeout:
            return False

    def _already_applied(self, page: Page) -> bool:
        for sel in _ALREADY_APPLIED_SELS:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return True
            except Exception:
                pass
        return False

    # ── Multi-step form loop ───────────────────────────────────────────────

    def _fill_form(
        self,
        page: Page,
        resume_path: str | None,
        db_conn,
    ) -> tuple[int, str]:
        """
        Iterate over modal steps.  Returns (steps_completed, outcome_string).
        outcome_string starts with "submitted" on success.
        """
        for step_num in range(self.MAX_STEPS):
            _jitter(0.8, 1.5)

            # Modal might have closed (submitted or dismissed)
            if not self._modal_open(page):
                # Check for success confirmation outside the modal
                if self._check_success(page):
                    return step_num, "submitted: confirmation detected"
                return step_num, "modal closed unexpectedly"

            self._dismiss_error_dialogs(page)

            # Detect and perform the current step
            action = self._detect_action(page)

            if action == "submit":
                self._advance(page, action)
                _jitter(2, 4)
                if self._check_success(page):
                    return step_num + 1, "submitted: application sent"
                # Might still show confirmation after short delay
                _jitter(1, 2)
                if self._check_success(page):
                    return step_num + 1, "submitted: application sent"
                return step_num + 1, "submit clicked but confirmation unclear"

            # Fill fields on this step BEFORE advancing
            unanswered = self._fill_current_step(page, resume_path)
            _jitter(0.5, 1)

            if unanswered:
                label = unanswered[0]
                _db_log(db_conn, "WARNING", "automation",
                        f"Abandoned: required field has no configured answer — '{label}'")
                self._discard_modal(page)
                return step_num, f"abandoned: unanswered required question: {label}"

            # Check for validation errors after fill
            errors = self._get_errors(page)

            if errors and self.headless:
                # Try advancing anyway — LinkedIn sometimes clears errors on retry
                pass

            if not self._advance(page, action):
                return step_num, f"could not find '{action}' button"

            _jitter(1.5, 2.5)

            # If we clicked "next" / "review" and errors are still showing,
            # we're stuck — bail with a useful message
            post_errors = self._get_errors(page)
            if post_errors and self._modal_open(page):
                return step_num + 1, f"validation errors: {'; '.join(post_errors[:3])}"

        return self.MAX_STEPS, f"exceeded {self.MAX_STEPS} steps without finishing"

    # ── Step-level filling ─────────────────────────────────────────────────

    def _fill_current_step(self, page: Page, resume_path: str | None) -> list[str]:
        """Dispatch to specialized fillers for the current modal step.
        Returns labels of required fields that were left unanswered."""
        if self._is_resume_step(page):
            self._handle_resume_section(page, resume_path)

        unanswered: list[str] = []
        unanswered.extend(self._fill_inputs(page))
        self._fill_selects(page)
        self._fill_radios(page)
        unanswered.extend(self._fill_textareas(page))
        return unanswered

    def _is_resume_step(self, page: Page) -> bool:
        try:
            headers = page.query_selector_all(f"{_MODAL} h3, {_MODAL} h2, {_MODAL} legend")
            for h in headers:
                if h.inner_text().strip().lower() in _RESUME_SECTION_HEADERS:
                    return True
            # Also check for file inputs or resume picker
            if page.query_selector(f"{_RESUME_FILE_INPUT}, .jobs-resume-picker"):
                return True
        except Exception:
            pass
        return False

    def _handle_resume_section(self, page: Page, resume_path: str | None) -> None:
        """Upload a PDF or select the most-recent existing resume."""
        if resume_path and os.path.exists(resume_path):
            # 1) Look for a visible file input directly
            try:
                page.set_input_files(_RESUME_FILE_INPUT, resume_path)
                _jitter(1.5, 2.5)
                return
            except Exception:
                pass

            # 2) Click an "Upload resume" / "Change resume" trigger first
            for text in ["Upload resume", "Upload a resume", "Change resume", "Upload"]:
                try:
                    trigger = page.locator(
                        f"button:has-text('{text}'), label:has-text('{text}')"
                    ).first
                    if trigger.count() > 0 and trigger.is_visible():
                        trigger.click()
                        _jitter(0.8, 1.5)
                        page.set_input_files(_RESUME_FILE_INPUT, resume_path)
                        _jitter(1.5, 2.5)
                        return
                except Exception:
                    continue

        # 3) Fallback: select the first available resume radio
        try:
            picker_radios = page.query_selector_all(
                ".jobs-resume-picker input[type='radio'], "
                f"{_MODAL} .artdeco-card input[type='radio']"
            )
            if picker_radios:
                if not picker_radios[0].is_checked():
                    picker_radios[0].check()
        except Exception:
            pass

    def _fill_inputs(self, page: Page) -> list[str]:
        """Fill text / number / email / tel inputs that are empty or need our value.
        Returns labels of required fields left unanswered."""
        unanswered: list[str] = []
        selectors   = (
            f"{_MODAL} input[type='text'], {_MODAL} input[type='number'], "
            f"{_MODAL} input[type='email'], {_MODAL} input[type='tel'], "
            f"{_MODAL} input:not([type]):not([hidden])"
        )
        try:
            inputs = page.query_selector_all(selectors)
        except Exception:
            return unanswered

        for inp in inputs:
            try:
                if not inp.is_visible() or inp.is_disabled():
                    continue
                label = self._label_for(page, inp)
                value = self._resolve(label or "", inp.get_attribute("type") or "text")
                if not value:
                    if _is_required(inp):
                        ai_val = _try_ai_answer(
                            label or "", inp.get_attribute("type") or "text",
                            None, self._config, getattr(self, "_current_job", {}),
                        )
                        if ai_val:
                            inp.fill(ai_val)
                            _jitter(0.1, 0.3)
                        else:
                            unanswered.append(label or "(unlabelled)")
                    continue
                current = inp.input_value()
                if current and current.strip():
                    continue   # already filled — don't clobber
                inp.fill(value)
                _jitter(0.1, 0.3)
            except Exception:
                continue
        return unanswered

    def _fill_selects(self, page: Page) -> None:
        """Select the best option in every visible <select> element."""
        try:
            selects = page.query_selector_all(f"{_MODAL} select")
        except Exception:
            return

        for sel_el in selects:
            try:
                if not sel_el.is_visible() or sel_el.is_disabled():
                    continue
                label   = self._label_for(page, sel_el)
                options = _get_select_options(sel_el)
                choice  = self._resolve_select(label or "", options)
                if choice:
                    try:
                        sel_el.select_option(label=choice)
                    except Exception:
                        sel_el.select_option(value=choice)
                    _jitter(0.1, 0.3)
            except Exception:
                continue

    def _fill_radios(self, page: Page) -> None:
        """Handle radio-button groups (Yes/No questions, EEO fields, etc.)."""
        try:
            fieldsets = page.query_selector_all(f"{_MODAL} fieldset")
        except Exception:
            return

        for fs in fieldsets:
            try:
                legend = fs.query_selector("legend")
                if not legend:
                    continue
                question = legend.inner_text().strip()
                answer   = self._resolve_radio(question)
                if not answer:
                    continue

                radios = fs.query_selector_all("input[type='radio']")
                matched = False
                for radio in radios:
                    radio_label = self._label_for(page, radio) or ""
                    if answer.lower() in radio_label.lower():
                        if not radio.is_checked():
                            radio.check()
                        matched = True
                        break

                # If no label matched, check the first radio when none are selected
                if not matched:
                    checked = any(r.is_checked() for r in radios if r.is_visible())
                    if not checked and radios:
                        for r in radios:
                            if r.is_visible():
                                r.check()
                                break
                _jitter(0.1, 0.2)
            except Exception:
                continue

    def _fill_textareas(self, page: Page) -> list[str]:
        """Fill textarea elements (cover letter, additional info).
        Returns labels of required fields left unanswered."""
        unanswered: list[str] = []
        try:
            textareas = page.query_selector_all(f"{_MODAL} textarea")
        except Exception:
            return unanswered

        for ta in textareas:
            try:
                if not ta.is_visible() or ta.is_disabled():
                    continue
                if ta.input_value().strip():
                    continue
                label = self._label_for(page, ta)
                value = self._resolve(label or "", "textarea")
                if not value:
                    if _is_required(ta):
                        ai_val = _try_ai_answer(
                            label or "", "textarea",
                            None, self._config, getattr(self, "_current_job", {}),
                        )
                        if ai_val:
                            ta.fill(ai_val)
                            _jitter(0.2, 0.5)
                        else:
                            unanswered.append(label or "(unlabelled)")
                    continue
                ta.fill(value)
                _jitter(0.2, 0.5)
            except Exception:
                continue
        return unanswered

    # ── Field value resolution ─────────────────────────────────────────────

    # Maps normalised label substrings → applicant/answers config key
    _LABEL_MAP: dict[str, str] = {
        "first name":          "first_name",
        "last name":           "last_name",
        "full name":           "name",
        "name":                "name",
        "email":               "email",
        "email address":       "email",
        "phone":               "phone",
        "phone number":        "phone",
        "mobile":              "phone",
        "linkedin":            "linkedin",
        "github":              "github",
        "website":             "website",
        "portfolio":           "website",
        "personal site":       "website",
        "city":                "city",
        "state":               "state",
        "country":             "country",
        "years of experience": "years_experience",
        "years experience":    "years_experience",
        "how many years":      "years_experience",
        "years":               "years_experience",
        "salary":              "desired_salary",
        "desired salary":      "desired_salary",
        "expected salary":     "desired_salary",
        "cover letter":        "cover_letter",
        "additional":          "cover_letter",
        "summary":             "cover_letter",
    }

    def _resolve(self, label: str, input_type: str) -> str:
        """Return the value to put in a field given its label text."""
        a   = self._applicant
        ans = self._answers
        lbl = label.lower().strip()

        # Remove asterisk (required indicator) and trailing colon/punctuation
        lbl = re.sub(r'[*\(].*', '', lbl).strip().rstrip(":").strip()

        # Direct map lookup
        for pattern, key in self._LABEL_MAP.items():
            if pattern in lbl:
                return self._applicant_value(key)

        return ""

    def _applicant_value(self, key: str) -> str:
        a   = self._applicant
        ans = self._answers
        name   = a.get("name", "")
        parts  = name.split()
        return {
            "first_name":       parts[0] if parts else "",
            "last_name":        " ".join(parts[1:]) if len(parts) > 1 else "",
            "name":             name,
            "email":            a.get("email",   ""),
            "phone":            a.get("phone",   ""),
            "linkedin":         a.get("linkedin",""),
            "github":           a.get("github",  ""),
            "website":          a.get("website", ""),
            "city":             a.get("city",    ""),
            "state":            a.get("state",   ""),
            "country":          a.get("country", "United States"),
            "years_experience": str(ans.get("years_experience", "")),
            "desired_salary":   str(ans.get("desired_salary",   "")),
            "cover_letter":     ans.get("cover_letter", ""),
        }.get(key, "")

    def _resolve_select(self, label: str, options: list[str]) -> str:
        """
        Pick the best select option given the field label.

        Strategy:
          1. Check if the label maps to a known answer in config["answers"].
          2. Otherwise try to fuzzy-match the label to a common question type.
          3. Fall back to the first non-empty option.
        """
        if not options:
            return ""

        lbl = label.lower().strip()
        ans = self._answers

        # Known answer mappings
        answer_map = {
            "work authorization":  ans.get("work_authorization",   "Yes"),
            "authoriz":            ans.get("work_authorization",   "Yes"),
            "sponsorship":         ans.get("requires_sponsorship", "No"),
            "visa":                ans.get("requires_sponsorship", "No"),
            "relocat":             ans.get("willing_to_relocate",  "No"),
            "country":             self._applicant.get("country",  "United States"),
            "start date":          ans.get("earliest_start_date",  "Immediately"),
            "earliest":            ans.get("earliest_start_date",  "Immediately"),
            "education":           ans.get("education_level",       ""),
            "degree":              ans.get("education_level",       ""),
            "experience level":    ans.get("experience_level",      ""),
            "gender":              ans.get("gender",                ""),
            "race":                ans.get("race",                  ""),
            "ethnicity":           ans.get("race",                  ""),
            "veteran":             ans.get("veteran_status",        ""),
            "disability":          ans.get("disability_status",     ""),
        }

        desired = ""
        for key, val in answer_map.items():
            if key in lbl:
                desired = val
                break

        if desired:
            # Find closest option (case-insensitive substring match)
            for opt in options:
                if desired.lower() in opt.lower() or opt.lower() in desired.lower():
                    return opt
            # Exact first-word match
            for opt in options:
                if opt.lower().startswith(desired.lower()[:3]):
                    return opt

        # No configured answer — return first non-placeholder option
        for opt in options:
            if opt and "select" not in opt.lower() and "choose" not in opt.lower():
                return opt
        return ""

    def _resolve_radio(self, question: str) -> str:
        """Return the desired radio answer text for a given question."""
        q   = question.lower()
        ans = self._answers

        if "sponsor" in q or "visa" in q:
            return ans.get("requires_sponsorship", "No")
        if "authoriz" in q or "work in the u" in q or "eligible to work" in q:
            return ans.get("work_authorization", "Yes")
        if "relocat" in q:
            return ans.get("willing_to_relocate", "No")
        if "veteran" in q:
            return ans.get("veteran_status", "")
        if "disability" in q:
            return ans.get("disability_status", "")
        if "gender" in q:
            return ans.get("gender", "")
        if "race" in q or "ethnicity" in q:
            return ans.get("race", "")

        return ""  # unknown question — leave as-is or let first-radio fallback handle it

    # ── Label detection ────────────────────────────────────────────────────

    def _label_for(self, page: Page, el) -> str | None:
        """Return the human-readable label for a form element."""
        try:
            # 1. <label for="id">
            el_id = el.get_attribute("id")
            if el_id:
                lbl = page.query_selector(f"label[for='{el_id}']")
                if lbl:
                    return lbl.inner_text().strip()

            # 2. Ancestor <label>
            text = el.evaluate(
                "el => { const l = el.closest('label'); return l ? l.innerText : null; }"
            )
            if text and text.strip():
                return text.strip()

            # 3. aria-label attribute
            aria = el.get_attribute("aria-label")
            if aria:
                return aria.strip()

            # 4. aria-labelledby
            labelledby = el.get_attribute("aria-labelledby")
            if labelledby:
                parts = labelledby.split()
                texts = []
                for pid in parts:
                    lbl_el = page.query_selector(f"#{pid}")
                    if lbl_el:
                        texts.append(lbl_el.inner_text().strip())
                if texts:
                    return " ".join(texts)

            # 5. placeholder
            return el.get_attribute("placeholder")

        except Exception:
            return None

    # ── Action detection & advancement ────────────────────────────────────

    def _detect_action(self, page: Page) -> str:
        """
        Inspect the modal footer to decide what button to click next.
        Returns one of: "submit", "review", "next", "unknown".
        """
        footer_sel = (
            ".artdeco-modal__actionbar, "
            ".jobs-easy-apply-form-section__action-bar, "
            ".jobs-easy-apply-form__footer"
        )
        try:
            footer = page.query_selector(footer_sel)
            if not footer:
                return "unknown"

            # Look for the primary (rightmost) button
            primary = footer.query_selector_all("button.artdeco-button--primary")
            if not primary:
                primary = footer.query_selector_all("button")

            for btn in reversed(primary):   # rightmost is always the forward action
                if not btn.is_visible():
                    continue
                txt  = btn.inner_text().strip().lower()
                aria = (btn.get_attribute("aria-label") or "").lower()
                combined = txt + " " + aria

                if "submit" in combined:
                    return "submit"
                if "review" in combined:
                    return "review"
                if "next" in combined or "continue" in combined:
                    return "next"
        except Exception:
            pass

        return "unknown"

    def _advance(self, page: Page, action: str) -> bool:
        """Click the appropriate footer button for *action*."""
        aria_map = {
            "submit": _SUBMIT_ARIA,
            "review": _REVIEW_ARIA,
            "next":   _NEXT_ARIA,
        }
        labels = aria_map.get(action, _NEXT_ARIA)

        # Try by aria-label first
        for lbl in labels:
            try:
                btn = page.get_by_role("button", name=lbl, exact=False)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    return True
            except Exception:
                continue

        # Fallback: primary button in footer
        footer_sel = (
            ".artdeco-modal__actionbar button.artdeco-button--primary, "
            ".jobs-easy-apply-form-section__action-bar button.artdeco-button--primary"
        )
        try:
            btns = page.query_selector_all(footer_sel)
            for btn in reversed(btns):
                if btn.is_visible():
                    btn.click()
                    return True
        except Exception:
            pass

        return False

    # ── Success / error detection ──────────────────────────────────────────

    def _check_success(self, page: Page) -> bool:
        for sel in _SUCCESS_SELS:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return True
            except Exception:
                continue

        # Check page text for "Application sent" pattern
        try:
            content = page.query_selector(".jobs-easy-apply-content")
            if content:
                txt = content.inner_text().lower()
                if "application sent" in txt or "your application was sent" in txt:
                    return True
        except Exception:
            pass

        return False

    def _get_errors(self, page: Page) -> list[str]:
        """Return visible validation error messages from the current modal step."""
        error_sels = [
            ".artdeco-inline-feedback--error",
            ".fb-form-element-error",
            "[data-test-inline-feedback]",
            ".jobs-easy-apply-form-section__grouping .artdeco-inline-feedback",
        ]
        errors: list[str] = []
        for sel in error_sels:
            try:
                els = page.query_selector_all(sel)
                for el in els:
                    if el.is_visible():
                        txt = el.inner_text().strip()
                        if txt:
                            errors.append(txt)
            except Exception:
                continue
        return errors

    def _modal_open(self, page: Page) -> bool:
        try:
            modal = page.query_selector(_MODAL + ", .jobs-easy-apply-content")
            return modal is not None and modal.is_visible()
        except Exception:
            return False

    # ── Utility ────────────────────────────────────────────────────────────

    def _dismiss_modals(self, page: Page) -> None:
        """Close any overlay that might block the Easy Apply button."""
        for sel in [
            "button[aria-label='Dismiss']",
            ".artdeco-modal__dismiss",
            "button[data-tracking-control-name*='modal_dismiss']",
            "button[data-test-modal-close-btn]",
        ]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    _jitter(0.2, 0.4)
            except Exception:
                pass

    def _dismiss_error_dialogs(self, page: Page) -> None:
        """Dismiss LinkedIn's 'Are you sure you want to discard?' dialog if it appears."""
        for aria in _DISCARD_ARIA:
            try:
                btn = page.get_by_role("button", name=aria, exact=False)
                if btn.count() > 0 and btn.first.is_visible():
                    # Only dismiss the close/dismiss — not "Discard application"
                    label = (btn.first.get_attribute("aria-label") or "").lower()
                    if "discard application" not in label:
                        btn.first.click()
                        _jitter(0.3, 0.6)
            except Exception:
                pass

    def _discard_modal(self, page: Page) -> None:
        """Close the Easy Apply modal cleanly by discarding the in-progress application.
        Clicks the modal X button, then confirms 'Discard' in the dialog LinkedIn shows."""
        # Step 1: click the modal close/X button to trigger the discard confirmation
        for aria in ["Dismiss", "Close"]:
            try:
                btn = page.get_by_role("button", name=aria, exact=False)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    _jitter(0.5, 1.0)
                    break
            except Exception:
                pass
        # Step 2: confirm discard in the dialog LinkedIn shows
        try:
            btn = page.get_by_role("button", name="Discard", exact=False)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                _jitter(0.3, 0.6)
        except Exception:
            pass

    def _screenshot(self, page: Page, name: str) -> str:
        """Save a screenshot and return its path."""
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self._screenshot_dir, f"{name}_{ts}.png")
        try:
            page.screenshot(path=path, full_page=False)
        except Exception as exc:
            logger.warning("Screenshot failed: %s", exc)
            return ""
        return path


# ── Module-level helpers ───────────────────────────────────────────────────

def _is_required(el) -> bool:
    """Return True if the element carries required or aria-required=true."""
    try:
        if el.get_attribute("required") is not None:
            return True
        if (el.get_attribute("aria-required") or "").lower() == "true":
            return True
    except Exception:
        pass
    return False


def _ai_enabled(config: dict) -> bool:
    ai = config.get("ai", {})
    return bool(ai.get("enabled")) and bool((ai.get("anthropic_api_key") or "").strip())


def _try_ai_answer(
    label: str,
    field_type: str,
    options: list[str] | None,
    config: dict,
    job: dict,
) -> str:
    if not _ai_enabled(config):
        return ""
    try:
        from modules.ai.ai_form_filler import answer_question
        return answer_question(label, field_type, options, config, job)
    except Exception:
        return ""


def _jitter(min_s: float = 0.4, max_s: float = 1.2) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _find_visible(page: Page, selectors: list[str]):
    """Return the first visible element matching any of the given selectors."""
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el
        except Exception:
            continue
    return None


def _get_select_options(sel_el) -> list[str]:
    """Return the visible text of all <option> elements in a <select>."""
    try:
        opts = sel_el.query_selector_all("option")
        return [o.inner_text().strip() for o in opts if o.inner_text().strip()]
    except Exception:
        return []


def _update_db_applied(
    conn,
    job_id: int,
    resume_path: str | None,
    notes: str,
) -> None:
    resume_id: int | None = None

    if resume_path and os.path.exists(resume_path):
        cur = conn.execute(
            "INSERT OR IGNORE INTO resumes (pdf_path) VALUES (?)", (resume_path,)
        )
        if cur.lastrowid:
            resume_id = cur.lastrowid
        else:
            row = conn.execute(
                "SELECT id FROM resumes WHERE pdf_path=?", (resume_path,)
            ).fetchone()
            if row:
                resume_id = row[0]

    conn.execute(
        "INSERT INTO applications (job_id, resume_id, notes, outcome) VALUES (?,?,?,'pending')",
        (job_id, resume_id, notes),
    )
    conn.execute(
        "UPDATE jobs SET status='applied', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (job_id,),
    )
    conn.commit()


def _db_log(conn, level: str, module: str, message: str) -> None:
    try:
        conn.execute(
            "INSERT INTO logs (level, module, message) VALUES (?,?,?)",
            (level, module, message),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("DB log failed: %s — %s", message, exc)
