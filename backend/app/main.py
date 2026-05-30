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
from app.services.automation_bridge import AutomationBridgeError, get_bridge_connect_session
from app.services.bridge_desktop import BridgeDesktopTokenError, bridge_desktop_target, parse_bridge_desktop_token

logger = logging.getLogger(__name__)

# Keep startup-time schema mutation intentionally narrow.
# Most schema evolution is already represented in repo migrations and should not
# continue to drift through application startup side effects.
_LEGACY_STARTUP_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "users": [
        ("full_name", "full_name VARCHAR(255)"),
        ("settings_json", "settings_json JSON"),
    ],
    "vine_import_items": [
        ("brand", "brand VARCHAR(255)"),
        ("category", "category VARCHAR(255)"),
        ("source_status", "source_status VARCHAR(64)"),
        ("review_deadline", "review_deadline DATE"),
        ("item_url", "item_url TEXT"),
        ("manual_amazon_url", "manual_amazon_url TEXT"),
        ("amazon_match_status", "amazon_match_status VARCHAR(64)"),
        ("amazon_match_confidence", "amazon_match_confidence VARCHAR(32)"),
        ("amazon_match_asin", "amazon_match_asin VARCHAR(16)"),
        ("amazon_match_title", "amazon_match_title VARCHAR(512)"),
        ("amazon_source_page_url", "amazon_source_page_url TEXT"),
        ("image_import_status", "image_import_status VARCHAR(64)"),
        ("image_import_error", "image_import_error TEXT"),
    ],
}

def _add_column_if_missing(connection, table_name: str, column_name: str, ddl: str) -> bool:
    inspector = inspect(connection)
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name not in existing:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))
        return True
    return False


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


def _bootstrap_database() -> dict[str, object]:
    Base.metadata.create_all(bind=engine)
    applied_legacy_columns: list[str] = []
    with engine.begin() as connection:
        if settings.startup_schema_compat_enabled:
            for table_name, columns in _LEGACY_STARTUP_COLUMNS.items():
                for column_name, ddl in columns:
                    if _add_column_if_missing(connection, table_name, column_name, ddl):
                        applied_legacy_columns.append(f"{table_name}.{column_name}")
        elif _LEGACY_STARTUP_COLUMNS:
            logger.info("Startup schema compatibility shim is disabled.")
    if applied_legacy_columns:
        logger.warning(
            "Startup schema compatibility applied legacy columns at runtime: %s",
            ", ".join(applied_legacy_columns),
        )
    return {
        "startup_schema_compat_enabled": bool(settings.startup_schema_compat_enabled),
        "legacy_schema_columns_applied": applied_legacy_columns,
    }

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
        bootstrap_summary = _bootstrap_database()
        app.state.database_ready = True
        app.state.database_error = None
        app.state.bootstrap_summary = bootstrap_summary
    except SQLAlchemyError as exc:
        logger.exception("PosterPro database bootstrap failed")
        app.state.database_ready = False
        app.state.database_error = exc.__class__.__name__
        app.state.bootstrap_summary = {
            "startup_schema_compat_enabled": bool(settings.startup_schema_compat_enabled),
            "legacy_schema_columns_applied": [],
        }


@app.get("/health")
def health():
    database_ready = bool(getattr(app.state, "database_ready", False))
    bootstrap_summary = getattr(app.state, "bootstrap_summary", {}) or {}
    payload = {
        "ok": database_ready,
        "database_ready": database_ready,
        "database_error": getattr(app.state, "database_error", None),
        "startup_schema_compat_enabled": bool(bootstrap_summary.get("startup_schema_compat_enabled", settings.startup_schema_compat_enabled)),
        "legacy_schema_columns_applied": bootstrap_summary.get("legacy_schema_columns_applied", []),
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


def _bridge_desktop_session_is_active(connect_session_id: str) -> bool:
    try:
        session = get_bridge_connect_session(connect_session_id)
    except AutomationBridgeError:
        return False
    status_value = str(session.get("status") or "").strip().lower()
    return status_value in {"queued", "launching_browser", "opening_marketplace", "waiting_for_login", "validating_session"}


@app.websocket("/marketplace-jobs/bridge-desktop/ws")
async def bridge_desktop_websocket(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token") or ""
    try:
        claims = parse_bridge_desktop_token(token)
        current_user_id = _websocket_user_id(websocket)
        if int(claims["user_id"]) != current_user_id:
            raise PermissionError("Bridge desktop token does not match the current user")
        connect_session_id = str(claims["connect_session_id"])
        if not _bridge_desktop_session_is_active(connect_session_id):
            raise PermissionError("Bridge desktop session is no longer active")
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

    async def _watch_session_status() -> None:
        try:
            while True:
                await asyncio.sleep(2)
                still_active = await asyncio.to_thread(_bridge_desktop_session_is_active, connect_session_id)
                if still_active:
                    continue
                await websocket.close(code=1008, reason="Bridge desktop session is no longer active")
                break
        finally:
            writer.close()
            await writer.wait_closed()

    client_task = asyncio.create_task(_client_to_vnc())
    vnc_task = asyncio.create_task(_vnc_to_client())
    watch_task = asyncio.create_task(_watch_session_status())
    done, pending = await asyncio.wait({client_task, vnc_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        with contextlib.suppress(Exception):
            await task
