from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.config import settings
from app.core.secrets import decrypt_secret_if_needed, encrypt_secret
from app.models.enums import EbayPublishStatus, ListingStatus, MarketplaceListingStatus, MarketplaceName


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EncryptedTokenText(TypeDecorator):
    """Transparent at-rest encryption with backward-compatible legacy reads."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_secret(value, secret_key=settings.session_secret) if value else value

    def process_result_value(self, value, dialect):
        return decrypt_secret_if_needed(value, secret_key=settings.session_secret) if value else value


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    role: Mapped[str] = mapped_column(String(32), default="public", index=True)
    settings_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enabled_platforms: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    sale_detection_platforms: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    listings: Mapped[list["Listing"]] = relationship(back_populates="user")
    marketplace_accounts: Mapped[list["MarketplaceAccount"]] = relationship(back_populates="user")
    sales: Mapped[list["Sale"]] = relationship(back_populates="user")


class Cluster(Base, TimestampMixin):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_centroid: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)

    images: Mapped[list["Image"]] = relationship(back_populates="cluster")
    listings: Mapped[list["Listing"]] = relationship(back_populates="cluster")


class Image(Base, TimestampMixin):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("clusters.id"), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    image_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    cluster: Mapped["Cluster | None"] = relationship(back_populates="images")


class Listing(Base, TimestampMixin):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("clusters.id"), index=True, nullable=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("storage_unit_batches.id"), index=True, nullable=True)
    status: Mapped[ListingStatus] = mapped_column(Enum(ListingStatus), default=ListingStatus.draft)
    image_urls: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    listing_images: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    raw_photo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_unit_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_suggestion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    item_specifics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    estimated_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_it_now_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_acceptable_offer: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    listing_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    purchase_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees_estimated: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    shipping_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    sale_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    photo_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    condition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    condition_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ebay_listing_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    ebay_publish_status: Mapped[EbayPublishStatus] = mapped_column(
        Enum(EbayPublishStatus), default=EbayPublishStatus.DRAFT, index=True
    )
    marketplace_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    platform_quantities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    custom_labels: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    last_refreshed: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    stale_flag: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    shipping_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    restricted_review_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    restricted_reasons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    detected_category_guess: Mapped[str | None] = mapped_column(String(255), nullable=True)
    marketplace_allowed_status: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped["User"] = relationship(back_populates="listings")
    cluster: Mapped["Cluster | None"] = relationship(back_populates="listings")
    batch: Mapped["StorageUnitBatch | None"] = relationship(back_populates="listings")
    marketplace_listings: Mapped[list["MarketplaceListing"]] = relationship(back_populates="listing")
    publish_attempts: Mapped[list["MarketplacePublishAttempt"]] = relationship(back_populates="listing")
    sales: Mapped[list["Sale"]] = relationship(back_populates="listing")


class StorageUnitBatch(Base, TimestampMixin):
    __tablename__ = "storage_unit_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    storage_unit_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="INGESTED", index=True)
    overnight_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    processed_items: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    listings: Mapped[list["Listing"]] = relationship(back_populates="batch")


class IntakeSession(Base, TimestampMixin):
    __tablename__ = "intake_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_album_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    default_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    item_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)
    box_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="active", index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class IntakeSlate(Base, TimestampMixin):
    __tablename__ = "intake_slates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    intake_session_id: Mapped[int | None] = mapped_column(ForeignKey("intake_sessions.id"), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    item_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    box_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    condition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    flaws: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[str | None] = mapped_column(String(64), nullable=True)
    length: Mapped[str | None] = mapped_column(String(64), nullable=True)
    width: Mapped[str | None] = mapped_column(String(64), nullable=True)
    height: Mapped[str | None] = mapped_column(String(64), nullable=True)
    packed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    slate_image_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="draft", index=True)


class IntakePhotoBatch(Base, TimestampMixin):
    __tablename__ = "intake_photo_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    intake_session_id: Mapped[int | None] = mapped_column(ForeignKey("intake_sessions.id"), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    item_id: Mapped[str] = mapped_column(String(255), index=True)
    slate_id: Mapped[int | None] = mapped_column(ForeignKey("intake_slates.id"), nullable=True, index=True)
    first_photo_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    last_photo_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    photo_count: Mapped[int] = mapped_column(Integer, default=0)
    public_photo_count: Mapped[int] = mapped_column(Integer, default=0)
    internal_photo_count: Mapped[int] = mapped_column(Integer, default=0)
    draft_listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="collecting", index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class IntakePhoto(Base, TimestampMixin):
    __tablename__ = "intake_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_provider: Mapped[str] = mapped_column(String(64), index=True)
    source_photo_id: Mapped[str] = mapped_column(String(512), index=True)
    source_album_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    local_path: Mapped[str] = mapped_column(Text)
    downloaded_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    image_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_slate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_public_listing_candidate: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_internal_only: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    item_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("intake_photo_batches.id"), nullable=True, index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class IntakeSlateRecoveryCandidate(Base, TimestampMixin):
    """Append-only-in-effect recovery evidence; never an assignment instruction."""

    __tablename__ = "intake_slate_recovery_candidates"
    __table_args__ = (
        UniqueConstraint("user_id", "intake_photo_id", "pipeline_version", name="uq_intake_slate_recovery_candidate_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    intake_photo_id: Mapped[int] = mapped_column(ForeignKey("intake_photos.id"), index=True)
    pipeline_version: Mapped[str] = mapped_column(String(64), index=True)
    classification: Mapped[str] = mapped_column(String(32), index=True)
    raw_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    box_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    condition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    stored_qr_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    stored_ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    item_id_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_status: Mapped[str] = mapped_column(String(32), default="unresolved", index=True)
    matched_canonical_item_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_items.id"), nullable=True, index=True)
    matched_intake_slate_id: Mapped[int | None] = mapped_column(ForeignKey("intake_slates.id"), nullable=True, index=True)
    matched_batch_id: Mapped[int | None] = mapped_column(ForeignKey("intake_photo_batches.id"), nullable=True, index=True)
    review_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    accepted_rejected_state: Mapped[str] = mapped_column(String(32), default="unreviewed", index=True)


# Media recovery is deliberately isolated from intake assignment tables.  These
# rows retain enough provenance to recreate a draft without asserting that a
# recovered image belonged to a historical IntakePhoto or IntakePhotoBatch.
class MediaRecoveryRun(Base, TimestampMixin):
    __tablename__ = "media_recovery_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    run_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    pipeline_version: Mapped[str] = mapped_column(String(64), index=True)
    source_roots_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    processing_status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    imported_media_count: Mapped[int] = mapped_column(Integer, default=0)
    group_count: Mapped[int] = mapped_column(Integer, default=0)
    draft_count: Mapped[int] = mapped_column(Integer, default=0)
    # Recovery-only control.  This is intentionally not shared with the normal
    # listing pipeline, which must remain available while recovery is audited.
    draft_creation_state: Mapped[str] = mapped_column(String(48), default="frozen_for_quality_audit", index=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class MediaRecoveryMedia(Base, TimestampMixin):
    __tablename__ = "media_recovery_media"
    __table_args__ = (UniqueConstraint("run_id", "absolute_path", name="uq_media_recovery_media_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("media_recovery_runs.id"), index=True)
    absolute_path: Mapped[str] = mapped_column(Text)
    relative_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    slate_evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duplicate_of_media_id: Mapped[int | None] = mapped_column(ForeignKey("media_recovery_media.id"), nullable=True, index=True)
    processing_state: Mapped[str] = mapped_column(String(32), default="manifested", index=True)
    final_disposition: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    assigned_recovery_item_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)


class MediaRecoveryItemGroup(Base, TimestampMixin):
    __tablename__ = "media_recovery_item_groups"
    __table_args__ = (UniqueConstraint("run_id", "recovery_item_id", name="uq_media_recovery_group_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("media_recovery_runs.id"), index=True)
    parent_group_id: Mapped[int | None] = mapped_column(ForeignKey("media_recovery_item_groups.id"), nullable=True, index=True)
    recovery_item_id: Mapped[str] = mapped_column(String(255), index=True)
    grouping_status: Mapped[str] = mapped_column(String(32), index=True)
    grouping_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    media_paths_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    draft_listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)


class MediaRecoveryPhotoEvidence(Base, TimestampMixin):
    """Versioned, photo-local recovery evidence; never inferred from assignment."""
    __tablename__ = "media_recovery_photo_evidence"
    __table_args__ = (
        UniqueConstraint("recovery_group_id", "media_id", "pipeline_version", name="uq_media_recovery_photo_evidence_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("media_recovery_runs.id"), index=True)
    recovery_group_id: Mapped[int] = mapped_column(ForeignKey("media_recovery_item_groups.id"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media_recovery_media.id"), index=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    pipeline_version: Mapped[str] = mapped_column(String(96), index=True)
    photo_role: Mapped[str] = mapped_column(String(64), default="unclassified", index=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    barcode_attempts_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    decoded_barcode_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decoded_barcode_value: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    mpn: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    manufacturer_sku: Mapped[str | None] = mapped_column(String(255), nullable=True)
    upc: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ean: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    gtin: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    isbn: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    specifications_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    packaging_identity: Mapped[str | None] = mapped_column(Text, nullable=True)
    included_components_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    damage_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    condition_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    measurement_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    testing_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(64), default="deterministic", index=True)
    error_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


# Canonical intake records are intentionally separate from Listing. A listing is a
# marketplace representation; these records preserve the item, evidence, and
# reconciliation history that can safely survive a listing regeneration.
class CanonicalItem(Base, TimestampMixin):
    __tablename__ = "canonical_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    item_id: Mapped[str] = mapped_column(String(255), index=True)
    inventory_sku: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    current_listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    current_slate_id: Mapped[int | None] = mapped_column(ForeignKey("intake_slates.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="provisional", index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SlateObservation(Base, TimestampMixin):
    __tablename__ = "slate_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    canonical_item_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_items.id"), nullable=True, index=True)
    intake_slate_id: Mapped[int | None] = mapped_column(ForeignKey("intake_slates.id"), nullable=True, index=True)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("intake_photos.id"), nullable=True, index=True)
    item_id: Mapped[str] = mapped_column(String(255), index=True)
    observation_type: Mapped[str] = mapped_column(String(64), default="original", index=True)
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    capture_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    raw_qr_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_ocr_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parsed_values_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reconciliation_status: Mapped[str] = mapped_column(String(64), default="resolved", index=True)
    operator_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class CanonicalItemFact(Base, TimestampMixin):
    __tablename__ = "canonical_item_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_item_id: Mapped[int] = mapped_column(ForeignKey("canonical_items.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(128), index=True)
    value_json: Mapped[object | None] = mapped_column(JSON, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    source_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_status: Mapped[str] = mapped_column(String(32), default="inferred", index=True)
    precedence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    conflict_state: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class IntakeReconciliationEvent(Base, TimestampMixin):
    __tablename__ = "intake_reconciliation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), default="completed", index=True)
    source_media_id: Mapped[int | None] = mapped_column(ForeignKey("intake_photos.id"), nullable=True, index=True)
    canonical_item_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_items.id"), nullable=True, index=True)
    interval_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class IntakeNotification(Base, TimestampMixin):
    __tablename__ = "intake_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    canonical_item_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_items.id"), nullable=True, index=True)
    notification_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    href: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


# Intake jobs are deliberately independent of Celery task IDs. A broker can
# redeliver a task or a process can die mid-run; these rows remain the durable
# source of truth for ownership, idempotency, and operator-visible recovery.
class IntakeSourceState(Base, TimestampMixin):
    __tablename__ = "intake_source_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    source_key: Mapped[str] = mapped_column(String(512), index=True)
    provider_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    pagination_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_poll_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_poll_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_successful_poll_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_full_enumeration_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_complete_page: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lookback_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_integrity_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    poll_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    next_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    enumeration_generation: Mapped[int] = mapped_column(Integer, default=0)
    scan_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    enumerated_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_budget_count: Mapped[int] = mapped_column(Integer, default=0)
    oldest_capture_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    newest_capture_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enumeration_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    enumeration_interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    discovery_persisted_count: Mapped[int] = mapped_column(Integer, default=0)
    provider_visible_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processing_backlog_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    reconciliation_backlog_count: Mapped[int] = mapped_column(Integer, default=0)
    source_caught_up: Mapped[bool] = mapped_column(Boolean, default=False)
    poll_lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    poll_lease_acquired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    poll_lease_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    poll_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    poll_cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    enumeration_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    enumeration_progress_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class IntakeProviderMedia(Base, TimestampMixin):
    """Lightweight durable discovery record; full assets live in IntakePhoto after processing."""
    __tablename__ = "intake_provider_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_state_id: Mapped[int] = mapped_column(ForeignKey("intake_source_states.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    source_key: Mapped[str] = mapped_column(String(512), index=True)
    provider_media_id: Mapped[str] = mapped_column(String(512), index=True)
    provider_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    provider_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    discovery_generation: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    processing_status: Mapped[str] = mapped_column(String(64), default="discovered", index=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    processing_lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    processing_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    intake_photo_id: Mapped[int | None] = mapped_column(ForeignKey("intake_photos.id"), nullable=True, index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class IntakeReconciliationJob(Base, TimestampMixin):
    __tablename__ = "intake_reconciliation_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_state_id: Mapped[int | None] = mapped_column(ForeignKey("intake_source_states.id"), nullable=True, index=True)
    source_media_id: Mapped[int | None] = mapped_column(ForeignKey("intake_photos.id"), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(64), default="reconcile_media", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), index=True)
    interval_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class MarketplaceAccount(Base, TimestampMixin):
    __tablename__ = "marketplace_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    marketplace: Mapped[MarketplaceName] = mapped_column(Enum(MarketplaceName), index=True)
    external_account_id: Mapped[str] = mapped_column(String(255))
    access_token: Mapped[str] = mapped_column(EncryptedTokenText())
    refresh_token: Mapped[str | None] = mapped_column(EncryptedTokenText(), nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    connection_status: Mapped[str] = mapped_column(String(32), default="connected", index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_successful_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="marketplace_accounts")


class MarketplaceListing(Base, TimestampMixin):
    __tablename__ = "marketplace_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    marketplace: Mapped[MarketplaceName] = mapped_column(Enum(MarketplaceName), index=True)
    marketplace_listing_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    status: Mapped[MarketplaceListingStatus] = mapped_column(
        Enum(MarketplaceListingStatus), default=MarketplaceListingStatus.DRAFT, index=True
    )
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    listing: Mapped["Listing"] = relationship(back_populates="marketplace_listings")


class MarketplaceMetadataCache(Base, TimestampMixin):
    __tablename__ = "marketplace_metadata_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marketplace: Mapped[str] = mapped_column(String(64), index=True)
    cache_key: Mapped[str] = mapped_column(String(255), index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class MarketplaceImportJob(Base, TimestampMixin):
    __tablename__ = "marketplace_import_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_marketplace: Mapped[str] = mapped_column(String(64), index=True)
    source_listing_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    import_mode: Mapped[str] = mapped_column(String(64), default="manual", index=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    normalized_preview: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MarketplaceCrosspostJob(Base, TimestampMixin):
    __tablename__ = "marketplace_crosspost_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    source_marketplace: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target_marketplaces: Mapped[list[str]] = mapped_column(JSON, default=list)
    requested_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    execution_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MarketplacePublishAttempt(Base, TimestampMixin):
    __tablename__ = "marketplace_publish_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    marketplace: Mapped[MarketplaceName] = mapped_column(Enum(MarketplaceName), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    preflight_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    payload_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    inventory_item_sku: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    offer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    marketplace_listing_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    marketplace_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    translated_error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    previous_attempt_id: Mapped[int | None] = mapped_column(ForeignKey("marketplace_publish_attempts.id"), nullable=True, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("marketplace_crosspost_jobs.id"), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    listing: Mapped["Listing"] = relationship(back_populates="publish_attempts")
    user: Mapped["User"] = relationship()


class ListingTemplate(Base, TimestampMixin):
    __tablename__ = "listing_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    category_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    is_category_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fields: Mapped[dict] = mapped_column(JSON, default=dict)




class Sale(Base, TimestampMixin):
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), index=True, nullable=True)
    platform: Mapped[MarketplaceName] = mapped_column(Enum(MarketplaceName), index=True)
    marketplace_order_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    marketplace_listing_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    fees_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    shipping_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    promotional_fees: Mapped[float | None] = mapped_column(Float, nullable=True)
    marketplace_fees: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="DETECTED")
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="sales")
    listing: Mapped["Listing | None"] = relationship(back_populates="sales")


class DailyStat(Base, TimestampMixin):
    __tablename__ = "daily_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    stat_date: Mapped[datetime.date] = mapped_column(Date, index=True)
    total_revenue: Mapped[float] = mapped_column(Float, default=0.0)
    total_profit: Mapped[float] = mapped_column(Float, default=0.0)
    roi_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    sell_through_rate: Mapped[float] = mapped_column(Float, default=0.0)
    avg_days_to_sell: Mapped[float] = mapped_column(Float, default=0.0)


class EbayOfferHistory(Base, TimestampMixin):
    __tablename__ = "ebay_offer_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), index=True, nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ebay_offer_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    ebay_listing_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    buyer_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    offered_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    offer_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CategoryStat(Base, TimestampMixin):
    __tablename__ = "category_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(255), index=True)
    total_listed: Mapped[int] = mapped_column(Integer, default=0)
    total_sold: Mapped[int] = mapped_column(Integer, default=0)
    avg_days_to_sell: Mapped[float] = mapped_column(Float, default=0.0)
    avg_sale_price: Mapped[float] = mapped_column(Float, default=0.0)
    sell_through_rate: Mapped[float] = mapped_column(Float, default=0.0)


class ListingPrediction(Base, TimestampMixin):
    __tablename__ = "listing_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True, unique=True)
    probability_sale_7d: Mapped[float] = mapped_column(Float, default=0.0)
    probability_sale_30d: Mapped[float] = mapped_column(Float, default=0.0)
    model_version: Mapped[str] = mapped_column(String(64), default="heuristic-v1")
    features: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ListingABTestVariant(Base, TimestampMixin):
    __tablename__ = "listing_ab_test_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    variant_label: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    watch_count: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)


class BulkJob(Base, TimestampMixin):
    __tablename__ = "bulk_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    processed_items: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    filters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class OfferAutomationRule(Base, TimestampMixin):
    __tablename__ = "offer_automation_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class AutomatedOfferLog(Base, TimestampMixin):
    __tablename__ = "automated_offer_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(32), default="ebay", index=True)
    watcher_count: Mapped[int] = mapped_column(Integer, default=0)
    offer_percent: Mapped[float] = mapped_column(Float, default=0.0)
    offer_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="SENT")
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VineImportBatch(Base, TimestampMixin):
    __tablename__ = "vine_import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    report_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="uploaded", index=True)
    parsed_count: Mapped[int] = mapped_column(Integer, default=0)
    eligible_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_count: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    drafts_created_count: Mapped[int] = mapped_column(Integer, default=0)
    stats_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class VineImportItem(Base, TimestampMixin):
    __tablename__ = "vine_import_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("vine_import_batches.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_number: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    asin: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    product_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    order_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    shipped_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cancelled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_tax_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    eligible_after: Mapped[date | None] = mapped_column(Date, nullable=True)
    eligibility_status: Mapped[str] = mapped_column(String(64), default="invalid", index=True)
    raw_row_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parse_warnings_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    media_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_asset_ids_json: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    restricted_review_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    restricted_reasons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    detected_category_guess: Mapped[str | None] = mapped_column(String(255), nullable=True)
    marketplace_allowed_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inventory_item_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"), nullable=True)
    source_confidence: Mapped[str] = mapped_column(String(16), default="high")
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    item_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_amazon_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    amazon_match_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    amazon_match_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amazon_match_asin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    amazon_match_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    amazon_source_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_import_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_import_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProductMediaCache(Base, TimestampMixin):
    __tablename__ = "product_media_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asin: Mapped[str] = mapped_column(String(16), index=True)
    marketplace_region: Mapped[str] = mapped_column(String(8), default="US", index=True)
    product_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    gallery_image_urls_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    local_asset_ids_json: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    source_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetch_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    fetch_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
