"""Dry-run or idempotently seed the verified 2026-10-14 tote scan history.

This uses PosterPro's existing tote, product, stock, manifest, and event tables.
It intentionally never re-applies a tote whose seed record already exists, so
reruns cannot put stock back after a later move, sale, or removal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models.models import (
    InventoryBin,
    InventoryEvent,
    InventoryManifestEntry,
    InventoryManifestImport,
    InventoryProduct,
    InventoryStock,
)
from app.api.inventory import _find_products_by_barcode, _identifier_type


SEED_ID = "posterpro-totes-20261014-v1"
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "tote_manifest_20261014_v1.json"
EXPECTED_SHA256 = "cc30125b8ddc789251a0eacbd6840ef0bc27dd72da608a0afbc31610bee19590"
EXPECTED = {
    "A01": (41, 38), "A02": (54, 50), "A03": (9, 9), "A04": (17, 16),
    "A05": (6, 6), "A06": (9, 9), "A07": (13, 12), "A08": (5, 5),
}
CROSS_TOTE_CODES = ("X00556MVCF", "B0H72RMLSN")
CANDIDATE_PRODUCTS = {
    "198715192346": ("Blockstream Jade Plus hardware wallet, Genesis Grey", "operator_purchase_confirmed"),
    "B0DKXPX549": ("Girafit 3K/5MP Wi-Fi security camera", "operator_candidate"),
    "764742517911": ("Girafit GRF-K300SW security camera", "operator_candidate"),
    "6921502300341": ("Swiff Audio WS-80 wireless instrument transmitter/receiver set", "operator_candidate"),
    "MN: 9574-2SFP28": ("Dual-port 25GbE SFP28 PCIe adapter, Broadcom BCM57414 family", "operator_candidate"),
    "812293017746": ("Smart Caregiver Early Alert bed-exit alarm with wireless pager", "operator_candidate"),
    "198168881767": ("Walston Industrial GH-5670 mechanical fuel-flow meter", "operator_candidate"),
    "DC97-15459H": ("Washing-machine water inlet valve, Samsung part number DC97-15459H", "operator_candidate"),
}


def load_manifest() -> tuple[bytes, dict[str, list[str]]]:
    raw = DATA_PATH.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != EXPECTED_SHA256:
        raise SystemExit(f"MANIFEST CORRUPTED OR TRUNCATED: {actual}")
    manifest = json.loads(raw)
    if set(manifest) != set(EXPECTED):
        raise SystemExit("Manifest tote keys do not match the verified seed.")
    for tote, scans in manifest.items():
        if (len(scans), len(set(scans))) != EXPECTED[tote]:
            raise SystemExit(f"Manifest count assertion failed for {tote}.")
    if sum(map(len, manifest.values())) != 154:
        raise SystemExit("Manifest raw scan total assertion failed.")
    if sum(len(set(scans)) for scans in manifest.values()) != 145:
        raise SystemExit("Manifest per-tote unique total assertion failed.")
    if len({code for scans in manifest.values() for code in scans}) != 143:
        raise SystemExit("Manifest global unique total assertion failed.")
    return raw, manifest


def reconcile(user_id: int, apply: bool) -> dict:
    raw, manifest = load_manifest()
    db = SessionLocal()
    try:
        totes = db.execute(
            select(InventoryBin).where(
                InventoryBin.user_id == user_id,
                func.upper(InventoryBin.code).in_(EXPECTED),
            )
        ).scalars().all()
        by_code = {row.code.upper(): row for row in totes}
        if set(by_code) != set(EXPECTED):
            raise SystemExit(f"Expected existing totes {sorted(EXPECTED)}, found {sorted(by_code)}; no writes made.")
        if len({row.container_uuid for row in totes}) != 8:
            raise SystemExit("Tote container identities are not unique; no writes made.")

        plans = []
        all_locations: dict[str, list[str]] = {}
        for code, scans in manifest.items():
            for barcode in set(scans):
                all_locations.setdefault(barcode, []).append(code)
        if any(set(all_locations.get(code, [])) != set(expected) for code, expected in {
            "X00556MVCF": ("A03", "A05"), "B0H72RMLSN": ("A04", "A05")
        }.items()):
            raise SystemExit("Cross-tote conflict assertions failed; no writes made.")

        for code, scans in manifest.items():
            tote = by_code[code]
            source_text = "\n".join(scans) + "\n"
            checksum = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
            operation_id = f"{SEED_ID}-{code}"
            prior = db.execute(select(InventoryManifestImport).where(
                InventoryManifestImport.user_id == user_id,
                InventoryManifestImport.operation_id == operation_id,
            )).scalar_one_or_none()
            if prior:
                if prior.checksum != checksum or prior.tote_id != tote.id:
                    raise SystemExit(f"Seed identity conflict for {code}; no writes made.")
                plans.append({"tote": code, "state": "already_seeded", "entries": len(set(scans)), "manifest_id": prior.id})
                continue

            entries = []
            for barcode, occurrences in Counter(scans).items():
                matches = _find_products_by_barcode(db, user_id, barcode)
                product = matches[0] if len(matches) == 1 else None
                other_locations = []
                if product:
                    other_locations = db.execute(select(InventoryStock.location_key).where(
                        InventoryStock.user_id == user_id,
                        InventoryStock.product_id == product.id,
                        InventoryStock.location_key != tote.container_uuid,
                        InventoryStock.on_hand > 0,
                    )).scalars().all()
                # These two identifiers were observed in separate physical
                # totes, but their identity does not prove two physical units.
                # Keep both location claims visible and out of stock totals
                # until the operator reconciles them.
                conflict = len(matches) > 1 or bool(other_locations) or len(all_locations.get(barcode, [])) > 1
                entries.append({
                    "barcode": barcode,
                    "scan_count": occurrences,
                    "product_id": product.id if product and len(matches) == 1 else None,
                    "new_provisional": product is None and len(matches) == 0,
                    "ambiguous": len(matches) > 1,
                    "existing_other_location": bool(other_locations),
                    "pending_reconciliation": conflict,
                })
            existing_stock = db.execute(select(InventoryStock.product_id, InventoryStock.on_hand).where(
                InventoryStock.user_id == user_id,
                InventoryStock.location_key == tote.container_uuid,
            )).all()
            plans.append({
                "tote": code,
                "state": "would_seed",
                "manifest_id": None,
                "container_uuid": tote.container_uuid,
                "raw_scans": len(scans),
                "unique_entries": len(set(scans)),
                "existing_stock_rows": len(existing_stock),
                "existing_stock_units": sum(row.on_hand for row in existing_stock),
                "pending_reconciliation": sum(e["pending_reconciliation"] for e in entries),
                "known_products": sum(e["product_id"] is not None for e in entries),
                "provisional_products": sum(e["new_provisional"] for e in entries),
                "entries": entries,
            })

        result = {
            "seed_id": SEED_ID,
            "dataset_sha256": hashlib.sha256(raw).hexdigest(),
            "mode": "apply" if apply else "dry_run",
            "totes": plans,
        }
        if not apply or all(plan["state"] == "already_seeded" for plan in plans):
            return result

        # One transaction for the full seed. Existing records are not modified.
        try:
            for plan in plans:
                if plan["state"] == "already_seeded":
                    continue
                code = plan["tote"]
                tote = by_code[code]
                scans = manifest[code]
                source_text = "\n".join(scans) + "\n"
                checksum = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                operation_id = f"{SEED_ID}-{code}"
                import_row = InventoryManifestImport(
                    user_id=user_id,
                    tote_id=tote.id,
                    filename=f"{SEED_ID}-{code}.txt",
                    checksum=checksum,
                    duplicate_policy="historical_dedupe",
                    raw_scan_count=len(scans),
                    source_text=source_text,
                    operation_id=operation_id,
                    request_fingerprint=hashlib.sha256(f"{SEED_ID}:{code}:{checksum}".encode()).hexdigest(),
                    reconciliation_json={
                        "seed_identity": SEED_ID,
                        "dataset_sha256": EXPECTED_SHA256,
                        "cross_tote_conflicts": [b for b in CROSS_TOTE_CODES if code in all_locations.get(b, [])],
                    },
                    status="pending_reconciliation" if plan["pending_reconciliation"] else "applied",
                )
                db.add(import_row)
                db.flush()
                for planned in plan["entries"]:
                    barcode = planned["barcode"]
                    product = db.get(InventoryProduct, planned["product_id"]) if planned["product_id"] else None
                    if planned["ambiguous"]:
                        product = None
                    if product is None and planned["new_provisional"]:
                        candidate = CANDIDATE_PRODUCTS.get(barcode)
                        product = InventoryProduct(
                            user_id=user_id,
                            product_key=f"historical-scan-{hashlib.sha256((code + ':' + barcode).encode()).hexdigest()[:40]}",
                            title=candidate[0] if candidate else f"Unidentified item ({barcode})",
                            identifiers_json={"barcode": barcode},
                            facts_json={
                                "identification_status": candidate[1] if candidate else "unidentified",
                                "candidate_identifier_type": "operator_candidate" if candidate else _identifier_type(barcode),
                                "review_note": "Possible same model as the other Girafit identifier; physical unit relationship is unconfirmed." if barcode in {"B0DKXPX549", "764742517911"} else ("Samsung OEM versus compatible replacement is unconfirmed." if barcode == "DC97-15459H" else None),
                            },
                            source_json={"source": "historical_tote_scan", "seed_id": SEED_ID, "tote_code": code, "raw_barcode_preserved": barcode, "candidate_evidence": "operator-provided product identification" if candidate else None},
                            enrichment_status="provisional",
                        )
                        db.add(product)
                        db.flush()

                    pending = planned["pending_reconciliation"]
                    applied = 0 if pending or product is None else 1
                    resolution = "pending_reconciliation" if pending else ("provisional" if planned["new_provisional"] else "known")
                    if applied:
                        stock = db.execute(select(InventoryStock).where(
                            InventoryStock.user_id == user_id,
                            InventoryStock.product_id == product.id,
                            InventoryStock.location_key == tote.container_uuid,
                        )).scalar_one_or_none()
                        if stock is None:
                            stock = InventoryStock(user_id=user_id, product_id=product.id, location_key=tote.container_uuid, on_hand=0)
                            db.add(stock)
                        stock.on_hand += 1
                    db.add(InventoryManifestEntry(
                        manifest_import_id=import_row.id,
                        barcode=barcode,
                        scan_count=planned["scan_count"],
                        applied_quantity=applied,
                        product_id=product.id if product else None,
                        resolution=resolution,
                    ))
                db.add(InventoryEvent(
                    user_id=user_id,
                    operation_id=operation_id,
                    event_type="manifest_imported",
                    entity_type="tote",
                    entity_key=tote.container_uuid,
                    payload_json={"manifest_id": import_row.id, "seed_id": SEED_ID, "checksum": checksum},
                ))
            db.commit()
        except Exception:
            db.rollback()
            raise
        return result
    finally:
        db.close()


def classify_existing_seed_products(user_id: int) -> dict:
    """Add identifier-family hints without changing identity, title, or stock."""
    load_manifest()
    db = SessionLocal()
    try:
        products = db.execute(select(InventoryProduct).where(
            InventoryProduct.user_id == user_id,
            InventoryProduct.source_json["seed_id"].as_string() == SEED_ID,
        )).scalars().all()
        changed = 0
        for product in products:
            facts = dict(product.facts_json or {})
            if facts.get("identifier_family"):
                continue
            identifiers = product.identifiers_json if isinstance(product.identifiers_json, dict) else {}
            raw = str(identifiers.get("barcode") or "")
            family = _identifier_type(raw)
            facts["identifier_family"] = family
            if facts.get("candidate_identifier_type") == "unclassified_scan":
                facts["candidate_identifier_type"] = family
            product.facts_json = facts
            changed += 1
        db.commit()
        return {"seed_id": SEED_ID, "products_seen": len(products), "metadata_rows_updated": changed, "stock_changed": False}
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="commit the seed after checksum and dry-run validation")
    mode.add_argument("--classify-existing", action="store_true", help="add identifier-family hints to this seed only; does not change stock")
    args = parser.parse_args()
    result = classify_existing_seed_products(args.user_id) if args.classify_existing else reconcile(args.user_id, args.apply)
    print(json.dumps(result, indent=2))
