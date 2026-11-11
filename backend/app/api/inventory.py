from fastapi import APIRouter, Depends, HTTPException, Query, Body, UploadFile, File, Header, Response
from sqlalchemy import and_, case, func, select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import BulkJobResponse, InventoryBulkEditRequest, InventoryBulkRequest, ListingResponse, RecoveryCopyRepairRequest
from app.core.auth import ensure_user_owns_resource, get_current_user
from app.core.database import get_db
from app.models.models import BulkJob, CanonicalItem, Listing, MarketplaceListing, User, InventoryBin, InventoryEvent, InventoryProduct, InventoryStock, InventoryPackage, InventoryPrintJob, InventoryManifestImport, InventoryManifestEntry, InventoryReconciliationDecision
from app.models.enums import ListingStatus, MarketplaceListingStatus
from secrets import token_urlsafe
from uuid import uuid4
from collections import Counter
import hashlib
from datetime import datetime, timezone
from app.services.inventory_service import InventorySafetyError, InventoryService
from app.services.scanner_dispatcher import COMMANDS, dispatch_scan
from app.services.package_labels import PAPER_PROFILES, data_url, normalize_profile, render_package_label
from html import escape
import re

router = APIRouter(prefix="/inventory", tags=["inventory"])
bulk_router = APIRouter(tags=["bulk-jobs"])
service = InventoryService()

_PERMANENT_TOTE_CODES = tuple(f"A{i:02d}" for i in range(1, 9))
_PERMANENT_TOTE_BASE_URL = "https://posterpro.sparkleserver.site"


def _normalize_tote_code(value: str) -> str:
    """Resolve the NFC's permanent public slug to the existing tote code."""
    text = str(value or "").strip()
    match = re.fullmatch(r"tote-(a0[1-8])", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return text.upper()


def _permanent_tote_url(code: str) -> str | None:
    normalized = _normalize_tote_code(code)
    if normalized not in _PERMANENT_TOTE_CODES:
        return None
    return f"{_PERMANENT_TOTE_BASE_URL}/inventory/tote-{normalized.lower()}"


def _barcode_key(value: object) -> str:
    """Normalize a scanned or stored product identifier without losing leading zeroes."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def _identifier_type(value: object) -> str:
    """Classify the scanned label family while retaining its exact raw value."""
    raw = str(value or "").strip()
    upper = raw.upper()
    key = _barcode_key(raw)
    if re.fullmatch(r"X00[A-Z0-9]{7}", key):
        return "amazon_fnsku"
    if re.fullmatch(r"LPN[A-Z0-9]{8,}", key):
        return "amazon_lpn"
    if re.fullmatch(r"[0-9]{9}[0-9X]", upper):
        values = [10 if char == "X" else int(char) for char in upper]
        return "isbn10_candidate" if sum((10 - index) * value for index, value in enumerate(values)) % 11 == 0 else "invalid_isbn10_check_digit"
    if re.fullmatch(r"97[89][0-9]{10}", key):
        return "isbn13_candidate" if _valid_gtin(key) else "invalid_isbn13_check_digit"
    if re.search(r"[-:/]", raw) or upper.startswith("MN:"):
        return "manufacturer_part_number_candidate"
    if re.fullmatch(r"[A-Z0-9]{10}", key) and not key.isdigit():
        return "asin_candidate"
    # A GTIN may contain conventional visual separators, but letters or other
    # scanner framing mean the digit substring is not a standalone barcode.
    # Keeping this check strict avoids turning e.g. "SKU 4006381333931" into
    # a false GTIN match.
    digits_only_scan = bool(re.fullmatch(r"[0-9][0-9\s-]*", raw))
    digits = re.sub(r"\D", "", raw) if digits_only_scan else ""
    if len(digits) in {8, 12, 13, 14}:
        if not _valid_gtin(digits):
            return "invalid_gtin_check_digit"
        return {8: "ean8_candidate", 12: "upc_candidate", 13: "ean13_candidate", 14: "gtin14_candidate"}[len(digits)]
    return "unknown_scanned_identifier"


def _valid_gtin(value: str) -> bool:
    if len(value) not in {8, 12, 13, 14} or not value.isdigit():
        return False
    body, check = value[:-1], int(value[-1])
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(body)))
    return (total + check) % 10 == 0


def _gtin_equivalence_key(value: str) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(14) if _valid_gtin(digits) else None


def _product_barcode_values(product: InventoryProduct) -> set[str]:
    identifiers = product.identifiers_json if isinstance(product.identifiers_json, dict) else {}
    accepted = {"barcode", "upc", "upca", "upce", "ean", "ean8", "ean13", "gtin", "gtin8", "gtin12", "gtin13", "gtin14", "isbn", "isbn10", "isbn13", "jan", "asin", "fnsku", "amazonfnsku", "lpn", "amazonlpn", "mpn", "manufacturerpartnumber", "modelnumber"}
    values: set[str] = set()
    for name, value in identifiers.items():
        normalized_name = re.sub(r"[^a-z0-9]", "", str(name).strip().casefold())
        if normalized_name in accepted and value not in (None, ""):
            values.add(str(value).strip())
    return values


def _display_barcode(product: InventoryProduct) -> str | None:
    identifiers = product.identifiers_json if isinstance(product.identifiers_json, dict) else {}
    priority = ("barcode", "upc", "upca", "upce", "ean", "ean13", "ean8", "gtin", "gtin14", "gtin13", "gtin12", "isbn", "isbn13", "isbn10", "jan")
    normalized = {re.sub(r"[^a-z0-9]", "", str(key).strip().casefold()): value for key, value in identifiers.items()}
    for name in priority:
        value = normalized.get(name)
        if value is not None:
            return str(value)
    return None


def _find_products_by_barcode(db: Session, user_id: int, barcode: str) -> list[InventoryProduct]:
    """Resolve exact aliases and equivalent UPC/EAN/GTIN forms in this tenant."""
    scanned_key = _barcode_key(barcode)
    if not scanned_key:
        return []
    scanned_gtin = _gtin_equivalence_key(scanned_key) if re.fullmatch(r"[0-9\s-]+", str(barcode or "").strip()) else None
    scanned_exact = str(barcode or "").strip().casefold()
    matches: list[InventoryProduct] = []
    for product in db.execute(select(InventoryProduct).where(InventoryProduct.user_id == user_id)).scalars():
        for stored in _product_barcode_values(product):
            stored_exact = str(stored or "").strip().casefold()
            stored_gtin = _gtin_equivalence_key(stored) if re.fullmatch(r"[0-9\s-]+", stored_exact) else None
            if stored_exact == scanned_exact or (scanned_gtin and stored_gtin == scanned_gtin):
                matches.append(product)
                break
    return matches


def _find_listings_by_identifier(db: Session, user_id: int, identifier: str) -> list[Listing]:
    """Find existing seller listings by an exact recognized product identifier.

    This lets explicit Add Unit connect physical stock to an existing listing
    identity instead of minting an unrelated provisional product. Titles are
    never used as the identity key.
    """
    accepted = {"barcode", "upc", "upca", "upce", "ean", "ean8", "ean13", "gtin", "gtin8", "gtin12", "gtin13", "gtin14", "isbn", "isbn10", "isbn13", "asin", "fnsku", "amazonfnsku", "lpn", "amazonlpn", "mpn", "manufacturerpartnumber", "modelnumber"}
    expected = str(identifier or "").strip().casefold()
    expected_gtin = _gtin_equivalence_key(expected) if re.fullmatch(r"[0-9\s-]+", expected) else None
    matched: dict[int, Listing] = {}
    for listing in db.execute(select(Listing).where(Listing.user_id == user_id)).scalars():
        values: list[str] = []
        for source in (listing.item_specifics, listing.source_metadata, listing.marketplace_data):
            if not isinstance(source, dict):
                continue
            candidates = [source, source.get("physical_inventory"), source.get("amazon_product_facts")]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                for key, value in candidate.items():
                    normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                    if normalized_key in accepted and value not in (None, "") and not isinstance(value, (dict, list)):
                        values.append(str(value).strip())
        for value in values:
            stored = str(value or "").strip().casefold()
            stored_gtin = _gtin_equivalence_key(stored) if re.fullmatch(r"[0-9\s-]+", stored) else None
            if stored == expected or (expected_gtin and stored_gtin == expected_gtin):
                matched[listing.id] = listing
                break
    return list(matched.values())


def _record_inventory_event(db: Session, *, user_id: int, event_type: str, entity_type: str, entity_key: str, payload: dict) -> None:
    """Every physical mutation receives an idempotent, auditable operation id."""
    db.add(InventoryEvent(user_id=user_id, operation_id=str(uuid4()), event_type=event_type, entity_type=entity_type, entity_key=entity_key, payload_json=payload))


def _package_payload(db: Session, package: InventoryPackage) -> dict:
    """Serialize a package without exposing a cross-user physical location."""
    stocks = db.execute(select(InventoryStock, InventoryProduct).join(InventoryProduct, InventoryStock.product_id == InventoryProduct.id).where(
        InventoryStock.user_id == package.user_id, InventoryStock.location_key == package.package_uuid,
    )).all()
    parent = None
    if package.parent_location_key:
        tote = db.execute(select(InventoryBin).where(InventoryBin.user_id == package.user_id, InventoryBin.container_uuid == package.parent_location_key)).scalar_one_or_none()
        nested = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == package.user_id, InventoryPackage.package_uuid == package.parent_location_key)).scalar_one_or_none()
        parent = (tote.code if tote else (nested.package_code if nested else package.parent_location_key))
    return {
        "id": package.id, "package_uuid": package.package_uuid, "package_code": package.package_code,
        "status": package.status, "parent_location_key": package.parent_location_key, "parent_location_label": parent or "Outside tote",
        "lookup_url": f"/inventory/packages/{package.package_code}", "metadata": package.metadata_json or {},
        "items": [{"product_id": product.id, "title": product.title, "barcode": (product.identifiers_json or {}).get("barcode"), "on_hand": stock.on_hand, "reserved": stock.reserved, "available": max(0, stock.on_hand-stock.reserved)} for stock, product in stocks],
        "quantity": sum(stock.on_hand for stock, _ in stocks),
    }


def _owned_location(db: Session, user_id: int, key: str | None, *, exclude_package_uuid: str | None = None) -> bool:
    if not key:
        return True
    tote = db.execute(select(InventoryBin.id).where(InventoryBin.user_id == user_id, InventoryBin.container_uuid == key)).first()
    package = db.execute(select(InventoryPackage.id).where(InventoryPackage.user_id == user_id, InventoryPackage.package_uuid == key)).first()
    return bool(tote or (package and key != exclude_package_uuid))


def _ensure_no_package_cycle(db: Session, package: InventoryPackage, parent_key: str | None) -> None:
    cursor = parent_key
    seen: set[str] = set()
    while cursor:
        if cursor == package.package_uuid:
            raise HTTPException(422, "A package cannot contain itself or one of its ancestors.")
        if cursor in seen:
            raise HTTPException(409, "The requested package hierarchy contains a cycle.")
        seen.add(cursor)
        parent = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == package.user_id, InventoryPackage.package_uuid == cursor)).scalar_one_or_none()
        cursor = parent.parent_location_key if parent else None


def _location_label(db: Session, *, user_id: int, location_key: str) -> str:
    """Resolve an owned physical key without ever leaking another owner's data."""
    tote = db.execute(select(InventoryBin).where(
        InventoryBin.user_id == user_id, InventoryBin.container_uuid == location_key,
    )).scalar_one_or_none()
    if tote:
        return tote.code
    package = db.execute(select(InventoryPackage).where(
        InventoryPackage.user_id == user_id, InventoryPackage.package_uuid == location_key,
    )).scalar_one_or_none()
    return package.package_code if package else "Outside / legacy location"


def _manifest_lines(raw: bytes) -> tuple[str, list[str]]:
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else: text = raw.decode("utf-8", errors="replace")
    return text, [line.strip() for line in text.splitlines() if line.strip()]


def _manifest_preview(db: Session, user: User, tote: InventoryBin, filename: str, raw: bytes, policy: str) -> dict:
    text, scans = _manifest_lines(raw); counts = Counter(scans); known = []; unknown = []; conflicts = []; ambiguous = []
    for code, count in counts.items():
        products = _find_products_by_barcode(db, user.id, code)
        product = products[0] if len(products) == 1 else None
        if len(products) > 1:
            ambiguous.append({"barcode": code, "requires_decision": True, "matches": [{"product_id": item.id, "title": item.title, "identifiers": item.identifiers_json or {}} for item in products]})
        elif product:
            known.append({"barcode": code, "product_id": product.id, "title": product.title, "scan_count": count})
        else: unknown.append({"barcode": code, "scan_count": count})
        other_rows = []
        if product:
            other_rows = db.execute(select(InventoryStock, InventoryBin.code)
                .outerjoin(InventoryBin, InventoryBin.container_uuid == InventoryStock.location_key)
                .where(InventoryStock.user_id == user.id, InventoryStock.product_id == product.id,
                       InventoryStock.location_key != (tote.container_uuid or ""), InventoryStock.on_hand > 0)).all()
        if other_rows:
            conflicts.append({
                "barcode": code,
                "kind": "existing_stock_other_location",
                "requires_decision": True,
                "source_locations": [
                    {
                        "location_key": stock.location_key,
                        "label": bin_code or stock.location_key,
                        "on_hand": stock.on_hand,
                        "reserved": stock.reserved,
                        "available": max(0, stock.on_hand - stock.reserved),
                    }
                    for stock, bin_code in other_rows
                ],
            })
    checksum = hashlib.sha256(raw).hexdigest()
    prior = db.execute(select(InventoryManifestImport).where(InventoryManifestImport.user_id == user.id, InventoryManifestImport.tote_id == tote.id, InventoryManifestImport.checksum == checksum)).scalars().all()
    return {"tote": tote.code, "filename": filename, "checksum": checksum, "duplicate_policy": policy, "raw_scan_count": len(scans), "unique_barcode_count": len(counts), "duplicate_occurrences": [{"barcode": c, "extra_scans": n-1} for c,n in counts.items() if n > 1], "known_products": known, "unknown_products": unknown, "ambiguous_matches": ambiguous, "existing_location_conflicts": conflicts, "proposed_changes": [{"barcode": c, "quantity": n if policy == "quantity" else 1} for c,n in counts.items()], "prior_imports": [{"id": row.id, "policy": row.duplicate_policy} for row in prior], "source_text": text}


@router.post("/manifests/preview")
async def preview_manifest(tote_code: str = Query(...), duplicate_policy: str = Query("dedupe"), file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if duplicate_policy not in {"dedupe", "quantity"}: raise HTTPException(422, "duplicate_policy must be dedupe or quantity")
    tote = db.execute(select(InventoryBin).where(InventoryBin.user_id == current_user.id, InventoryBin.code == _normalize_tote_code(tote_code))).scalar_one_or_none()
    if not tote: raise HTTPException(404, "Tote not found")
    return _manifest_preview(db, current_user, tote, file.filename or "manifest.txt", await file.read(), duplicate_policy)


@router.post("/manifests/import")
async def import_manifest(tote_code: str = Query(...), duplicate_policy: str = Query("dedupe"), operation_id: str | None = Header(None, alias="Idempotency-Key"), file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if duplicate_policy not in {"dedupe", "quantity"}: raise HTTPException(422, "duplicate_policy must be dedupe or quantity")
    tote = db.execute(select(InventoryBin).where(InventoryBin.user_id == current_user.id, InventoryBin.code == _normalize_tote_code(tote_code))).scalar_one_or_none()
    if not tote: raise HTTPException(404, "Tote not found")
    raw = await file.read()
    if not raw or len(raw) > 2_000_000: raise HTTPException(422, "Manifest must be non-empty and at most 2 MB.")
    preview = _manifest_preview(db, current_user, tote, file.filename or "manifest.txt", raw, duplicate_policy)
    if preview["ambiguous_matches"]:
        raise HTTPException(409, detail={"message": "Some barcodes match multiple existing products. Resolve the product identity before importing to avoid duplicate inventory.", "preview": preview})
    fingerprint = hashlib.sha256(f"manifest:{tote.id}:{duplicate_policy}:{preview['checksum']}".encode()).hexdigest()
    if operation_id:
        replay = db.execute(select(InventoryManifestImport).where(InventoryManifestImport.user_id == current_user.id, InventoryManifestImport.operation_id == operation_id)).scalar_one_or_none()
        if replay:
            if replay.request_fingerprint != fingerprint: raise HTTPException(409, "Idempotency key was already used for a different import.")
            return {"status":"replayed", "import_id":replay.id, "preview":preview}
    same = db.execute(select(InventoryManifestImport).where(InventoryManifestImport.user_id == current_user.id, InventoryManifestImport.tote_id == tote.id, InventoryManifestImport.checksum == preview["checksum"], InventoryManifestImport.duplicate_policy == duplicate_policy)).scalar_one_or_none()
    if same: return {"status": "already_imported", "import_id": same.id, "preview": preview}
    if preview["prior_imports"]: raise HTTPException(409, detail={"message":"Same manifest was imported under another policy; reconcile explicitly.", "preview":preview})
    text, scans = _manifest_lines(raw); counts = Counter(scans); operation_id = operation_id or str(uuid4())
    try:
        manifest = InventoryManifestImport(user_id=current_user.id, tote_id=tote.id, filename=file.filename or "manifest.txt", checksum=preview["checksum"], duplicate_policy=duplicate_policy, raw_scan_count=len(scans), source_text=text, operation_id=operation_id, request_fingerprint=fingerprint)
        db.add(manifest); db.flush()
        for barcode, scan_count in counts.items():
            matches = _find_products_by_barcode(db, current_user.id, barcode)
            product = matches[0] if len(matches) == 1 else None
            if not product:
                product = InventoryProduct(user_id=current_user.id, product_key=str(uuid4()), title=f"Unidentified item ({barcode})", identifiers_json={"barcode": barcode}, enrichment_status="provisional")
                db.add(product); db.flush()
            quantity = scan_count if duplicate_policy == "quantity" else 1
            other = db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == product.id, InventoryStock.location_key != tote.container_uuid, InventoryStock.on_hand > 0)).first()
            if other:
                db.add(InventoryManifestEntry(manifest_import_id=manifest.id, barcode=barcode, scan_count=scan_count, applied_quantity=0, product_id=product.id, resolution="pending_reconciliation"))
                continue
            stock = db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == product.id, InventoryStock.location_key == tote.container_uuid)).scalar_one_or_none()
            if not stock: stock = InventoryStock(user_id=current_user.id, product_id=product.id, location_key=tote.container_uuid, on_hand=0); db.add(stock)
            stock.on_hand += quantity
            db.add(InventoryManifestEntry(manifest_import_id=manifest.id, barcode=barcode, scan_count=scan_count, applied_quantity=quantity, product_id=product.id, resolution="known" if product.title and not product.title.startswith("Unidentified") else "provisional"))
        db.add(InventoryEvent(user_id=current_user.id, operation_id=operation_id, event_type="manifest_imported", entity_type="tote", entity_key=tote.container_uuid or tote.code, payload_json={"manifest_id": manifest.id, "checksum": preview["checksum"]}))
        manifest.status = "pending_reconciliation" if preview["existing_location_conflicts"] else "applied"
        db.commit(); return {"status":manifest.status, "import_id":manifest.id, "preview":preview}
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409, "Concurrent import conflict; retry with the same idempotency key.") from exc


@router.post("/manifests/{manifest_id}/entries/{entry_id}/reconcile")
def reconcile_manifest_entry(manifest_id: int, entry_id: int, payload: dict = Body(...), operation_id: str | None = Header(None, alias="Idempotency-Key"), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    manifest = db.get(InventoryManifestImport, manifest_id); entry = db.get(InventoryManifestEntry, entry_id)
    if not manifest or manifest.user_id != current_user.id or not entry or entry.manifest_import_id != manifest.id: raise HTTPException(404, "Manifest entry not found")
    action = str(payload.get("action") or "").lower(); quantity = int(payload.get("quantity") or 0); source = payload.get("source_location_key")
    if action not in {"add", "move", "skip", "unresolved"} or quantity < 0: raise HTTPException(422, "Choose add, move, skip, or unresolved with a valid quantity.")
    op = operation_id or str(uuid4()); fp = hashlib.sha256(f"{manifest.id}:{entry.id}:{action}:{quantity}:{source}".encode()).hexdigest()
    prior = db.execute(select(InventoryReconciliationDecision).where(InventoryReconciliationDecision.user_id == current_user.id, InventoryReconciliationDecision.operation_id == op)).scalar_one_or_none()
    if prior:
        if prior.request_fingerprint != fp: raise HTTPException(409, "Idempotency key was reused with different reconciliation data.")
        return {"status":"replayed", "decision_id":prior.id}
    if entry.resolution != "pending_reconciliation": raise HTTPException(409, "This entry has already been resolved.")
    tote = db.get(InventoryBin, manifest.tote_id); product = db.get(InventoryProduct, entry.product_id)
    if action == "move":
        if not source or source == tote.container_uuid:
            raise HTTPException(422, "Choose a different source location to move stock from.")
        src = db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == product.id, InventoryStock.location_key == source).with_for_update()).scalar_one_or_none()
        if not src or quantity <= 0 or src.on_hand - src.reserved < quantity: raise HTTPException(409, "Insufficient unreserved stock to move.")
        src.on_hand -= quantity
    if action == "add":
        if quantity <= 0: raise HTTPException(422, "Added quantity must be positive.")
    if action in {"add", "move"}:
        dest = db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == product.id, InventoryStock.location_key == tote.container_uuid).with_for_update()).scalar_one_or_none()
        if not dest: dest = InventoryStock(user_id=current_user.id, product_id=product.id, location_key=tote.container_uuid, on_hand=0); db.add(dest)
        dest.on_hand += quantity; entry.applied_quantity = quantity; entry.resolution = action
    else: entry.resolution = action
    decision = InventoryReconciliationDecision(user_id=current_user.id, manifest_entry_id=entry.id, action=action, quantity=quantity, source_location_key=source, destination_location_key=tote.container_uuid, operation_id=op, request_fingerprint=fp, status="pending" if action == "unresolved" else "applied")
    db.add(decision); db.flush(); manifest.status = "applied" if not any(e.resolution == "pending_reconciliation" for e in db.execute(select(InventoryManifestEntry).where(InventoryManifestEntry.manifest_import_id == manifest.id)).scalars()) else "pending_reconciliation"; db.commit()
    return {"status":decision.status, "decision_id":decision.id, "manifest_status":manifest.status}


@router.get("/totes")
def list_totes(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.execute(select(InventoryBin).where(InventoryBin.user_id == current_user.id).order_by(InventoryBin.code)).scalars().all()
    return {"totes": [{
        "code": row.code, "label": row.label, "container_uuid": row.container_uuid,
        "parent_location_key": row.parent_location_key, "nfc_token": row.nfc_token,
        "lookup_url": _permanent_tote_url(row.code) or f"/scan/bin/{row.nfc_token}",
        "scan_url": f"/scan/bin/{row.nfc_token}",
        "permanent_url": _permanent_tote_url(row.code),
        "unit_count": int(db.execute(select(func.coalesce(func.sum(InventoryStock.on_hand), 0)).where(InventoryStock.user_id == current_user.id, InventoryStock.location_key == row.container_uuid)).scalar_one()),
    } for row in rows]}


@router.post("/totes/permanent-pages")
def ensure_permanent_tote_pages(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Idempotently ensure the eight NFC-addressed totes exist for this owner.

    This provisions only empty container identities. It never creates product
    records or stock, and all later manifest/scanner operations remain in the
    existing physical inventory tables.
    """
    rows = []
    created = 0
    for code in _PERMANENT_TOTE_CODES:
        tote = db.execute(select(InventoryBin).where(
            InventoryBin.user_id == current_user.id,
            func.upper(InventoryBin.code) == code,
        ).order_by(InventoryBin.id).limit(1)).scalar_one_or_none()
        if tote is None:
            tote = InventoryBin(
                user_id=current_user.id,
                code=code,
                label=f"Tote {code}",
                nfc_token=token_urlsafe(24),
                container_uuid=str(uuid4()),
                metadata_json={"permanent_nfc_url": _permanent_tote_url(code), "container_identity_version": 1},
            )
            db.add(tote)
            db.flush()
            created += 1
            _record_inventory_event(db, user_id=current_user.id, event_type="container_created", entity_type="tote", entity_key=tote.container_uuid, payload={"code": code, "permanent_nfc_url": _permanent_tote_url(code), "source": "permanent_tote_pages"})
        rows.append(tote)
    db.commit()
    return {
        "created_count": created,
        "existing_count": len(rows) - created,
        "totes": [{"code": row.code, "label": row.label or row.code, "container_uuid": row.container_uuid, "permanent_url": _permanent_tote_url(row.code)} for row in rows],
    }


@router.get("/find")
def find_inventory_item(query: str = Query(..., min_length=1, max_length=255), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Private physical lookup for a scanned barcode, tote NFC code, or package ID.

    A missing result is deliberately a normal, explicit state: callers can
    distinguish "not in my synced inventory" from a transport/offline error
    without exposing inventory owned by another account.
    """
    needle = query.strip()
    upper = needle.upper()
    tote = db.execute(select(InventoryBin).where(
        InventoryBin.user_id == current_user.id,
        or_(InventoryBin.code == _normalize_tote_code(upper), InventoryBin.container_uuid == needle, InventoryBin.nfc_token == needle),
    )).scalar_one_or_none()
    if tote:
        contents = db.execute(
            select(InventoryStock, InventoryProduct)
            .join(InventoryProduct, InventoryProduct.id == InventoryStock.product_id)
            .where(InventoryStock.user_id == current_user.id, InventoryStock.location_key == tote.container_uuid, InventoryStock.on_hand > 0)
            .order_by(InventoryProduct.title)
        ).all()
        return {"found": True, "match_type": "tote", "tote": {
            "code": tote.code, "container_uuid": tote.container_uuid, "lookup_url": _permanent_tote_url(tote.code) or f"/scan/bin/{tote.nfc_token}",
            "permanent_url": _permanent_tote_url(tote.code),
            "contents": [{"product_id": product.id, "title": product.title, "barcode": _display_barcode(product), "on_hand": stock.on_hand, "reserved": stock.reserved} for stock, product in contents],
        }}
    package = db.execute(select(InventoryPackage).where(
        InventoryPackage.user_id == current_user.id,
        or_(InventoryPackage.package_code == upper, InventoryPackage.package_uuid == needle),
    )).scalar_one_or_none()
    if package:
        return {"found": True, "match_type": "package", "package": _package_payload(db, package)}
    products = db.execute(select(InventoryProduct).where(InventoryProduct.user_id == current_user.id, InventoryProduct.product_key == needle)).scalars().all()
    products.extend(_find_products_by_barcode(db, current_user.id, needle))
    products = list({product.id: product for product in products}.values())
    if not products:
        return {"found": False, "match_type": None, "query": needle, "message": "No owned tote, package, or product matched this scan."}
    matches = []
    for product in products:
        rows = db.execute(select(InventoryStock).where(
            InventoryStock.user_id == current_user.id, InventoryStock.product_id == product.id, InventoryStock.on_hand > 0,
        ).order_by(InventoryStock.location_key)).scalars().all()
        matches.append({
            "product_id": product.id, "product_key": product.product_key, "title": product.title,
            "barcode": _display_barcode(product),
            "locations": [{"location_key": row.location_key, "label": _location_label(db, user_id=current_user.id, location_key=row.location_key), "on_hand": row.on_hand, "reserved": row.reserved, "available": max(0, row.on_hand - row.reserved), "move_eligible": _owned_location(db, current_user.id, row.location_key)} for row in rows],
        })
    return {"found": True, "match_type": "product", "products": matches}


@router.get("/freeform-qr/lookup")
def suggest_freeform_qr_associations(payload: str = Query(..., min_length=1, max_length=4000), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Suggest owned inventory from descriptive box-QR text without linking it.

    This endpoint is intentionally read-only.  A descriptive payload is not a
    product barcode or a stable identity, so every response requires explicit
    operator confirmation in the caller before any package/item association.
    """
    raw = payload.strip()
    terms = [term for term in re.findall(r"[A-Za-z0-9]{3,}", raw.lower()) if term not in {"the", "and", "with", "from", "box", "item"}]
    terms = list(dict.fromkeys(terms))[:8]
    if not terms:
        return {"payload": raw, "requires_operator_confirmation": True, "suggestions": []}
    predicates = [InventoryProduct.title.ilike(f"%{term}%") for term in terms]
    products = db.execute(
        select(InventoryProduct).where(
            InventoryProduct.user_id == current_user.id,
            InventoryProduct.title.is_not(None),
            or_(*predicates),
        ).order_by(InventoryProduct.title).limit(20)
    ).scalars().all()
    return {
        "payload": raw,
        "search_terms": terms,
        "requires_operator_confirmation": True,
        "automatic_merge_performed": False,
        "suggestions": [{
            "product_id": product.id,
            "product_key": product.product_key,
            "title": product.title,
            "confidence": round(min(0.75, 0.25 + 0.1 * sum(term in str(product.title or "").lower() for term in terms)), 2),
        } for product in products],
    }


@router.get("/manifests")
def manifest_history(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.execute(select(InventoryManifestImport).where(InventoryManifestImport.user_id == current_user.id).order_by(InventoryManifestImport.created_at.desc()).limit(100)).scalars().all()
    return {"imports": [{
        "id": r.id, "filename": r.filename, "status": r.status,
        "raw_scan_count": r.raw_scan_count, "policy": r.duplicate_policy,
        "created_at": r.created_at.isoformat(),
        "tote_code": (db.get(InventoryBin, r.tote_id).code if db.get(InventoryBin, r.tote_id) else None),
        "entry_count": db.execute(select(func.count()).select_from(InventoryManifestEntry).where(InventoryManifestEntry.manifest_import_id == r.id)).scalar_one(),
        "pending_count": db.execute(select(func.count()).select_from(InventoryManifestEntry).where(InventoryManifestEntry.manifest_import_id == r.id, InventoryManifestEntry.resolution == "pending_reconciliation")).scalar_one(),
    } for r in rows]}


@router.get("/manifests/{manifest_id}")
def manifest_detail(manifest_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    manifest = db.get(InventoryManifestImport, manifest_id)
    if not manifest or manifest.user_id != current_user.id: raise HTTPException(404, "Manifest not found")
    entries = db.execute(select(InventoryManifestEntry).where(InventoryManifestEntry.manifest_import_id == manifest.id).order_by(InventoryManifestEntry.id)).scalars().all()
    tote = db.get(InventoryBin, manifest.tote_id)
    serialized_entries = []
    for entry in entries:
        product = db.get(InventoryProduct, entry.product_id) if entry.product_id else None
        sources = []
        if product:
            source_rows = db.execute(
                select(InventoryStock, InventoryBin.code)
                .outerjoin(InventoryBin, InventoryBin.container_uuid == InventoryStock.location_key)
                .where(
                    InventoryStock.user_id == current_user.id,
                    InventoryStock.product_id == product.id,
                    InventoryStock.location_key != tote.container_uuid,
                    InventoryStock.on_hand > 0,
                )
                .order_by(InventoryStock.location_key)
            ).all()
            sources = [{"location_key": stock.location_key, "label": bin_code or stock.location_key, "on_hand": stock.on_hand, "reserved": stock.reserved, "available": max(0, stock.on_hand - stock.reserved)} for stock, bin_code in source_rows]
        serialized_entries.append({"id":entry.id,"barcode":entry.barcode,"scan_count":entry.scan_count,"applied_quantity":entry.applied_quantity,"resolution":entry.resolution,"product_title":(product.title if product else None),"source_locations":sources})
    return {"id":manifest.id, "status":manifest.status, "tote_code":tote.code, "duplicate_policy":manifest.duplicate_policy, "entries":serialized_entries}


@router.get("/totes/{code}")
def tote_detail(code: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tote = db.execute(select(InventoryBin).where(InventoryBin.user_id == current_user.id, InventoryBin.code == _normalize_tote_code(code))).scalar_one_or_none()
    if not tote: raise HTTPException(404, "Tote not found")
    rows = db.execute(select(InventoryStock, InventoryProduct).join(InventoryProduct, InventoryStock.product_id == InventoryProduct.id).where(InventoryStock.user_id == current_user.id, InventoryStock.location_key == tote.container_uuid, InventoryStock.on_hand > 0)).all()
    items = [{"product_id":p.id,"title":p.title,"barcode":_display_barcode(p),"on_hand":s.on_hand,"reserved":s.reserved,"available":max(0,s.on_hand-s.reserved),"status":p.enrichment_status,"identification_status":((p.facts_json or {}).get("identification_status") if isinstance(p.facts_json, dict) else None) or ("identified" if p.enrichment_status != "provisional" else "unidentified"),"candidate_identifier_type":((p.facts_json or {}).get("identifier_family") or (p.facts_json or {}).get("candidate_identifier_type")) if isinstance(p.facts_json, dict) else None} for s,p in rows]
    stocked_products = {p.id for _, p in rows}
    # Manifest claims awaiting location/identity reconciliation do not join to
    # stock. Keep them visible without counting them as physical on-hand units.
    unresolved = db.execute(
        select(InventoryManifestEntry, InventoryProduct)
        .join(InventoryManifestImport, InventoryManifestImport.id == InventoryManifestEntry.manifest_import_id)
        .outerjoin(InventoryProduct, InventoryProduct.id == InventoryManifestEntry.product_id)
        .where(
            InventoryManifestImport.user_id == current_user.id,
            InventoryManifestImport.tote_id == tote.id,
            InventoryManifestEntry.resolution.in_(("pending_reconciliation", "unresolved")),
        )
        .order_by(InventoryManifestEntry.id)
    ).all()
    unresolved_barcodes = set()
    for entry, product in unresolved:
        if entry.barcode in unresolved_barcodes:
            continue
        unresolved_barcodes.add(entry.barcode)
        if product and product.id in stocked_products:
            continue
        items.append({
            "product_id": product.id if product else None,
            "title": product.title if product and product.title else "Unidentified item",
            "barcode": entry.barcode,
            "on_hand": 0,
            "reserved": 0,
            "available": 0,
            "provisional_quantity": 1,
            "status": "pending_reconciliation",
            "identification_status": ((product.facts_json or {}).get("identification_status") if product and isinstance(product.facts_json, dict) else None) or ("unidentified" if not product or product.enrichment_status == "provisional" else "identified"),
            "candidate_identifier_type": ((product.facts_json or {}).get("identifier_family") or (product.facts_json or {}).get("candidate_identifier_type")) if product and isinstance(product.facts_json, dict) else _identifier_type(entry.barcode),
            "warning": "This barcode was observed in more than one location or has an unresolved identity. Reconcile its location before counting it as on-hand stock.",
        })
    on_hand = sum(item["on_hand"] for item in items)
    available = sum(item["available"] for item in items)
    return {"tote":{"code":tote.code,"label":tote.label or tote.code,"container_uuid":tote.container_uuid,"parent_location_key":tote.parent_location_key,"nfc_token":tote.nfc_token,"lookup_url":_permanent_tote_url(tote.code) or f"/scan/bin/{tote.nfc_token}","permanent_url":_permanent_tote_url(tote.code),"scan_url":f"/scan/bin/{tote.nfc_token}"}, "items":items, "summary":{"product_count":len(items),"on_hand":on_hand,"available":available,"unresolved_location_claims":len(unresolved_barcodes)}}


@router.post("/totes")
def create_tote(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    code = str(payload.get("code") or "").strip()
    if not code or len(code) > 128:
        raise HTTPException(status_code=422, detail="A tote code of up to 128 characters is required.")
    existing = db.execute(select(InventoryBin).where(InventoryBin.user_id == current_user.id, InventoryBin.code == code)).scalar_one_or_none()
    if existing:
        return {"created": False, "code": existing.code, "container_uuid": existing.container_uuid, "lookup_url": _permanent_tote_url(existing.code) or f"/scan/bin/{existing.nfc_token}", "permanent_url": _permanent_tote_url(existing.code)}
    tote = InventoryBin(user_id=current_user.id, code=code, label=str(payload.get("label") or code), nfc_token=token_urlsafe(24), container_uuid=str(uuid4()), parent_location_key=str(payload.get("parent_location_key") or "").strip() or None)
    db.add(tote); db.flush()
    _record_inventory_event(db, user_id=current_user.id, event_type="container_created", entity_type="tote", entity_key=tote.container_uuid, payload={"code": tote.code, "parent_location_key": tote.parent_location_key})
    db.commit()
    return {"created": True, "code": tote.code, "container_uuid": tote.container_uuid, "lookup_url": _permanent_tote_url(tote.code) or f"/scan/bin/{tote.nfc_token}", "permanent_url": _permanent_tote_url(tote.code)}


@router.post("/totes/bulk")
def create_totes_bulk(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Create permanent tote identities and NFC-writing instructions in one safe batch.

    Writing a physical NFC tag stays deliberately on the operator's device; the
    server generates no bearer credential and never claims a tag was written.
    """
    codes = payload.get("codes")
    if not isinstance(codes, list) or not codes or len(codes) > 200:
        raise HTTPException(422, "codes must contain between 1 and 200 tote codes.")
    normalized = [str(code or "").strip().upper() for code in codes]
    if any(not code or len(code) > 128 for code in normalized) or len(set(normalized)) != len(normalized):
        raise HTTPException(422, "Tote codes must be unique, non-empty, and at most 128 characters.")
    parent_key = str(payload.get("parent_location_key") or "").strip() or None
    if parent_key and not _owned_location(db, current_user.id, parent_key):
        raise HTTPException(422, "parent_location_key must be one of your totes or packages.")
    created, existing = [], []
    for code in normalized:
        tote = db.execute(select(InventoryBin).where(InventoryBin.user_id == current_user.id, InventoryBin.code == code)).scalar_one_or_none()
        if tote:
            existing.append(tote)
            continue
        tote = InventoryBin(user_id=current_user.id, code=code, label=code, nfc_token=token_urlsafe(24), container_uuid=str(uuid4()), parent_location_key=parent_key)
        db.add(tote); db.flush(); created.append(tote)
        _record_inventory_event(db, user_id=current_user.id, event_type="container_created", entity_type="tote", entity_key=tote.container_uuid, payload={"code": code, "parent_location_key": parent_key, "bulk": True})
    db.commit()
    rows = created + existing
    return {"created_count": len(created), "existing_count": len(existing), "totes": [{"code": tote.code, "container_uuid": tote.container_uuid, "nfc_url": _permanent_tote_url(tote.code) or f"/scan/bin/{tote.nfc_token}", "scan_url": f"/scan/bin/{tote.nfc_token}", "permanent_url": _permanent_tote_url(tote.code), "programming_status": "not_programmed"} for tote in rows], "nfc_programming_note": "Write each returned NFC URL to its physical tag with a device-side NFC writer, then scan to verify ownership-authenticated lookup."}


@router.get("/packages")
def list_packages(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == current_user.id).order_by(InventoryPackage.created_at.desc())).scalars().all()
    return {"packages": [_package_payload(db, row) for row in rows], "paper_profiles": list(PAPER_PROFILES)}


@router.post("/packages")
def create_package(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    code = str(payload.get("package_code") or "").strip().upper()
    if not code or len(code) > 64 or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in code):
        raise HTTPException(422, "package_code must be 1–64 uppercase letters, digits, hyphens, or underscores.")
    existing = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == current_user.id, InventoryPackage.package_code == code)).scalar_one_or_none()
    if existing:
        return {"created": False, "package": _package_payload(db, existing)}
    parent_key = str(payload.get("parent_location_key") or "").strip() or None
    if not _owned_location(db, current_user.id, parent_key):
        raise HTTPException(422, "parent_location_key must be one of your tote or package identities.")
    status = str(payload.get("status") or "open").strip().lower()
    if status not in {"open", "sealed", "shipped", "archived"}:
        raise HTTPException(422, "status must be open, sealed, shipped, or archived.")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    package = InventoryPackage(user_id=current_user.id, package_uuid=str(uuid4()), package_code=code, parent_location_key=parent_key, status=status, metadata_json=metadata)
    db.add(package); db.flush()
    _record_inventory_event(db, user_id=current_user.id, event_type="package_created", entity_type="package", entity_key=package.package_uuid, payload={"package_code": code, "parent_location_key": parent_key})
    db.commit(); db.refresh(package)
    return {"created": True, "package": _package_payload(db, package)}


@router.get("/packages/{package_code}")
def package_detail(package_code: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    package = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == current_user.id, InventoryPackage.package_code == package_code.upper())).scalar_one_or_none()
    if not package:
        raise HTTPException(404, "Package not found")
    return {"package": _package_payload(db, package)}


@router.patch("/packages/{package_code}")
def update_package(package_code: str, payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    package = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == current_user.id, InventoryPackage.package_code == package_code.upper())).scalar_one_or_none()
    if not package:
        raise HTTPException(404, "Package not found")
    if "parent_location_key" in payload:
        parent_key = str(payload.get("parent_location_key") or "").strip() or None
        if not _owned_location(db, current_user.id, parent_key, exclude_package_uuid=package.package_uuid):
            raise HTTPException(422, "parent_location_key must be one of your other tote or package identities.")
        _ensure_no_package_cycle(db, package, parent_key)
        package.parent_location_key = parent_key
    if "status" in payload:
        status = str(payload.get("status") or "").lower()
        if status not in {"open", "sealed", "shipped", "archived"}:
            raise HTTPException(422, "status must be open, sealed, shipped, or archived.")
        package.status = status
    if isinstance(payload.get("metadata"), dict):
        package.metadata_json = {**(package.metadata_json or {}), **payload["metadata"]}
    _record_inventory_event(db, user_id=current_user.id, event_type="package_updated", entity_type="package", entity_key=package.package_uuid, payload={"parent_location_key": package.parent_location_key, "status": package.status})
    db.add(package); db.commit(); db.refresh(package)
    return {"package": _package_payload(db, package)}


@router.post("/packages/{package_code}/contents")
def package_contents(package_code: str, payload: dict = Body(...), operation_id: str | None = Header(None, alias="Idempotency-Key"), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Add stock into a physical package or move unreserved stock into it."""
    package = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == current_user.id, InventoryPackage.package_code == package_code.upper())).scalar_one_or_none()
    if not package:
        raise HTTPException(404, "Package not found")
    if package.status != "open":
        raise HTTPException(409, "Only an open package can receive inventory.")
    try:
        product_id, quantity = int(payload.get("product_id")), int(payload.get("quantity"))
    except (TypeError, ValueError):
        raise HTTPException(422, "product_id and a positive quantity are required.")
    if quantity < 1:
        raise HTTPException(422, "quantity must be positive.")
    action = str(payload.get("action") or "move").lower()
    if action not in {"move", "add"}:
        raise HTTPException(422, "action must be move or add.")
    source_key = str(payload.get("source_location_key") or "").strip() or None
    fingerprint = hashlib.sha256(f"package:{package.id}:{product_id}:{quantity}:{action}:{source_key}".encode()).hexdigest()
    op = operation_id or str(uuid4())
    replay = db.execute(select(InventoryEvent).where(InventoryEvent.user_id == current_user.id, InventoryEvent.operation_id == op)).scalar_one_or_none()
    if replay:
        if (replay.payload_json or {}).get("request_fingerprint") != fingerprint:
            raise HTTPException(409, "Idempotency key was already used for different package contents.")
        return {"status": "replayed", "package": _package_payload(db, package)}
    product = db.get(InventoryProduct, product_id)
    if not product or product.user_id != current_user.id:
        raise HTTPException(404, "Product not found")
    if action == "move":
        if not source_key or source_key == package.package_uuid or not _owned_location(db, current_user.id, source_key):
            raise HTTPException(422, "A different owned source_location_key is required to move stock.")
        source = db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == product.id, InventoryStock.location_key == source_key).with_for_update()).scalar_one_or_none()
        if not source or source.on_hand - source.reserved < quantity:
            raise HTTPException(409, "Insufficient unreserved stock in the source location.")
        source.on_hand -= quantity
    destination = db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == product.id, InventoryStock.location_key == package.package_uuid).with_for_update()).scalar_one_or_none()
    if not destination:
        destination = InventoryStock(user_id=current_user.id, product_id=product.id, location_key=package.package_uuid, on_hand=0)
        db.add(destination)
    destination.on_hand += quantity
    payload_event = {"request_fingerprint": fingerprint, "product_id": product.id, "quantity": quantity, "action": action, "source_location_key": source_key}
    db.add(InventoryEvent(user_id=current_user.id, operation_id=op, event_type="package_contents_changed", entity_type="package", entity_key=package.package_uuid, payload_json=payload_event))
    db.commit(); db.refresh(package)
    return {"status": "applied", "package": _package_payload(db, package)}


@router.post("/stock/move")
def move_inventory_stock(payload: dict = Body(...), operation_id: str | None = Header(None, alias="Idempotency-Key"), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Move available units between this operator's totes/open packages."""
    try:
        product_id, quantity = int(payload.get("product_id")), int(payload.get("quantity"))
    except (TypeError, ValueError):
        raise HTTPException(422, "product_id and a positive quantity are required.")
    source_key = str(payload.get("source_location_key") or "").strip()
    destination_key = str(payload.get("destination_location_key") or "").strip()
    if quantity < 1:
        raise HTTPException(422, "quantity must be positive.")
    if not source_key or not destination_key or source_key == destination_key:
        raise HTTPException(422, "Choose distinct source and destination locations.")
    if not _owned_location(db, current_user.id, source_key) or not _owned_location(db, current_user.id, destination_key):
        raise HTTPException(404, "Source or destination location was not found.")
    destination_package = db.execute(select(InventoryPackage).where(
        InventoryPackage.user_id == current_user.id,
        InventoryPackage.package_uuid == destination_key,
    )).scalar_one_or_none()
    if destination_package and destination_package.status != "open":
        raise HTTPException(409, "Only an open package can receive inventory.")
    source_package = db.execute(select(InventoryPackage).where(
        InventoryPackage.user_id == current_user.id,
        InventoryPackage.package_uuid == source_key,
    )).scalar_one_or_none()
    if source_package and source_package.status != "open":
        raise HTTPException(409, "Stock cannot be removed from a package that is not open.")
    product = db.execute(select(InventoryProduct).where(
        InventoryProduct.user_id == current_user.id,
        InventoryProduct.id == product_id,
    )).scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Product not found.")
    fingerprint = hashlib.sha256(
        f"stock-move:{product_id}:{quantity}:{source_key}:{destination_key}".encode()
    ).hexdigest()
    op = operation_id or str(uuid4())
    prior = db.execute(select(InventoryEvent).where(
        InventoryEvent.user_id == current_user.id,
        InventoryEvent.operation_id == op,
    )).scalar_one_or_none()
    if prior:
        if (prior.payload_json or {}).get("request_fingerprint") != fingerprint:
            raise HTTPException(409, "Idempotency key was already used for a different inventory operation.")
        return {"status": "replayed", "operation_id": op, "product_id": product_id, "quantity": quantity,
                "source_location_key": source_key, "destination_location_key": destination_key}
    source = db.execute(select(InventoryStock).where(
        InventoryStock.user_id == current_user.id,
        InventoryStock.product_id == product_id,
        InventoryStock.location_key == source_key,
    ).with_for_update()).scalar_one_or_none()
    if not source or source.on_hand - source.reserved < quantity:
        raise HTTPException(409, "Insufficient unreserved stock at the source location.")
    destination = db.execute(select(InventoryStock).where(
        InventoryStock.user_id == current_user.id,
        InventoryStock.product_id == product_id,
        InventoryStock.location_key == destination_key,
    ).with_for_update()).scalar_one_or_none()
    if not destination:
        destination = InventoryStock(user_id=current_user.id, product_id=product_id, location_key=destination_key, on_hand=0)
        db.add(destination)
    source.on_hand -= quantity
    destination.on_hand += quantity
    event_payload = {
        "request_fingerprint": fingerprint, "product_id": product_id, "quantity": quantity,
        "source_location_key": source_key, "destination_location_key": destination_key,
    }
    db.add(InventoryEvent(user_id=current_user.id, operation_id=op, event_type="inventory_stock_moved",
                          entity_type="product", entity_key=str(product_id), payload_json=event_payload))
    db.commit()
    return {"status": "applied", "operation_id": op, "product_id": product_id, "quantity": quantity,
            "source_location_key": source_key, "source_on_hand": source.on_hand,
            "destination_location_key": destination_key, "destination_on_hand": destination.on_hand}


@router.post("/packages/{package_code}/label")
def queue_package_label(package_code: str, payload: dict = Body(default={}), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    package = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == current_user.id, InventoryPackage.package_code == package_code.upper())).scalar_one_or_none()
    if not package:
        raise HTTPException(404, "Package not found")
    try:
        copies = max(1, min(int(payload.get("copies") or 1), 25)); profile = normalize_profile(payload.get("paper_profile"))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    view = _package_payload(db, package)
    title = str((package.metadata_json or {}).get("title") or (view["items"][0]["title"] if len(view["items"]) == 1 else "PACKAGED INVENTORY"))
    label_payload = {"package_code": package.package_code, "lookup_url": view["lookup_url"], "title": title, "quantity": view["quantity"], "parent_location_key": package.parent_location_key, "parent_location_label": view["parent_location_label"]}
    job = InventoryPrintJob(user_id=current_user.id, package_id=package.id, job_type="package_label", copies=copies, paper_profile=profile, status="queued", payload_json=label_payload)
    db.add(job)
    metadata = dict(package.metadata_json or {}); metadata["last_label_profile"] = profile; metadata["last_label_copies"] = copies; package.metadata_json = metadata
    _record_inventory_event(db, user_id=current_user.id, event_type="package_label_queued", entity_type="package", entity_key=package.package_uuid, payload={"copies": copies, "profile": profile})
    db.commit()
    return {"job_id": job.id, "status": job.status, "copies": copies, "paper_profile": profile, "printable_url": f"/inventory/packages/{package.package_code}/label/printable?paper_profile={profile}&copies={copies}"}


@router.get("/packages/{package_code}/label/preview")
def preview_package_label(package_code: str, paper_profile: str = Query("thermal_4x6"), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    package = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == current_user.id, InventoryPackage.package_code == package_code.upper())).scalar_one_or_none()
    if not package: raise HTTPException(404, "Package not found")
    try: profile = normalize_profile(paper_profile)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    view = _package_payload(db, package)
    payload = {"package_code": package.package_code, "lookup_url": view["lookup_url"], "title": str((package.metadata_json or {}).get("title") or (view["items"][0]["title"] if len(view["items"]) == 1 else "PACKAGED INVENTORY")), "quantity": view["quantity"], "parent_location_key": package.parent_location_key, "parent_location_label": view["parent_location_label"]}
    return {"paper_profile": profile, "payload": payload, "image_data_url": data_url(render_package_label(payload, profile))}


@router.get("/packages/{package_code}/label/printable")
def printable_package_label(package_code: str, paper_profile: str = Query("thermal_4x6"), copies: int = Query(1, ge=1, le=25), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    preview = preview_package_label(package_code, paper_profile, db, current_user)
    page_size = PAPER_PROFILES[preview["paper_profile"]][2]
    image = escape(preview["image_data_url"], quote=True)
    html_doc = f'<!doctype html><html><head><meta charset="utf-8"><title>PosterPro package {escape(package_code)}</title><style>@page{{size:{page_size};margin:0}}body{{margin:0}}img{{display:block;width:100%;page-break-after:always}}</style></head><body>{"".join(f"<img src=\"{image}\" alt=\"Package label\">" for _ in range(copies))}<script>window.onload=()=>window.print()</script></body></html>'
    return Response(content=html_doc, media_type="text/html")


@router.get("/scanner/commands")
def scanner_commands(current_user: User = Depends(get_current_user)):
    """Printable command-sheet source. Clients encode `POSTERPRO:COMMAND:<NAME>` in QR/Code128."""
    return {"commands": [{"label": label, "command": command, "payload": f"POSTERPRO:COMMAND:{label.replace(' ', '_')}"} for label, command in COMMANDS.items()]}


@router.post("/scanner/dispatch")
def scanner_dispatch(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Durably journal one completed scanner event and identify known product scans.

    State changes that need operator context stay in the workstation client; this
    endpoint is intentionally safe for HID, camera, manual, and offline replay.
    """
    result = dispatch_scan(payload.get("value"))
    if result.get("kind") == "barcode":
        result["identifier_type"] = _identifier_type(result.get("barcode"))
    session_id = str(payload.get("session_id") or "").strip() or None
    operation_id = str(payload.get("operation_id") or uuid4())
    prior = db.execute(select(InventoryEvent).where(InventoryEvent.user_id == current_user.id, InventoryEvent.operation_id == operation_id)).scalar_one_or_none()
    if prior:
        return {"status": "replayed", "event_id": prior.id, **(prior.payload_json or result)}
    # A NEW TOTE command arms exactly the next scan as a tote identity. Do not
    # turn that label into an item/product or create a Slate boundary.
    checkpoint = payload.get("workstation_state") if isinstance(payload.get("workstation_state"), dict) else None
    awaiting_tote_code = bool((checkpoint or {}).get("awaiting_tote_code"))
    awaiting_add_unit = bool((checkpoint or {}).get("awaiting_add_unit"))
    awaiting_find_query = bool((checkpoint or {}).get("awaiting_find_query"))
    awaiting_move_query = bool((checkpoint or {}).get("awaiting_move_query"))
    awaiting_bundle_scan = bool((checkpoint or {}).get("awaiting_bundle_scan"))
    if result["kind"] == "barcode" and awaiting_tote_code:
        code = _normalize_tote_code(result["barcode"])
        if not code or len(code) > 128:
            raise HTTPException(422, "Tote code must contain 1–128 characters.")
        tote = db.execute(select(InventoryBin).where(InventoryBin.user_id == current_user.id, func.upper(InventoryBin.code) == code)).scalar_one_or_none()
        created = tote is None
        if tote is None:
            tote = InventoryBin(user_id=current_user.id, code=code, label=code, nfc_token=token_urlsafe(24), container_uuid=str(uuid4()))
            db.add(tote); db.flush()
            _record_inventory_event(db, user_id=current_user.id, event_type="container_created", entity_type="tote", entity_key=tote.container_uuid, payload={"code": code, "source": "scanner_workstation", "scanner_operation_id": operation_id})
        result = {"kind": "tote", "display": tote.code, "tote": {"code": tote.code, "container_uuid": tote.container_uuid, "lookup_url": _permanent_tote_url(tote.code) or f"/scan/bin/{tote.nfc_token}", "created": created}}
    elif result["kind"] == "barcode" and awaiting_add_unit:
        tote_code = str((checkpoint or {}).get("tote") or "").strip().upper()
        tote = db.execute(select(InventoryBin).where(InventoryBin.user_id == current_user.id, func.upper(InventoryBin.code) == tote_code)).scalar_one_or_none() if tote_code else None
        if tote is None:
            result = {"kind": "inventory_unit_blocked", "display": "Add unit blocked", "blocker": "Select one of your totes before adding physical stock."}
        else:
            barcode = str(result["barcode"])
            products = _find_products_by_barcode(db, current_user.id, barcode)
            if len(products) > 1:
                result = {"kind": "inventory_unit_blocked", "display": f"Add unit: {barcode}", "blocker": "This identifier matches multiple PosterPro products. Resolve the identity before adding stock.", "ambiguous_products": [{"id": item.id, "title": item.title} for item in products]}
            else:
                product = products[0] if products else None
                if product is None:
                    listing_matches = _find_listings_by_identifier(db, current_user.id, barcode)
                    titles = {re.sub(r"\s+", " ", str(row.title or "").strip()).casefold() for row in listing_matches if str(row.title or "").strip()}
                    if len(titles) > 1:
                        result = {"kind": "inventory_unit_blocked", "display": f"Add unit: {barcode}", "blocker": "This exact identifier occurs on existing listings with different product titles. Confirm the product identity before adding stock.", "matching_listing_ids": [row.id for row in listing_matches]}
                    else:
                        identifier_type = _identifier_type(barcode)
                        identifiers = {"barcode": barcode}
                        if identifier_type == "amazon_fnsku": identifiers["fnsku"] = barcode
                        elif identifier_type == "amazon_lpn": identifiers["lpn"] = barcode
                        elif identifier_type == "asin_candidate": identifiers["asin"] = barcode
                        elif identifier_type == "manufacturer_part_number_candidate": identifiers["mpn"] = barcode
                        elif identifier_type.endswith("_candidate") and identifier_type not in {"isbn10_candidate", "isbn13_candidate"}:
                            identifiers[identifier_type.removesuffix("_candidate")] = barcode
                        identified_from_listing = bool(listing_matches)
                        product = InventoryProduct(user_id=current_user.id, product_key=str(uuid4()), title=(next((row.title for row in listing_matches if row.title), None) if identified_from_listing else None) or f"Unidentified item ({barcode})", identifiers_json=identifiers, facts_json={"identifier_family": identifier_type, "identification_status": "identified" if identified_from_listing else "unidentified", "identity_provenance": "exact_identifier_match_to_existing_posterpro_listing" if identified_from_listing else "explicit_scanner_add_unit", "matched_listing_ids": [row.id for row in listing_matches]}, enrichment_status="identified" if identified_from_listing else "provisional")
                        db.add(product); db.flush()
                if result.get("kind") == "inventory_unit_blocked":
                    pass
                else:
                    quantity = max(1, min(1000, int((checkpoint or {}).get("quantity") or 1)))
                    stock = db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == product.id, InventoryStock.location_key == tote.container_uuid).with_for_update()).scalar_one_or_none()
                    if stock is None:
                        stock = InventoryStock(user_id=current_user.id, product_id=product.id, location_key=tote.container_uuid, on_hand=0)
                        db.add(stock)
                    stock.on_hand += quantity
                    result = {"kind": "inventory_unit_added", "display": f"Added {quantity} unit(s)", "barcode": barcode, "product": {"id": product.id, "title": product.title, "enrichment_status": product.enrichment_status}, "quantity_added": quantity, "tote": {"code": tote.code, "container_uuid": tote.container_uuid}, "identifier_type": _identifier_type(barcode), "lookup": find_inventory_item(barcode, db, current_user)}
    elif result["kind"] == "barcode" and awaiting_find_query:
        query = result["barcode"].strip()
        lookup = find_inventory_item(query, db, current_user)
        result = {"kind": "inventory_lookup", "display": f"Find: {query}", "lookup": lookup}
    elif result["kind"] == "barcode" and awaiting_move_query:
        query = result["barcode"].strip()
        lookup = find_inventory_item(query, db, current_user)
        result = {"kind": "inventory_move_selection", "display": f"Move: {query}", "lookup": lookup}
    elif result["kind"] == "barcode" and awaiting_bundle_scan:
        query = result["barcode"].strip()
        lookup = find_inventory_item(query, db, current_user)
        draft = (checkpoint or {}).get("bundle_draft") or {}
        products = lookup.get("products") if lookup.get("match_type") == "product" else []
        if draft.get("status") != "collecting":
            result = {"kind": "bundle_component_error", "display": f"Bundle: {query}", "blocker": "Start a bundle before adding scanned components.", "lookup": lookup}
        elif len(products or []) != 1:
            result = {"kind": "bundle_component_error", "display": f"Bundle: {query}", "blocker": "This scan did not resolve to exactly one owned product. Identify or disambiguate it before adding it to a bundle.", "lookup": lookup}
        else:
            product = products[0]
            try:
                quantity = max(1, int((checkpoint or {}).get("quantity") or 1))
            except (TypeError, ValueError):
                quantity = 1
            available = sum(max(0, int(location.get("available") or 0)) for location in product.get("locations") or [])
            already_added = sum(int(component.get("quantity") or 0) for component in draft.get("components") or [] if component.get("product_id") == product["product_id"])
            if available < already_added + quantity:
                result = {"kind": "bundle_component_error", "display": f"Bundle: {query}", "blocker": f"Only {max(0, available - already_added)} unreserved unit(s) are currently recorded for this product; the bundle capture was not changed.", "lookup": lookup}
            else:
                result = {"kind": "bundle_component_selection", "display": f"Bundle: {query}", "bundle_component": {"product_id": product["product_id"], "product_key": product["product_key"], "title": product["title"], "barcode": product["barcode"], "quantity": quantity, "available_at_capture": available}, "lookup": lookup}
    elif result["kind"] == "barcode":
        products = _find_products_by_barcode(db, current_user.id, result["barcode"])
        product = products[0] if len(products) == 1 else None
        result["product"] = None if not product else {"id": product.id, "title": product.title, "status": product.enrichment_status}
        if len(products) > 1:
            result["ambiguous_products"] = [{"id": item.id, "title": item.title, "identifiers": item.identifiers_json or {}} for item in products]
    # The client sends its compact workstation checkpoint with every event. This
    # lets a second device/reload resume the active tote and item without making
    # scanner speed depend on a separate mutable-session table.
    if result.get("command") == "new_package":
        code = f"PKG-{hashlib.sha256(f'{current_user.id}:{operation_id}'.encode()).hexdigest()[:12].upper()}"
        package = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == current_user.id, InventoryPackage.package_code == code)).scalar_one_or_none()
        if package is None:
            tote_code = str((checkpoint or {}).get("tote") or "").strip().upper()
            tote = db.execute(select(InventoryBin).where(InventoryBin.user_id == current_user.id, InventoryBin.code == tote_code)).scalar_one_or_none() if tote_code else None
            package = InventoryPackage(
                user_id=current_user.id, package_uuid=str(uuid4()), package_code=code,
                parent_location_key=tote.container_uuid if tote else None, status="open",
                metadata_json={"source": "scanner_workstation", "scanner_operation_id": operation_id, "session_id": session_id},
            )
            db.add(package); db.flush()
            _record_inventory_event(db, user_id=current_user.id, event_type="package_created", entity_type="package", entity_key=package.package_uuid, payload={"package_code": code, "parent_location_key": package.parent_location_key, "scanner_operation_id": operation_id})
        result["package"] = {"package_code": package.package_code, "package_uuid": package.package_uuid, "lookup_url": f"/inventory/packages/{package.package_code}"}
    elif result.get("command") in {"print_label", "reprint_label"}:
        package_code = str((checkpoint or {}).get("current_package_code") or "").strip().upper()
        package = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == current_user.id, InventoryPackage.package_code == package_code)).scalar_one_or_none() if package_code else None
        if package is None:
            result["action_status"] = "blocked"
            result["blocker"] = "Scan NEW PACKAGE or a known package label before PRINT LABEL."
        else:
            view = _package_payload(db, package)
            title = str((package.metadata_json or {}).get("title") or (view["items"][0]["title"] if len(view["items"]) == 1 else "PACKAGED INVENTORY"))
            paper_profile = "thermal_4x6"
            label_payload = {"package_code": package.package_code, "lookup_url": view["lookup_url"], "title": title, "quantity": view["quantity"], "parent_location_key": package.parent_location_key, "parent_location_label": view["parent_location_label"]}
            job = InventoryPrintJob(user_id=current_user.id, package_id=package.id, job_type="package_label", copies=1, paper_profile=paper_profile, status="queued", payload_json=label_payload)
            db.add(job); db.flush()
            _record_inventory_event(db, user_id=current_user.id, event_type="package_label_queued", entity_type="package", entity_key=package.package_uuid, payload={"copies": 1, "profile": paper_profile, "scanner_operation_id": operation_id})
            result["label"] = {"job_id": job.id, "paper_profile": paper_profile, "printable_url": f"/inventory/packages/{package.package_code}/label/printable?paper_profile={paper_profile}&copies=1"}
    if checkpoint:
        # Persist the state *after* this scan too, so a reload immediately
        # after a barcode/command does not lose the just-completed operation.
        checkpoint = dict(checkpoint)
        if result["kind"] == "tote":
            checkpoint["tote"] = result["tote"]["code"]
            checkpoint["awaiting_tote_code"] = False
            checkpoint["awaiting_find_query"] = False
            checkpoint["current"] = None
        elif result["kind"] == "inventory_lookup":
            checkpoint["awaiting_find_query"] = False
            checkpoint["awaiting_tote_code"] = False
            checkpoint["last_inventory_lookup"] = result["lookup"]
            checkpoint["current"] = None
        elif result["kind"] in {"inventory_unit_added", "inventory_unit_blocked"}:
            checkpoint["awaiting_add_unit"] = False
            if result.get("lookup"):
                checkpoint["last_inventory_lookup"] = result["lookup"]
            checkpoint["current"] = None
        elif result["kind"] == "barcode":
            checkpoint["current"] = {"barcode": result["barcode"], "product": result.get("product"), "quantity": max(1, int(checkpoint.get("quantity") or 1))}
        elif result.get("command") == "new_tote":
            checkpoint["awaiting_tote_code"] = True
            checkpoint["awaiting_find_query"] = False
            result["prompt"] = "Scan the new tote's barcode or type its tote code."
        elif result.get("command") == "find_item":
            checkpoint["awaiting_find_query"] = True
            checkpoint["awaiting_tote_code"] = False
            result["prompt"] = "Scan the item's barcode or enter its private inventory code to find its storage location."
        elif result.get("command") == "add_unit":
            if not str(checkpoint.get("tote") or "").strip():
                result["blocker"] = "Select a tote before using Add Unit. No stock was changed."
                checkpoint["awaiting_add_unit"] = False
            else:
                checkpoint["awaiting_add_unit"] = True
                checkpoint["awaiting_find_query"] = False
                checkpoint["awaiting_move_query"] = False
                checkpoint["awaiting_tote_code"] = False
                result["prompt"] = f"Scan one item to add the selected quantity to Tote {checkpoint['tote']}."
        elif result.get("command") == "move_item":
            checkpoint["awaiting_move_query"] = True
            checkpoint["awaiting_find_query"] = False
            checkpoint["awaiting_tote_code"] = False
            result["prompt"] = "Scan an owned product barcode to choose available stock to move."
        elif result.get("command") == "start_bundle":
            current_bundle = checkpoint.get("bundle_draft")
            if current_bundle and current_bundle.get("status") == "collecting":
                result["blocker"] = "Finish or discard the current bundle capture before starting another one."
            else:
                if current_bundle and current_bundle.get("status") == "review_required":
                    history = list(checkpoint.get("bundle_history") or [])
                    if not any(row.get("bundle_id") == current_bundle.get("bundle_id") for row in history):
                        history.append(current_bundle)
                    checkpoint["bundle_history"] = history
                bundle_id = f"BND-{hashlib.sha256(f'{current_user.id}:{session_id}:{operation_id}'.encode()).hexdigest()[:12].upper()}"
                checkpoint["bundle_draft"] = {"bundle_id": bundle_id, "status": "collecting", "components": [], "stock_reserved": False, "listing_created": False}
                result["bundle_draft"] = checkpoint["bundle_draft"]
                result["prompt"] = "Bundle capture started. Scan ADD TO BUNDLE, then scan each already identified product. Stock is not reserved or moved."
        elif result.get("command") == "add_to_bundle":
            current_bundle = checkpoint.get("bundle_draft") or {}
            if current_bundle.get("status") != "collecting":
                result["blocker"] = "Start a bundle capture before adding components."
            else:
                checkpoint["awaiting_bundle_scan"] = True
                checkpoint["awaiting_find_query"] = False
                checkpoint["awaiting_move_query"] = False
                checkpoint["awaiting_tote_code"] = False
                result["prompt"] = "Scan one existing product barcode to add it to this bundle capture."
        elif result.get("command") == "finish_bundle":
            current_bundle = checkpoint.get("bundle_draft") or {}
            if current_bundle.get("status") != "collecting":
                result["blocker"] = "There is no active bundle capture to finish."
            elif sum(int(component.get("quantity") or 0) for component in current_bundle.get("components") or []) < 2:
                result["blocker"] = "A bundle capture needs at least two units. No inventory or listing state was changed."
            else:
                current_bundle["status"] = "review_required"
                current_bundle["completed_at"] = datetime.now(timezone.utc).isoformat()
                current_bundle["review_blockers"] = ["No canonical bundle product or listing relationship exists yet.", "Bundle stock is not reserved or consolidated; verify component inventory before listing."]
                checkpoint["bundle_draft"] = current_bundle
                result["bundle_draft"] = current_bundle
                result["warning"] = "Bundle capture saved for review only. It is not a sellable product, stock reservation, or marketplace listing."
        elif result.get("command") in {"new_item", "next_item", "finish_item"}:
            checkpoint["current"] = None
        elif result.get("command") in {"next_tote", "select_tote", "finish_session"}:
            checkpoint["tote"] = ""
            checkpoint["current"] = None
        elif result.get("command") == "add_quantity":
            checkpoint["quantity"] = max(1, int(checkpoint.get("quantity") or 1) + 1)
        elif result.get("command") == "remove_quantity":
            checkpoint["quantity"] = max(1, int(checkpoint.get("quantity") or 1) - 1)
        elif result.get("kind") == "inventory_move_selection":
            checkpoint["awaiting_move_query"] = False
            checkpoint["last_inventory_lookup"] = {**result["lookup"], "move_mode": True, "query": result["display"].removeprefix("Move: ")}
            checkpoint["current"] = None
        elif result.get("kind") == "bundle_component_selection":
            current_bundle = dict(checkpoint.get("bundle_draft") or {})
            components = list(current_bundle.get("components") or [])
            component = result["bundle_component"]
            existing = next((row for row in components if row.get("product_id") == component["product_id"]), None)
            if existing:
                existing["quantity"] = int(existing.get("quantity") or 0) + component["quantity"]
                existing["available_at_capture"] = component["available_at_capture"]
            else:
                components.append(component)
            current_bundle["components"] = components
            checkpoint["bundle_draft"] = current_bundle
            checkpoint["awaiting_bundle_scan"] = False
            checkpoint["current"] = None
            result["bundle_draft"] = current_bundle
        elif result.get("kind") == "bundle_component_error":
            checkpoint["awaiting_bundle_scan"] = False
            result["bundle_draft"] = checkpoint.get("bundle_draft")
        if result.get("package"):
            checkpoint["current_package_code"] = result["package"]["package_code"]
        if result.get("label"):
            checkpoint["last_printable_label_url"] = result["label"]["printable_url"]
        result["workstation_state"] = checkpoint
    db.add(InventoryEvent(user_id=current_user.id, operation_id=operation_id, event_type="scanner_scan", entity_type=result["kind"], entity_key=str(result.get("command") or result.get("barcode") or "scan"), session_id=session_id, payload_json=result))
    db.commit()
    return {"status": "recorded", "operation_id": operation_id, **result}


@router.get("/scanner/sessions/{session_id}")
def scanner_session(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Return the latest durable workstation checkpoint for safe resume."""
    events = db.execute(select(InventoryEvent).where(InventoryEvent.user_id == current_user.id, InventoryEvent.session_id == session_id, InventoryEvent.event_type == "scanner_scan").order_by(InventoryEvent.created_at.desc()).limit(100)).scalars().all()
    checkpoint = next((event.payload_json.get("workstation_state") for event in events if isinstance(event.payload_json, dict) and isinstance(event.payload_json.get("workstation_state"), dict)), None)
    return {"session_id": session_id, "workstation_state": checkpoint, "events": [{"id": event.id, "created_at": event.created_at.isoformat(), **(event.payload_json or {})} for event in events]}


@router.get("")
def get_inventory(
    label: str | None = Query(default=None),
    quantity_gt_one: bool = Query(default=False),
    stale: bool = Query(default=False),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=5000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = service.build_inventory_query(
        user_id=current_user.id,
        label=label,
        multi_quantity_only=quantity_gt_one,
        stale=stale,
        search=search,
    )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    paginated = stmt.offset((page - 1) * page_size).limit(page_size)
    listings = db.execute(paginated).scalars().all()
    return {
        "items": service.apply_label_filter(listings, label),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/summary")
def get_inventory_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return owner-scoped inventory concepts as aggregate counts, not page counts."""
    owned = Listing.user_id == current_user.id
    is_archived = Listing.status == ListingStatus.archived
    is_sold = or_(Listing.sold_at.is_not(None), Listing.quantity <= 0)
    listing_counts = db.execute(select(
        func.count(Listing.id).label("all_listings"),
        func.coalesce(func.sum(case((and_(~is_archived, ~is_sold), 1), else_=0)), 0).label("active_listings"),
        func.coalesce(func.sum(case((Listing.status == ListingStatus.draft, 1), else_=0)), 0).label("draft_listings"),
        func.coalesce(func.sum(case((is_sold, 1), else_=0)), 0).label("sold_listings"),
        func.coalesce(func.sum(case((is_archived, 1), else_=0)), 0).label("archived_listings"),
        func.coalesce(func.sum(case((Listing.status == ListingStatus.INGESTED, 1), else_=0)), 0).label("intake_listings"),
        func.coalesce(func.sum(case((and_(~is_archived, ~is_sold, Listing.stale_flag.is_(True)), 1), else_=0)), 0).label("stale_listings"),
        func.coalesce(func.sum(case((and_(~is_archived, ~is_sold, Listing.quantity > 1), 1), else_=0)), 0).label("multi_quantity_listings"),
    ).where(owned)).mappings().one()
    published_placements = db.scalar(select(func.count(MarketplaceListing.id)).join(
        Listing, Listing.id == MarketplaceListing.listing_id,
    ).where(
        Listing.user_id == current_user.id,
        MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]),
        MarketplaceListing.marketplace_listing_id.is_not(None),
        func.trim(MarketplaceListing.marketplace_listing_id) != "",
    )) or 0
    canonical_items = db.scalar(select(func.count(CanonicalItem.id)).where(CanonicalItem.user_id == current_user.id)) or 0
    tracked_products = db.scalar(select(func.count(InventoryProduct.id)).where(InventoryProduct.user_id == current_user.id)) or 0
    stock_records = db.scalar(select(func.count(InventoryStock.id)).where(InventoryStock.user_id == current_user.id)) or 0
    units_on_hand = db.scalar(select(func.coalesce(func.sum(InventoryStock.on_hand), 0)).where(InventoryStock.user_id == current_user.id)) or 0
    return {
        **{key: int(value or 0) for key, value in listing_counts.items()},
        "published_marketplace_placements": int(published_placements),
        "canonical_inventory_items": int(canonical_items),
        "physical_stock_products": int(tracked_products),
        "physical_stock_records": int(stock_records),
        "physical_units_on_hand": int(units_on_hand),
    }


@router.post("/bulk-edit", response_model=list[ListingResponse])
def bulk_edit_inventory(
    payload: InventoryBulkEditRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.listing_ids:
        raise HTTPException(status_code=400, detail="listing_ids is required")

    listings = db.query(Listing).filter(Listing.id.in_(payload.listing_ids)).all()
    if not listings:
        raise HTTPException(status_code=404, detail="No listings found for given ids")
    for listing in listings:
        ensure_user_owns_resource(current_user, listing.user_id)

    try:
        return service.bulk_update(db, listings, payload.model_dump())
    except InventorySafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/bulk", response_model=BulkJobResponse)
def bulk_inventory(
    payload: InventoryBulkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    listing_ids = service.resolve_listing_ids(
        db,
        user_id=current_user.id,
        listing_ids=payload.listing_ids,
        filters=payload.filters.model_dump() if payload.filters else None,
    )
    job = service.queue_bulk_job(
        db,
        user_id=current_user.id,
        action=payload.action,
        listing_ids=listing_ids,
        payload=payload.payload or {},
        filters=payload.filters.model_dump() if payload.filters else {},
    )
    return BulkJobResponse(
        job_id=job.id,
        action=job.action,
        status=job.status,
        total_items=job.total_items,
        processed_items=job.processed_items,
        error_count=job.error_count,
        errors=job.errors or [],
    )


@router.post("/recovery-copy-repair", response_model=BulkJobResponse)
def repair_recovery_copy(
    payload: RecoveryCopyRepairRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(Listing.id).where(Listing.source_type == "media_inventory_recovery")
    if not getattr(current_user, "is_admin", False):
        stmt = stmt.where(Listing.user_id == current_user.id)
    if payload.listing_ids:
        stmt = stmt.where(Listing.id.in_(payload.listing_ids))
    elif payload.filters and payload.filters.search:
        term = f"%{payload.filters.search.strip()}%"
        stmt = stmt.where(Listing.title.ilike(term))
    listing_ids = [int(row[0]) for row in db.execute(stmt).all()]
    job = service.queue_bulk_job(
        db,
        user_id=current_user.id,
        action="repair_recovery_copy",
        listing_ids=listing_ids,
        payload=payload.payload or {},
        filters=payload.filters.model_dump() if payload.filters else {},
    )
    return BulkJobResponse(
        job_id=job.id,
        action=job.action,
        status=job.status,
        total_items=job.total_items,
        processed_items=job.processed_items,
        error_count=job.error_count,
        errors=job.errors or [],
    )


@bulk_router.get("/bulk-jobs/{job_id}", response_model=BulkJobResponse)
def get_bulk_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = db.get(BulkJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Bulk job not found")
    ensure_user_owns_resource(current_user, job.user_id)
    return BulkJobResponse(
        job_id=job.id,
        action=job.action,
        status=job.status,
        total_items=job.total_items,
        processed_items=job.processed_items,
        error_count=job.error_count,
        errors=job.errors or [],
    )


# Feature-level integration: keep the active tote/manifest endpoints intact while
# mounting the additive Census API on the existing inventory router.
from app.api.inventory_census import (
    router as _census_router,
    census_confirm, census_search, census_session, save_census_location,
    census_item_label, reprint_census_item, undo_census_item, move_census_item,
    update_census_label_template, census_label_template_test,
    create_print_agent_pairing_code, pair_print_agent, _auth_print_agent,
    list_print_agents, update_census_print_settings, claim_print_agent_job,
    update_agent_print_job_state, revoke_print_agent, mark_browser_label_dispatched,
)
router.include_router(_census_router)
