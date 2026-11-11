"""Dedicated Celery Beat app for durable publisher delivery recovery.

The main Celery app contains schedules for several unrelated queues. This
entry point intentionally runs only the publisher watchdog so it cannot fill
an unconsumed default queue when the publisher worker is deployed alone.
"""

from app.workers.celery_app import celery_app

celery_app.conf.beat_schedule = {
    "recover-publisher-delivery-every-30-seconds": {
        "task": "dispatch_queued_crosspost_jobs",
        "schedule": 30.0,
        "kwargs": {"limit": 25},
    },
}
