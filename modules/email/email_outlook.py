"""
Outlook inbox monitor via Microsoft Graph API.

Auth flow (one-time setup):
  1. Register an Azure app — follow the guide in Settings > Outlook Email.
  2. Add client_id / client_secret / tenant_id to config.json (or the Settings page).
  3. Click "Connect Outlook" on the Dashboard — you'll be redirected to Microsoft login.
  4. Grant Mail.Read permission; you'll land back on the Dashboard.
  5. Tokens are cached in outlook_token_cache.json at the project root.
     MSAL handles silent refresh automatically — you only log in once.

Sync logic:
  - Fetches up to 50 messages from the last 30 days of your inbox.
  - Matches each email's subject + sender + body preview against company names
    of all non-resolved jobs in the DB (status not in rejected/offer).
  - Classifies matched emails as offer > interview > rejection via keyword lists.
  - Updates the matching job's status and writes a log entry.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import msal
import requests

logger = logging.getLogger(__name__)

# ── Classification keyword lists (offer checked first — highest priority) ──

_OFFER_KEYWORDS = [
    "offer of employment", "pleased to offer you", "we'd like to offer",
    "we would like to offer", "formal offer", "offer letter",
    "compensation package", "welcome to the team",
    "we are delighted to offer", "thrilled to offer",
    "conditional offer",
]

_INTERVIEW_KEYWORDS = [
    "invite you to interview", "schedule an interview", "interview invitation",
    "like to invite you", "move you forward", "move forward with you",
    "next steps", "next round", "technical screen", "phone screen",
    "phone interview", "coding challenge", "take-home assignment",
    "hiring manager", "speak with you", "chat with our team",
    "schedule a time", "book a time", "video interview", "virtual interview",
    "on-site interview", "pleased to invite",
]

_REJECTION_KEYWORDS = [
    "unfortunately", "not moving forward", "not selected",
    "other candidates", "decided to move forward with other",
    "will not be moving", "regret to inform", "not a match",
    "not be proceeding", "position has been filled", "filled the position",
    "not be advancing", "not be continuing", "not be able to offer",
    "we have decided not", "not progress your application", "unsuccessful",
    "not shortlisted", "not been selected", "no longer considering",
    "chosen not to proceed", "not successful", "withdraw your application",
    "not be progressing",
]

# ── Constants ──────────────────────────────────────────────────────────────

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOKEN_CACHE_PATH = os.path.join(_ROOT, "outlook_token_cache.json")

GRAPH_SCOPES = ["https://graph.microsoft.com/Mail.Read", "offline_access"]
GRAPH_BASE   = "https://graph.microsoft.com/v1.0"


# ── Main class ─────────────────────────────────────────────────────────────

class OutlookMonitor:

    def __init__(self, config: dict):
        ms = config.get("microsoft", {})
        self.client_id     = ms.get("client_id", "")
        self.client_secret = ms.get("client_secret", "")
        self.tenant_id     = ms.get("tenant_id", "common")
        self.redirect_uri  = ms.get(
            "redirect_uri", "http://localhost:5000/email/auth/callback"
        )
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"

        self._cache = msal.SerializableTokenCache()
        if os.path.exists(TOKEN_CACHE_PATH):
            try:
                with open(TOKEN_CACHE_PATH, encoding="utf-8") as f:
                    self._cache.deserialize(f.read())
            except Exception:
                pass  # Corrupt cache — start fresh

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _app(self) -> msal.ConfidentialClientApplication:
        return msal.ConfidentialClientApplication(
            self.client_id,
            authority=self.authority,
            client_credential=self.client_secret,
            token_cache=self._cache,
        )

    def _persist_cache(self) -> None:
        if self._cache.has_state_changed:
            with open(TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
                f.write(self._cache.serialize())

    def get_auth_url(self) -> str:
        return self._app().get_authorization_request_url(
            scopes=GRAPH_SCOPES,
            redirect_uri=self.redirect_uri,
        )

    def exchange_code(self, code: str) -> dict:
        result = self._app().acquire_token_by_authorization_code(
            code=code,
            scopes=GRAPH_SCOPES,
            redirect_uri=self.redirect_uri,
        )
        self._persist_cache()
        return result

    def _token(self) -> Optional[str]:
        app      = self._app()
        accounts = app.get_accounts()
        if not accounts:
            return None
        result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
        self._persist_cache()
        return result.get("access_token") if result else None

    def is_authenticated(self) -> bool:
        return bool(os.path.exists(TOKEN_CACHE_PATH) and self._token())

    # ── Sync ─────────────────────────────────────────────────────────────────

    def sync(self, db_conn) -> dict:
        """
        Scan the last 30 days of inbox, match emails to jobs, classify,
        and update job statuses.  Returns {"processed": N, "updated": N}.
        """
        token = self._token()
        if not token:
            raise RuntimeError(
                "Not authenticated — visit /email/auth to connect your Outlook account."
            )

        # Only look at jobs that haven't reached a terminal state
        rows = db_conn.execute(
            "SELECT id, title, company, status FROM jobs "
            "WHERE status NOT IN ('rejected', 'offer')"
        ).fetchall()
        if not rows:
            return {"processed": 0, "updated": 0}

        # Build normalized company name → job rows index
        company_map: dict[str, list] = {}
        for row in rows:
            key = _normalize_company(row["company"])
            if key:
                company_map.setdefault(key, []).append(row)

        headers = {"Authorization": f"Bearer {token}"}
        params  = {
            "$select":  "subject,from,receivedDateTime,bodyPreview",
            "$top":     50,
            "$orderby": "receivedDateTime desc",
            "$filter":  f"receivedDateTime ge {_iso_ago(30)}",
        }

        resp = requests.get(
            f"{GRAPH_BASE}/me/messages",
            headers=headers,
            params=params,
            timeout=20,
        )

        if resp.status_code == 401:
            # Token invalid — wipe cache so the user is prompted to reconnect
            if os.path.exists(TOKEN_CACHE_PATH):
                os.remove(TOKEN_CACHE_PATH)
            raise RuntimeError(
                "Outlook session expired — visit /email/auth to reconnect."
            )

        resp.raise_for_status()
        messages = resp.json().get("value", [])
        updated  = 0

        for msg in messages:
            subject     = msg.get("subject", "") or ""
            sender_addr = (
                msg.get("from", {}).get("emailAddress", {}).get("address", "") or ""
            )
            body_preview = msg.get("bodyPreview", "") or ""

            matched = _match_to_jobs(subject, sender_addr, body_preview, company_map)
            if not matched:
                continue

            classification = _classify(subject, body_preview)
            if classification is None:
                continue

            for job in matched:
                new_status = _new_status(classification, job["status"])
                if new_status is None:
                    continue

                db_conn.execute(
                    "UPDATE jobs SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (new_status, job["id"]),
                )
                db_conn.execute(
                    "INSERT INTO logs (level, module, message) VALUES ('INFO', 'email', ?)",
                    (
                        f"[{classification}] {job['title']} at {job['company']}"
                        f" → {new_status} | \"{subject[:65]}\"",
                    ),
                )
                updated += 1

        db_conn.commit()
        return {"processed": len(messages), "updated": updated}


# ── Helpers ────────────────────────────────────────────────────────────────

def _normalize_company(name: str) -> str:
    """Lowercase + strip legal suffixes for fuzzy matching."""
    name = name.lower().strip()
    for suffix in (
        " incorporated", " corporation", " international", " technologies",
        " solutions", " systems", " holdings", " group", " limited",
        " inc.", " inc", " llc", " ltd", " corp", " co.", " co",
        " tech", " labs", " lab",
    ):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
    return name


def _match_to_jobs(
    subject: str, sender: str, body: str, company_map: dict
) -> list:
    combined = f"{subject} {sender} {body}".lower()
    matched  = []
    for norm_name, jobs in company_map.items():
        if len(norm_name) < 4:
            continue  # Too short — avoids false positives like "ibm"… actually 3 chars is risky
        if norm_name in combined:
            matched.extend(jobs)
    return matched


def _classify(subject: str, body: str) -> Optional[str]:
    """Return 'offer', 'interview', 'rejection', or None (offer wins ties)."""
    text = f"{subject} {body}".lower()
    for kw in _OFFER_KEYWORDS:
        if kw in text:
            return "offer"
    for kw in _INTERVIEW_KEYWORDS:
        if kw in text:
            return "interview"
    for kw in _REJECTION_KEYWORDS:
        if kw in text:
            return "rejection"
    return None


def _new_status(classification: str, current: str) -> Optional[str]:
    """Return the new DB status, or None if no update is needed."""
    if classification == "rejection":
        return "rejected" if current != "rejected" else None
    if classification == "interview":
        return "interview" if current in ("discovered", "applied") else None
    if classification == "offer":
        return "offer" if current != "offer" else None
    return None


def _iso_ago(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
