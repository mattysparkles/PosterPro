from app.core.database import SessionLocal
from app.models.models import Listing, ListingCorrectionJob
from app.services.marketplace_preflight import MarketplacePreflightService
from sqlalchemy import select

ids = [2177, 2167, 2170, 2174, 2186, 2178, 2179, 2185, 2187, 2191]
db = SessionLocal(); svc = MarketplacePreflightService()
for listing_id in ids:
    listing = db.get(Listing, listing_id)
    job = db.execute(select(ListingCorrectionJob).where(ListingCorrectionJob.listing_id == listing_id).order_by(ListingCorrectionJob.created_at.desc()).limit(1)).scalar_one_or_none()
    if not listing:
        print(listing_id, "NOT_FOUND"); continue
    preflight = svc.preflight_listing(db, listing, "ebay")
    svc.cache_preflight_summary(db, listing, preflight)
    codes = [str(x.get("code")) for x in preflight.get("blockers", [])]
    print(listing_id, "job", job.id if job else None, job.status if job else None, "decision", (job.result or {}).get("decision") if job else None, "changed", (job.result or {}).get("fields_changed") if job else None, "category_state", (job.result or {}).get("category_state") if job else None, "preflight", preflight.get("status"), "blockers", codes, "condition", listing.condition, "category", listing.category_id)
db.commit(); db.close()
