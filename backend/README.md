# Backend (FastAPI + Celery)

## Run API
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Run worker
```bash
celery -A app.workers.celery_app.celery_app worker --loglevel=info
```

## Run tests
```bash
PYTHONPATH=. DATABASE_URL=sqlite:///./test.db pytest tests -q
```

## eBay publishing flow
1. `GET /ebay/auth/url` to build OAuth link.
2. Seller authenticates and returns to `GET /ebay/callback`.
3. `POST /listings/{id}/publish/ebay` runs:
   - create inventory location
   - create/replace inventory item
   - create offer
   - publish offer
4. Listing table updated with `ebay_listing_id`, `ebay_publish_status`, `marketplace_data`.

## Photo ingestion flow
1. `POST /ingest/photos` with multipart files (`photos`) and optional `user_id`, `storage_unit_name`.
2. API stores files in `storage/uploads` and creates listings in `INGESTED`.
3. Worker task `process_photo_batch` runs OpenAI vision prompts (`extract_poster_title`, `extract_description`, `detect_category`, `extract_keywords`) and updates each listing to `PROCESSED` or `FAILED`.

### Example curl
```bash
curl -X POST "http://localhost:8000/ingest/photos" \
  -F "user_id=1" \
  -F "storage_unit_name=Shelf B2" \
  -F "photos=@/tmp/poster-1.jpg" \
  -F "photos=@/tmp/poster-2.jpg"
```
