from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.core.config import settings

_DATA_PATH = Path(__file__).resolve().parents[2] / "runtime" / "site_content.json"


def _default_site_content() -> dict[str, Any]:
    app_name = settings.app_name or "PosterPro"
    return {
        "brand_name": app_name,
        "pages": {
            "privacy_policy": {
                "kind": "privacy_policy",
                "slug": "privacy-policy",
                "title": f"{app_name} Privacy Policy",
                "html": (
                    f"<p>{app_name} uses marketplace and operator-provided data to help generate, review, publish, "
                    "and manage resale listings.</p>"
                    "<p>When an operator connects a marketplace account such as eBay, the application may store "
                    "account tokens, listing metadata, and workflow activity required to publish listings, sync "
                    "status, and support inventory or sales operations.</p>"
                    "<p>The self-hosting operator is responsible for reviewing this policy, customizing it for "
                    "their business, and ensuring it matches how their deployment actually handles personal data, "
                    "marketplace data, and customer communications.</p>"
                ),
            },
            "ebay_auth_accepted": {
                "kind": "ebay_auth_accepted",
                "slug": "ebay-auth-complete",
                "title": "eBay Connection Complete",
                "html": (
                    "<p>PosterPro is finalizing the eBay connection for this account.</p>"
                    "<p>If the window does not close automatically, return to Settings and confirm the connection "
                    "status refreshed.</p>"
                ),
            },
            "ebay_auth_declined": {
                "kind": "ebay_auth_declined",
                "slug": "ebay-auth-declined",
                "title": "eBay Access Declined",
                "html": (
                    "<p>The eBay authorization request was declined or cancelled before PosterPro could complete "
                    "the account connection.</p>"
                    "<p>Return to Settings and start the eBay connection again when you are ready.</p>"
                ),
            },
        },
    }


def _normalize_page(kind: str, payload: dict[str, Any], default_page: dict[str, Any]) -> dict[str, Any]:
    slug = str(payload.get("slug") or default_page["slug"]).strip().strip("/")
    title = str(payload.get("title") or default_page["title"]).strip() or default_page["title"]
    html = str(payload.get("html") or default_page["html"]).strip() or default_page["html"]
    return {
        "kind": kind,
        "slug": slug,
        "title": title,
        "html": html,
    }


def load_site_content() -> dict[str, Any]:
    base = _default_site_content()
    if not _DATA_PATH.exists():
        return base
    try:
        raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return base

    merged = deepcopy(base)
    merged["brand_name"] = str(raw.get("brand_name") or base["brand_name"]).strip() or base["brand_name"]
    raw_pages = raw.get("pages") if isinstance(raw.get("pages"), dict) else {}
    for kind, default_page in base["pages"].items():
        payload = raw_pages.get(kind) if isinstance(raw_pages.get(kind), dict) else {}
        merged["pages"][kind] = _normalize_page(kind, payload, default_page)
    return merged


def save_site_content(payload: dict[str, Any]) -> dict[str, Any]:
    base = _default_site_content()
    current = load_site_content()
    next_payload = {
        "brand_name": str(payload.get("brand_name") or current["brand_name"]).strip() or base["brand_name"],
        "pages": {},
    }
    raw_pages = payload.get("pages") if isinstance(payload.get("pages"), dict) else {}
    for kind, default_page in base["pages"].items():
        page_payload = raw_pages.get(kind) if isinstance(raw_pages.get(kind), dict) else current["pages"].get(kind, {})
        next_payload["pages"][kind] = _normalize_page(kind, page_payload, default_page)

    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DATA_PATH.write_text(json.dumps(next_payload, indent=2) + "\n", encoding="utf-8")
    return load_site_content()


def get_public_page_by_slug(slug: str) -> dict[str, Any] | None:
    normalized = str(slug or "").strip().strip("/")
    if not normalized:
        return None
    content = load_site_content()
    for page in content["pages"].values():
        if page["slug"] == normalized:
            return {
                "brand_name": content["brand_name"],
                **page,
            }
    return None


def build_public_page_urls(base_url: str | None = None) -> dict[str, str]:
    content = load_site_content()
    root = (base_url or settings.app_base_url or "").strip().rstrip("/")
    pages = content["pages"]
    if not root:
        return {
            "privacy_policy_url": "",
            "auth_accepted_url": "",
            "auth_declined_url": "",
        }
    return {
        "privacy_policy_url": f"{root}/legal/{pages['privacy_policy']['slug']}",
        "auth_accepted_url": f"{root}/connect/{pages['ebay_auth_accepted']['slug']}",
        "auth_declined_url": f"{root}/connect/{pages['ebay_auth_declined']['slug']}",
    }

