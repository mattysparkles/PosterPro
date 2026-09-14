"""Short-lived, tenant-scoped snapshots of confirmed external listing IDs."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, MarketplaceMetadataCache
from app.services.ebay_service import get_active_ebay_listings

logger = logging.getLogger(__name__)

SNAPSHOT_TTL = timedelta(minutes=10)


def _cache_key(user_id: int) -> str:
    return f"dashboard-active-listing-identities-v1:{int(user_id)}"


def _read_cache(db: Session, user_id: int) -> MarketplaceMetadataCache | None:
    return db.execute(
        select(MarketplaceMetadataCache)
        .where(
            MarketplaceMetadataCache.marketplace == MarketplaceName.ebay.value,
            MarketplaceMetadataCache.cache_key == _cache_key(user_id),
        )
        .order_by(MarketplaceMetadataCache.created_at.desc(), MarketplaceMetadataCache.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def cached_ebay_active_ids(db: Session, user_id: int) -> set[str] | None:
    """Return a fresh cache only; never make an external request from a list view."""
    cache = _read_cache(db, user_id)
    now = datetime.now(UTC).replace(tzinfo=None)
    if not cache or not cache.expires_at or cache.expires_at <= now:
        return None
    payload = cache.payload if isinstance(cache.payload, dict) else {}
    values = payload.get("listing_ids") if isinstance(payload.get("listing_ids"), list) else []
    return {str(value).strip() for value in values if str(value).strip()}


def refresh_ebay_active_snapshot(db: Session, user_id: int) -> dict:
    """Refresh the active eBay listing identity set, reusing a short tenant cache."""
    now = datetime.now(UTC).replace(tzinfo=None)
    cache = _read_cache(db, user_id)
    cached_payload = cache.payload if cache and isinstance(cache.payload, dict) else {}
    cached_ids = {
        str(value).strip()
        for value in (cached_payload.get("listing_ids") or [])
        if str(value).strip()
    }
    verified_at = cached_payload.get("verified_at")

    if cache and cache.expires_at and cache.expires_at > now:
        return {"status": "REMOTE_VERIFIED", "listing_ids": cached_ids, "verified_at": verified_at}

    account_exists = db.execute(
        select(MarketplaceAccount.id).where(
            MarketplaceAccount.user_id == user_id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        ).limit(1)
    ).scalar_one_or_none()
    if not account_exists:
        if verified_at:
            return {"status": "STALE_REMOTE_SNAPSHOT", "listing_ids": cached_ids, "verified_at": verified_at}
        return {"status": "NOT_CONNECTED", "listing_ids": None, "verified_at": None}

    try:
        # The caller may itself be in an event loop (e.g. ASGI test clients),
        # so run this legacy async service in a dedicated short-lived worker.
        def fetch_rows():
            from app.core.database import SessionLocal

            remote_db = SessionLocal()
            try:
                return asyncio.run(get_active_ebay_listings(user_id, remote_db, limit=1000))
            finally:
                remote_db.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            rows = executor.submit(fetch_rows).result()
        listing_ids = set()
        for row in rows:
            identifiers = row.get("source_identifiers") if isinstance(row, dict) else None
            external_id = identifiers.get("ebay_listing_id") if isinstance(identifiers, dict) else None
            if external_id:
                listing_ids.add(str(external_id).strip())
        verified_at = datetime.now(UTC).isoformat()
        payload = {
            "listing_ids": sorted(value for value in listing_ids if value),
            "verified_at": verified_at,
            "result_count": len(rows),
            "source": "ebay_active_listings_read",
        }
        if cache:
            cache.payload = payload
            cache.source_version = "dashboard-live-v1"
            cache.expires_at = now + SNAPSHOT_TTL
            db.add(cache)
        else:
            db.add(MarketplaceMetadataCache(
                marketplace=MarketplaceName.ebay.value,
                cache_key=_cache_key(user_id),
                payload=payload,
                source_version="dashboard-live-v1",
                expires_at=now + SNAPSHOT_TTL,
            ))
        db.commit()
        return {"status": "REMOTE_VERIFIED", "listing_ids": listing_ids, "verified_at": verified_at}
    except Exception as exc:  # Dashboard availability must survive an external API outage.
        db.rollback()
        logger.warning("Could not refresh tenant eBay live listing snapshot (user_id=%s, error=%s)", user_id, type(exc).__name__)
        if verified_at:
            return {"status": "STALE_REMOTE_SNAPSHOT", "listing_ids": cached_ids, "verified_at": verified_at}
        return {"status": "UNAVAILABLE", "listing_ids": None, "verified_at": None}
