from __future__ import annotations

import zipfile
from datetime import date
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.api.schemas import VineImportActionRequest
from app.api.vine_imports import create_vine_drafts, create_vine_inventory, list_vine_batches
from app.models.models import Image, Listing, ProductMediaCache, User, VineImportItem
from app.services.amazon_media import AmazonProductMediaProvider
from app.services.vine_import_service import VineImportService
from app.services.vine_parser import calculate_vine_eligibility, parse_vine_pdf, parse_vine_xlsx
from app.services.vine_policy import review_vine_product


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


def build_sample_xlsx():
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

    monkeypatch.setattr("app.services.amazon_media.AmazonProductMediaProvider.lookup_by_asin", lambda self, asin: {"status": "blocked", "local_asset_ids": []})
    result = VineImportService().fetch_media(
        db_session,
        batch=batch,
        item_ids=[item.id for item in db_session.query(VineImportItem).filter(VineImportItem.batch_id == batch.id).all()],
    )
    assert result["blocked"] >= 1


def test_cached_media_url_lookup_is_safe_when_cache_missing_or_partial(db_session):
    service = VineImportService()
    assert service._lookup_cached_media_urls(db_session, None) == []
    assert service._lookup_cached_media_urls(db_session, "B000MISS00") == []

    db_session.add(ProductMediaCache(asin="B000TEST10", marketplace_region="US", primary_image_url="/media/primary.jpg"))
    db_session.commit()
    assert service._lookup_cached_media_urls(db_session, "B000TEST10") == ["/media/primary.jpg"]


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
        text = '<meta property="og:image" content="https://images.example.com/primary.jpg" />'

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
    monkeypatch.setattr(provider.storage, "save_from_url", lambda url, prefix="amazon-vine": f"/tmp/storage/{prefix}/cached.jpg")

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


def test_unauthorized_users_cannot_access_vine_endpoints(monkeypatch, db_session):
    monkeypatch.setattr(settings, "amazon_vine_import_enabled", True)

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


def test_authorized_user_can_upload_xlsx_and_create_inventory_and_drafts(monkeypatch, db_session):
    monkeypatch.setattr(settings, "amazon_vine_import_enabled", True)

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
    assert all(set((listing.marketplace_data or {}).get("targets") or []) >= {"ebay", "amazon"} for listing in created)
    assert any("needs_photos" in (listing.custom_labels or []) for listing in created)


def test_fetch_media_with_lookup_disabled_sets_manual_only_and_drafts_mark_needs_photos(monkeypatch, db_session):
    monkeypatch.setattr(settings, "amazon_media_lookup_enabled", False)
    monkeypatch.setattr(settings, "amazon_media_page_fallback_enabled", False)

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
    assert drafts["created"] >= 1
    created_listings = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine").all()
    assert created_listings
    assert any("needs_photos" in (listing.custom_labels or []) for listing in created_listings)


def test_create_listing_drafts_attaches_cached_amazon_images_and_is_idempotent(monkeypatch, db_session):
    monkeypatch.setattr(settings, "amazon_media_lookup_enabled", True)
    monkeypatch.setattr(settings, "amazon_media_page_fallback_enabled", True)

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
    assert first["listing_ids"]
    assert all(isinstance(listing_id, int) for listing_id in first["listing_ids"])
    created_listings = db_session.query(Listing).filter(Listing.user_id == user.id, Listing.source_type == "amazon_vine").all()
    assert created_listings
    assert all((listing.image_urls or []) for listing in created_listings)
    assert all("needs_photos" not in (listing.custom_labels or []) for listing in created_listings)
    stored_items = (
        db_session.query(VineImportItem)
        .filter(VineImportItem.batch_id == batch.id, VineImportItem.id.in_([item.id for item in eligible_items]))
        .all()
    )
    assert stored_items
    assert all(item.listing_id is not None for item in stored_items)
    db_session.refresh(batch)
    assert batch.drafts_created_count >= len(stored_items)

    second = service.create_listing_drafts(
        db_session,
        batch=batch,
        item_ids=[item.id for item in eligible_items],
        fetch_media_first=False,
        require_media_for_asin=True,
        allow_drafts_without_media=False,
    )
    assert second["created"] == 0
    assert set(second.get("listing_ids") or []) == set(first.get("listing_ids") or [])


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
    assert refreshed.media_status == "missing_asin"
