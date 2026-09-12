from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.registry import get_connector
from app.models.enums import MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceExtensionJob, MarketplaceListing


class MarketplaceExtensionJobError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


ACTIVE_JOB_STATES = (
    "QUEUED",
    "CLAIMED",
    "NAVIGATING",
    "FORM_FILLING",
    "AWAITING_OPERATOR_REVIEW",
    "SUBMITTING",
    "SUBMITTED",
    "RETRYABLE",
)


def queue_extension_marketplace_action(
    db: Session,
    *,
    user_id: int,
    listing: Listing,
    marketplace: str,
    action: str,
    priority: int = 1,
    crosspost_job: MarketplaceCrosspostJob | None = None,
) -> tuple[MarketplaceExtensionJob, bool]:
    """Create/reuse one durable assisted action without asserting marketplace success."""
    market = str(marketplace or "").strip().lower()
    action_value = str(action or "CREATE").strip().upper()
    if market not in MarketplaceName._value2member_map_:
        raise MarketplaceExtensionJobError("UNSUPPORTED_MARKETPLACE", f"Unsupported marketplace: {market or '(empty)'}")
    if action_value not in {"CREATE", "UPDATE", "END"}:
        raise MarketplaceExtensionJobError("UNSUPPORTED_ACTION", f"Unsupported extension action: {action_value}")

    connector = get_connector(market)
    capability = connector.get_capabilities()
    capability_name = f"supports_extension_{action_value.lower()}"
    if not capability.get(capability_name, False):
        raise MarketplaceExtensionJobError("UNSUPPORTED_ACTION", f"{action_value} is not supported for {market} through the extension")

    row = db.execute(
        select(MarketplaceListing)
        .where(
            MarketplaceListing.listing_id == listing.id,
            MarketplaceListing.marketplace == MarketplaceName(market),
        )
        .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
    ).scalars().first()
    external_id = str(row.marketplace_listing_id or "").strip() if row else ""
    raw = row.raw_response if row and isinstance(row.raw_response, dict) else {}
    external_url = str(raw.get("external_url") or raw.get("listing_url") or raw.get("url") or "").strip()

    if action_value == "CREATE" and external_id and row and row.status not in {
        MarketplaceListingStatus.DELETED,
        MarketplaceListingStatus.CLOSED,
    }:
        raise MarketplaceExtensionJobError(
            "EXTERNAL_IDENTITY_EXISTS",
            f"{market} already has external listing {external_id}; use UPDATE or END instead of CREATE",
        )
    if action_value == "CREATE":
        uncertain = db.execute(
            select(MarketplaceExtensionJob).where(
                MarketplaceExtensionJob.user_id == int(user_id),
                MarketplaceExtensionJob.listing_id == int(listing.id),
                MarketplaceExtensionJob.marketplace == market,
                MarketplaceExtensionJob.action == "CREATE",
                MarketplaceExtensionJob.error_code == "SUBMISSION_OUTCOME_UNKNOWN",
            ).order_by(MarketplaceExtensionJob.created_at.desc(), MarketplaceExtensionJob.id.desc())
        ).scalars().first()
        if uncertain:
            raise MarketplaceExtensionJobError(
                "OPERATOR_RECONCILIATION_REQUIRED",
                f"A prior {market} submission has an unknown outcome; verify the marketplace before creating another listing",
            )
    if action_value in {"UPDATE", "END"} and not external_id:
        raise MarketplaceExtensionJobError("EXTERNAL_IDENTITY_REQUIRED", f"Confirmed {market} external listing identity is required for {action_value}")
    if action_value in {"UPDATE", "END"} and not external_url:
        raise MarketplaceExtensionJobError("EXTERNAL_URL_REQUIRED", f"Confirmed {market} listing URL is required to target {action_value} safely")
    if action_value in {"UPDATE", "END"}:
        hostname = (urlsplit(external_url).hostname or "").lower()
        allowed_hosts = {
            "facebook": ("facebook.com",),
            "mercari": ("mercari.com",),
            "poshmark": ("poshmark.com",),
            "vinted": ("vinted.com",),
            "etsy": ("etsy.com",),
            "offerup": ("offerup.com",),
            "depop": ("depop.com",),
            "whatnot": ("whatnot.com",),
        }.get(market, ())
        if not hostname or not any(hostname == host or hostname.endswith(f".{host}") for host in allowed_hosts):
            raise MarketplaceExtensionJobError("EXTERNAL_URL_MARKETPLACE_MISMATCH", f"The stored URL is not a recognized {market} listing URL")

    existing_job = db.execute(
        select(MarketplaceExtensionJob)
        .where(
            MarketplaceExtensionJob.user_id == int(user_id),
            MarketplaceExtensionJob.listing_id == int(listing.id),
            MarketplaceExtensionJob.marketplace == market,
            MarketplaceExtensionJob.action == action_value,
            MarketplaceExtensionJob.status.in_(ACTIVE_JOB_STATES),
        )
        .order_by(MarketplaceExtensionJob.created_at.desc(), MarketplaceExtensionJob.id.desc())
    ).scalars().first()
    if existing_job:
        return existing_job, False

    payload = connector.prepare_listing(listing)
    if action_value == "CREATE":
        missing = connector.validate_listing(listing)
        if missing:
            raise MarketplaceExtensionJobError(
                "MARKETPLACE_FIELDS_INCOMPLETE",
                ", ".join(str(item.get("field") or "unknown") for item in missing),
            )
    payload["start_url"] = external_url if action_value in {"UPDATE", "END"} else _create_url(market)
    payload["external_listing_id"] = external_id or None

    now = datetime.now(UTC).replace(tzinfo=None)
    job = MarketplaceExtensionJob(
        user_id=int(user_id),
        listing_id=int(listing.id),
        crosspost_job_id=crosspost_job.id if crosspost_job else None,
        marketplace=market,
        action=action_value,
        status="QUEUED",
        priority=max(0, min(int(priority), 10)),
        payload_version=1,
        payload_snapshot={
            "version": 1,
            "marketplace_payload": payload,
            "external_listing_id": external_id or None,
            "external_url": external_url or None,
        },
        external_listing_id=external_id or None,
        external_url=external_url or None,
        last_state_at=now,
    )
    db.add(job)
    db.flush()
    if row is None:
        row = MarketplaceListing(
            listing_id=listing.id,
            marketplace=MarketplaceName(market),
            status=MarketplaceListingStatus.PENDING,
            raw_response={"extension_job_id": job.id, "action": action_value},
        )
        db.add(row)
    elif action_value == "CREATE":
        row.raw_response = {**raw, "extension_job_id": job.id, "action": action_value}
        row.status = MarketplaceListingStatus.PENDING
        db.add(row)
    db.flush()
    return job, True


def _create_url(marketplace: str) -> str:
    return {
        "facebook": "https://www.facebook.com/marketplace/create/item",
        "mercari": "https://www.mercari.com/sell/",
        "poshmark": "https://poshmark.com/create-listing",
        "vinted": "https://www.vinted.com/items/new",
        "etsy": "https://www.etsy.com/your/shops/me/listing-editor/create",
        "offerup": "https://offerup.com/post",
        "depop": "https://www.depop.com/products/create/",
        "whatnot": "https://www.whatnot.com/sell",
    }.get(marketplace, "")
