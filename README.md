# jaskot-clio

Shared Clio API client for Jaskot Law automation. Provides a single, OAuth-capable Python client for [Clio's API v4](https://app.clio.com/api/v4/documentation) with automatic token refresh and rate-limit handling.

## Installation

```bash
pip install git+https://github.com/Jaredjaskot/jaskot-clio.git
```

Or add to your `requirements.txt`:

```
jaskot-clio @ git+https://github.com/Jaredjaskot/jaskot-clio.git
```

## Quick Start

### OAuth Mode (recommended)

First, run the interactive setup to obtain tokens:

```bash
python -m jaskot_clio.auth \
    --client-id YOUR_CLIENT_ID \
    --client-secret YOUR_CLIENT_SECRET
```

This opens a browser for Clio authorization and saves tokens to `clio_tokens.json`. Then use the client:

```python
from jaskot_clio import ClioClient

client = ClioClient(
    client_id="YOUR_CLIENT_ID",
    client_secret="YOUR_CLIENT_SECRET",
    token_file="clio_tokens.json",
)

matters = client.search_matters("Smith")
```

Tokens are automatically refreshed on 401 responses.

### Bearer Token Mode

For simple integrations where you already have an access token:

```python
client = ClioClient(access_token="YOUR_ACCESS_TOKEN")
```

No automatic refresh is performed in this mode.

## API Reference

### Matters

```python
client.search_matters("query")                  # Search by keyword
client.get_matter_by_number("00001-Smith")       # Find by display number
client.get_matter_details(matter_id)             # Full matter details
client.search_matters_by_contact(contact_id)     # Matters for a contact
client.create_matter(client_id, "Description")   # Create a new matter
```

### Contacts

```python
client.search_contacts("Jane Doe")              # Search by name/keyword
client.get_contact(contact_id)                   # Fetch by ID
client.search_contact_by_email("jane@example.com")  # Find by exact email
client.create_contact("Jane", "Doe", email="jane@example.com")
client.find_or_create_contact("Jane", "Doe", email="jane@example.com")
```

### Notes

```python
client.get_matter_notes(matter_id)               # List notes (newest first)
client.find_note_by_subject(matter_id, "Subject") # Dedup by subject
client.create_note(matter_id, "Subject", "Detail", "2026-01-15")
```

### Activities

```python
client.get_matter_activities(matter_id)          # List time entries
client.log_activity(matter_id, user_id, "Description", "TimeEntry", "2026-01-15")
```

### Documents

```python
client.get_matter_documents(matter_id)           # List documents
client.upload_document(matter_id, "file.pdf", file_bytes)
```

### Bills / Transactions

```python
client.get_matter_transactions(matter_id)        # List bills
```

### Webhooks

```python
client.setup_webhook("https://example.com/hook", ["matter.created"])
```

### Convenience Lookups

Safe methods that return `{"found": False}` instead of raising on failure:

```python
client.lookup_by_matter("00001-Smith")           # Matter + contact details
client.lookup_contact_by_email("jane@example.com")
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

[MIT](LICENSE)
