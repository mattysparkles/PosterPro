from pathlib import Path

import pytest

from app.core.database import engine
from app.core.database_test_safety import validate_disposable_test_database_url


def test_pytest_global_engine_is_a_disposable_database():
    assert engine.dialect.name == "sqlite"
    path = Path(engine.url.database or "").resolve()
    assert path.parent == Path("/tmp").resolve()
    assert path.name.startswith("posterpro-test-")
    assert path.suffix == ".db"


def test_destructive_test_database_rejects_production_identity():
    production_url = "postgresql://operator:secret@db.example:5432/posterpro"
    with pytest.raises(RuntimeError, match="matches production"):
        validate_disposable_test_database_url(
            production_url,
            "postgresql://operator:another-secret@db.example:5432/posterpro",
        )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://tester:secret@localhost:5432/posterpro_test",
        "sqlite:///./posterpro_test.db",
        "sqlite:////tmp/unmarked-test.db",
    ],
)
def test_destructive_test_database_rejects_ambiguous_targets(url):
    with pytest.raises(RuntimeError):
        validate_disposable_test_database_url(url, "postgresql://db.example/posterpro")


def test_destructive_test_database_accepts_marked_temporary_sqlite():
    url = "sqlite:////tmp/posterpro-test-isolation-regression.db"
    assert validate_disposable_test_database_url(url, "postgresql://db.example/posterpro") == url
