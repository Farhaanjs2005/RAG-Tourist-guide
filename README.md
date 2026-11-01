# Wayfarer — Travel agency RAG guide

A two-workspace application: admins manage PDF/Markdown knowledge and customers; customers ask Gemini grounded travel questions with source citations and private conversation history.

**Stack:** React + Vite, FastAPI, SQLite (accounts, sessions, jobs, documents, conversations), persistent ChromaDB (vectors), Gemini (embeddings and answers). There is no PostgreSQL dependency.

## Run with Docker

1. Copy `.env.example` to `.env` and set `GEMINI_API_KEY` locally. Never put the key in frontend files.
2. Run `docker compose up --build -d`.
3. Create your administrator: `docker compose exec travel-guide python -m app.create_admin`.
4. Open http://localhost:8000. Sign in as the admin, upload a document, and wait for **ready**.
5. Register a customer in another browser session and ask a question. `samples/demo-travel-packages.md` contains explicitly fictional test data.

Data persists in the `travel-data` Docker volume. API documentation: http://localhost:8000/docs; OpenAPI contract: http://localhost:8000/openapi.json.

## Local development (PowerShell)

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend/requirements.txt
$env:PYTHONPATH = "$PWD/backend"
$env:GEMINI_API_KEY = "your-key"
$env:APP_ORIGIN = "http://localhost:5173"
.\.venv\Scripts\python -m app.create_admin
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite proxy forwards `/api` requests to FastAPI. Keep the backend running with **one worker** because the ingestion worker and persistent Chroma client are process-local. Local development reads environment variables, not `.env` automatically; Docker Compose loads `.env`.

For a single-server local build, run `npm run build` in `frontend`, set `APP_ORIGIN=http://localhost:8000`, and restart FastAPI. It serves the built UI automatically.

## Configuration

| Variable | Default / purpose |
|---|---|
| `GEMINI_API_KEY` | Required for ingestion and answers; server-side only |
| `GEMINI_MODEL` | `gemini-3-flash-preview`; main answer model (works on the free tier) |
| `GEMINI_FALLBACK_MODELS` | `gemini-3.5-flash-lite,gemini-flash-lite-latest`; tried in order when the main model is out of free quota, times out, or returns invalid citations |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001`, 768-dimensional retrieval embeddings |
| `DATA_DIR` | `./data`; SQLite, uploads, Chroma |
| `APP_ORIGIN` | `http://localhost:5173` in code; example Docker env uses port 8000 |
| `COOKIE_SECURE` | `0` for local HTTP; set `1` behind HTTPS |

Changing embedding models requires rebuilding the index; incompatible models are rejected rather than silently mixed. Back up the entire data directory with the app stopped to keep SQLite, uploads, and Chroma consistent. Documents and questions are sent to Gemini for AI processing.

## Behavior

- Uploads accept `.pdf` and `.md` up to 20 MB. PDFs must have extractable text on every page; encrypted PDFs and blank/image-only pages require preparation. OCR is not included.
- SQLite-backed jobs survive restarts. Failed documents can be retried; partial vectors remain inaccessible until the whole document is ready.
- A replacement becomes active only after successful processing. The previous document becomes archived. Deleting a document removes its vectors/file and cancels pending replacements. Previously generated chat text remains in history, but source excerpts for inactive/deleted documents are unavailable.
- All signed-in customers share the agency's active document library. Conversations remain private to their owner. Admins manage account access, not customers' chat histories.
- Answers use retrieved excerpts only. The model is instructed to clarify ambiguous requests, identify missing/conflicting information, and cite sources. Citation IDs are validated, but automated checks cannot guarantee every generated statement is semantically correct; evaluate real agency documents before customer rollout.
- No booking, payment, browsing, live availability, or email/password recovery flow is included. This is a local application; internet deployment requires HTTPS, abuse controls, and an operational recovery process.

## Verification

```powershell
$env:PYTHONPATH = "$PWD/backend"
.\.venv\Scripts\python -m pytest backend/tests -q
cd frontend
npm run build
```

Tests isolate data in temporary directories and mock the AI boundary; they do not consume Gemini quota. Live answer quality needs a configured key and representative documents. The application never silently substitutes demo responses when credentials are missing.
