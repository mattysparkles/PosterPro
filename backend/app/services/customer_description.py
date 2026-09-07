from __future__ import annotations

import re

FORBIDDEN_DESCRIPTION_PATTERNS = (
    r"please review",
    r"confirm (?:the )?(?:attached )?product images",
    r"confirm before publishing",
    r"verify from (?:the )?photographs?",
    r"category guidance:",
    r"read full return policy",
    r"return policy",
    r"product support included",
    r"research notes?:",
    r"internal note:",
    r"ai confidence:",
    r"needs review",
    r"marketplace guidance:",
)

def sanitize_customer_description(value: str | None) -> tuple[str, list[str]]:
    text = str(value or "").strip()
    removed: list[str] = []
    kept: list[str] = []
    for line in text.splitlines():
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in FORBIDDEN_DESCRIPTION_PATTERNS):
            removed.append(line.strip())
        else:
            kept.append(line.strip())
    return "\n".join(line for line in kept if line).strip(), removed

def customer_description_is_safe(value: str | None) -> bool:
    _, removed = sanitize_customer_description(value)
    return not removed
