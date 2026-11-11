#!/usr/bin/env python3
"""Dry-run by default; repair only unpublished under-$25 free-shipping drafts."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

for key, value in dotenv_values("/opt/apps/posterpro/repo/backend/.env").items():
    if value is not None:
        os.environ.setdefault(key, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.services.shipping_profitability_policy import repair_unpublished_under_threshold_shipping

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true", help="Apply scoped repairs to unpublished records")
args = parser.parse_args()
session = SessionLocal()
try:
    print(json.dumps(repair_unpublished_under_threshold_shipping(session, apply=args.apply), indent=2, default=str))
finally:
    session.close()
