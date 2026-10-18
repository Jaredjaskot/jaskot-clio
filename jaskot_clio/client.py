"""
Shared Clio API client for all Jaskot Law automation.

Supports two auth modes:
  1. OAuth (client_id + client_secret + token file) — auto-refreshes on 401
  2. Bearer token (access_token only) — no refresh, for simple integrations
"""

import json
import logging
import os
import time
import urllib.parse
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


class ClioClient:
    """Clio API v4 client with OAuth token refresh and rate-limit handling."""

    BASE_URL = "https://app.clio.com"

    def __init__(
        self,
        *,
        client_id: str = "",
        client_secret: str = "",
        redirect_uri: str = "http://127.0.0.1:9876/callback",
        token_file: str = "clio_tokens.json",
        access_token: str = "",
    ):
        """Initialize the Clio client.

        For OAuth mode, provide client_id + client_secret. Tokens are loaded
        from token_file and auto-refreshed on 401.

        For bearer-token mode, provide access_token only. No refresh is
        attempted on 401 (raises immediately).
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_file = token_file
        self.tokens: dict[str, Any] = {}

        if access_token:
            self.tokens = {"access_token": access_token}
        elif client_id and client_secret:
            self._load_tokens()

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def get_auth_url(self, scopes: Optional[list[str]] = None) -> str:
        """Return the Clio OAuth authorization URL."""
        if scopes is None:
            scopes = [
                "matters:read", "matters:write",
                "notes:read", "notes:write",
                "activities:read", "activities:write",
                "documents:read", "documents:write",
                "contacts:read", "contacts:write",
                "bills:read",
            ]
        # access_type=offline is what makes Clio return a refresh_token. Without
        # it you get a bare 30-day access token that simply dies (YTB-689).
        query = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(scopes),
            "access_type": "offline",
        })
        return f"{self.BASE_URL}/oauth/authorize?{query}"

    def exchange_code(self, code: str) -> None:
        """Exchange an authorization code for tokens."""
        resp = requests.post(
            f"{self.BASE_URL}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
            },
            timeout=10,
        )
        resp.raise_for_status()
        self.tokens = resp.json()
        self._save_tokens()
        logger.info("Exchanged authorization code for tokens")

    def refresh_token(self) -> None:
        """Refresh the access token using the stored refresh token."""
        if "refresh_token" not in self.tokens:
            raise ValueError("No refresh token available")

        resp = requests.post(
            f"{self.BASE_URL}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.tokens["refresh_token"],
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        # Merge, don't replace: if Clio's refresh response omits refresh_token we
        # must keep the one we already hold, or the next refresh has nothing to use.
        prior_refresh = self.tokens.get("refresh_token")
        self.tokens = {**self.tokens, **resp.json()}
        if prior_refresh and not self.tokens.get("refresh_token"):
            self.tokens["refresh_token"] = prior_refresh
        self._save_tokens()
        logger.info("Refreshed access token")

    def is_authenticated(self) -> bool:
        return "access_token" in self.tokens

    @property
    def _can_refresh(self) -> bool:
        return bool(self.client_id and self.client_secret and self.tokens.get("refresh_token"))

    # ------------------------------------------------------------------
    # Token persistence
    # ------------------------------------------------------------------

    def _save_tokens(self) -> None:
        try:
            # Stamp issue time so expiry is knowable; Clio only sends expires_in.
            self.tokens["obtained_at"] = int(time.time())
            with open(self.token_file, "w") as f:
                json.dump(self.tokens, f)
        except IOError as e:
            logger.error("Failed to save tokens: %s", e)

    def _load_tokens(self) -> None:
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, "r") as f:
                    self.tokens = json.load(f)
            except (IOError, json.JSONDecodeError) as e:
                logger.warning("Failed to load tokens: %s", e)
                self.tokens = {}

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    def _request(
        self, method: str, endpoint: str, *, max_retries: int = 1, **kwargs
    ) -> requests.Response:
        """Authenticated Clio API request with 401 refresh and 429 backoff."""
        url = f"{self.BASE_URL}/api/v4/{endpoint}"
        custom_headers = kwargs.pop("headers", {})

        for attempt in range(max_retries + 1):
            headers = dict(custom_headers)
            if self.is_authenticated():
                headers["Authorization"] = f"Bearer {self.tokens['access_token']}"
            headers["Accept"] = "application/json"

            try:
                response = requests.request(
                    method, url, headers=headers, timeout=30, **kwargs
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning("Rate limited, retrying after %ds", retry_after)
                    time.sleep(retry_after)
                    continue

                if response.status_code == 401 and attempt < max_retries and self._can_refresh:
                    logger.info("Refreshing token due to 401")
                    try:
                        self.refresh_token()
                        continue
                    except Exception as e:
                        logger.error("Token refresh failed: %s", e)
                        response.raise_for_status()

                response.raise_for_status()
                return response

            except requests.RequestException:
                raise

        raise requests.RequestException(f"Failed after {max_retries + 1} attempts: {endpoint}")

    def _get_json(self, endpoint: str, **params) -> Optional[dict]:
        """GET + parse JSON. Returns None on failure (fail-open for lookups)."""
        try:
            resp = self._request("GET", endpoint, params=params)
            return resp.json()
        except requests.RequestException as e:
            logger.error("GET %s failed: %s", endpoint, e)
            return None

    # ------------------------------------------------------------------
    # Matters — read
    # ------------------------------------------------------------------

    def search_matters(self, query: str, *, limit: int = 20) -> list[dict]:
        """Search matters by query string. Returns [] on failure."""
        data = self._get_json(
            "matters",
            query=query,
            fields="id,display_number,description,client{id,name},status,practice_area{name}",
            limit=limit,
        )
        return data.get("data", []) if data else []

    def get_matter_by_number(self, display_number: str) -> Optional[dict]:
        """Find a matter by exact display number (falls back to first result)."""
        matters = self.search_matters(display_number, limit=5)
        for m in matters:
            if m.get("display_number") == display_number:
                return m
        return matters[0] if matters else None

    def get_matter_details(self, matter_id: int) -> Optional[dict]:
        """Fetch full details for a single matter. Returns None on failure."""
        data = self._get_json(
            f"matters/{matter_id}",
            fields="id,display_number,description,status,client{id,name},"
                   "practice_area{name},opened_date,closed_date,pending_date",
        )
        return data.get("data") if data else None

    def search_matters_by_contact(self, contact_id: int, *, status: str = "open") -> list[dict]:
        """Find matters belonging to a contact, filtered by status."""
        data = self._get_json(
            "matters",
            client_id=contact_id,
            status=status,
            fields="id,display_number,description",
            limit=5,
        )
        return data.get("data", []) if data else []

    # ------------------------------------------------------------------
    # Matters — write
    # ------------------------------------------------------------------

    def create_matter(
        self,
        client_id: int,
        description: str,
        *,
        practice_area_id: Optional[int] = None,
        responsible_attorney_id: Optional[int] = None,
        status: str = "open",
    ) -> Optional[dict]:
        """Create a new matter."""
        payload: dict[str, Any] = {
            "data": {
                "client": {"id": client_id},
                "description": description,
                "status": status,
            }
        }
        if practice_area_id:
            payload["data"]["practice_area"] = {"id": int(practice_area_id)}
        if responsible_attorney_id:
            payload["data"]["responsible_attorney"] = {"id": int(responsible_attorney_id)}

        resp = self._request("POST", "matters", json=payload)
        return resp.json().get("data")

    # ------------------------------------------------------------------
    # Contacts — read
    # ------------------------------------------------------------------

    def get_contact(self, contact_id: int) -> Optional[dict]:
        """Fetch a contact by ID with name, emails, phones."""
        data = self._get_json(
            f"contacts/{contact_id}",
            fields="id,name,email_addresses,phone_numbers",
        )
        return data.get("data") if data else None

    def search_contacts(self, query: str, *, limit: int = 5) -> list[dict]:
        """Search contacts by query string."""
        data = self._get_json(
            "contacts",
            query=query,
            type="Person",
            fields="id,name,email_addresses,phone_numbers",
            limit=limit,
        )
        return data.get("data", []) if data else []

    def search_contact_by_email(self, email: str) -> Optional[dict]:
        """Find a contact by exact email match. Returns {id, name} or None."""
        contacts = self.search_contacts(email)
        for contact in contacts:
            for ea in contact.get("email_addresses", []):
                if ea.get("address", "").lower() == email.lower():
                    return {"id": contact["id"], "name": contact.get("name", "")}
        return None

    # ------------------------------------------------------------------
    # Contacts — write
    # ------------------------------------------------------------------

    def create_contact(
        self,
        first_name: str,
        last_name: str,
        *,
        email: str = "",
        phone: str = "",
    ) -> Optional[dict]:
        """Create a new Person contact."""
        payload: dict[str, Any] = {
            "data": {
                "first_name": first_name,
                "last_name": last_name,
                "type": "Person",
            }
        }
        if email:
            payload["data"]["email_addresses"] = [{"name": "Work", "address": email}]
        if phone:
            payload["data"]["phone_numbers"] = [{"name": "Work", "number": phone}]

        resp = self._request("POST", "contacts", json=payload)
        return resp.json().get("data")

    def find_or_create_contact(
        self,
        first_name: str,
        last_name: str,
        *,
        email: str = "",
        phone: str = "",
    ) -> Optional[dict]:
        """Find existing contact by email, or create a new one."""
        if email:
            existing = self.search_contact_by_email(email)
            if existing:
                logger.info("Found existing contact %s for %s", existing["id"], email)
                return existing

        created = self.create_contact(first_name, last_name, email=email, phone=phone)
        if created:
            logger.info("Created contact %s", created.get("id"))
        return created

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------

    def get_matter_notes(self, matter_id: int, *, limit: int = 50) -> list[dict]:
        """List notes for a matter, newest first. Returns [] on failure."""
        data = self._get_json(
            "notes",
            matter_id=matter_id,
            order="date(desc)",
            limit=limit,
            fields="id,subject,detail,date,user{name},created_at",
        )
        return data.get("data", []) if data else []

    def find_note_by_subject(self, matter_id: int, subject: str) -> Optional[dict]:
        """Find an existing note on a matter by exact subject match (for dedup)."""
        try:
            resp = self._request(
                "GET", "notes",
                params={"matter_id": matter_id, "fields": "id,subject,date", "limit": 50},
            )
            for note in resp.json().get("data", []):
                if note.get("subject") == subject:
                    return note
        except Exception as e:
            logger.warning("Dedup check failed: %s", e)
        return None

    def create_note(self, matter_id: int, subject: str, detail: str, date: str) -> dict:
        """Create a note on a matter."""
        payload = {
            "data": {
                "type": "Matter",
                "subject": subject,
                "detail": detail,
                "date": date,
                "matter": {"id": matter_id},
            }
        }
        resp = self._request("POST", "notes", json=payload, max_retries=2)
        logger.info("Created note for matter %s", matter_id)
        return resp.json().get("data", {})

    # ------------------------------------------------------------------
    # Activities
    # ------------------------------------------------------------------

    def get_matter_activities(self, matter_id: int, *, limit: int = 50) -> list[dict]:
        """List activities (time entries) for a matter. Returns [] on failure."""
        data = self._get_json(
            "activities",
            matter_id=matter_id,
            order="date(desc)",
            limit=limit,
            fields="id,type,date,quantity,note,user{name},created_at",
        )
        return data.get("data", []) if data else []

    def log_activity(
        self,
        matter_id: int,
        user_id: int,
        description: str,
        activity_type: str = "TimeEntry",
        started_at: str = "",
        *,
        ended_at: str = "",
        notes: str = "",
    ) -> dict:
        """Log a time entry / activity.

        Args:
            activity_type: Clio activity type (e.g. "TimeEntry", "ExpenseEntry").
            started_at: ISO datetime or date string. Used for the ``date`` field
                (truncated to YYYY-MM-DD) and the ``started_at`` field if provided.
            ended_at: ISO datetime string for the ``ended_at`` field.
        """
        note_text = f"{description}\n{notes}".strip() if notes else description
        date = started_at[:10] if started_at else ""
        payload: dict[str, Any] = {
            "data": {
                "type": activity_type,
                "note": note_text,
                "matter": {"id": matter_id},
                "user": {"id": user_id},
            }
        }
        if date:
            payload["data"]["date"] = date
        if started_at:
            payload["data"]["started_at"] = started_at
        if ended_at:
            payload["data"]["ended_at"] = ended_at

        resp = self._request("POST", "activities", json=payload)
        return resp.json().get("data", {})

    # ------------------------------------------------------------------
    # Bills / Transactions
    # ------------------------------------------------------------------

    def get_matter_transactions(self, matter_id: int, *, limit: int = 50) -> list[dict]:
        """List bills/transactions for a matter. Returns [] on failure."""
        data = self._get_json(
            "bills",
            matter_id=matter_id,
            order="issued_date(desc)",
            limit=limit,
            fields="id,type,total,state,description,issued_date,due_date",
        )
        return data.get("data", []) if data else []

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def get_matter_documents(self, matter_id: int, *, limit: int = 50) -> list[dict]:
        """List documents attached to a matter. Returns [] on failure."""
        data = self._get_json(
            "documents",
            matter_id=matter_id,
            order="created_at(desc)",
            limit=limit,
            fields="id,name,created_at,updated_at,latest_document_version{id}",
        )
        return data.get("data", []) if data else []

    def upload_document(self, matter_id: int, filename: str, file_content: bytes) -> dict:
        """Create a document record and upload the file (two-step)."""
        # Step 1: create record
        create_resp = self._request(
            "POST", "documents",
            json={"data": {"name": filename, "parent": {"id": matter_id, "type": "Matter"}}},
            max_retries=2,
        )
        doc = create_resp.json().get("data", {})
        doc_id = doc.get("id")
        if not doc_id:
            raise ValueError("Failed to create document record")

        # Step 2: upload file
        upload_resp = self._request(
            "POST", f"documents/{doc_id}/file",
            files={"file": (filename, file_content)},
            max_retries=2,
        )
        logger.info("Uploaded document %s to matter %s", doc_id, matter_id)
        return upload_resp.json().get("data", {})

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def setup_webhook(self, callback_url: str, event_types: list[str]) -> dict:
        """Register a Clio webhook."""
        resp = self._request(
            "POST", "webhooks",
            json={
                "data": {
                    "url": callback_url,
                    "fields": ",".join(event_types),
                    "events": event_types,
                }
            },
        )
        return resp.json().get("data", {})

    # ------------------------------------------------------------------
    # Convenience lookups (used by intake funnels)
    # ------------------------------------------------------------------

    def lookup_by_matter(self, query: str) -> dict:
        """Full lookup: search matter → fetch contact details.

        Returns dict with keys: found, name, email, phone,
        matter_number, matter_description. Always safe to call
        (returns {"found": False} on any failure).
        """
        try:
            matter = self.get_matter_by_number(query)
            if not matter:
                return {"found": False}

            client = matter.get("client") or {}
            result = {
                "found": True,
                "name": client.get("name", ""),
                "email": "",
                "phone": "",
                "matter_number": matter.get("display_number", ""),
                "matter_description": matter.get("description", ""),
            }

            client_id = client.get("id")
            if client_id:
                contact = self.get_contact(client_id)
                if contact:
                    result["name"] = contact.get("name", "") or result["name"]
                    emails = contact.get("email_addresses", [])
                    if emails:
                        result["email"] = emails[0].get("address", "")
                    phones = contact.get("phone_numbers", [])
                    if phones:
                        result["phone"] = phones[0].get("number", "")

            return result

        except Exception as e:
            logger.error("lookup_by_matter failed: %s", e)
            return {"found": False, "error": str(e)}

    def lookup_contact_by_email(self, email: str) -> dict:
        """Lookup a contact by email. Returns {found, name, email, phone}."""
        try:
            contacts = self.search_contacts(email)
            for contact in contacts:
                for ea in contact.get("email_addresses", []):
                    if ea.get("address", "").lower() == email.lower():
                        phone = ""
                        phones = contact.get("phone_numbers", [])
                        if phones:
                            phone = phones[0].get("number", "")
                        return {
                            "found": True,
                            "name": contact.get("name", ""),
                            "email": ea.get("address", ""),
                            "phone": phone,
                        }
            return {"found": False}
        except Exception as e:
            logger.error("lookup_contact_by_email failed: %s", e)
            return {"found": False, "error": str(e)}
