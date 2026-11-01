# Verification record

## Passed in this workspace

- Python compilation of backend application and tests.
- Node syntax check of Vite configuration.
- SQLite smoke check: schema initialization, records persisted across connections, cascading deletion of messages, repeat initialization.

## Written but not executed

`backend/tests/test_app.py` covers authentication and disabled sessions, admin restrictions, conversation isolation, document ingestion/replacement/deletion, failed replacements, invalid uploads, origin checks, cited chat/history flow with a mocked Gemini boundary, invalid citations, missing documents, Markdown and blank PDF extraction, bounded retries, pending replacement cancellation, and worker restart recovery.

## Environment limits

- Python packages could not be installed: package indexes returned no available distributions in this restricted environment. Virtual-environment pip bootstrapping also failed.
- npm dependencies could not be installed: offline cache entries were insufficient and the configured network proxy refused registry connections.
- Therefore the React production build, backend integration suite, browser checks, and Docker build have not run.
- Postman CLI failed at startup because its managed Private Cloud manifest could not be read; no Postman simulation ran.
- No Gemini API key was supplied. Live ingestion, retrieval quality, grounded generation, conflicting-price handling, and prompt-injection resistance require evaluation against real documents. Unit tests of a mocked AI response do not establish these model behaviors.

Run the commands in README.md on a machine with package access, then test the sample document with a Gemini key. All implementation files are present; runtime validation remains outstanding.
