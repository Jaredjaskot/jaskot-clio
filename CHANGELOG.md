# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-03-29

### Added

- OAuth 2.0 authorization flow with interactive browser-based setup
- Automatic token refresh on 401 responses
- Bearer token mode for simple integrations
- Rate limit handling (429 with Retry-After backoff)
- Matters API: search, get by number, details, by contact, create
- Contacts API: search, get by ID, search by email, create, find-or-create
- Notes API: list, find by subject (dedup), create
- Activities API: list time entries, log activity
- Bills/Transactions API: list
- Documents API: list, upload (two-step)
- Webhooks API: register callback
- Convenience lookups: `lookup_by_matter`, `lookup_contact_by_email`
- Comprehensive test suite (39 tests)
- GitHub Actions CI across Python 3.10-3.12
