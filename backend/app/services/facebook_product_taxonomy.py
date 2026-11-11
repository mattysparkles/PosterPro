"""Import Meta's public product-catalog taxonomy as reference-only evidence.

Meta's product-catalog taxonomy is not the Facebook Marketplace create-form
taxonomy. Rows imported here are deliberately non-publishable and cannot be
confirmed as Facebook Marketplace category mappings.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services.marketplace_taxonomy_registry import _json, _store_version


SOURCE_URL = "https://www.facebook.com/products/categories/en_US.csv"
SOURCE = "official_meta_product_catalog_taxonomy"
SCOPE = "meta:product-catalog:en_US"
NAMESPACE = "meta_product_catalog_en_us"


def parse_meta_product_categories(raw: bytes | str) -> tuple[list[dict[str, Any]], str]:
    content = raw.decode("utf-8-sig") if isinstance(raw, bytes) else str(raw).lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(content))
    headers = {str(value or "").strip().casefold(): value for value in (reader.fieldnames or [])}
    id_header = headers.get("category_id") or headers.get("id")
    path_header = headers.get("category") or headers.get("category_path")
    if not id_header or not path_header:
        raise ValueError("Meta taxonomy file must contain category_id and category columns")

    nodes: dict[tuple[str, ...], dict[str, Any]] = {}
    raw_rows = 0
    for row_number, row in enumerate(reader, start=2):
        category_id = str(row.get(id_header) or "").strip()
        raw_path = str(row.get(path_header) or "").strip()
        path = tuple(part.strip() for part in re.split(r"\s*>\s*", raw_path) if part.strip())
        if not category_id or not path:
            continue
        raw_rows += 1
        for depth in range(1, len(path) + 1):
            prefix = path[:depth]
            node = nodes.setdefault(prefix, {"path": prefix, "category_ids": [], "source_row": row_number})
            if depth == len(path):
                if category_id not in node["category_ids"]:
                    node["category_ids"].append(category_id)
    if not raw_rows:
        raise ValueError("Meta taxonomy file contained no category rows")

    parent_paths = {path[:-1] for path in nodes if len(path) > 1}
    output = []
    for path, node in sorted(nodes.items(), key=lambda item: (len(item[0]), item[0])):
        path_key = hashlib.sha256("\x1f".join(part.casefold() for part in path).encode()).hexdigest()[:40]
        output.append({
            "category_key": f"{NAMESPACE}:{path_key}",
            # A duplicate path can have more than one official ID. Keep all
            # source IDs in evidence and leave the singular registry ID blank
            # unless the source gives an unambiguous value.
            "category_id": node["category_ids"][0] if len(node["category_ids"]) == 1 else None,
            "source_category_ids": node["category_ids"],
            "category_name": path[-1],
            "parent_path": path[:-1],
            "path": list(path),
            "depth": len(path),
            "terminal": path not in parent_paths,
            "source_row": node["source_row"],
        })
    return output, hashlib.sha256(content.encode("utf-8")).hexdigest()


def import_meta_product_categories(db: Session, raw: bytes | str) -> dict[str, Any]:
    """Persist the official catalog taxonomy without making it Marketplace-ready."""
    categories, fingerprint = parse_meta_product_categories(raw)
    now = datetime.now(UTC).replace(tzinfo=None)
    version_id = f"facebook:meta-catalog-products:en_US:{fingerprint[:16]}"
    by_path = {tuple(item["path"]): item for item in categories}
    version_categories = []
    for item in categories:
        parent = by_path.get(tuple(item["parent_path"])) if item["parent_path"] else None
        evidence = {
            "source_url": SOURCE_URL,
            "source_file_sha256": fingerprint,
            "taxonomy_scope": "Meta Commerce product catalog",
            "source_row": item["source_row"],
            "source_category_ids": item["source_category_ids"],
            "marketplace_form_category_equivalence": "unverified",
            "required_attributes": None,
        }
        version_categories.append({
            "version_id": version_id,
            "marketplace": "facebook",
            "category_key": item["category_key"],
            "category_id": item["category_id"],
            "category_name": item["category_name"],
            "parent_key": parent["category_key"] if parent else None,
            "parent_id": parent["category_id"] if parent else None,
            "path": " > ".join(item["path"]),
            "path_json": _json(item["path"]),
            "depth": item["depth"],
            "terminal": "terminal" if item["terminal"] else "branch",
            "publishability": "reference_only",
            "required": None,
            "optional": None,
            "recommended": None,
            "conditional": None,
            "source": SOURCE,
            "evidence": _json(evidence),
            "verified": now,
            "verification": "authoritatively_verified",
            "staleness": "fresh",
            "eligible": False,
        })
    _store_version(db, {
        "version_id": version_id,
        "marketplace": "facebook",
        "scope_key": SCOPE,
        "native_version": fingerprint[:16],
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "evidence": {
            "source_file_sha256": fingerprint,
            "source_type": "official_meta_product_catalog_taxonomy",
            "scope_warning": "Catalog taxonomy is not evidence of Facebook Marketplace form category IDs, leaves, or required fields.",
            "required_marketplace_attributes": "not_provided_by_taxonomy_file",
        },
        "captured_at": now,
        "last_checked_at": now,
        "verification_status": "authoritatively_verified",
        # Keep overall Facebook coverage partial: this version does not cover
        # the Marketplace seller form required by PosterPro's publishing path.
        "completeness_status": "partial",
        "staleness_status": "fresh",
        "notes": "Official Meta Commerce product-category reference only; not the Facebook Marketplace create-form taxonomy. Leaves and IDs must not be used as Marketplace mappings.",
    }, version_categories)

    db.commit()
    return {
        "version_id": version_id,
        "scope_key": SCOPE,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "source_file_sha256": fingerprint,
        "source_row_count": sum(len(item["source_category_ids"]) for item in categories),
        "category_count": len(categories),
        "terminal_count": sum(bool(item["terminal"]) for item in categories),
        "marketplace_publishable": False,
        "completeness_status": "partial",
    }
