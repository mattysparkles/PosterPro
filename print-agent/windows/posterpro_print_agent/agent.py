"""Explicitly launched Windows poller; outbound HTTPS only, no listener/service."""
from __future__ import annotations

import argparse
import base64
import json
import logging
import time
import threading
from pathlib import Path
from typing import Any

from .client import config_path, load_config, prompt_enrollment, request_json
from .core import PRINTER_NAME, load_journal, may_submit, save_journal, validate_job
from .windows_print import print_raster, queue_status


LOG = logging.getLogger("posterpro_print_agent")


def agent_paths() -> tuple[Path, Path]:
    root = config_path().parent
    return root / "agent.log", root / "job-journal.json"


def _state(config: dict[str, Any], job: dict[str, Any], lease: str, state: str, **extra: Any) -> dict[str, Any]:
    body = {"lease_token": lease, "state": state, **extra}
    return request_json(config["server_url"], f"/census/print-agent/jobs/{job['job_id']}/state", token=config["token"], payload=body)


def _renew_until_stopped(config: dict[str, Any], job: dict[str, Any], stop: threading.Event) -> None:
    while not stop.wait(60):
        try:
            request_json(config["server_url"], f"/census/print-agent/jobs/{job['job_id']}/lease", token=config["token"], payload={"lease_token": job["lease_token"]})
        except Exception:
            LOG.exception("Could not renew lease for job %s; output will not be resubmitted", job["job_id"])


def process_one(config: dict[str, Any], journal_path: Path, *, dry_run: bool = False, printer_adapter=print_raster) -> bool:
    if dry_run:
        raise ValueError("Dry-run is local-only and must not claim a production job.")
    response = request_json(config["server_url"], "/census/print-agent/jobs/claim", token=config["token"], payload={})
    job = response.get("job")
    if not job:
        return False
    png = validate_job(job)
    journal = load_journal(journal_path)
    job_id = job["job_id"]
    if not may_submit(job_id, journal):
        _state(config, job, job["lease_token"], "UNKNOWN", note="Local journal prevents duplicate printer submission.")
        return True
    journal[job_id] = {"state": "RENDERED", "item_uuid": job["payload"]["item_uuid"], "agent_id": config["agent_id"]}
    save_journal(journal_path, journal)
    # Persist the no-resubmit marker before the server handoff and before GDI.
    journal[job_id]["state"] = "SUBMITTING"
    save_journal(journal_path, journal)
    _state(config, job, job["lease_token"], "SUBMITTING")
    stop_lease = threading.Event()
    lease_thread = threading.Thread(target=_renew_until_stopped, args=(config, job, stop_lease), daemon=True)
    lease_thread.start()
    try:
        spooler_id = printer_adapter(PRINTER_NAME, png, f"PosterPro {job_id}", dry_run=False)
    except Exception as exc:
        journal[job_id]["state"] = "UNKNOWN"; journal[job_id]["error"] = str(exc)[:1000]; save_journal(journal_path, journal)
        try: _state(config, job, job["lease_token"], "UNKNOWN", error=str(exc)[:1000], note="Printer call failed; physical output may be uncertain. Do not auto-retry.")
        except Exception: LOG.exception("Could not report uncertain print result for job %s", job_id)
        return True
    finally:
        stop_lease.set(); lease_thread.join(timeout=2)
    journal[job_id]["state"] = "SPOOLER_ACCEPTED"; journal[job_id]["spooler_job_id"] = spooler_id; save_journal(journal_path, journal)
    _state(config, job, job["lease_token"], "SPOOLER_ACCEPTED", spooler_job_id=spooler_id, note="Windows GDI/driver accepted the document; physical paper output is not verified.")
    return True


def run_loop(*, dry_run: bool = False) -> None:
    log_path, journal_path = agent_paths()
    logging.basicConfig(filename=log_path, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()
    if config["printer_name"] != PRINTER_NAME:
        raise RuntimeError("Configured printer changed; stopping without fallback.")
    while True:
        try:
            status = {"status": "online", "capabilities": {"agent_version": "0.1.0", "dpi": 203, "media_mm": [58, 40], "outbound_https": True}}
            status.update(queue_status(PRINTER_NAME))
            request_json(config["server_url"], "/census/print-agent/heartbeat", token=config["token"], payload=status)
            if status["status"] not in {"online", "dry_run"}:
                LOG.warning("Configured printer state is %s; no print attempted", status["status"])
                time.sleep(10); continue
            profile = request_json(config["server_url"], "/census/print-agent/profile", token=config["token"])
            if not profile.get("enabled") or not profile.get("hardware_tested") or profile.get("print_mode") != "automatic_agent":
                time.sleep(10); continue
            # The separate dry-run command below never reaches this queue loop.
            if not process_one(config, journal_path, dry_run=False): time.sleep(5)
        except KeyboardInterrupt:
            LOG.info("Stopped by operator")
            return
        except Exception:
            LOG.exception("Agent iteration failed; no blind reprint attempted")
            time.sleep(10)


def main() -> None:
    parser = argparse.ArgumentParser(description="PosterPro outbound WT20 Shelf Census label agent")
    parser.add_argument("command", choices=["enroll", "run", "dry-run", "status"])
    args = parser.parse_args()
    if args.command == "enroll":
        print(json.dumps(prompt_enrollment(), indent=2)); return
    if args.command == "status":
        config = load_config(); print(json.dumps(queue_status(config["printer_name"]), indent=2)); return
    if args.command == "dry-run":
        dry_run_check()
        return
    run_loop(dry_run=False)


def dry_run_check() -> None:
    """Validate a local synthetic label without contacting or printing to hardware."""
    import io
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Dry run requires Pillow from requirements.txt.") from exc
    image = Image.new("1", (464, 320), 1)
    buffer = io.BytesIO(); image.save(buffer, format="PNG", dpi=(203, 203))
    png = buffer.getvalue()
    job = {"job_type": "census_item_label", "job_id": "local-dry-run", "printer_name": PRINTER_NAME,
        "paper": {"width_mm":58,"height_mm":40,"dpi_x":203,"dpi_y":203,"orientation":"portrait","scaling_percent":100},
        "pixel_width":464,"pixel_height":320,"payload":{"item_uuid":"dry-run-item","barcode_token":"ABCDEFGHIJKLMNOP"[:13]},
        "image_png_base64":base64.b64encode(png).decode("ascii")}
    validate_job(job)
    print("Dry run passed: structured 58x40 mm / 203 DPI payload accepted; no network request or print call was made.")


if __name__ == "__main__":
    main()
