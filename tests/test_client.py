"""
Contract tests for ClioClient.

All HTTP calls are mocked with the `responses` library — no real Clio access.
Tests verify:
  - OAuth token refresh on 401
  - Rate-limit (429) backoff
  - Every public method's request shape and return value
  - Bearer-token mode (no refresh)
  - Interface compatibility with every consuming repo
"""

import json
import os
import tempfile

import pytest
import responses

from jaskot_clio import ClioClient

API = "https://app.clio.com/api/v4"
TOKEN_URL = "https://app.clio.com/oauth/token"


@pytest.fixture
def token_file(tmp_path):
    tf = tmp_path / "tokens.json"
    tf.write_text(json.dumps({
        "access_token": "test-access",
        "refresh_token": "test-refresh",
    }))
    return str(tf)


@pytest.fixture
def client(token_file):
    return ClioClient(
        client_id="test-id",
        client_secret="test-secret",
        token_file=token_file,
    )


@pytest.fixture
def bearer_client():
    return ClioClient(access_token="static-token")


# ---------------------------------------------------------------
# Auth / token management
# ---------------------------------------------------------------

class TestAuth:
    def test_loads_tokens_from_file(self, client):
        assert client.is_authenticated()
        assert client.tokens["access_token"] == "test-access"

    def test_bearer_token_mode(self, bearer_client):
        assert bearer_client.is_authenticated()
        assert not bearer_client._can_refresh

    def test_no_tokens_not_authenticated(self, tmp_path):
        c = ClioClient(client_id="x", client_secret="y", token_file=str(tmp_path / "nope.json"))
        assert not c.is_authenticated()

    @responses.activate
    def test_refresh_on_401(self, client):
        # First call returns 401
        responses.add(responses.GET, f"{API}/matters", status=401)
        # Refresh succeeds
        responses.add(responses.POST, TOKEN_URL, json={
            "access_token": "new-access",
            "refresh_token": "new-refresh",
        })
        # Retry succeeds
        responses.add(responses.GET, f"{API}/matters", json={"data": []})

        result = client.search_matters("test")
        assert result == []
        assert client.tokens["access_token"] == "new-access"

    @responses.activate
    def test_bearer_no_refresh_on_401(self, bearer_client):
        responses.add(responses.GET, f"{API}/matters", status=401)
        with pytest.raises(Exception):
            bearer_client.search_matters("test")

    @responses.activate
    def test_exchange_code(self, tmp_path):
        tf = str(tmp_path / "tokens.json")
        c = ClioClient(client_id="cid", client_secret="csec", token_file=tf)
        responses.add(responses.POST, TOKEN_URL, json={
            "access_token": "new", "refresh_token": "ref",
        })
        c.exchange_code("auth-code")
        assert c.tokens["access_token"] == "new"
        assert json.loads(open(tf).read())["access_token"] == "new"


# ---------------------------------------------------------------
# Matters
# ---------------------------------------------------------------

SAMPLE_MATTER = {
    "id": 123,
    "display_number": "00582",
    "description": "SIJS - Smith",
    "client": {"id": 456, "name": "John Smith"},
    "status": "open",
    "practice_area": {"name": "Immigration"},
}


class TestMatters:
    @responses.activate
    def test_search_matters(self, client):
        responses.add(responses.GET, f"{API}/matters", json={"data": [SAMPLE_MATTER]})
        result = client.search_matters("Smith")
        assert len(result) == 1
        assert result[0]["display_number"] == "00582"

    @responses.activate
    def test_get_matter_by_number_exact(self, client):
        responses.add(responses.GET, f"{API}/matters", json={"data": [
            {"display_number": "00581", "id": 1},
            {"display_number": "00582", "id": 2},
        ]})
        result = client.get_matter_by_number("00582")
        assert result["id"] == 2

    @responses.activate
    def test_get_matter_by_number_fallback(self, client):
        responses.add(responses.GET, f"{API}/matters", json={"data": [
            {"display_number": "00583", "id": 3},
        ]})
        result = client.get_matter_by_number("00582")
        assert result["id"] == 3

    @responses.activate
    def test_get_matter_by_number_not_found(self, client):
        responses.add(responses.GET, f"{API}/matters", json={"data": []})
        assert client.get_matter_by_number("99999") is None

    @responses.activate
    def test_get_matter_details(self, client):
        responses.add(responses.GET, f"{API}/matters/123", json={"data": SAMPLE_MATTER})
        result = client.get_matter_details(123)
        assert result["description"] == "SIJS - Smith"

    @responses.activate
    def test_search_matters_by_contact(self, client):
        responses.add(responses.GET, f"{API}/matters", json={"data": [SAMPLE_MATTER]})
        result = client.search_matters_by_contact(456)
        assert len(result) == 1

    @responses.activate
    def test_create_matter(self, client):
        responses.add(responses.POST, f"{API}/matters", json={"data": {
            "id": 999, "display_number": "01000",
        }})
        result = client.create_matter(456, "New SIJS", practice_area_id=10)
        assert result["display_number"] == "01000"
        body = json.loads(responses.calls[-1].request.body)
        assert body["data"]["practice_area"] == {"id": 10}


# ---------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------

SAMPLE_CONTACT = {
    "id": 456,
    "name": "John Smith",
    "email_addresses": [{"address": "john@example.com"}],
    "phone_numbers": [{"number": "+1234567890"}],
}


class TestContacts:
    @responses.activate
    def test_get_contact(self, client):
        responses.add(responses.GET, f"{API}/contacts/456", json={"data": SAMPLE_CONTACT})
        result = client.get_contact(456)
        assert result["name"] == "John Smith"

    @responses.activate
    def test_search_contact_by_email_found(self, client):
        responses.add(responses.GET, f"{API}/contacts", json={"data": [SAMPLE_CONTACT]})
        result = client.search_contact_by_email("john@example.com")
        assert result["id"] == 456

    @responses.activate
    def test_search_contact_by_email_not_found(self, client):
        responses.add(responses.GET, f"{API}/contacts", json={"data": [SAMPLE_CONTACT]})
        result = client.search_contact_by_email("other@example.com")
        assert result is None

    @responses.activate
    def test_create_contact(self, client):
        responses.add(responses.POST, f"{API}/contacts", json={"data": {"id": 789, "name": "Jane Doe"}})
        result = client.create_contact("Jane", "Doe", email="jane@example.com", phone="+1999999999")
        assert result["id"] == 789
        body = json.loads(responses.calls[-1].request.body)
        assert body["data"]["first_name"] == "Jane"
        assert body["data"]["email_addresses"][0]["address"] == "jane@example.com"

    @responses.activate
    def test_find_or_create_existing(self, client):
        responses.add(responses.GET, f"{API}/contacts", json={"data": [SAMPLE_CONTACT]})
        result = client.find_or_create_contact("John", "Smith", email="john@example.com")
        assert result["id"] == 456
        assert len(responses.calls) == 1  # no create call

    @responses.activate
    def test_find_or_create_new(self, client):
        responses.add(responses.GET, f"{API}/contacts", json={"data": []})
        responses.add(responses.POST, f"{API}/contacts", json={"data": {"id": 800, "name": "New Person"}})
        result = client.find_or_create_contact("New", "Person", email="new@example.com")
        assert result["id"] == 800


# ---------------------------------------------------------------
# Notes
# ---------------------------------------------------------------

class TestNotes:
    @responses.activate
    def test_get_matter_notes(self, client):
        responses.add(responses.GET, f"{API}/notes", json={"data": [
            {"id": 1, "subject": "Meeting", "date": "2026-03-01"},
        ]})
        result = client.get_matter_notes(123)
        assert len(result) == 1

    @responses.activate
    def test_find_note_by_subject_found(self, client):
        responses.add(responses.GET, f"{API}/notes", json={"data": [
            {"id": 1, "subject": "Meeting Notes - 2026-03-01", "date": "2026-03-01"},
            {"id": 2, "subject": "Other", "date": "2026-03-02"},
        ]})
        result = client.find_note_by_subject(123, "Meeting Notes - 2026-03-01")
        assert result["id"] == 1

    @responses.activate
    def test_find_note_by_subject_not_found(self, client):
        responses.add(responses.GET, f"{API}/notes", json={"data": []})
        assert client.find_note_by_subject(123, "Nonexistent") is None

    @responses.activate
    def test_create_note(self, client):
        responses.add(responses.POST, f"{API}/notes", json={"data": {"id": 10}})
        result = client.create_note(123, "Subject", "Detail text", "2026-03-25")
        assert result["id"] == 10
        body = json.loads(responses.calls[-1].request.body)
        assert body["data"]["matter"]["id"] == 123


# ---------------------------------------------------------------
# Activities, Bills, Documents, Webhooks
# ---------------------------------------------------------------

class TestActivities:
    @responses.activate
    def test_get_matter_activities(self, client):
        responses.add(responses.GET, f"{API}/activities", json={"data": [{"id": 1}]})
        assert len(client.get_matter_activities(123)) == 1

    @responses.activate
    def test_log_activity(self, client):
        responses.add(responses.POST, f"{API}/activities", json={"data": {"id": 5}})
        result = client.log_activity(123, 1, "Review docs", "Research", "2026-03-25T10:00:00")
        assert result["id"] == 5


class TestTransactions:
    @responses.activate
    def test_get_matter_transactions(self, client):
        responses.add(responses.GET, f"{API}/bills", json={"data": [{"id": 1}]})
        assert len(client.get_matter_transactions(123)) == 1


class TestDocuments:
    @responses.activate
    def test_get_matter_documents(self, client):
        responses.add(responses.GET, f"{API}/documents", json={"data": [{"id": 1}]})
        assert len(client.get_matter_documents(123)) == 1

    @responses.activate
    def test_upload_document(self, client):
        responses.add(responses.POST, f"{API}/documents", json={"data": {"id": 50}})
        responses.add(responses.POST, f"{API}/documents/50/file", json={"data": {"id": 50}})
        result = client.upload_document(123, "file.pdf", b"content")
        assert result["id"] == 50
        assert len(responses.calls) == 2


class TestWebhooks:
    @responses.activate
    def test_setup_webhook(self, client):
        responses.add(responses.POST, f"{API}/webhooks", json={"data": {"id": 1}})
        result = client.setup_webhook("https://example.com/hook", ["matter.created"])
        assert result["id"] == 1


# ---------------------------------------------------------------
# Convenience lookups
# ---------------------------------------------------------------

class TestLookups:
    @responses.activate
    def test_lookup_by_matter_found(self, client):
        responses.add(responses.GET, f"{API}/matters", json={"data": [SAMPLE_MATTER]})
        responses.add(responses.GET, f"{API}/contacts/456", json={"data": SAMPLE_CONTACT})

        result = client.lookup_by_matter("00582")
        assert result["found"] is True
        assert result["name"] == "John Smith"
        assert result["email"] == "john@example.com"
        assert result["matter_number"] == "00582"

    @responses.activate
    def test_lookup_by_matter_not_found(self, client):
        responses.add(responses.GET, f"{API}/matters", json={"data": []})
        result = client.lookup_by_matter("99999")
        assert result["found"] is False

    @responses.activate
    def test_lookup_contact_by_email_found(self, client):
        responses.add(responses.GET, f"{API}/contacts", json={"data": [SAMPLE_CONTACT]})
        result = client.lookup_contact_by_email("john@example.com")
        assert result["found"] is True
        assert result["phone"] == "+1234567890"


# ---------------------------------------------------------------
# Interface compatibility — every consuming repo's expected methods
# ---------------------------------------------------------------

class TestInterfaceCompatibility:
    """Verify the shared client exposes every method that consuming repos call."""

    def test_clio_mcp_server_interface(self, client):
        """clio-mcp-server uses these methods."""
        assert callable(client.search_matters)
        assert callable(client.get_matter_by_number)
        assert callable(client.get_matter_details)
        assert callable(client.get_matter_notes)
        assert callable(client.get_matter_activities)
        assert callable(client.get_matter_transactions)
        assert callable(client.get_matter_documents)
        assert callable(client.create_note)

    def test_sijs_intake_interface(self, client):
        """sijs-intake uses these methods."""
        assert callable(client.search_contact_by_email)
        assert callable(client.create_contact)
        assert callable(client.find_or_create_contact)
        assert callable(client.search_matters_by_contact)
        assert callable(client.create_matter)
        assert callable(client.get_contact)
        assert callable(client.lookup_by_matter)

    def test_generic_intake_interface(self, client):
        """generic-intake / staff-portal use these methods."""
        assert callable(client.lookup_by_matter)
        assert callable(client.lookup_contact_by_email)

    def test_meeting_processor_interface(self, client):
        """meeting-processor uses these methods."""
        assert callable(client.search_matters)
        assert callable(client.find_note_by_subject)
        assert callable(client.create_note)
        assert callable(client.log_activity)
        assert callable(client.upload_document)
        assert callable(client.setup_webhook)

    def test_ytb_framework_bearer_mode(self, bearer_client):
        """ytb-framework uses bearer token mode for matter lookups."""
        assert bearer_client.is_authenticated()
        assert callable(bearer_client.search_matters)
        assert callable(bearer_client.get_matter_details)

    def test_oscar_staff_modes_interface(self, client):
        """oscar-staff-modes uses full ClioClient."""
        assert callable(client.get_auth_url)
        assert callable(client.exchange_code)
        assert callable(client.refresh_token)
        assert callable(client.search_matters)
        assert callable(client.get_matter_details)
        assert callable(client.get_matter_notes)
        assert callable(client.create_note)


# ---------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------

class TestRateLimiting:
    @responses.activate
    def test_429_retries(self, client):
        responses.add(responses.GET, f"{API}/matters", status=429, headers={"Retry-After": "0"})
        responses.add(responses.GET, f"{API}/matters", json={"data": []})
        result = client.search_matters("test")
        assert result == []
        assert len(responses.calls) == 2
