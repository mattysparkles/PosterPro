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
        },
        "monitor-and-relist-nightly": {
            "task": "monitor_and_relist",
            "schedule": crontab(minute=15, hour=1),
        },
        "process-ebay-incoming-offers-every-15m": {
            "task": "process_incoming_offers",
            "schedule": crontab(minute="*/15"),
        },
        "run-overnight-storage-batches": {
            "task": "process_overnight_storage_batches",
            "schedule": crontab(minute=30, hour=2),
        },
        "poll-for-sales-every-15m": {
            "task": "poll_for_sales",
            "schedule": crontab(minute="*/15"),
        },
        "flag-stale-listings-daily": {
            "task": "flag_stale_listings",
            "schedule": crontab(minute=5, hour=0),
        },
    },
)
