"""Dedicated Celery Beat app for durable scheduled job recovery.

The main Celery app contains schedules for several unrelated queues. This
entry point runs only watchdogs whose task queues have active consumers. The
category watchdog re-enqueues persisted review-only work onto its dedicated
posterpro-category queue; it never publishes listings.
"""

from app.workers.celery_app import celery_app

celery_app.conf.beat_schedule = {
    "recover-publisher-delivery-every-30-seconds": {
        "task": "dispatch_queued_crosspost_jobs",
        "schedule": 30.0,
        "kwargs": {"limit": 25},
    },
    "resume-marketplace-category-suggestion-jobs": {
        "task": "resume_marketplace_category_suggestion_jobs",
        "schedule": 60.0,
    },
}
