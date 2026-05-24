import asyncio
import tempfile
import functools

import anyio.to_thread
import httpx
import pytest
import fastapi.routing
import starlette.concurrency
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main as main_module
from app.core import database as database_module
from app.core.database import Base
from app.main import app


async def _anyio_run_sync_compat(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):  # noqa: ARG001
    return await asyncio.to_thread(func, *args)


# Patch AnyIO thread offload for this test environment.
# In this sandbox, AnyIO's default worker-thread path can hang, which blocks
# FastAPI's sync route/dependency execution. Swapping to asyncio.to_thread keeps
# the runtime behavior consistent while unblocking route-level tests.
anyio.to_thread.run_sync = _anyio_run_sync_compat  # type: ignore[assignment]


def _reset_database(test_engine) -> None:
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)


@pytest.fixture
def db_session():
    _reset_database(database_module.engine)
    db = database_module.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def async_client():
    original_fastapi_run_in_threadpool = fastapi.routing.run_in_threadpool
    original_starlette_run_in_threadpool = starlette.concurrency.run_in_threadpool

    async def _run_in_threadpool_compat(func, *args, **kwargs):
        if kwargs:
            func = functools.partial(func, **kwargs)
        return await asyncio.to_thread(func, *args)

    fastapi.routing.run_in_threadpool = _run_in_threadpool_compat  # type: ignore[assignment]
    starlette.concurrency.run_in_threadpool = _run_in_threadpool_compat  # type: ignore[assignment]

    original_engine = database_module.engine
    original_session_local = database_module.SessionLocal
    try:
        tmp = tempfile.NamedTemporaryFile(prefix="posterpro-test-", suffix=".db", dir="/tmp", delete=False)
        tmp.close()

        test_engine = create_engine(
            f"sqlite:///{tmp.name}",
            pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 3},
        )
        database_module.engine = test_engine
        database_module.SessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
        main_module.engine = test_engine

        await app.router.startup()
        _reset_database(test_engine)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
        await app.router.shutdown()
    finally:
        main_module.engine = original_engine
        database_module.engine = original_engine
        database_module.SessionLocal = original_session_local
        fastapi.routing.run_in_threadpool = original_fastapi_run_in_threadpool  # type: ignore[assignment]
        starlette.concurrency.run_in_threadpool = original_starlette_run_in_threadpool  # type: ignore[assignment]
