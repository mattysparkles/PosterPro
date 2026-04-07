# PosterPro MVP - Reseller Marketplace Cross-Posting Tool

Production-minded MVP scaffolding with:
- FastAPI + PostgreSQL + SQLAlchemy
- Redis + Celery workers for async ingestion/clustering
- Next.js dashboard for cluster preview + listing management
- eBay Inventory API publish pipeline with OAuth account linking

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

### Photo ingestion quick test

```bash
curl -X POST "http://localhost:8000/ingest/photos" \
  -F "user_id=1" \
  -F "storage_unit_name=Unit A3" \
  -F "photos=@/absolute/path/poster1.jpg" \
  -F "photos=@/absolute/path/poster2.jpg"
```

The endpoint stores uploads under `./storage/uploads`, creates `listings` in `INGESTED` status, and enqueues Celery task `process_photo_batch` to extract title/description/category/keywords and update each listing to `PROCESSED` (or `FAILED` on errors).

## Autonomous Reseller Mode (Storage Unit Overnight Flow)

### End-to-end: upload 200 photos and let it list while you sleep

1. Upload one storage-unit batch (zip or URL list):
   - `POST /batch/storage-unit` with `zip_file=@unit-photos.zip`, optional `storage_unit_name`, and optional `overnight_mode=true`.
   - or `POST /batch/storage-unit/from-urls` with JSON `image_urls` for mobile upload workflows.
2. API creates one `storage_unit_batches` row, creates all child listings in `INGESTED`, and tracks progress (`processed_items/total_items`).
3. If `overnight_mode=false`, the autonomous Prompt 1–3 pipeline starts immediately through a Celery chord/group:
   - per-photo enrichment
   - auto-pricing / autonomous publish trigger
   - final batch completion status
4. If `overnight_mode=true`, batch is queued (`QUEUED`) and can be started by:
   - Celery Beat nightly schedule (`process_overnight_storage_batches`), or
   - dashboard **Run Overnight Batch** button / manual API call.
5. Dashboard polls batch progress every few seconds to show live `processed/total` progress until `COMPLETED` or `FAILED`.

That gives you a practical overnight pipeline: **Upload 200 photos from a storage unit and wake up to processed/published inventory.**

## Example API request/response logging

```text
INFO publish start listing_id=42 user_id=1 status=POSTING
INFO createOrReplaceInventoryItem sku=posterpro-1-42 status=204
INFO createOffer offerId=6241573010
INFO publishOffer listingId=387612300245
INFO publish success listing_id=42 ebay_listing_id=387612300245 status=POSTED
```

Failure example:
```text
ERROR publish failed listing_id=42 status=FAILED reason="eBay API request failed (429) ..."
```

## Notes
- `app/services/ebay_service.py` uses retry/backoff for `429/5xx` responses.
- Taxonomy and policy helper functions are included for category and item specifics enrichment.
