"""Prepare operator-owned inventory inside Mercari's official XLSX template.

PosterPro only fills the workbook supplied by the operator. It never uploads or
activates the workbook on Mercari.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MarketplaceName, MarketplaceListingStatus
from app.models.models import Listing, MarketplaceListing


def _shipping(listing: Listing) -> dict[str, Any]:
    profile = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
    mercari = profile.get("mercari") if isinstance(profile.get("mercari"), dict) else {}
    return mercari


def inventory_candidates(db: Session, user_id: int, active_ebay_ids: set[str] | None = None) -> list[dict[str, Any]]:
    query = select(Listing).join(MarketplaceListing, MarketplaceListing.listing_id == Listing.id).where(
               Listing.user_id == user_id, MarketplaceListing.marketplace == MarketplaceName.ebay,
               MarketplaceListing.status.in_([MarketplaceListingStatus.PUBLISHED, MarketplaceListingStatus.UPDATED]))
    if active_ebay_ids is not None:
        query = query.where(Listing.ebay_listing_id.in_(active_ebay_ids))
    rows = db.execute(query
        .order_by(Listing.id)).scalars().unique().all()
    output = []
    for listing in rows:
        shipping = _shipping(listing)
        specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
        category_id = str(shipping.get("category_id") or "").strip()
        category_status = str(shipping.get("category_status") or "").lower()
        blockers = []
        category_leaf_status = str(shipping.get("category_leaf_status") or "").lower()
        if not category_id or category_status not in {"operator_verified", "path_verified"} or category_leaf_status != "confirmed_leaf":
            blockers.append("category")
        weight = shipping.get("package_weight")
        try:
            valid_weight = float(weight) > 0 and str(shipping.get("package_weight_unit") or "").lower() in {"oz", "lb", "g", "kg"}
        except (TypeError, ValueError):
            valid_weight = False
        if not valid_weight: blockers.append("shipping")
        condition = str(shipping.get("condition") or listing.condition or "").strip()
        price = listing.listing_price
        if not condition or price is None or not (1 <= float(price) <= 2000): blockers.append("required_information")
        if not str(listing.title or "").strip() or not str(listing.description or listing.canonical_description or "").strip() or not listing.image_urls:
            blockers.append("required_information")
        if not str(shipping.get("shipping_payer") or "").strip() or not str(shipping.get("shipping_service") or "").strip():
            if "shipping" not in blockers: blockers.append("shipping")
        output.append({"listing_id": listing.id, "title": listing.title, "price": listing.listing_price,
                       "category_path": shipping.get("category_path"), "category_id": category_id or None,
                       "category_status": category_status or "unknown", "status": "export_ready" if not blockers else "needs_repair",
                       "blockers": sorted(set(blockers)), "photo_count": len(listing.image_urls or []),
                       "brand": specifics.get("Brand") or specifics.get("Brand Name") or ""})
    return output


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def official_workbook_category_rows(template: bytes) -> list[dict[str, Any]]:
    """Read actual spreadsheet-import category IDs without conflating UI IDs.

    Workbook names and paths vary, so this accepts common header spellings but
    requires a real category_id reference sheet and nonempty IDs. Missing path
    or parent data remains unknown; no terminal leaves are inferred here.
    """
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise ValueError("XLSX reference import requires openpyxl.") from exc
    try:
        workbook = load_workbook(BytesIO(template), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("Upload Mercari's current official .xlsx listing template.") from exc
    sheet = next((ws for ws in workbook.worksheets if ws.title.casefold() == "category_id"), None)
    if sheet is None:
        raise ValueError("The workbook must contain Mercari's category_id reference worksheet.")
    aliases = {
        "id": {"categoryid", "sourcecategoryid", "id"},
        "name": {"categoryname", "name", "category"},
        "path": {"categorypath", "fullpath", "path"},
        "parent": {"parentcategoryid", "parentid", "parentcategory"},
        "leaf": {"isleaf", "leaf", "terminal"},
    }
    header = None
    header_row = 0
    for row_number, cells in enumerate(sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 30), values_only=True), 1):
        normalized = {_norm(value): index for index, value in enumerate(cells) if value is not None}
        picked = {key: next((normalized[name] for name in candidates if name in normalized), None) for key, candidates in aliases.items()}
        if picked["id"] is not None and (picked["name"] is not None or picked["path"] is not None):
            header, header_row = picked, row_number
            break
    if header is None:
        raise ValueError("Could not identify category ID and name/path columns in Mercari's category_id worksheet.")
    raw_rows = []
    for cells in sheet.iter_rows(min_row=header_row + 1, values_only=True):
        value = lambda key: cells[header[key]] if header.get(key) is not None and header[key] < len(cells) else None
        category_id = str(value("id") or "").strip()
        if not category_id:
            continue
        path_value = value("path")
        path = [part.strip() for part in re.split(r"\s*(?:>|/|\|)\s*", str(path_value)) if part.strip()] if path_value else []
        name = str(value("name") or (path[-1] if path else "") or "").strip()
        if not name:
            continue
        if not path:
            path = [part.strip() for part in re.split(r"\s*(?:>|/|\|)\s*", name) if part.strip()]
            name = path[-1] if path else name
        raw_rows.append({"category_id": category_id, "category_name": name, "path": path,
                         "parent_category_id": str(value("parent") or "").strip() or None,
                         "is_leaf": value("leaf"), "source_namespace": "mercari_spreadsheet_import",
                         "id_semantics": "official Mercari spreadsheet-import category ID; not browser/navigation ID",
                         "verification_status": "authoritatively_verified"})
    if not raw_rows:
        raise ValueError("No usable category IDs and names were found in the category_id worksheet.")
    by_id = {row["category_id"]: row for row in raw_rows}
    for row in raw_rows:
        if len(row["path"]) == 1 and row["parent_category_id"] in by_id:
            parent = by_id[row["parent_category_id"]]
            row["path"] = list(parent["path"]) + [row["category_name"]]
    workbook.close()
    return raw_rows


ALIASES = {
    "title": {"title", "name", "itemtitle", "listingtitle"},
    "description": {"description", "itemdescription", "listingdescription"},
    "price": {"price", "listingprice", "itemprice"},
    "category_id": {"categoryid", "category", "itemcategoryid"},
    "condition": {"condition", "itemcondition", "itemconditionname"},
    "brand": {"brand", "brandid", "brandname"},
    "sku": {"sku", "sellerid", "customsku", "itemid"},
    "shipping_payer": {"shipperpayername", "shippingpayer", "shippingpayername", "shipperpayer"},
    "shipping_method": {"shippingmethod", "shippingmethodid", "shippingservice"},
    "weight": {"weight", "packageweight", "shippingweight"},
    "weight_unit": {"weightunit", "packageweightunit", "shippingweightunit"},
    "size": {"size", "sizeid"},
    "category_size_group_id": {"categorysizegroupid", "sizegroupid"},
    "photo_urls": {"photourls", "imageurls", "images", "imageurl", "photourl"},
    "color": {"color", "colour"},
}


def fill_official_workbook(template: bytes, listings: list[Listing]) -> tuple[bytes, dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise ValueError("XLSX export requires openpyxl to be installed in the PosterPro backend environment.") from exc
    try:
        workbook = load_workbook(BytesIO(template))
    except Exception as exc:
        raise ValueError("Upload Mercari's current .xlsx listing template.") from exc
    # Mercari's FAQ calls this the listing_template format and tells XLSX
    # uploaders to select import_template. Different official workbook builds
    # have used either tab name; keep the original sheet intact and fill the
    # one whose headers match PosterPro's supported fields.
    sheet = next((ws for name in ("import_template", "listing_template")
                  for ws in workbook.worksheets if ws.title.casefold() == name), None)
    if sheet is None:
        raise ValueError("The uploaded workbook must contain Mercari's import_template worksheet.")
    header_row = None
    columns: dict[str, int] = {}
    for row_num in range(1, min(sheet.max_row, 30) + 1):
        raw = {_norm(cell.value): cell.column for cell in sheet[row_num] if cell.value is not None}
        found = {field: next((raw[_norm(alias)] for alias in aliases if _norm(alias) in raw), None) for field, aliases in ALIASES.items()}
        if found["title"] and found["price"] and found["category_id"]:
            header_row, columns = row_num, {field: col for field, col in found.items() if col}
            break
    if header_row is None:
        raise ValueError("Could not identify title, price, and category columns in import_template. The template may have changed.")
    required = {"condition", "shipping_payer", "shipping_method", "weight", "weight_unit"}
    missing_columns = sorted(required - set(columns))
    if missing_columns:
        raise ValueError("This Mercari template is missing required mapped columns: " + ", ".join(missing_columns))
    def reference_values(name: str) -> set[str]:
        ref = next((ws for ws in workbook.worksheets if ws.title.casefold() == name.casefold()), None)
        if ref is None: return set()
        return {_norm(cell.value) for row in ref.iter_rows() for cell in row if cell.value is not None and _norm(cell.value)}
    references = {
        "condition": reference_values("item_condition_names"),
        "category_id": reference_values("category_id"),
        "shipping_payer": reference_values("shipper_payer_name"),
        "shipping_method": reference_values("shipping_method"),
    }
    absent_reference_sheets = [name for name, values in references.items() if not values]
    if absent_reference_sheets:
        raise ValueError("The uploaded official template is missing reference tabs required for validation: " + ", ".join(absent_reference_sheets))
    sheet.delete_rows(header_row + 1, max(0, sheet.max_row - header_row))
    manifest: list[dict[str, Any]] = []
    out_row = header_row + 1
    for listing in listings:
        shipping = _shipping(listing)
        specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
        brand = specifics.get("Brand") or specifics.get("Brand Name") or ""
        values = {
            "title": listing.title or "", "description": listing.marketplace_descriptions.get("mercari") if isinstance(listing.marketplace_descriptions, dict) else listing.description or listing.canonical_description or "",
            "price": listing.listing_price, "category_id": shipping.get("category_id"),
            "condition": shipping.get("condition") or listing.condition, "brand": brand,
            "sku": f"PP-{listing.id}", "shipping_payer": shipping.get("shipping_payer"),
            "shipping_method": shipping.get("shipping_service"), "weight": shipping.get("package_weight"),
            "weight_unit": shipping.get("package_weight_unit"), "size": specifics.get("Size"),
            "category_size_group_id": shipping.get("category_size_group_id"),
            "photo_urls": "\n".join(listing.image_urls or []), "color": specifics.get("Color"),
        }
        for field in references:
            reference = str(values.get(field) or "").strip()
            if not reference or _norm(reference) not in references[field]:
                raise ValueError(f"PosterPro listing #{listing.id} has a {field} value not present in the uploaded official Mercari reference tab.")
        for field, col in columns.items():
            if values.get(field) is not None:
                sheet.cell(out_row, col).value = values[field]
        manifest.append({"row": out_row, "posterpro_listing_id": listing.id, "title": listing.title,
                         "source_hash": hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()})
        out_row += 1
    mapping = workbook.create_sheet("PosterPro mapping")
    mapping.sheet_state = "hidden"
    mapping.append(["mercari_import_row", "posterpro_listing_id", "title", "payload_fingerprint"])
    for item in manifest:
        mapping.append([item["row"], item["posterpro_listing_id"], item["title"], item["source_hash"]])
    mapping.append(["exported_at_utc", datetime.now(UTC).isoformat()])
    buffer = BytesIO(); workbook.save(buffer)
    return buffer.getvalue(), {"exported_count": len(manifest), "row_map": manifest, "worksheet": sheet.title}
