import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import inspect
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.auth import router as auth_router
from app.api.ebay import router as ebay_router
from app.api.inventory import bulk_router as bulk_jobs_router
from app.api.inventory import router as inventory_router
from app.api.intelligence import router as intelligence_router
from app.api.intake import router as intake_router
from app.api.media import router as media_router
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


def _resolve_media_root() -> Path:
    configured = Path(settings.storage_root)
    candidates: list[Path] = []
    if configured.is_absolute():
        if configured.exists() and configured.is_dir():
            return configured
        candidates.append(configured)
    else:
        cwd_configured = (Path.cwd() / configured).resolve()
        if cwd_configured.exists() and cwd_configured.is_dir():
            return cwd_configured
        candidates.append(cwd_configured)
        candidates.append((Path(__file__).resolve().parents[3] / configured).resolve())
    candidates.append((Path(__file__).resolve().parents[3] / "storage").resolve())
    candidates.append((Path(__file__).resolve().parents[2] / "storage").resolve())
    candidates.append((Path(__file__).resolve().parents[1] / "storage").resolve())

    existing: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_dir():
            existing.append(candidate)
    if not existing:
        return candidates[0]

    def _score(path: Path) -> int:
        score = 0
        if (path / "amazon-vine").exists():
            score += 120
        if (path / "vine-search").exists():
            score += 100
        if (path / "vine-search-fallback").exists():
            score += 60
        if (path / "imports").exists():
            score += 10
        return score

    best = max(existing, key=_score)
    return best


def _repair_unsafe_vine_images_on_startup() -> dict[str, object]:
    environment = str(settings.environment or "").strip().lower()
    if environment not in {"production", "staging"}:
        return {"ran": False, "reason": "environment_not_production"}
    if not settings.amazon_vine_import_enabled:
        return {"ran": False, "reason": "vine_import_disabled"}
    if settings.autonomous_dry_run:
        return {"ran": False, "reason": "dry_run_mode_requires_manual_repair"}

    from app.services.listing_review import normalize_listing_images
    from app.services.vine_import_service import VineImportService, _is_unsafe_vine_image

    scanned = 0
    repair_map: dict[int, list[int]] = {}
    with SessionLocal() as db:
        rows = db.execute(
            text(
                "select id, user_id, listing_images, image_urls "
                "from listings where source_type = 'amazon_vine'"
            )
        ).all()
        for row in rows:
            scanned += 1
            normalized = normalize_listing_images(
                listing_images=row.listing_images,
                image_urls=row.image_urls,
                source_platform="amazon_vine",
                default_is_reference=True,
                approved=False,
            )
            if not normalized or any(_is_unsafe_vine_image(image) for image in normalized):
                repair_map.setdefault(int(row.user_id), []).append(int(row.id))

        service = VineImportService()
        repaired = 0
        processed_users = 0
        for user_id, listing_ids in repair_map.items():
            result = service.repair_vine_listing_images(
                db,
                user_id=user_id,
                listing_ids=listing_ids,
                include_archived=False,
                force_refresh=True,
                use_bridge_session=False,
                only_missing_images=False,
            )
            processed_users += 1
            repaired += int(result.get("updated") or 0)
    return {
        "ran": bool(repair_map),
        "scanned": scanned,
        "repaired": repaired,
        "users": processed_users,
        "unsafe_listing_count": sum(len(listing_ids) for listing_ids in repair_map.values()),
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
app.include_router(ebay_router)
app.include_router(marketplaces_router)
app.include_router(marketplace_jobs_router)
app.include_router(intelligence_router)
app.include_router(intake_router)
app.include_router(media_router)
app.include_router(inventory_router)
app.include_router(bulk_jobs_router)
app.include_router(sales_router)
app.include_router(vine_imports_router)
app.include_router(router)
app.mount("/media", StaticFiles(directory=_resolve_media_root()), name="media")


@app.on_event("startup")
def startup() -> None:
    try:
        bootstrap_summary = _bootstrap_database()
        vine_repair_summary = _repair_unsafe_vine_images_on_startup()
        app.state.database_ready = True
        app.state.database_error = None
        app.state.bootstrap_summary = {**bootstrap_summary, "vine_image_repair": vine_repair_summary}
    except SQLAlchemyError as exc:
        logger.exception("PosterPro database bootstrap failed")
        app.state.database_ready = False
        app.state.database_error = exc.__class__.__name__
        app.state.bootstrap_summary = {
            "startup_schema_compat_enabled": bool(settings.startup_schema_compat_enabled),
            "legacy_schema_columns_applied": [],
            "vine_image_repair": {"ran": False, "reason": "bootstrap_failed"},
        }


def _run_intake_monitor_for_user(user_id: int) -> None:
    """Run blocking provider I/O off the ASGI event loop with a scoped session."""
    from app.models.models import User
    from app.services.intake_slate import IntakeSlateService

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user:
            IntakeSlateService().monitor_google_album(db, user=user)
    finally:
        db.close()


async def _intake_monitor_loop() -> None:
    from app.models.models import User
    from app.services.intake_slate import IntakeSlateService

    service = IntakeSlateService()
    while True:
        await asyncio.sleep(60)
        try:
            db = SessionLocal()
            users = db.execute(select(User.id).order_by(User.id.asc())).scalars().all()
            now = datetime.now(UTC)
            due_user_ids: list[int] = []
            for user_id in users:
                user = db.get(User, user_id)
                if user is None:
                    continue
                settings_payload = service.settings_for_user(user)
                if not settings_payload.get("enabled"):
                    continue
                if not str(settings_payload.get("album_url") or settings_payload.get("folder_id") or "").strip():
                    continue
                last_synced = service._parse_datetime(settings_payload.get("last_synced_at"))  # noqa: SLF001
                poll_seconds = max(60, int(settings_payload.get("poll_interval_seconds") or 300))
                if last_synced and (now - last_synced.astimezone(UTC)).total_seconds() < poll_seconds:
                    continue
                due_user_ids.append(user.id)
            db.close()
            for user_id in due_user_ids:
                try:
                    await asyncio.to_thread(_run_intake_monitor_for_user, user_id)
                except Exception:
                    logger.exception("PosterPro intake monitor failed for user %s", user_id)
                    failure_db = SessionLocal()
                    try:
                        failed_user = failure_db.get(User, user_id)
                        if failed_user:
                            settings_payload = service.settings_for_user(failed_user)
                            service.save_settings(
                                db=failure_db,
                                user=failed_user,
                                payload={
                                    **settings_payload,
                                    "last_error": "Automatic intake monitor failed. Check backend logs.",
                                },
                            )
                    finally:
                        failure_db.close()
        except Exception:
            logger.exception("PosterPro intake monitor loop error")
        finally:
            with contextlib.suppress(UnboundLocalError):
                db.close()


@app.on_event("startup")
async def startup_intake_monitor() -> None:
    app.state.intake_monitor_task = asyncio.create_task(_intake_monitor_loop())


@app.on_event("shutdown")
async def shutdown_intake_monitor() -> None:
    task = getattr(app.state, "intake_monitor_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


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
        "vine_image_repair": bootstrap_summary.get("vine_image_repair", {}),
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
