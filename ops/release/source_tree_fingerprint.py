#!/usr/bin/env python3
"""Stable fingerprint of runtime source inputs, excluding generated files."""
import hashlib
import pathlib
import sys

EXCLUDED = {".pytest_cache", "__pycache__", "node_modules", ".next"}

def fingerprint(root: pathlib.Path, paths: list[str]) -> str:
    root = root.resolve()
    digest = hashlib.sha256()
    files = []
    for rel in paths:
        path = root / rel
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(p for p in path.rglob("*") if p.is_file() and not (EXCLUDED & set(p.parts)))
    for path in sorted(set(files), key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(hashlib.sha256(chunk).digest())
    return digest.hexdigest()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: source_tree_fingerprint.py ROOT PATH [PATH ...]")
    print(fingerprint(pathlib.Path(sys.argv[1]), sys.argv[2:]))
