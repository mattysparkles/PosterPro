from celery.schedules import crontab
from celery import Celery

from app.core.config import settings

celery_app = Celery("posterpro", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_track_started=True,
    # Job/application state is durable in PosterPro's database. Celery result
    # keys are ephemeral transport metadata; retaining them for the default
    # day caused the small Redis field host to grow until it was OOM-killed.
    # Keep enough history for the Jobs UI while bounding broker memory.
    result_expires=3600,
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
        "reconcile_retroactive_intake_boundaries": {"queue": "posterpro", "routing_key": "posterpro", "priority": 9},
        "repair_recent_vine_images": {"queue": "posterpro", "routing_key": "posterpro", "priority": 7},
        "resume_vine_processing": {"queue": "posterpro", "routing_key": "posterpro", "priority": 1},
        # Live publication is the revenue-critical lane. Celery priority 0 is
        # highest, so these tasks cannot sit behind recurring backlog work.
        "publish_listing_to_marketplace": {"queue": "posterpro", "routing_key": "posterpro", "priority": 0},
        "process_marketplace_crosspost_job": {"queue": "posterpro", "routing_key": "posterpro", "priority": 0},
        "resume_product_research_backlog": {"queue": "posterpro", "routing_key": "posterpro", "priority": 8},
        "resume_image_identification_backlog": {"queue": "posterpro", "routing_key": "posterpro", "priority": 8},
        # Inventory enrichment has a dedicated consumer.  Marketplace sync
        # and publication traffic must not starve the autonomous listing
        # pipeline or make its progress depend on an operator session.
        "resume_incomplete_listings": {"queue": "posterpro-inventory", "routing_key": "posterpro-inventory", "priority": 8},
        "reevaluate_legacy_recovery_attention": {"queue": "posterpro-inventory", "routing_key": "posterpro-inventory", "priority": 7},
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
        "purge-expired-sold-media-hourly": {
            "task": "purge_expired_sold_media",
            "schedule": crontab(minute=10),
        },
        # Keep the local Published/Archived projection aligned with the
        # account's actual active eBay set.  The sync is read-only remotely and
        # archives only identities absent from a complete successful snapshot.
        "sync-ebay-active-listings-every-15m": {
            "task": "sync_ebay_active_listings",
            "schedule": crontab(minute="*/15"),
        },
        "flag-stale-listings-daily": {
            "task": "flag_stale_listings",
            "schedule": crontab(minute=5, hour=0),
        },
        "recover-intake-reconciliation-jobs-every-minute": {
            "task": "process_intake_reconciliation_jobs",
            "schedule": crontab(minute="*"),
        },
        "dispatch-durable-crosspost-jobs-every-minute": {
            "task": "dispatch_queued_crosspost_jobs",
            "schedule": crontab(minute="*"),
        },
        "recover-hosted-marketplace-polls-every-minute": {
            "task": "dispatch_pending_hosted_bridge_polls",
            "schedule": crontab(minute="*"),
            "args": [50],
        },
        "repair-deterministic-facebook-publish-blocks-every-minute": {
            "task": "repair_blocked_facebook_publish_targets",
            "schedule": crontab(minute="*"),
            "args": [25],
        },
        "queue-due-intake-syncs-every-5m": {
            "task": "queue_due_intake_syncs",
            "schedule": crontab(minute="*/5"),
        },
        # Local timeline repairs can be authoritative even when a rendered
        # slate could not be inserted upstream.  Reconcile them through the
        # durable intake service, then hand resulting drafts to inventory.
        "reconcile-retroactive-intake-boundaries-every-minute": {
            "task": "reconcile_retroactive_intake_boundaries",
            "schedule": crontab(minute="*"),
        },
        "repair-recent-vine-images-every-5m": {
            "task": "repair_recent_vine_images",
            "schedule": crontab(minute="*/5"),
        },
        "repair-vine-listing-quality-every-15m": {
            "task": "repair_vine_listing_quality",
            "schedule": crontab(minute="*/15"),
        },
        "resume-vine-processing-every-minute": {
            "task": "resume_vine_processing",
            "schedule": crontab(minute="*"),
            "args": [None, 25, False],
        },
        "audit-published-vine-quality-hourly": {
            "task": "audit_published_vine_quality",
            "schedule": crontab(minute=25),
        },
        "resume-waiting-ai-work-every-minute": {
            "task": "resume_waiting_ai_work",
            "schedule": crontab(minute="*"),
        },
        # Keep queued historical/Vine enrichment resumable without requiring
        # the operator to revisit the UI.  The worker task is deliberately
        # bounded; each listing is claimed with its own lease and failures are
        # isolated, so a large catalog cannot monopolize the queue.
        "resume-listing-backlog-every-5m": {
            "task": "resume_incomplete_listings",
            "schedule": crontab(minute="*/5"),
            "args": [None, 5, False],
        },
        # Fresh, clean slate intake is the revenue-priority lane. It gets the
        # first bounded claim each minute; legacy recovery remains durable but
        # cannot consume every singleton-worker slot ahead of new inventory.
        "resume-google-intake-every-minute": {
            "task": "resume_incomplete_listings",
            "schedule": crontab(minute="*"),
            "args": [None, 5, False, ["intake_head_slate"]],
        },
        # Adds real external asking-comparable provenance to a small number of
        # unreviewed Google timeline drafts. It never changes a listing price
        # or promotes a draft; those decisions remain behind quality review.
        "research-google-intake-pricing-every-15m": {
            "task": "research_google_intake_pricing",
            "schedule": crontab(minute="*/15"),
            "args": [5, False],
        },
        # Recovered Google-photo inventory is a separate durable intake lane.
        # It must not depend on a browser session or on a human invoking the
        # generic backfill endpoint.  The processing service atomically leases
        # one listing at a time and applies recovery-group ordering.
        "resume-google-recovery-processing-every-5m": {
            "task": "resume_incomplete_listings",
            "schedule": crontab(minute="*/5"),
            "args": [None, 1, False, ["media_inventory_recovery"]],
        },
        # Capability upgrades must be able to reopen historical Attention
        # rows without a Codex/manual database loop.  The task marks true
        # human blockers with a versioned verdict and processes at most one
        # newly machine-resolvable recovery listing per tick.
        "reevaluate-legacy-recovery-attention-every-5m": {
            "task": "reevaluate_legacy_recovery_attention",
            "schedule": crontab(minute="*/5"),
            "args": [None, 1, False],
        },
        "repair-google-taxonomy-every-5m": {
            "task": "repair_google_listing_categories",
            "schedule": crontab(minute="*/5"),
            "args": [None, 10],
        },
        "resume-image-identification-every-10m": {
            "task": "resume_image_identification_backlog",
            "schedule": crontab(minute="*/10"),
            "args": [None, 3, False],
        },
        "resume-product-research-every-15m": {
            "task": "resume_product_research_backlog",
            "schedule": crontab(minute="*/15"),
            "args": [None, 4, False],
        },
        # Historical cleanup jobs stay available for manual use but are not
        # self-requeued by default on the 2 GB field host.
    },
)
