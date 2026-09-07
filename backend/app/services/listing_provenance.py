from __future__ import annotations

from typing import Any

_HUMAN_PROVENANCE_VALUES = {
    "human",
    "human_operator",
    "operator",
    "operator_override",
    "manual",
    "user",
}


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def recovery_field_provenance(source_metadata: dict[str, Any] | None) -> dict[str, Any]:
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    provenance = recovery.get("field_provenance") if isinstance(recovery.get("field_provenance"), dict) else {}
    return {str(key): value for key, value in provenance.items() if str(key).strip()}


def is_human_owned_field(source_metadata: dict[str, Any] | None, field: str) -> bool:
    source_metadata = source_metadata if isinstance(source_metadata, dict) else {}
    recovery = source_metadata.get("recovery") if isinstance(source_metadata.get("recovery"), dict) else {}
    locked_fields = {
        str(value).strip()
        for value in (recovery.get("operator_locked_fields") or [])
        if str(value).strip()
    }
    if field in locked_fields:
        return True
    provenance = recovery_field_provenance(source_metadata)
    return _normalize(provenance.get(field)) in _HUMAN_PROVENANCE_VALUES


def mark_field_provenance(
    source_metadata: dict[str, Any] | None,
    *,
    field: str,
    provenance: str,
    lock: bool = False,
) -> dict[str, Any]:
    source_metadata = dict(source_metadata or {})
    recovery = dict(source_metadata.get("recovery") or {})
    field_provenance = dict(recovery.get("field_provenance") or {})
    field_provenance[field] = provenance
    recovery["field_provenance"] = field_provenance
    if lock:
        locked_fields = list(dict.fromkeys(list(recovery.get("operator_locked_fields") or []) + [field]))
        recovery["operator_locked_fields"] = locked_fields
    source_metadata["recovery"] = recovery
    return source_metadata


def mark_manual_field_provenance(source_metadata: dict[str, Any] | None, fields: list[str]) -> dict[str, Any]:
    updated = dict(source_metadata or {})
    for field in fields:
        updated = mark_field_provenance(updated, field=field, provenance="human_operator", lock=True)
    return updated

