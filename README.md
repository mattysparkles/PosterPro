# PosterPro — Complete Operator & User Manual

PosterPro is a self-hosted **reseller operations platform** designed for high-volume, photo-first listing workflows.

It helps you:
- ingest large photo batches (single uploads, ZIPs, URL lists, storage-unit style lots),
- auto-enrich listings with AI-generated copy and metadata,
- price and publish to eBay (and queue multi-marketplace publishing),
- manage inventory at scale with unlimited bulk actions,
- detect sales and keep cross-posted inventory synchronized,
- automate offers, relisting, analytics, and operational reporting.

This README is intentionally written as a full instruction manual for two audiences:
1. **Self-hosters/admins** running PosterPro infrastructure.
2. **End users/operators** (future you, staff, assistants, VA teams) using your hosted instance day to day.

---

## Table of Contents

1. [What PosterPro Is](#what-posterpro-is)
2. [Who This Is For](#who-this-is-for)
3. [Core Concepts](#core-concepts)
4. [Feature Map (What Exists Today)](#feature-map-what-exists-today)
5. [System Architecture](#system-architecture)
6. [Project Structure](#project-structure)
7. [Prerequisites](#prerequisites)
8. [Quick Start (Local Development)](#quick-start-local-development)
9. [Configuration Reference (.env)](#configuration-reference-env)
10. [Database & Migrations](#database--migrations)
11. [Task Worker & Background Jobs](#task-worker--background-jobs)
12. [Operational Runbook (Admin)](#operational-runbook-admin)
13. [User Onboarding Guide (Non-Technical)](#user-onboarding-guide-non-technical)
14. [UI Walkthrough by Page](#ui-walkthrough-by-page)
15. [Feature Deep Dives](#feature-deep-dives)
16. [API Reference (Practical)](#api-reference-practical)
17. [Recommended Team SOPs](#recommended-team-sops)
18. [Troubleshooting](#troubleshooting)
19. [Security, Privacy, and Safety Notes](#security-privacy-and-safety-notes)
20. [Testing](#testing)
21. [Roadmap-Ready Extension Points](#roadmap-ready-extension-points)
22. [Glossary](#glossary)

---

## What PosterPro Is

PosterPro is a **Reseller Command Center** that unifies ingestion, listing operations, marketplace publishing, inventory safety controls, sales tracking, analytics, and offer automation.

It uses:
- **FastAPI** backend for API + orchestration,
- **PostgreSQL** for durable business data,
- **Redis + Celery** for background processing,
- **Next.js** frontend for operator workflows,
- marketplace integration services (currently strongest for **eBay**).

PosterPro is optimized for real reseller workflows where you need to process many items quickly, delegate operations to assistants, and reduce overselling mistakes.

---

## Who This Is For

### A) Self-Hoster / Owner / Technical Admin
You care about:
- installation,
- configuration,
- API secrets,
- workers and queues,
- migration application,
- uptime and monitoring.

### B) Daily Operator / Assistant / Employee
You care about:
- where to click,
- what each toggle means,
- how to process new inventory,
- how to publish safely,
- how to mark sold and avoid double-selling,
- how to export reports and complete bookkeeping.

This manual covers both.

---

## Core Concepts

Understanding these will make the app intuitive.

### 1) Listing lifecycle statuses
Listings can move through states such as:
- `INGESTED` → photo received
- `PROCESSED` → AI enrichment complete
- `PUBLISHED` / `posted` → live on marketplace
- `FAILED` → processing/publishing failed
- `draft` / `ready` states are also used in parts of the flow

### 2) Storage Unit Batch
A storage unit batch is a group of many photos processed together. It tracks:
- total item count,
- processed count,
- status (`INGESTED`, `QUEUED`, `PROCESSING`, `COMPLETED`, `FAILED`),
- optional overnight scheduling.

### 3) Inventory safety model
PosterPro has explicit controls to prevent oversells:
- central quantity,
- per-platform quantities,
- stale listing signals,
- sale sync + mark sold pathways,
- guarded bulk operations.

### 4) Autonomous mode
When enabled, newly processed listings can trigger autonomous publishing logic (dry-run or live depending on config).

### 5) Bulk jobs
Large operations (mark sold, relist, refresh, label, etc.) are queued and processed in chunks asynchronously so you can handle large inventories.

---

## Feature Map (What Exists Today)

### Ingestion & Listing Creation
- Upload photos directly.
- Upload ZIPs for storage units.
- Submit image URL arrays for mobile-friendly ingestion.
- Optional Google Photos import path.
- AI enrichment for title/description/category/tags/item specifics/estimated value.

### Listing Editing
- Manual edits to title, description, pricing, condition, quantity, labels.
- Listing template create/apply support.
- Photo tools: brightness, contrast, filters, crop, optional background removal.

### Marketplace Workflows
- eBay OAuth connect flow.
- eBay publish endpoint and status polling.
- Multi-marketplace publish queue abstraction (ebay/etsy/facebook/mercari/poshmark/depop/whatnot/vinted modeled).
- Marketplace status history per listing.

### Inventory Command Center
- Search/filter/pagination.
- Tabs for multi-quantity and stale views.
- Grid + table modes.
- Virtualized table behavior for large inventories.
- Unlimited bulk actions with tracked job progress.

### Sales & Offer Ops
- Sales dashboard + timeline.
- Sale detection marketplace settings.
- Sale detail patching for bookkeeping (fees/shipping/notes).
- CSV exports (sales + inventory).
- Offer automation rules and offer history.
- Manual “send offers now” trigger.

### Intelligence & Analytics
- Overview/dashboard analytics.
- Listing-level analytics detail.
- Pricing recommendations.
- Listing optimization.
- Sell-through prediction.
- Alerts.

### Automation
- Photo batch processing pipeline.
- Storage batch pipeline (including overnight queue mode).
- Auto-pricing tasks.
- Autonomous publish task.
- Sale polling task.
- Offer processing/sending tasks.
- Relist monitoring task.

---

## System Architecture

```text
[Next.js Frontend]
        |
        v
 [FastAPI API Layer] -----> [PostgreSQL]
        |
        +------> [Redis broker]
                    |
                    v
               [Celery workers]
                    |
                    v
       [AI enrichment / publishing / bulk jobs / polling]
```

### High-level flow
1. Operator uploads photos.
2. API writes listing records and storage files.
3. Celery tasks enrich listings and optionally trigger autonomous publishing.
4. Operator reviews/edits in UI.
5. Publish actions hit marketplace services.
6. Sales and offer automations run in background.
7. Analytics and reports update continuously.

---

## Project Structure

```text
/backend
  /app
    /api           # REST endpoints (listings, inventory, marketplaces, sales, intelligence, ebay)
    /connectors    # marketplace connector implementations
    /core          # config + DB bootstrap
    /models        # SQLAlchemy models + enums
    /prompts       # AI prompt templates
    /services      # business logic layer
    /workers       # Celery app + tasks
  /migrations      # SQL migration files
  /tests           # backend tests
/frontend
  /components      # UI building blocks
  /hooks           # data hooks + workflow hooks
  /lib             # API client + utilities
  /pages           # app routes/pages
/docker-compose.yml
/README.md
```

---

## Prerequisites

- Python 3.10+
- Node.js 18+
- PostgreSQL 16 (or compatible)
- Redis 7
- pip + virtualenv
- npm

Optional/feature-specific:
- OpenAI API key (AI enrichment features)
- eBay developer app credentials
- Photoroom API key (background removal in photo tools)

---

## Quick Start (Local Development)

### 1) Start infrastructure services

```bash
docker compose up -d db redis
```

### 2) Start backend API

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3) Start Celery worker

In a second terminal:

```bash
cd backend
source .venv/bin/activate
celery -A app.workers.celery_app.celery_app worker --loglevel=info
```

### 4) Start frontend

In a third terminal:

```bash
cd frontend
npm install
npm run dev
```

### 5) Open app

- Frontend: `http://localhost:3000`
- Backend API docs (if enabled by default FastAPI config): `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

---

## Configuration Reference (.env)

PosterPro reads backend settings from `backend/.env`.

### Core
- `APP_NAME` (default: PosterPro)
- `ENVIRONMENT` (default: development)
- `DATABASE_URL` (default local postgres URI)
- `REDIS_URL` (default local redis URI)
- `STORAGE_ROOT` (default `./storage`)

### AI / Enrichment
- `OPENAI_API_KEY`

### eBay Integration
- `EBAY_CLIENT_ID`
- `EBAY_CLIENT_SECRET`
- `EBAY_REDIRECT_URI`

### Photo Tools
- `PHOTOROOM_API_KEY`
- `PHOTOROOM_API_URL` (default provided)

### Automation / Behavior
- `AUTONOMOUS_MODE` (default true)
- `AUTONOMOUS_DRY_RUN` (default false)
- `AUTONOMOUS_CROSSPOST_ENABLED` (default true)
- `AUTO_RELIST_ENABLED` (default true)
- `AUTO_RELIST_MIN_PRICE` (default 20.0)
- `AUTO_RELIST_USER_RULES_JSON` (optional per-user override map)
- `SALE_DETECTION_ENABLED` (default true)
- `SALE_DETECTION_DRY_RUN` (default true)
- `SALE_DETECTION_POLL_MINUTES` (default 15)
- `MAX_CONCURRENT_BULK_TASKS` (default 50)
- `BULK_CHUNK_SIZE` (0 = auto)

### Frontend
- `NEXT_PUBLIC_API_BASE` in frontend environment if backend is not `http://localhost:8000`.

---

## Database & Migrations

Migration SQL files exist under `backend/migrations/`.

Typical process:
1. Ensure Postgres is up.
2. Apply each migration in chronological order.
3. Start API.

Example:

```bash
psql "$DATABASE_URL" -f backend/migrations/20260407_inventory_listing_fields.sql
```

Repeat for each migration file in the folder.

> Note: `Base.metadata.create_all()` is called at app startup, but for production-like consistency you should still apply migration scripts deliberately.

---

## Task Worker & Background Jobs

Celery tasks cover:
- photo enrichment,
- storage-unit batch orchestration,
- autonomous publishing,
- auto pricing,
- stale listing flagging,
- bulk job chunk processing + finalization,
- sales polling,
- offer processing/sending,
- relist monitoring.

If workers are not running, ingestion and automation-heavy features appear “stuck.”

---

## Operational Runbook (Admin)

### Startup order
1. Postgres
2. Redis
3. Backend API
4. Celery worker
5. Frontend

### Daily checks
- `/health` returns `{ "ok": true }`
- worker logs show no repeated retries/failures
- storage directory has available disk space
- bulk job completion ratios are normal

### Suggested production hardening (when you move beyond MVP)
- run API and worker under process supervision (systemd, Docker, Kubernetes)
- add reverse proxy + TLS
- centralize logs
- use managed Postgres backups
- rotate API secrets

---

## User Onboarding Guide (Non-Technical)

This section is for assistants and operators.

### First day checklist
1. Sign in to your hosted PosterPro URL.
2. Confirm **Autonomous Mode** state in the top bar (ON/OFF and dry-run note).
3. Open **Dashboard** and verify counts load.
4. Open **Inventory** and confirm you can filter/search.
5. Open **Listings** to verify cards and editing controls.
6. If you will publish to eBay, ensure account is connected.

### Daily workflow (recommended)
1. Ingest new photos (batch preferred for large lots).
2. Wait for processing (PROCESSED items).
3. Review/edit listings (title, description, condition, price, qty).
4. Apply templates for consistency.
5. Publish to enabled marketplaces.
6. Use inventory bulk actions for operational updates.
7. Check Sales page and complete fee/shipping details.
8. Review analytics and offer opportunities.

---

## UI Walkthrough by Page

### 1) Dashboard (`/`)
Use this as your daily command center.

Contains:
- readiness cards (ready-to-publish, storage batches, auto-published count),
- cluster preview,
- intelligence panel,
- sync panel,
- listing inventory cards,
- overnight batch controls,
- guided onboarding tour.

Why it matters:
- gives a one-screen operational pulse.

### 2) Listings (`/listings`)
Primary listing editing workspace.

What you do here:
- edit listing text and pricing,
- generate AI improvements,
- apply/save templates,
- run photo tools,
- publish per listing.

Why it matters:
- highest quality control point before going live.

### 3) Inventory (`/inventory`)
High-scale operations page.

What you can do:
- table or grid mode,
- filter by stale / multi-quantity,
- search quickly,
- select all matching and launch bulk jobs,
- mark sold, relist, label, refresh, etc.,
- monitor progress with background-safe chunking.

Why it matters:
- lets a small team manage massive SKU counts efficiently.

### 4) Published (`/published`)
Publishing result visibility.

What it shows:
- published listings,
- recently auto-published items.

Why it matters:
- fast validation that automation and manual publish are working.

### 5) Sales (`/sales`)
Sales tracking + bookkeeping controls.

What you can do:
- monitor sales timeline,
- configure sale-detection platforms,
- export CSV,
- patch sale details (fees/shipping/notes).

Why it matters:
- prevents accounting drift and preserves margin visibility.

### 6) Analytics (`/analytics`)
Business performance dashboard.

Includes:
- KPI cards,
- revenue trend chart,
- revenue split by marketplace,
- top seller chart,
- marketplace sales volume,
- report download shortcuts.

Why it matters:
- informs repricing, sourcing, and staffing decisions.

### 7) Send Offers (`/offers`)
Offer automation workstation.

What you can do:
- enable/disable automated offer rule set,
- define discount %, minimum price, exclusions, and message template,
- run send-offers-now manually,
- review recent automated offer history.

Why it matters:
- drives conversions without constant manual outreach.

---

## Feature Deep Dives

### A) Storage-unit ingestion (ZIP/URL/overnight)

Where to find it:
- API endpoints under `/batch/storage-unit*`
- dashboard overnight controls

How it works:
1. Create batch from ZIP or URL list.
2. Batch creates listing rows in `INGESTED`.
3. Immediate mode starts pipeline now; overnight mode queues.
4. Worker processes each listing → enriches metadata.
5. Batch finalizer marks `COMPLETED` or `FAILED`.

Why you want it:
- best path for high-volume intake from auctions, pallet buys, or storage units.

### B) Photo enrichment + autonomous publish

Where:
- worker tasks for `process_photo_batch`, `process_storage_unit_listing`, `autonomous_publish`

How it works:
- enrichment service extracts listing-ready metadata,
- listing status becomes `PROCESSED`,
- optional autonomous path either dry-runs or publishes,
- crosspost targets can be queued based on enabled platforms.

Why you want it:
- compresses listing turnaround time.

### C) Inventory bulk processing

Actions supported:
- `edit`, `delist`, `relist`, `label`, `mark_sold`, `refresh`, `autobump`

How it works:
- create one bulk job,
- worker runs chunk tasks with safety checks,
- progress tracked by processed/total/errors,
- finalizer marks completed state.

Why you want it:
- lets operators manage thousands of listings with confidence.

### D) eBay account connection & publish

Connection:
1. request auth URL
2. authenticate on eBay
3. callback stores tokens in marketplace account table

Publishing:
- publish endpoint validates listing data,
- pushes listing to eBay integration service,
- status endpoint reports publish state and data.

Why you want it:
- direct path to live market with inventory-aware lifecycle tracking.

### E) Sales detection + sync posture

Where:
- `/sales` endpoints + polling task

Capabilities:
- poll and summarize sales,
- configure included marketplaces,
- patch bookkeeping fields,
- export data for accounting,
- support sync workflows to avoid double-selling.

### F) Offer automation

Where:
- `/sales/offers/*`

Capabilities:
- store rule set per user,
- process incoming offers,
- send personalized offers,
- log results in history.

Why you want it:
- increases conversion while maintaining margin guardrails.

---

## API Reference (Practical)

> This is a practical grouping, not an exhaustive OpenAPI dump.

### Health
- `GET /health`

### Listings & templates
- `GET /listings`
- `PATCH /listings/{listing_id}`
- `POST /listings/{listing_id}/generate`
- `POST /listings/{listing_id}/photo-tools`
- `GET /listing-templates`
- `POST /listing-templates`
- `POST /listings/{listing_id}/apply-template`

### Ingestion & batches
- `POST /ingest/photos`
- `POST /batch/storage-unit`
- `POST /batch/storage-unit/from-urls`
- `GET /batch/storage-unit`
- `GET /batch/storage-unit/{batch_id}`
- `POST /batch/storage-unit/{batch_id}/run-overnight`
- `POST /batch/storage-unit/run-overnight`

### Inventory
- `GET /inventory`
- `POST /inventory/bulk-edit`
- `POST /inventory/bulk`
- `GET /bulk-jobs/{job_id}`

### Marketplace & publish
- `GET /marketplaces`
- `POST /marketplaces/{name}/connect`
- `GET /marketplaces/{name}/callback`
- `POST /listings/{listing_id}/publish`
- `GET /listings/{listing_id}/marketplace_status`
- `POST /listings/sync_sold`
- `GET /users/{user_id}/platform-config`
- `PUT /users/{user_id}/platform-config`

### eBay-specific
- `GET /ebay/auth/url`
- `GET /ebay/callback`
- `POST /listings/{listing_id}/publish/ebay`
- `GET /ebay/status/{listing_id}`
- `GET /ebay/offers/dashboard`

### Intelligence & analytics
- `GET /analytics/overview`
- `GET /analytics/dashboard`
- `GET /analytics/listings/{listing_id}`
- `GET /pricing/recommendations/{listing_id}`
- `POST /listings/{listing_id}/optimize`
- `GET /predictions/{listing_id}`
- `GET /alerts`

### Sales, reporting, offers
- `GET /sales/dashboard`
- `PATCH /sales/{sale_id}/details`
- `GET /sales/settings/{user_id}`
- `PUT /sales/settings/{user_id}`
- `GET /sales/reports/sales.csv`
- `GET /sales/reports/inventory.csv`
- `GET /sales/offers/rules/{user_id}`
- `PUT /sales/offers/rules/{user_id}`
- `POST /sales/offers/send/{user_id}`
- `GET /sales/offers/history`

### Config
- `GET /config/autonomous`
- `POST /config/toggle-autonomous`

---

## Recommended Team SOPs

### SOP: New batch intake
- Name storage unit consistently (e.g., `Unit-A-2026-04-07`).
- Upload in one batch.
- Verify batch item count immediately.
- Track until processing complete.

### SOP: Listing QA
- Require title quality pass before publish.
- Ensure condition + price + quantity filled.
- Use templates for category consistency.

### SOP: Oversell prevention
- Use mark-sold action as soon as offline sale occurs.
- Keep sale detection platforms configured correctly.
- Review stale tab daily.

### SOP: Reporting cadence
- End-of-day export sales CSV.
- Weekly review analytics trends.
- Monthly tune offer discount rules.

---

## Troubleshooting

### “Uploads succeed but nothing processes”
Likely cause:
- Celery worker not running.

Check:
- worker terminal logs
- redis connectivity

### “Publish fails with authentication errors”
Likely cause:
- expired/missing eBay tokens or incorrect redirect URI.

Check:
- eBay app credentials
- callback URI match
- reconnect marketplace account

### “Bulk action seems frozen”
Likely cause:
- job is queued/running in background.

Check:
- `/bulk-jobs/{job_id}` progress
- worker throughput and retries

### “Images not loading in UI”
Likely cause:
- storage path or media mount mismatch.

Check:
- `STORAGE_ROOT`
- API `/media` mount
- `toPublicImageUrl` output path

### “Background removal fails”
Likely cause:
- missing/invalid Photoroom API key or network issue.

Check:
- `PHOTOROOM_API_KEY`
- `PHOTOROOM_API_URL`

---

## Security, Privacy, and Safety Notes

- Protect `.env` values; never commit secrets.
- Run behind HTTPS in production.
- Use separate credentials per environment.
- Restrict DB/Redis network exposure.
- Treat uploaded images and listing data as potentially sensitive business data.
- Review autonomous and dry-run toggles before enabling full automation for staff.

---

## Testing

Backend tests include coverage for:
- eBay publishing,
- connector behavior,
- marketplace APIs,
- offer service,
- reseller intelligence,
- inventory bulk paths,
- storage unit pipeline flows.

Run:

```bash
cd backend
PYTHONPATH=. DATABASE_URL=sqlite:///./test.db pytest tests -q
```

Frontend lint/build scripts available in `frontend/package.json`.

---

## Roadmap-Ready Extension Points

PosterPro already contains structural support for additional marketplace expansion and intelligence workflows:
- connector registry pattern for marketplaces,
- marketplace listing tracking model,
- prediction and optimizer services,
- AB test variant model,
- chunked async processing primitives.

As you evolve this app, you can add:
- richer auth/user management,
- webhook ingestion,
- warehouse/bin location modules,
- role-based permissions,
- enhanced audit logs,
- more deterministic migration tooling.

---

## Glossary

- **Autonomous Mode**: automatic post-enrichment publish behavior.
- **Dry Run**: simulate autonomous behavior without live publish side effects.
- **Stale Listing**: listing with old/absent refresh timestamp.
- **Bulk Job**: asynchronous large-scale inventory action.
- **Storage Batch**: grouped ingest unit for high-volume photo uploads.
- **Crosspost**: publish one listing to multiple marketplaces.

---

## Final Notes

If you are onboarding a new assistant, have them read in this order:
1. [User Onboarding Guide](#user-onboarding-guide-non-technical)
2. [UI Walkthrough by Page](#ui-walkthrough-by-page)
3. [Feature Deep Dives](#feature-deep-dives)
4. [Recommended Team SOPs](#recommended-team-sops)

If you are setting up infrastructure, read in this order:
1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start-local-development)
3. [Configuration Reference](#configuration-reference-env)
4. [Operational Runbook](#operational-runbook-admin)
5. [Troubleshooting](#troubleshooting)

PosterPro is built to help you move from chaotic reseller operations to repeatable systems.
