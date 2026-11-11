#!/usr/bin/env python3
"""Explicit schema baseline and allowlisted migration runner."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schema_migrations import apply_managed_migrations, baseline_existing_schema, migration_status
from app.core.config import settings

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=("status", "baseline-existing", "apply"))
parser.add_argument("--confirm-existing-schema", action="store_true", help="Required for a baseline operation after external schema review.")
args = parser.parse_args()
url = os.environ.get("DATABASE_URL") or settings.database_url
if args.action == "status":
    result = migration_status(url)
elif args.action == "baseline-existing":
    if not args.confirm_existing_schema:
        parser.error("baseline-existing requires --confirm-existing-schema")
    result = baseline_existing_schema(url)
else:
    result = {"applied": apply_managed_migrations(url)}
print(json.dumps(result, default=str, sort_keys=True))
