from __future__ import annotations

import zipfile
import asyncio
from datetime import date
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi import UploadFile

from app.core.config import settings
from app.api.schemas import VineImportActionRequest
from app.api.vine_imports import (
    auto_build_vine_drafts,
    create_vine_drafts,
    create_vine_inventory,
    list_vine_batches,
    repair_vine_images,
    upload_vine_report,
)
from app.models.models import Image, IntakeNotification, Listing, MarketplaceAccount, ProductMediaCache, User, VineImportBatch, VineImportItem
from app.services.amazon_media import AmazonProductMediaProvider, _extract_amazon_product_facts
from app.services.amazon_product_discovery import AmazonProductDiscoveryService
from app.services.listing_review import normalize_listing_images
from app.services.listing_review import derive_shipping_profile
from app.services.vine_import_service import VineImportService, _is_unsafe_vine_image, description_source_similarity
from app.services.vine_parser import calculate_vine_eligibility, parse_vine_csv, parse_vine_pdf, parse_vine_xlsx
from app.services.vine_policy import review_vine_product


@pytest.mark.parametrize(
    ("label", "value", "expected"),
    [
        ("Item Dimensions L x W x H", "12 x 18 x 24 inches", (12.0, 18.0, 24.0, "item")),
        ("Product Dimensions", '12\" L x 18\" W x 24\" H', (12.0, 18.0, 24.0, "product")),
        ("Package Dimensions", "30 x 20 x 10 cm", (30.0, 20.0, 10.0, "package")),
        ("Dimensions", "1.5 x 2.25 x 3 ft", (1.5, 2.25, 3.0, "product")),
    ],
)
def test_amazon_dimension_formats_are_normalized(label, value, expected):
    html = f'<span id="productTitle">Test Product</span><table><tr><th>{label}</th><td>{value}</td></tr></table>'
    facts = _extract_amazon_product_facts(html)
    dimensions = facts["dimensions"]
    assert (dimensions["length"], dimensions["width"], dimensions["height"], dimensions["dimension_type"]) == expected
    assert dimensions["raw_text"] == value


def test_vine_fallback_does_not_copy_source_product_prose():
    item = VineImportItem(product_name="Nilight RV Bumper Tote Tank Carrier", asin="B0TEST2141")
    source_sentence = "This carrier provides a secure and convenient way to transport a portable tote tank on a square RV bumper."
    description = VineImportService()._generate_description(item, amazon_facts={"title": item.product_name, "product_description": source_sentence, "brand": "Nilight", "capacity": "15 gallons"})
    assert source_sentence not in description
    assert "Nilight" in description
    assert "15 gallons" in description
    assert description_source_similarity(description, [source_sentence]) < 1.0


def _xlsx_sheet_xml(rows):
    def cell_ref(col_idx, row_idx):
        letters = ""
        value = col_idx + 1
        while value:
            value, remainder = divmod(value - 1, 26)
            letters = chr(65 + remainder) + letters
        return f"{letters}{row_idx}"

    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for row_idx, row in enumerate(rows, start=1):
        lines.append(f'<row r="{row_idx}">')
        for col_idx, value in enumerate(row):
            if value is None:
                continue
            ref = cell_ref(col_idx, row_idx)
            if isinstance(value, (int, float)):
                lines.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                lines.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        lines.append("</row>")
    lines.extend(["</sheetData>", "</worksheet>"])
    return "\n".join(lines)


def build_sample_xlsx(extra_rows=None):
    rows = [
        ["Amazon Vine Itemized Report"],
        ["Generated for internal testing"],
        [None],
        ["Order Number", "ASIN", "Product Name", "Order Type", "Order Date", "Shipped Date", "Cancelled Date", "Estimated Tax Value"],
        ["111-2222222-3333333", "B000TEST01", "Desk Lamp", "ORDER", "01/01/2025", "01/03/2025", "", 19.99],
        ["111-2222222-3333333", "B000TEST01", "Desk Lamp", "CANCELLATION", "01/01/2025", "01/03/2025", "01/04/2025", -19.99],
        ["444-5555555-6666666", "B000TEST02", "RFID Copier Tool", "ORDER", "12/15/2025", "12/16/2025", "", 39.99],
        ["777-\n8888888-\n9999999", "B000TEST03", "Shelf Organizer", "ORDER", "02/10/2025", "", "", 14.5],
    ]
    if extra_rows:
        rows.extend(extra_rows)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as workbook:
        workbook.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        )
        workbook.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        )
        workbook.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="2025" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""",
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""",
        )
        workbook.writestr("xl/worksheets/sheet1.xml", _xlsx_sheet_xml(rows))
    return buffer.getvalue()


def build_sample_pdf():
    lines = [
        "Amazon Vine Itemized Report",
        "Order Number ASIN Product Name Order Type Order Date Shipped Date Cancelled Date Estimated Tax Value",
        "111-2222222-3333333 B000TEST01 Desk Lamp ORDER 01/01/2025 01/03/2025 19.99",
        "444-5555555-6666666 B000TEST02 RFID Copier",
        "Tool ORDER 12/15/2025 12/16/2025 39.99",
    ]
    content_lines = ["BT", "/F1 10 Tf", "72 740 Td"]
    for index, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if index == 0:
            content_lines.append(f"({escaped}) Tj")
        else:
            content_lines.append("0 -16 Td")
            content_lines.append(f"({escaped}) Tj")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >> endobj\n",
        f"4 0 obj << /Length {len(content)} >> stream\n".encode("latin-1") + content + b"\nendstream endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    buffer = BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(buffer.tell())
        buffer.write(obj)
    xref_start = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    buffer.write(
        f"""trailer << /Size {len(objects) + 1} /Root 1 0 R >>
startxref
{xref_start}
%%EOF
""".encode("latin-1")
    )
    return buffer.getvalue()


def build_sample_csv():
    return (
        "Product Title,ASIN,Order Date,Order Number,Brand,Category,Status,Review Deadline,Item URL,Estimated Tax Value\n"
        "Desk Lamp,B000CSV001,01/01/2025,123-1234567-1234567,Acme,Lighting,ordered,07/01/2025,https://www.amazon.com/dp/B000CSV001,19.99\n"
    ).encode("utf-8")


def _write_cached_media_file(tmp_path, relative_path: str) -> str:
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"posterpro-test-image")
    return f"/media/{relative_path}"


def test_xlsx_header_detection_and_field_extraction():
    rows = parse_vine_xlsx(build_sample_xlsx(), reference_date=date(2026, 5, 5))
    assert len(rows) == 4
    assert rows[0].order_number == "111-2222222-3333333"
    assert rows[0].asin == "B000TEST01"
    assert rows[0].product_name == "Desk Lamp"
    assert rows[0].order_type == "ORDER"
    assert rows[0].order_date.isoformat() == "2025-01-01"
    assert rows[0].shipped_date.isoformat() == "2025-01-03"
    assert rows[0].estimated_tax_value == 19.99


def test_dynamic_header_detection_and_multiline_order_numbers():
    rows = parse_vine_xlsx(build_sample_xlsx(), reference_date=date(2026, 5, 5))
    multiline = next(row for row in rows if row.asin == "B000TEST03")
    assert multiline.order_number == "777-8888888-9999999"


def test_xlsx_footer_rows_are_skipped():
    rows = parse_vine_xlsx(
        build_sample_xlsx(
            extra_rows=[
                ["Does the total value change if I have to cancel an order because I can't review it?"],
                ["Yes, if you have to cancel a Vine order because you cannot review it, we may deduct the value."],
            ]
        ),
        reference_date=date(2026, 5, 5),
    )
    assert len(rows) == 4


def test_order_and_cancellation_rows_mark_cancelled():
    rows = parse_vine_xlsx(build_sample_xlsx(), reference_date=date(2026, 5, 5))
    order_row = next(row for row in rows if row.order_type == "ORDER" and row.asin == "B000TEST01")
    cancel_row = next(row for row in rows if row.order_type == "CANCELLATION")
    assert cancel_row.estimated_tax_value == -19.99
    assert order_row.eligibility_status == "cancelled"


def test_eligibility_uses_shipped_date_then_order_date():
    eligible_after, status = calculate_vine_eligibility(
        {
            "order_number": "1",
            "asin": "B000TEST99",
            "product_name": "Desk Lamp",
            "order_type": "ORDER",
            "order_date": date(2025, 1, 1),
            "shipped_date": date(2025, 1, 3),
            "cancelled_date": None,
        },
        reference_date=date(2026, 5, 5),
    )
    assert eligible_after.isoformat() == "2025-07-03"
    assert status == "eligible"

    eligible_after, status = calculate_vine_eligibility(
        {
            "order_number": "1",
            "asin": "B000TEST98",
            "product_name": "Shelf Organizer",
            "order_type": "ORDER",
            "order_date": date(2025, 2, 10),
            "shipped_date": None,
            "cancelled_date": None,
        },
        reference_date=date(2026, 5, 5),
    )
    assert eligible_after.isoformat() == "2025-08-10"
    assert status == "eligible"


def test_december_rows_lock_before_eligible_after():
    rows = parse_vine_xlsx(build_sample_xlsx(), reference_date=date(2026, 5, 5))
    december_row = next(row for row in rows if row.asin == "B000TEST02")
    assert december_row.eligible_after.isoformat() == "2026-06-16"
    assert december_row.eligibility_status == "locked_until_2026-06-16"


def test_pdf_parser_marks_rows_for_review():
    rows = parse_vine_pdf(build_sample_pdf(), reference_date=date(2026, 5, 5))
    assert len(rows) == 2
    assert all("Require preflight review before draft creation" in row.parse_warnings for row in rows)
    assert any("PDF fallback parse" in row.parse_warnings for row in rows)
    assert rows[1].product_name.startswith("RFID Copier Tool")


def test_csv_parser_maps_common_alias_headers():
    rows = parse_vine_csv(build_sample_csv(), reference_date=date(2026, 5, 5))
    assert len(rows) == 1
    row = rows[0]
    assert row.product_name == "Desk Lamp"
    assert row.asin == "B000CSV001"
    assert row.brand == "Acme"
    assert row.category == "Lighting"
    assert row.item_url == "https://www.amazon.com/dp/B000CSV001"


def test_policy_flags_restricted_keywords():
    result = review_vine_product("RFID Copier Tool with hidden camera and lithium battery")
    assert result.restricted_review_required is True
    assert "rfid-security" in result.restricted_reasons
    assert "surveillance" in result.restricted_reasons
    assert "battery-hazmat" in result.restricted_reasons


def test_media_fetch_failure_does_not_fail_import(monkeypatch, db_session):
    user = User(email=f"media-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    batch = VineImportService().create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )

    monkeypatch.setattr(
        "app.services.amazon_media.AmazonProductMediaProvider.lookup_by_asin",
        lambda self, asin, **kwargs: {"status": "blocked", "local_asset_ids": []},
    )
    result = VineImportService().fetch_media(
        db_session,
        batch=batch,
        item_ids=[item.id for item in db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id).all()],
    )
    assert (result.get("blocked", 0) + result.get("manual_review_needed", 0)) >= 1


def test_cached_media_url_lookup_is_safe_when_cache_missing_or_partial(db_session, monkeypatch, tmp_path):
    service = VineImportService()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    assert service._lookup_cached_media_urls(db_session, None) == []
    assert service._lookup_cached_media_urls(db_session, "B000MISS00") == []

    db_session.add(
        ProductMediaCache(
            asin="B000TEST10",
            marketplace_region="US",
            primary_image_url=_write_cached_media_file(tmp_path, "primary.jpg"),
        )
    )
    db_session.commit()
    assert service._lookup_cached_media_urls(db_session, "B000TEST10") == ["/media/primary.jpg"]


def test_cached_media_url_lookup_skips_missing_local_media(db_session, monkeypatch, tmp_path):
    service = VineImportService()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    db_session.add(
        ProductMediaCache(
            asin="B000MISS11",
            marketplace_region="US",
            fetch_status="fetched",
            primary_image_url="/media/amazon-vine/missing-primary.jpg",
            gallery_image_urls_json=["/media/amazon-vine/missing-gallery.jpg"],
        )
    )
    db_session.commit()
    assert service._lookup_cached_media_urls(db_session, "B000MISS11") == []


def test_amazon_media_provider_uses_owner_user_and_region(monkeypatch, db_session):
    user = User(email=f"media-owner-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    provider = AmazonProductMediaProvider(db_session, owner_user_id=user.id)
    assert provider.get_product_url("B000TEST01", region="CA") == "https://www.amazon.ca/dp/B000TEST01"
    assert provider.get_product_url("B000TEST01", region="UK") == "https://www.amazon.co.uk/dp/B000TEST01"

    class FakeResponse:
        status_code = 200
        text = '<meta property="og:image" content="https://m.media-amazon.com/images/I/primary.jpg" />'

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            return FakeResponse()

    monkeypatch.setattr("app.services.amazon_media.httpx.Client", FakeClient)
    monkeypatch.setattr(
        provider.storage,
        "save_from_url",
        lambda url, prefix="amazon-vine", suggested_basename=None: f"/tmp/storage/{prefix}/cached.jpg",
    )

    result = provider._lookup_from_product_page("B000TEST01")
    images = db_session.query(Image).filter(Image.user_id == user.id).all()
    assert result["status"] == "fetched"
    assert result["gallery_image_urls"] == ["/media/amazon-vine/cached.jpg"]
    assert len(images) == 1
    assert images[0].user_id == user.id


def test_listing_draft_hides_order_number_from_public_text(db_session):
    user = User(email=f"draft-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    items = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id, VineImportItem.asin == "B000TEST03").all()
    service.create_inventory_records(db_session, batch=batch, item_ids=[item.id for item in items], include_locked=True)
    service.create_listing_drafts(db_session, batch=batch, item_ids=[item.id for item in items])
    listing = db_session.query(Listing).filter(Listing.source_type == "amazon_vine", Listing.user_id == user.id).first()
    assert listing is not None
    assert "777-8888888-9999999" not in (listing.title or "")
    assert "777-8888888-9999999" not in (listing.description or "")
    assert listing.source_metadata["order_number"] == "777-8888888-9999999"


def test_unauthorized_users_cannot_access_vine_endpoints(db_session):
    settings.amazon_vine_import_enabled = True

    public_user = User(
        email=f"public-{uuid4()}@example.com",
        full_name="Public User",
        role="public",
        is_admin=False,
    )
    db_session.add(public_user)
    db_session.commit()
    db_session.refresh(public_user)
    with pytest.raises(HTTPException) as exc_info:
        list_vine_batches(db=db_session, current_user=public_user)
    assert exc_info.value.status_code == 403


def test_authorized_user_can_upload_xlsx_and_create_inventory_and_drafts(db_session):
    settings.amazon_vine_import_enabled = True

    owner = User(
        email=f"owner2-{uuid4()}@example.com",
        full_name="Owner User",
        role="owner",
        is_admin=True,
    )
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)

    batch = VineImportService().create_batch_from_upload(
        db_session,
        current_user=owner,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    items = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id).order_by(VineImportItem.id.asc()).all()
    assert batch.parsed_count == 4
    assert batch.cancelled_count >= 1
    assert any(item.eligibility_status == "cancelled" for item in items)

    eligible_ids = [item.id for item in items if item.eligibility_status == "eligible"]
    inventory = create_vine_inventory(
        batch_id=batch.id,
        payload=VineImportActionRequest(item_ids=eligible_ids, include_locked=True),
        db=db_session,
        current_user=owner,
    )
    assert inventory["created"] >= 1

    drafts = create_vine_drafts(
        batch_id=batch.id,
        payload=VineImportActionRequest(item_ids=eligible_ids, include_locked=True),
        db=db_session,
        current_user=owner,
    )
    assert drafts["created"] >= 1

    refreshed = list_vine_batches(db=db_session, current_user=owner)
    refreshed_batch = next(item for item in refreshed if item.id == batch.id)
    assert refreshed_batch.parsed_count == 4

    stored_items = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id, VineImportItem.eligibility_status == "eligible").all()
    assert any(item.listing_id for item in stored_items)
    assert any(item.inventory_item_id for item in stored_items)


def test_created_vine_records_keep_source_metadata_and_needs_photos(db_session):
    user = User(email=f"smoke-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    eligible_items = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id, VineImportItem.eligibility_status == "eligible").all()
    service.create_inventory_records(db_session, batch=batch, item_ids=[item.id for item in eligible_items], include_locked=True)
    service.create_listing_drafts(db_session, batch=batch, item_ids=[item.id for item in eligible_items])

    created = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine").all()
    assert created
    assert all(listing.source_metadata.get("order_number") for listing in created)
    assert any("needs_photos" in (listing.custom_labels or []) for listing in created)
    enriched = next((listing for listing in created if (listing.source_metadata or {}).get("asin") != "B000TEST02"), created[0])
    marketplace_data = enriched.marketplace_data or {}
    assert marketplace_data.get("vine_ready_for_approval") is True
    assert "ebay" in (marketplace_data.get("targets") or [])
    assert "facebook" in (marketplace_data.get("targets") or [])
    draft_previews = marketplace_data.get("draft_previews") or {}
    assert draft_previews.get("ebay", {}).get("marketplace") == "ebay"
    assert draft_previews.get("facebook", {}).get("marketplace") == "facebook"
    assert "Model" in (enriched.item_specifics or {})
    assert (enriched.shipping_profile or {}).get("estimated") is True
    assert (enriched.shipping_profile or {}).get("manual_measurement_needed") is False
    provenance = marketplace_data.get("ebay_item_specifics_provenance") or {}
    assert provenance
    assert {"Brand", "Type", "Model"}.issubset(set(provenance))
    assert draft_previews.get("ebay", {}).get("item_specifics_provenance") == provenance


def test_create_listing_drafts_includes_locked_and_restricted_rows_for_review(db_session):
    user = User(email=f"vine-review-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    items = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id).all()
    target_items = [item for item in items if item.eligibility_status != "cancelled"]
    assert target_items

    result = service.create_listing_drafts(
        db_session,
        batch=batch,
        item_ids=[item.id for item in target_items],
    )
    assert (result["created"] + result["updated"]) >= len(target_items)

    locked_item = next(item for item in target_items if item.eligibility_status.startswith("locked_until_"))
    restricted_item = next(item for item in target_items if item.restricted_review_required)
    refreshed_locked = db_session.get(VineImportItem, locked_item.id)
    refreshed_restricted = db_session.get(VineImportItem, restricted_item.id)
    assert refreshed_locked is not None and refreshed_locked.listing_id is not None
    assert refreshed_restricted is not None and refreshed_restricted.listing_id is not None


def test_create_listing_drafts_can_include_cancelled_rows_when_requested(db_session):
    user = User(email=f"vine-cancelled-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    items = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id).all()
    cancelled = next(item for item in items if item.eligibility_status == "cancelled")

    result = service.create_listing_drafts(
        db_session,
        batch=batch,
        item_ids=[cancelled.id],
        include_cancelled=True,
        fetch_media_first=False,
    )

    assert result["skipped"] == 0
    refreshed = db_session.get(VineImportItem, cancelled.id)
    assert refreshed is not None and refreshed.listing_id is not None


def test_fetch_media_with_lookup_disabled_sets_manual_only_and_drafts_mark_needs_photos(db_session):
    settings.amazon_media_lookup_enabled = False
    settings.amazon_media_page_fallback_enabled = False

    user = User(email=f"vine-manual-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    eligible_items = (
        db_session.query(VineImportItem)
        .filter(VineImportItem.batch_id == batch.id, VineImportItem.eligibility_status == "eligible", VineImportItem.asin.is_not(None))
        .all()
    )
    assert eligible_items

    service.fetch_media(db_session, batch=batch, item_ids=[item.id for item in eligible_items])
    refreshed = db_session.query(VineImportItem).filter(VineImportItem.id == eligible_items[0].id).one()
    assert refreshed.media_status == "manual_only"

    drafts = service.create_listing_drafts(
        db_session,
        batch=batch,
        item_ids=[item.id for item in eligible_items],
        fetch_media_first=False,
        require_media_for_asin=True,
        allow_drafts_without_media=False,
    )
    assert drafts["created"] == 0
    assert drafts["skipped"] >= 1
    created_listings = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine").all()
    assert created_listings
    assert all("needs_photos" in (listing.custom_labels or []) for listing in created_listings)


def test_create_listing_drafts_attaches_cached_amazon_images_and_is_idempotent(db_session):
    settings.amazon_media_lookup_enabled = True
    settings.amazon_media_page_fallback_enabled = True

    user = User(email=f"vine-cached-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    eligible_items = (
        db_session.query(VineImportItem)
        .filter(VineImportItem.batch_id == batch.id, VineImportItem.eligibility_status == "eligible", VineImportItem.asin.is_not(None))
        .all()
    )
    assert eligible_items

    asin = eligible_items[0].asin
    assert asin
    db_session.add(
        ProductMediaCache(
            asin=asin,
            marketplace_region=settings.amazon_marketplace_region.upper(),
            fetch_status="fetched",
            primary_image_url="/media/amazon-vine/primary.jpg",
            gallery_image_urls_json=["/media/amazon-vine/primary.jpg", "/media/amazon-vine/alt.jpg"],
            local_asset_ids_json=[1, 2],
        )
    )
    db_session.commit()

    first = service.create_listing_drafts(
        db_session,
        batch=batch,
        item_ids=[item.id for item in eligible_items],
        fetch_media_first=False,
        require_media_for_asin=True,
        allow_drafts_without_media=False,
    )
    assert first["created"] >= 1
    created_listings = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine").all()
    assert created_listings
    assert all((listing.image_urls or []) for listing in created_listings)
    assert all("needs_photos" not in (listing.custom_labels or []) for listing in created_listings)
    assert all((listing.listing_images or []) for listing in created_listings)
    assert all(
        image.get("operator_state") == "approved" and image.get("is_reference") is False
        for listing in created_listings
        for image in (listing.listing_images or [])
    )

    second = service.create_listing_drafts(
        db_session,
        batch=batch,
        item_ids=[item.id for item in eligible_items],
        fetch_media_first=False,
        require_media_for_asin=True,
        allow_drafts_without_media=False,
    )
    assert second["created"] == 0


def test_create_listing_drafts_uses_amazon_discovery_for_images_and_description(monkeypatch, db_session):
    user = User(email=f"vine-no-fallback-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    eligible_item = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id, VineImportItem.eligibility_status == "eligible").first()
    assert eligible_item is not None
    service.create_inventory_records(db_session, batch=batch, item_ids=[eligible_item.id], include_locked=True)

    def _fake_discover_for_vine_item(*args, **kwargs):  # noqa: ARG001
        return {
            "status": "matched",
            "confidence": "high",
            "asin": eligible_item.asin,
            "title": "Desk Lamp",
            "source_page_url": f"https://www.amazon.com/dp/{eligible_item.asin}",
            "images": ["/media/amazon-vine/good.jpg"],
            "local_asset_ids": [],
            "image_status": "fetched",
            "description": "Amazon page description with useful product details.",
        }

    monkeypatch.setattr("app.services.amazon_product_discovery.AmazonProductDiscoveryService.discover_for_vine_item", _fake_discover_for_vine_item)

    result = service.create_listing_drafts(
        db_session,
        batch=batch,
        item_ids=[eligible_item.id],
        fetch_media_first=False,
        allow_drafts_without_media=True,
    )
    assert result["created"] == 1
    listing = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine", Listing.id == eligible_item.listing_id).one()
    assert listing.image_urls == ["/media/amazon-vine/good.jpg"]
    assert listing.listing_images
    assert listing.listing_images[0]["operator_state"] == "approved"
    assert listing.listing_images[0]["is_reference"] is False
    assert "vine" not in (listing.description or "").lower()
    assert listing.condition == "New"


def test_vine_draft_uses_amazon_facts_for_category_price_shipping_and_rewritten_copy(monkeypatch, db_session):
    user = User(email=f"vine-facts-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    service = VineImportService()
    batch = VineImportBatch(user_id=user.id, filename="vine.xlsx", source_type="xlsx")
    db_session.add(batch)
    db_session.flush()
    item = VineImportItem(
        batch_id=batch.id,
        user_id=user.id,
        asin="B0POOL0001",
        product_name="Portable Pool Pump with Filter",
        category="Collectibles > Cameras",
        estimated_tax_value=0,
        eligibility_status="eligible",
    )
    db_session.add(item)
    db_session.commit()

    monkeypatch.setattr(
        AmazonProductDiscoveryService,
        "discover_for_vine_item",
        lambda *args, **kwargs: {
            "status": "matched", "confidence": "high", "asin": "B0POOL0001",
            "source_page_url": "https://www.amazon.com/dp/B0POOL0001",
            "images": ["/media/amazon-vine/pool-pump.jpg"], "local_asset_ids": [], "image_status": "fetched",
            "description": "Original Amazon copy that should not be pasted word for word.",
            "product_facts": {
                "title": "AquaFlow Portable Pool Pump with Filter",
                "current_price": 8.99,
                "feature_bullets": ["Compact filter pump for above-ground pools"],
                "specifications": {"Power Source": "Corded electric"},
                "breadcrumbs": ["Patio, Lawn & Garden", "Pools & Spas", "Pool Pumps"],
            },
        },
    )

    result = service.create_listing_drafts(db_session, batch=batch, item_ids=[item.id], allow_drafts_without_media=True)
    assert result["created"] == 1
    listing = db_session.get(Listing, item.listing_id)
    assert listing is not None
    assert listing.category_suggestion.endswith("Pool Pumps")
    assert listing.listing_price == 8.99
    assert listing.suggested_price == 8.99
    assert listing.marketplace_data["shipping"]["buyer_pays_shipping"] is True
    assert listing.marketplace_data["shipping"]["free_shipping"] is False
    assert "Original Amazon copy" not in (listing.description or "")
    assert listing.source_metadata["amazon_product_facts"]["current_price"] == 8.99


def test_vine_draft_uses_seller_paid_shipping_at_ten_or_more(monkeypatch, db_session):
    user = User(email=f"vine-shipping-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    service = VineImportService()
    batch = VineImportBatch(user_id=user.id, filename="vine.xlsx", source_type="xlsx")
    db_session.add(batch)
    db_session.flush()
    item = VineImportItem(batch_id=batch.id, user_id=user.id, asin="B0PRICE001", product_name="Desk Lamp", estimated_tax_value=0, eligibility_status="eligible")
    db_session.add(item)
    db_session.commit()
    monkeypatch.setattr(AmazonProductDiscoveryService, "discover_for_vine_item", lambda *args, **kwargs: {
        "status": "matched", "asin": item.asin, "images": [], "local_asset_ids": [], "image_status": "pending",
        "description": "", "product_facts": {"current_price": 10.00, "title": "Desk Lamp"},
    })
    service.create_listing_drafts(db_session, batch=batch, item_ids=[item.id], allow_drafts_without_media=True)
    listing = db_session.get(Listing, item.listing_id)
    assert listing is not None
    assert listing.marketplace_data["shipping"]["buyer_pays_shipping"] is False
    assert listing.marketplace_data["shipping"]["free_shipping"] is True


def test_vine_category_and_pricing_policy_uses_product_facts_without_etv():
    service = VineImportService()
    pool_pump = VineImportItem(
        product_name="Pool Booster Pump Compatible With Pools",
        category="Collectibles > Cameras",
        estimated_tax_value=0,
    )
    category, source = service._resolve_category(pool_pump, amazon_facts={"current_price": 287.0})
    pricing = service._pricing_from_amazon(pool_pump, amazon_facts={"current_price": 8.99})
    assert category.endswith("Pool Pumps")
    assert source == "keyword_rules"
    assert pricing["listing_price"] == 8.99
    assert pricing["price_source"] == "amazon_current_price"


def test_shared_shipping_policy_charges_buyer_below_ten_dollars():
    low = derive_shipping_profile(listing={"listing_price": 9.99})
    regular = derive_shipping_profile(listing={"listing_price": 10.00})
    assert low["buyer_pays_shipping"] is True
    assert low["free_shipping"] is False
    assert regular["buyer_pays_shipping"] is False
    assert regular["free_shipping"] is True


def test_vine_pricing_prefers_current_amazon_price_over_etv():
    item = VineImportItem(product_name="Example", estimated_tax_value=46.82, asin="B000000000")
    pricing = VineImportService()._pricing_from_amazon(item, amazon_facts={"current_price": 41.0})
    assert pricing["listing_price"] == 41.0
    assert pricing["price_source"] == "amazon_current_price"


def test_vine_normalized_facts_detects_substantive_evidence():
    from app.services.vine_import_service import _facts_have_content
    assert _facts_have_content({"current_price": 41.0})
    assert not _facts_have_content({"title": "", "feature_bullets": [], "specifications": {}})


def test_repair_vine_listing_images_replaces_unsafe_sources_with_amazon_cache(db_session, monkeypatch, tmp_path):
    user = User(email=f"vine-repair-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    monkeypatch.setattr(settings, "storage_root", tmp_path)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    eligible_item = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id, VineImportItem.eligibility_status == "eligible").first()
    assert eligible_item is not None
    service.create_inventory_records(db_session, batch=batch, item_ids=[eligible_item.id], include_locked=True)
    service.create_listing_drafts(db_session, batch=batch, item_ids=[eligible_item.id], allow_drafts_without_media=True)

    listing = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine", Listing.id == eligible_item.listing_id).one()
    listing.listing_images = normalize_listing_images(
        listing_images=[
            {
                "storage_path": "/media/vine-search-auto/bad.jpg",
                "source_url": "https://example.invalid/bad.jpg",
                "source_platform": "amazon",
                "operator_state": "suggested",
                "is_reference": True,
            }
        ],
        approved=False,
        default_is_reference=True,
        source_platform="amazon",
    )
    listing.image_urls = ["/media/vine-search-auto/bad.jpg"]
    cache = db_session.query(ProductMediaCache).filter(ProductMediaCache.asin == eligible_item.asin).first()
    if cache is None:
        cache = ProductMediaCache(
            asin=eligible_item.asin,
            marketplace_region=settings.amazon_marketplace_region.upper(),
        )
    cache.fetch_status = "fetched"
    cache.source_provider = "page_metadata"
    cache.primary_image_url = _write_cached_media_file(tmp_path, "amazon-vine/good.jpg")
    cache.gallery_image_urls_json = [
        _write_cached_media_file(tmp_path, "amazon-vine/good.jpg"),
        _write_cached_media_file(tmp_path, "amazon-vine/second.jpg"),
    ]
    cache.local_asset_ids_json = []
    db_session.add(cache)
    db_session.commit()

    result = service.repair_vine_listing_images(
        db_session,
        user_id=user.id,
        listing_ids=[listing.id],
        include_archived=False,
        force_refresh=True,
        use_bridge_session=False,
    )
    refreshed = db_session.get(Listing, listing.id)
    assert refreshed is not None
    assert result["updated"] == 1
    assert all("vine-search" not in str(image.get("storage_path") or "") for image in (refreshed.listing_images or []))
    assert refreshed.image_urls and refreshed.image_urls[0] == "/media/amazon-vine/good.jpg"
    assert refreshed.listing_images
    assert all(image.get("operator_state") == "approved" for image in (refreshed.listing_images or []))
    assert all(image.get("is_reference") is False for image in (refreshed.listing_images or []))
    assert "vine" not in (refreshed.description or "").lower()
    assert refreshed.condition == "New"
    assert refreshed.needs_review is True
    assert getattr(refreshed.status, "value", str(refreshed.status)).lower() == "processed"


def test_repair_vine_listing_images_is_scoped_to_requested_batch(db_session, monkeypatch):
    user = User(email=f"vine-batch-scope-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    batch_a = VineImportBatch(user_id=user.id, filename="first.xlsx", source_type="xlsx")
    batch_b = VineImportBatch(user_id=user.id, filename="second.xlsx", source_type="xlsx")
    listing_a = Listing(user_id=user.id, title="First batch item", source_type="amazon_vine")
    listing_b = Listing(user_id=user.id, title="Second batch item", source_type="amazon_vine")
    db_session.add_all([batch_a, batch_b, listing_a, listing_b])
    db_session.flush()
    item_a = VineImportItem(batch_id=batch_a.id, user_id=user.id, asin="B000SCOPE1", product_name="First", eligibility_status="eligible", listing_id=listing_a.id)
    item_b = VineImportItem(batch_id=batch_b.id, user_id=user.id, asin="B000SCOPE2", product_name="Second", eligibility_status="eligible", listing_id=listing_b.id)
    db_session.add_all([item_a, item_b])
    db_session.commit()

    seen = []
    def _discover(self, *, asin=None, **kwargs):  # noqa: ANN001
        seen.append(asin)
        return {}

    monkeypatch.setattr(AmazonProductDiscoveryService, "discover_for_vine_item", _discover)
    result = VineImportService().repair_vine_listing_images(
        db_session,
        user_id=user.id,
        batch_id=batch_a.id,
        force_refresh=True,
        use_bridge_session=False,
    )
    assert result["processed"] == 1
    assert seen == ["B000SCOPE1"]


def test_repair_vine_listing_images_since_order_date_targets_recent_rows(db_session, monkeypatch):
    user = User(email=f"vine-recent-scope-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    batch = VineImportBatch(user_id=user.id, filename="recent.xlsx", source_type="xlsx")
    recent_listing = Listing(user_id=user.id, title="Recent batch item", source_type="amazon_vine")
    older_listing = Listing(user_id=user.id, title="Older batch item", source_type="amazon_vine")
    db_session.add_all([batch, recent_listing, older_listing])
    db_session.flush()
    recent_item = VineImportItem(
        batch_id=batch.id,
        user_id=user.id,
        asin="B000RECENT1",
        product_name="Recent Item",
        order_date=date(2026, 6, 15),
        eligibility_status="eligible",
        listing_id=recent_listing.id,
    )
    older_item = VineImportItem(
        batch_id=batch.id,
        user_id=user.id,
        asin="B000OLDER1",
        product_name="Older Item",
        order_date=date(2026, 6, 14),
        eligibility_status="eligible",
        listing_id=older_listing.id,
    )
    db_session.add_all([recent_item, older_item])
    db_session.commit()

    seen: list[str | None] = []

    def _discover(self, *, asin=None, **kwargs):  # noqa: ANN001
        seen.append(asin)
        return {}

    monkeypatch.setattr(AmazonProductDiscoveryService, "discover_for_vine_item", _discover)
    result = VineImportService().repair_vine_listing_images(
        db_session,
        user_id=user.id,
        since_order_date=date(2026, 6, 15),
        force_refresh=True,
        use_bridge_session=False,
    )
    assert result["processed"] == 1
    assert seen == ["B000RECENT1"]


def test_auto_build_batch_drafts_force_refreshes_recent_vine_images(db_session, monkeypatch):
    user = User(email=f"vine-autobuild-recent-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    batch = VineImportBatch(user_id=user.id, filename="auto-build.xlsx", source_type="xlsx")
    db_session.add(batch)
    db_session.flush()
    recent_item = VineImportItem(
        batch_id=batch.id,
        user_id=user.id,
        asin="B000RECENT2",
        product_name="Recent Item",
        order_date=date(2026, 6, 15),
        eligibility_status="eligible",
    )
    older_item = VineImportItem(
        batch_id=batch.id,
        user_id=user.id,
        asin="B000OLDER2",
        product_name="Older Item",
        order_date=date(2026, 6, 14),
        eligibility_status="eligible",
    )
    db_session.add_all([recent_item, older_item])
    db_session.commit()

    calls: list[dict[str, object]] = []

    def _fake_create_listing_drafts(self, db, *, batch, item_ids, include_cancelled, fetch_media_first, require_media_for_asin, allow_drafts_without_media):  # noqa: ANN001
        assert fetch_media_first is True
        for item_id in item_ids:
            item = db.get(VineImportItem, item_id)
            if item is not None:
                item.listing_id = item_id + 1000
                db.add(item)
        db.commit()
        return {"created": len(item_ids), "updated": 0, "skipped": 0, "created_listing_ids": [item_id + 1000 for item_id in item_ids]}

    def _fake_repair(self, db, **kwargs):  # noqa: ANN001
        calls.append({
            "listing_ids": kwargs.get("listing_ids"),
            "force_refresh": kwargs.get("force_refresh"),
            "only_missing_images": kwargs.get("only_missing_images"),
            "since_order_date": kwargs.get("since_order_date"),
        })
        return {
            "updated": 0,
            "removed_unsafe": 0,
            "already_present": 0,
            "missing_asin": 0,
            "no_cache": 0,
            "bridge_refetched": 0,
            "bridge_failed": 0,
            "total_vine_listings": 0,
            "processed": 0,
            "listing_ids": kwargs.get("listing_ids") or [],
            "batch_id": kwargs.get("batch_id"),
        }

    monkeypatch.setattr(VineImportService, "create_listing_drafts", _fake_create_listing_drafts)
    monkeypatch.setattr(VineImportService, "repair_vine_listing_images", _fake_repair)

    result = VineImportService().auto_build_batch_drafts(db_session, batch=batch, new_only=True, include_cancelled=True)
    assert result["draft_result"]["created"] == 2
    assert len(calls) == 3
    assert calls[0]["force_refresh"] is True
    assert calls[0]["only_missing_images"] is False
    assert calls[0]["since_order_date"] == date(2026, 6, 15)
    assert calls[0]["listing_ids"] == [1001]
    assert calls[1]["force_refresh"] is False
    assert calls[1]["only_missing_images"] is True
    assert calls[1]["since_order_date"] is None
    assert calls[1]["listing_ids"] == [1002]
    assert calls[2]["force_refresh"] is True
    assert calls[2]["only_missing_images"] is True
    assert calls[2]["listing_ids"] == [1001, 1002]


def test_create_listing_drafts_fetch_media_first_uses_bridge_fallback_when_images_missing(db_session, monkeypatch, tmp_path):
    user = User(email=f"vine-fetch-media-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    batch = VineImportBatch(user_id=user.id, filename="fetch-media.xlsx", source_type="xlsx")
    db_session.add(batch)
    db_session.flush()
    item = VineImportItem(
        batch_id=batch.id,
        user_id=user.id,
        asin="B000FETCH1",
        product_name="Fetch Media Item",
        eligibility_status="eligible",
    )
    db_session.add(item)
    db_session.commit()

    bridged: dict[str, object] = {}

    def _discover(self, *, asin=None, product_name=None, manual_url=None):  # noqa: ANN001
        return {"asin": asin or "B000FETCH1", "status": "ok", "images": [], "product_facts": {}}

    def _bridge_capture(self, db, asin, title_hint):  # noqa: ANN001
        primary = _write_cached_media_file(tmp_path, f"amazon-vine/{asin.lower()}-primary.jpg")
        secondary = _write_cached_media_file(tmp_path, f"amazon-vine/{asin.lower()}-secondary.jpg")
        cache = ProductMediaCache(
            asin=asin,
            marketplace_region=settings.amazon_marketplace_region.upper(),
            fetch_status="fetched",
            source_provider="bridge_browser",
            primary_image_url=primary,
            gallery_image_urls_json=[primary, secondary],
        )
        db.add(cache)
        db.flush()
        bridged["asin"] = asin
        bridged["title_hint"] = title_hint
        return cache

    monkeypatch.setattr(AmazonProductDiscoveryService, "discover_for_vine_item", _discover)
    monkeypatch.setattr(VineImportService, "_bridge_capture_for_asin", _bridge_capture)

    result = VineImportService().create_listing_drafts(
        db_session,
        batch=batch,
        item_ids=[item.id],
        fetch_media_first=True,
        allow_drafts_without_media=True,
    )
    db_session.refresh(item)
    listing = db_session.get(Listing, item.listing_id)
    assert result["created"] == 1
    assert bridged["asin"] == "B000FETCH1"
    assert listing is not None
    assert listing.image_urls == ["/media/amazon-vine/b000fetch1-primary.jpg", "/media/amazon-vine/b000fetch1-secondary.jpg"]
    assert listing.listing_images
    assert all(image.get("operator_state") == "approved" for image in listing.listing_images)


def test_repair_all_vine_listing_images_chunks_the_full_catalog(db_session, monkeypatch):
    user = User(email=f"vine-repair-all-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    listings = [
        Listing(user_id=user.id, source_type="amazon_vine", title=f"Vine item {index}", custom_labels=["amazon_vine"])
        for index in range(3)
    ]
    db_session.add_all(listings)
    db_session.commit()

    calls: list[list[int]] = []

    def _fake_repair(self, db, **kwargs):  # noqa: ANN001
        calls.append(list(kwargs.get("listing_ids") or []))
        return {
            "updated": len(kwargs.get("listing_ids") or []),
            "removed_unsafe": 0,
            "already_present": 0,
            "missing_asin": 0,
            "no_cache": 0,
            "bridge_refetched": 0,
            "bridge_failed": 0,
            "total_vine_listings": len(kwargs.get("listing_ids") or []),
            "processed": len(kwargs.get("listing_ids") or []),
            "listing_ids": kwargs.get("listing_ids") or [],
        }

    monkeypatch.setattr(VineImportService, "repair_vine_listing_images", _fake_repair)
    result = VineImportService().repair_all_vine_listing_images(
        db_session,
        user_id=user.id,
        include_archived=False,
        force_refresh=True,
        use_bridge_session=True,
        only_missing_images=True,
        chunk_size=2,
    )
    assert result["total_vine_listings"] == 3
    assert result["updated"] == 3
    assert calls == [[listings[0].id, listings[1].id], [listings[2].id]]


def test_refresh_batch_drafts_from_stored_amazon_facts_sets_new_condition_and_policy_preferences(db_session):
    user = User(email=f"vine-refresh-metadata-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    batch = VineImportBatch(user_id=user.id, filename="refresh.xlsx", source_type="xlsx")
    listing = Listing(
        user_id=user.id,
        source_type="amazon_vine",
        title="Old title",
        description="Old description",
        category_suggestion="Collectibles > Cameras",
        condition="Used",
        source_metadata={
            "amazon_product_facts": {
                "title": "Pool Booster Pump",
                "feature_bullets": ["Powerful pump"],
                "specifications": {"Power Source": "Corded electric"},
                "breadcrumbs": ["Home & Garden", "Yard, Garden & Outdoor Living", "Pools & Spas", "Pool Pumps"],
            }
        },
        marketplace_data={},
        condition_data={"condition_bucket": "used", "operator_review_required": False},
    )
    db_session.add_all([batch, listing])
    db_session.flush()
    item = VineImportItem(
        batch_id=batch.id,
        user_id=user.id,
        asin="B000REFRESH",
        product_name="Pool Booster Pump",
        order_date=date(2026, 6, 15),
        eligibility_status="eligible",
        listing_id=listing.id,
    )
    db_session.add(item)
    db_session.commit()

    result = VineImportService().refresh_batch_drafts_from_stored_amazon_facts(db_session, batch=batch)
    refreshed = db_session.get(Listing, listing.id)
    assert result["updated"] == 1
    assert refreshed is not None
    assert refreshed.condition == "New"
    assert "Pool Pumps" in (refreshed.category_suggestion or "")
    assert refreshed.marketplace_data["policy_preferences"]["returns_accepted"] is False
    assert refreshed.marketplace_data["policy_preferences"]["return_policy_preference"] == "no_returns_accepted"
    assert refreshed.needs_review is True
    assert getattr(refreshed.status, "value", str(refreshed.status)).lower() == "processed"


def test_discover_for_vine_item_allows_title_search(db_session, monkeypatch):
    provider = AmazonProductMediaProvider(db_session, owner_user_id=None)
    service = AmazonProductDiscoveryService(provider)

    html = """
    <html>
      <body>
        <div data-asin="B000BAD000">
          <span class="a-size-medium a-color-base a-text-normal">Completely Different Item</span>
        </div>
        <div data-asin="B000GOOD01">
          <span class="a-size-medium a-color-base a-text-normal">Desk Lamp LED Table Light</span>
        </div>
      </body>
    </html>
    """

    class FakeResponse:
        status_code = 200
        text = html

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            return FakeResponse()

    lookup_calls = {"asin": None}

    def _fake_lookup_by_asin(self, asin, **kwargs):  # noqa: ARG001
        lookup_calls["asin"] = asin
        return {
            "status": "fetched",
            "primary_image_url": "/media/amazon-vine/good.jpg",
            "gallery_image_urls": ["/media/amazon-vine/good.jpg"],
            "local_asset_ids": [1],
            "description": None,
        }

    monkeypatch.setattr("app.services.amazon_product_discovery.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.amazon_media.AmazonProductMediaProvider.lookup_by_asin", _fake_lookup_by_asin)
    monkeypatch.setattr("app.services.amazon_media.AmazonProductMediaProvider.fetch_product_page_description", lambda self, asin, title_hint=None: "Title search description")

    result = service.discover_for_vine_item(asin=None, product_name="Desk Lamp", manual_url=None)
    assert result["asin"] == "B000GOOD01"
    assert lookup_calls["asin"] == "B000GOOD01"
    assert result["description"] == "Title search description"
    assert result["images"] == ["/media/amazon-vine/good.jpg"]


def test_discover_for_vine_item_falls_back_to_title_search_when_asin_page_has_no_images(monkeypatch, db_session):
    provider = AmazonProductMediaProvider(db_session, owner_user_id=None)
    service = AmazonProductDiscoveryService(provider)

    monkeypatch.setattr(
        "app.services.amazon_media.AmazonProductMediaProvider.lookup_by_asin",
        lambda self, asin, **kwargs: {
            "status": "blocked",
            "primary_image_url": None,
            "gallery_image_urls": [],
            "local_asset_ids": [],
            "description": None,
        },
    )
    monkeypatch.setattr(
        "app.services.amazon_media.AmazonProductMediaProvider.fetch_product_page_description",
        lambda self, asin, title_hint=None: None,
    )

    html = """
    <html>
      <body>
        <div data-asin="B000BAD000">
          <span class="a-size-medium a-color-base a-text-normal">Wrong Match Item</span>
        </div>
        <div data-asin="B000GOOD02">
          <span class="a-size-medium a-color-base a-text-normal">Desk Lamp LED Table Light</span>
        </div>
      </body>
    </html>
    """

    class FakeResponse:
        status_code = 200
        text = html

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url):
            return FakeResponse()

    monkeypatch.setattr("app.services.amazon_product_discovery.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.amazon_media.AmazonProductMediaProvider.lookup_by_asin", lambda self, asin, **kwargs: {
        "status": "blocked" if asin == "B000ASIN01" else "fetched",
        "primary_image_url": None if asin == "B000ASIN01" else "/media/amazon-vine/fallback.jpg",
        "gallery_image_urls": [] if asin == "B000ASIN01" else ["/media/amazon-vine/fallback.jpg"],
        "local_asset_ids": [],
        "description": None,
    })
    monkeypatch.setattr("app.services.amazon_media.AmazonProductMediaProvider.fetch_product_page_description", lambda self, asin, title_hint=None: "Title search description")

    result = service.discover_for_item(asin="B000ASIN01", product_name="Desk Lamp", manual_url=None, allow_title_search=True)
    assert result["asin"] == "B000GOOD02"
    assert result["images"] == ["/media/amazon-vine/fallback.jpg"] or result["images"] == ["/media/amazon-vine/good.jpg"]


def test_create_listing_drafts_resolves_title_only_rows_via_search(monkeypatch, db_session):
    user = User(email=f"vine-title-only-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    item = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id, VineImportItem.eligibility_status == "eligible").first()
    assert item is not None
    item.asin = None
    db_session.add(item)
    db_session.commit()

    def _fake_discover_for_vine_item(*args, **kwargs):  # noqa: ARG001
        return {
            "status": "matched",
            "confidence": "high",
            "asin": "B000TITLE1",
            "title": "Desk Lamp",
            "source_page_url": "https://www.amazon.com/dp/B000TITLE1",
            "images": ["/media/amazon-vine/title-match.jpg"],
            "local_asset_ids": [],
            "image_status": "fetched",
            "description": "Amazon page description with useful product details.",
        }

    monkeypatch.setattr("app.services.amazon_product_discovery.AmazonProductDiscoveryService.discover_for_vine_item", _fake_discover_for_vine_item)

    result = service.create_listing_drafts(
        db_session,
        batch=batch,
        item_ids=[item.id],
        fetch_media_first=False,
        allow_drafts_without_media=True,
    )
    assert result["created"] == 1
    refreshed_item = db_session.get(VineImportItem, item.id)
    assert refreshed_item is not None
    assert refreshed_item.asin == "B000TITLE1"
    listing = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine").first()
    assert listing is not None
    assert listing.image_urls == ["/media/amazon-vine/title-match.jpg"]
    assert "vine" not in (listing.description or "").lower()
    assert listing.condition == "New"


def test_is_unsafe_vine_image_flags_vine_search_last():
    assert _is_unsafe_vine_image({"storage_path": "storage/vine-search-last/example.jpg"})


def test_fetch_media_handles_missing_asin_gracefully(db_session):
    user = User(email=f"vine-missing-asin-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    item = VineImportItem(
        batch_id=batch.id,
        user_id=user.id,
        order_number="111-2222222-3333333",
        asin=None,
        product_name="No ASIN Item",
        order_type="ORDER",
        eligibility_status="eligible",
        media_status="pending",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    result = service.fetch_media(db_session, batch=batch, item_ids=[item.id])
    assert result["blocked"] == 0
    refreshed = db_session.query(VineImportItem).filter(VineImportItem.id == item.id).one()
    assert refreshed.media_status in {"search_failed", "no_search_match", "missing_identifiers", "search_http_error"}


def test_vine_duplicate_import_reuses_existing_listing(db_session):
    service = VineImportService()
    user = User(email=f"vine-dedupe-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    batch_a = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.csv",
        file_bytes=build_sample_csv(),
        reference_date=date(2026, 5, 5),
    )
    item_a = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch_a.id).first()
    first_result = service.create_listing_drafts(db_session, batch=batch_a, item_ids=[item_a.id], fetch_media_first=False)
    assert first_result["created"] == 1

    batch_b = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.csv",
        file_bytes=build_sample_csv(),
        reference_date=date(2026, 5, 5),
    )
    item_b = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch_b.id).first()
    second_result = service.create_listing_drafts(db_session, batch=batch_b, item_ids=[item_b.id], fetch_media_first=False)
    assert second_result["created"] == 0
    db_session.refresh(item_b)
    assert item_b.listing_id is not None
    created = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine").all()
    assert len(created) == 1


def test_real_batch_import_reuses_product_when_csv_row_moves(db_session, monkeypatch):
    service = VineImportService()
    user = User(email=f"vine-row-move-import-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user); db_session.commit(); db_session.refresh(user)
    header = "Product Title,ASIN,Order Date,Order Number,Brand,Category,Status,Review Deadline,Item URL,Estimated Tax Value\n"
    same = "Portable Work Light,B0ROWIMPORT1,01/01/2025,ROW-ORDER-1,Acme,Lighting,ordered,07/01/2025,https://www.amazon.com/dp/B0ROWIMPORT1,19.99\n"
    # Empty CSV records move the exact same source content from physical row
    # 999 to 1021 while parsing into the same stable Vine row identity.
    filler = ",,,,,,,,,,\n"
    first_payload = (header + filler * 997 + same).encode()
    moved_payload = (header + filler * 1019 + same).encode()
    assert first_payload.decode().splitlines().index(same.strip()) + 1 == 999
    assert moved_payload.decode().splitlines().index(same.strip()) + 1 == 1021

    def _discover(self, *, asin=None, **kwargs):  # noqa: ANN001
        return {"status": "fetched", "asin": asin, "title": "Portable Work Light", "images": ["https://images.example/portable-light.jpg"], "product_facts": {"title": "Portable Work Light", "brand": "Acme", "feature_bullets": ["Steel body", "Portable lighting"], "current_price": 19.99}}
    monkeypatch.setattr(AmazonProductDiscoveryService, "discover_for_vine_item", _discover)

    batch_a = service.create_batch_from_upload(db_session, current_user=user, filename="row-999.csv", file_bytes=first_payload, reference_date=date(2026, 5, 5))
    item_a = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch_a.id, VineImportItem.asin == "B0ROWIMPORT1").one()
    result_a = service.create_listing_drafts(db_session, batch=batch_a, item_ids=[item_a.id], fetch_media_first=False, allow_drafts_without_media=True)
    assert result_a["created"] == 1
    listing_a_id = item_a.listing_id

    batch_b = service.create_batch_from_upload(db_session, current_user=user, filename="row-1021.csv", file_bytes=moved_payload, reference_date=date(2026, 5, 5))
    item_b = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch_b.id, VineImportItem.asin == "B0ROWIMPORT1").one()
    assert service._is_duplicate_vine_item(item_b)
    result_b = service.create_listing_drafts(db_session, batch=batch_b, item_ids=[item_b.id], fetch_media_first=False, allow_drafts_without_media=True)
    db_session.refresh(item_b)
    assert result_b["created"] == 0 and item_b.listing_id == listing_a_id

    different = "Portable Work Light Pro,B0ROWIMPORT2,01/01/2025,ROW-ORDER-2,Acme,Lighting,ordered,07/01/2025,https://www.amazon.com/dp/B0ROWIMPORT2,19.99\n"
    different_payload = (header + filler * 1019 + different).encode()
    batch_c = service.create_batch_from_upload(db_session, current_user=user, filename="different-asin-row-1021.csv", file_bytes=different_payload, reference_date=date(2026, 5, 5))
    item_c = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch_c.id, VineImportItem.asin == "B0ROWIMPORT2").one()
    assert not service._is_duplicate_vine_item(item_c)
    result_c = service.create_listing_drafts(db_session, batch=batch_c, item_ids=[item_c.id], fetch_media_first=False, allow_drafts_without_media=True)
    assert result_c["created"] == 1
    listings = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine").all()
    assert len(listings) == 2 and item_c.listing_id != listing_a_id
    assert {item.batch_id for item in (item_a, item_b, item_c)} == {batch_a.id, batch_b.id, batch_c.id}


def test_vine_fingerprint_ignores_spreadsheet_row_position_and_distinguishes_asin():
    service = VineImportService()
    base = {
        "ASIN": "B0ROWMOVE01",
        "Product Name": "Example storage organizer",
        "Order Number": "111-2222222-3333333",
        "Order Type": "Vine",
        "Order Date": "2026-09-01",
        "Estimated Tax Value": "$12.00",
        "Brand": "Example",
        "Category": "Office",
        "Item URL": "https://www.amazon.com/dp/B0ROWMOVE01",
    }
    moved = {**base, "source_row": 1021}
    changed = {**base, "ASIN": "B0ROWMOVE02", "Item URL": "https://www.amazon.com/dp/B0ROWMOVE02"}
    assert service._vine_row_fingerprint(base) == service._vine_row_fingerprint(moved)
    assert service._vine_row_fingerprint(base) != service._vine_row_fingerprint(changed)


def test_synthetic_new_vine_batch_is_quality_ready_on_first_pass(db_session, monkeypatch):
    """Acceptance proof for the normal import path before repair beat work."""
    user = User(email=f"vine-synthetic-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.flush()
    batch = VineImportBatch(user_id=user.id, filename="synthetic-next.xlsx", source_type="xlsx")
    db_session.add(batch)
    db_session.flush()
    asins = ["B000SYNTHA", "B000SYNTHB", "B000SYNTHC", "B000SYNTHD", "B000SYNTHE", "B000SYNTHF"]
    names = ["Known Desk Lamp", "New Ceramic Mug", "Portable Pool Pump", "RV Organizer", "Durable Evidence Tool", "Image Missing Widget"]
    items = [VineImportItem(batch_id=batch.id, user_id=user.id, asin=asin, product_name=name,
                            order_number=f"SYN-{idx}", order_type="ORDER", estimated_tax_value=etv,
                            eligibility_status="eligible") for idx, (asin, name, etv) in enumerate(zip(asins, names, [10, 20, 30, 45, 15, 12]), start=1)]
    db_session.add_all(items)
    db_session.commit()

    def _discover(self, *, asin=None, **kwargs):  # noqa: ANN001
        facts = {"title": names[asins.index(asin)], "brand": "Synthetic Brand",
                 "feature_bullets": ["Durable construction", "Designed for everyday use"],
                 "specifications": {"Material": "Steel"},
                 "current_price": 12.50 if asin == asins[3] else 24.99,
                 "product_type": "Pool Pump" if asin == asins[2] else "Accessory"}
        return {"status": "fetched", "asin": asin, "title": facts["title"],
                "source_page_url": f"https://amazon.example/dp/{asin}",
                "images": [] if asin == asins[5] else [f"https://images.example/{asin}.jpg"],
                "product_facts": facts}

    def _repair(self, db, **kwargs):  # noqa: ANN001
        return {"updated": 0, "listing_ids": kwargs.get("listing_ids") or []}

    def _preflight(self, db, listing, marketplace):  # noqa: ANN001
        blocked = not bool(listing.image_urls)
        return {"listing_id": listing.id, "marketplace": marketplace,
                "status": "blocked" if blocked else "ready_with_warnings",
                "blockers": ([{"code": "EBAY_IMAGE_URL_INVALID", "field": "image_urls", "message": "No usable source image"}] if blocked else []),
                "warnings": [] if blocked else [{"code": "EBAY_CATEGORY_METADATA_UNAVAILABLE", "message": "Synthetic taxonomy"}],
                "last_checked_at": None, "payload_preview": {"payload": {}}}

    monkeypatch.setattr(AmazonProductDiscoveryService, "discover_for_vine_item", _discover)
    monkeypatch.setattr(VineImportService, "repair_vine_listing_images", _repair)
    monkeypatch.setattr("app.services.marketplace_preflight.MarketplacePreflightService.preflight_listing", _preflight)

    result = VineImportService().auto_build_batch_drafts(db_session, batch=batch, new_only=False, include_cancelled=True)
    listings = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine").order_by(Listing.id).all()
    assert len(listings) == 6
    assert result["preflight_result"]["count"] == 6
    assert all(listing.needs_review for listing in listings if listing.image_urls)
    missing = next(listing for listing in listings if not listing.image_urls)
    assert missing.processing_state == "needs_attention" and missing.needs_review is False
    priced = next(listing for listing in listings if listing.title == "RV Organizer")
    assert priced.listing_price == 12.50  # current Amazon price beats ETV
    pool = next(listing for listing in listings if listing.title == "Portable Pool Pump")
    assert "Pool" in (pool.category_suggestion or "")
    assert len((pool.description or "").split()) >= 8


def test_first_pass_uses_real_preflight_contract_and_persists_ebay_cache(db_session, monkeypatch):
    user = User(email=f"vine-preflight-contract-{uuid4()}@example.com", role="owner", is_admin=True)
    user.settings_json = {"ebay_marketplace_policy_settings": {"payment_policy_id": "p", "fulfillment_policy_id": "f", "return_policy_id": "r", "merchant_location_key": "loc", "package_weight_required": False, "package_dimensions_required": False}}
    db_session.add(user); db_session.flush()
    account = MarketplaceAccount(user_id=user.id, marketplace="ebay", external_account_id="contract-account", access_token="token", refresh_token="refresh")
    db_session.add(account); db_session.commit()

    async def fake_account(_user_id, _db): return account
    async def fake_category(_listing, _account, marketplace_id="EBAY_US"): return {"categoryId": "30090", "categoryName": "Office Supplies"}
    async def fake_aspects(_db, _account, category_id, marketplace_id="EBAY_US", force_refresh=False): return ({"aspects": []}, "live", True)
    async def fake_policies(_token, marketplace_id="EBAY_US", create_if_missing=False): return {"paymentPolicyId": "p", "fulfillmentPolicyId": "f", "returnPolicyId": "r"}
    monkeypatch.setattr("app.services.ebay_service.get_or_refresh_account", fake_account)
    monkeypatch.setattr("app.services.ebay_service.suggest_ebay_category", fake_category)
    monkeypatch.setattr("app.services.ebay_service._cached_category_aspects", fake_aspects)
    monkeypatch.setattr("app.services.ebay_service.get_business_policy_ids", fake_policies)
    monkeypatch.setattr("app.services.ebay_service._build_ebay_image_urls", lambda _listing: ["https://images.example/contract.jpg"])
    monkeypatch.setattr(AmazonProductDiscoveryService, "discover_for_vine_item", lambda _self, *, asin=None, **kwargs: {"status": "fetched", "asin": asin, "title": "Contract item", "images": ["https://images.example/contract.jpg"], "product_facts": {"title": "Contract item", "brand": "Contract Co", "feature_bullets": ["Steel construction", "Compact design"], "current_price": 19.0}})
    monkeypatch.setattr(VineImportService, "repair_vine_listing_images", lambda _self, _db, **kwargs: {"updated": 0, "listing_ids": kwargs.get("listing_ids") or []})
    batch = VineImportBatch(user_id=user.id, filename="contract.csv", source_type="csv")
    db_session.add(batch); db_session.flush()
    item = VineImportItem(batch_id=batch.id, user_id=user.id, asin="B000CONTRACT", product_name="Contract item", order_number="CONTRACT-1", order_type="ORDER", eligibility_status="eligible", estimated_tax_value=19.0)
    db_session.add(item); db_session.commit()

    result = VineImportService().auto_build_batch_drafts(db_session, batch=batch, new_only=False, include_cancelled=True)
    assert result["preflight_result"]["count"] == 1
    listing_id = result["listing_ids"][0]
    refreshed = db_session.get(Listing, listing_id)
    fresh = result["preflight_result"]["results"][0]
    assert fresh["status"] in {"ready", "ready_with_warnings", "blocked"}
    cached = (refreshed.marketplace_data or {}).get("marketplace_preflight", {}).get("by_marketplace", {}).get("ebay")
    assert cached is not None and cached["marketplace"] == "ebay"
    is_blocked = bool(fresh["blockers"])
    assert refreshed.processing_state == ("needs_attention" if is_blocked else "complete")
    assert refreshed.needs_review is (not is_blocked)
    assert bool(refreshed.processing_blocking_reason) is is_blocked


def test_vine_update_preserves_durable_facts_when_discovery_is_empty(db_session, monkeypatch):
    user = User(email=f"vine-durable-empty-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user); db_session.flush()
    batch = VineImportBatch(user_id=user.id, filename="durable.csv", source_type="csv")
    facts = {"title": "Durable Evidence Tool", "brand": "Evidence Co", "model": "DT-40", "current_price": 12.5, "product_type": "Workshop tool", "material": "Steel", "capacity": "40 lb", "included_components": ["mounting bracket", "hardware"], "feature_bullets": ["Steel construction", "40 lb rated capacity", "Includes mounting bracket and hardware"], "specifications": {"Material": "Steel", "Capacity": "40 lb", "Model": "DT-40"}}
    listing = Listing(user_id=user.id, source_type="amazon_vine", title="Old title", description="Durable original description with evidence.", listing_price=12.5, suggested_price=12.5, source_metadata={"amazon_product_facts": facts}, marketplace_data={"pricing_analysis": {"listing_price": 12.5}})
    db_session.add_all([batch, listing]); db_session.flush()
    item = VineImportItem(batch_id=batch.id, user_id=user.id, asin="B000DURABLE", product_name="Durable Evidence Tool", estimated_tax_value=45, listing_id=listing.id, inventory_item_id=listing.id, eligibility_status="eligible")
    db_session.add(item); db_session.commit()
    monkeypatch.setattr(AmazonProductDiscoveryService, "discover_for_vine_item", lambda *args, **kwargs: {"status": "fetch_failed", "asin": "B000DURABLE", "product_facts": {}, "images": []})
    VineImportService().create_listing_drafts(db_session, batch=batch, item_ids=[item.id], fetch_media_first=False, allow_drafts_without_media=True)
    refreshed = db_session.get(Listing, listing.id)
    assert refreshed.source_metadata["amazon_product_facts"]["brand"] == "Evidence Co"
    assert refreshed.listing_price == 12.5
    assert "Evidence Co" in (refreshed.description or "")
    from app.services.marketplace_preflight import MarketplacePreflightService
    service = MarketplacePreflightService()
    fresh = service.preflight_listing(db_session, refreshed, "ebay")
    assert fresh["marketplace"] == "ebay"
    assert not any(issue.get("code") == "DESCRIPTION_INADEQUATE" for issue in fresh["blockers"])


def test_vine_duplicate_import_rows_are_skipped_until_prior_listing_exists(db_session):
    service = VineImportService()
    user = User(email=f"vine-dup-skip-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    batch_a = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.csv",
        file_bytes=build_sample_csv(),
        reference_date=date(2026, 5, 5),
    )
    item_a = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch_a.id).first()
    assert item_a is not None

    batch_b = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.csv",
        file_bytes=build_sample_csv(),
        reference_date=date(2026, 5, 5),
    )
    item_b = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch_b.id).first()
    assert item_b is not None
    assert any(
        str(warning).lower().startswith("duplicate of prior vine import row") for warning in (item_b.parse_warnings_json or [])
    )

    result = service.create_listing_drafts(
        db_session,
        batch=batch_b,
        item_ids=[item_b.id],
        fetch_media_first=False,
        allow_drafts_without_media=True,
    )
    assert result["created"] == 0
    assert result["skipped"] == 1
    db_session.refresh(item_b)
    assert item_b.listing_id is None
    assert batch_b.stats_json and batch_b.stats_json.get("rows_duplicate") == 1


def test_vine_auto_build_processes_only_new_rows(db_session, monkeypatch):
    service = VineImportService()
    user = User(email=f"vine-auto-build-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    first_csv = (
        "Product Title,ASIN,Order Date,Order Number,Brand,Category,Status,Review Deadline,Item URL,Estimated Tax Value\n"
        "Desk Lamp,B000CSV001,01/01/2025,123-1234567-1234567,Acme,Lighting,ordered,07/01/2025,https://www.amazon.com/dp/B000CSV001,19.99\n"
    ).encode("utf-8")
    second_csv = (
        "Product Title,ASIN,Order Date,Order Number,Brand,Category,Status,Review Deadline,Item URL,Estimated Tax Value\n"
        "Desk Lamp,B000CSV001,01/01/2025,123-1234567-1234567,Acme,Lighting,ordered,07/01/2025,https://www.amazon.com/dp/B000CSV001,19.99\n"
        "Portable Fan,B000CSV002,01/02/2025,123-1234567-7654321,Breeze,Home,ordered,07/02/2025,https://www.amazon.com/dp/B000CSV002,24.99\n"
    ).encode("utf-8")

    batch_a = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.csv",
        file_bytes=first_csv,
        reference_date=date(2026, 5, 5),
    )
    item_a = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch_a.id).first()
    assert item_a is not None

    def _fake_discover_for_vine_item(*args, **kwargs):  # noqa: ARG001
        asin = str(kwargs.get("asin") or "").strip().upper()
        title = "Desk Lamp" if asin == "B000CSV001" else "Portable Fan"
        return {
            "status": "matched",
            "confidence": "high",
            "asin": asin or "B000CSV001",
            "title": title,
            "source_page_url": f"https://www.amazon.com/dp/{asin or 'B000CSV001'}",
            "images": [f"/media/amazon-vine/{(asin or 'B000CSV001').lower()}.jpg"],
            "local_asset_ids": [],
            "image_status": "fetched",
            "description": f"{title} product page description.",
        }

    monkeypatch.setattr("app.services.amazon_product_discovery.AmazonProductDiscoveryService.discover_for_vine_item", _fake_discover_for_vine_item)
    monkeypatch.setattr(
        VineImportService,
        "repair_vine_listing_images",
        lambda self, db, **kwargs: {  # noqa: ARG005
            "updated": 0,
            "removed_unsafe": 0,
            "already_present": len(kwargs.get("listing_ids") or []),
            "missing_asin": 0,
            "bridge_refetched": 0,
            "bridge_failed": 0,
            "listing_ids": kwargs.get("listing_ids") or [],
            "batch_id": kwargs.get("batch_id"),
        },
    )

    initial_result = service.create_listing_drafts(
        db_session,
        batch=batch_a,
        item_ids=[item_a.id],
        fetch_media_first=False,
        allow_drafts_without_media=True,
    )
    assert initial_result["created"] == 1

    batch_b = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.csv",
        file_bytes=second_csv,
        reference_date=date(2026, 5, 5),
    )
    result = service.auto_build_batch_drafts(db_session, batch=batch_b, new_only=True, include_cancelled=True)

    assert result["duplicates_skipped"] == 1
    assert result["draft_result"]["created"] == 1
    assert len(result["processed_item_ids"]) == 1
    assert len(result["listing_ids"]) == 1

    batch_b_items = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch_b.id).order_by(VineImportItem.id.asc()).all()
    assert len(batch_b_items) == 2
    duplicate_item = next(item for item in batch_b_items if item.asin == "B000CSV001")
    new_item = next(item for item in batch_b_items if item.asin == "B000CSV002")
    assert duplicate_item.listing_id is None
    assert new_item.listing_id is not None


def test_upload_vine_report_triggers_auto_build_new_only(db_session, monkeypatch):
    settings.amazon_vine_import_enabled = True
    owner = User(email=f"vine-upload-autobuild-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)

    seen: dict[str, object] = {}
    queued: dict[str, object] = {}

    def _fake_auto_build(db, *, batch, item_ids, new_only, include_cancelled):  # noqa: ANN001
        seen["batch_id"] = batch.id
        seen["item_ids"] = item_ids
        seen["new_only"] = new_only
        seen["include_cancelled"] = include_cancelled
        batch.stats_json = {
            **(batch.stats_json or {}),
            "auto_build_processed": 1,
        }
        db.add(batch)
        db.commit()
        return {
            "batch_id": batch.id,
            "processed_item_ids": [],
            "listing_ids": [],
            "new_only": new_only,
            "duplicates_skipped": 0,
            "draft_result": {"created": 0, "updated": 0, "skipped": 0, "created_listing_ids": []},
            "repair_result": {"updated": 0, "removed_unsafe": 0, "already_present": 0, "missing_asin": 0, "bridge_refetched": 0, "bridge_failed": 0},
        }

    monkeypatch.setattr("app.api.vine_imports.service.auto_build_batch_drafts", _fake_auto_build)
    monkeypatch.setattr("app.api.vine_imports.repair_recent_vine_images_task.delay", lambda user_id: queued.setdefault("user_id", user_id))

    upload = UploadFile(filename="vine.xlsx", file=BytesIO(build_sample_xlsx()))
    batch = asyncio.run(upload_vine_report(file=upload, db=db_session, current_user=owner))

    assert seen["batch_id"] == batch.id
    assert seen["item_ids"] is None
    assert seen["new_only"] is True
    assert seen["include_cancelled"] is True
    assert batch.stats_json and batch.stats_json.get("auto_build_processed") == 1
    assert queued["user_id"] == owner.id


def test_auto_build_vine_drafts_triggers_background_retry(db_session, monkeypatch):
    user = User(email=f"vine-auto-build-retry-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    batch = VineImportBatch(user_id=user.id, filename="vine.xlsx", source_type="xlsx")
    db_session.add(batch)
    db_session.commit()
    db_session.refresh(batch)

    queued: dict[str, object] = {}

    def _fake_auto_build(db, *, batch, item_ids, new_only, include_cancelled):  # noqa: ANN001
        return {
            "batch_id": batch.id,
            "processed_item_ids": [],
            "listing_ids": [],
            "new_only": new_only,
            "duplicates_skipped": 0,
            "draft_result": {"created": 0, "updated": 0, "skipped": 0, "created_listing_ids": []},
            "repair_result": {"updated": 0, "removed_unsafe": 0, "already_present": 0, "missing_asin": 0, "bridge_refetched": 0, "bridge_failed": 0},
        }

    monkeypatch.setattr("app.api.vine_imports.service.auto_build_batch_drafts", _fake_auto_build)
    monkeypatch.setattr("app.api.vine_imports.repair_recent_vine_images_task.delay", lambda user_id: queued.setdefault("user_id", user_id))

    payload = VineImportActionRequest(new_only=True, include_cancelled=True)
    result = auto_build_vine_drafts(batch_id=batch.id, payload=payload, db=db_session, current_user=user)

    assert result["batch_id"] == batch.id
    assert queued["user_id"] == user.id


def test_upload_vine_report_emits_process_notifications(db_session, monkeypatch):
    settings.amazon_vine_import_enabled = True
    owner = User(email=f"vine-upload-notify-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)

    def _fake_auto_build(db, *, batch, item_ids, new_only, include_cancelled):  # noqa: ANN001
        batch.stats_json = {**(batch.stats_json or {}), "auto_build_processed": 1}
        db.add(batch)
        db.commit()
        return {
            "batch_id": batch.id,
            "processed_item_ids": [],
            "listing_ids": [],
            "new_only": new_only,
            "duplicates_skipped": 0,
            "draft_result": {"created": 0, "updated": 0, "skipped": 0, "created_listing_ids": []},
            "repair_result": {"updated": 0, "removed_unsafe": 0, "already_present": 0, "missing_asin": 0, "bridge_refetched": 0, "bridge_failed": 0},
        }

    monkeypatch.setattr("app.api.vine_imports.service.auto_build_batch_drafts", _fake_auto_build)

    upload = UploadFile(filename="vine.xlsx", file=BytesIO(build_sample_xlsx()))
    batch = asyncio.run(upload_vine_report(file=upload, db=db_session, current_user=owner))

    notifications = db_session.query(IntakeNotification).filter(IntakeNotification.user_id == owner.id).order_by(IntakeNotification.id.asc()).all()
    types = [notification.notification_type for notification in notifications]
    titles = [notification.title for notification in notifications]
    assert "vine_import" in types
    assert any("upload received" in title.lower() for title in titles)
    assert any("draft build started" in title.lower() for title in titles)
    assert any("draft build complete" in title.lower() for title in titles)
    assert batch.stats_json and batch.stats_json.get("auto_build_processed") == 1


def test_upload_vine_report_with_lock_enforcement_disabled_treats_locked_rows_as_informational(db_session):
    settings.amazon_vine_import_enabled = True
    owner = User(email=f"vine-upload-lock-off-{uuid4()}@example.com", role="owner", is_admin=True)
    owner.settings_json = {"vine_preferences": {"enforce_six_month_lock": False}}
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=owner,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )

    imported_rows = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id).all()
    assert imported_rows
    assert any(
        "lock enforcement is disabled" in " ".join(item.parse_warnings_json or []).lower()
        for item in imported_rows
    )


def test_repair_vine_images_route_maps_item_ids_to_listing_ids(db_session, monkeypatch):
    user = User(email=f"vine-route-repair-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    service = VineImportService()
    batch = service.create_batch_from_upload(
        db_session,
        current_user=user,
        filename="vine.xlsx",
        file_bytes=build_sample_xlsx(),
        reference_date=date(2026, 5, 5),
    )
    item = db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id, VineImportItem.eligibility_status == "eligible").first()
    assert item is not None
    service.create_inventory_records(db_session, batch=batch, item_ids=[item.id], include_locked=True)
    draft_result = service.create_listing_drafts(db_session, batch=batch, item_ids=[item.id], allow_drafts_without_media=True)
    assert draft_result["created"] == 1
    db_session.refresh(item)
    assert item.listing_id is not None

    captured = {}

    def _fake_repair(*args, **kwargs):
        captured["listing_ids"] = kwargs.get("listing_ids")
        return {"updated": 1, "listing_ids": kwargs.get("listing_ids") or []}

    monkeypatch.setattr("app.api.vine_imports.service.repair_vine_listing_images", _fake_repair)

    result = repair_vine_images(
        batch_id=batch.id,
        payload=VineImportActionRequest(item_ids=[item.id]),
        db=db_session,
        current_user=user,
    )

    assert captured["listing_ids"] == [item.listing_id]
    assert result["listing_ids"] == [item.listing_id]


def test_upload_vine_report_returns_400_for_unexpected_parse_errors(monkeypatch, db_session):
    settings.amazon_vine_import_enabled = True
    owner = User(email=f"vine-upload-{uuid4()}@example.com", role="owner", is_admin=True)
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)

    def _raise_unexpected(*args, **kwargs):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr("app.api.vine_imports.service.create_batch_from_upload", _raise_unexpected)
    upload = UploadFile(filename="vine.xlsx", file=BytesIO(build_sample_xlsx()))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(upload_vine_report(file=upload, db=db_session, current_user=owner))
    assert exc_info.value.status_code == 400
    assert "Vine report upload failed: parser exploded" in str(exc_info.value.detail)
