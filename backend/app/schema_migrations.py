"""Minimal checksum-tracked migration ledger for explicit, reviewed SQL migrations.

Historical SQL files are deliberately not auto-discovered or replayed. Only
MIGRATION_ALLOWLIST is executable until each migration has been reviewed and
added here.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ALLOWLIST = (
    "20261109_shelf_census.sql",
    "20261110_amazon_media_retry_state.sql",
)
LEDGER = "posterpro_schema_migrations"
BASELINE_VERSION = "baseline-existing-schema"


def _connect_url(value: str | URL) -> str | URL:
    url = make_url(value) if isinstance(value, str) else value
    if url.drivername.startswith("postgresql"):
        return url.set(drivername="postgresql+psycopg2")
    return url


def schema_fingerprint(connection) -> str:
    rows = connection.execute(text("""
        SELECT table_name, column_name, ordinal_position, data_type,
               udt_name, is_nullable, COALESCE(column_default, '')
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name <> :ledger
        ORDER BY table_name, ordinal_position
    """), {"ledger": LEDGER}).all()
    canonical = "\n".join("|".join(str(part or "") for part in row) for row in rows)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _ensure_ledger(connection) -> None:
    connection.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {LEDGER} (
            version VARCHAR(160) PRIMARY KEY,
            checksum VARCHAR(64) NOT NULL,
            description TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))


def baseline_existing_schema(database_url: str | URL, baseline_id: str = BASELINE_VERSION) -> dict:
    """Record the actual current schema without replaying historical scripts."""
    engine = create_engine(_connect_url(database_url))
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('posterpro-schema-migrations'))"))
            before = schema_fingerprint(connection)
            _ensure_ledger(connection)
            existing = connection.execute(text(f"SELECT checksum FROM {LEDGER} WHERE version=:version"), {"version": baseline_id}).scalar_one_or_none()
            if existing and existing != before:
                raise RuntimeError("Existing baseline checksum differs from the live schema; refusing to rewrite the baseline.")
            connection.execute(text(f"""
                INSERT INTO {LEDGER}(version, checksum, description)
                VALUES (:version, :checksum, 'Operator-reviewed baseline of the pre-ledger public schema')
                ON CONFLICT (version) DO NOTHING
            """), {"version": baseline_id, "checksum": before})
            return {"baseline": baseline_id, "schema_fingerprint": before}
    finally:
        engine.dispose()


def apply_managed_migrations(database_url: str | URL, *, migration_dir: Path | None = None) -> list[dict]:
    """Apply only allowlisted additive scripts, each with checksum and ledger row."""
    directory = migration_dir or ROOT / "migrations"
    engine = create_engine(_connect_url(database_url))
    applied: list[dict] = []
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(hashtext('posterpro-schema-migrations'))"))
            connection.commit()
            try:
                with connection.begin():
                    if not connection.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{LEDGER}"}).scalar_one():
                        raise RuntimeError("Schema migration ledger is absent; establish and review the existing-schema baseline first.")
                for filename in MIGRATION_ALLOWLIST:
                    path = directory / filename
                    if not path.is_file():
                        continue
                    sql = path.read_text(encoding="utf-8")
                    checksum = hashlib.sha256(sql.encode()).hexdigest()
                    with connection.begin():
                        existing = connection.execute(text(f"SELECT checksum FROM {LEDGER} WHERE version=:version"), {"version": filename}).scalar_one_or_none()
                        if existing:
                            if existing != checksum:
                                raise RuntimeError(f"Applied migration checksum changed: {filename}")
                            continue
                        connection.exec_driver_sql(sql)
                        connection.execute(text(f"INSERT INTO {LEDGER}(version, checksum, description) VALUES (:version, :checksum, :description)"), {
                            "version": filename, "checksum": checksum,
                            "description": "Additive Shelf Census locations, identity, and print queue support",
                        })
                        applied.append({"version": filename, "checksum": checksum})
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(hashtext('posterpro-schema-migrations'))"))
                connection.commit()
        return applied
    finally:
        engine.dispose()


def migration_status(database_url: str | URL) -> dict:
    engine = create_engine(_connect_url(database_url))
    try:
        with engine.connect() as connection:
            exists = connection.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{LEDGER}"}).scalar_one()
            fingerprint = schema_fingerprint(connection)
            if not exists:
                return {"initialized": False, "schema_fingerprint": fingerprint, "applied": []}
            rows = connection.execute(text(f"SELECT version, checksum, applied_at FROM {LEDGER} ORDER BY applied_at, version")).mappings().all()
            return {"initialized": True, "schema_fingerprint": fingerprint, "applied": [dict(row) for row in rows]}
    finally:
        engine.dispose()
