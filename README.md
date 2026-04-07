# PosterPro MVP - Reseller Marketplace Cross-Posting Tool

PosterPro is now tuned for fast, phone-first listing workflows: snap photos, auto-ingest, review inventory, bulk-edit quantities, and keep cross-posted inventory in sync when items sell.

Production-minded MVP scaffolding with:
- FastAPI + PostgreSQL + SQLAlchemy
- Redis + Celery workers for async ingestion/clustering
- Next.js dashboard for cluster preview + listing management
- eBay Inventory API publish pipeline with OAuth account linking
- Mobile-responsive inventory command center with quick actions

## Project Structure

```
/backend
  /app
    /api
      ebay.py
      routes.py
    /core
    /models
    /prompts
    /services
      ebay_service.py
    /workers
  /migrations
    20260407_add_ebay_listing_fields.sql
  /tests
    test_ebay_service.py
    test_ebay_publish_e2e.py
  requirements.txt
/frontend
  /components
    PublishedListings.js
  /hooks
    useEbayAuth.js
    useEbayPublish.js
  /lib
  /pages
  /styles
```

## Quick Start

### Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Worker
```bash
cd backend
celery -A app.workers.celery_app.celery_app worker --loglevel=info
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## eBay setup (Sandbox)

1. Create an app in the [eBay Developer Portal](https://developer.ebay.com/).
2. Add redirect URI from your environment (example: `http://localhost:8000/ebay/callback`).
3. Configure environment variables in `backend/.env`:
   - `EBAY_CLIENT_ID`
   - `EBAY_CLIENT_SECRET`
   - `EBAY_REDIRECT_URI`
   - `ENVIRONMENT=development` (uses sandbox endpoints)
4. Run SQL migration file:
   - `psql < backend/migrations/20260407_add_ebay_listing_fields.sql`
5. Ensure account has business policies configured in eBay sandbox (payment, fulfillment/shipping, return).

### Required OAuth scopes
- `https://api.ebay.com/oauth/api_scope`
- `https://api.ebay.com/oauth/api_scope/sell.inventory`
- `https://api.ebay.com/oauth/api_scope/sell.account`
- `https://api.ebay.com/oauth/api_scope/sell.fulfillment`

## New API Endpoints
- `GET /ebay/auth/url?user_id=1&redirect_uri=...`
- `GET /ebay/callback?code=...&state=...`
- `POST /listings/{id}/publish/ebay`
- `GET /ebay/status/{id}`
- `POST /ingest/photos` (multipart photo ingestion + autonomous AI enrichment)
- `POST /batch/storage-unit` (zip upload OR image URL batch ingestion)
- `POST /batch/storage-unit/from-urls` (JSON URL list for mobile clients)
- `GET /batch/storage-unit` and `GET /batch/storage-unit/{batch_id}` (batch progress)
- `POST /batch/storage-unit/{batch_id}/run-overnight` (manual run now)
- `POST /batch/storage-unit/run-overnight` (queue all overnight batches now)
- `POST /inventory/bulk` supports `edit|delist|relist|label|mark_sold|refresh|autobump`

## What’s New in the Final Unification Pass

- **Phone-first UX polish**
  - Mobile bottom navigation for quick switching between key views.
  - Inventory controls stack cleanly on small screens.
  - Keyboard shortcut hints for desktop power users.
  - Loading skeletons and friendlier empty states.
- **Manual "Mark as Sold" override**
  - Available as a bulk action and as a one-tap quick action.
  - Sets quantity to `0`, zeroes channel quantities, and stamps `sold_at` (plus optional `sale_price`).
- **Integrated ingestion → inventory flow**
  - Autonomous photo/storage-unit ingestion now stamps freshness metadata and sale-detection readiness.
  - Processed items initialize quantity-aware platform inventory state.
- **Inventory safety + multi-quantity support**
  - Anti-oversell validation remains in place while chunked bulk jobs provide transparent progress.

## Screenshots (Descriptions)

When you run the app locally (`npm run dev`), capture these views:

1. **Inventory Command Center (Mobile):**
   - Bottom nav visible.
   - Bulk action tray wraps naturally.
   - "Mark as Sold" action shown with bulk controls.
2. **Inventory Loading / Empty States:**
   - Skeleton cards/rows while inventory loads.
   - Empty state card with guidance to reset filters or ingest a new unit.
3. **Inventory Grid Quick Actions:**
   - Card-level photo edit and one-tap Sold actions.

## How a Non-Technical User Can List a Storage Unit in Minutes

1. Open PosterPro on your phone or laptop.
2. Upload photos using ZIP (`/batch/storage-unit`) or URL list (`/batch/storage-unit/from-urls`).
3. Let PosterPro auto-process titles, descriptions, categories, tags, and pricing signals.
4. Open Inventory Command Center to review, filter, and bulk-edit quantities/labels.
5. Use **Mark as Sold** for any manual/offline sale so inventory stays accurate.
6. Publish and monitor while sale detection handles quantity sync and delist logic.

## Autonomous Reseller Mode (Storage Unit Overnight Flow)

### End-to-end: upload 200 photos and let it list while you sleep

1. Upload one storage-unit batch (zip or URL list).
2. API creates one `storage_unit_batches` row and child listings in `INGESTED`.
3. If `overnight_mode=false`, autonomous processing starts immediately.
4. If `overnight_mode=true`, batch is queued and can run via scheduler or manual trigger.
5. Dashboard polls progress until `COMPLETED` or `FAILED`.

That gives you a practical overnight pipeline: **Upload 200 photos from a storage unit and wake up to processed/published inventory.**

## Notes
- `app/services/ebay_service.py` uses retry/backoff for `429/5xx` responses.
- Taxonomy and policy helper functions are included for category and item specifics enrichment.
