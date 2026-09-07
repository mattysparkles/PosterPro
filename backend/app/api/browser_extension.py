from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.schemas import BrowserExtensionSessionImportRequest, MarketplaceConnectionStatusResponse
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import User
from app.services.marketplace_setup import (
    MARKETPLACE_SETUP_PROFILES,
    marketplace_status_snapshot,
    save_manual_marketplace_settings,
)
from app.services.automation_bridge import AutomationBridgeError, update_bridge_account_session, upsert_bridge_account
from app.services.multi_platform_publisher import get_enabled_platforms

router = APIRouter()


@router.get("/browser-extension/download")
def download_browser_extension():
    """Build a deterministic Chromium ZIP without exposing repository paths."""
    root = Path(__file__).resolve().parents[3] / "browser-extension"
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Extension artifact is unavailable")
    payload = BytesIO()
    with ZipFile(payload, "w", ZIP_DEFLATED) as archive:
        excluded = {".git", "__pycache__", ".pytest_cache", "node_modules"}
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(part in excluded for part in path.parts):
                continue
            if path.suffix.lower() not in {".js", ".json", ".html", ".css", ".md", ".png", ".svg", ".ico", ".webp"}:
                continue
            archive.writestr(f"posterpro-extension/{path.relative_to(root).as_posix()}", path.read_bytes())
    payload.seek(0)
    return StreamingResponse(payload, media_type="application/zip", headers={"Content-Disposition": "attachment; filename=posterpro-marketplace-extension.zip"})

SUPPORTED_EXTENSION_MARKETPLACES = {
    MarketplaceName.facebook.value,
    MarketplaceName.mercari.value,
    MarketplaceName.poshmark.value,
    MarketplaceName.etsy.value,
    MarketplaceName.depop.value,
    MarketplaceName.whatnot.value,
    MarketplaceName.vinted.value,
}


@router.post("/browser-extension/sessions/import", response_model=MarketplaceConnectionStatusResponse)
def import_browser_extension_session(
    payload: BrowserExtensionSessionImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    marketplace = str(payload.marketplace or "").strip().lower()
    if marketplace not in SUPPORTED_EXTENSION_MARKETPLACES:
        raise HTTPException(status_code=400, detail=f"Browser-extension session import is not available for marketplace '{marketplace}'")

    account_key = str(payload.account_key or "").strip().lower()
    if not account_key:
        raise HTTPException(status_code=400, detail="A bridge account key is required")

    profile = MARKETPLACE_SETUP_PROFILES.get(marketplace, {})
    session_state = str(payload.bridge_session_state or "").strip().lower() or "draft"
    if session_state not in {"draft", "ready", "active", "valid", "expired", "invalid"}:
        session_state = "draft"
    workflow_state = str(payload.workflow_state or "").strip().lower() or ("ready" if session_state in {"ready", "active", "valid"} else "draft")
    if workflow_state not in {"draft", "ready"}:
        workflow_state = "draft"
    import_mode = str(payload.import_mode or "").strip().lower() or str(profile.get("default_import_mode") or "manual")
    publish_mode = str(payload.publish_mode or "").strip().lower() or str(profile.get("default_publish_mode") or "manual_review")
    shipping_scope = str(payload.shipping_scope or "").strip().lower() or str(profile.get("default_shipping_scope") or "local_only")
    renewal_mode = str(payload.renewal_mode or "").strip().lower() or "manual"

    bridge_session_payload = payload.session_payload if isinstance(payload.session_payload, dict) else {}
    display_name = str(payload.display_name or "").strip()
    login_handle = str(payload.login_handle or "").strip()
    notes = str(payload.notes or "").strip()

    try:
        upsert_bridge_account(
            marketplace=marketplace,
            account_key=account_key,
            payload={
                "display_name": display_name or MARKETPLACE_SETUP_PROFILES.get(marketplace, {}).get("status_label", marketplace),
                "login_handle": login_handle,
                "credential_secret": payload.credential_secret if payload.credential_secret is not None else f"{marketplace}-session-imported",
                "notes": notes,
                "provider_enabled": False,
                "browser_enabled": True,
                "session_state": session_state,
                "session_payload": bridge_session_payload,
            },
        )
        update_bridge_account_session(
            marketplace=marketplace,
            account_key=account_key,
            payload={
                "session_state": session_state,
                "session_payload": bridge_session_payload,
                "last_tested_at": datetime.now(UTC).isoformat(),
                "notes": notes,
            },
        )
    except AutomationBridgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    save_manual_marketplace_settings(
        current_user,
        marketplace,
        {
            "display_name": display_name,
            "account_handle": login_handle,
            "notes": notes,
            "workflow_state": workflow_state,
            "import_mode": import_mode,
            "publish_mode": publish_mode,
            "shipping_scope": shipping_scope,
            "renewal_mode": renewal_mode,
            "support_url": "",
            "bridge_account_key": account_key,
            "import_listing_limit": 10,
        },
    )
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    snapshot = marketplace_status_snapshot(marketplace=marketplace, account=None, user=current_user)
    snapshot["enabled_for_publishing"] = marketplace in set(get_enabled_platforms(current_user))
    snapshot["enabled_for_sale_detection"] = marketplace in set(current_user.sale_detection_platforms or [])
    return MarketplaceConnectionStatusResponse(**snapshot)
