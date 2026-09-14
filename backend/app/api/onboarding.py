from __future__ import annotations

from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.secrets import decrypt_secret_if_needed, encrypt_secret
from app.models.enums import MarketplaceName
from app.models.models import MarketplaceAccount, User
from app.services.ai_entitlements import ai_provider_config, public_ai_setup_state, sponsored_ai_entitlement
from app.services.ebay_service import EbayIntegrationError, _list_business_policies_for_account, get_or_refresh_account
from app.services.onboarding_service import (
    ALLOWED_DESTINATIONS,
    _root,
    _state,
    onboarding_snapshot,
    record_event,
    save_state,
    update_task_state,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


class MarketplaceSelectionRequest(BaseModel):
    marketplaces: list[str] = Field(default_factory=list, max_length=7)


class StepRequest(BaseModel):
    step_id: str = Field(min_length=1, max_length=64)


class AIKeyRequest(BaseModel):
    api_key: str = Field(min_length=16, max_length=512)


class AIModeRequest(BaseModel):
    mode: str


class OnboardingEventRequest(BaseModel):
    event: str = Field(min_length=1, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)


def _save_user(db: Session, user: User) -> None:
    db.add(user)
    db.commit()
    db.refresh(user)


def _task_exists(snapshot: dict, task_id: str) -> bool:
    return any(row.get("id") == task_id for row in snapshot.get("tasks", []))


@router.get("/state")
def get_onboarding_state(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return onboarding_snapshot(current_user, db)


@router.post("/start")
def start_onboarding(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    state = _state(current_user)
    state.setdefault("started_at", datetime.now(UTC).isoformat())
    state.pop("welcome_dismissed_at", None)
    state["current_step"] = state.get("current_step") or "choose_marketplaces"
    save_state(current_user, state)
    record_event(current_user, "ONBOARDING_STARTED")
    _save_user(db, current_user)
    return onboarding_snapshot(current_user, db)


@router.post("/skip-for-now")
def skip_onboarding_for_now(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    state = _state(current_user)
    state["welcome_dismissed_at"] = datetime.now(UTC).isoformat()
    state["current_step"] = "welcome"
    save_state(current_user, state)
    record_event(current_user, "ONBOARDING_SKIPPED")
    _save_user(db, current_user)
    return onboarding_snapshot(current_user, db)


@router.post("/restart")
def restart_onboarding(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    state = _state(current_user)
    state.update({"started_at": datetime.now(UTC).isoformat(), "current_step": "welcome", "skipped_tasks": []})
    state.pop("welcome_dismissed_at", None)
    save_state(current_user, state)
    record_event(current_user, "ONBOARDING_RESTARTED")
    _save_user(db, current_user)
    return onboarding_snapshot(current_user, db)


@router.put("/selection")
def select_marketplaces(payload: MarketplaceSelectionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    normalized = list(dict.fromkeys(str(item).strip().lower() for item in payload.marketplaces if str(item).strip()))
    invalid = sorted(set(normalized) - ALLOWED_DESTINATIONS)
    if invalid:
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_MARKETPLACE", "marketplaces": invalid})
    state = _state(current_user)
    state["selected_marketplaces"] = normalized
    state["marketplace_choice_saved"] = True
    state["current_step"] = "ai"
    save_state(current_user, state)
    record_event(current_user, "MARKETPLACES_SELECTED")
    _save_user(db, current_user)
    return onboarding_snapshot(current_user, db)


@router.post("/step")
def save_onboarding_step(payload: StepRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    snapshot = onboarding_snapshot(current_user, db)
    if payload.step_id != "welcome" and not _task_exists(snapshot, payload.step_id):
        raise HTTPException(status_code=404, detail="Setup step not found")
    state = _state(current_user)
    state["current_step"] = payload.step_id
    save_state(current_user, state)
    update_task_state(current_user, payload.step_id, status="IN_PROGRESS")
    _save_user(db, current_user)
    return onboarding_snapshot(current_user, db)


@router.post("/skip/{task_id}")
def skip_onboarding_task(task_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    snapshot = onboarding_snapshot(current_user, db)
    task = next((row for row in snapshot.get("tasks", []) if row.get("id") == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Setup step not found")
    state = _state(current_user)
    skipped = list(state.get("skipped_tasks") or [])
    if task_id not in skipped:
        skipped.append(task_id)
    state["skipped_tasks"] = skipped
    save_state(current_user, state)
    update_task_state(current_user, task_id, status="SKIPPED")
    record_event(current_user, "ONBOARDING_TASK_SKIPPED", task_id)
    _save_user(db, current_user)
    return onboarding_snapshot(current_user, db)


@router.post("/ai/mode")
def choose_ai_mode(payload: AIModeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    mode = str(payload.mode or "").strip().upper()
    if mode not in {"BYO_OPENAI", "POSTERPRO_SPONSORED", "DISABLED"}:
        raise HTTPException(status_code=422, detail="Choose your own OpenAI account, PosterPro AI, or skip AI for now.")
    root = _root(current_user)
    config = dict(ai_provider_config(current_user))
    if mode == "POSTERPRO_SPONSORED":
        entitlement = sponsored_ai_entitlement(current_user)
        if not entitlement["entitled"]:
            config["requested_mode"] = mode
            config["managed_interest_at"] = datetime.now(UTC).isoformat()
            root["ai_provider"] = config
            current_user.settings_json = root
            record_event(current_user, "SPONSORED_AI_UPGRADE_VIEWED", "ai")
            _save_user(db, current_user)
            return {"status": "COMING_SOON", "activated": False, "message": "Paid plan activation is not available on this deployment. Continue with your own OpenAI account or skip AI for now."}
        config["mode"] = mode
        config["requested_mode"] = None
    else:
        config["mode"] = mode
        config["requested_mode"] = None
    root["ai_provider"] = config
    current_user.settings_json = root
    record_event(current_user, "BYO_AI_SELECTED" if mode == "BYO_OPENAI" else "SPONSORED_AI_SELECTED" if mode == "POSTERPRO_SPONSORED" else "AI_SETUP_SKIPPED", "ai")
    _save_user(db, current_user)
    return public_ai_setup_state(current_user)


@router.post("/ai/test")
async def test_and_save_byo_ai(payload: AIKeyRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    api_key = payload.api_key.strip()
    if not api_key or any(ch.isspace() for ch in api_key):
        raise HTTPException(status_code=422, detail={"code": "INVALID_KEY_FORMAT", "message": "Paste the complete OpenAI key without spaces."})
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "input": "Reply with the single word OK.", "max_output_tokens": 12},
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=503, detail={"code": "AI_NETWORK_TIMEOUT", "message": "OpenAI did not respond in time. Check your connection and try again."}) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail={"code": "AI_NETWORK_ERROR", "message": "PosterPro could not reach OpenAI. Try again in a moment."}) from exc
    if response.status_code == 401:
        raise HTTPException(status_code=400, detail={"code": "OPENAI_KEY_INVALID", "message": "OpenAI did not accept that key. Create or copy a new key, then try again."})
    if response.status_code == 429:
        raise HTTPException(status_code=400, detail={"code": "OPENAI_QUOTA_UNAVAILABLE", "message": "OpenAI could not run the test. Check your OpenAI billing or usage limit, then try again."})
    if response.status_code == 404:
        raise HTTPException(status_code=400, detail={"code": "OPENAI_MODEL_UNAVAILABLE", "message": "This OpenAI account cannot use the selected test model. Contact support or use an eligible OpenAI account."})
    if response.is_error:
        raise HTTPException(status_code=400, detail={"code": "OPENAI_TEST_FAILED", "message": "OpenAI could not complete the connection test. Check the account and try again."})
    root = _root(current_user)
    config = dict(ai_provider_config(current_user))
    config.update({
        "mode": "BYO_OPENAI",
        "openai_api_key_enc": encrypt_secret(api_key, secret_key=settings.session_secret),
        "verified_at": datetime.now(UTC).isoformat(),
        "verified_model": "gpt-4o-mini",
        "last_verification": "success",
        "last_error_code": None,
    })
    config.pop("requested_mode", None)
    root["ai_provider"] = config
    current_user.settings_json = root
    record_event(current_user, "BYO_AI_COMPLETED", "ai")
    _save_user(db, current_user)
    return public_ai_setup_state(current_user)


@router.post("/verify/{task_id}")
async def verify_setup_task(task_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    snapshot = onboarding_snapshot(current_user, db)
    task = next((row for row in snapshot.get("tasks", []) if row.get("id") == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Setup step not found")

    verification: dict[str, str] = {"level": "SAVED_STATE_ONLY", "message": "PosterPro refreshed the saved status. This did not run an external service test."}
    if task_id == "ai":
        config = dict(ai_provider_config(current_user))
        mode = str(config.get("mode") or "").upper()
        if mode == "BYO_OPENAI":
            encoded = config.get("openai_api_key_enc")
            key = decrypt_secret_if_needed(encoded, secret_key=settings.session_secret) if isinstance(encoded, str) else None
            if not key:
                raise HTTPException(status_code=409, detail={"code": "OPENAI_KEY_MISSING", "message": "Your OpenAI key is not available. Reconnect it in Guided Setup."})
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/responses",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": "gpt-4o-mini", "input": "Reply with the single word OK.", "max_output_tokens": 12},
                    )
            except httpx.HTTPError as exc:
                config.update({"last_verification": "failed", "last_error_code": "AI_NETWORK_ERROR"})
                root = _root(current_user); root["ai_provider"] = config; current_user.settings_json = root
                _save_user(db, current_user)
                raise HTTPException(status_code=503, detail={"code": "AI_NETWORK_ERROR", "message": "PosterPro could not reach OpenAI. Check your internet connection and try again."}) from exc
            if response.is_error:
                config.update({"last_verification": "failed", "last_error_code": "OPENAI_TEST_FAILED"})
                root = _root(current_user); root["ai_provider"] = config; current_user.settings_json = root
                _save_user(db, current_user)
                friendly = "OpenAI no longer accepts this key. Reconnect it." if response.status_code == 401 else "OpenAI could not run the test. Check your usage or billing and try again." if response.status_code == 429 else "OpenAI could not complete the test. Try again or reconnect your key."
                raise HTTPException(status_code=400, detail={"code": "OPENAI_TEST_FAILED", "message": friendly})
            config.update({"verified_at": datetime.now(UTC).isoformat(), "last_verification": "success", "last_error_code": None})
            root = _root(current_user); root["ai_provider"] = config; current_user.settings_json = root
            verification = {"level": "LIVE_PROVIDER_TEST", "message": "OpenAI completed a small test request successfully."}
        elif mode == "POSTERPRO_SPONSORED" and sponsored_ai_entitlement(current_user)["entitled"]:
            verification = {"level": "ENTITLEMENT_ONLY", "message": "Your plan is eligible, but managed-AI test requests are not enabled by this deployment."}
        else:
            verification = {"level": "NOT_CONNECTED", "message": "Choose and connect an AI option first."}
        task_status = "COMPLETE" if verification["level"] == "LIVE_PROVIDER_TEST" else "NEEDS_ATTENTION" if verification["level"] == "NOT_CONNECTED" else "WAITING_FOR_USER"
        update_task_state(current_user, task_id, status=task_status, summary=verification["message"], verified=verification["level"] == "LIVE_PROVIDER_TEST")
        record_event(current_user, "ONBOARDING_TASK_VERIFIED", task_id)
        _save_user(db, current_user)
        state = onboarding_snapshot(current_user, db)
        return {"task": next(row for row in state["tasks"] if row["id"] == task_id), "verification": verification, "state": state}

    if task_id == "marketplace:ebay":
        account = db.query(MarketplaceAccount).filter(
            MarketplaceAccount.user_id == current_user.id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        ).one_or_none()
        if account:
            try:
                refreshed = await get_or_refresh_account(current_user.id, db)
                await _list_business_policies_for_account(current_user.id, db, refreshed, marketplace_id="EBAY_US")
                refreshed.last_successful_check_at = datetime.now(UTC).replace(tzinfo=None)
                refreshed.connection_status = "connected"
                refreshed.last_error = None
                db.add(refreshed); db.commit()
                prefs = current_user.settings_json.get("ebay_marketplace_policy_settings") if isinstance(current_user.settings_json, dict) else {}
                prefs = prefs if isinstance(prefs, dict) else {}
                publish_setup = (
                    all(str(prefs.get(key) or "").strip() for key in ("payment_policy_id", "fulfillment_policy_id", "return_policy_id", "merchant_location_key"))
                    and prefs.get("merchant_location_verified") is True
                )
                verification = {
                    "level": "LIVE_SELL_API_READ",
                    "publish_setup_complete": bool(publish_setup),
                    "message": "eBay accepted a read-only seller-policy check. No listing was created or changed. " + ("PosterPro also has the required policy and verified-location setup recorded." if publish_setup else "Finish seller policies and verify your shipping location in eBay Settings before treating publishing setup as ready."),
                }
            except EbayIntegrationError:
                db.rollback()
                verification = {"level": "FAILED", "message": "eBay did not accept the seller connection test. Reconnect eBay from Settings; no listing was changed."}
        else:
            verification = {"level": "NOT_CONNECTED", "message": "Connect eBay from Settings, then return here for the read-only seller-account test."}

    if task_id == "browser_extension":
        extension_online = task.get("status") == "ONLINE"
        verification = {"level": "LIVE_EXTENSION_HEARTBEAT" if extension_online else "NOT_ONLINE", "message": "PosterPro received a recent heartbeat from this browser. Marketplace login and form-fill are not verified by a heartbeat." if extension_online else "The browser extension is not online yet. Open PosterPro in the browser where it is installed, then refresh status."}
    elif task_id == "google_photos":
        verification = {"level": "SAVED_OAUTH_ONLY", "message": "Google authorization was found, but this deployment does not yet run a harmless upload test. Photo upload is not marked ready."}
    elif task_id.startswith("marketplace:") and task_id != "marketplace:ebay":
        verification = {"level": "OPERATOR_TEST_REQUIRED", "message": "This deployment has no safe, non-submitting form-fill check for this marketplace yet. Saved settings are not treated as working automation."}

    ebay_ready = verification["level"] == "LIVE_SELL_API_READ" and bool(verification.get("publish_setup_complete"))
    complete = verification["level"] == "LIVE_EXTENSION_HEARTBEAT" or ebay_ready
    update_task_state(current_user, task_id, status="COMPLETE" if complete else "NEEDS_ATTENTION" if verification["level"] in {"FAILED", "NOT_CONNECTED"} else "WAITING_FOR_USER", error_code="SELLER_API_CHECK_FAILED" if verification["level"] == "FAILED" else None, summary=verification["message"], verified=complete)

    # For browser/Google integrations this endpoint refreshes only PosterPro's
    # observed status. It must not imply a marketplace form or photo operation
    # was actually exercised.
    record_event(current_user, "ONBOARDING_TASK_VERIFIED", task_id)
    _save_user(db, current_user)
    refreshed_state = onboarding_snapshot(current_user, db)
    refreshed_task = next((row for row in refreshed_state["tasks"] if row.get("id") == task_id), task)
    return {"task": refreshed_task, "verification": verification, "state": refreshed_state}


@router.post("/event")
def track_onboarding_event(payload: OnboardingEventRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    allowed = {"AI_SETUP_VIEWED", "PLAN_VIEWED_FROM_AI_SETUP"}
    if payload.event not in allowed:
        raise HTTPException(status_code=422, detail="This onboarding event is not supported")
    if payload.event == "PLAN_VIEWED_FROM_AI_SETUP":
        config = ai_provider_config(current_user)
        if config.get("requested_mode") != "POSTERPRO_SPONSORED":
            raise HTTPException(status_code=409, detail="Open the PosterPro AI plan preview from Guided Setup first")
    record_event(current_user, payload.event, payload.task_id)
    _save_user(db, current_user)
    return {"recorded": True}
