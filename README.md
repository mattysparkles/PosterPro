# PosterPro MVP - Reseller Marketplace Cross-Posting Tool

Production-minded MVP scaffolding with:
- FastAPI + PostgreSQL + SQLAlchemy
- Redis + Celery workers for async ingestion/clustering
- Next.js dashboard for cluster preview + listing management
- eBay-first publish pipeline with pluggable marketplace adapters

## Project Structure

```
/backend
  /app
    /api
    /core
    /models
    /prompts
    /services
    /workers
  requirements.txt
/frontend
  /components
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

## API Endpoints
- `POST /import/google-photos`
- `GET /clusters`
- `GET /listings`
- `POST /listings/{id}/generate`
- `POST /listings/{id}/publish/ebay`

## Notes
- Google Photos extraction and embeddings are scaffolded for MVP speed.
- Replace `fake_clip_embedding` with CLIP inference service in production.
- eBay service currently mocked for safe local development.
