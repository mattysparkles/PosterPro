"""Scanner command normalization shared by keyboard-wedge and camera clients.

The scanner hardware deliberately has no special server dependency: a HID/Bluetooth
reader submits the same completed string as a phone camera or the on-screen pad.
"""
from __future__ import annotations

import re

COMMANDS = {
    # The compact production command vocabulary is intentionally explicit so
    # printed command labels, HID scans, camera scans, and manual entry share
    # exactly the same behavior.  Older long-form labels remain available as
    # aliases rather than forcing operators to replace existing sheets.
    "NEW ITEM": "new_item", "NEXT ITEM": "next_item", "FINISH ITEM": "finish_item", "VOICE NOTE": "voice_note",
    "NEXT TOTE": "next_tote", "SELECT TOTE": "select_tote", "NEW TOTE": "new_tote",
    "NEW PACKAGE": "new_package", "FINISH PACKAGE": "finish_package",
    "PRINT LABEL": "print_label", "REPRINT LABEL": "reprint_label", "SET COPIES": "set_copies",
    "ADD QUANTITY": "add_quantity", "REMOVE QUANTITY": "remove_quantity", "UNDO": "undo",
    "SESSION STATUS": "session_status", "FIND ITEM": "find_item", "IDENTIFY": "identify",
    "MOVE ITEM": "move_item", "AUDIT TOTE": "audit_tote", "PACK INTO TOTE": "pack_into_tote",
    "STORE OUTSIDE TOTE": "store_outside_tote", "BUNDLE": "start_bundle", "START BUNDLE": "start_bundle",
    "ADD TO BUNDLE": "add_to_bundle", "FINISH BUNDLE": "finish_bundle",
    "CALIBRATE PHOTO TIME": "calibrate_photo_time", "FINISH SESSION": "finish_session",
}

def normalize_scan(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()

def dispatch_scan(value: object) -> dict:
    """Classify a completed scan without guessing at an unknown barcode."""
    raw = str(value or "").strip()
    normalized = normalize_scan(raw)
    if not normalized:
        return {"kind": "invalid", "message": "A scan value is required."}
    command = COMMANDS.get(normalized)
    if command:
        return {"kind": "command", "command": command, "display": normalized}
    # Command-sheet QR labels can use PosterPro's explicit namespace.
    if normalized.startswith("POSTERPRO:COMMAND:"):
        label = normalized.rsplit(":", 1)[-1].replace("_", " ")
        command = COMMANDS.get(label)
        if command:
            return {"kind": "command", "command": command, "display": label}
        return {"kind": "unsupported_command", "message": f"Unsupported command: {label}"}
    return {"kind": "barcode", "barcode": raw, "display": raw}
