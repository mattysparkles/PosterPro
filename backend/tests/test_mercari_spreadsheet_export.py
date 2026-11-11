from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from app.services.mercari_spreadsheet_export import fill_official_workbook, official_workbook_category_rows


def _template(sheet_name="import_template"):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(["title", "description", "price", "category_id", "condition", "brand", "shipper_payer_name", "shipping_method", "weight", "weight_unit", "sku", "image_urls"])
    refs = wb.create_sheet("category_id")
    refs.append(["category_id", "category_name"])
    refs.append(["verified-42", "Home > Storage"])
    for name, value in [("item_condition_names", "Good"), ("shipper_payer_name", "buyer"), ("shipping_method", "USPS Ground Advantage")]:
        ref = wb.create_sheet(name); ref.append([name]); ref.append([value])
    out = BytesIO(); wb.save(out); return out.getvalue()


def test_fills_exact_official_sheet_columns_and_retains_reference_sheet_and_identity_map():
    listing = SimpleNamespace(
        id=4601, title="Canvas organizer", description="Foldable organizer", canonical_description=None,
        listing_price=24.5, marketplace_descriptions={}, shipping_profile={"mercari": {
            "category_id": "verified-42", "category_path": "Home > Storage", "category_status": "verified",
            "condition": "Good", "shipping_payer": "buyer", "shipping_service": "USPS Ground Advantage",
            "package_weight": 12.5, "package_weight_unit": "oz"}},
        item_specifics={"Brand": "Acme"}, image_urls=["https://img/1.jpg", "https://img/2.jpg"],
    )
    payload, manifest = fill_official_workbook(_template(), [listing])
    wb = load_workbook(BytesIO(payload), data_only=True)
    ws = wb["import_template"]
    assert ws.cell(2, 1).value == "Canvas organizer"
    assert ws.cell(2, 3).value == 24.5
    assert ws.cell(2, 4).value == "verified-42"
    assert ws.cell(2, 7).value == "buyer"
    assert ws.cell(2, 9).value == 12.5
    assert ws.cell(2, 12).value == "https://img/1.jpg\nhttps://img/2.jpg"
    assert "category_id" in wb.sheetnames
    assert wb["PosterPro mapping"].sheet_state == "hidden"
    assert wb["PosterPro mapping"].cell(2, 2).value == 4601
    assert manifest["exported_count"] == 1
    assert manifest["row_map"][0]["posterpro_listing_id"] == 4601


def test_rejects_changed_template_when_required_columns_are_missing():
    wb = Workbook(); wb.active.title = "import_template"
    wb.active.append(["title", "price", "category_id"])
    out = BytesIO(); wb.save(out)
    listing = SimpleNamespace(id=1, shipping_profile={"mercari": {}}, item_specifics={}, image_urls=[], title="x", listing_price=5,
                              description="x", canonical_description=None, marketplace_descriptions={})
    with pytest.raises(ValueError, match="missing required mapped columns"):
        fill_official_workbook(out.getvalue(), [listing])


def test_official_category_reference_import_keeps_spreadsheet_namespace_and_unknown_leaf():
    rows = official_workbook_category_rows(_template())
    category = next(row for row in rows if row["category_id"] == "verified-42")
    assert category["source_namespace"] == "mercari_spreadsheet_import"
    assert category["id_semantics"].startswith("official Mercari spreadsheet-import category ID")
    assert category["path"] == ["Home", "Storage"]
    assert category["is_leaf"] is None


def test_fills_official_listing_template_tab_when_import_template_alias_is_absent():
    listing = SimpleNamespace(
        id=4602, title="Canvas organizer", description="Foldable organizer", canonical_description=None,
        listing_price=24.5, marketplace_descriptions={}, shipping_profile={"mercari": {
            "category_id": "verified-42", "category_path": "Home > Storage", "category_status": "verified",
            "condition": "Good", "shipping_payer": "buyer", "shipping_service": "USPS Ground Advantage",
            "package_weight": 12.5, "package_weight_unit": "oz"}},
        item_specifics={"Brand": "Acme"}, image_urls=["https://img/1.jpg"],
    )
    payload, manifest = fill_official_workbook(_template("listing_template"), [listing])
    wb = load_workbook(BytesIO(payload), data_only=True)
    assert wb["listing_template"].cell(2, 1).value == "Canvas organizer"
    assert manifest["worksheet"] == "listing_template"
