from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.models import Listing, ListingVariant, OptionPreset, User
from app.services.variant_service import ensure_system_presets, replace_listing_variants, serialize_listing_variants, serialize_preset, decrement_variant

router = APIRouter(tags=["variants"])

class PresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    axes: list[dict] = Field(default_factory=list)
    default_price: float | None = None
    price_modifiers: dict = Field(default_factory=dict)
    sku_pattern: str | None = Field(default=None, max_length=255)
    notes: str | None = None

class VariantStructureRequest(BaseModel):
    options: list[dict] = Field(default_factory=list)
    variants: list[dict] = Field(default_factory=list)

class VariantDecrementRequest(BaseModel):
    marketplace: str = Field(min_length=1, max_length=32)
    external_event_id: str = Field(min_length=1, max_length=255)
    quantity: int = Field(gt=0)
    details: dict = Field(default_factory=dict)

def _owned_listing(db: Session, user_id: int, listing_id: int) -> Listing:
    listing = db.execute(select(Listing).where(Listing.id == listing_id, Listing.user_id == user_id)).scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing

@router.get("/variants/presets")
def list_option_presets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_system_presets(db)
    rows = db.execute(select(OptionPreset).where((OptionPreset.scope == "system") | ((OptionPreset.scope == "tenant") & (OptionPreset.user_id == current_user.id))).order_by(OptionPreset.scope, OptionPreset.name)).scalars().all()
    return {"items": [serialize_preset(row) for row in rows]}

@router.post("/variants/presets", status_code=201)
def create_option_preset(payload: PresetRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = OptionPreset(scope="tenant", user_id=current_user.id, created_by=current_user.id, name=payload.name.strip(), axes=payload.axes, default_price=payload.default_price, price_modifiers=payload.price_modifiers, sku_pattern=payload.sku_pattern, notes=payload.notes)
    db.add(row)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A preset with that name already exists") from exc
    db.refresh(row)
    return serialize_preset(row)

@router.post("/variants/presets/{preset_id}/copy", status_code=201)
def copy_option_preset(preset_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    source = db.execute(select(OptionPreset).where(OptionPreset.id == preset_id, (OptionPreset.scope == "system") | (OptionPreset.user_id == current_user.id))).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Preset not found")
    row = OptionPreset(scope="tenant", user_id=current_user.id, created_by=current_user.id, name=f"{source.name} (Copy)", axes=source.axes, default_price=source.default_price, price_modifiers=source.price_modifiers, sku_pattern=source.sku_pattern, notes=source.notes)
    db.add(row); db.commit(); db.refresh(row)
    return serialize_preset(row)

@router.patch("/variants/presets/{preset_id}")
def update_option_preset(preset_id: int, payload: PresetRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.execute(select(OptionPreset).where(OptionPreset.id == preset_id, OptionPreset.scope == "tenant", OptionPreset.user_id == current_user.id)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant preset not found")
    row.name = payload.name.strip(); row.axes = payload.axes; row.default_price = payload.default_price; row.price_modifiers = payload.price_modifiers; row.sku_pattern = payload.sku_pattern; row.notes = payload.notes
    db.commit(); db.refresh(row)
    return serialize_preset(row)

@router.delete("/variants/presets/{preset_id}")
def delete_option_preset(preset_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.execute(select(OptionPreset).where(OptionPreset.id == preset_id, OptionPreset.scope == "tenant", OptionPreset.user_id == current_user.id)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant preset not found")
    db.delete(row); db.commit()
    return {"deleted": True, "id": preset_id}

@router.get("/listings/{listing_id}/variants")
def get_listing_variants(listing_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    listing = _owned_listing(db, current_user.id, listing_id)
    return {"listing_id": listing.id, **serialize_listing_variants(listing)}

@router.put("/listings/{listing_id}/variants")
def put_listing_variants(listing_id: int, payload: VariantStructureRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    listing = _owned_listing(db, current_user.id, listing_id)
    try:
        result = replace_listing_variants(db, listing, payload.model_dump())
        db.commit()
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"listing_id": listing.id, **result}

@router.post("/variants/{variant_id}/decrement")
def consume_listing_variant(variant_id: int, payload: VariantDecrementRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    variant = db.execute(select(ListingVariant).join(Listing).where(ListingVariant.id == variant_id, Listing.user_id == current_user.id)).scalar_one_or_none()
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")
    try:
        result = decrement_variant(db, variant_id=variant_id, marketplace=payload.marketplace, external_event_id=payload.external_event_id, quantity=payload.quantity, details=payload.details)
        db.commit()
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result
