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


class ListingResponse(BaseModel):
    id: int
    cluster_id: int
    status: str
    title: str | None
    description: str | None
    suggested_price: float | None

    class Config:
        from_attributes = True
