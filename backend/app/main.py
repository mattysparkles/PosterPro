from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from sqlalchemy import text

from app.api.auth import router as auth_router
from app.api.ebay import router as ebay_router
from app.api.inventory import bulk_router as bulk_jobs_router
from app.api.inventory import router as inventory_router
from app.api.intelligence import router as intelligence_router
from app.api.marketplaces import router as marketplaces_router
from app.api.routes import router
from app.api.sales import router as sales_router
from app.core.config import settings
from app.core.database import Base, engine

Base.metadata.create_all(bind=engine)

with engine.begin() as connection:
    connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT"))
    connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE"))

app = FastAPI(title="PosterPro API")
app.include_router(auth_router)
app.include_router(router)
app.include_router(ebay_router)
app.include_router(marketplaces_router)
app.include_router(intelligence_router)
app.include_router(inventory_router)
app.include_router(bulk_jobs_router)
app.include_router(sales_router)
app.mount("/media", StaticFiles(directory=Path(settings.storage_root)), name="media")


@app.get("/health")
def health():
    return {"ok": True}
