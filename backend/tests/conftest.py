from __future__ import annotations

import os
import tempfile
from pathlib import Path


_TEST_DIR = Path(tempfile.mkdtemp(prefix="posterpro-vine-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR / 'posterpro_test.db'}"
os.environ["STORAGE_ROOT"] = str(_TEST_DIR / "storage")

import pytest

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    settings.amazon_vine_import_enabled = False
    settings.amazon_vine_import_premium_only = False
    settings.amazon_media_lookup_enabled = False
    settings.amazon_media_page_fallback_enabled = False
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
