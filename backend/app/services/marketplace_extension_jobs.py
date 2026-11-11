from __future__ import annotations

from datetime import UTC, datetime
import re
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.registry import get_connector
from app.models.enums import MARKETPLACE_DESTINATION_VALUES, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceCrosspostJob, MarketplaceExtensionJob, MarketplaceListing, User


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


def normalize_facebook_remote_identity(value: str | None) -> tuple[str, str | None]:
    raw = str(value or "").strip()
    match = re.search(r"facebook\.com/marketplace/item/(\d+)", raw, re.I)
    if match:
        return match.group(1), raw if raw.startswith(("http://", "https://")) else f"https://{raw}"
    return raw, None


def build_extension_marketplace_payload(db: Session, *, user: User, listing: Listing, marketplace: str, action: str, external_id: str | None = None, external_url: str | None = None) -> dict:
    """Build a fresh, destination-scoped payload for an authorized extension action."""
    market = str(marketplace or "").strip().lower()
    action_value = str(action or "CREATE").strip().upper()
    payload = get_connector(market).prepare_listing(listing)
    if market in {"facebook", "mercari"}:
        # Browser marketplaces need human-readable taxonomy, not a numeric
        # eBay category ID. Resolve only from the synchronized local taxonomy;
        # the destination adapter still has to match a live rendered choice.
        category_hint = payload.get("category_hint") or payload.get("category")
        if category_hint is not None and str(category_hint).strip().isdigit():
            from app.services.ebay_taxonomy import category_by_id

            category = category_by_id(db, str(category_hint).strip())
            if category:
                payload["source_category_id"] = str(category["category_id"])
                payload["source_category_path"] = str(category["full_path"] or "")
                payload["category_hint"] = str(category["full_path"] or category["category_name"] or category_hint)
    if market == "facebook":
        from app.services.marketplace_setup import load_manual_marketplace_settings
        from app.services.facebook_category_resolver import resolve_facebook_category, listing_is_explicitly_local_only

        facebook_settings = load_manual_marketplace_settings(user).get("facebook", {})
        configured_location = facebook_settings.get("default_location", "")
        if configured_location:
            payload["location"] = configured_location
        listing_data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        shipping = listing_data.get("shipping") if isinstance(listing_data.get("shipping"), dict) else {}
        channel = (listing_data.get("channels") or {}).get("facebook") if isinstance(listing_data.get("channels"), dict) else {}
        channel = channel if isinstance(channel, dict) else {}
        # Negotiation is opt-in. A configured asking price must never be
        # reused as a minimum offer floor; only an explicit destination
        # preference may turn offers on or supply a separate floor.
        offers_enabled = channel.get("offers_enabled") is True or channel.get("accept_offers") is True
        payload["facebook_offers_enabled"] = offers_enabled
        if offers_enabled:
            minimum_offer_price = channel.get("minimum_offer_price")
            if minimum_offer_price is not None:
                payload["minimum_offer_price"] = minimum_offer_price
        else:
            payload.pop("minimum_offer_price", None)
        profile = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
        shipping_mode = str(shipping.get("mode") or "").strip().lower()
        use_shipping = bool(shipping.get("domestic_service")) or shipping_mode in {"included", "free", "shipping"}
        decision = resolve_facebook_category(
            listing,
            prefer_shipping_capable=bool(facebook_settings.get("prefer_shipping_capable_categories", True)),
            allow_local_pickup_only=bool(facebook_settings.get("allow_local_pickup_only_categories", False)),
        )
        payload.update({
            "category_hint": decision.category,
            "facebook_category": decision.category,
            "category_source": decision.source,
            "category_confidence": decision.confidence,
            "category_supports_shipping": decision.supports_shipping,
        })
        if use_shipping and not decision.supports_shipping and not listing_is_explicitly_local_only(listing):
            raise MarketplaceExtensionJobError("FACEBOOK_NO_SHIPPING_COMPATIBLE_CATEGORY", "No evidence-backed Facebook category supports shipping for this listing.")
        if use_shipping:
            payload["delivery_method"] = "shipping"
        elif shipping.get("local_pickup_enabled"):
            payload["delivery_method"] = "local pickup"
        else:
            payload["delivery_method"] = "manual"
        payload["shipping"] = {
            "mode": shipping_mode or None,
            "domestic_service": shipping.get("domestic_service"),
            "local_pickup_enabled": bool(shipping.get("local_pickup_enabled")),
            "free_shipping": bool(shipping.get("free_shipping")),
            "buyer_pays_shipping": bool(shipping.get("buyer_pays_shipping")),
            "handling_time_days": shipping.get("handling_time_days"),
            "channel_shipping_available": channel.get("shipping_available"),
            "package_weight": profile.get("package_weight"),
            "package_dimensions": profile.get("package_dimensions"),
            "estimated": bool(profile.get("estimated")),
            "estimated_fields": profile.get("estimated_fields") if isinstance(profile.get("estimated_fields"), list) else [],
            "shipping_notes": profile.get("shipping_notes"),
        }
    elif market == "mercari":
        shipping = payload.get("shipping") if isinstance(payload.get("shipping"), dict) else {}
        payload["delivery_method"] = "local pickup" if shipping.get("local_pickup_enabled") else "shipping"
    payload["start_url"] = external_url if action_value in {"UPDATE", "END"} else _create_url(market)
    payload["external_listing_id"] = external_id or None
    return payload


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
    if market not in MARKETPLACE_DESTINATION_VALUES:
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
    if market == "facebook" and external_id:
        external_id, legacy_url = normalize_facebook_remote_identity(external_id)
        external_url = external_url or legacy_url or ""
    prior_external_identity = None
    if action_value == "CREATE" and row and row.status in {MarketplaceListingStatus.DELETED, MarketplaceListingStatus.CLOSED}:
        prior_external_identity = {"listing_id": external_id or None, "url": external_url or None, "status": row.status.value}
        external_id = ""
        external_url = ""

    if action_value == "CREATE" and row and row.status not in {
        MarketplaceListingStatus.DELETED,
        MarketplaceListingStatus.CLOSED,
    } and not (row.status == MarketplaceListingStatus.PENDING and raw.get("extension_job_id")):
        raise MarketplaceExtensionJobError(
            "EXTERNAL_IDENTITY_EXISTS" if external_id else "REMOTE_STATE_RECONCILIATION_REQUIRED",
            f"{market} already has a placement or unresolved remote state; reconcile it before creating another listing",
        )
    if action_value == "CREATE":
        prior_creates = db.execute(
            select(MarketplaceExtensionJob).where(
                MarketplaceExtensionJob.user_id == int(user_id),
                MarketplaceExtensionJob.listing_id == int(listing.id),
                MarketplaceExtensionJob.marketplace == market,
                MarketplaceExtensionJob.action == "CREATE",
            ).order_by(MarketplaceExtensionJob.created_at.desc(), MarketplaceExtensionJob.id.desc())
        ).scalars().all()
        uncertain = next((prior for prior in prior_creates if (
            prior.error_code == "SUBMISSION_OUTCOME_UNKNOWN"
            or (
                market == "facebook" and isinstance(prior.result, dict)
                and prior.result.get("submission_performed") is True
            )
        )), None)
        if uncertain:
            raise MarketplaceExtensionJobError(
                "OPERATOR_RECONCILIATION_REQUIRED",
                f"A prior {market} submission may exist or is pending review; reconcile it before creating another listing",
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

    from app.models.models import User

    user = db.get(User, int(user_id))
    if user is None:
        raise MarketplaceExtensionJobError("USER_NOT_FOUND", "Marketplace action owner could not be loaded")
    payload = build_extension_marketplace_payload(
        db, user=user, listing=listing, marketplace=market, action=action_value,
        external_id=external_id or None, external_url=external_url or None,
    )
    if action_value == "CREATE":
        missing = connector.validate_listing(listing)
        if missing:
            raise MarketplaceExtensionJobError(
                "MARKETPLACE_FIELDS_INCOMPLETE",
                ", ".join(str(item.get("field") or "unknown") for item in missing),
            )
    now = datetime.now(UTC).replace(tzinfo=None)
    approved_plan = crosspost_job.execution_plan if crosspost_job and isinstance(crosspost_job.execution_plan, dict) else {}
    approved_payloads = approved_plan.get("approved_execution_payload_by_marketplace") if isinstance(approved_plan, dict) else {}
    approved_hashes = approved_plan.get("preflight_payload_hash_by_marketplace") if isinstance(approved_plan, dict) else {}
    preflight_by_marketplace = approved_plan.get("preflight_by_marketplace") if isinstance(approved_plan, dict) else {}
    facebook_preflight = preflight_by_marketplace.get("facebook") if isinstance(preflight_by_marketplace, dict) else {}
    if market == "facebook":
        from app.services.marketplace_field_mapper import build_marketplace_payload
        current_approved_base_payload = build_marketplace_payload(listing, "facebook")
    else:
        current_approved_base_payload = None
    from app.services.marketplace_preflight import compute_payload_hash
    requires_facebook_remote_precheck = bool(
        market == "facebook" and action_value == "CREATE" and crosspost_job
        and crosspost_job.user_id == int(user_id)
        and crosspost_job.requested_by == int(user_id)
        and str(crosspost_job.status).lower() == "running"
        and "facebook" in (crosspost_job.target_marketplaces or [])
        and isinstance(approved_payloads, dict)
        and approved_payloads.get("facebook") == current_approved_base_payload
        and isinstance(approved_hashes, dict)
        and approved_hashes.get("facebook") == compute_payload_hash(current_approved_base_payload)
        and isinstance(facebook_preflight, dict)
        and str(facebook_preflight.get("status") or "").lower() in {"ready", "ready_with_warnings"}
        and not facebook_preflight.get("blockers")
    )
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
            # A Facebook CREATE must perform an authenticated exact-title
            # seller-inventory lookup before the first submit. The paired
            # extension already has a read-only reconciliation path; this
            # marker keeps the ordinary CREATE claim behind that check.
            "remote_precheck_required": requires_facebook_remote_precheck,
            # Prevent legacy queued Mercari rows from becoming live work when
            # browser-assisted support is enabled; only a fresh authenticated
            # operator action carries this authorization marker.
            "operator_authorized": bool(crosspost_job and crosspost_job.requested_by == int(user_id)),
            "operator_authorized_at": now.isoformat() if crosspost_job and crosspost_job.requested_by == int(user_id) else None,
            "marketplace_payload": payload,
            "approved_payload_hash": __import__("app.services.marketplace_preflight", fromlist=["compute_payload_hash"]).compute_payload_hash(payload),
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
        row.raw_response = {
            **raw,
            **({"prior_external_identity": prior_external_identity} if prior_external_identity else {}),
            "extension_job_id": job.id,
            "action": action_value,
        }
        row.marketplace_listing_id = None
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
