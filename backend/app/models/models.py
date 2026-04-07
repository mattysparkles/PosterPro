from datetime import datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import EbayPublishStatus, ListingStatus, MarketplaceListingStatus, MarketplaceName


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled_platforms: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    listings: Mapped[list["Listing"]] = relationship(back_populates="user")
    marketplace_accounts: Mapped[list["MarketplaceAccount"]] = relationship(back_populates="user")


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
    status: Mapped[ListingStatus] = mapped_column(Enum(ListingStatus), default=ListingStatus.draft)
    image_urls: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
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
    ebay_listing_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    ebay_publish_status: Mapped[EbayPublishStatus] = mapped_column(
        Enum(EbayPublishStatus), default=EbayPublishStatus.DRAFT, index=True
    )
    marketplace_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="listings")
    cluster: Mapped["Cluster | None"] = relationship(back_populates="listings")
    marketplace_listings: Mapped[list["MarketplaceListing"]] = relationship(back_populates="listing")


class MarketplaceAccount(Base, TimestampMixin):
    __tablename__ = "marketplace_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    marketplace: Mapped[MarketplaceName] = mapped_column(Enum(MarketplaceName), index=True)
    external_account_id: Mapped[str] = mapped_column(String(255))
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

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
