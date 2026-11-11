"""Fail-closed database selection helpers for destructive test fixtures.

This module intentionally has no dependency on application settings so it can
run before the API, workers, or SQLAlchemy engine are imported by pytest.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url


def _database_identity(value: str) -> tuple[str, str | None, int | None, str | None, str | None]:
    url = make_url(value)
    return (url.drivername.split("+", 1)[0], url.host, url.port, url.database, url.username)


def validate_disposable_test_database_url(test_url: str, production_url: str | None) -> str:
    """Return a safe test URL or reject it before destructive fixture work."""
    try:
        identity = _database_identity(test_url)
        production_identity = _database_identity(production_url) if production_url else None
    except Exception:  # noqa: BLE001
        raise RuntimeError("Test database identity is missing or invalid; refusing destructive test setup.") from None

    if production_identity and identity == production_identity:
        raise RuntimeError("Test database matches production; refusing destructive test setup.")

    url = make_url(test_url)
    if url.drivername.split("+", 1)[0] != "sqlite":
        raise RuntimeError("Destructive tests require a disposable SQLite database under /tmp.")

    database_path = url.database or ""
    path = Path(database_path)
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(Path("/tmp").resolve())
    except (OSError, ValueError) as exc:
        raise RuntimeError("Destructive tests require a disposable database path under /tmp.") from exc

    if not resolved.name.startswith("posterpro-test-") or resolved.suffix != ".db":
        raise RuntimeError("Destructive tests require a uniquely designated posterpro-test-*.db file under /tmp.")

    return test_url


def initialize_pytest_database() -> str:
    """Set DATABASE_URL to an isolated disposable DB before importing app code.

    If the caller supplied DATABASE_URL, it must exactly match an explicitly
    designated POSTERPRO_TEST_DATABASE_URL. This prevents production or an
    ambiguous external DB from becoming the import-time global engine.
    """
    backend_dir = Path(__file__).resolve().parents[2]
    production_url = None
    env_file = backend_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text(errors="ignore").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "DATABASE_URL":
                production_url = value.strip().strip("\"'")
                break

    configured_url = os.environ.get("DATABASE_URL")
    test_url = os.environ.get("POSTERPRO_TEST_DATABASE_URL")
    generated_path: str | None = None

    if not test_url:
        if configured_url:
            raise RuntimeError(
                "DATABASE_URL is set but POSTERPRO_TEST_DATABASE_URL is not; "
                "refusing to run tests against an ambiguous database."
            )
        descriptor, generated_path = tempfile.mkstemp(prefix="posterpro-test-", suffix=".db", dir="/tmp")
        os.close(descriptor)
        test_url = f"sqlite:////{generated_path.lstrip('/')}"

    validate_disposable_test_database_url(test_url, production_url)
    if configured_url:
        try:
            configured_identity = _database_identity(configured_url)
        except Exception:  # noqa: BLE001
            raise RuntimeError("DATABASE_URL is invalid; refusing destructive test setup.") from None
        if configured_identity != _database_identity(test_url):
            raise RuntimeError(
                "DATABASE_URL does not match POSTERPRO_TEST_DATABASE_URL; refusing destructive test setup."
            )

    os.environ["POSTERPRO_TEST_DATABASE_URL"] = test_url
    os.environ["DATABASE_URL"] = test_url
    if generated_path:
        os.environ["POSTERPRO_TEST_DATABASE_PATH"] = generated_path
    return generated_path or ""


def cleanup_pytest_database(path: str) -> None:
    """Remove only the uniquely named disposable database created by this harness."""
    if not path:
        return
    resolved = Path(path).resolve(strict=False)
    if resolved.parent != Path("/tmp").resolve() or not resolved.name.startswith("posterpro-test-") or resolved.suffix != ".db":
        raise RuntimeError("Refusing to clean a path that is not a designated disposable test database.")
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{resolved}{suffix}").unlink(missing_ok=True)
