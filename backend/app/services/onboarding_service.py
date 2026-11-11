"""User-scoped, resumable setup state and truthful connection checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import MarketplaceName
from app.models.models import Listing, MarketplaceAccount, MarketplaceExtensionDevice, MarketplaceExtensionJob, MarketplaceListing, User
from app.services.ai_entitlements import public_ai_setup_state
from app.services.ebay_service import summarize_ebay_account_health
from app.services.google_photos_oauth import GooglePhotosOAuthError, get_google_photos_oauth_state, google_photos_oauth_ready, refresh_google_photos_access_token
from app.services.marketplace_setup import load_manual_marketplace_settings


ONBOARDING_KEY = "guided_onboarding_v1"
DESTINATIONS = {
    "ebay": {"title": "eBay", "purpose": "Connect the account PosterPro will publish and sync through.", "deep_link": "/settings/ebay"},
    "facebook": {"title": "Facebook Marketplace", "purpose": "Use the Facebook session already open in your browser.", "deep_link": "/settings?tab=marketplaces&marketplace=facebook"},
    "mercari": {"title": "Mercari", "purpose": "Check the browser connection and Mercari listing workflow.", "deep_link": "/settings?tab=marketplaces&marketplace=mercari"},
    "poshmark": {"title": "Poshmark", "purpose": "Check the browser connection and Poshmark listing workflow.", "deep_link": "/settings?tab=marketplaces&marketplace=poshmark"},
    "vinted": {"title": "Vinted", "purpose": "Check the browser connection, region, and listing workflow.", "deep_link": "/settings?tab=marketplaces&marketplace=vinted"},
    "etsy": {"title": "Etsy", "purpose": "Connect the Etsy shop when supported authorization is available.", "deep_link": "/settings?tab=marketplaces&marketplace=etsy"},
    "offerup": {"title": "OfferUp", "purpose": "Check the browser connection and OfferUp listing workflow.", "deep_link": "/settings?tab=marketplaces&marketplace=offerup"},
}
MARKETPLACE_GUIDANCE = {
    "ebay": {
        "open_label": "Open eBay connection setup",
        "steps": ["Open eBay setup in PosterPro.", "Choose Connect eBay and sign in on eBay's page.", "Review eBay's permission screen and choose Allow.", "Return to PosterPro and choose Run eBay account test.", "If seller policies or a shipping location are missing, use the named setup links and run the test again."],
        "expect": "PosterPro confirms it can read your seller account and required selling policies. The test does not create or change a listing.",
        "troubleshooting": "If eBay says the connection expired, choose Reconnect eBay. If the test names a policy or location, finish that section in eBay setup before retrying.",
    },
    "facebook": {"open_label": "Open Facebook", "open_url": "https://www.facebook.com/marketplace/", "steps": ["Use the browser where the PosterPro extension is paired.", "Open Facebook and sign in normally if asked; never paste your Facebook password into PosterPro.", "Confirm your listing location in the preceding guided-setup step.", "Run the safe Facebook form test and review its field-by-field result; it stops without submitting."], "expect": "PosterPro reports login state and each supported listing field separately. A browser heartbeat alone does not prove that every listing field works.", "troubleshooting": "If the result says LOGIN_REQUIRED, open Facebook and sign in normally, then run the test again. If a field fails, the result names that field and its safe selector error."},
    "mercari": {"open_label": "Open Mercari", "open_url": "https://www.mercari.com/", "steps": ["Open Mercari in the same browser where PosterPro is paired.", "Sign in on Mercari itself if asked; do not enter Mercari credentials in PosterPro.", "Return to PosterPro and start the Mercari real form test.", "Review the field-by-field result. The test stops without submitting."], "expect": "PosterPro reports login state and whether the live create form exposes the supported listing fields.", "troubleshooting": "Complete sign-in at Mercari and run the test again. If a field is absent or its options changed, the field result names it so the adapter can be repaired."},
    "poshmark": {"open_label": "Open Poshmark", "open_url": "https://poshmark.com/", "steps": ["Open Poshmark in the paired browser.", "Sign in normally on Poshmark if asked.", "Return to PosterPro and start the Poshmark real form test.", "Review the field-by-field result; PosterPro will not submit."], "expect": "PosterPro reports whether the live form exposes the destination-specific fields.", "troubleshooting": "Finish sign-in on Poshmark and rerun the test. A missing field is shown by name in the result."},
    "vinted": {"open_label": "Open Vinted", "open_url": "https://www.vinted.com/", "steps": ["Open the Vinted site for your country in the paired browser.", "Sign in normally on Vinted if asked.", "Return to PosterPro and start the Vinted real form test.", "Review the field-by-field result; PosterPro will not submit."], "expect": "The diagnostic records the Vinted region/domain and supported live form fields.", "troubleshooting": "Use your country's Vinted web address if the wrong region opened. Sign in normally and run the test again."},
    "offerup": {"open_label": "Open OfferUp", "open_url": "https://offerup.com/", "steps": ["Open OfferUp in the paired browser.", "Sign in normally on OfferUp if asked.", "Return to PosterPro and start the OfferUp real form test.", "Review the field-by-field result; PosterPro will not submit."], "expect": "PosterPro reports login state and whether the live form exposes the mapped fields.", "troubleshooting": "Finish sign-in on OfferUp and rerun the test. If a field is missing, the diagnostic names it."},
    "etsy": {"open_label": "Open Etsy shop", "open_url": "https://www.etsy.com/your/shops/me/dashboard", "steps": ["Open your Etsy shop dashboard.", "Check that the shop is active and signed in.", "Return to PosterPro and review Etsy connection setup.", "Etsy official API authorization is not complete in this deployment, so PosterPro cannot publish through Etsy yet."], "expect": "Your Etsy seller dashboard opens. This is a shop check, not an API connection.", "troubleshooting": "Etsy authorization still needs PosterPro app setup/approval. Do not enter your shop password or API secrets into an unrelated form."},
}
ALLOWED_DESTINATIONS = set(DESTINATIONS)


def _root(user: User) -> dict[str, Any]:
    value = user.settings_json
    return dict(value) if isinstance(value, dict) else {}


def _state(user: User) -> dict[str, Any]:
    value = _root(user).get(ONBOARDING_KEY)
    return dict(value) if isinstance(value, dict) else {}


def _location_fingerprint(value: str) -> str:
    return sha256(" ".join(str(value or "").split()).casefold().encode("utf-8")).hexdigest()


def _format_public_location(city: str, region: str, postal: str) -> str:
    locality = ", ".join(part for part in (city.strip(), region.strip()) if part)
    return " ".join(part for part in (locality, postal.strip()) if part)


def facebook_location_is_confirmed(user: User) -> bool:
    """True only when the currently saved Facebook location was explicitly confirmed in setup."""
    saved = load_manual_marketplace_settings(user).get("facebook", {}).get("default_location", "")
    confirmation = _state(user).get("facebook_location_confirmation")
    return bool(
        saved
        and isinstance(confirmation, dict)
        and confirmation.get("location_fingerprint") == _location_fingerprint(saved)
        and confirmation.get("confirmed_at")
    )


def facebook_location_candidate(user: User, db: Session) -> dict[str, Any]:
    """Return a safe, seller-location candidate; never infer from shipping or warehouse data."""
    saved = load_manual_marketplace_settings(user).get("facebook", {}).get("default_location", "").strip()
    state = _state(user)
    confirmation = state.get("facebook_location_confirmation")
    if saved:
        confirmed = bool(
            isinstance(confirmation, dict)
            and confirmation.get("location_fingerprint") == _location_fingerprint(saved)
            and confirmation.get("confirmed_at")
        )
        return {
            "value": saved,
            "source": str(confirmation.get("provenance") or "Previously saved Facebook Marketplace location") if confirmed else "Previously saved Facebook Marketplace location (confirm before use)",
            "confirmed": confirmed,
        }

    # Prefer an eBay inventory origin that PosterPro has actually verified
    # against eBay over values inferred from older Facebook listing records.
    root = _root(user)
    ebay = root.get("ebay_marketplace_policy_settings")
    ebay = ebay if isinstance(ebay, dict) else {}
    if ebay.get("merchant_location_verified") is True and str(ebay.get("merchant_location_status") or "").lower() in {"verified", "created"}:
        city = str(ebay.get("merchant_location_city") or "").strip()
        region = str(ebay.get("merchant_location_state_or_province") or "").strip()
        postal = str(ebay.get("merchant_location_postal_code") or "").strip()
        value = _format_public_location(city, region, postal)
        if value and (city or postal):
            return {"value": value, "source": "Verified eBay seller inventory location (confirm for Facebook)", "confirmed": False}

    # Only examine remote Marketplace listings that are actually published, and
    # never interpret shipping destination fields as the seller's location.
    published = db.execute(
        select(MarketplaceListing)
        .join(Listing, Listing.id == MarketplaceListing.listing_id)
        .where(
            Listing.user_id == user.id,
            MarketplaceListing.marketplace == MarketplaceName.facebook,
            MarketplaceListing.status.in_(["PUBLISHED", "UPDATED", "SOLD"]),
            MarketplaceListing.marketplace_listing_id.is_not(None),
        )
        .order_by(MarketplaceListing.updated_at.desc())
        .limit(20)
    ).scalars().all()
    for row in published:
        raw = row.raw_response if isinstance(row.raw_response, dict) else {}
        candidates = [raw]
        for key in ("listing", "seller", "marketplace_listing", "location"):
            nested = raw.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
        for source in candidates:
            # Accept only an explicit listing/seller location object or named
            # seller city/state/postal fields. Exclude address/shipping objects.
            loc = source.get("seller_location") or source.get("item_location") or source.get("marketplace_location")
            if isinstance(loc, dict):
                city = str(loc.get("city") or "").strip()
                region = str(loc.get("state") or loc.get("region") or loc.get("state_or_province") or "").strip()
                postal = str(loc.get("postal_code") or loc.get("postalCode") or loc.get("zip") or "").strip()
                value = _format_public_location(city, region, postal)
                if value:
                    return {"value": value, "source": "Your published Facebook Marketplace listing", "confirmed": False}

    return {"value": "", "source": None, "confirmed": False}


def save_state(user: User, state: dict[str, Any]) -> None:
    root = _root(user)
    root[ONBOARDING_KEY] = state
    user.settings_json = root


def update_task_state(user: User, task_id: str, *, status: str | None = None, error_code: str | None = None, summary: str | None = None, verified: bool = False) -> None:
    """Persist safe task lifecycle metadata; never store credentials or tokens."""
    state = _state(user)
    progress = dict(state.get("task_progress") or {})
    task = dict(progress.get(task_id) or {})
    now = datetime.now(UTC).isoformat()
    task.setdefault("started_at", now)
    if status:
        task["status"] = status
    if verified:
        task["last_verified_at"] = now
    task["last_error_code"] = error_code
    task["last_error_summary"] = (summary or "")[:240] or None
    progress[task_id] = task
    state["task_progress"] = progress
    save_state(user, state)


def record_event(user: User, event_name: str, task_id: str | None = None) -> None:
    state = _state(user)
    events = list(state.get("events") or [])
    events.append({"event": event_name, "task_id": task_id, "at": datetime.now(UTC).isoformat()})
    state["events"] = events[-200:]
    state["updated_at"] = datetime.now(UTC).isoformat()
    save_state(user, state)


def _destination_status(name: str, user: User, db: Session) -> dict[str, Any]:
    if name == "facebook" and not facebook_location_is_confirmed(user):
        candidate = facebook_location_candidate(user, db)
        return {
            "status": "NEEDS_LOCATION_CONFIRMATION",
            "message": "Confirm the Facebook Marketplace listing location in guided setup before testing or marking Facebook ready.",
            "location_candidate": candidate,
        }
    if name == "ebay":
        account = db.execute(select(MarketplaceAccount).where(
            MarketplaceAccount.user_id == user.id,
            MarketplaceAccount.marketplace == MarketplaceName.ebay,
        )).scalar_one_or_none()
        health = summarize_ebay_account_health(account)
        listing_setup_ready = False
        if not health["connected"]:
            status = "AUTH_REQUIRED" if health["reconnect_required"] else "NOT_CONFIGURED"
        elif not account.last_successful_check_at:
            status = "CONNECTED_UNVERIFIED"
        elif account.last_successful_check_at < datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24):
            status = "CONNECTED_UNVERIFIED"
        else:
            prefs = user.settings_json.get("ebay_marketplace_policy_settings") if isinstance(user.settings_json, dict) else {}
            prefs = prefs if isinstance(prefs, dict) else {}
            listing_setup_ready = (
                all(str(prefs.get(key) or "").strip() for key in ("payment_policy_id", "fulfillment_policy_id", "return_policy_id", "merchant_location_key"))
                and prefs.get("merchant_location_verified") is True
            )
            # Keep OAuth health distinct from the publish checklist. A seller
            # account with missing policies is authenticated, but onboarding
            # must not label the destination fully Connected/complete.
            status = "CONNECTED" if listing_setup_ready else "CONNECTED_BUT_NEEDS_ATTENTION"
        if status == "CONNECTED_BUT_NEEDS_ATTENTION":
            message = "eBay sign-in works, but seller policies or a verified shipping location still need setup. Open eBay setup to finish those checks."
        elif status == "CONNECTED_UNVERIFIED":
            message = "eBay sign-in is saved, but run the read-only seller-account check again before relying on it."
        else:
            message = health["status_note"]
        return {"status": status, "message": message, "publish_ready": bool(listing_setup_ready), "verified_at": account.last_successful_check_at.isoformat() if account and account.last_successful_check_at else None}
    if name == "google_photos":
        oauth = get_google_photos_oauth_state(user)
        platform_configured = google_photos_oauth_ready()
        # Platform OAuth application settings and a user's Google grant are
        # separate lifecycles. A saved user grant must remain recognizable;
        # ordinary users must never be sent to a developer-credential form.
        if not oauth.get("connected"):
            if not platform_configured:
                return {"status": "PLATFORM_CONFIG_MISSING", "connection_state": "PLATFORM_CONFIG_MISSING", "platform_configured": False, "message": "PosterPro's Google Photos connection is not available yet. A PosterPro administrator must finish the platform setup; you do not need to enter Google developer credentials."}
            return {"status": "AUTH_REQUIRED", "connection_state": "DISCONNECTED", "platform_configured": True, "message": "Connect the Google account you want to use for photos."}
        expires_at = oauth.get("token_expires_at")
        if expires_at:
            try:
                expired = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).replace(tzinfo=None) <= datetime.now(UTC).replace(tzinfo=None)
            except ValueError:
                expired = False
            if expired:
                # Access-token expiry is normal OAuth lifecycle behavior. A
                # durable refresh token is the user's authorization and must
                # be used automatically; it is not a reason to ask for client
                # credentials or force a consent flow again.
                if oauth.get("has_refresh_token"):
                    try:
                        refreshed = refresh_google_photos_access_token(user, db)
                        return {"status": "CONNECTED", "connection_state": "CONNECTED", "platform_configured": platform_configured, "message": "Google Photos is connected and ready for photo intake.", "verified_at": refreshed.get("last_connected_at") or oauth.get("last_connected_at"), "account": refreshed.get("account_email") or oauth.get("account_email"), "refreshed": True}
                    except GooglePhotosOAuthError as exc:
                        return {"status": "REAUTH_REQUIRED", "connection_state": "REAUTH_REQUIRED", "platform_configured": platform_configured, "message": "Google Photos connection needs to be renewed. PosterPro could not refresh the saved authorization; reconnect through Google sign-in.", "error": str(exc)}
                return {"status": "REAUTH_REQUIRED", "connection_state": "REAUTH_REQUIRED", "platform_configured": platform_configured, "message": "Google Photos connection needs to be renewed. Reconnect through Google sign-in; platform OAuth settings are managed by PosterPro."}
        if oauth.get("connection_state") in {"token_expired", "error"} and not oauth.get("has_refresh_token"):
            return {"status": "REAUTH_REQUIRED", "connection_state": "REAUTH_REQUIRED", "platform_configured": platform_configured, "message": "Google Photos connection needs to be renewed. Reconnect through Google sign-in."}
        # Google Photos is used by PosterPro for authenticated library/media
        # access. A valid saved OAuth connection is the required capability;
        # do not expose an unimplemented synthetic upload test as a user task.
        return {"status": "CONNECTED", "connection_state": "CONNECTED", "platform_configured": platform_configured, "message": "Google Photos is connected and ready for photo intake.", "verified_at": oauth.get("last_connected_at"), "account": oauth.get("account_email")}
    if name == "browser_extension":
        now = datetime.now(UTC).replace(tzinfo=None)
        device = db.execute(select(MarketplaceExtensionDevice).where(
            MarketplaceExtensionDevice.user_id == user.id,
            MarketplaceExtensionDevice.revoked_at.is_(None),
        ).order_by(MarketplaceExtensionDevice.last_seen_at.desc().nullslast())).scalars().first()
        if not device:
            return {"status": "EXTENSION_REQUIRED", "message": "Install and authorize the PosterPro browser connection."}
        recent = bool(device.last_seen_at and device.last_seen_at >= now - timedelta(minutes=2))
        if not recent:
            return {"status": "OFFLINE", "message": "The browser connection has not checked in recently."}
        return {"status": "ONLINE", "message": "Browser connection is online.", "device_name": device.name, "browser": device.browser, "version": device.extension_version, "verified_at": device.last_seen_at.isoformat()}
    if name in DESTINATIONS:
        if name == "etsy":
            return {"status": "AUTH_REQUIRED", "message": "Etsy's official API/shop authorization is not connected here. A browser form check is not a substitute for the official integration."}
        latest = db.execute(select(MarketplaceExtensionJob).where(
            MarketplaceExtensionJob.user_id == user.id,
            MarketplaceExtensionJob.marketplace == name,
            MarketplaceExtensionJob.action == "DIAGNOSTIC",
        ).order_by(MarketplaceExtensionJob.created_at.desc(), MarketplaceExtensionJob.id.desc()).limit(1)).scalars().first()
        if not latest:
            return {"status": "OPERATOR_TEST_REQUIRED", "message": "Start the real, non-submitting form test from this setup step or Marketplace Settings."}
        result = latest.result if isinstance(latest.result, dict) else {}
        if latest.status == "LOGIN_REQUIRED":
            return {"status": "AUTH_REQUIRED", "message": f"Sign in to {DESTINATIONS[name]['title']} in the paired browser, then run the form test again.", "diagnostic_status": latest.status, "last_test_at": latest.created_at.isoformat() if latest.created_at else None}
        if latest.status == "BLOCKED_EXTERNAL":
            return {"status": "BLOCKED_EXTERNAL", "message": latest.error_detail or "The marketplace requires an external security or account step.", "diagnostic_status": latest.status, "last_test_at": latest.created_at.isoformat() if latest.created_at else None}
        if latest.status in {"QUEUED", "CLAIMED", "NAVIGATING", "FORM_DETECTED", "TESTING_FIELDS"}:
            return {"status": "VERIFYING", "message": "The paired browser is checking the live form. This test never submits.", "diagnostic_status": latest.status}
        if latest.status == "COMPLETED" and result.get("capability_ready") is True:
            return {"status": "CONNECTED", "message": "The real form diagnostic passed for the required mapped fields. No listing was submitted.", "diagnostic_status": latest.status, "last_test_at": latest.created_at.isoformat() if latest.created_at else None, "field_results": result.get("field_results") or []}
        failed = result.get("missing_required_fields") or []
        return {"status": "NEEDS_ATTENTION", "message": latest.error_detail or ("The real form test found required fields PosterPro could not safely fill." if failed else "The real form test did not complete successfully."), "diagnostic_status": latest.status, "missing_required_fields": failed, "field_results": result.get("field_results") or [], "last_test_at": latest.created_at.isoformat() if latest.created_at else None}
    if name == "ai":
        setup = public_ai_setup_state(user)
        setup["status"] = setup.get("state") or "NOT_CONFIGURED"
        return setup
    return {"status": "NOT_CONFIGURED", "message": "Setup has not been checked yet."}


def onboarding_snapshot(user: User, db: Session) -> dict[str, Any]:
    state = _state(user)
    selected = [name for name in state.get("selected_marketplaces", []) if name in ALLOWED_DESTINATIONS]
    checks = {
        "ai": _destination_status("ai", user, db),
        "google_photos": _destination_status("google_photos", user, db),
        "browser_extension": _destination_status("browser_extension", user, db),
    }
    checks.update({name: _destination_status(name, user, db) for name in selected})
    tasks = [
        {"id": "choose_marketplaces", "category": "Selling accounts", "title": "Choose where to sell", "purpose": "Only the marketplaces you choose become setup steps. You may choose none and return later.", "why_it_matters": "PosterPro only asks you to set up the services you want to use.", "verification_method": "Your selection is saved to this signed-in account.", "dependencies": [], "required": True, "estimated_minutes": 1, "status": "COMPLETE" if state.get("marketplace_choice_saved") else "NOT_STARTED", "deep_link": None},
        {"id": "ai", "category": "AI", "title": "Choose how PosterPro uses AI", "purpose": "Connect your own OpenAI account or see the truthful PosterPro AI availability.", "why_it_matters": "AI helps identify and prepare inventory, but can be skipped.", "verification_method": "PosterPro sends a small test request and confirms the provider reply.", "dependencies": [], "required": checks["ai"].get("mode") != "DISABLED", "estimated_minutes": 5, **checks["ai"], **({"status": "SKIPPED", "message": "AI is skipped for now; you can connect it later in Settings."} if checks["ai"].get("mode") == "DISABLED" else {}), "deep_link": "/settings?tab=api-keys#openai", "guidance": {"steps": ["Choose Use my own OpenAI account for setup available today.", "Open OpenAI API Keys and sign in.", "Choose Create new secret key, name it PosterPro, and copy it.", "Paste the key into the password box and choose Test and connect.", "PosterPro sends a small test request. When successful, the key is stored encrypted and is never shown again."], "expect": "A Connected result with a verification time. OpenAI bills usage to your OpenAI account.", "troubleshooting": "If the key is rejected, create a new key in OpenAI and check the account's usage/billing. PosterPro Managed AI is Coming Soon here; no payment or paid entitlement can be activated yet."}},
        {"id": "google_photos", "category": "Photos", "title": "Connect Google Photos", "purpose": "Use the Google account already connected for photo intake.", "why_it_matters": "PosterPro uses your photos to prepare inventory.", "verification_method": "PosterPro verifies the saved authorization and account connection automatically.", "dependencies": [], "required": False, "estimated_minutes": 2, **checks["google_photos"], "deep_link": "/settings/intake", "guidance": {"steps": ["Choose Connect Google Photos.", "Sign in to the Google account you use for PosterPro photos.", "Allow access, then return to PosterPro."], "expect": "Google Photos connected and ready.", "troubleshooting": "If the connection expires, choose Reconnect Google Photos."}},
    ]
    assisted = any(name != "ebay" for name in selected)
    if assisted:
        tasks.append({"id": "browser_extension", "category": "Browser connection", "title": "Connect your browser once", "purpose": "Use marketplace sessions already signed in to this browser; never send passwords or cookies to PosterPro.", "why_it_matters": "Assisted marketplace jobs need an online PosterPro browser connection.", "verification_method": "A recent authenticated extension heartbeat proves the device is online, but not that every marketplace form works.", "dependencies": [], "required": True, "estimated_minutes": 3, **checks["browser_extension"], "deep_link": "/settings?tab=marketplaces#browser-extension", "guidance": {"steps": ["Download PosterPro Browser Extension from Settings → Browser Automation.", "Install it in the same Chrome or Edge profile where you sign into marketplaces.", "Open PosterPro in that browser and authorize this browser once using the pairing flow.", "Leave the browser open; PosterPro checks the connection automatically.", "When the heartbeat arrives, this step completes on its own. Online means the extension is connected, not that any marketplace form was tested."], "expect": "A recent browser heartbeat with browser name and extension version. Routine job claims do not require opening the extension popup.", "troubleshooting": "If Offline, open PosterPro in the browser where the extension is installed, check that it is enabled, and reconnect it from Settings → Browser Automation."}})
    extension_state = checks["browser_extension"].get("status")
    for name in selected:
        meta = DESTINATIONS[name]
        destination_check = checks[name]
        if name != "ebay" and (name != "facebook" or facebook_location_is_confirmed(user)) and extension_state in {"EXTENSION_REQUIRED", "OFFLINE"}:
            dependency_copy = "Connect the PosterPro browser extension first." if extension_state == "EXTENSION_REQUIRED" else "Bring the PosterPro browser connection online first."
            destination_check = {**destination_check, "status": extension_state, "message": f"{dependency_copy} PosterPro has not tested this marketplace form yet."}
        if name == "facebook":
            candidate = facebook_location_candidate(user, db)
            state_location_confirmed = facebook_location_is_confirmed(user)
            tasks.append({
                "id": "facebook_location",
                "category": "Facebook Marketplace",
                "title": "Confirm your listing location",
                "purpose": "Choose the seller location Facebook Marketplace should use for your listings.",
                "why_it_matters": "Facebook requires a listing location. PosterPro asks for confirmation here before running its live form test.",
                "verification_method": "The confirmed location is saved to your Facebook marketplace profile and reused by the safe diagnostic.",
                "dependencies": ["browser_extension"],
                "required": True,
                "estimated_minutes": 1,
                "status": "COMPLETE" if state_location_confirmed else "NEEDS_LOCATION_CONFIRMATION",
                "message": "Facebook location confirmed." if state_location_confirmed else "Confirm or edit the suggested location to continue Facebook setup.",
                "candidate": candidate,
                "deep_link": None,
            })
        tasks.append({"id": f"marketplace:{name}", "category": "Selling accounts", "title": f"Check {meta['title']}", "purpose": meta["purpose"], "why_it_matters": "This destination must be tested before PosterPro can call assisted publishing ready.", "verification_method": "A real, non-submitting form test is required; stored settings alone do not count.", "dependencies": (["browser_extension", "facebook_location"] if name == "facebook" else ["browser_extension"]) if name != "ebay" else [], "required": True, "estimated_minutes": 3, **destination_check, "deep_link": meta["deep_link"], "guidance": MARKETPLACE_GUIDANCE.get(name, {})})
    ai_ready = checks["ai"].get("status") == "CONNECTED" or checks["ai"].get("mode") == "DISABLED"
    destinations_ready = all(checks[name].get("status") == "CONNECTED" and checks[name].get("publish_ready", True) for name in selected)
    tasks.extend([
        {"id": "pricing", "category": "Pricing", "title": "Review price protection", "purpose": "Current pricing research is evidence-limited; do not treat a generated estimate as sold-comparable proof.", "required": False, "estimated_minutes": 2, "status": "PARTIAL", "deep_link": "/settings?tab=pricing"},
        {"id": "test_workflow", "category": "Ready to sell", "title": "Run the safe readiness check", "purpose": "Check configured services without publishing a live listing.", "required": True, "estimated_minutes": 2, "status": "READY" if ai_ready and destinations_ready else "NEEDS_ATTENTION", "message": "The selected services have not all passed their live connection checks." if not (ai_ready and destinations_ready) else "Selected services are connected; no marketplace publication was attempted.", "deep_link": "/settings?tab=marketplaces"},
    ])
    skipped = set(state.get("skipped_tasks") or []) - {"facebook_location"}
    for task in tasks:
        if task["id"] in skipped and task.get("status") not in {"COMPLETE", "CONNECTED", "ONLINE"}:
            task["status"] = "SKIPPED"
    required = [task for task in tasks if task.get("required")]
    complete_states = {"COMPLETE", "CONNECTED", "ONLINE", "READY"}
    completed = sum(task.get("status") in complete_states for task in required)
    outstanding = [task for task in required if task.get("status") not in complete_states]
    current_step = str(state.get("current_step") or "welcome")
    if "facebook" in selected and not facebook_location_is_confirmed(user):
        location_index = next((index for index, task in enumerate(tasks) if task.get("id") == "facebook_location"), None)
        current_index = next((index for index, task in enumerate(tasks) if task.get("id") == current_step), None)
        if location_index is not None and current_index is not None and current_index >= location_index:
            current_step = "facebook_location"
    current_task = next((task for task in tasks if task.get("id") == current_step), None)
    if current_task and (current_task.get("status") in complete_states or current_task.get("status") == "SKIPPED"):
        current_index = tasks.index(current_task)
        next_required = next((task for task in tasks[current_index + 1:] if task.get("required") and task.get("status") not in complete_states and task.get("status") != "SKIPPED"), None)
        if next_required:
            current_step = next_required["id"]
        elif outstanding:
            current_step = outstanding[0]["id"]
        else:
            current_step = "test_workflow"
    return {
        "version": 1,
        "started": bool(state.get("started_at")),
        "welcome_dismissed": bool(state.get("welcome_dismissed_at")),
        "completed": bool(required) and not outstanding,
        "current_step": current_step,
        "selected_marketplaces": selected,
        "marketplace_choice_saved": bool(state.get("marketplace_choice_saved")),
        "skipped_tasks": sorted(skipped),
        "task_progress": dict(state.get("task_progress") or {}),
        "progress": {"completed": completed, "required": len(required), "remaining": len(outstanding)},
        "tasks": tasks,
        "events": list(state.get("events") or [])[-20:],
        "updated_at": state.get("updated_at"),
    }
