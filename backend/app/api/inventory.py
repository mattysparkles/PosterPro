from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.schemas import InventoryBulkEditRequest, ListingResponse
from app.core.database import get_db
from app.models.models import Listing
from app.services.inventory_service import InventorySafetyError, InventoryService

router = APIRouter(prefix="/inventory", tags=["inventory"])
service = InventoryService()


@router.get("", response_model=list[ListingResponse])
def get_inventory(
    label: str | None = Query(default=None),
    quantity_gt_one: bool = Query(default=False),
    stale: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    stmt = service.build_inventory_query(label=label, multi_quantity_only=quantity_gt_one, stale=stale)
    listings = db.execute(stmt).scalars().all()
    return service.apply_label_filter(listings, label)


@router.post("/bulk-edit", response_model=list[ListingResponse])
def bulk_edit_inventory(payload: InventoryBulkEditRequest, db: Session = Depends(get_db)):
    if not payload.listing_ids:
        raise HTTPException(status_code=400, detail="listing_ids is required")

    listings = db.query(Listing).filter(Listing.id.in_(payload.listing_ids)).all()
    if not listings:
        raise HTTPException(status_code=404, detail="No listings found for given ids")

    try:
        return service.bulk_update(db, listings, payload.model_dump())
    except InventorySafetyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
