#!/usr/bin/env python3
"""Dry-run by default; --apply clears Vine review pricing blockers without changing prices."""
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
from app.services.vine_pricing_repair import repair_verified_vine_price_readiness


parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true", help="Apply the narrowly scoped, idempotent repair")
args = parser.parse_args()
session = SessionLocal()
try:
    print(json.dumps(repair_verified_vine_price_readiness(session, apply=args.apply), indent=2, default=str))
finally:
    session.close()
