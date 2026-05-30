from __future__ import annotations

import io
import csv
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET


EXPECTED_HEADERS = [
    "order number",
    "asin",
    "product name",
    "order type",
    "order date",
    "shipped date",
    "cancelled date",
    "estimated tax value",
]
CSV_HEADER_ALIASES = {
    "asin": "ASIN",
    "product name": "Product Name",
    "product title": "Product Name",
    "title": "Product Name",
    "item name": "Product Name",
    "order number": "Order Number",
    "order #": "Order Number",
    "order date": "Order Date",
    "ordered date": "Order Date",
    "ship date": "Shipped Date",
    "shipped date": "Shipped Date",
    "cancelled date": "Cancelled Date",
    "canceled date": "Cancelled Date",
    "estimated tax value": "Estimated Tax Value",
    "etv": "Estimated Tax Value",
    "order type": "Order Type",
    "brand": "Brand",
    "category": "Category",
    "status": "Status",
    "review deadline": "Review Deadline",
    "item url": "Item URL",
    "product url": "Item URL",
    "url": "Item URL",
}
ORDER_NUMBER_RE = re.compile(r"\d[\d\s-]{8,}\d")
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
ETV_RE = re.compile(r"-?\$?\d[\d,]*(?:\.\d{2})?")
TYPE_RE = re.compile(r"\b(ORDER|CANCELLATION)\b", re.IGNORECASE)
XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}
CANONICAL_HEADER_NAMES = {
    "order number": "Order Number",
    "asin": "ASIN",
    "product name": "Product Name",
    "order type": "Order Type",
    "order date": "Order Date",
    "shipped date": "Shipped Date",
    "cancelled date": "Cancelled Date",
    "estimated tax value": "Estimated Tax Value",
}


@dataclass
class ParsedVineRow:
    order_number: str | None
    asin: str | None
    product_name: str | None
    order_type: str | None
    order_date: date | None
    shipped_date: date | None
    cancelled_date: date | None
    estimated_tax_value: float | None
    eligible_after: date | None
    eligibility_status: str
    parse_warnings: list[str]
    raw_row_json: dict
    brand: str | None = None
    category: str | None = None
    status: str | None = None
    review_deadline: date | None = None
    item_url: str | None = None
    source_confidence: str = "high"


def normalize_whitespace(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_order_number(value: str | None) -> str | None:
    normalized = re.sub(r"\s+", "", value or "")
    if not normalized:
        return None
    if len(normalized) > 64:
        return None
    if not any(character.isdigit() for character in normalized):
        return None
    return normalized


def parse_order_type(value: str | None) -> str | None:
    normalized = normalize_whitespace(value).upper()
    return normalized if normalized in {"ORDER", "CANCELLATION"} else None


def parse_date_value(value: object, *, allow_excel_serial: bool = False) -> date | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and allow_excel_serial:
        serial = int(value)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
        return None
    text = normalize_whitespace(str(value))
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_decimal_value(value: object) -> float | None:
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = normalize_whitespace(str(value)).replace("$", "").replace(",", "")
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _add_months(base_date: date, months: int) -> date:
    year = base_date.year + (base_date.month - 1 + months) // 12
    month = (base_date.month - 1 + months) % 12 + 1
    month_lengths = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(base_date.day, month_lengths[month - 1]))


def calculate_vine_eligibility(item: dict, reference_date: date | None = None) -> tuple[date | None, str]:
    reference = reference_date or date.today()
    base_date = item.get("shipped_date") or item.get("order_date")
    if item.get("order_type") == "CANCELLATION" or item.get("cancelled_date"):
        return None, "cancelled"
    if not item.get("order_number") or not item.get("asin") or not item.get("product_name") or not base_date:
        return None, "invalid"
    eligible_after = _add_months(base_date, 6)
    if reference >= eligible_after:
        return eligible_after, "eligible"
    return eligible_after, f"locked_until_{eligible_after.isoformat()}"


def normalize_vine_row(row: dict, *, reference_date: date | None = None, source_confidence: str = "high") -> ParsedVineRow:
    warnings: list[str] = []
    order_number = normalize_order_number(row.get("Order Number"))
    asin = normalize_whitespace(row.get("ASIN")).upper() or None
    if asin and not ASIN_RE.match(asin):
        warnings.append("Invalid ASIN format")
    product_name = normalize_whitespace(row.get("Product Name")) or None
    order_type = parse_order_type(row.get("Order Type"))
    if row.get("Order Type") and not order_type:
        warnings.append("Unknown order type")
    parsed = {
        "order_number": order_number,
        "asin": asin,
        "product_name": product_name,
        "order_type": order_type,
        "order_date": parse_date_value(row.get("Order Date"), allow_excel_serial=True),
        "shipped_date": parse_date_value(row.get("Shipped Date"), allow_excel_serial=True),
        "cancelled_date": parse_date_value(row.get("Cancelled Date"), allow_excel_serial=True),
        "estimated_tax_value": parse_decimal_value(row.get("Estimated Tax Value")),
        "brand": normalize_whitespace(row.get("Brand")) or None,
        "category": normalize_whitespace(row.get("Category")) or None,
        "status": normalize_whitespace(row.get("Status")) or None,
        "review_deadline": parse_date_value(row.get("Review Deadline")),
        "item_url": normalize_whitespace(row.get("Item URL")) or None,
    }
    eligible_after, eligibility_status = calculate_vine_eligibility(parsed, reference_date=reference_date)
    if not order_number:
        warnings.append("Missing order number")
    if not asin:
        warnings.append("Missing ASIN")
    if not product_name:
        warnings.append("Missing product name")
    return ParsedVineRow(
        **parsed,
        eligible_after=eligible_after,
        eligibility_status=eligibility_status,
        parse_warnings=warnings,
        raw_row_json=row,
        source_confidence=source_confidence,
    )


def should_skip_vine_row(row: ParsedVineRow) -> bool:
    """Drop obvious non-data/footer rows that show up after report tables."""
    return (
        not row.asin
        and not row.product_name
        and not row.order_type
        and not row.order_date
        and not row.shipped_date
        and not row.cancelled_date
        and row.estimated_tax_value is None
    )


def dedupe_vine_items(rows: list[ParsedVineRow]) -> list[ParsedVineRow]:
    seen: set[tuple[str | None, str | None, str | None]] = set()
    result: list[ParsedVineRow] = []
    for row in rows:
        key = (row.order_number, row.asin, row.order_type)
        if key in seen:
            row.parse_warnings.append("Duplicate row skipped")
            continue
        seen.add(key)
        result.append(row)
    return result


def detect_cancelled_items(rows: list[ParsedVineRow]) -> list[ParsedVineRow]:
    cancelled_keys = {(row.order_number, row.asin) for row in rows if row.order_type == "CANCELLATION" or row.cancelled_date}
    for row in rows:
        if row.order_type == "ORDER" and (row.order_number, row.asin) in cancelled_keys:
            row.eligibility_status = "cancelled"
            row.eligible_after = None
            if "Matched cancellation row" not in row.parse_warnings:
                row.parse_warnings.append("Matched cancellation row")
    return rows


def _column_index(cell_ref: str) -> int:
    letters = "".join(character for character in cell_ref if character.isalpha())
    value = 0
    for character in letters:
        value = (value * 26) + (ord(character.upper()) - 64)
    return value - 1


def _load_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.parse(workbook.open("xl/sharedStrings.xml")).getroot()
    except KeyError:
        return []
    return ["".join(node.text or "" for node in item.iterfind(".//main:t", XML_NS)) for item in root.findall("main:si", XML_NS)]


def _sheet_targets(workbook: zipfile.ZipFile) -> list[str]:
    workbook_root = ET.parse(workbook.open("xl/workbook.xml")).getroot()
    rels_root = ET.parse(workbook.open("xl/_rels/workbook.xml.rels")).getroot()
    rel_map = {item.attrib["Id"]: item.attrib["Target"] for item in rels_root.findall("pkg:Relationship", XML_NS)}
    targets: list[str] = []
    for sheet in workbook_root.findall("main:sheets/main:sheet", XML_NS):
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rel_map.get(rel_id or "")
        if not target:
            continue
        targets.append(target if target.startswith("xl/") else f"xl/{target.lstrip('/')}")
    return targets


def _parse_sheet_rows(workbook: zipfile.ZipFile, target: str, shared_strings: list[str]) -> list[dict[int, object]]:
    root = ET.parse(workbook.open(target)).getroot()
    rows: list[dict[int, object]] = []
    for row in root.findall(".//main:sheetData/main:row", XML_NS):
        values: dict[int, object] = {}
        for cell in row.findall("main:c", XML_NS):
            cell_type = cell.attrib.get("t")
            ref = cell.attrib.get("r", "")
            index = _column_index(ref)
            value_node = cell.find("main:v", XML_NS)
            inline_node = cell.find("main:is/main:t", XML_NS)
            raw_value = value_node.text if value_node is not None else (inline_node.text if inline_node is not None else "")
            if cell_type == "s" and raw_value != "":
                values[index] = shared_strings[int(raw_value)]
            else:
                values[index] = raw_value
        rows.append(values)
    return rows


def _detect_header_row(rows: list[dict[int, object]]) -> tuple[int, dict[int, str]]:
    required_headers = {
        "Order Number",
        "ASIN",
        "Product Name",
        "Order Date",
        "Estimated Tax Value",
    }
    for row_index, row in enumerate(rows):
        normalized = {index: normalize_whitespace(str(value)).lower() for index, value in row.items()}
        canonical_by_index: dict[int, str] = {}
        for index, header in normalized.items():
            if header in CANONICAL_HEADER_NAMES:
                canonical_by_index[index] = CANONICAL_HEADER_NAMES[header]
                continue
            mapped = CSV_HEADER_ALIASES.get(header)
            if mapped:
                canonical_by_index[index] = mapped
        if required_headers.issubset(set(canonical_by_index.values())):
            return row_index, canonical_by_index
    raise ValueError("Could not locate Vine report header row")


def parse_vine_xlsx(file_bytes: bytes, *, reference_date: date | None = None) -> list[ParsedVineRow]:
    rows: list[ParsedVineRow] = []
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as workbook:
        shared_strings = _load_shared_strings(workbook)
        for target in _sheet_targets(workbook):
            sheet_rows = _parse_sheet_rows(workbook, target, shared_strings)
            if not sheet_rows:
                continue
            header_index, header_map = _detect_header_row(sheet_rows)
            for row in sheet_rows[header_index + 1 :]:
                normalized = {header_map[index]: value for index, value in row.items() if index in header_map}
                if not any(normalize_whitespace(str(value)) for value in normalized.values()):
                    continue
                parsed = normalize_vine_row(normalized, reference_date=reference_date)
                if should_skip_vine_row(parsed):
                    continue
                rows.append(parsed)
    if not rows:
        raise ValueError("No Vine rows found in XLSX report")
    return detect_cancelled_items(dedupe_vine_items(rows))


def _normalize_csv_headers(headers: list[str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for header in headers:
        key = normalize_whitespace(header).lower()
        mapped = CSV_HEADER_ALIASES.get(key)
        if mapped:
            normalized[header] = mapped
    return normalized


def parse_vine_csv(file_bytes: bytes, *, reference_date: date | None = None) -> list[ParsedVineRow]:
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = list(csv.DictReader(io.StringIO(text)))
    if not reader:
        raise ValueError("No Vine rows found in CSV report")
    mapped_headers = _normalize_csv_headers(list(reader[0].keys() or []))
    if not mapped_headers:
        raise ValueError("Could not map CSV headers to Vine fields")
    rows: list[ParsedVineRow] = []
    for row in reader:
        normalized: dict[str, object] = {}
        for raw_header, canonical_header in mapped_headers.items():
            normalized[canonical_header] = row.get(raw_header)
        if not any(normalize_whitespace(str(value)) for value in normalized.values() if value is not None):
            continue
        parsed = normalize_vine_row(normalized, reference_date=reference_date, source_confidence="high")
        if should_skip_vine_row(parsed):
            continue
        rows.append(parsed)
    if not rows:
        raise ValueError("No Vine rows found in CSV report")
    return detect_cancelled_items(dedupe_vine_items(rows))


def _extract_pdf_text(file_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(file_bytes)
        temp_path = Path(handle.name)
    try:
        result = subprocess.run(["pdftotext", "-layout", str(temp_path), "-"], capture_output=True, text=True, check=True)
        return result.stdout
    finally:
        temp_path.unlink(missing_ok=True)


def _parse_pdf_record(lines: list[str], *, reference_date: date | None = None) -> ParsedVineRow:
    text = " ".join(lines)
    order_number_match = ORDER_NUMBER_RE.search(text)
    asin_match = re.search(r"\b[A-Z0-9]{10}\b", text.upper())
    type_match = TYPE_RE.search(text)
    dates = DATE_RE.findall(text)
    etv_matches = list(ETV_RE.finditer(text))
    row = {
        "Order Number": normalize_order_number(order_number_match.group(0) if order_number_match else None),
        "ASIN": asin_match.group(0) if asin_match else None,
        "Order Type": type_match.group(1).upper() if type_match else None,
        "Order Date": dates[0] if len(dates) > 0 else None,
        "Shipped Date": dates[1] if len(dates) > 1 else None,
        "Cancelled Date": dates[2] if len(dates) > 2 else None,
        "Estimated Tax Value": etv_matches[-1].group(0) if etv_matches else None,
    }
    product_text = text
    for token in [row["Order Number"], row["ASIN"], row["Order Type"], row["Order Date"], row["Shipped Date"], row["Cancelled Date"], row["Estimated Tax Value"]]:
        if token:
            product_text = product_text.replace(str(token), " ")
    row["Product Name"] = normalize_whitespace(product_text)
    parsed = normalize_vine_row(row, reference_date=reference_date, source_confidence="medium")
    parsed.parse_warnings.extend(["PDF fallback parse", "Require preflight review before draft creation"])
    if not row["Order Number"] or not row["ASIN"] or len(dates) < 2:
        parsed.source_confidence = "low"
        parsed.parse_warnings.append("Low confidence PDF row")
    return parsed


def parse_vine_pdf(file_bytes: bytes, *, reference_date: date | None = None) -> list[ParsedVineRow]:
    text = _extract_pdf_text(file_bytes)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rows: list[ParsedVineRow] = []
    current: list[str] = []
    in_table = False
    for line in lines:
        lowered = line.lower()
        if not in_table and "order number" in lowered and "asin" in lowered:
            in_table = True
            continue
        if not in_table:
            continue
        if ORDER_NUMBER_RE.search(line) and current:
            rows.append(_parse_pdf_record(current, reference_date=reference_date))
            current = [line]
        else:
            current.append(line)
    if current:
        rows.append(_parse_pdf_record(current, reference_date=reference_date))
    if not rows:
        raise ValueError("No Vine rows found in PDF report")
    return detect_cancelled_items(dedupe_vine_items(rows))
