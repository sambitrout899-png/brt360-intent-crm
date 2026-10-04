# BRT 360 — Intent CRM

Sprint 1 vertical slice (INT-01/02/03 — Launch/Modify/Retire a bundle), built per
`docs/SPRINT1_IMPLEMENTATION_PLAN.md` against `BRT360_Intent_CRM_HLD.docx`.

## Run it

**Backend** (Python 3.11+, FastAPI):

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` on Linux/Mac
pip install -r requirements.txt
copy .env.example .env          # optionally set OPENAI_API_KEY - omitted, uses the rule-based fallback classifier
python -m app.seed              # seeds atomic products + bundle compatibility rules
python -m uvicorn app.main:app --reload --port 8000
```

API docs: http://127.0.0.1:8000/docs

**Frontend** (Node 20+, Vite + React + TypeScript):

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 — three tabs: Intent Command Bar, Catalog, Audit Log.
Three quick-fill example buttons on the Intent Command Bar demonstrate INT-01/02/03
end to end (launch a bundle, then modify it, then retire it with a replacement).

## Tests

```bash
cd backend
pytest -q
```

7 tests covering: successful execution of all 3 intents, composition-rule
rejection, missing-migration-path rejection (routed to human review), and
version-chain integrity (supersede/retire never mutates or deletes a row).

## What's here vs. what's next

See `docs/SPRINT1_IMPLEMENTATION_PLAN.md` for the full design rationale,
including the fallback-classifier phrasing convention (so you can drive the
UI without an OpenAI key), and section 9 for what's deliberately deferred
past Sprint 1 (real TMF620/OSM integration, Kafka, RBAC/OAuth2,
multi-tenancy).
