"""Print a secret-free PosterPro database-target fingerprint and table counts.

Run from the backend directory for the service-equivalent .env lookup, or pass
--pid with the backend process ID to inspect its effective DATABASE_URL.
"""

import argparse
import hashlib
import json
import os


def _hash(value: str | None) -> str | None:
    return hashlib.sha256((value or "").encode()).hexdigest()[:16] if value else None


def _database_url_from_pid(pid: int) -> str:
    values = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
    for value in values:
        if value.startswith(b"DATABASE_URL="):
            return value.split(b"=", 1)[1].decode()
    raise RuntimeError(f"DATABASE_URL is not present in process {pid}'s environment")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, help="Read DATABASE_URL from this running process.")
    args = parser.parse_args()
    if args.pid:
        os.environ["DATABASE_URL"] = _database_url_from_pid(args.pid)

    from sqlalchemy import inspect, text
    from sqlalchemy.engine import make_url
    from app.core.database import engine

    url = make_url(str(engine.url))
    dialect = url.get_backend_name()
    with engine.connect() as connection:
        if dialect == "postgresql":
            current_database, current_user, search_path, server_version, server_identity = connection.execute(
                text("select current_database(), current_user, current_setting('search_path'), version(), inet_server_addr()::text")
            ).one()
        else:
            current_database, current_user, search_path, server_version, server_identity = (
                url.database or ":memory:", None, "main", connection.dialect.server_version_info, None
            )
        inspector = inspect(connection)
        table_counts = {}
        for table in ("users", "intake_photos", "intake_provider_media", "intake_slate_recovery_candidates", "listings"):
            table_counts[table] = connection.execute(text(f"select count(*) from {table}")).scalar_one() if inspector.has_table(table) else None

    target = {
        "dialect": dialect,
        "host_hash": _hash(url.host),
        "port": url.port,
        "database": current_database,
        "schema": search_path,
    }
    target["target_hash"] = _hash(json.dumps(target, sort_keys=True))
    print(json.dumps({
        "target": target,
        "server": {"identity_hash": _hash(str(server_identity)), "version": str(server_version), "current_user": current_user},
        "table_counts": table_counts,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
