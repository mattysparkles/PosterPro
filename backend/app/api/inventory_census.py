from fastapi import APIRouter, Depends, HTTPException, Query, Body, UploadFile, File, Header, Response
from sqlalchemy import func, select, or_, cast, String, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import BulkJobResponse, InventoryBulkEditRequest, InventoryBulkRequest, ListingResponse, RecoveryCopyRepairRequest
from app.core.auth import ensure_user_owns_resource, get_current_user
from app.core.database import get_db
from app.core.config import settings
from app.models.models import BulkJob, Listing, User, InventoryBin, InventoryBinAssignment, InventoryEvent, InventoryProduct, InventoryStock, InventoryPackage, InventoryPrintJob, InventoryPrintAgent, InventoryManifestImport, InventoryManifestEntry, InventoryReconciliationDecision, InventoryLocation, InventoryCensusSession, InventoryCensusRecord, ListingVariant, MarketplaceVariant
from secrets import token_urlsafe, token_bytes
import base64
import hmac
from uuid import uuid4
from collections import Counter
import hashlib
from datetime import datetime, timezone, timedelta
from app.services.inventory_service import InventorySafetyError, InventoryService
from app.services.scanner_dispatcher import COMMANDS, dispatch_scan
from app.services.package_labels import PAPER_PROFILES, INVENTORY_LABEL_TEMPLATE, INVENTORY_LABEL_DPI, INVENTORY_LABEL_TEMPLATE_VERSION, data_url, normalize_profile, normalize_inventory_template, render_package_label, render_inventory_label, render_inventory_calibration
from html import escape
import re
import json

router = APIRouter(tags=["inventory"])
bulk_router = APIRouter(tags=["bulk-jobs"])
service = InventoryService()



def _inventory_label_template(user: User) -> dict:
    saved = (user.settings_json or {}).get("inventory_label_template") or {}
    return normalize_inventory_template(saved)


@router.get("/census/label-template")
def get_census_label_template(current_user: User = Depends(get_current_user)):
    return {"template": _inventory_label_template(current_user), "width_mm": 58, "height_mm": 40, "dpi": INVENTORY_LABEL_DPI, "pixel_width": 464, "pixel_height": 320, "template_version": INVENTORY_LABEL_TEMPLATE_VERSION, "calibration_pending": True}


@router.put("/census/label-template")
def update_census_label_template(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    template = normalize_inventory_template(payload)
    bounds = {key: (0.0, 58.0 if "x_" in key or key in {"margin_mm", "barcode_width_mm", "title_width_mm", "sku_width_mm", "shelf_width_mm"} else 40.0) for key in template if key.endswith("_mm")}
    bounds.update({key: (1.0, 100.0) for key in ("title_size_mm", "sku_size_mm", "unit_size_mm", "shelf_size_mm", "barcode_height_mm")})
    bounds.update({"barcode_quiet_zone_modules": (10, 40), "barcode_min_module_dots": (2, 8), "print_scale_percent": (70, 130)})
    for key, (low, high) in bounds.items():
        try: value = float(template[key])
        except (TypeError, ValueError): raise HTTPException(422, f"{key} must be a number.")
        if not low <= value <= high: raise HTTPException(422, f"{key} must be between {low} and {high} mm/value.")
    if int(payload.get("version", INVENTORY_LABEL_TEMPLATE_VERSION)) != INVENTORY_LABEL_TEMPLATE_VERSION:
        raise HTTPException(409, "The supplied label template version is unsupported; reload the current template first.")
    try:
        render_inventory_label({"title":"Test product", "sku":"TEST", "unit_id":"01175", "shelf_code":"A01", "lookup_url":"https://posterpro.sparkleserver.site/inventory/census?item=test", "barcode_token":"ABCDEFGHIJKLMNOP"[:13]}, template=template, layout="qr_code128")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    settings_json = dict(current_user.settings_json or {}); settings_json["inventory_label_template"] = template; current_user.settings_json = settings_json
    db.commit()
    return {"template": template, "width_mm":58, "height_mm":40, "dpi": INVENTORY_LABEL_DPI, "pixel_width":464, "pixel_height":320, "template_version": INVENTORY_LABEL_TEMPLATE_VERSION, "calibration_pending":True}


@router.get("/census/label-template/test")
def census_label_template_test(current_user: User = Depends(get_current_user)):
    return {"image_data_url":data_url(render_inventory_calibration()), "width_mm":58, "height_mm":40, "dpi":INVENTORY_LABEL_DPI, "pixel_width":464, "pixel_height":320, "print_scale_percent":_inventory_label_template(current_user)["print_scale_percent"], "template_version":INVENTORY_LABEL_TEMPLATE_VERSION}


PRINT_MODES = {"automatic_agent", "browser", "queue_only", "disabled"}
PRINT_AGENT_PRINTER = "Sparkles SKU Printer"


def _print_agent_public(agent: InventoryPrintAgent) -> dict:
    status = agent.last_status
    if status == "online" and (not agent.last_seen_at or agent.last_seen_at < datetime.utcnow() - timedelta(seconds=90)):
        status = "offline"
    return {"id": agent.id, "display_name": agent.display_name, "printer_name": agent.printer_name,
            "paired": bool(agent.paired_at and agent.token_hash and not agent.revoked_at), "enabled": agent.enabled,
            "hardware_tested": agent.hardware_tested, "status": status,
            "last_seen_at": agent.last_seen_at.isoformat() if agent.last_seen_at else None,
            "last_error": agent.last_error}


def _auth_print_agent(authorization: str | None, db: Session) -> InventoryPrintAgent:
    if not authorization or not authorization.startswith("Bearer ppprint_"):
        raise HTTPException(401, "Inventory print-agent bearer credential required.")
    raw = authorization.removeprefix("Bearer ").strip()
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    agent = db.execute(select(InventoryPrintAgent).where(InventoryPrintAgent.token_hash == token_hash, InventoryPrintAgent.revoked_at.is_(None))).scalar_one_or_none()
    if not agent:
        raise HTTPException(401, "Print-agent credential is invalid or revoked.")
    return agent


def _require_agent_authorized(agent: InventoryPrintAgent):
    if not agent.paired_at or agent.printer_name != PRINT_AGENT_PRINTER or agent.revoked_at:
        raise HTTPException(403, "This print agent is not paired for the configured inventory printer.")


@router.get("/census/print-settings")
def get_census_print_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    settings_json = current_user.settings_json or {}
    mode = settings_json.get("inventory_print_mode", "browser")
    agents = db.execute(select(InventoryPrintAgent).where(InventoryPrintAgent.user_id == current_user.id, InventoryPrintAgent.revoked_at.is_(None))).scalars().all()
    layout = settings_json.get("inventory_label_layout", "qr")
    active_agents = [a for a in agents if a.enabled and a.hardware_tested and a.paired_at and a.last_status == "online" and a.last_seen_at and a.last_seen_at >= datetime.utcnow() - timedelta(seconds=90)]
    return {"mode": mode if mode in PRINT_MODES else "browser", "layout": layout if layout in {"qr", "code128", "qr_code128"} else "qr", "agents": [_print_agent_public(agent) for agent in agents], "automatic_ready": bool(active_agents)}


@router.put("/census/print-settings")
def update_census_print_settings(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    mode = str(payload.get("mode") or "")
    if mode not in PRINT_MODES:
        raise HTTPException(422, "Choose automatic_agent, browser, queue_only, or disabled.")
    agents = db.execute(select(InventoryPrintAgent).where(InventoryPrintAgent.user_id == current_user.id, InventoryPrintAgent.revoked_at.is_(None))).scalars().all()
    if mode == "automatic_agent" and not any(a.enabled and a.hardware_tested and a.paired_at and a.last_status == "online" and a.last_seen_at and a.last_seen_at >= datetime.utcnow() - timedelta(seconds=90) for a in agents):
        raise HTTPException(409, "Automatic printing requires an enabled, paired, online agent marked physically tested by the operator.")
    layout = str(payload.get("layout") or (current_user.settings_json or {}).get("inventory_label_layout", "qr"))
    if layout not in {"qr", "code128", "qr_code128"}: raise HTTPException(422, "Choose QR, Code 128, or combined label layout.")
    if mode == "automatic_agent" and layout != "qr" and not any(a.enabled and a.hardware_tested for a in agents):
        raise HTTPException(409, "Keep QR as the automatic layout until physical barcode verification is completed.")
    previous_mode = (current_user.settings_json or {}).get("inventory_print_mode", "browser")
    settings_json = dict(current_user.settings_json or {})
    settings_json["inventory_print_mode"] = mode
    settings_json["inventory_label_layout"] = layout
    current_user.settings_json = settings_json
    if previous_mode == "automatic_agent" and mode != "automatic_agent":
        outstanding = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.user_id == current_user.id, InventoryPrintJob.job_type == "census_item_label", InventoryPrintJob.status.in_(["CLAIMED", "RENDERED", "SUBMITTING"])).with_for_update()).scalars().all()
        for job in outstanding:
            if job.status in {"CLAIMED", "RENDERED"}:
                job.status = "CANCELLED"; job.outcome_note = "Automatic mode was disabled before spooler submission."; job.completed_at = datetime.utcnow()
            else:
                job.status = "UNKNOWN"; job.outcome_note = "Automatic mode was disabled while submitting; inspect physical output before reprinting."
            job.lease_token = None; job.lease_expires_at = None
    db.commit()
    return {"mode": mode, "layout": layout}


@router.post("/census/print-agents/pairing-codes")
def create_print_agent_pairing_code(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    name = str(payload.get("display_name") or "Windows WT20 agent").strip()[:128]
    code = "pppair_" + token_urlsafe(24)
    agent = InventoryPrintAgent(user_id=current_user.id, display_name=name or "Windows WT20 agent", printer_name=PRINT_AGENT_PRINTER,
        printer_profile_json={"driver": "WT20", "media_width_um": 58000, "media_height_um": 40000, "dpi_x": 203, "dpi_y": 203, "orientation": "portrait", "scaling_percent": 100},
        pairing_code_hash=hashlib.sha256(code.encode()).hexdigest(), pairing_code_expires_at=datetime.utcnow() + timedelta(minutes=10), last_status="pairing")
    db.add(agent); db.commit(); db.refresh(agent)
    return {"agent_id": agent.id, "pairing_code": code, "expires_in_seconds": 600, "printer_name": PRINT_AGENT_PRINTER}


@router.post("/census/print-agents/pair")
def pair_print_agent(payload: dict = Body(...), db: Session = Depends(get_db)):
    code = str(payload.get("pairing_code") or "")
    name = str(payload.get("display_name") or "Windows WT20 agent").strip()[:128]
    printer = str(payload.get("printer_name") or "")
    if printer != PRINT_AGENT_PRINTER:
        raise HTTPException(422, "The agent may pair only with Sparkles SKU Printer.")
    digest = hashlib.sha256(code.encode()).hexdigest()
    agent = db.execute(select(InventoryPrintAgent).where(InventoryPrintAgent.pairing_code_hash == digest, InventoryPrintAgent.pairing_code_expires_at > datetime.utcnow(), InventoryPrintAgent.paired_at.is_(None), InventoryPrintAgent.revoked_at.is_(None)).with_for_update()).scalar_one_or_none()
    if not agent or not hmac.compare_digest(agent.pairing_code_hash or "", digest):
        raise HTTPException(401, "Pairing code is invalid, expired, or already used.")
    raw_token = "ppprint_" + token_urlsafe(40)
    agent.token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    agent.pairing_code_hash = None; agent.pairing_code_expires_at = None
    agent.display_name = name or agent.display_name; agent.paired_at = datetime.utcnow(); agent.last_seen_at = datetime.utcnow()
    agent.last_status = "offline"; agent.capabilities_json = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
    db.commit()
    return {"agent_id": agent.id, "access_token": raw_token, "printer_name": agent.printer_name, "message": "Save this token in the agent's Windows DPAPI-protected local configuration; it will not be shown again."}


@router.get("/census/print-agents")
def list_print_agents(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.execute(select(InventoryPrintAgent).where(InventoryPrintAgent.user_id == current_user.id).order_by(InventoryPrintAgent.id.desc())).scalars().all()
    return {"items": [_print_agent_public(agent) for agent in rows]}


@router.post("/census/print-agents/{agent_id}/enable")
def enable_print_agent(agent_id: int, payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    agent = db.execute(select(InventoryPrintAgent).where(InventoryPrintAgent.id == agent_id, InventoryPrintAgent.user_id == current_user.id, InventoryPrintAgent.revoked_at.is_(None)).with_for_update()).scalar_one_or_none()
    if not agent or not agent.paired_at: raise HTTPException(404, "Paired printer agent not found.")
    if payload.get("enabled") is True and payload.get("confirm_hardware_tested") is not True:
        raise HTTPException(409, "Enable printing only after completing the Windows physical test checklist and explicitly confirming it.")
    if payload.get("enabled") is True and agent.last_status != "online":
        raise HTTPException(409, "The paired Windows agent must be online before it can be enabled.")
    agent.enabled = bool(payload.get("enabled"))
    if payload.get("confirm_hardware_tested") is True:
        agent.hardware_tested = True
    if not agent.enabled:
        agent.hardware_tested = False
        outstanding = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.agent_id == agent.id, InventoryPrintJob.status.in_(["CLAIMED", "RENDERED", "SUBMITTING"])).with_for_update()).scalars().all()
        for job in outstanding:
            if job.status in {"CLAIMED", "RENDERED"}:
                job.status = "CANCELLED"; job.outcome_note = "Agent was disabled before spooler submission."; job.completed_at = datetime.utcnow()
            else:
                job.status = "UNKNOWN"; job.outcome_note = "Agent was disabled while submitting; inspect physical output before reprinting."
            job.lease_token = None; job.lease_expires_at = None
    db.commit()
    return _print_agent_public(agent)


@router.post("/census/print-agents/{agent_id}/revoke")
def revoke_print_agent(agent_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    agent = db.execute(select(InventoryPrintAgent).where(InventoryPrintAgent.id == agent_id, InventoryPrintAgent.user_id == current_user.id, InventoryPrintAgent.revoked_at.is_(None)).with_for_update()).scalar_one_or_none()
    if not agent: raise HTTPException(404, "Print agent not found.")
    agent.revoked_at = datetime.utcnow(); agent.enabled = False; agent.hardware_tested = False; agent.token_hash = None
    jobs = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.agent_id == agent.id, InventoryPrintJob.status.in_(["CLAIMED", "RENDERED", "SUBMITTING"])).with_for_update()).scalars().all()
    for job in jobs:
        job.status = "UNKNOWN" if job.status == "SUBMITTING" else "CANCELLED"
        job.outcome_note = "Agent revoked while holding the lease; submission may be uncertain." if job.status == "UNKNOWN" else "Agent revoked before spooler submission."
        job.lease_token = None; job.lease_expires_at = None
    db.commit()
    return {"status": "revoked", "agent_id": agent.id}


@router.get("/census/print-agent/profile")
def print_agent_profile(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    agent = _auth_print_agent(authorization, db); _require_agent_authorized(agent)
    agent.last_seen_at = datetime.utcnow(); agent.last_status = "online"; db.commit()
    user = db.get(User, agent.user_id)
    return {"agent_id": agent.id, "printer_name": agent.printer_name, "profile": agent.printer_profile_json or {}, "scope": ["census_label_jobs"], "enabled": agent.enabled, "hardware_tested": agent.hardware_tested, "print_mode": (user.settings_json or {}).get("inventory_print_mode", "browser"), "server_time": datetime.utcnow().isoformat()}


@router.post("/census/print-agent/heartbeat")
def print_agent_heartbeat(payload: dict = Body(...), authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    agent = _auth_print_agent(authorization, db); _require_agent_authorized(agent)
    status = str(payload.get("status") or "online")
    if status not in {"online", "offline", "printer_missing", "printer_offline", "error", "dry_run"}: raise HTTPException(422, "Unsupported local agent status.")
    agent.last_seen_at = datetime.utcnow(); agent.last_status = status
    agent.last_error = str(payload.get("error") or "")[:2000] or None
    agent.capabilities_json = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else agent.capabilities_json
    db.commit()
    return {"status": status, "received_at": agent.last_seen_at.isoformat()}


@router.post("/census/print-agent/jobs/claim")
def claim_print_agent_job(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    agent = _auth_print_agent(authorization, db); _require_agent_authorized(agent)
    if not agent.enabled or not agent.hardware_tested:
        raise HTTPException(403, "This agent is not enabled for automatic printing.")
    mode = (db.get(User, agent.user_id).settings_json or {}).get("inventory_print_mode", "browser")
    if mode != "automatic_agent" or not agent.hardware_tested or not agent.last_seen_at or agent.last_seen_at < datetime.utcnow() - timedelta(seconds=90):
        raise HTTPException(409, "Automatic label printing is not enabled for this paired, physically tested agent.")
    now = datetime.utcnow()
    # A lease timeout after SUBMITTING is ambiguous: never put it back in the
    # queue. A stale CLAIMED/RENDERED job is also kept UNKNOWN until reconciled.
    stale = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.user_id == agent.user_id, InventoryPrintJob.agent_id == agent.id,
        InventoryPrintJob.status.in_(["CLAIMED", "RENDERED", "SUBMITTING"]), InventoryPrintJob.lease_expires_at < now).with_for_update(skip_locked=True)).scalars().all()
    for old in stale:
        old.status = "UNKNOWN"; old.outcome_note = "Agent lease expired; automatic retry is disabled until operator reconciliation."; old.lease_token = None; old.lease_expires_at = None
    job = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.user_id == agent.user_id, InventoryPrintJob.job_type == "census_item_label", InventoryPrintJob.status == "queued").order_by(InventoryPrintJob.created_at, InventoryPrintJob.id).limit(1).with_for_update(skip_locked=True)).scalar_one_or_none()
    if not job:
        agent.last_seen_at = now; agent.last_status = "online"; db.commit()
        return {"job": None, "server_time": now.isoformat()}
    payload = dict(job.payload_json or {})
    item_uuid = str(payload.get("item_uuid") or "")
    record = db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == agent.user_id, InventoryCensusRecord.item_uuid == item_uuid)).scalar_one_or_none()
    if not record or record.status != "present":
        job.status = "CANCELLED"; job.outcome_note = "The census item no longer exists as present stock."; db.commit()
        return {"job": None, "server_time": now.isoformat()}
    location = db.execute(select(InventoryLocation).where(InventoryLocation.user_id == agent.user_id, InventoryLocation.location_key == record.location_key)).scalar_one_or_none()
    base_url = (settings.app_base_url or "https://posterpro.sparkleserver.site").rstrip("/")
    payload.update({"item_uuid": record.item_uuid, "barcode_token": record.barcode_token, "title": record.title_snapshot,
        "sku": record.sku_snapshot or "", "lookup_url": f"{base_url}/inventory/census?item={record.item_uuid}",
        "location_key": record.location_key, "unit_id": location.unit_id if location else "",
        "shelf_code": location.section_code + location.shelf_level if location else record.location_key,
        "location_is_snapshot": True})
    template = _inventory_label_template(db.get(User, agent.user_id))
    layout = str(payload.get("layout") or (db.get(User, agent.user_id).settings_json or {}).get("inventory_label_layout", "qr"))
    try:
        png = render_inventory_label(payload, "thermal_58x40mm", layout=layout, template=template)
    except ValueError as exc:
        job.status = "FAILED"; job.error = str(exc); job.completed_at = now; db.commit()
        raise HTTPException(422, f"Label job failed structured validation: {exc}") from exc
    lease = token_urlsafe(32)
    job.job_uuid = job.job_uuid or str(uuid4()); job.agent_id = agent.id; job.delivery_attempts = int(job.delivery_attempts or 0) + 1
    job.lease_token = lease; job.lease_expires_at = now + timedelta(minutes=3); job.claimed_at = now
    job.status = "RENDERED"; job.label_template_version = INVENTORY_LABEL_TEMPLATE_VERSION
    agent.last_seen_at = now; agent.last_status = "online"
    db.commit()
    return {"job": {"job_id": job.job_uuid, "job_type": "census_item_label", "idempotency_key": job.idempotency_key,
        "lease_token": lease, "lease_expires_at": job.lease_expires_at.isoformat(), "printer_name": agent.printer_name,
        "paper": {"width_mm":58, "height_mm":40, "dpi_x":203, "dpi_y":203, "orientation":"portrait", "scaling_percent":100},
        "template_version": job.label_template_version, "layout": layout, "payload": payload,
        "image_png_base64": base64.b64encode(png).decode("ascii"), "pixel_width":464, "pixel_height":320}, "server_time": now.isoformat()}


@router.post("/census/print-agent/jobs/{job_uuid}/lease")
def renew_agent_print_lease(job_uuid: str, payload: dict = Body(...), authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    agent = _auth_print_agent(authorization, db); _require_agent_authorized(agent)
    job = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.job_uuid == job_uuid, InventoryPrintJob.user_id == agent.user_id, InventoryPrintJob.agent_id == agent.id).with_for_update()).scalar_one_or_none()
    if not job: raise HTTPException(404, "Print job is not assigned to this agent.")
    lease = str(payload.get("lease_token") or "")
    if not job.lease_token or not hmac.compare_digest(job.lease_token, lease): raise HTTPException(409, "Print-job lease does not match.")
    if job.status not in {"RENDERED", "SUBMITTING"}: raise HTTPException(409, "This print job no longer has a renewable lease.")
    now = datetime.utcnow()
    if job.lease_expires_at and job.lease_expires_at < now:
        job.status = "UNKNOWN"; job.outcome_note = "Agent lease expired; automatic retry is disabled."; job.lease_token = None; job.lease_expires_at = None; db.commit()
        raise HTTPException(409, "Lease expired; print outcome requires operator reconciliation.")
    job.lease_expires_at = now + timedelta(minutes=3); agent.last_seen_at = now; agent.last_status = "online"; db.commit()
    return {"job_id":job.job_uuid, "lease_expires_at":job.lease_expires_at.isoformat()}


@router.post("/census/print-agent/jobs/{job_uuid}/state")
def update_agent_print_job_state(job_uuid: str, payload: dict = Body(...), authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    agent = _auth_print_agent(authorization, db); _require_agent_authorized(agent)
    job = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.job_uuid == job_uuid, InventoryPrintJob.user_id == agent.user_id, InventoryPrintJob.agent_id == agent.id).with_for_update()).scalar_one_or_none()
    if not job: raise HTTPException(404, "Print job is not assigned to this agent.")
    supplied_lease = str(payload.get("lease_token") or "")
    if not job.lease_token or not hmac.compare_digest(job.lease_token, supplied_lease): raise HTTPException(409, "Print-job lease does not match.")
    if job.lease_expires_at and job.lease_expires_at < datetime.utcnow():
        job.status = "UNKNOWN"; job.outcome_note = "Agent reported after lease expiry; inspect physical output before reprinting."; job.lease_token = None; db.commit()
        raise HTTPException(409, "Lease expired; the print outcome is unknown and was not retried.")
    requested = str(payload.get("state") or "")
    allowed = {"RENDERED": {"SUBMITTING", "FAILED", "CANCELLED"}, "SUBMITTING": {"SPOOLER_ACCEPTED", "FAILED", "UNKNOWN"}}
    if requested not in allowed.get(job.status, set()): raise HTTPException(409, f"Invalid print transition {job.status} -> {requested}.")
    if requested == "SUBMITTING":
        user = db.get(User, agent.user_id)
        if (user.settings_json or {}).get("inventory_print_mode") != "automatic_agent" or not agent.enabled or not agent.hardware_tested:
            job.status = "CANCELLED"; job.outcome_note = "Printing mode or agent authorization was disabled before spooler submission."; job.lease_token = None; job.lease_expires_at = None; job.completed_at = datetime.utcnow(); db.commit()
            raise HTTPException(409, "Automatic printing was disabled before spooler submission; this job will not be printed.")
    job.status = requested
    if requested in {"SPOOLER_ACCEPTED", "FAILED", "UNKNOWN", "CANCELLED"}:
        job.completed_at = datetime.utcnow(); job.lease_token = None; job.lease_expires_at = None
    job.spooler_job_id = str(payload.get("spooler_job_id") or "")[:128] or job.spooler_job_id
    job.error = str(payload.get("error") or "")[:2000] or None
    job.outcome_note = str(payload.get("note") or "")[:2000] or None
    agent.last_seen_at = datetime.utcnow(); agent.last_status = "online"
    db.add(InventoryEvent(user_id=agent.user_id, operation_id=str(uuid4()), event_type="census_print_agent_state", entity_type="print_job", entity_key=job.job_uuid,
        payload_json={"state":requested, "spooler_job_id":job.spooler_job_id, "note":job.outcome_note}))
    db.commit()
    return {"job_id":job.job_uuid, "state":job.status, "physical_print_verified":False}


@router.get("/census/print-jobs")
def list_census_print_jobs(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _expire_print_job_leases(db, current_user.id)
    jobs = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.user_id == current_user.id, InventoryPrintJob.job_type == "census_item_label").order_by(InventoryPrintJob.created_at.desc()).limit(limit)).scalars().all()
    return {"items": [{"job_id": job.job_uuid, "database_id": job.id, "status": job.status, "attempts": job.delivery_attempts or 0,
        "printer_agent_id": job.agent_id, "spooler_job_id": job.spooler_job_id, "error": job.error, "outcome_note": job.outcome_note,
        "created_at": job.created_at.isoformat() if job.created_at else None, "item_uuid": (job.payload_json or {}).get("item_uuid"),
        "reprint_of_job_id": job.reprint_of_job_id} for job in jobs]}


@router.post("/census/print-jobs/{job_id}/reconcile")
def reconcile_census_print_job(job_id: int, payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.id == job_id, InventoryPrintJob.user_id == current_user.id, InventoryPrintJob.job_type == "census_item_label").with_for_update()).scalar_one_or_none()
    if not job: raise HTTPException(404, "Census label job not found.")
    if job.status not in {"UNKNOWN", "SPOOLER_ACCEPTED", "dispatched", "FAILED"}: raise HTTPException(409, "This job is not awaiting print outcome reconciliation.")
    resolution = str(payload.get("resolution") or "")
    if resolution not in {"operator_saw_label", "operator_did_not_see_label"}: raise HTTPException(422, "Choose whether the operator physically saw the label.")
    job.status = "OPERATOR_CONFIRMED_PRINTED" if resolution == "operator_saw_label" else "OPERATOR_CONFIRMED_NOT_PRINTED"
    job.outcome_note = "Operator-reported observation; PosterPro has no independent physical print sensor."
    db.add(InventoryEvent(user_id=current_user.id, operation_id=str(uuid4()), event_type="census_print_outcome_reconciled", entity_type="print_job", entity_key=job.job_uuid or str(job.id), payload_json={"resolution":resolution}))
    db.commit()
    return {"job_id":job.job_uuid, "status":job.status, "operator_reported":True, "physical_print_sensor_verified":False,
        "reprint_required":resolution == "operator_did_not_see_label"}


def _census_location_key(payload: dict) -> str:
    unit = str(payload.get("unit_id") or "").strip()
    section = str(payload.get("section_code") or "").strip().upper()
    shelf = str(payload.get("shelf_level") or "").strip().upper()
    position = str(payload.get("position_code") or "").strip().upper()
    if not unit or not section or not shelf:
        raise HTTPException(422, "Choose a unit, section, and shelf or bulk area.")
    return "/".join([unit, section, shelf, *([position] if position else [])])


def _census_location_payload(row: InventoryLocation) -> dict:
    return {"location_key": row.location_key, "unit_id": row.unit_id, "section_code": row.section_code,
            "shelf_level": row.shelf_level, "position_code": row.position_code, "location_type": row.location_type,
            "accessibility": row.accessibility, "is_full": row.is_full}


def _record_inventory_event(db: Session, *, user_id: int, event_type: str, entity_type: str, entity_key: str, payload: dict) -> None:
    """Every physical mutation receives an idempotent, auditable operation id."""
    db.add(InventoryEvent(user_id=user_id, operation_id=str(uuid4()), event_type=event_type, entity_type=entity_type, entity_key=entity_key, payload_json=payload))


def _new_census_barcode_token(db: Session) -> str:
    """Create a short 65-bit Base32 token with an authoritative DB uniqueness check."""
    for _ in range(10):
        token = base64.b32encode(token_bytes(8)).decode("ascii").rstrip("=")
        if not db.execute(select(InventoryCensusRecord.id).where(InventoryCensusRecord.barcode_token == token)).first():
            return token
    raise HTTPException(503, "Could not allocate a unique barcode identity; retry this census action.")


def _expire_print_job_leases(db: Session, user_id: int) -> int:
    now = datetime.utcnow()
    jobs = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.user_id == user_id,
        InventoryPrintJob.status.in_(["CLAIMED", "RENDERED", "SUBMITTING"]), InventoryPrintJob.lease_expires_at < now).with_for_update(skip_locked=True)).scalars().all()
    for job in jobs:
        job.status = "UNKNOWN"; job.outcome_note = "Agent lease expired or disconnected; inspect physical output before reprinting."; job.lease_token = None; job.lease_expires_at = None
    if jobs: db.commit()
    return len(jobs)


def _package_payload(db: Session, package: InventoryPackage) -> dict:
    """Serialize a package without exposing a cross-user physical location."""
    stocks = db.execute(select(InventoryStock, InventoryProduct).join(InventoryProduct, InventoryStock.product_id == InventoryProduct.id).where(
        InventoryStock.user_id == package.user_id, InventoryStock.location_key == package.package_uuid,
    )).all()
    parent = None
    if package.parent_location_key:
        tote = db.execute(select(InventoryBin).where(InventoryBin.user_id == package.user_id, InventoryBin.container_uuid == package.parent_location_key)).scalar_one_or_none()
        nested = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == package.user_id, InventoryPackage.package_uuid == package.parent_location_key)).scalar_one_or_none()
        parent = (tote.code if tote else (nested.package_code if nested else package.parent_location_key))
    return {
        "id": package.id, "package_uuid": package.package_uuid, "package_code": package.package_code,
        "status": package.status, "parent_location_key": package.parent_location_key, "parent_location_label": parent or "Outside tote",
        "lookup_url": f"/inventory/packages/{package.package_code}", "metadata": package.metadata_json or {},
        "items": [{"product_id": product.id, "title": product.title, "barcode": (product.identifiers_json or {}).get("barcode"), "on_hand": stock.on_hand, "reserved": stock.reserved, "available": max(0, stock.on_hand-stock.reserved)} for stock, product in stocks],
        "quantity": sum(stock.on_hand for stock, _ in stocks),
    }


def _owned_location(db: Session, user_id: int, key: str | None, *, exclude_package_uuid: str | None = None) -> bool:
    if not key:
        return True
    tote = db.execute(select(InventoryBin.id).where(InventoryBin.user_id == user_id, InventoryBin.container_uuid == key)).first()
    package = db.execute(select(InventoryPackage.id).where(InventoryPackage.user_id == user_id, InventoryPackage.package_uuid == key)).first()
    shelf = db.execute(select(InventoryLocation.id).where(InventoryLocation.user_id == user_id, InventoryLocation.location_key == key)).first()
    return bool(tote or (package and key != exclude_package_uuid) or shelf)


def _ensure_no_package_cycle(db: Session, package: InventoryPackage, parent_key: str | None) -> None:
    cursor = parent_key
    seen: set[str] = set()
    while cursor:
        if cursor == package.package_uuid:
            raise HTTPException(422, "A package cannot contain itself or one of its ancestors.")
        if cursor in seen:
            raise HTTPException(409, "The requested package hierarchy contains a cycle.")
        seen.add(cursor)
        parent = db.execute(select(InventoryPackage).where(InventoryPackage.user_id == package.user_id, InventoryPackage.package_uuid == cursor)).scalar_one_or_none()
        cursor = parent.parent_location_key if parent else None


def _location_label(db: Session, *, user_id: int, location_key: str) -> str:
    """Resolve an owned physical key without ever leaking another owner's data."""
    tote = db.execute(select(InventoryBin).where(
        InventoryBin.user_id == user_id, InventoryBin.container_uuid == location_key,
    )).scalar_one_or_none()
    if tote:
        return tote.code
    package = db.execute(select(InventoryPackage).where(
        InventoryPackage.user_id == user_id, InventoryPackage.package_uuid == location_key,
    )).scalar_one_or_none()
    if package:
        return package.package_code
    shelf = db.execute(select(InventoryLocation).where(InventoryLocation.user_id == user_id, InventoryLocation.location_key == location_key)).scalar_one_or_none()
    return shelf.location_key if shelf else "Outside / legacy location"


@router.get("/census/locations")
def census_locations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.execute(select(InventoryLocation).where(InventoryLocation.user_id == current_user.id).order_by(InventoryLocation.unit_id, InventoryLocation.section_code, InventoryLocation.shelf_level, InventoryLocation.position_code)).scalars().all()
    return {"items": [_census_location_payload(row) for row in rows]}


@router.post("/census/locations")
def save_census_location(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    key = _census_location_key(payload)
    accessibility = str(payload.get("accessibility") or "accessible")
    if accessibility not in {"accessible", "staged_inaccessible", "missing_unresolved", "shipped_removed"}:
        raise HTTPException(422, "Choose a supported physical accessibility state.")
    row = db.execute(select(InventoryLocation).where(InventoryLocation.user_id == current_user.id, InventoryLocation.location_key == key)).scalar_one_or_none()
    if row is None:
        row = InventoryLocation(user_id=current_user.id, location_key=key, unit_id=str(payload["unit_id"]).strip(), section_code=str(payload["section_code"]).strip().upper(), shelf_level=str(payload["shelf_level"]).strip().upper(), position_code=(str(payload.get("position_code") or "").strip().upper() or None))
        db.add(row)
    row.location_type = str(payload.get("location_type") or "shelf")[:32]
    row.accessibility = accessibility
    row.is_full = bool(payload.get("is_full", False))
    db.commit(); db.refresh(row)
    return _census_location_payload(row)


@router.get("/census/sessions/{session_key}")
def census_session(session_key: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _expire_print_job_leases(db, current_user.id)
    row = db.execute(select(InventoryCensusSession).where(InventoryCensusSession.user_id == current_user.id, InventoryCensusSession.session_key == session_key)).scalar_one_or_none()
    if row is None:
        row = InventoryCensusSession(user_id=current_user.id, session_key=session_key)
        db.add(row); db.commit(); db.refresh(row)
    counts = db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == current_user.id, InventoryCensusRecord.session_key == session_key)).scalars().all()
    warning_count = db.execute(select(func.count()).select_from(InventoryEvent).where(InventoryEvent.user_id == current_user.id, InventoryEvent.session_id == session_key, InventoryEvent.event_type == "census_duplicate_warning")).scalar_one()
    jobs = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.user_id == current_user.id, InventoryPrintJob.job_type == "census_item_label", InventoryPrintJob.created_at >= row.created_at)).scalars().all()
    return {"session_key": row.session_key, "active_location_key": row.active_location_key, "items_processed": len(counts), "matched": sum(not item.provisional for item in counts), "unmatched": sum(item.provisional for item in counts), "duplicate_warnings": warning_count,
        "labels_pending": sum(job.status in {"queued", "created", "CLAIMED", "RENDERED", "SUBMITTING"} for job in jobs),
        "labels_browser_dispatched": sum(job.status == "dispatched" for job in jobs),
        "labels_spooler_accepted": sum(job.status == "SPOOLER_ACCEPTED" for job in jobs),
        "labels_operator_reported_printed": sum(job.status == "OPERATOR_CONFIRMED_PRINTED" for job in jobs),
        "labels_unknown": sum(job.status in {"UNKNOWN", "unknown"} for job in jobs),
        "items": [{"item_uuid": item.item_uuid, "listing_id": item.listing_id, "title": item.title_snapshot, "location_key": item.location_key, "provisional": item.provisional, "status": item.status} for item in counts[-50:]]}


@router.patch("/census/sessions/{session_key}")
def update_census_session(session_key: str, payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.execute(select(InventoryCensusSession).where(InventoryCensusSession.user_id == current_user.id, InventoryCensusSession.session_key == session_key)).scalar_one_or_none()
    if row is None:
        row = InventoryCensusSession(user_id=current_user.id, session_key=session_key); db.add(row)
    if "active_location" in payload:
        location = payload.get("active_location")
        row.active_location_key = _census_location_key(location) if location else None
        if location: save_census_location(location, db, current_user)
    db.commit()
    return {"session_key": row.session_key, "active_location_key": row.active_location_key}


@router.get("/census/search")
def census_search(query: str = Query(..., min_length=2, max_length=255), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    needle = query.strip()
    scanned_location = db.execute(select(InventoryLocation).where(InventoryLocation.user_id == current_user.id, InventoryLocation.location_key == needle)).scalar_one_or_none()
    if scanned_location:
        return {"items": [], "scanned_location": _census_location_payload(scanned_location)}
    item_token = needle.replace("-", "").lower()
    scanned = db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == current_user.id, or_(func.replace(InventoryCensusRecord.item_uuid, "-", "").ilike(item_token), InventoryCensusRecord.barcode_token == needle.upper()))).scalar_one_or_none()
    if scanned:
        return {"items": [], "scanned_item": {"item_uuid": scanned.item_uuid, "barcode_token": scanned.barcode_token, "title": scanned.title_snapshot, "sku": scanned.sku_snapshot, "location_key": scanned.location_key, "provisional": scanned.provisional, "status": scanned.status}}
    pattern = f"%{needle}%"
    variant_ids = select(ListingVariant.listing_id).where(or_(ListingVariant.sku.ilike(pattern), ListingVariant.barcode.ilike(pattern)))
    rows = db.execute(select(Listing).where(Listing.user_id == current_user.id, or_(Listing.title.ilike(pattern), Listing.description.ilike(pattern), Listing.canonical_description.ilike(pattern), Listing.source_type.ilike(pattern), cast(Listing.item_specifics, String).ilike(pattern), cast(Listing.source_metadata, String).ilike(pattern), Listing.id.in_(variant_ids))).order_by(Listing.updated_at.desc()).limit(25)).scalars().all()
    results=[]
    for listing in rows:
        locs = db.execute(select(InventoryCensusRecord.location_key).where(InventoryCensusRecord.user_id == current_user.id, InventoryCensusRecord.listing_id == listing.id, InventoryCensusRecord.status == "present")).scalars().all()
        legacy_locations = db.execute(select(InventoryBin.code).join(InventoryBinAssignment, InventoryBinAssignment.bin_id == InventoryBin.id).where(InventoryBinAssignment.user_id == current_user.id, InventoryBinAssignment.listing_id == listing.id, InventoryBinAssignment.active.is_(True))).scalars().all()
        locs = list(dict.fromkeys([*(listing.storage_unit_name and [listing.storage_unit_name] or []), *legacy_locations, *locs]))
        variant = db.execute(select(ListingVariant).where(ListingVariant.listing_id == listing.id).order_by(ListingVariant.id).limit(1)).scalar_one_or_none()
        remote_sku = db.execute(select(MarketplaceVariant.remote_sku).join(ListingVariant, MarketplaceVariant.variant_id == ListingVariant.id).where(ListingVariant.listing_id == listing.id, MarketplaceVariant.remote_sku.is_not(None)).limit(1)).scalar_one_or_none()
        metadata_sku = (listing.source_metadata or {}).get("sku") or (listing.marketplace_data or {}).get("sku")
        results.append({"listing_id": listing.id, "title": listing.title, "description": (listing.canonical_description or listing.description or "")[:240], "image": (listing.image_urls or [None])[0], "sku": (variant.sku if variant else None) or remote_sku or metadata_sku, "barcode": variant.barcode if variant else None, "source": listing.source_type, "brand": (listing.item_specifics or {}).get("Brand") or (listing.source_metadata or {}).get("brand"), "model": (listing.item_specifics or {}).get("Model"), "status": str(listing.status.value if hasattr(listing.status, "value") else listing.status), "locations": locs, "score": 100 if needle.casefold() == str(listing.title or "").casefold() else (90 if needle.casefold() in str(listing.title or "").casefold() else 60)})
    results.sort(key=lambda item: (-item["score"], item["listing_id"]))
    return {"items": results}


@router.post("/census/confirm")
def census_confirm(payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    operation_id = str(payload.get("operation_id") or "").strip()
    session_key = str(payload.get("session_key") or "").strip()
    location_key = str(payload.get("location_key") or "").strip()
    if not operation_id or not session_key or not location_key: raise HTTPException(422, "Session, location, and operation ID are required.")
    fingerprint = hashlib.sha256(json.dumps({"session_key":session_key, "location_key":location_key, "listing_id":payload.get("listing_id"), "provisional_title":payload.get("provisional_title"), "description":payload.get("description"), "allow_duplicate":bool(payload.get("allow_duplicate"))}, sort_keys=True).encode()).hexdigest()
    replay = db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == current_user.id, InventoryCensusRecord.operation_id == operation_id)).scalar_one_or_none()
    if replay:
        if replay.request_fingerprint != fingerprint: raise HTTPException(409, "Idempotency key was already used for a different census item.")
        print_job = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.user_id == current_user.id, InventoryPrintJob.idempotency_key == f"census:{current_user.id}:{operation_id}:label")).scalar_one_or_none()
        return {"status": "replayed", "replayed": True, "item_uuid": replay.item_uuid, "barcode_token": replay.barcode_token, "title": replay.title_snapshot, "sku":replay.sku_snapshot, "location_key":replay.location_key, "print_job_id": print_job.id if print_job else None, "print_mode": (current_user.settings_json or {}).get("inventory_print_mode", "browser")}
    loc = db.execute(select(InventoryLocation).where(InventoryLocation.user_id == current_user.id, InventoryLocation.location_key == location_key)).scalar_one_or_none()
    if loc is None: raise HTTPException(422, "Save/select a census location before confirming items.")
    listing_id = payload.get("listing_id")
    listing = db.get(Listing, int(listing_id)) if listing_id else None
    if listing and listing.user_id != current_user.id: raise HTTPException(404, "Product not found.")
    provisional = listing is None
    title = str((listing.title if listing else payload.get("provisional_title")) or "").strip()[:255]
    if not title: raise HTTPException(422, "Confirm a catalog product or enter a provisional description.")
    if listing:
        duplicate = db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == current_user.id, InventoryCensusRecord.listing_id == listing.id, InventoryCensusRecord.location_key == location_key, InventoryCensusRecord.status == "present")).scalar_one_or_none()
        if duplicate and not payload.get("allow_duplicate"):
            db.add(InventoryEvent(user_id=current_user.id, operation_id=str(uuid4()), event_type="census_duplicate_warning", entity_type="listing", entity_key=str(listing.id), session_id=session_key, payload_json={"location_key":location_key, "existing_item_uuid":duplicate.item_uuid}))
            db.commit()
            raise HTTPException(409, detail={"message":"This product is already recorded at this location. Confirm another physical unit explicitly to continue.", "existing_item_uuid": duplicate.item_uuid})
    product = None
    if listing:
        product_key = f"listing-{listing.id}"
        product = db.execute(select(InventoryProduct).where(InventoryProduct.user_id == current_user.id, InventoryProduct.product_key == product_key)).scalar_one_or_none()
        if product is None:
            product = InventoryProduct(user_id=current_user.id, product_key=product_key, title=listing.title, source_json={"listing_id": listing.id}, enrichment_status="matched"); db.add(product); db.flush()
    else:
        product_key = "census-" + str(uuid4())
        product = InventoryProduct(user_id=current_user.id, product_key=product_key, title=title, facts_json={"description": str(payload.get("description") or "")[:2000]}, source_json={"source":"shelf_census","reconciliation_required":True}, enrichment_status="provisional"); db.add(product); db.flush()
    item_uuid = str(uuid4())
    barcode_token = _new_census_barcode_token(db)
    record = InventoryCensusRecord(user_id=current_user.id, item_uuid=item_uuid, barcode_token=barcode_token, operation_id=operation_id, request_fingerprint=fingerprint, session_key=session_key, listing_id=listing.id if listing else None, inventory_product_id=product.id, location_key=location_key, title_snapshot=title, provisional=provisional)
    stock = db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == product.id, InventoryStock.location_key == location_key)).scalar_one_or_none()
    if stock is None: stock = InventoryStock(user_id=current_user.id, product_id=product.id, location_key=location_key, on_hand=0); db.add(stock)
    stock.on_hand += 1
    db.add(record); db.flush()
    existing_variant = db.execute(select(ListingVariant).where(ListingVariant.listing_id == listing.id).order_by(ListingVariant.id).limit(1)).scalar_one_or_none() if listing else None
    marketplace_sku = db.execute(select(MarketplaceVariant.remote_sku).join(ListingVariant, MarketplaceVariant.variant_id == ListingVariant.id).where(ListingVariant.listing_id == listing.id, MarketplaceVariant.remote_sku.is_not(None)).limit(1)).scalar_one_or_none() if listing else None
    metadata_sku = ((listing.source_metadata or {}).get("sku") or (listing.marketplace_data or {}).get("sku")) if listing else None
    sku = (payload.get("sku") or (existing_variant.sku if existing_variant else None) or marketplace_sku or metadata_sku) or item_uuid[:12].upper()
    record.sku_snapshot = str(sku)
    base_url = (settings.app_base_url or "https://posterpro.sparkleserver.site").rstrip("/")
    label_layout = (current_user.settings_json or {}).get("inventory_label_layout", "qr")
    if label_layout not in {"qr", "code128", "qr_code128"}: label_layout = "qr"
    label_payload={"title": title, "sku": str(sku), "item_uuid": item_uuid, "barcode_token": barcode_token, "lookup_url": f"{base_url}/inventory/census?item={item_uuid}", "location_key": location_key, "unit_id": loc.unit_id, "shelf_code": loc.section_code + loc.shelf_level, "listing_id": listing.id if listing else None, "location_is_snapshot": True, "layout": label_layout}
    print_mode = (current_user.settings_json or {}).get("inventory_print_mode", "browser")
    if print_mode not in PRINT_MODES: print_mode = "browser"
    if print_mode != "disabled":
        job=InventoryPrintJob(user_id=current_user.id, listing_id=listing.id if listing else None, job_type="census_item_label", copies=1, paper_profile="thermal_58x40mm", status="queued", payload_json=label_payload, job_uuid=str(uuid4()), idempotency_key=f"census:{current_user.id}:{operation_id}:label", label_template_version=INVENTORY_LABEL_TEMPLATE_VERSION); db.add(job)
    else:
        job = None
    db.add(InventoryEvent(user_id=current_user.id, operation_id=operation_id, event_type="census_item_confirmed", entity_type="census_item", entity_key=item_uuid, session_id=session_key, payload_json={"listing_id": listing.id if listing else None, "location_key": location_key, "provisional": provisional, "stock_id": stock.id}))
    session = db.execute(select(InventoryCensusSession).where(InventoryCensusSession.user_id == current_user.id, InventoryCensusSession.session_key == session_key)).scalar_one_or_none()
    if session is None: session=InventoryCensusSession(user_id=current_user.id, session_key=session_key); db.add(session)
    session.active_location_key=location_key
    db.commit()
    return {"status":"created", "replayed":False, "item_uuid":item_uuid, "barcode_token":barcode_token, "title":title, "provisional":provisional, "sku":str(sku), "location_key":location_key, "print_job_id":job.id if job else None, "print_mode":print_mode, "label_payload":label_payload}


@router.get("/census/items/{item_uuid}")
def get_census_item(item_uuid: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row=db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == current_user.id, InventoryCensusRecord.item_uuid == item_uuid)).scalar_one_or_none()
    if not row: raise HTTPException(404, "Inventory item not found.")
    return {"item_uuid":row.item_uuid, "barcode_token":row.barcode_token, "listing_id":row.listing_id, "title":row.title_snapshot, "sku":row.sku_snapshot, "location_key":row.location_key, "provisional":row.provisional, "status":row.status}


@router.get("/census/items/{item_uuid}/label")
def census_item_label(item_uuid: str, layout: str = Query("qr", pattern="^(qr_code128|qr|code128)$"), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row=db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == current_user.id, InventoryCensusRecord.item_uuid == item_uuid)).scalar_one_or_none()
    if not row: raise HTTPException(404, "Inventory item not found.")
    location=db.execute(select(InventoryLocation).where(InventoryLocation.user_id == current_user.id, InventoryLocation.location_key == row.location_key)).scalar_one_or_none()
    base_url = (settings.app_base_url or "https://posterpro.sparkleserver.site").rstrip("/")
    payload={"title":row.title_snapshot, "sku":row.sku_snapshot or row.item_uuid[:12].upper(), "item_uuid":row.item_uuid, "barcode_token":row.barcode_token, "lookup_url":f"{base_url}/inventory/census?item={row.item_uuid}", "location_key":row.location_key, "unit_id":location.unit_id if location else "", "shelf_code":(location.section_code + location.shelf_level) if location else row.location_key, "location_is_snapshot":True}
    template = _inventory_label_template(current_user)
    return {"image_data_url":data_url(render_inventory_label(payload, "thermal_58x40mm", layout=layout, template=template)), "payload":payload, "width_mm":58, "height_mm":40, "dpi":INVENTORY_LABEL_DPI, "pixel_width":464, "pixel_height":320, "template_version":INVENTORY_LABEL_TEMPLATE_VERSION, "print_scale_percent":template["print_scale_percent"], "physical_print_verified":False}


@router.post("/census/items/{item_uuid}/reprint")
def reprint_census_item(item_uuid: str, payload: dict = Body(default={}), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row=db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == current_user.id, InventoryCensusRecord.item_uuid == item_uuid)).scalar_one_or_none()
    if not row: raise HTTPException(404, "Inventory item not found.")
    operation_id = str(payload.get("operation_id") or uuid4())
    idem = f"census:{current_user.id}:reprint:{operation_id}"
    old = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.user_id == current_user.id, InventoryPrintJob.idempotency_key == idem)).scalar_one_or_none()
    if old: return {"job_id":old.id, "job_uuid":old.job_uuid, "status":old.status, "item_uuid":row.item_uuid, "replayed":True}
    job=InventoryPrintJob(user_id=current_user.id, listing_id=row.listing_id, job_type="census_item_label", copies=1, paper_profile="thermal_58x40mm", status="queued", payload_json={"item_uuid":row.item_uuid, "reprint":True}, job_uuid=str(uuid4()), idempotency_key=idem, label_template_version=INVENTORY_LABEL_TEMPLATE_VERSION, reprint_of_job_id=db.execute(select(InventoryPrintJob.id).where(InventoryPrintJob.user_id == current_user.id, InventoryPrintJob.job_type == "census_item_label", InventoryPrintJob.payload_json["item_uuid"].as_string() == row.item_uuid).order_by(InventoryPrintJob.created_at).limit(1)).scalar_one_or_none())
    db.add(job); db.add(InventoryEvent(user_id=current_user.id, operation_id=str(uuid4()), event_type="census_label_reprint_queued", entity_type="census_item", entity_key=row.item_uuid, session_id=row.session_key, payload_json={"item_uuid":row.item_uuid}))
    db.commit(); db.refresh(job)
    return {"job_id":job.id, "job_uuid":job.job_uuid, "status":job.status, "item_uuid":row.item_uuid, "replayed":False}


@router.post("/census/items/{item_uuid}/undo")
def undo_census_item(item_uuid: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row=db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == current_user.id, InventoryCensusRecord.item_uuid == item_uuid).with_for_update()).scalar_one_or_none()
    if not row: raise HTTPException(404, "Census item not found.")
    if row.status != "present": raise HTTPException(409, "This census action was already reversed or removed.")
    stock=db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == row.inventory_product_id, InventoryStock.location_key == row.location_key).with_for_update()).scalar_one_or_none()
    if not stock or stock.on_hand <= 0: raise HTTPException(409, "Physical stock has since changed; reconcile before undoing.")
    in_flight = db.execute(select(InventoryPrintJob.id).where(InventoryPrintJob.user_id == current_user.id, InventoryPrintJob.job_type == "census_item_label", InventoryPrintJob.payload_json["item_uuid"].as_string() == row.item_uuid, InventoryPrintJob.status.in_(["SUBMITTING", "SPOOLER_ACCEPTED", "UNKNOWN", "dispatched"]))).first()
    if in_flight:
        raise HTTPException(409, "A label is submitting or its print outcome is unknown. Reconcile it before reversing the census item.")
    stock.on_hand -= 1; row.status="reversed"
    pending = db.execute(select(InventoryPrintJob).where(InventoryPrintJob.user_id == current_user.id, InventoryPrintJob.job_type == "census_item_label", InventoryPrintJob.payload_json["item_uuid"].as_string() == row.item_uuid, InventoryPrintJob.status.in_(["queued", "CLAIMED", "RENDERED"])).with_for_update()).scalars().all()
    for pending_job in pending:
        pending_job.status = "CANCELLED"; pending_job.outcome_note = "Census item was reversed before spooler submission."; pending_job.lease_token = None; pending_job.lease_expires_at = None; pending_job.completed_at = datetime.utcnow()
    db.add(InventoryEvent(user_id=current_user.id, operation_id=str(uuid4()), event_type="census_item_undo", entity_type="census_item", entity_key=item_uuid, session_id=row.session_key, payload_json={"location_key":row.location_key, "listing_id":row.listing_id, "stock_id":stock.id}))
    db.commit()
    return {"status":"reversed", "item_uuid":item_uuid, "title":row.title_snapshot, "location_key":row.location_key}


@router.post("/census/items/{item_uuid}/move")
def move_census_item(item_uuid: str, payload: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    operation_id = str(payload.get("operation_id") or "").strip()
    destination_key = str(payload.get("destination_location_key") or "").strip()
    allow_duplicate = bool(payload.get("allow_duplicate"))
    if not operation_id or not destination_key: raise HTTPException(422, "Operation ID and destination location are required.")
    fingerprint=hashlib.sha256(f"move:{item_uuid}:{destination_key}:{allow_duplicate}".encode()).hexdigest()
    prior=db.execute(select(InventoryEvent).where(InventoryEvent.user_id == current_user.id, InventoryEvent.operation_id == operation_id)).scalar_one_or_none()
    if prior:
        if (prior.payload_json or {}).get("request_fingerprint") != fingerprint: raise HTTPException(409, "Idempotency key was already used for another move.")
        return {"status":"replayed", "item_uuid":item_uuid, "location_key":destination_key}
    row=db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == current_user.id, InventoryCensusRecord.item_uuid == item_uuid).with_for_update()).scalar_one_or_none()
    destination=db.execute(select(InventoryLocation).where(InventoryLocation.user_id == current_user.id, InventoryLocation.location_key == destination_key)).scalar_one_or_none()
    if not row or row.status != "present": raise HTTPException(404, "Present inventory item not found.")
    if not destination or destination.accessibility in {"missing_unresolved", "shipped_removed"}: raise HTTPException(422, "Choose an active owned destination location.")
    if row.location_key == destination_key: raise HTTPException(409, "Item is already at this location.")
    duplicate=db.execute(select(InventoryCensusRecord).where(InventoryCensusRecord.user_id == current_user.id, InventoryCensusRecord.listing_id == row.listing_id, InventoryCensusRecord.location_key == destination_key, InventoryCensusRecord.status == "present")).scalar_one_or_none() if row.listing_id else None
    if duplicate and not allow_duplicate:
        db.add(InventoryEvent(user_id=current_user.id, operation_id=str(uuid4()), event_type="census_duplicate_warning", entity_type="census_item", entity_key=item_uuid, session_id=row.session_key, payload_json={"location_key":destination_key, "existing_item_uuid":duplicate.item_uuid, "operation":"move"}))
        db.commit()
        raise HTTPException(409, detail={"message":"A matching product is already at the destination. Confirm this is the scanned separate physical item to move it.", "existing_item_uuid":duplicate.item_uuid})
    source_stock=db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == row.inventory_product_id, InventoryStock.location_key == row.location_key).with_for_update()).scalar_one_or_none()
    if not source_stock or source_stock.on_hand - source_stock.reserved < 1: raise HTTPException(409, "No unreserved physical stock is available to move.")
    destination_stock=db.execute(select(InventoryStock).where(InventoryStock.user_id == current_user.id, InventoryStock.product_id == row.inventory_product_id, InventoryStock.location_key == destination_key).with_for_update()).scalar_one_or_none()
    if not destination_stock: destination_stock=InventoryStock(user_id=current_user.id, product_id=row.inventory_product_id, location_key=destination_key, on_hand=0); db.add(destination_stock)
    source_key=row.location_key; source_stock.on_hand -= 1; destination_stock.on_hand += 1; row.location_key=destination_key
    db.add(InventoryEvent(user_id=current_user.id, operation_id=operation_id, event_type="census_item_moved", entity_type="census_item", entity_key=item_uuid, session_id=row.session_key, payload_json={"request_fingerprint":fingerprint, "from_location_key":source_key, "to_location_key":destination_key, "listing_id":row.listing_id}))
    db.commit()
    return {"status":"moved", "item_uuid":item_uuid, "from_location_key":source_key, "location_key":destination_key}


@router.post("/print-jobs/{job_id}/browser-dispatch")
def mark_browser_label_dispatched(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job=db.get(InventoryPrintJob, job_id)
    if not job or job.user_id != current_user.id or job.job_type != "census_item_label": raise HTTPException(404, "Census print job not found.")
    if (current_user.settings_json or {}).get("inventory_print_mode", "browser") != "browser": raise HTTPException(409, "Browser dispatch is disabled by the selected Census print mode.")
    if job.status not in {"queued", "created", "failed", "unknown"}: return {"job_id":job.id, "status":job.status}
    job.status="dispatched"; job.claimed_at=datetime.utcnow(); db.commit()
    return {"job_id":job.id, "status":"dispatched", "physical_print_verified":False}
