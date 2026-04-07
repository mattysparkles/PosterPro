from fastapi import FastAPI

from app.api.ebay import router as ebay_router
from app.api.marketplaces import router as marketplaces_router
from app.api.routes import router
from app.core.database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(title="PosterPro API")
app.include_router(router)
app.include_router(ebay_router)
app.include_router(marketplaces_router)


@app.get("/health")
def health():
    return {"ok": True}
