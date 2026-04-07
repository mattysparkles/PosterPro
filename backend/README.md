# Backend (FastAPI + Celery)

## Run API
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Run worker
```bash
celery -A app.workers.celery_app.celery_app worker --loglevel=info
```
