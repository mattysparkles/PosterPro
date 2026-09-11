from celery.schedules import crontab
from celery import Celery

from app.core.config import settings

celery_app = Celery("posterpro", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    accept_content=["json"],
    task_default_queue="posterpro",
    task_default_exchange="posterpro",
    task_default_routing_key="posterpro",
    task_queue_max_priority=10,
    task_default_priority=5,
    task_routes={
        "drain_intake_provider_media": {"queue": "posterpro", "routing_key": "posterpro", "priority": 9},
        "queue_due_intake_syncs": {"queue": "posterpro", "routing_key": "posterpro", "priority": 9},
        "process_intake_reconciliation_jobs": {"queue": "posterpro", "routing_key": "posterpro", "priority": 9},
        "repair_recent_vine_images": {"queue": "posterpro", "routing_key": "posterpro", "priority": 7},
        "resume_product_research_backlog": {"queue": "posterpro", "routing_key": "posterpro", "priority": 1},
        "resume_image_identification_backlog": {"queue": "posterpro", "routing_key": "posterpro", "priority": 1},
        "resume_incomplete_listings": {"queue": "posterpro", "routing_key": "posterpro", "priority": 1},
        "*": {"queue": "posterpro", "routing_key": "posterpro"},
    },
    imports=("app.workers.tasks",),
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
        "send-personalized-offers-every-20m": {
            "task": "send_personalized_offers",
            "schedule": crontab(minute="*/20"),
        },
        "run-overnight-storage-batches": {
            "task": "process_overnight_storage_batches",
            "schedule": crontab(minute=30, hour=2),
        },
        "poll-for-sales-every-15m": {
            "task": "poll_for_sales",
            "schedule": crontab(minute="*/15"),
        },
        "sync-ebay-active-listings-twice-daily": {
            "task": "sync_ebay_active_listings",
            "schedule": crontab(minute=0, hour="6,18"),
        },
        "flag-stale-listings-daily": {
            "task": "flag_stale_listings",
            "schedule": crontab(minute=5, hour=0),
        },
        "recover-intake-reconciliation-jobs-every-minute": {
            "task": "process_intake_reconciliation_jobs",
            "schedule": crontab(minute="*"),
        },
        "queue-due-intake-syncs-every-5m": {
            "task": "queue_due_intake_syncs",
            "schedule": crontab(minute="*/5"),
        },
        "repair-recent-vine-images-every-5m": {
            "task": "repair_recent_vine_images",
            "schedule": crontab(minute="*/5"),
        },
        "repair-vine-listing-quality-every-15m": {
            "task": "repair_vine_listing_quality",
            "schedule": crontab(minute="*/15"),
        },
        "resume-waiting-ai-work-every-minute": {
            "task": "resume_waiting_ai_work",
            "schedule": crontab(minute="*"),
        },
        # Historical cleanup jobs stay available for manual use but are not
        # self-requeued by default on the 2 GB field host.
    },
)
