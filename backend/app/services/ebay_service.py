from __future__ import annotations

import asyncio
import base64
import hmac
import hashlib
import html
import json
import re
import secrets
from xml.etree import ElementTree
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import reload_settings, settings
from app.models.enums import EbayPublishStatus, ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceAccount, MarketplaceListing, MarketplaceMetadataCache, MarketplacePublishAttempt, User
from app.services.marketplace_error_translation import translate_marketplace_error
from app.services.rate_limiter import rate_limiter



class EbayIntegrationError(RuntimeError):
    """Raised for eBay API integration errors."""


_EBAY_TRADING_NAMESPACE = "urn:ebay:apis:eBLBaseComponents"


def _ebay_trading_value(element: ElementTree.Element | None, path: str) -> str:
    """Read a Trading API XML field without leaking namespace details to callers."""
    if element is None:
        return ""
    value = element.findtext(path, namespaces={"e": _EBAY_TRADING_NAMESPACE})
    return str(value or "").strip()


def _is_inventory_sku_catalog_error(error: EbayIntegrationError) -> bool:
    message = str(error).lower()
    return "25707" in message or "invalid value for a sku" in message


async def _get_active_ebay_listings_via_trading_api(
    account: MarketplaceAccount,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Read active listings through eBay's seller API when Inventory rejects a legacy SKU.

    Inventory API refuses to enumerate *any* offers for an account that contains a
    historical SKU which violates its current character rules.  GetMyeBaySelling
    remains able to enumerate those listings, so this is a read-only compatibility
    path for imports and reconciliation.
    """
    runtime_settings = reload_settings()
    endpoint = "https://api.ebay.com/ws/api.dll" if runtime_settings.environment == "production" else "https://api.sandbox.ebay.com/ws/api.dll"
    requested_limit = max(1, int(limit or 1))
    page_size = min(requested_limit, 200)
    page_number = 1
    imported: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        while len(imported) < requested_limit:
            request_xml = f'''<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="{_EBAY_TRADING_NAMESPACE}">
  <DetailLevel>ReturnAll</DetailLevel>
  <ActiveList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>{min(page_size, requested_limit - len(imported))}</EntriesPerPage>
      <PageNumber>{page_number}</PageNumber>
    </Pagination>
  </ActiveList>
</GetMyeBaySellingRequest>'''
            await rate_limiter.acquire_async("ebay")
            response = await client.post(
                endpoint,
                content=request_xml.encode("utf-8"),
                headers={
                    "X-EBAY-API-CALL-NAME": "GetMyeBaySelling",
                    "X-EBAY-API-SITEID": "0",
                    "X-EBAY-API-COMPATIBILITY-LEVEL": "1231",
                    "X-EBAY-API-IAF-TOKEN": account.access_token,
                    "Content-Type": "text/xml",
                },
            )
            if response.status_code >= 400:
                raise EbayIntegrationError(f"eBay Trading API request failed ({response.status_code}) while listing active items")
            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError as exc:
                raise EbayIntegrationError("eBay Trading API returned an unreadable active-listing response") from exc

            ack = _ebay_trading_value(root, "e:Ack")
            if ack.lower() not in {"success", "warning"}:
                errors = root.findall("e:Errors", {"e": _EBAY_TRADING_NAMESPACE})
                detail = "; ".join(
                    filter(None, (_ebay_trading_value(error, "e:LongMessage") or _ebay_trading_value(error, "e:ShortMessage") for error in errors))
                )
                raise EbayIntegrationError(f"eBay Trading API could not list active items: {detail or ack or 'unknown error'}")

            items = root.findall("e:ActiveList/e:ItemArray/e:Item", {"e": _EBAY_TRADING_NAMESPACE})
            for item in items:
                listing_id = _ebay_trading_value(item, "e:ItemID")
                if not listing_id:
                    continue
                specifics: dict[str, Any] = {}
                for pair in item.findall("e:ItemSpecifics/e:NameValueList", {"e": _EBAY_TRADING_NAMESPACE}):
                    name = _ebay_trading_value(pair, "e:Name")
                    values = [str(value.text or "").strip() for value in pair.findall("e:Value", {"e": _EBAY_TRADING_NAMESPACE}) if str(value.text or "").strip()]
                    if name and values:
                        specifics[name] = values[0]
                image_urls = [
                    str(value.text or "").strip()
                    for value in item.findall("e:PictureDetails/e:PictureURL", {"e": _EBAY_TRADING_NAMESPACE})
                    if str(value.text or "").strip()
                ]
                price = _ebay_trading_value(item, "e:SellingStatus/e:CurrentPrice")
                quantity = _ebay_trading_value(item, "e:QuantityAvailable") or _ebay_trading_value(item, "e:Quantity") or "1"
                sku = _ebay_trading_value(item, "e:SKU")
                imported.append(
                    {
                        "source_listing_reference": f"https://www.ebay.com/itm/{listing_id}",
                        "source_url": f"https://www.ebay.com/itm/{listing_id}",
                        "title": _ebay_trading_value(item, "e:Title"),
                        "description": _ebay_trading_value(item, "e:Description"),
                        "price": price,
                        "listing_price": price,
                        "quantity": quantity,
                        "image_urls": image_urls,
                        "item_specifics": specifics,
                        "attributes": specifics,
                        "category_id": _ebay_trading_value(item, "e:PrimaryCategory/e:CategoryID"),
                        "condition": _ebay_trading_value(item, "e:ConditionDisplayName") or _ebay_trading_value(item, "e:ConditionID"),
                        "tags": ["ebay", "imported"],
                        "source_identifiers": {"ebay_listing_id": listing_id, "offer_id": None, "sku": sku or None},
                        "listing_start_time": _ebay_trading_value(item, "e:ListingDetails/e:StartTime"),
                        "view_count": _ebay_trading_value(item, "e:HitCount"),
                        "raw_offer": {"source": "ebay_trading_api", "item": {"listingId": listing_id, "sku": sku or None}},
                        "raw_inventory_item": {"source": "ebay_trading_api", "item": {"listingId": listing_id}},
                    }
                )
                if len(imported) >= requested_limit:
                    break

            total_pages = _ebay_trading_value(root, "e:ActiveList/e:PaginationResult/e:TotalNumberOfPages")
            if not items or page_number >= int(total_pages or 1) or len(imported) >= requested_limit:
                break
            page_number += 1
    return imported


@dataclass(slots=True)
class EbayTokenBundle:
    access_token: str
    refresh_token: str | None
    expires_in: int


class EbayAPIClient:
    """Thin async wrapper around eBay APIs with retries and standardized errors."""

    def __init__(self, access_token: str, *, sandbox: bool | None = None, timeout_seconds: int = 30):
        self.access_token = access_token
        runtime_settings = reload_settings()
        use_sandbox = runtime_settings.environment != "production" if sandbox is None else sandbox
        self.base_url = "https://api.sandbox.ebay.com" if use_sandbox else "https://api.ebay.com"
        self.timeout = httpx.Timeout(timeout_seconds)

    async def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        request_headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Content-Language": "en-US",
            "Accept": "application/json",
            **(headers or {}),
        }

        backoff_seconds = 0.5
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, retries + 1):
                await rate_limiter.acquire_async("ebay")
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=payload,
                    headers=request_headers,
                )
                if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds *= 2
                    continue

                if response.status_code >= 400:
                    detail = _safe_json(response)
                    raise EbayIntegrationError(
                        f"eBay API request failed ({response.status_code}) for {path}: {json.dumps(detail)}"
                    )
                return _safe_json(response)

        raise EbayIntegrationError(f"eBay API retry exhaustion for {path}")


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            return parsed
        return {"data": parsed}
    except Exception:
        return {"raw": response.text}


def _coerce_positive_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    return round(numeric, 3)


def _coerce_price_number(value: Any) -> float | None:
    """Normalize a persisted or remote price without accepting zero/negative values."""
    return _coerce_positive_float(value)


def _coerce_positive_int(value: Any) -> int | None:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def _to_public_image_url(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    base_url = (settings.app_base_url or "").strip().rstrip("/")

    def _absolute_media_url(media_path: str) -> str:
        normalized = media_path if media_path.startswith("/") else f"/{media_path}"
        return f"{base_url}{normalized}" if base_url else normalized

    if raw.startswith("/media/"):
        return _absolute_media_url(raw)
    marker = "/storage/"
    storage_root = settings.storage_root.rstrip("/")
    if raw.startswith("storage/"):
        return _absolute_media_url(f"/media/{raw.removeprefix('storage/')}")
    if raw.startswith("./storage/"):
        return _absolute_media_url(f"/media/{raw.removeprefix('./storage/')}")
    if marker in raw:
        return _absolute_media_url(f"/media/{raw.split(marker, 1)[1]}")
    if raw.startswith(storage_root):
        relative = raw[len(storage_root):].lstrip("/\\")
        if relative:
            return _absolute_media_url(f"/media/{relative.replace('\\', '/')}")
    return raw


def _resolve_storage_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    storage_root = settings.storage_root.rstrip("/")
    if raw.startswith(storage_root):
        return raw
    if raw.startswith("/media/"):
        return f"{storage_root}/{raw.removeprefix('/media/')}"
    if raw.startswith("storage/"):
        return f"{storage_root}/{raw.removeprefix('storage/')}"
    if raw.startswith("./storage/"):
        return f"{storage_root}/{raw.removeprefix('./storage/')}"
    marker = "/storage/"
    if marker in raw:
        return f"{storage_root}/{raw.split(marker, 1)[1]}"
    return raw


def _image_meets_ebay_policy(path: str) -> bool:
    resolved = _resolve_storage_path(path)
    try:
        with Image.open(resolved) as image:
            width, height = image.size
            return max(width, height) >= 500
    except Exception:
        return False


def _build_ebay_image_urls(listing: Listing) -> list[str]:
    image_urls: list[str] = []
    for candidate in listing.image_urls or []:
        if not _image_meets_ebay_policy(candidate):
            continue
        public_url = _to_public_image_url(candidate)
        if public_url and public_url not in image_urls:
            image_urls.append(public_url)
    return image_urls


def _sanitize_ebay_description(description: str | None) -> str:
    raw = str(description or "").strip()
    if not raw:
        return "No description provided"
    cleaned_lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("source url:"):
            continue
        stripped = re.sub(r"https?://\S+", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"www\.\S+", "", stripped, flags=re.IGNORECASE).strip()
        if stripped:
            cleaned_lines.append(stripped)
    cleaned = "\n".join(cleaned_lines)
    cleaned = html.escape(cleaned)
    cleaned = cleaned.replace("\n", "<br>")
    cleaned = re.sub(r"(<br>){3,}", "<br><br>", cleaned)
    return cleaned or "No description provided"


def _sync_ebay_marketplace_listing(
    db: Session,
    *,
    listing_id: int,
    status: MarketplaceListingStatus,
    response: dict[str, Any] | None,
) -> None:
    row = db.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.listing_id == listing_id,
            MarketplaceListing.marketplace == MarketplaceName.ebay,
        )
        .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
    ).scalars().first()
    if not row:
        row = MarketplaceListing(
            listing_id=listing_id,
            marketplace=MarketplaceName.ebay,
            status=status,
        )
    row.status = status
    row.raw_response = response
    row.marketplace_listing_id = (
        (response or {}).get("listing_id")
        or (response or {}).get("listingId")
        or row.marketplace_listing_id
    )
    db.add(row)


def _serialize_payload_snapshot(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return json.loads(json.dumps(payload, sort_keys=True, default=str))


def _payload_hash(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    serialized = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _build_ebay_sku(user_id: int, listing_id: int) -> str:
    # eBay inventory SKUs must be alphanumeric and <= 50 chars.
    # Keep the prefix recognizable while avoiding punctuation.
    return f"posterprou{int(user_id)}l{int(listing_id)}"


def _extract_listing_id_from_ebay_sku(sku: str, user_id: int | None = None) -> int | None:
    raw = str(sku or "").strip()
    if not raw:
        return None
    patterns = []
    if user_id is not None:
        patterns.append(rf"^posterprou{int(user_id)}l(\d+)$")
        patterns.append(rf"^posterpro-{int(user_id)}-(\d+)$")
    else:
        patterns.append(r"^posterprou\d+l(\d+)$")
        patterns.append(r"^posterpro-\d+-(\d+)$")
    for pattern in patterns:
        match = re.match(pattern, raw, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                return None
    return None


def _start_publish_attempt(
    db: Session,
    *,
    listing: Listing,
    marketplace: str,
    dry_run: bool,
    preflight_status: str | None,
    payload_snapshot: dict[str, Any] | None,
) -> MarketplacePublishAttempt:
    attempt = MarketplacePublishAttempt(
        listing_id=listing.id,
        user_id=listing.user_id,
        marketplace=MarketplaceName(marketplace),
        started_at=datetime.now(UTC).replace(tzinfo=None),
        dry_run=dry_run,
        preflight_status=preflight_status,
        payload_snapshot=_serialize_payload_snapshot(payload_snapshot),
        payload_hash=_payload_hash(payload_snapshot),
        inventory_item_sku=_build_ebay_sku(listing.user_id, listing.id),
        retry_count=0,
    )
    db.add(attempt)
    db.flush()
    return attempt


def _finish_publish_attempt(
    db: Session,
    *,
    attempt: MarketplacePublishAttempt,
    status: str,
    response: dict[str, Any] | None,
    error: Exception | str | None = None,
    retryable: bool = False,
) -> MarketplacePublishAttempt:
    attempt.finished_at = datetime.now(UTC).replace(tzinfo=None)
    attempt.marketplace_status = status
    attempt.marketplace_listing_id = (
        (response or {}).get("listing_id")
        or (response or {}).get("listingId")
        or attempt.marketplace_listing_id
    )
    attempt.offer_id = (
        (response or {}).get("offerId")
        or (response or {}).get("offer_id")
        or attempt.offer_id
    )
    attempt.translated_error = translate_marketplace_error("ebay", error) if error else None
    attempt.raw_error = str(error) if error else None
    attempt.retryable = bool(retryable or (attempt.translated_error or {}).get("retryable"))
    db.add(attempt)
    return attempt


def _is_invalid_access_token_error(error: Exception) -> bool:
    message = str(error).lower()
    return "invalid access token" in message or "(401)" in message


def _extract_ebay_error_parameter(message: str, name: str) -> str | None:
    if not message:
        return None
    pattern = rf'"name"\s*:\s*"{re.escape(name)}"\s*,\s*"value"\s*:\s*"([^"]+)"'
    match = re.search(pattern, message)
    if match:
        return match.group(1)
    return None


def _ebay_listing_query(listing: Listing) -> str:
    metadata = listing.source_metadata or {}
    raw_row = metadata.get("raw_row_json") or {}
    for candidate in (metadata.get("product_name"), raw_row.get("Product Name"), listing.title):
        text = str(candidate or "").strip()
        if text:
            return text[:160]
    return f"PosterPro listing {listing.id}"


def _derive_brand(listing: Listing) -> str:
    metadata = listing.source_metadata or {}
    raw_row = metadata.get("raw_row_json") or {}
    for candidate in (metadata.get("brand"), raw_row.get("Brand")):
        text = str(candidate or "").strip()
        if text:
            return text
    title = str(listing.title or "").strip()
    prefix = re.split(r"\s+for\s+", title, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    if prefix and 1 < len(prefix.split()) <= 3 and len(prefix) <= 40:
        return prefix
    return "Unbranded"


def _derive_phone_model(title: str) -> str | None:
    patterns = (
        r"(iPhone\s+\d+\s+Pro\s+Max)",
        r"(iPhone\s+\d+\s+Pro)",
        r"(iPhone\s+\d+\s+Plus)",
        r"(iPhone\s+\d+)",
        r"(iPhone\s+XR)",
        r"(iPhone\s+XS\s+Max)",
        r"(iPhone\s+XS)",
        r"(iPhone\s+X)",
        r"(iPad\s+[A-Za-z0-9\s]+)",
        r"(Samsung\s+Galaxy\s+(?:S\d{1,2}|A\d{2}|Note\s*\d{1,2}|Z\s*Flip\s*\d?|Z\s*Fold\s*\d?)(?:\s+(?:Ultra|Plus|Pro|FE))?(?:\s+5G)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, title, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    code_match = re.search(r"\b([A-Z]{1,3}-?[A-Z0-9]{3,})\b", title)
    if code_match:
        return code_match.group(1)
    return None


def _derive_dimension_value(title: str) -> str | None:
    patterns = (
        r'(\d+(?:\.\d+)?\s*[xX]\s*\d+(?:\.\d+)?(?:\s*[xX]\s*\d+(?:\.\d+)?)?\s*(?:inch|inches|in|"))',
        r'(\d+(?:\.\d+)?\s*(?:inch|inches|in|"))',
    )
    for pattern in patterns:
        match = re.search(pattern, title, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return None


def _derive_capacity_value(title: str) -> str | None:
    match = re.search(r"(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?\s*(?:cup|cups|oz|fl oz|gallon|gallons|gal|mah|ah|amp|amps|a|l|liter|liters))", title, flags=re.IGNORECASE)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return None


def _derive_color(title: str) -> str | None:
    colors = [
        'Black', 'White', 'Gray', 'Grey', 'Silver', 'Gold', 'Blue', 'Red', 'Green', 'Pink', 'Purple', 'Brown', 'Beige', 'Clear', 'Chrome'
    ]
    lowered = title.lower()
    for color in colors:
        if color.lower() in lowered:
            return color
    return None


def _clip_specific_value(value: str, limit: int = 65) -> str:
    compact = re.sub(r"\s+", " ", str(value or '')).strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip(' ,;-')


def _fallback_aspect_value(listing: Listing, aspect_name: str, title: str) -> str | None:
    normalized = aspect_name.lower()
    model = _derive_phone_model(title)
    dimension = _derive_dimension_value(title)
    capacity = _derive_capacity_value(title)
    color = _derive_color(title)
    if normalized == 'model':
        return model or 'Universal'
    if normalized == 'compatible model':
        return f'For {model}' if model else 'Universal'
    if normalized == 'size':
        return dimension or 'One Size'
    if normalized == 'item length':
        return dimension or 'Does Not Apply'
    if normalized == 'capacity':
        return capacity or 'Does Not Apply'
    if normalized == 'storage capacity':
        return capacity or 'Does Not Apply'
    if normalized == 'type':
        return _derive_item_type(title) or 'Does Not Apply'
    if normalized == 'material':
        lowered = title.lower()
        for token, value in (
            ("stainless steel", "Stainless Steel"),
            ("aluminum", "Aluminum"),
            ("aluminium", "Aluminum"),
            ("bamboo", "Bamboo"),
            ("wood", "Wood"),
            ("plastic", "Plastic"),
            ("carbon", "Carbon Fiber"),
            ("nylon", "Nylon"),
            ("polyester", "Polyester"),
            ("metal", "Metal"),
        ):
            if token in lowered:
                return value
        return 'Does Not Apply'
    if normalized in {'voltage', 'volts'}:
        match = re.search(r"(\d+(?:\.\d+)?)\s*v\b", title, flags=re.IGNORECASE)
        return f"{match.group(1)}V" if match else 'Does Not Apply'
    if normalized in {'wattage', 'watts'}:
        match = re.search(r"(\d+(?:\.\d+)?)\s*w\b", title, flags=re.IGNORECASE)
        return f"{match.group(1)}W" if match else 'Does Not Apply'
    if normalized in {'amperage', 'amps', 'amp'}:
        match = re.search(r"(\d+(?:\.\d+)?)\s*a\b", title, flags=re.IGNORECASE)
        return f"{match.group(1)}A" if match else 'Does Not Apply'
    if normalized == 'color':
        return color or 'Multicolor'
    if normalized == 'artist':
        return _derive_brand(listing) or 'Unknown'
    if normalized == 'insect repellent treated':
        return 'No'
    return 'Does Not Apply'


def _derive_item_type(title: str) -> str | None:
    lowered = title.lower()
    if "makeup brush cleaner" in lowered or "brush cleaner" in lowered:
        return "Brush Cleaner Machine"
    if "meat chopper" in lowered or "masher" in lowered:
        return "Meat Chopper"
    if "bidet" in lowered:
        return "Bidet Sprayer"
    if "flag pole mount" in lowered or "flagpole holder" in lowered:
        return "Flag Pole Mount"
    if "lpg hose" in lowered or "propane hose" in lowered:
        return "Propane Hose"
    if "sump pump battery backup" in lowered or "battery backup system" in lowered:
        return "Battery Backup System"
    if "backpack" in lowered:
        return "Backpack"
    if "golf bag" in lowered:
        return "Golf Bag"
    if "starter replacement" in lowered:
        return "Starter"
    if "towel rack" in lowered:
        return "Towel Rack"
    if "alternator" in lowered:
        return "Alternator"
    if "charger" in lowered:
        return "Charger"
    if "pressure switch" in lowered:
        return "Pressure Switch"
    if "hub" in lowered or "gateway" in lowered:
        return "Wireless Hub"
    if "flagstick" in lowered:
        return "Flagstick"
    if "exhaust fan" in lowered:
        return "Exhaust Fan"
    if "vent filter" in lowered:
        return "Vent Filter"
    if "water transfer pump" in lowered:
        return "Water Transfer Pump"
    if "fume extractor" in lowered:
        return "Fume Extractor"
    if "spot welder" in lowered:
        return "Spot Welder"
    if "battery pack" in lowered:
        return "Battery Pack"
    if "baitcaster reel" in lowered or "reel" in lowered:
        return "Fishing Reel"
    if "ceiling fan" in lowered:
        return "Ceiling Fan"
    if "heater" in lowered:
        return "Heater"
    if "drop sticks" in lowered or "reaction training toy" in lowered:
        return "Game"
    if "wifi extender" in lowered or "wifi repeater" in lowered:
        return "WiFi Extender"
    if "tpms" in lowered or "tire pressure monitoring" in lowered:
        return "Tire Pressure Monitoring System"
    if "screen replacement" in lowered or "digitizer" in lowered or "lcd display" in lowered:
        return "Screen"
    if "back glass" in lowered:
        return "Back Glass"
    if "battery" in lowered:
        return "Battery"
    if "torch" in lowered:
        return "Torch"
    return None


def _oauth_base() -> str:
    runtime_settings = reload_settings()
    return "https://auth.sandbox.ebay.com/oauth2/authorize" if runtime_settings.environment != "production" else "https://auth.ebay.com/oauth2/authorize"


def _token_endpoint() -> str:
    runtime_settings = reload_settings()
    return "https://api.sandbox.ebay.com/identity/v1/oauth2/token" if runtime_settings.environment != "production" else "https://api.ebay.com/identity/v1/oauth2/token"


def _scopes() -> str:
    return " ".join(
        [
            "https://api.ebay.com/oauth/api_scope",
            "https://api.ebay.com/oauth/api_scope/sell.marketing.readonly",
            "https://api.ebay.com/oauth/api_scope/sell.marketing",
            "https://api.ebay.com/oauth/api_scope/sell.inventory.readonly",
            "https://api.ebay.com/oauth/api_scope/sell.inventory",
            "https://api.ebay.com/oauth/api_scope/sell.account.readonly",
            "https://api.ebay.com/oauth/api_scope/sell.account",
            "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
            "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
            "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly",
            "https://api.ebay.com/oauth/api_scope/sell.finances",
            "https://api.ebay.com/oauth/api_scope/sell.payment.dispute",
            "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
            "https://api.ebay.com/oauth/api_scope/sell.reputation",
            "https://api.ebay.com/oauth/api_scope/sell.reputation.readonly",
            "https://api.ebay.com/oauth/api_scope/commerce.notification.subscription",
            "https://api.ebay.com/oauth/api_scope/commerce.notification.subscription.readonly",
            "https://api.ebay.com/oauth/api_scope/sell.stores",
            "https://api.ebay.com/oauth/api_scope/sell.stores.readonly",
            "https://api.ebay.com/oauth/scope/sell.edelivery",
            "https://api.ebay.com/oauth/api_scope/commerce.vero",
            "https://api.ebay.com/oauth/api_scope/sell.inventory.mapping",
            "https://api.ebay.com/oauth/api_scope/commerce.message",
            "https://api.ebay.com/oauth/api_scope/commerce.feedback",
            "https://api.ebay.com/oauth/api_scope/commerce.shipping",
        ]
    )


def build_ebay_auth_url(user_id: int, redirect_uri: str) -> str:
    runtime_settings = reload_settings()
    if not runtime_settings.ebay_client_id:
        raise EbayIntegrationError("Missing eBay OAuth settings (ebay_client_id / ebay_client_secret)")
    state = _make_oauth_state(user_id)
    query = urlencode(
        {
            "client_id": runtime_settings.ebay_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _scopes(),
            "state": state,
        }
    )
    return f"{_oauth_base()}?{query}"


def _make_oauth_state(user_id: int) -> str:
    random_nonce = secrets.token_urlsafe(16)
    payload = f"{user_id}:{random_nonce}"
    signature = hmac.new((settings.session_secret or "posterpro-oauth").encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("utf-8")


def parse_oauth_state(state: str) -> int:
    try:
        decoded = base64.urlsafe_b64decode(state + ("=" * (-len(state) % 4))).decode("utf-8")
        user_id_text, nonce, signature = decoded.split(":", maxsplit=2)
    except Exception as exc:
        raise EbayIntegrationError("Invalid OAuth state") from exc
    payload = f"{user_id_text}:{nonce}"
    expected = hmac.new((settings.session_secret or "posterpro-oauth").encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise EbayIntegrationError("Invalid OAuth state")
    return int(user_id_text)


async def authenticate_user_ebay(user_id: int, redirect_uri: str) -> str:
    """Return user-specific OAuth URL; callback handling stores token in DB."""
    runtime_settings = reload_settings()
    if not runtime_settings.ebay_client_id or not runtime_settings.ebay_client_secret:
        raise EbayIntegrationError("Missing eBay OAuth settings (ebay_client_id / ebay_client_secret)")
    if not redirect_uri:
        raise EbayIntegrationError("redirect_uri is required")
    return build_ebay_auth_url(user_id, redirect_uri)


async def exchange_code_for_tokens(code: str, redirect_uri: str) -> EbayTokenBundle:
    runtime_settings = reload_settings()
    if not runtime_settings.ebay_client_id or not runtime_settings.ebay_client_secret:
        raise EbayIntegrationError("Missing eBay OAuth settings (ebay_client_id / ebay_client_secret)")
    credentials = f"{runtime_settings.ebay_client_id}:{runtime_settings.ebay_client_secret}".encode("utf-8")
    basic_auth = base64.b64encode(credentials).decode("utf-8")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        response = await client.post(_token_endpoint(), data=data, headers=headers)

    if response.status_code >= 400:
        raise EbayIntegrationError(f"OAuth token exchange failed: {response.text}")

    token_data = response.json()
    return EbayTokenBundle(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_in=int(token_data.get("expires_in", 7200)),
    )


async def refresh_ebay_token(user_id: int, db: Session) -> MarketplaceAccount:
    runtime_settings = reload_settings()
    account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == user_id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    if not account or not account.refresh_token:
        raise EbayIntegrationError("No eBay account with refresh token found")

    if not runtime_settings.ebay_client_id or not runtime_settings.ebay_client_secret:
        raise EbayIntegrationError("Missing eBay OAuth settings (ebay_client_id / ebay_client_secret)")
    credentials = f"{runtime_settings.ebay_client_id}:{runtime_settings.ebay_client_secret}".encode("utf-8")
    basic_auth = base64.b64encode(credentials).decode("utf-8")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": account.refresh_token,
        "scope": _scopes(),
    }
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        response = await client.post(_token_endpoint(), data=data, headers=headers)

    if response.status_code >= 400:
        raise EbayIntegrationError(f"Token refresh failed: {response.text}")

    payload = response.json()
    account.access_token = payload["access_token"]
    account.token_expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=int(payload.get("expires_in", 7200)))
    if payload.get("refresh_token"):
        account.refresh_token = payload["refresh_token"]
    account.connection_status = "connected"
    account.last_error = None
    account.last_refresh_at = datetime.now(UTC).replace(tzinfo=None)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def summarize_ebay_account_health(account: MarketplaceAccount | None) -> dict[str, Any]:
    now = datetime.utcnow()
    reauthorization_required = bool(account and getattr(account, "connection_status", "connected") == "reauthorization_required")
    connected = bool(account and account.access_token) and not reauthorization_required
    has_refresh_token = bool(account and account.refresh_token)
    token_expires_at = account.token_expires_at if account else None

    token_status = "disconnected"
    import_ready = False
    reconnect_required = False
    status_note = "Connect eBay for this operator before importing or publishing."

    if reauthorization_required:
        token_status = "reauthorization_required"
        reconnect_required = True
        status_note = "eBay rejected the saved credentials. Reconnect eBay to authorize this operator again."
    elif connected:
        if token_expires_at is None:
            token_status = "connected"
            import_ready = True
            status_note = "eBay is connected for this operator."
        else:
            expires_in_seconds = int((token_expires_at - now).total_seconds())
            if expires_in_seconds <= 0:
                if has_refresh_token:
                    token_status = "expired_refreshable"
                    import_ready = True
                    status_note = "The saved eBay token has expired, but PosterPro can refresh it on the next import or publish."
                else:
                    token_status = "expired"
                    reconnect_required = True
                    status_note = "The saved eBay token has expired and does not include a refresh token. Reconnect eBay or import fresh user tokens."
            elif expires_in_seconds <= 300:
                if has_refresh_token:
                    token_status = "expiring_soon"
                    import_ready = True
                    status_note = "The saved eBay token expires soon, but PosterPro can refresh it automatically."
                else:
                    token_status = "expiring_soon_manual"
                    import_ready = True
                    status_note = "The saved eBay token expires soon and does not include a refresh token. Reconnect eBay to avoid the next import failing."
            elif has_refresh_token:
                token_status = "healthy"
                import_ready = True
                status_note = "eBay is connected for this operator."
            else:
                token_status = "manual_token_only"
                import_ready = True
                status_note = "eBay is connected with a manual access token only. Imports work until the token expires; reconnect eBay for automatic refresh."

    return {
        "connected": connected,
        "has_refresh_token": has_refresh_token,
        "token_status": token_status,
        "import_ready": import_ready,
        "reconnect_required": reconnect_required,
        "status_note": status_note,
        "last_error": getattr(account, "last_error", None) if account else None,
        "last_refresh_at": getattr(account, "last_refresh_at", None) if account else None,
        "last_successful_check_at": getattr(account, "last_successful_check_at", None) if account else None,
    }


def _mark_ebay_reauthorization_required(account: MarketplaceAccount, db: Session, error: Exception) -> None:
    """Persist a safe, actionable credential failure without retaining token material."""
    message = str(error).replace(account.access_token or "", "[redacted]")[:500]
    account.connection_status = "reauthorization_required"
    account.last_error = message
    db.add(account)
    db.commit()


async def get_or_refresh_account(user_id: int, db: Session) -> MarketplaceAccount:
    account = db.execute(
        select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == user_id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )
    ).scalar_one_or_none()
    if not account:
        raise EbayIntegrationError("No connected eBay account for user")
    if account.token_expires_at and account.token_expires_at <= datetime.utcnow() + timedelta(minutes=5):
        if not account.refresh_token and account.access_token:
            # Older/manual eBay connections in this deployment can have an access token without a refresh token.
            # Fall back to the stored token so read-only/import paths can still attempt the API call.
            return account
        try:
            return await refresh_ebay_token(user_id, db)
        except EbayIntegrationError as exc:
            # A refresh failure is not a healthy connection. Persist the
            # actionable state so Settings and the publish queue stop showing
            # a stale "connected" badge after the access token has expired.
            _mark_ebay_reauthorization_required(account, db, exc)
            raise
    return account


async def _request_with_single_refresh(
    user_id: int,
    db: Session,
    account: MarketplaceAccount,
    *,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[MarketplaceAccount, dict[str, Any]]:
    client = EbayAPIClient(account.access_token)
    try:
        response = await client.request(method, path, params=params, payload=payload, headers=headers)
        return account, response
    except EbayIntegrationError as exc:
        if "(401)" not in str(exc) or not account.refresh_token:
            raise
        try:
            account = await refresh_ebay_token(user_id, db)
        except EbayIntegrationError as refresh_exc:
            _mark_ebay_reauthorization_required(account, db, refresh_exc)
            raise
        client = EbayAPIClient(account.access_token)
        try:
            response = await client.request(method, path, params=params, payload=payload, headers=headers)
            return account, response
        except EbayIntegrationError as retry_exc:
            if "(401)" in str(retry_exc):
                _mark_ebay_reauthorization_required(account, db, retry_exc)
            raise


async def create_inventory_location(
    user_id: int,
    db: Session,
    *,
    location_key: str | None = None,
    origin: dict[str, str] | None = None,
) -> dict[str, Any]:
    account = await get_or_refresh_account(user_id, db)
    client = EbayAPIClient(account.access_token)
    policy_settings = _load_listing_ebay_policy_settings(db, user_id)
    location_key = str(location_key or "").strip() or policy_settings.get("merchant_location_key") or f"posterpro-{user_id}"
    origin_settings = origin or _normalize_merchant_location_origin(policy_settings)
    missing_origin_fields = [
        field
        for field, value in {
            "merchant_location_postal_code": origin_settings.get("postal_code"),
            "merchant_location_country": origin_settings.get("country"),
        }.items()
        if not str(value or "").strip()
    ]
    if missing_origin_fields:
        raise EbayIntegrationError(f"Cannot create merchant location without: {', '.join(missing_origin_fields)}")
    payload = _merchant_location_payload(origin_settings)
    try:
        _, response = await _request_with_single_refresh(
            user_id,
            db,
            account,
            method="POST",
            path=f"/sell/inventory/v1/location/{location_key}",
            payload=payload,
        )
        _ = response
    except EbayIntegrationError as exc:
        message = str(exc).lower()
        if "merchantlocationkey already exists" not in message:
            raise
    return {"merchantLocationKey": location_key}


async def suggest_ebay_category(listing: Listing, account: MarketplaceAccount, marketplace_id: str = "EBAY_US") -> dict[str, str]:
    if str(listing.category_suggestion or "").strip().isdigit():
        category_id = str(listing.category_suggestion).strip()
        return {"categoryId": category_id, "categoryName": category_id}
    client = EbayAPIClient(account.access_token)
    tree = await client.request("GET", "/commerce/taxonomy/v1/get_default_category_tree_id", params={"marketplace_id": marketplace_id})
    tree_id = tree.get("categoryTreeId")
    if not tree_id:
        raise EbayIntegrationError("Unable to resolve eBay category tree id")
    suggestions = await client.request(
        "GET",
        f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions",
        params={"q": _ebay_listing_query(listing)},
    )
    first = (suggestions.get("categorySuggestions") or [{}])[0]
    category = first.get("category") or {}
    category_id = str(category.get("categoryId") or "").strip()
    if not category_id:
        fallback_category_id = str(listing.category_id or "").strip() if str(listing.category_id or "").strip().isdigit() else ""
        if not fallback_category_id:
            fallback_category_id = str(listing.category_suggestion or "").strip() if str(listing.category_suggestion or "").strip().isdigit() else ""
        fallback_category_id = fallback_category_id or "171485"
        return {"categoryId": fallback_category_id, "categoryName": fallback_category_id}
    return {
        "categoryId": category_id,
        "categoryName": str(category.get("categoryName") or "").strip(),
    }


async def build_ebay_item_specifics(
    listing: Listing,
    account: MarketplaceAccount,
    category_id: str,
    marketplace_id: str = "EBAY_US",
) -> dict[str, list[str]]:
    required = await get_required_item_specifics(account.access_token, category_id, marketplace_id=marketplace_id)
    title = str(listing.title or "").strip()
    model = _derive_phone_model(title)
    compatible_brand = "For Apple" if re.search(r"\biphone\b|\bipad\b|\bapple\b", title, flags=re.IGNORECASE) else None
    item_type = _derive_item_type(title)
    specifics: dict[str, list[str]] = {}
    for aspect in required.get("aspects", []):
        constraint = aspect.get("aspectConstraint") or {}
        if not constraint.get("aspectRequired"):
            continue
        name = str(aspect.get("localizedAspectName") or "").strip()
        if not name:
            continue
        normalized = name.lower()
        value: str | None = None
        if normalized == "brand":
            value = _derive_brand(listing)
        elif normalized == "compatible brand":
            value = compatible_brand or "Universal"
        elif normalized == "compatible model":
            value = f"For {model}" if model and not model.lower().startswith("for ") else model or "Universal"
        elif normalized == "type":
            value = item_type or "Replacement Part"
        else:
            value = _fallback_aspect_value(listing, name, title)
        if value:
            specifics[name] = [_clip_specific_value(value)]
    if "Brand" not in specifics:
        specifics["Brand"] = [_derive_brand(listing)]
    return specifics


async def create_or_replace_item(
    listing: Listing,
    account: MarketplaceAccount,
    *,
    item_specifics: dict[str, list[str]] | None = None,
    inventory_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = EbayAPIClient(account.access_token)
    sku = _build_ebay_sku(listing.user_id, listing.id)
    payload = inventory_payload or {
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
        "condition": "NEW",
        "product": {
            "title": listing.title or f"PosterPro Listing #{listing.id}",
            "description": _sanitize_ebay_description(listing.description),
            "aspects": item_specifics or {"Brand": [_derive_brand(listing)]},
            "imageUrls": _build_ebay_image_urls(listing),
        },
    }
    if not (payload.get("product") or {}).get("imageUrls"):
        raise EbayIntegrationError(f"Listing {listing.id} has no images to send to eBay")
    response = await client.request("PUT", f"/sell/inventory/v1/inventory_item/{sku}", payload=payload)
    return {"sku": sku, "response": response}


async def create_offer_for_item(
    listing: Listing,
    account: MarketplaceAccount,
    sku: str,
    *,
    category_id: str,
    offer_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = EbayAPIClient(account.access_token)
    payload = offer_payload or {
        "sku": sku,
        "marketplaceId": "EBAY_US",
        "format": "FIXED_PRICE",
        "availableQuantity": 1,
        "categoryId": category_id,
        "merchantLocationKey": f"posterpro-{listing.user_id}",
        "pricingSummary": {"price": {"value": str(listing.suggested_price or 19.99), "currency": "USD"}},
    }
    try:
        response = await client.request("POST", "/sell/inventory/v1/offer", payload=payload)
    except EbayIntegrationError as exc:
        message = str(exc).lower()
        if "not eligible for business policy" not in message:
            existing_offer_id = _extract_ebay_error_parameter(str(exc), "offerId")
            if "offer entity already exists" in message and existing_offer_id:
                await client.request("DELETE", f"/sell/inventory/v1/offer/{existing_offer_id}")
                response = await client.request("POST", "/sell/inventory/v1/offer", payload=payload)
                return {"offerId": response.get("offerId"), "response": response}
            raise
        try:
            policy_ids = await get_business_policy_ids(account.access_token, create_if_missing=True)
            if any(policy_ids.values()):
                payload["listingPolicies"] = {
                    "paymentPolicyId": policy_ids.get("paymentPolicyId"),
                    "returnPolicyId": policy_ids.get("returnPolicyId"),
                    "fulfillmentPolicyId": policy_ids.get("fulfillmentPolicyId"),
                }
            response = await client.request("POST", "/sell/inventory/v1/offer", payload=payload)
        except EbayIntegrationError as retry_exc:
            retry_message = str(retry_exc).lower()
            existing_offer_id = _extract_ebay_error_parameter(str(retry_exc), "offerId")
            if "offer entity already exists" in retry_message and existing_offer_id:
                await client.request("DELETE", f"/sell/inventory/v1/offer/{existing_offer_id}")
                response = await client.request("POST", "/sell/inventory/v1/offer", payload=payload)
                return {"offerId": response.get("offerId"), "response": response}
            raise
    return {"offerId": response.get("offerId"), "response": response}


async def publish_offer(listing: Listing, account: MarketplaceAccount, offer_id: str) -> dict[str, Any]:
    client = EbayAPIClient(account.access_token)
    # Example payload for publishOffer (official field names)
    response = await client.request("POST", f"/sell/inventory/v1/offer/{offer_id}/publish")
    listing_id = response.get("listingId")
    if not listing_id:
        raise EbayIntegrationError("publishOffer did not return listingId")
    return {"listingId": listing_id, "response": response}


async def get_category_tree(access_token: str, marketplace_id: str = "EBAY_US") -> dict[str, Any]:
    client = EbayAPIClient(access_token)
    trees = await client.request("GET", "/commerce/taxonomy/v1/get_default_category_tree_id", params={"marketplace_id": marketplace_id})
    tree_id = trees.get("categoryTreeId")
    if not tree_id:
        return trees
    return await client.request("GET", f"/commerce/taxonomy/v1/category_tree/{tree_id}")


async def get_business_policy_ids(
    access_token: str,
    marketplace_id: str = "EBAY_US",
    *,
    create_if_missing: bool = True,
) -> dict[str, Any]:
    client = EbayAPIClient(access_token)
    headers = {"Content-Language": "en-US"}
    base = f"/sell/account/v1"
    payment = await client.request("GET", f"{base}/payment_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    shipping = await client.request("GET", f"{base}/fulfillment_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    returns = await client.request("GET", f"{base}/return_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    if create_if_missing and not (payment.get("paymentPolicies") or []):
        await client.request(
            "POST",
            f"{base}/payment_policy",
            headers=headers,
            payload={
                "name": "PosterPro Default Payment",
                "marketplaceId": marketplace_id,
                "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
                "paymentMethods": [{"paymentMethodType": "PERSONAL_CHECK"}],
                "immediatePay": False,
            },
        )
        payment = await client.request("GET", f"{base}/payment_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    if create_if_missing and not (shipping.get("fulfillmentPolicies") or []):
        await client.request(
            "POST",
            f"{base}/fulfillment_policy",
            headers=headers,
            payload={
                "name": "PosterPro Default Fulfillment",
                "marketplaceId": marketplace_id,
                "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
                "handlingTime": {"unit": "DAY", "value": 1},
                "shippingOptions": [
                    {
                        "optionType": "DOMESTIC",
                        "costType": "FLAT_RATE",
                        "shippingServices": [
                            {
                                "shippingCarrierCode": "USPS",
                                "shippingServiceCode": "USPSGroundAdvantage",
                                "freeShipping": True,
                                "buyerResponsibleForShipping": False,
                            }
                        ],
                    }
                ],
                "globalShipping": False,
            },
        )
        shipping = await client.request("GET", f"{base}/fulfillment_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    if create_if_missing and not (returns.get("returnPolicies") or []):
        await client.request(
            "POST",
            f"{base}/return_policy",
            headers=headers,
            payload={
                "name": "PosterPro Default Returns",
                "marketplaceId": marketplace_id,
                "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
                "returnsAccepted": False,
            },
        )
        returns = await client.request("GET", f"{base}/return_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    return {
        "paymentPolicyId": (payment.get("paymentPolicies") or [{}])[0].get("paymentPolicyId"),
        "fulfillmentPolicyId": (shipping.get("fulfillmentPolicies") or [{}])[0].get("fulfillmentPolicyId"),
        "returnPolicyId": (returns.get("returnPolicies") or [{}])[0].get("returnPolicyId"),
    }


async def _list_business_policies_for_account(
    user_id: int,
    db: Session,
    account: MarketplaceAccount,
    *,
    marketplace_id: str = "EBAY_US",
) -> dict[str, Any]:
    async def _fetch(current_account: MarketplaceAccount) -> dict[str, Any]:
        return await list_business_policies(current_account.access_token, marketplace_id=marketplace_id)

    try:
        return await _fetch(account)
    except EbayIntegrationError as exc:
        if "(401)" not in str(exc) or not account.refresh_token:
            raise
        try:
            refreshed = await refresh_ebay_token(user_id, db)
        except EbayIntegrationError as refresh_exc:
            _mark_ebay_reauthorization_required(account, db, refresh_exc)
            raise
        return await _fetch(refreshed)


async def _get_business_policy_ids_for_account(
    user_id: int,
    db: Session,
    account: MarketplaceAccount,
    *,
    marketplace_id: str = "EBAY_US",
    create_if_missing: bool = True,
) -> dict[str, Any]:
    async def _fetch(current_account: MarketplaceAccount) -> dict[str, Any]:
        return await get_business_policy_ids(
            current_account.access_token,
            marketplace_id=marketplace_id,
            create_if_missing=create_if_missing,
        )

    try:
        return await _fetch(account)
    except EbayIntegrationError as exc:
        if "(401)" not in str(exc) or not account.refresh_token:
            raise
        try:
            refreshed = await refresh_ebay_token(user_id, db)
        except EbayIntegrationError as refresh_exc:
            _mark_ebay_reauthorization_required(account, db, refresh_exc)
            raise
        return await _fetch(refreshed)


def _policy_summary_entry(policy: dict[str, Any], *, id_field: str, marketplace_id: str) -> dict[str, Any]:
    category_types = []
    for category_type in policy.get("categoryTypes") or []:
        if not isinstance(category_type, dict):
            continue
        name = str(category_type.get("name") or "").strip()
        if name:
            category_types.append(name)
    return {
        "id": str(policy.get(id_field) or "").strip(),
        "name": str(policy.get("name") or "").strip(),
        "marketplace_id": str(policy.get("marketplaceId") or marketplace_id).strip() or marketplace_id,
        "is_default": bool(policy.get("default")),
        "category_types": category_types,
        "raw_category_types": policy.get("categoryTypes") or [],
    }


def _pick_preferred_policy(policies: list[dict[str, Any]], *, id_field: str) -> tuple[dict[str, Any] | None, str]:
    if not policies:
        return None, "missing"
    defaults = [policy for policy in policies if policy.get("is_default")]
    if defaults:
        return defaults[0], "default"
    category_match = [
        policy
        for policy in policies
        if "ALL_EXCLUDING_MOTORS_VEHICLES" in {str(item).upper() for item in policy.get("category_types") or []}
    ]
    if category_match:
        return category_match[0], "all_excluding_motors_vehicles"
    return policies[0], "first_available"


async def list_business_policies(access_token: str, marketplace_id: str = "EBAY_US") -> dict[str, Any]:
    client = EbayAPIClient(access_token)
    headers = {"Content-Language": "en-US"}
    base = "/sell/account/v1"
    payment = await client.request("GET", f"{base}/payment_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    shipping = await client.request("GET", f"{base}/fulfillment_policy", params={"marketplace_id": marketplace_id}, headers=headers)
    returns = await client.request("GET", f"{base}/return_policy", params={"marketplace_id": marketplace_id}, headers=headers)

    payment_policies = [_policy_summary_entry(policy, id_field="paymentPolicyId", marketplace_id=marketplace_id) for policy in payment.get("paymentPolicies") or [] if isinstance(policy, dict)]
    fulfillment_policies = [_policy_summary_entry(policy, id_field="fulfillmentPolicyId", marketplace_id=marketplace_id) for policy in shipping.get("fulfillmentPolicies") or [] if isinstance(policy, dict)]
    return_policies = [_policy_summary_entry(policy, id_field="returnPolicyId", marketplace_id=marketplace_id) for policy in returns.get("returnPolicies") or [] if isinstance(policy, dict)]

    selected_payment, payment_reason = _pick_preferred_policy(payment_policies, id_field="paymentPolicyId")
    selected_fulfillment, fulfillment_reason = _pick_preferred_policy(fulfillment_policies, id_field="fulfillmentPolicyId")
    selected_return, return_reason = _pick_preferred_policy(return_policies, id_field="returnPolicyId")

    return {
        "marketplace_id": marketplace_id,
        "payment_policies": payment_policies,
        "fulfillment_policies": fulfillment_policies,
        "return_policies": return_policies,
        "selected": {
            "payment_policy_id": selected_payment["id"] if selected_payment else "",
            "payment_policy_name": selected_payment["name"] if selected_payment else "",
            "payment_selection_reason": payment_reason,
            "fulfillment_policy_id": selected_fulfillment["id"] if selected_fulfillment else "",
            "fulfillment_policy_name": selected_fulfillment["name"] if selected_fulfillment else "",
            "fulfillment_selection_reason": fulfillment_reason,
            "return_policy_id": selected_return["id"] if selected_return else "",
            "return_policy_name": selected_return["name"] if selected_return else "",
            "return_selection_reason": return_reason,
        },
    }


def _normalize_merchant_location_origin(raw: dict | None) -> dict[str, str]:
    source = raw if isinstance(raw, dict) else {}
    return {
        "location_name": str(source.get("merchant_location_location_name") or "PosterPro Default Location").strip(),
        "postal_code": str(source.get("merchant_location_postal_code") or "").strip(),
        "country": str(source.get("merchant_location_country") or "").strip(),
        "city": str(source.get("merchant_location_city") or "").strip(),
        "state_or_province": str(source.get("merchant_location_state_or_province") or "").strip(),
        "phone": str(source.get("merchant_location_phone") or "").strip(),
    }


def _merchant_location_payload(origin: dict[str, str]) -> dict[str, Any]:
    return {
        "name": origin.get("location_name") or "PosterPro Default Location",
        "location": {
            "address": {
                "addressLine1": "123 Marketplace St",
                "city": origin.get("city") or "San Jose",
                "stateOrProvince": origin.get("state_or_province") or "CA",
                "postalCode": origin.get("postal_code") or "",
                "country": origin.get("country") or "",
            }
        },
        "merchantLocationStatus": "ENABLED",
        "locationTypes": ["WAREHOUSE"],
    }


async def sync_business_policies(
    user_id: int,
    db: Session,
    *,
    marketplace_id: str = "EBAY_US",
    create_missing_defaults: bool = False,
) -> dict[str, Any]:
    account = await get_or_refresh_account(user_id, db)
    # Policy selection is user-owned configuration.  Load it before building
    # the sync response so a harmless catalog read cannot fail with an
    # unbound local variable and leave the publishing UI in a false "blocked"
    # state.
    policy_settings = _load_listing_ebay_policy_settings(db, user_id)
    policy_catalog = await _list_business_policies_for_account(
        user_id,
        db,
        account,
        marketplace_id=marketplace_id,
    )
    selected = policy_catalog["selected"]
    payment_policies = policy_catalog["payment_policies"]
    fulfillment_policies = policy_catalog["fulfillment_policies"]
    return_policies = policy_catalog["return_policies"]

    missing_types = []
    if not payment_policies:
        missing_types.append("payment")
    if not fulfillment_policies:
        missing_types.append("fulfillment")
    if not return_policies:
        missing_types.append("return")

    settings_updates: dict[str, Any] = {
        "marketplace_id": marketplace_id,
        "policy_candidates": {
            "payment": payment_policies,
            "fulfillment": fulfillment_policies,
            "return": return_policies,
        },
        "last_policy_sync_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "policy_sync_error": "",
        "shipping_service_code": policy_settings.get("shipping_service_code") or "USPSGroundAdvantage",
    }

    if missing_types:
        if not create_missing_defaults:
            settings_updates["policy_sync_status"] = "blocked"
            settings_updates["policy_sync_error"] = f"Missing eBay policies: {', '.join(missing_types)}"
            return {
                "status": "blocked",
                "marketplace_id": marketplace_id,
                "missing_policy_types": missing_types,
                "policy_catalog": policy_catalog,
                "settings_updates": settings_updates,
            }
        policy_ids = await _get_business_policy_ids_for_account(
            user_id,
            db,
            account,
            marketplace_id=marketplace_id,
            create_if_missing=True,
        )
        policy_catalog = await _list_business_policies_for_account(
            user_id,
            db,
            account,
            marketplace_id=marketplace_id,
        )
        payment_policies = policy_catalog["payment_policies"]
        fulfillment_policies = policy_catalog["fulfillment_policies"]
        return_policies = policy_catalog["return_policies"]
        selected = policy_catalog["selected"]
        settings_updates["policy_candidates"] = {
            "payment": payment_policies,
            "fulfillment": fulfillment_policies,
            "return": return_policies,
        }
        settings_updates["policy_sync_status"] = "created_defaults"
    else:
        policy_ids = await _get_business_policy_ids_for_account(
            user_id,
            db,
            account,
            marketplace_id=marketplace_id,
            create_if_missing=False,
        )
        settings_updates["policy_sync_status"] = "synced"

    settings_updates.update(
        {
            "payment_policy_id": selected.get("payment_policy_id") or policy_ids.get("paymentPolicyId") or "",
            "payment_policy_name": selected.get("payment_policy_name") or "",
            "fulfillment_policy_id": selected.get("fulfillment_policy_id") or policy_ids.get("fulfillmentPolicyId") or "",
            "fulfillment_policy_name": selected.get("fulfillment_policy_name") or "",
            "return_policy_id": selected.get("return_policy_id") or policy_ids.get("returnPolicyId") or "",
            "return_policy_name": selected.get("return_policy_name") or "",
            "marketplace_id": marketplace_id,
            "shipping_service_code": policy_settings.get("shipping_service_code") or "USPSGroundAdvantage",
        }
    )

    return {
        "status": "updated",
        "marketplace_id": marketplace_id,
        "policy_catalog": policy_catalog,
        "selected": selected,
        "missing_policy_types": missing_types,
        "settings_updates": settings_updates,
    }


async def verify_merchant_location(
    user_id: int,
    db: Session,
    *,
    location_key: str | None = None,
    create_if_missing: bool = False,
    origin: dict[str, str] | None = None,
) -> dict[str, Any]:
    account = await get_or_refresh_account(user_id, db)
    policy_settings = _load_listing_ebay_policy_settings(db, user_id)
    resolved_key = str(location_key or policy_settings.get("merchant_location_key") or f"posterpro-{user_id}").strip()
    origin_settings = _normalize_merchant_location_origin(policy_settings)
    if isinstance(origin, dict):
        for field, key in (
            ("merchant_location_location_name", "location_name"),
            ("merchant_location_postal_code", "postal_code"),
            ("merchant_location_country", "country"),
            ("merchant_location_city", "city"),
            ("merchant_location_state_or_province", "state_or_province"),
            ("merchant_location_phone", "phone"),
        ):
            value = str(origin.get(field) or "").strip()
            if value:
                origin_settings[key] = value
    client = EbayAPIClient(account.access_token)
    settings_updates: dict[str, Any] = {
        "merchant_location_key": resolved_key,
        "merchant_location_error": "",
        "merchant_location_last_checked_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
    }

    try:
        _, location = await _request_with_single_refresh(
            user_id,
            db,
            account,
            method="GET",
            path=f"/sell/inventory/v1/location/{resolved_key}",
        )
        address = (location.get("location") or {}).get("address") if isinstance(location.get("location"), dict) else {}
        address = address if isinstance(address, dict) else {}
        settings_updates.update(
            {
                "merchant_location_verified": True,
                "merchant_location_status": "verified",
                "merchant_location_location_name": str(location.get("name") or origin_settings.get("location_name") or "").strip() or "PosterPro Default Location",
                "merchant_location_postal_code": str(address.get("postalCode") or origin_settings.get("postal_code") or "").strip(),
                "merchant_location_country": str(address.get("country") or origin_settings.get("country") or "").strip(),
                "merchant_location_city": str(address.get("city") or origin_settings.get("city") or "").strip(),
                "merchant_location_state_or_province": str(address.get("stateOrProvince") or origin_settings.get("state_or_province") or "").strip(),
            }
        )
        return {
            "status": "verified",
            "marketplace_id": "EBAY_US",
            "merchant_location_key": resolved_key,
            "merchant_location": location,
            "settings_updates": settings_updates,
        }
    except EbayIntegrationError as exc:
        message = str(exc)
        lower = message.lower()
        if "404" not in lower and "not found" not in lower and "does not exist" not in lower:
            settings_updates.update(
                {
                    "merchant_location_verified": False,
                    "merchant_location_status": "error",
                    "merchant_location_error": message,
                }
            )
            return {
                "status": "error",
                "marketplace_id": "EBAY_US",
                "merchant_location_key": resolved_key,
                "error": message,
                "settings_updates": settings_updates,
            }
        if not create_if_missing:
            settings_updates.update(
                {
                    "merchant_location_verified": False,
                    "merchant_location_status": "missing",
                    "merchant_location_error": message,
                }
            )
            return {
                "status": "blocked",
                "marketplace_id": "EBAY_US",
                "merchant_location_key": resolved_key,
                "missing_fields": [
                    field
                    for field, value in {
                        "merchant_location_postal_code": origin.get("postal_code"),
                        "merchant_location_country": origin.get("country"),
                    }.items()
                    if not str(value or "").strip()
                ],
                "error": message,
                "settings_updates": settings_updates,
            }

        missing_origin_fields = [
            field
            for field, value in {
                "merchant_location_postal_code": origin_settings.get("postal_code"),
                "merchant_location_country": origin_settings.get("country"),
            }.items()
            if not str(value or "").strip()
        ]
        if missing_origin_fields:
            settings_updates.update(
                {
                    "merchant_location_verified": False,
                    "merchant_location_status": "blocked",
                    "merchant_location_error": f"Missing origin fields: {', '.join(missing_origin_fields)}",
                }
            )
            return {
                "status": "blocked",
                "marketplace_id": "EBAY_US",
                "merchant_location_key": resolved_key,
                "missing_fields": missing_origin_fields,
                "error": f"Missing origin fields: {', '.join(missing_origin_fields)}",
                "settings_updates": settings_updates,
            }

        created = await create_inventory_location(user_id, db, location_key=resolved_key, origin=origin_settings)
        settings_updates.update(
            {
                "merchant_location_verified": True,
                "merchant_location_status": "created",
            }
        )
        return {
            "status": "created",
            "marketplace_id": "EBAY_US",
            "merchant_location_key": created.get("merchantLocationKey") or resolved_key,
            "settings_updates": settings_updates,
        }


async def get_required_item_specifics(access_token: str, category_id: str, marketplace_id: str = "EBAY_US") -> dict[str, Any]:
    client = EbayAPIClient(access_token)
    tree = await client.request("GET", "/commerce/taxonomy/v1/get_default_category_tree_id", params={"marketplace_id": marketplace_id})
    tree_id = tree.get("categoryTreeId")
    if not tree_id:
        raise EbayIntegrationError("Unable to resolve category tree id")
    return await client.request(
        "GET",
        f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category",
        params={"category_id": category_id},
    )


def _normalize_ebay_policy_settings(raw: dict | None) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    policy_candidates = source.get("policy_candidates") if isinstance(source.get("policy_candidates"), dict) else {}
    return {
        "fulfillment_policy_id": str(source.get("fulfillment_policy_id") or "").strip(),
        "fulfillment_policy_name": str(source.get("fulfillment_policy_name") or "").strip(),
        "payment_policy_id": str(source.get("payment_policy_id") or "").strip(),
        "payment_policy_name": str(source.get("payment_policy_name") or "").strip(),
        "return_policy_id": str(source.get("return_policy_id") or "").strip(),
        "return_policy_name": str(source.get("return_policy_name") or "").strip(),
        "merchant_location_key": str(source.get("merchant_location_key") or "").strip(),
        "merchant_location_verified": bool(source.get("merchant_location_verified")),
        "merchant_location_status": str(source.get("merchant_location_status") or "").strip(),
        "merchant_location_last_checked_at": str(source.get("merchant_location_last_checked_at") or "").strip(),
        "merchant_location_error": str(source.get("merchant_location_error") or "").strip(),
        "merchant_location_location_name": str(source.get("merchant_location_location_name") or "PosterPro Default Location").strip(),
        "merchant_location_postal_code": str(source.get("merchant_location_postal_code") or "95125").strip(),
        "merchant_location_country": str(source.get("merchant_location_country") or "US").strip(),
        "merchant_location_city": str(source.get("merchant_location_city") or "San Jose").strip(),
        "merchant_location_state_or_province": str(source.get("merchant_location_state_or_province") or "CA").strip(),
        "merchant_location_phone": str(source.get("merchant_location_phone") or "").strip(),
        "shipping_service_code": str(source.get("shipping_service_code") or "USPSGroundAdvantage").strip(),
        "handling_time_days": max(1, int(source.get("handling_time_days") or 1)),
        "local_pickup_allowed": bool(source.get("local_pickup_allowed")),
        "calculated_shipping": bool(source.get("calculated_shipping")),
        "package_weight_required": bool(source.get("package_weight_required", True)),
        "package_dimensions_required": bool(source.get("package_dimensions_required", True)),
        "marketplace_id": str(source.get("marketplace_id") or "EBAY_US").strip() or "EBAY_US",
        "last_policy_sync_at": str(source.get("last_policy_sync_at") or "").strip(),
        "policy_sync_status": str(source.get("policy_sync_status") or "uninitialized").strip(),
        "policy_sync_error": str(source.get("policy_sync_error") or "").strip(),
        "policy_candidates": policy_candidates,
    }


def _load_listing_ebay_policy_settings(db: Session, user_id: int) -> dict[str, Any]:
    user = db.get(User, user_id)
    settings_json = user.settings_json if user else {}
    raw = settings_json.get("ebay_marketplace_policy_settings") if isinstance(settings_json, dict) else {}
    return _normalize_ebay_policy_settings(raw if isinstance(raw, dict) else {})


async def _cached_category_aspects(
    db: Session,
    account: MarketplaceAccount,
    category_id: str,
    *,
    marketplace_id: str = "EBAY_US",
    force_refresh: bool = False,
) -> tuple[dict[str, Any] | None, str, bool]:
    cache_key = f"ebay:{marketplace_id}:{category_id}:aspects"
    cached_rows = db.execute(
        select(MarketplaceMetadataCache).where(
            MarketplaceMetadataCache.marketplace == "ebay",
            MarketplaceMetadataCache.cache_key == cache_key,
        )
        .order_by(MarketplaceMetadataCache.updated_at.desc(), MarketplaceMetadataCache.id.desc())
    ).scalars().all()
    cached = cached_rows[0] if cached_rows else None
    if cached and cached.payload and cached.expires_at and cached.expires_at > datetime.utcnow() and not force_refresh:
        return cached.payload, "cache", True
    try:
        payload = await get_required_item_specifics(account.access_token, category_id, marketplace_id=marketplace_id)
    except Exception:
        if cached and cached.payload:
            return cached.payload, "cache_stale", True
        return None, "unavailable", False

    expires_at = datetime.utcnow() + timedelta(days=7)
    row = cached or MarketplaceMetadataCache(
        marketplace="ebay",
        cache_key=cache_key,
    )
    row.payload = payload
    row.source_version = "ebay_taxonomy_v1"
    row.expires_at = expires_at
    db.add(row)
    db.commit()
    return payload, "live", True


def _flatten_aspect_values(values: Any) -> list[str]:
    if isinstance(values, list):
        return [str(value).strip() for value in values if str(value).strip()]
    if values is None:
        return []
    text = str(values).strip()
    return [text] if text else []


def _ebay_candidate_aspect_values(listing: Listing) -> dict[str, str]:
    specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
    source = listing.source_metadata if isinstance(listing.source_metadata, dict) else {}
    condition = listing.condition_data if isinstance(listing.condition_data, dict) else {}
    title = str(listing.title or "").strip()
    candidates = {
        "brand": _derive_brand(listing),
        "model": str(specifics.get("Model") or specifics.get("MPN") or source.get("model") or "").strip(),
        "mpn": str(specifics.get("MPN") or source.get("mpn") or "").strip(),
        "upc": str(specifics.get("UPC") or source.get("upc") or "").strip(),
        "ean": str(specifics.get("EAN") or source.get("ean") or "").strip(),
        "isbn": str(specifics.get("ISBN") or source.get("isbn") or "").strip(),
        "color": str(specifics.get("Color") or source.get("color") or "").strip(),
        "size": str(specifics.get("Size") or source.get("size") or "").strip(),
        "material": str(specifics.get("Material") or source.get("material") or "").strip(),
        "type": str(specifics.get("Type") or source.get("type") or _derive_item_type(title) or "").strip(),
        "style": str(specifics.get("Style") or source.get("style") or "").strip(),
        "compatible brand": str(specifics.get("Compatible Brand") or source.get("compatible_brand") or "").strip(),
        "compatible model": str(specifics.get("Compatible Model") or source.get("compatible_model") or "").strip(),
        "condition": str(condition.get("condition_bucket") or listing.condition or "").strip(),
        "condition description": str(condition.get("item_condition_notes") or listing.description or "").strip(),
    }
    return {key: value for key, value in candidates.items() if value}


def _ebay_condition_value(listing: Listing) -> str:
    condition_data = listing.condition_data if isinstance(listing.condition_data, dict) else {}
    bucket = str(condition_data.get("condition_bucket") or listing.condition or "").strip().lower()
    if str(listing.source_type or "").strip().lower() == "amazon_vine":
        return "NEW"
    if bucket in {"new", "new_in_box", "brand_new"}:
        return "NEW"
    if bucket in {"parts_only", "for_parts", "for_parts_or_not_working"}:
        return "FOR_PARTS_OR_NOT_WORKING"
    if bucket in {"open_box", "open_box_or_used_unknown", "used", "used_good", "used_like_new", "used_very_good"}:
        return "USED_GOOD"
    return "USED_GOOD"


def _is_placeholder_specific_value(name: str, values: list[str]) -> bool:
    normalized_name = str(name or "").strip().lower()
    normalized_values = [str(value or "").strip().lower() for value in values if str(value or "").strip()]
    if not normalized_values:
        return True
    placeholders = {"does not apply", "n/a", "na", "unknown", "not applicable"}
    if all(value in placeholders for value in normalized_values):
        return True
    if normalized_name == "compatible model" and all(value in {"for asin", "universal"} for value in normalized_values):
        return True
    return False


def _sanitize_ebay_specific_values(name: str, values: list[str]) -> list[str]:
    normalized_name = str(name or "").strip().lower()
    cleaned: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if normalized_name == "eprel registration number" and not re.fullmatch(r"\d{1,19}", text):
            continue
        cleaned.append(_clip_specific_value(text))
    return cleaned


def _cap_ebay_item_specifics(
    mapped: dict[str, list[str]],
    required_aspects: list[dict[str, Any]],
    recommended_aspects: list[dict[str, Any]],
) -> dict[str, list[str]]:
    max_specifics = 45
    if len(mapped) <= max_specifics:
        return mapped

    required_names = [str(item.get("localizedAspectName") or "").strip() for item in required_aspects if str(item.get("localizedAspectName") or "").strip()]
    recommended_names = [str(item.get("localizedAspectName") or "").strip() for item in recommended_aspects if str(item.get("localizedAspectName") or "").strip()]
    priority_names = []
    seen: set[str] = set()
    for name in [*required_names, *recommended_names, "Brand", "Type", "Model", "MPN", "Color", "Size", "Material"]:
        if name and name in mapped and name not in seen:
            seen.add(name)
            priority_names.append(name)

    capped: dict[str, list[str]] = {}
    for name in priority_names:
        capped[name] = mapped[name]
        if len(capped) >= max_specifics:
            return capped

    for name, values in mapped.items():
        if name in capped:
            continue
        capped[name] = values
        if len(capped) >= max_specifics:
            break
    return capped


def _build_ebay_package_weight_and_size(listing: Listing) -> dict[str, Any] | None:
    shipping = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
    weight_value = _coerce_positive_float(shipping.get("package_weight") or shipping.get("item_weight"))
    dimensions = shipping.get("package_dimensions") if isinstance(shipping.get("package_dimensions"), dict) else {}
    length = _coerce_positive_int(dimensions.get("length"))
    width = _coerce_positive_int(dimensions.get("width"))
    height = _coerce_positive_int(dimensions.get("height"))

    payload: dict[str, Any] = {}
    if weight_value is not None:
        payload["weight"] = {"value": weight_value, "unit": "POUND"}
    if length and width and height:
        payload["dimensions"] = {
            "length": length,
            "width": width,
            "height": height,
            "unit": "INCH",
        }
    return payload or None


def _flatten_ebay_specifics_for_storage(mapped_specifics: dict[str, list[str]] | None) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if not isinstance(mapped_specifics, dict):
        return flattened
    for key, values in mapped_specifics.items():
        name = str(key or "").strip()
        if not name:
            continue
        if isinstance(values, list):
            cleaned = [str(value).strip() for value in values if str(value or "").strip()]
        else:
            cleaned = [str(values).strip()] if str(values or "").strip() else []
        if not cleaned:
            continue
        flattened[name] = cleaned[0] if len(cleaned) == 1 else cleaned
    return flattened


def _apply_ebay_plan_repairs_to_listing(listing: Listing, plan: dict[str, Any]) -> None:
    payload_preview = plan.get("payload_preview") if isinstance(plan.get("payload_preview"), dict) else {}
    category = plan.get("category") if isinstance(plan.get("category"), dict) else {}
    category_id = str(category.get("category_id") or "").strip()
    if category_id.isdigit():
        listing.category_suggestion = category_id

    flattened_specifics = _flatten_ebay_specifics_for_storage(payload_preview.get("item_specifics"))
    if flattened_specifics:
        listing.item_specifics = flattened_specifics

    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    listing.marketplace_data = {
        **marketplace_data,
        "ebay_last_resolved_category": {
            "category_id": category_id,
            "category_name": str(category.get("category_name") or "").strip(),
            "source": str(category.get("source") or "").strip(),
        },
        "ebay_item_specifics_provenance": payload_preview.get("item_specifics_provenance") or {},
        "ebay_item_specifics_approximate": payload_preview.get("item_specifics_approximate") or [],
        "ebay_last_auto_repair_at": datetime.now(UTC).isoformat(),
    }


def _map_ebay_item_specifics(
    listing: Listing,
    aspects: dict[str, Any] | None,
) -> tuple[dict[str, list[str]], list[dict[str, Any]], list[dict[str, Any]], list[str], list[str], dict[str, str]]:
    raw_aspects = (aspects or {}).get("aspects") or []
    allowed: dict[str, dict[str, Any]] = {}
    required: list[dict[str, Any]] = []
    recommended: list[dict[str, Any]] = []
    optional: list[dict[str, Any]] = []
    for aspect in raw_aspects:
        if not isinstance(aspect, dict):
            continue
        name = str(aspect.get("localizedAspectName") or "").strip()
        if not name:
            continue
        allowed[name.lower()] = aspect
        constraint = aspect.get("aspectConstraint") or {}
        if constraint.get("aspectRequired"):
            required.append(aspect)
        elif constraint.get("aspectEnabledForVariations"):
            recommended.append(aspect)
        else:
            optional.append(aspect)

    existing_specifics = listing.item_specifics if isinstance(listing.item_specifics, dict) else {}
    draft_provenance = {}
    if isinstance(listing.marketplace_data, dict):
        raw_provenance = listing.marketplace_data.get("ebay_item_specifics_provenance")
        if isinstance(raw_provenance, dict):
            draft_provenance = {str(key): str(value) for key, value in raw_provenance.items() if str(key).strip()}
    candidate_values = _ebay_candidate_aspect_values(listing)
    mapped: dict[str, list[str]] = {}
    missing_required: list[str] = []
    unsupported: list[str] = []
    provenance: dict[str, str] = {}

    for key, value in existing_specifics.items():
        normalized = str(key).strip().lower()
        if normalized and normalized not in allowed:
            unsupported.append(str(key))
        if normalized in allowed and value not in (None, "", []):
            flattened = _sanitize_ebay_specific_values(str(key), _flatten_aspect_values(value))
            if not flattened or _is_placeholder_specific_value(str(key), flattened):
                continue
            mapped[str(key)] = flattened
            provenance[str(key)] = draft_provenance.get(str(key), "existing")

    for aspect in [*required, *recommended, *optional]:
        name = str(aspect.get("localizedAspectName") or "").strip()
        normalized = name.lower()
        constraint = aspect.get("aspectConstraint") or {}
        required_value = bool(constraint.get("aspectRequired"))
        if name in mapped and mapped[name]:
            continue
        candidate = candidate_values.get(normalized)
        if candidate:
            cleaned_candidate = _sanitize_ebay_specific_values(name, [candidate[:80]])
            if not cleaned_candidate or _is_placeholder_specific_value(name, cleaned_candidate):
                if required_value:
                    missing_required.append(name)
                continue
            mapped[name] = cleaned_candidate
            provenance[name] = "derived"
            continue
        fallback = _fallback_aspect_value(listing, name, str(listing.title or ""))
        if fallback:
            cleaned_fallback = _sanitize_ebay_specific_values(name, [_clip_specific_value(fallback)])
            if not cleaned_fallback or _is_placeholder_specific_value(name, cleaned_fallback):
                if required_value:
                    missing_required.append(name)
                continue
            mapped[name] = cleaned_fallback
            provenance[name] = "approximate"
            continue
        if required_value:
            missing_required.append(name)

    if "Brand" not in mapped and candidate_values.get("brand"):
        cleaned_brand = _sanitize_ebay_specific_values("Brand", [candidate_values["brand"][:80]])
        if cleaned_brand and not _is_placeholder_specific_value("Brand", cleaned_brand):
            mapped["Brand"] = cleaned_brand
            provenance["Brand"] = "derived"
    if "Brand" not in mapped:
        mapped["Brand"] = [_derive_brand(listing)]
        provenance["Brand"] = draft_provenance.get("Brand", "default")

    mapped = _cap_ebay_item_specifics(mapped, required, recommended)
    return mapped, required, recommended, missing_required, unsupported, provenance


async def build_ebay_publish_plan(
    listing: Listing,
    db: Session,
    *,
    marketplace_id: str = "EBAY_US",
    allow_create_policies: bool = False,
) -> dict[str, Any]:
    account = await get_or_refresh_account(listing.user_id, db)
    policy_settings = _load_listing_ebay_policy_settings(db, listing.user_id)
    candidate_values = _ebay_candidate_aspect_values(listing)
    category_data = await suggest_ebay_category(listing, account, marketplace_id=marketplace_id)
    category_id = str(category_data.get("categoryId") or "").strip()
    aspect_payload, category_aspect_source, category_aspect_available = await _cached_category_aspects(
        db,
        account,
        category_id,
        marketplace_id=marketplace_id,
    ) if category_id else (None, "unavailable", False)
    mapped_specifics, required_aspects, recommended_aspects, missing_required, unsupported, provenance = _map_ebay_item_specifics(
        listing,
        aspect_payload,
    )
    image_urls = _build_ebay_image_urls(listing)
    if not image_urls:
        raise EbayIntegrationError(f"Listing {listing.id} has no images to send to eBay")

    live_policy_ids = {}
    if all(policy_settings.get(key) for key in ("payment_policy_id", "fulfillment_policy_id", "return_policy_id")):
        live_policy_ids = {
            "paymentPolicyId": policy_settings.get("payment_policy_id") or "",
            "fulfillmentPolicyId": policy_settings.get("fulfillment_policy_id") or "",
            "returnPolicyId": policy_settings.get("return_policy_id") or "",
        }
    elif allow_create_policies:
        live_policy_ids = await get_business_policy_ids(account.access_token, marketplace_id=marketplace_id, create_if_missing=True)
        policy_settings = {
            **policy_settings,
            "payment_policy_id": live_policy_ids.get("paymentPolicyId") or policy_settings.get("payment_policy_id") or "",
            "fulfillment_policy_id": live_policy_ids.get("fulfillmentPolicyId") or policy_settings.get("fulfillment_policy_id") or "",
            "return_policy_id": live_policy_ids.get("returnPolicyId") or policy_settings.get("return_policy_id") or "",
        }
    else:
        live_policy_ids = await get_business_policy_ids(account.access_token, marketplace_id=marketplace_id, create_if_missing=False)

    sku = _build_ebay_sku(listing.user_id, listing.id)
    sku = _build_ebay_sku(listing.user_id, listing.id)
    price = listing.suggested_price or listing.listing_price or listing.buy_it_now_price or listing.estimated_value or 19.99
    package_weight_and_size = _build_ebay_package_weight_and_size(listing)
    inventory_payload = {
        "sku": sku,
        "availability": {"shipToLocationAvailability": {"quantity": max(1, int(listing.quantity or 1))}},
        "condition": _ebay_condition_value(listing),
        "product": {
            "title": listing.title or f"PosterPro Listing #{listing.id}",
            "description": _sanitize_ebay_description(listing.description),
            "aspects": mapped_specifics,
            "imageUrls": image_urls,
        },
    }
    if package_weight_and_size:
        inventory_payload["packageWeightAndSize"] = package_weight_and_size
    offer_payload = {
        "sku": sku,
        "marketplaceId": marketplace_id,
        "format": "FIXED_PRICE",
        "availableQuantity": max(1, int(listing.quantity or 1)),
        "categoryId": category_id,
        "merchantLocationKey": policy_settings.get("merchant_location_key") or f"posterpro-{listing.user_id}",
        "pricingSummary": {"price": {"value": str(price), "currency": "USD"}},
    }
    shipping_policy = {
        "merchantLocationKey": offer_payload["merchantLocationKey"],
        "fulfillmentPolicyId": live_policy_ids.get("fulfillmentPolicyId") or None,
        "paymentPolicyId": live_policy_ids.get("paymentPolicyId") or None,
        "returnPolicyId": live_policy_ids.get("returnPolicyId") or None,
        "handlingTime": policy_settings.get("handling_time_days"),
        "shippingServiceCode": policy_settings.get("shipping_service_code") or "USPSGroundAdvantage",
        "calculatedShipping": bool(policy_settings.get("calculated_shipping")),
        "localPickupAllowed": bool(policy_settings.get("local_pickup_allowed")),
    }
    if live_policy_ids.get("paymentPolicyId") and live_policy_ids.get("fulfillmentPolicyId") and live_policy_ids.get("returnPolicyId"):
        offer_payload["listingPolicies"] = {
            "paymentPolicyId": live_policy_ids.get("paymentPolicyId"),
            "returnPolicyId": live_policy_ids.get("returnPolicyId"),
            "fulfillmentPolicyId": live_policy_ids.get("fulfillmentPolicyId"),
        }
    return {
        "marketplace": "ebay",
        "listing_id": listing.id,
        "sku": sku,
        "category": {
            "category_id": category_id,
            "category_name": str(category_data.get("categoryName") or "").strip() or category_id,
            "source": "listing.category_suggestion" if str(listing.category_suggestion or "").strip().isdigit() else "ebay_taxonomy",
            "metadata_source": category_aspect_source,
            "metadata_available": category_aspect_available,
        },
        "aspect_summary": {
            "required": [str(item.get("localizedAspectName") or "").strip() for item in required_aspects],
            "recommended": [str(item.get("localizedAspectName") or "").strip() for item in recommended_aspects],
            "missing_required": missing_required,
            "unsupported": unsupported,
        },
        "policy_settings": policy_settings,
        "policy_ids": live_policy_ids,
        "inventory_item_payload": inventory_payload,
        "offer_payload": offer_payload,
        "payload_preview": {
            "sku": sku,
            "title": inventory_payload["product"]["title"],
            "description": inventory_payload["product"]["description"],
            "condition": inventory_payload["condition"],
            "conditionDescription": (listing.condition_data or {}).get("item_condition_notes") or listing.description or "",
            "category_id": category_id,
            "quantity": offer_payload["availableQuantity"],
            "price": price,
            "currency": "USD",
            "product_identifiers": {
                key.upper(): value
                for key, value in {
                    "upc": candidate_values.get("upc"),
                    "ean": candidate_values.get("ean"),
                    "isbn": candidate_values.get("isbn"),
                    "mpn": candidate_values.get("mpn"),
                }.items()
                if value
            },
            "item_specifics": mapped_specifics,
            "item_specifics_provenance": provenance,
            "item_specifics_approximate": [field for field, source in provenance.items() if source in {"derived", "approximate", "default"}],
            "image_urls": image_urls,
            "packageWeightAndSize": package_weight_and_size or {},
            "shipping_policy": shipping_policy,
            "marketplaceId": marketplace_id,
            "listing_format": "FIXED_PRICE",
            "duration": "GTC",
            "site": marketplace_id,
        },
        "image_summary": {
            "image_count": len(image_urls),
            "actual_image_count": len([image for image in (listing.listing_images or []) if isinstance(image, dict) and not image.get("is_reference") and image.get("operator_state") != "rejected"]),
            "reference_only": bool(listing.listing_images) and not any(
                isinstance(image, dict) and not image.get("is_reference") and image.get("operator_state") != "rejected"
                for image in (listing.listing_images or [])
            ),
        },
        "account_id": account.id,
        "account_summary": {
            "marketplace": account.marketplace.value if hasattr(account.marketplace, "value") else str(account.marketplace),
            "external_account_id": account.external_account_id,
            "has_refresh_token": bool(account.refresh_token),
            "token_expires_at": account.token_expires_at.isoformat() if account.token_expires_at else None,
        },
        "shipping_summary": {
            "shipping_profile": listing.shipping_profile or {},
            "shipping_policy": shipping_policy,
        },
    }


async def get_incoming_best_offers(
    account: MarketplaceAccount,
    *,
    limit: int = 100,
    marketplace_id: str = "EBAY_US",
) -> list[dict[str, Any]]:
    """
    Pull incoming buyer offers from eBay's negotiation API.
    """
    client = EbayAPIClient(account.access_token)
    response = await client.request(
        "GET",
        "/sell/negotiation/v1/find_offers",
        params={
            "limit": limit,
            "marketplace_id": marketplace_id,
            "offer_type": "COUNTER_OFFER",
        },
    )
    offers = response.get("offers") or response.get("bestOffers") or []
    return [offer for offer in offers if isinstance(offer, dict)]


async def get_fulfillment_orders(
    account: MarketplaceAccount,
    *,
    limit: int = 50,
    offset: int = 0,
    filter_expression: str | None = None,
) -> list[dict[str, Any]]:
    """Pull paid/completed orders from eBay Fulfillment API."""
    client = EbayAPIClient(account.access_token)
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if filter_expression:
        params["filter"] = filter_expression
    response = await client.request("GET", "/sell/fulfillment/v1/order", params=params)
    orders = response.get("orders") or []
    return [order for order in orders if isinstance(order, dict)]


async def get_active_ebay_listings(
    user_id: int,
    db: Session,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    account = await get_or_refresh_account(user_id, db)
    client = EbayAPIClient(account.access_token)

    async def _offer_page(*, page_limit: int, offset: int) -> dict[str, Any]:
        nonlocal account, client
        try:
            return await client.request("GET", "/sell/inventory/v1/offer", params={"limit": page_limit, "offset": offset})
        except EbayIntegrationError as exc:
            # eBay can revoke an access token before the locally supplied
            # expiry. Refresh once, then retry the same *read-only* page.
            if "(401)" not in str(exc) or not account.refresh_token:
                raise
            try:
                account = await refresh_ebay_token(user_id, db)
            except EbayIntegrationError as refresh_exc:
                _mark_ebay_reauthorization_required(account, db, refresh_exc)
                raise
            client = EbayAPIClient(account.access_token)
            try:
                return await client.request("GET", "/sell/inventory/v1/offer", params={"limit": page_limit, "offset": offset})
            except EbayIntegrationError as retry_exc:
                if "(401)" in str(retry_exc):
                    _mark_ebay_reauthorization_required(account, db, retry_exc)
                raise

    requested_limit = max(1, int(limit or 1))
    page_limit = min(requested_limit, 100)
    offers: list[dict[str, Any]] = []
    offset = 0
    try:
        while len(offers) < requested_limit:
            offers_response = await _offer_page(page_limit=min(page_limit, requested_limit - len(offers)), offset=offset)
            page = [offer for offer in (offers_response.get("offers") or []) if isinstance(offer, dict)]
            offers.extend(page)
            if len(page) < page_limit:
                break
            offset += len(page)
    except EbayIntegrationError as exc:
        if not _is_inventory_sku_catalog_error(exc):
            raise
        imported = await _get_active_ebay_listings_via_trading_api(account, limit=requested_limit)
        account.last_successful_check_at = datetime.now(UTC).replace(tzinfo=None)
        account.connection_status = "connected"
        account.last_error = None
        db.add(account)
        db.commit()
        return imported
    account.last_successful_check_at = datetime.now(UTC).replace(tzinfo=None)
    account.connection_status = "connected"
    account.last_error = None
    db.add(account)
    db.commit()
    imported: list[dict[str, Any]] = []
    for offer in offers:
        sku = str(offer.get("sku") or "").strip()
        if not sku:
            continue
        offer_status = str(offer.get("listingStatus") or offer.get("status") or "").strip().upper()
        if offer_status and offer_status not in {"ACTIVE", "PUBLISHED", "LISTED"}:
            continue

        inventory_item = await client.request("GET", f"/sell/inventory/v1/inventory_item/{sku}")
        product = inventory_item.get("product") or {}
        availability = (inventory_item.get("availability") or {}).get("shipToLocationAvailability") or {}
        pricing_summary = offer.get("pricingSummary") or {}
        price_payload = pricing_summary.get("price") or {}
        listing_id = str(offer.get("listingId") or "").strip()
        source_url = f"https://www.ebay.com/itm/{listing_id}" if listing_id else ""
        aspects = product.get("aspects") or {}
        normalized_aspects = {
            str(key): values[0] if isinstance(values, list) and values else values
            for key, values in aspects.items()
            if str(key).strip()
        }
        image_urls = [
            str(url).strip()
            for url in (product.get("imageUrls") or [])
            if str(url).strip()
        ]
        imported.append(
            {
                "source_listing_reference": source_url or listing_id or sku,
                "source_url": source_url or None,
                "title": str(product.get("title") or "").strip(),
                "description": str(product.get("description") or "").strip(),
                "price": price_payload.get("value"),
                "listing_price": price_payload.get("value"),
                "quantity": availability.get("quantity") or offer.get("availableQuantity") or 1,
                "image_urls": image_urls,
                "item_specifics": normalized_aspects,
                "attributes": normalized_aspects,
                "category_id": offer.get("categoryId"),
                "condition": inventory_item.get("condition") or "",
                "tags": ["ebay", "imported"],
                "source_identifiers": {
                    "ebay_listing_id": listing_id or None,
                    "offer_id": str(offer.get("offerId") or "").strip() or None,
                    "sku": sku,
                },
                "raw_offer": offer,
                "raw_inventory_item": inventory_item,
            }
        )
    return imported


def _resolve_existing_ebay_offer_id(listing: Listing) -> str | None:
    marketplace_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
    offer = marketplace_data.get("offer") if isinstance(marketplace_data.get("offer"), dict) else {}
    for candidate in (
        offer.get("offerId"),
        offer.get("offer_id"),
        marketplace_data.get("offer_id"),
        marketplace_data.get("current_offer_id"),
    ):
        value = str(candidate or "").strip()
        if value:
            return value

    attempts = [attempt for attempt in (listing.publish_attempts or []) if isinstance(attempt, MarketplacePublishAttempt)]
    attempts = sorted(
        attempts,
        key=lambda attempt: (
            attempt.updated_at.isoformat() if attempt.updated_at else "",
            attempt.id or 0,
        ),
        reverse=True,
    )
    for attempt in attempts:
        if str(attempt.offer_id or "").strip():
            return str(attempt.offer_id).strip()
    return None


def _ebay_listing_revision_changes(local: Listing, remote: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    remote_title = str(remote.get("title") or "").strip()
    remote_description = str(remote.get("description") or "").strip()
    remote_price = remote.get("listing_price")
    remote_quantity = remote.get("quantity")
    remote_category = str(remote.get("category_id") or "").strip()
    remote_condition = str(remote.get("condition") or "").strip()
    remote_specifics = remote.get("item_specifics") if isinstance(remote.get("item_specifics"), dict) else {}
    if remote_title and remote_title != str(local.title or "").strip():
        changed.append("title")
    if remote_description and remote_description != str(local.description or "").strip():
        changed.append("description")
    if remote_price is not None and _coerce_price_number(remote_price) != _coerce_price_number(local.listing_price or local.suggested_price):
        changed.append("listing_price")
    if remote_quantity is not None and int(remote_quantity or 0) != int(local.quantity or 0):
        changed.append("quantity")
    if remote_category and remote_category != str(local.category_id or "").strip():
        changed.append("category_id")
    if remote_condition and remote_condition != str(local.condition or "").strip():
        changed.append("condition")
    if remote_specifics and remote_specifics != (local.item_specifics or {}):
        changed.append("item_specifics")
    return changed


async def sync_ebay_active_listings(
    user_id: int,
    db: Session,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    account = await get_or_refresh_account(user_id, db)
    active_listings = await get_active_ebay_listings(user_id, db, limit=limit)
    matched = 0
    created = 0
    updated = 0
    unmatched = 0
    changed_fields: dict[str, int] = {}
    results: list[dict[str, Any]] = []

    for remote in active_listings:
        source_identifiers = remote.get("source_identifiers") if isinstance(remote.get("source_identifiers"), dict) else {}
        ebay_listing_id = str(source_identifiers.get("ebay_listing_id") or "").strip()
        sku = str(source_identifiers.get("sku") or "").strip()
        offer_id = str(source_identifiers.get("offer_id") or "").strip()
        local = None
        if ebay_listing_id:
            local = db.execute(
                select(Listing).where(
                    Listing.user_id == user_id,
                    Listing.ebay_listing_id == ebay_listing_id,
                )
            ).scalars().first()
        if not local and ebay_listing_id:
            # Older imports can retain the remote ID only on the marketplace
            # projection.  Match that first before creating a history row.
            local = db.execute(
                select(Listing)
                .join(MarketplaceListing, MarketplaceListing.listing_id == Listing.id)
                .where(
                    Listing.user_id == user_id,
                    MarketplaceListing.marketplace == MarketplaceName.ebay,
                    MarketplaceListing.marketplace_listing_id == ebay_listing_id,
                )
                .order_by(MarketplaceListing.updated_at.desc(), Listing.id.desc())
            ).scalars().first()
        if not local:
            listing_id = _extract_listing_id_from_ebay_sku(sku, user_id=user_id)
            if listing_id:
                local = db.get(Listing, listing_id)
                if local and local.user_id != user_id:
                    local = None
        if not local:
            # Read-only eBay reconciliation is allowed to create a local
            # historical mirror of a remote active offer.  It never calls a
            # remote create/revise/end endpoint, and stable remote IDs/SKUs
            # make reruns idempotent.
            remote_images = [str(url).strip() for url in (remote.get("image_urls") or []) if str(url).strip()]
            local = Listing(
                user_id=user_id,
                status=ListingStatus.posted,
                source_type="ebay_history_reconciliation",
                title=str(remote.get("title") or "eBay listing").strip()[:255] or "eBay listing",
                description=str(remote.get("description") or "").strip() or None,
                category_id=str(remote.get("category_id") or "").strip() or None,
                item_specifics=remote.get("item_specifics") if isinstance(remote.get("item_specifics"), dict) else {},
                condition=str(remote.get("condition") or "").strip() or None,
                listing_price=_coerce_price_number(remote.get("listing_price")),
                suggested_price=_coerce_price_number(remote.get("listing_price")),
                quantity=max(0, int(remote.get("quantity") or 0)),
                image_urls=remote_images or None,
                listing_images=[
                    {
                        "storage_path": url,
                        "source_url": url,
                        "source_platform": "ebay",
                        "operator_state": "approved",
                        "is_reference": False,
                        "metadata": {"source": "ebay_history_reconciliation", "remote_listing_id": ebay_listing_id},
                    }
                    for url in remote_images
                ] or None,
                ebay_listing_id=ebay_listing_id or None,
                ebay_publish_status=EbayPublishStatus.POSTED,
                source_metadata={
                    "source": "ebay_history_reconciliation",
                    "source_identifiers": {"ebay_listing_id": ebay_listing_id or None, "offer_id": offer_id or None, "sku": sku or None},
                    "source_url": remote.get("source_url"),
                    "imported_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                },
                marketplace_data={"ebay_sync": {"source": "ebay_active_listings", "sku": sku or None, "offer_id": offer_id or None}},
            )
            db.add(local)
            db.flush()
            created += 1

        matched += 1
        revision_changes = _ebay_listing_revision_changes(local, remote)
        changed_fields_summary: dict[str, Any] = {}
        if revision_changes:
            changed_fields_summary["changed_fields"] = revision_changes
            if remote.get("title"):
                local.title = str(remote.get("title") or "").strip() or local.title
            if remote.get("description"):
                local.description = str(remote.get("description") or "").strip() or local.description
            if remote.get("listing_price") is not None:
                remote_price = _coerce_price_number(remote.get("listing_price"))
                if remote_price is not None:
                    local.listing_price = remote_price
                    if local.suggested_price is None:
                        local.suggested_price = remote_price
            if remote.get("quantity") is not None:
                try:
                    local.quantity = max(0, int(remote.get("quantity") or 0))
                except (TypeError, ValueError):
                    pass
            if remote.get("category_id"):
                local.category_id = str(remote.get("category_id") or "").strip() or local.category_id
            if remote.get("condition"):
                local.condition = str(remote.get("condition") or "").strip() or local.condition
            if isinstance(remote.get("item_specifics"), dict) and remote.get("item_specifics"):
                local.item_specifics = remote.get("item_specifics")
            updated += 1
            for field in revision_changes:
                changed_fields[field] = changed_fields.get(field, 0) + 1

        local.marketplace_data = {
            **(local.marketplace_data or {}),
            "ebay_sync": {
                "last_synced_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "source": "ebay_active_listings",
                "ebay_listing_id": ebay_listing_id or local.ebay_listing_id,
                "offer_id": offer_id or _resolve_existing_ebay_offer_id(local),
                "sku": sku or f"posterpro-{local.user_id}-{local.id}",
                "remote_title": remote.get("title"),
                "remote_price": remote.get("listing_price"),
                "remote_quantity": remote.get("quantity"),
                "remote_category_id": remote.get("category_id"),
                "remote_condition": remote.get("condition"),
                "remote_item_specifics": remote.get("item_specifics"),
                "remote_image_urls": remote.get("image_urls") or [],
                "remote_listing_start_time": remote.get("listing_start_time"),
                "remote_view_count": remote.get("view_count"),
                **changed_fields_summary,
            },
        }
        _sync_ebay_marketplace_listing(
            db,
            listing_id=local.id,
            status=MarketplaceListingStatus.PUBLISHED,
            response={
                "status": "SYNCED",
                "source": "ebay_active_listings",
                "ebay_listing_id": ebay_listing_id or local.ebay_listing_id,
                "offer_id": offer_id or _resolve_existing_ebay_offer_id(local),
                "sku": sku or f"posterpro-{local.user_id}-{local.id}",
                "remote": remote,
            },
        )
        db.add(local)
        results.append(
            {
                "listing_id": local.id,
                "ebay_listing_id": ebay_listing_id or local.ebay_listing_id,
                "changed_fields": revision_changes,
                "status": "updated" if revision_changes else "unchanged",
            }
        )

    db.commit()
    return {
        "user_id": user_id,
        "marketplace": "ebay",
        "checked": len(active_listings),
        "matched": matched,
        "created": created,
        "updated": updated,
        "unmatched": unmatched,
        "changed_fields": changed_fields,
        "results": results,
        "account_id": account.id,
    }


async def sync_ebay_fulfillment_history(
    user_id: int,
    db: Session,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    from app.services.sale_detection_service import SaleDetectionService

    account = await get_or_refresh_account(user_id, db)
    requested_limit = max(1, int(limit or 1))
    page_limit = min(requested_limit, 50)
    orders: list[dict[str, Any]] = []
    offset = 0
    while len(orders) < requested_limit:
        page = await get_fulfillment_orders(account, limit=min(page_limit, requested_limit - len(orders)), offset=offset)
        orders.extend(page)
        if len(page) < min(page_limit, requested_limit - len(orders) + len(page)):
            break
        offset += len(page)
        if len(page) == 0:
            break
    sale_detection = SaleDetectionService()
    matched = 0
    created = 0
    updated = 0
    unmatched = 0
    results: list[dict[str, Any]] = []

    for order in orders:
        if not isinstance(order, dict):
            continue
        line_items = order.get("lineItems") if isinstance(order.get("lineItems"), list) else []
        first_line = line_items[0] if line_items and isinstance(line_items[0], dict) else {}
        marketplace_order_id = str(order.get("orderId") or order.get("order_id") or "").strip() or None
        marketplace_listing_id = (
            str(first_line.get("legacyItemId") or first_line.get("itemId") or first_line.get("listingId") or order.get("legacyItemId") or "").strip()
            or None
        )
        title = str(first_line.get("title") or order.get("title") or "").strip() or None
        try:
            quantity = max(1, int(first_line.get("quantity") or order.get("quantity") or 1))
        except (TypeError, ValueError):
            quantity = 1
        amount = None
        pricing_summary = order.get("pricingSummary") if isinstance(order.get("pricingSummary"), dict) else {}
        total = pricing_summary.get("total") if isinstance(pricing_summary.get("total"), dict) else {}
        try:
            if total.get("value") is not None:
                amount = float(total.get("value"))
        except (TypeError, ValueError):
            amount = None
        sold_at = str(order.get("creationDate") or order.get("lastModifiedDate") or "").strip() or None
        event = {
            "marketplace": "ebay",
            "marketplace_order_id": marketplace_order_id,
            "marketplace_listing_id": marketplace_listing_id,
            "quantity": quantity,
            "amount": amount,
            "currency": str(total.get("currency") or order.get("currency") or "USD"),
            "sold_at": sold_at,
            "raw": {"order": order, "source": "ebay_fulfillment_history"},
            "title": title,
        }
        if sale_detection._already_processed(db, "ebay", marketplace_order_id, marketplace_listing_id):
            continue
        listing = sale_detection._find_listing(db, user_id, event)
        sale = sale_detection._record_sale(db, user_id, listing, event)
        created += 1
        if listing:
            matched += 1
            remaining = max(0, int(listing.quantity or 0) - quantity)
            listing.quantity = remaining
            listing.sold_at = sale.sold_at
            if remaining <= 0 and listing.status in {ListingStatus.ready, ListingStatus.draft, ListingStatus.posted}:
                listing.status = ListingStatus.posted
            listing.marketplace_data = {
                **(listing.marketplace_data or {}),
                "ebay_history_sync": {
                    "synced_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                    "source": "ebay_fulfillment_history",
                    "marketplace_order_id": marketplace_order_id,
                    "marketplace_listing_id": marketplace_listing_id,
                    "quantity": quantity,
                    "amount": amount,
                },
            }
            db.add(listing)
            updated += 1
        else:
            unmatched += 1
        db.add(sale)
        results.append(
            {
                "sale_id": sale.id,
                "listing_id": listing.id if listing else None,
                "marketplace_order_id": marketplace_order_id,
                "marketplace_listing_id": marketplace_listing_id,
            }
        )

    db.commit()
    return {
        "user_id": user_id,
        "marketplace": "ebay",
        "checked": len(orders),
        "matched": matched,
        "created": created,
        "updated": updated,
        "unmatched": unmatched,
        "results": results,
        "account_id": account.id,
    }


async def revise_ebay_listing(listing: Listing, db: Session) -> dict[str, Any]:
    account = await get_or_refresh_account(listing.user_id, db)
    plan = await build_ebay_publish_plan(listing, db, allow_create_policies=False)
    sku = _build_ebay_sku(listing.user_id, listing.id)
    offer_id = _resolve_existing_ebay_offer_id(listing)
    if not offer_id and listing.ebay_listing_id:
        offer_id = str((listing.marketplace_data or {}).get("offer", {}).get("offerId") or "").strip() or None

    item_data = await create_or_replace_item(
        listing,
        account,
        item_specifics=plan["payload_preview"]["item_specifics"],
        inventory_payload=plan["inventory_item_payload"],
    )

    client = EbayAPIClient(account.access_token)
    if offer_id:
        offer_payload = dict(plan["offer_payload"])
        offer_payload["offerId"] = offer_id
        response = await client.request("PUT", f"/sell/inventory/v1/offer/{offer_id}", payload=offer_payload)
        publish_data = {"offerId": offer_id, "response": response, "status": "UPDATED"}
    else:
        offer_data = await create_offer_for_item(
            listing,
            account,
            item_data["sku"],
            category_id=plan["category"]["category_id"],
            offer_payload=plan["offer_payload"],
        )
        publish_data = {"offerId": offer_data.get("offerId"), "response": offer_data.get("response"), "status": "CREATED"}

    previous_data = listing.marketplace_data or {}
    ebay_url = str(previous_data.get("ebay_url") or "").strip() or f"https://www.ebay.com/itm/{listing.ebay_listing_id or item_data['sku']}"
    listing.marketplace_data = {
        **previous_data,
        "ebay_revision": {
            "synced_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
            "source": "posterpro",
            "item": item_data,
            "offer": publish_data,
            "plan": {key: value for key, value in plan.items() if key != "account"},
        },
        "ebay_url": ebay_url,
    }
    listing.ebay_publish_status = EbayPublishStatus.POSTED
    _sync_ebay_marketplace_listing(
        db,
        listing_id=listing.id,
        status=MarketplaceListingStatus.UPDATED if listing.ebay_listing_id else MarketplaceListingStatus.PUBLISHED,
        response={
            "status": "UPDATED",
            "sku": item_data.get("sku"),
            "offer_id": publish_data.get("offerId"),
            "item": item_data,
            "offer": publish_data,
            "plan": {key: value for key, value in plan.items() if key != "account"},
        },
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return {
        "status": "UPDATED" if listing.ebay_listing_id else "PUBLISHED",
        "sku": item_data.get("sku"),
        "offer_id": publish_data.get("offerId"),
        "listing_id": listing.ebay_listing_id,
        "revision": listing.marketplace_data.get("ebay_revision"),
    }


async def accept_best_offer(account: MarketplaceAccount, offer_id: str) -> dict[str, Any]:
    client = EbayAPIClient(account.access_token)
    return await client.request("POST", f"/sell/negotiation/v1/offer/{offer_id}/accept")


async def reject_best_offer(account: MarketplaceAccount, offer_id: str, reason: str | None = None) -> dict[str, Any]:
    client = EbayAPIClient(account.access_token)
    payload = {"declineReason": reason} if reason else None
    return await client.request("POST", f"/sell/negotiation/v1/offer/{offer_id}/decline", payload=payload)


async def publish_listing_to_ebay(listing: Listing, db: Session, *, relist: bool = False) -> dict[str, Any]:
    listing.ebay_publish_status = EbayPublishStatus.POSTING
    db.add(listing)
    db.commit()

    attempt = _start_publish_attempt(
        db,
        listing=listing,
        marketplace="ebay",
        dry_run=False,
        preflight_status=str(listing.ebay_publish_status),
        payload_snapshot={"listing_id": listing.id, "title": listing.title, "description": listing.description},
    )
    db.commit()

    try:
        plan = await build_ebay_publish_plan(listing, db, allow_create_policies=True)
        _apply_ebay_plan_repairs_to_listing(listing, plan)
        db.add(listing)
        db.commit()
        account = await get_or_refresh_account(listing.user_id, db)
        location_data = await create_inventory_location(
            listing.user_id,
            db,
            location_key=plan.get("policy_settings", {}).get("merchant_location_key") or None,
        )
        item_data = await create_or_replace_item(
            listing,
            account,
            item_specifics=plan["payload_preview"]["item_specifics"],
            inventory_payload=plan["inventory_item_payload"],
        )
        offer_data = await create_offer_for_item(
            listing,
            account,
            item_data["sku"],
            category_id=plan["category"]["category_id"],
            offer_payload=plan["offer_payload"],
        )
        publish_data = await publish_offer(listing, account, offer_data["offerId"])

        previous_listing_id = listing.ebay_listing_id
        listing.ebay_listing_id = publish_data["listingId"]
        listing.ebay_publish_status = EbayPublishStatus.POSTED
        previous_data = listing.marketplace_data or {}
        history = list(previous_data.get("auto_relist_history") or [])
        if relist:
            history.append(
                {
                    "action": "AUTO_RELISTED",
                    "previous_listing_id": previous_listing_id,
                    "new_listing_id": publish_data["listingId"],
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        listing.marketplace_data = {
            **previous_data,
            "preflight_plan": {key: value for key, value in plan.items() if key != "account"},
            "location": location_data,
            "category": plan["category"],
            "item_specifics": plan["payload_preview"]["item_specifics"],
            "item": item_data,
            "offer": offer_data,
            "publish": publish_data,
            "ebay_url": f"https://www.ebay.com/itm/{publish_data['listingId']}",
            "last_publish_action": "relist" if relist else "publish",
            "auto_relist_history": history,
        }
        _sync_ebay_marketplace_listing(
            db,
            listing_id=listing.id,
            status=MarketplaceListingStatus.PUBLISHED,
            response={
                "listing_id": publish_data["listingId"],
                "status": str(listing.ebay_publish_status),
                "ebay_url": listing.marketplace_data["ebay_url"],
                "publish": publish_data,
            },
        )
        _finish_publish_attempt(
            db,
            attempt=attempt,
            status="published",
            response={
                "listing_id": publish_data["listingId"],
                "status": str(listing.ebay_publish_status),
                "ebay_url": listing.marketplace_data["ebay_url"],
                "publish": publish_data,
                "offer_id": offer_data.get("offerId"),
                "sku": item_data.get("sku"),
            },
        )
        db.add(listing)
        db.commit()
        db.refresh(listing)
        return {
            "listing_id": publish_data["listingId"],
            "status": listing.ebay_publish_status,
            "ebay_url": listing.marketplace_data["ebay_url"],
        }
    except Exception as exc:
        listing.ebay_publish_status = EbayPublishStatus.FAILED
        translated = translate_marketplace_error("ebay", exc)
        listing.marketplace_data = {
            "error": translated.get("user_message") or str(exc),
            "error_detail": translated,
            "raw_error": str(exc),
        }
        _sync_ebay_marketplace_listing(
            db,
            listing_id=listing.id,
            status=MarketplaceListingStatus.FAILED,
            response={"error": translated.get("user_message") or str(exc), "error_detail": translated, "status": str(listing.ebay_publish_status)},
        )
        _finish_publish_attempt(
            db,
            attempt=attempt,
            status="failed",
            response={"error": translated.get("user_message") or str(exc), "error_detail": translated, "status": str(listing.ebay_publish_status)},
            error=exc,
            retryable=bool(translated.get("retryable")),
        )
        db.add(listing)
        db.commit()
        raise
