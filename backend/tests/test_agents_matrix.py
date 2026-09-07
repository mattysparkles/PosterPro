from pathlib import Path
import re


def test_agents_requirement_ids_are_unique():
    text = (Path(__file__).resolve().parents[2] / "AGENTS.md").read_text()
    ids = re.findall(r"^\|\s*(PP-[A-Z0-9-]+)\s*\|", text, flags=re.MULTILINE)
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    assert not duplicates, f"Duplicate AGENTS requirement IDs: {duplicates}"
