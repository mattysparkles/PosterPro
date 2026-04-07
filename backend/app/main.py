from fastapi import FastAPI

from app.api.routes import router
from app.core.database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(title="PosterPro API")
app.include_router(router)


@app.get("/health")
def health():
    return {"ok": True}
