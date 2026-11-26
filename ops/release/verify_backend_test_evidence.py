#!/usr/bin/env python3
"""Verify the two non-empty pytest summaries required for runtime promotion."""
from __future__ import annotations

import pathlib
import re
import sys


SUMMARY = re.compile(r"^\s*(\d+) passed(?:,.*)? in [\d.]+s(?:\s+\([^\r\n)]*\))?\s*$")


def verified_summaries(path: pathlib.Path) -> list[str]:
    lines = path.read_text(errors="replace").splitlines()
    summaries = [line.strip() for line in lines if SUMMARY.match(line)]
    if len(summaries) != 2:
        raise ValueError("Candidate must contain passing full-backend and bridge pytest summaries.")
    if any(re.search(r"\b(?:failed|error|errors)\b", line, re.I) for line in summaries):
        raise ValueError("Candidate test evidence contains a failed or errored test.")
    if any(int(SUMMARY.match(line).group(1)) < 1 for line in summaries):
        raise ValueError("Candidate test evidence contains an empty suite.")
    return summaries


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: verify_backend_test_evidence.py backend-tests.txt", file=sys.stderr)
        return 2
    try:
        summaries = verified_summaries(pathlib.Path(sys.argv[1]))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Candidate test evidence contains passing backend and bridge suites:")
    for summary in summaries:
        print(f"  {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
