from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import BulkJobResponse, InventoryBulkEditRequest, InventoryBulkRequest, ListingResponse
from app.core.database import get_db
from app.models.models import BulkJob, Listing
from app.services.inventory_service import InventorySafetyError, InventoryService

router = APIRouter(prefix="/inventory", tags=["inventory"])
bulk_router = APIRouter(tags=["bulk-jobs"])
service = InventoryService()


@router.get("")
def get_inventory(
    label: str | None = Query(default=None),
    quantity_gt_one: bool = Query(default=False),
    stale: bool = Query(default=False),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = service.build_inventory_query(label=label, multi_quantity_only=quantity_gt_one, stale=stale, search=search)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    paginated = stmt.offset((page - 1) * page_size).limit(page_size)
    listings = db.execute(paginated).scalars().all()
    return {
        "items": service.apply_label_filter(listings, label),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


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


@router.post("/bulk", response_model=BulkJobResponse)
def bulk_inventory(payload: InventoryBulkRequest, db: Session = Depends(get_db)):
    listing_ids = service.resolve_listing_ids(
        db,
        listing_ids=payload.listing_ids,
        filters=payload.filters.model_dump() if payload.filters else None,
    )
    job = service.queue_bulk_job(
        db,
        user_id=payload.user_id,
        action=payload.action,
        listing_ids=listing_ids,
        payload=payload.payload or {},
        filters=payload.filters.model_dump() if payload.filters else {},
    )
    return BulkJobResponse(
        job_id=job.id,
        action=job.action,
        status=job.status,
        total_items=job.total_items,
        processed_items=job.processed_items,
        error_count=job.error_count,
        errors=job.errors or [],
    )


@bulk_router.get("/bulk-jobs/{job_id}", response_model=BulkJobResponse)
def get_bulk_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(BulkJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Bulk job not found")
    return BulkJobResponse(
        job_id=job.id,
        action=job.action,
        status=job.status,
        total_items=job.total_items,
        processed_items=job.processed_items,
        error_count=job.error_count,
        errors=job.errors or [],
    )
