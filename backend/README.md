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
