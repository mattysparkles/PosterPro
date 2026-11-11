from app.workers.celery_app import celery_app


def test_commercial_publish_tasks_use_the_scoped_approval_queue():
    routes = celery_app.conf.task_routes
    for task_name in (
        "publish_listing_to_marketplace",
        "process_marketplace_crosspost_job",
    ):
        route = routes[task_name]
        assert route["queue"] == "posterpro-publish"
        assert route["routing_key"] == "posterpro-publish"
        assert route["priority"] == 0


def test_generic_work_stays_off_the_scoped_publish_queue():
    routes = celery_app.conf.task_routes
    assert routes["resume_incomplete_listings"]["queue"] == "posterpro-inventory"
    assert routes["*"]["queue"] == "posterpro"
