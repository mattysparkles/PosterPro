# PosterPro — Full Project Guide (Admin + Operator Manual)

PosterPro is a self-hosted resale operations platform for people who list lots of items and need help turning photos into clean listings, publishing to marketplaces, tracking inventory, and reducing mistakes.

In plain terms: **PosterPro is your listing back office**. You upload item photos, PosterPro helps draft listing details, and your team manages publishing, inventory, sales, and automation from one dashboard.

---

## Table of Contents

1. [What PosterPro Does](#what-posterpro-does)
2. [Who Should Use It](#who-should-use-it)
3. [How the System Works (Simple Explanation)](#how-the-system-works-simple-explanation)
4. [Current Features (Based on This Codebase)](#current-features-based-on-this-codebase)
5. [Tech Stack](#tech-stack)
6. [Repository Structure and What Each Part Does](#repository-structure-and-what-each-part-does)
7. [Backend Deep Dive (What Each Layer Does)](#backend-deep-dive-what-each-layer-does)
8. [Frontend Deep Dive (What Each Part Does)](#frontend-deep-dive-what-each-part-does)
9. [Step-by-Step Local Setup](#step-by-step-local-setup)
10. [Configuration Reference (`.env`)](#configuration-reference-env)
11. [Database and Migrations](#database-and-migrations)
12. [Background Jobs and Worker Tasks](#background-jobs-and-worker-tasks)
13. [How to Use PosterPro (Operator Walkthrough)](#how-to-use-posterpro-operator-walkthrough)
14. [Main API Areas](#main-api-areas)
15. [Testing](#testing)
16. [Troubleshooting](#troubleshooting)
17. [Security and Operational Notes](#security-and-operational-notes)
18. [Development Notes and Next Improvements](#development-notes-and-next-improvements)

---

## What PosterPro Does

PosterPro combines several jobs that sellers usually do in separate tools:

- **Photo ingestion** (single files, ZIPs, URL imports, batch flows)
- **AI listing enrichment** (title/description/category/keywords support)
- **Listing management** (edit, template apply, pricing adjustments)
- **Publishing workflows** (strongest eBay integration today)
- **Inventory safety workflows** (bulk actions, stale/multi-quantity views, labeling)
- **Sales/offer automation and tracking**
- **Operational dashboards and intelligence panels**

If you run a reselling business and need to process many listings quickly, this system is built for that style of work.

---

## Who Should Use It

### 1) Admin / Owner / Technical Lead
Use this guide for:
- install/setup
- environment config
- queue worker operations
- data migrations
- service health and troubleshooting

### 2) Daily Operator / Assistant / VA Team
Use this guide for:
- where to click and what each screen means
- how to upload/process inventory
- how to publish safely
- how to use bulk actions without causing oversells

---

## How the System Works (Simple Explanation)

Think of PosterPro as 4 connected pieces:

1. **Frontend dashboard (Next.js):** the screens your team uses.
2. **Backend API (FastAPI):** receives requests and applies business rules.
3. **Database (PostgreSQL or SQLite local default):** stores listings, inventory, jobs, sales metadata, etc.
4. **Worker queue (Redis + Celery):** runs heavy/background work such as photo processing, bulk operations, sync checks, and automation.

Basic flow:
1. You upload photos or import image sources.
2. PosterPro creates listing records.
3. Background tasks enrich/process items.
4. Team reviews and edits listings.
5. Listings are published (especially to eBay).
6. Inventory, sales, offers, and analytics keep updating over time.

---

## Current Features (Based on This Codebase)

### Listing and photo pipeline
- Upload photos and create listing records.
- Storage unit style batch pipeline with status tracking.
- Optional Google Photos import path.
- Photo editing tools (brightness/contrast/filter/crop + optional background removal via PhotoRoom API key).

### Listing intelligence and optimization
- AI-driven listing text assistance.
- Pricing and optimization service layer.
- Prediction/intelligence modules for operational insights.

### Inventory operations
- Inventory endpoint with filtering, searching, stale views, pagination.
- Unlimited-style bulk actions via queued jobs.
- Bulk edit and safety checks through inventory service logic.

### Marketplace operations
- eBay auth/publish/status flows.
- Marketplace abstraction + connector registry for multiple platforms (eBay, Mercari, Poshmark, Depop, Whatnot, Vinted, Facebook Marketplace, fallback connector).
- Marketplace status workflows from unified services.

### Sales and offers
- Sales API area and services.
- Offer rules/history/automation paths.
- Profit/sale detection components.

### Dashboard and UX
- Main app shell and multi-page operator dashboard.
- Panels for setup, sync, intelligence, marketplace status, and published listings.
- Auth gate and role-aware flows.

---

## Tech Stack

- **Backend:** FastAPI, SQLAlchemy, Pydantic Settings, Celery, Redis, HTTPX
- **Frontend:** Next.js 14, React 18, TailwindCSS, componentized UI toolkit
- **Data:** PostgreSQL in Docker compose for local infra; SQLite defaults for quick local boot if env vars are absent
- **Image/ML Helpers:** Pillow, NumPy, scikit-learn

---

## Repository Structure and What Each Part Does

```text
/backend
  /app
    /api          -> HTTP routes grouped by domain (auth, listings, inventory, marketplaces, sales, intelligence, ebay)
    /connectors   -> platform-specific marketplace connector implementations
    /core         -> runtime config, auth helpers, DB setup
    /models       -> ORM models and enums
    /prompts      -> AI prompt templates used by listing/intelligence services
    /services     -> core business logic (pricing, publishing, inventory safety, analytics, offers, etc.)
    /workers      -> Celery app + async task definitions
  /migrations     -> SQL migrations
  /tests          -> backend test suite
/frontend
  /components     -> reusable UI components and feature panels
  /contexts       -> global state like auth context
  /hooks          -> custom hooks for dashboard data and workflow APIs
  /lib            -> frontend API utilities
  /pages          -> Next.js routes/screens
  /styles         -> global and module styles
/docker-compose.yml -> local Postgres + Redis services
/README.md          -> this file
```

---

## Backend Deep Dive (What Each Layer Does)

### `backend/app/main.py`
- Boots FastAPI.
- Registers all API routers (auth, core routes, eBay, marketplaces, intelligence, inventory, bulk-jobs, sales).
- Mounts media files from storage root.
- Exposes `/health` endpoint.
- Ensures minimal user-table columns exist at startup.

### `backend/app/api/*`
- **`routes.py`**: central listing/ingestion/template/photo-tools and core listing actions.
- **`inventory.py`**: inventory list, bulk edit, bulk queued job APIs.
- **`marketplaces.py`**: multi-marketplace flows.
- **`ebay.py`**: eBay-specific auth/publishing/status APIs.
- **`sales.py`**: sales management APIs.
- **`intelligence.py`**: analytics/intelligence-facing APIs.
- **`auth.py`**: login/registration/session-scoping behavior.

### `backend/app/services/*`
This is where the main business rules live:
- listing AI/enrichment
- inventory safety and quantity rules
- marketplace orchestration and publishing
- pricing and intelligence logic
- photo processing/edit helpers
- sale detection and offer automation

### `backend/app/connectors/*`
Each connector represents marketplace-specific behavior behind a shared interface so the system can publish/sync in a unified way.

### `backend/app/workers/*`
Celery app and async tasks for background jobs such as large batch processing, bulk operations, and automation tasks.

---

## Frontend Deep Dive (What Each Part Does)

### Routing/pages (`frontend/pages`)
You have dedicated screens for:
- dashboard (`/app`)
- listings
- inventory
- published listings
- sales
- analytics
- offers
- settings
- auth (login/register)

### Components (`frontend/components`)
- High-level feature panels (sync, intelligence, setup checklist, status panels)
- Listing editor and photo modal flows
- Reusable UI primitives (`button`, `card`, `tabs`, `data-table`, etc.)
- App shell layout and navigation framing

### Hooks (`frontend/hooks`)
Custom hooks centralize API interactions and state management for dashboard data, auth, publish flows, and batch progress.

### Context (`frontend/contexts/AuthContext.js`)
Global authentication state + helper methods consumed by protected screens.

---

## Step-by-Step Local Setup

## 1) Start infrastructure

```bash
docker compose up -d db redis
```

## 2) Start backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 3) Start worker (new terminal)

```bash
cd backend
source .venv/bin/activate
celery -A app.workers.celery_app.celery_app worker --loglevel=info
```

## 4) Start frontend (new terminal)

```bash
cd frontend
npm install
npm run dev
```

App URLs (default):
- Frontend: `http://localhost:3000`
- Backend API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

---

## Configuration Reference (`.env`)

Backend settings are read by `pydantic-settings` from `.env`.

Important keys:

- `DATABASE_URL` (default local fallback uses SQLite file)
- `REDIS_URL`
- `STORAGE_ROOT`
- `SESSION_SECRET`
- `OPENAI_API_KEY`
- `EBAY_CLIENT_ID`
- `EBAY_CLIENT_SECRET`
- `EBAY_REDIRECT_URI`
- `PHOTOROOM_API_KEY`
- `PHOTOROOM_API_URL`
- `AUTONOMOUS_MODE`
- `AUTONOMOUS_DRY_RUN`
- `AUTONOMOUS_CROSSPOST_ENABLED`
- `AUTO_RELIST_ENABLED`
- `AUTO_RELIST_MIN_PRICE`
- `SALE_DETECTION_ENABLED`
- `SALE_DETECTION_DRY_RUN`
- `SALE_DETECTION_POLL_MINUTES`
- `MAX_CONCURRENT_BULK_TASKS`
- `BULK_CHUNK_SIZE`

Tip: create a `.env` inside `backend/` during local development and set production-safe secrets for hosted deployments.

---

## Database and Migrations

- SQL migrations live in `backend/migrations/`.
- The codebase includes migrations for inventory scaling, listing templates, marketplace support, pricing, photo pipelines, sales detection, auth bootstrap, and more.
- `Base.metadata.create_all(bind=engine)` in app startup ensures schema objects exist for ORM models.

Recommended production pattern:
1. Apply migrations in controlled release steps.
2. Back up DB before major schema updates.
3. Keep worker version and API version in sync after migration changes.

---

## Background Jobs and Worker Tasks

PosterPro uses Celery workers for work that should not block API responses.

Examples:
- photo batch processing
- storage-unit pipeline execution
- clustering/import follow-up tasks
- large inventory bulk job chunks
- automation checks (offers/sales/relist patterns)

Why this matters in plain terms:
- Your UI stays responsive while heavy tasks run in the background.
- You can process much larger inventories reliably.

---

## How to Use PosterPro (Operator Walkthrough)

1. **Sign in** using your workspace account.
2. **Upload new inventory photos** from listing/ingestion workflows.
3. Wait for **processing/enrichment** to complete.
4. Open each listing, **review title/description/price/condition**.
5. Use **photo tools** if needed to improve listing quality.
6. Use **templates** to speed repetitive listing fields.
7. Publish to marketplace (especially eBay flow).
8. Manage **inventory filters** and run **bulk actions** for large updates.
9. Monitor **sales/offers/intelligence** pages for operations and optimization opportunities.
10. Use dashboard alerts/status sections to catch failures early.

---

## Main API Areas

- `GET /health` — service health.
- Auth endpoints under auth router.
- Listing/template/ingestion endpoints in `routes.py` (including photo tools and imports).
- Inventory endpoints under `/inventory` and bulk job status at `/bulk-jobs/{job_id}`.
- eBay flow endpoints in eBay router.
- Marketplace orchestration routes in marketplace router.
- Sales and intelligence routes in respective routers.

Use `/docs` for interactive API exploration in local/dev mode.

---

## Testing

Backend tests are in `backend/tests` and include:
- eBay service/publish flows
- marketplace API/connector behavior
- offer services
- inventory bulk sale paths
- reseller intelligence
- storage unit batch e2e patterns

Run:

```bash
cd backend
PYTHONPATH=. DATABASE_URL=sqlite:///./test.db pytest tests -q
```

---

## Troubleshooting

### API starts but frontend cannot fetch data
- Confirm frontend uses correct API base URL (`NEXT_PUBLIC_API_BASE`).
- Confirm backend is running on expected host/port.

### Jobs are stuck in queued/pending status
- Confirm Redis is up.
- Confirm Celery worker is running and pointed at same Redis URL as API.

### eBay publish fails
- Verify `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, and redirect URI settings.
- Re-run OAuth connect flow.
- Check listing payload completeness before publish.

### Photo background removal not working
- Ensure `PHOTOROOM_API_KEY` is set.
- Check network egress and API response errors.

### Inventory bulk actions partially applied
- Inspect bulk job status endpoint for error details and per-item failures.

---

## Security and Operational Notes

- Do not commit real API keys or session secrets.
- Use HTTPS and secure cookies in production deployments.
- Restrict who can run admin-level inventory and publishing actions.
- Keep audit logs/backups for high-volume production usage.
- Consider rate limits and marketplace policy compliance when enabling full automation.

---

## Development Notes and Next Improvements

Possible improvement areas you can implement next:
- stronger migration runner integration and release automation
- richer observability (metrics dashboards, tracing)
- advanced role-based permissions and audit trails
- fuller docs for each API path with request/response examples
- expanded connector capability matrix by marketplace

---

If you want, I can also produce:
1. a separate **Operator SOP handbook** for non-technical assistants,
2. a **Production Deployment Guide** (reverse proxy, SSL, backups, process supervision), and
3. a **Marketplace-by-marketplace capability table** (publish/edit/sync/sale-detect support).
