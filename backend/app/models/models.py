from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import EbayPublishStatus, ListingStatus, MarketplaceName


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

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
    cluster_id: Mapped[int] = mapped_column(ForeignKey("clusters.id"), index=True)
    status: Mapped[ListingStatus] = mapped_column(Enum(ListingStatus), default=ListingStatus.draft)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_suggestion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    suggested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ebay_listing_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    ebay_publish_status: Mapped[EbayPublishStatus] = mapped_column(
        Enum(EbayPublishStatus), default=EbayPublishStatus.DRAFT, index=True
    )
    marketplace_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="listings")
    cluster: Mapped["Cluster"] = relationship(back_populates="listings")
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
    external_listing_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(100), default="posted")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    listing: Mapped["Listing"] = relationship(back_populates="marketplace_listings")
