from app.core.database import SessionLocal
from app.models.models import Listing, ListingCorrectionJob
from app.workers.tasks import process_listing_correction_jobs_task

IDS = [2177, 2167, 2170, 2174, 2186, 2178, 2179, 2185, 2187, 2191]
db = SessionLocal()
for listing_id in IDS:
    listing = db.get(Listing, listing_id)
    if listing:
        db.add(ListingCorrectionJob(user_id=listing.user_id, listing_id=listing_id, requested_by=listing.user_id, priority=0, fields=["description", "condition", "category"], operator_note="Rebuild from persisted Amazon/Vine facts and verify eBay readiness.", before_snapshot={"title": listing.title, "description": listing.description, "category_id": listing.category_id, "condition": listing.condition}))
db.commit()
print(process_listing_correction_jobs_task(limit=len(IDS)))
db.close()
