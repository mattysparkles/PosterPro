import base64
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1]))

from posterpro_print_agent.core import PRINTER_NAME, load_journal, may_submit, save_journal, validate_job
from posterpro_print_agent.agent import dry_run_check, process_one


def sample_job():
    image = Image.new("1", (464, 320), 1)
    data = io.BytesIO(); image.save(data, format="PNG", dpi=(203, 203))
    return {"job_type":"census_item_label", "job_id":"job-test-1", "printer_name":PRINTER_NAME,
        "paper":{"width_mm":58,"height_mm":40,"dpi_x":203,"dpi_y":203,"orientation":"portrait","scaling_percent":100},
        "pixel_width":464,"pixel_height":320,"payload":{"item_uuid":"uuid-1","barcode_token":"ABCDEFGHIJKLMNOP"[:13]},
        "image_png_base64":base64.b64encode(data.getvalue()).decode("ascii")}


def test_structured_native_size_job_validates_and_rejects_wrong_printer_and_payload():
    assert validate_job(sample_job()).startswith(b"\x89PNG")
    job = sample_job(); job["printer_name"] = "Other Printer"
    with pytest.raises(ValueError, match="not the configured"):
        validate_job(job)
    job = sample_job(); job["pixel_width"] = 686
    with pytest.raises(ValueError, match="464x320"):
        validate_job(job)
    job = sample_job(); job["image_png_base64"] = base64.b64encode(b"not png").decode()
    with pytest.raises(ValueError, match="not a PNG"):
        validate_job(job)


def test_local_journal_is_atomic_and_prevents_duplicate_after_uncertain_submission(tmp_path):
    path = tmp_path / "agent" / "journal.json"
    journal = {"job-test-1":{"state":"SUBMITTING","lease":"opaque"}}
    save_journal(path, journal)
    assert load_journal(path) == journal
    assert not may_submit("job-test-1", load_journal(path))
    assert may_submit("new-job", load_journal(path))
    assert not list(path.parent.glob("*.tmp"))


def test_dry_run_validates_only_synthetic_payload_without_claiming_jobs(monkeypatch, tmp_path):
    # The local dry-run path is offline and cannot issue a claim or call a printer.
    import posterpro_print_agent.agent as module
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run must not claim or print")
    monkeypatch.setattr(module, "request_json", forbidden)
    monkeypatch.setattr(module, "print_raster", forbidden)
    dry_run_check()
    with pytest.raises(ValueError, match="local-only"):
        process_one({"server_url":"https://example.invalid"}, tmp_path / "journal", dry_run=True)
