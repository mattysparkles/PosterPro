from celery.schedules import crontab
from celery import Celery

from app.core.config import settings

celery_app = Celery("posterpro", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    accept_content=["json"],
    beat_schedule={
        "adjust-active-listing-prices-every-24h": {
            "task": "adjust_active_listing_prices",
            "schedule": crontab(minute=0, hour=0),
        }
    },
)
