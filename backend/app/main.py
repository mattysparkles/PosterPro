import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.auth import router as auth_router
from app.api.ebay import router as ebay_router
from app.api.inventory import bulk_router as bulk_jobs_router
from app.api.inventory import router as inventory_router
from app.api.intelligence import router as intelligence_router
from app.api.marketplaces import router as marketplaces_router
from app.api.marketplace_jobs import router as marketplace_jobs_router
from app.api.routes import router
from app.api.sales import router as sales_router
from app.api.vine_imports import router as vine_imports_router
from app.core.auth import SESSION_COOKIE_NAME, parse_session_token
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.models.models import User
from app.services.bridge_desktop import BridgeDesktopTokenError, bridge_desktop_target, parse_bridge_desktop_token

logger = logging.getLogger(__name__)

def _add_column_if_missing(connection, table_name: str, column_name: str, ddl: str) -> None:
    inspector = inspect(connection)
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in existing:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))


def _cors_origins() -> list[str]:
    configured = [
        origin.strip()
        for origin in (settings.cors_allowed_origins or "").split(",")
        if origin.strip()
    ]
    defaults = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3030",
        "http://127.0.0.1:3030",
        "https://posterpro.sparkleserver.site",
    ]
    origins: list[str] = []
    for origin in [*configured, *defaults]:
        if origin not in origins:
            origins.append(origin)
    return origins


def _bootstrap_database() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        _add_column_if_missing(connection, "users", "password_hash", "password_hash TEXT")
        _add_column_if_missing(connection, "users", "is_admin", "is_admin BOOLEAN NOT NULL DEFAULT FALSE")
        _add_column_if_missing(connection, "users", "role", "role VARCHAR(32) NOT NULL DEFAULT 'public'")
        _add_column_if_missing(connection, "users", "settings_json", "settings_json JSON")
        _add_column_if_missing(connection, "users", "enabled_platforms", "enabled_platforms JSON")
        _add_column_if_missing(connection, "users", "sale_detection_platforms", "sale_detection_platforms JSON")
        _add_column_if_missing(connection, "listings", "image_urls", "image_urls JSON")
        _add_column_if_missing(connection, "listings", "raw_photo_path", "raw_photo_path TEXT")
        _add_column_if_missing(connection, "listings", "storage_unit_name", "storage_unit_name VARCHAR(255)")
        _add_column_if_missing(connection, "listings", "category_id", "category_id VARCHAR(255)")
        _add_column_if_missing(connection, "listings", "item_specifics", "item_specifics JSON")
        _add_column_if_missing(connection, "listings", "estimated_value", "estimated_value DOUBLE PRECISION")
        _add_column_if_missing(connection, "listings", "listing_price", "listing_price DOUBLE PRECISION")
        _add_column_if_missing(connection, "listings", "purchase_cost", "purchase_cost DOUBLE PRECISION")
        _add_column_if_missing(connection, "listings", "fees_estimated", "fees_estimated DOUBLE PRECISION")
        _add_column_if_missing(connection, "listings", "fees_actual", "fees_actual DOUBLE PRECISION")
        _add_column_if_missing(connection, "listings", "shipping_cost", "shipping_cost DOUBLE PRECISION")
        _add_column_if_missing(connection, "listings", "sale_price", "sale_price DOUBLE PRECISION")
        _add_column_if_missing(connection, "listings", "profit", "profit DOUBLE PRECISION")
        _add_column_if_missing(connection, "listings", "roi_percentage", "roi_percentage DOUBLE PRECISION")
        _add_column_if_missing(connection, "listings", "sold_at", "sold_at TIMESTAMP WITHOUT TIME ZONE")
        _add_column_if_missing(connection, "listings", "photo_quality_score", "photo_quality_score DOUBLE PRECISION")
        _add_column_if_missing(connection, "listings", "condition", "condition VARCHAR(64)")
        _add_column_if_missing(connection, "listings", "quantity", "quantity INTEGER NOT NULL DEFAULT 1")
        _add_column_if_missing(connection, "listings", "platform_quantities", "platform_quantities JSON")
        _add_column_if_missing(connection, "listings", "custom_labels", "custom_labels JSON")
        _add_column_if_missing(connection, "listings", "last_refreshed", "last_refreshed TIMESTAMP WITHOUT TIME ZONE")
        _add_column_if_missing(connection, "listings", "stale_flag", "stale_flag BOOLEAN NOT NULL DEFAULT FALSE")
        _add_column_if_missing(connection, "listings", "source_type", "source_type VARCHAR(64)")
        _add_column_if_missing(connection, "listings", "source_metadata", "source_metadata JSON")
        _add_column_if_missing(connection, "listings", "needs_review", "needs_review BOOLEAN NOT NULL DEFAULT FALSE")
        _add_column_if_missing(connection, "listings", "restricted_review_required", "restricted_review_required BOOLEAN NOT NULL DEFAULT FALSE")
        _add_column_if_missing(connection, "listings", "restricted_reasons", "restricted_reasons JSON")
        _add_column_if_missing(connection, "listings", "detected_category_guess", "detected_category_guess VARCHAR(255)")
        _add_column_if_missing(connection, "listings", "marketplace_allowed_status", "marketplace_allowed_status VARCHAR(64)")

app = FastAPI(title="PosterPro API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(router)
app.include_router(ebay_router)
app.include_router(marketplaces_router)
app.include_router(marketplace_jobs_router)
app.include_router(intelligence_router)
app.include_router(inventory_router)
app.include_router(bulk_jobs_router)
app.include_router(sales_router)
app.include_router(vine_imports_router)
app.mount("/media", StaticFiles(directory=Path(settings.storage_root)), name="media")


@app.on_event("startup")
def startup() -> None:
    try:
        _bootstrap_database()
        app.state.database_ready = True
        app.state.database_error = None
    except SQLAlchemyError as exc:
        logger.exception("PosterPro database bootstrap failed")
        app.state.database_ready = False
        app.state.database_error = exc.__class__.__name__


@app.get("/health")
def health():
    database_ready = bool(getattr(app.state, "database_ready", False))
    payload = {
        "ok": database_ready,
        "database_ready": database_ready,
        "database_error": getattr(app.state, "database_error", None),
    }
    if database_ready:
        return payload
    return JSONResponse(status_code=503, content=payload)


def _websocket_user_id(websocket: WebSocket) -> int:
    session_token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not session_token:
        raise PermissionError("Not authenticated")
    user_id, _ = parse_session_token(session_token)
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            raise PermissionError("User not found")
        return int(user.id)
    finally:
        db.close()


@app.websocket("/marketplace-jobs/bridge-desktop/ws")
async def bridge_desktop_websocket(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token") or ""
    try:
        claims = parse_bridge_desktop_token(token)
        current_user_id = _websocket_user_id(websocket)
        if int(claims["user_id"]) != current_user_id:
            raise PermissionError("Bridge desktop token does not match the current user")
    except (BridgeDesktopTokenError, PermissionError, Exception):
        await websocket.close(code=1008)
        return

    host, port = bridge_desktop_target()
    try:
        reader, writer = await asyncio.open_connection(host=host, port=port)
    except Exception:
        await websocket.close(code=1011)
        return

    await websocket.accept()

    async def _client_to_vnc() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                data = message.get("bytes")
                text = message.get("text")
                if data is not None:
                    writer.write(data)
                    await writer.drain()
                elif text is not None:
                    writer.write(text.encode("utf-8"))
                    await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _vnc_to_client() -> None:
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                await websocket.send_bytes(chunk)
        finally:
            await websocket.close()

    client_task = asyncio.create_task(_client_to_vnc())
    vnc_task = asyncio.create_task(_vnc_to_client())
    done, pending = await asyncio.wait({client_task, vnc_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        with contextlib.suppress(Exception):
            await task
