from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class GooglePhotosImportRequest(BaseModel):
    user_id: int = Field(default=1)
    album_url: HttpUrl


class ListingGenerateRequest(BaseModel):
    barcode: str | None = None


class ListingUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    suggested_price: float | None = None
    listing_price: float | None = None
    purchase_cost: float | None = None
    shipping_cost: float | None = None
    sale_price: float | None = None
    condition: str | None = None
    photo_quality_score: float | None = None
    quantity: int | None = None
    platform_quantities: dict | None = None
    custom_labels: list[str] | None = None
    last_refreshed: datetime | None = None


class ListingResponse(BaseModel):
    id: int
    cluster_id: int | None
    status: str
    image_urls: list[str] | None = None
    raw_photo_path: str | None = None
    storage_unit_name: str | None = None
    title: str | None
    description: str | None
    category_id: str | None = None
    category_suggestion: str | None = None
    item_specifics: dict | None = None
    tags: list[str] | None = None
    estimated_value: float | None = None
    start_price: float | None = None
    buy_it_now_price: float | None = None
    min_acceptable_offer: float | None = None
    suggested_price: float | None
    listing_price: float | None = None
    purchase_cost: float | None = None
    fees_estimated: float | None = None
    fees_actual: float | None = None
    shipping_cost: float | None = None
    sale_price: float | None = None
    profit: float | None = None
    roi_percentage: float | None = None
    ebay_listing_id: str | None = None
    ebay_publish_status: str | None = None
    marketplace_data: dict | None = None
    quantity: int = 1
    platform_quantities: dict | None = None
    custom_labels: list[str] | None = None
    last_refreshed: datetime | None = None

    class Config:
        from_attributes = True


class MarketplacePublishRequest(BaseModel):
    marketplaces: list[str] | None = None


class MarketplacePublishResult(BaseModel):
    marketplace: str
    task_id: str | None = None
    status: str


class ConnectMarketplaceResponse(BaseModel):
    marketplace: str
    auth: dict


class MarketplaceStatusEntry(BaseModel):
    marketplace: str
    status: str
    marketplace_listing_id: str | None = None
    raw_response: dict | None = None


class SoldSyncRequest(BaseModel):
    listing_ids: list[int] | None = None


class BatchStorageUnitUrlRequest(BaseModel):
    image_urls: list[HttpUrl] = Field(default_factory=list)
    user_id: int = 1
    storage_unit_name: str | None = None
    overnight_mode: bool = False


class StorageUnitBatchResponse(BaseModel):
    id: int
    user_id: int
    storage_unit_name: str | None = None
    status: str
    overnight_mode: bool
    total_items: int
    processed_items: int
    error_message: str | None = None
    pipeline_task_id: str | None = None

    class Config:
        from_attributes = True


class InventoryBulkEditRequest(BaseModel):
    listing_ids: list[int] = Field(default_factory=list)
    quantity: int | None = None
    platform_quantities: dict | None = None
    add_labels: list[str] | None = None
    remove_labels: list[str] | None = None
    delist: bool = False
    relist: bool = False


class InventoryFilterRequest(BaseModel):
    label: str | None = None
    stale: bool = False
    quantity_gt_one: bool = False
    search: str | None = None


class InventoryBulkRequest(BaseModel):
    action: str = Field(description="edit|delist|relist|label|refresh|autobump")
    listing_ids: list[int] = Field(default_factory=list)
    filters: InventoryFilterRequest | None = None
    payload: dict | None = None
    user_id: int = 1


class BulkJobResponse(BaseModel):
    job_id: str
    action: str
    status: str
    total_items: int
    processed_items: int
    error_count: int = 0
    errors: list[dict] = Field(default_factory=list)


class SaleDetectionConfigRequest(BaseModel):
    marketplaces: list[str] = Field(default_factory=list)


class SaleDetailsUpdateRequest(BaseModel):
    fees_actual: float | None = None
    shipping_cost: float | None = None
    notes: str | None = None

class PhotoEditRequest(BaseModel):
    brightness: float = 1.0
    contrast: float = 1.0
    filter_name: str = "none"
    crop_x: int | None = None
    crop_y: int | None = None
    crop_width: int | None = None
    crop_height: int | None = None


class PhotoEditResponse(BaseModel):
    image_url: str
    image_urls: list[str]
