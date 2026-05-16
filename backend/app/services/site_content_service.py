from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

_DATA_PATH = Path(__file__).resolve().parents[2] / "runtime" / "site_content.json"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_theme() -> dict[str, Any]:
    app_name = settings.app_name or "PosterPro"
    return {
        "id": "corporate-sky",
        "name": "Corporate Sky",
        "description": f"Clean default SaaS theme for {app_name} hosted pages.",
        "hero_eyebrow": "Trusted operator workspace",
        "hero_title": f"{app_name} hosted pages",
        "hero_body": "A clean customer-facing shell for policies, OAuth handoff, and future CMS pages.",
        "layout": {
            "align": "center",
            "content_width": "860px",
            "hero_style": "split-band",
            "card_style": "elevated",
            "show_brand_badge": True,
        },
        "palette": {
            "page_background": "linear-gradient(180deg, #f3f7ff 0%, #ffffff 52%, #eef4ff 100%)",
            "hero_background": "linear-gradient(135deg, #0f172a 0%, #1d4ed8 52%, #38bdf8 100%)",
            "hero_foreground": "#f8fbff",
            "surface_background": "rgba(255, 255, 255, 0.96)",
            "surface_foreground": "#101828",
            "surface_muted": "#475467",
            "border_color": "#d7e3f4",
            "accent_color": "#2563eb",
            "accent_soft": "#dbeafe",
            "success_color": "#166534",
            "warning_color": "#b54708",
            "danger_color": "#b42318",
        },
        "typography": {
            "font_family": "'Plus Jakarta Sans', 'Segoe UI', sans-serif",
            "heading_family": "'Plus Jakarta Sans', 'Segoe UI', sans-serif",
            "base_size": "15px",
        },
        "chrome": {
            "footer_note": f"{app_name} hosted pages can be white-labeled for self-hosted marketplace operations.",
            "primary_cta_label": "Return to PosterPro",
            "secondary_cta_label": "Close window",
        },
    }


def _button(label: str = "", href: str = "") -> dict[str, str]:
    return {"label": label, "href": href}


def _rich_text_block(html: str) -> dict[str, Any]:
    return {"type": "rich_text", "html": html}


def _steps_block(items: list[str]) -> dict[str, Any]:
    return {"type": "steps", "items": items}


def _features_block(items: list[dict[str, str]]) -> dict[str, Any]:
    return {"type": "feature_list", "items": items}


def _page_snapshot(
    *,
    title: str,
    summary: str,
    hero_eyebrow: str,
    hero_title: str,
    hero_body: str,
    blocks: list[dict[str, Any]],
    primary_button: dict[str, str] | None = None,
    secondary_button: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "summary": summary,
        "hero": {
            "eyebrow": hero_eyebrow,
            "title": hero_title,
            "body": hero_body,
        },
        "primary_button": primary_button or _button(),
        "secondary_button": secondary_button or _button(),
        "blocks": blocks,
    }


def _default_page_entry(
    *,
    kind: str,
    slug: str,
    route_group: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    now = _utcnow_iso()
    return {
        "kind": kind,
        "slug": slug,
        "route_group": route_group,
        "status": "published",
        "updated_at": now,
        "published_at": now,
        "draft": deepcopy(snapshot),
        "published": deepcopy(snapshot),
    }


def _default_site_content() -> dict[str, Any]:
    app_name = settings.app_name or "PosterPro"
    return {
        "brand_name": app_name,
        "active_theme_id": "corporate-sky",
        "themes": [_default_theme()],
        "pages": {
            "privacy_policy": _default_page_entry(
                kind="privacy_policy",
                slug="privacy-policy",
                route_group="legal",
                snapshot=_page_snapshot(
                    title=f"{app_name} Privacy Policy",
                    summary="A clear policy page covering marketplace data use, operator account handling, and support expectations.",
                    hero_eyebrow="Policy + compliance",
                    hero_title="Privacy policy for marketplace operations",
                    hero_body="Explain how the deployment handles operator accounts, marketplace data, listing content, and support workflows.",
                    blocks=[
                        _rich_text_block(
                            f"<p>{app_name} stores the data required to help operators prepare, review, publish, and maintain marketplace listings. That can include listing text, images, workflow activity, marketplace account metadata, and authenticated tokens where a connector requires them.</p>"
                            "<p>The self-hosting operator is responsible for tailoring this policy so it matches the deployment, the connected marketplaces, and the team workflows that actually run in production.</p>"
                            "<p>Use this page as the public source of truth for what data is collected, why it is processed, and who controls that data in your environment.</p>"
                        ),
                        _features_block(
                            [
                                {"title": "Data used for listings", "body": "Document the listing content, media, pricing, and inventory metadata your team stores in PosterPro."},
                                {"title": "Connected account data", "body": "Explain how OAuth tokens, session payloads, and marketplace account identifiers are retained and refreshed."},
                                {"title": "Operator responsibility", "body": "State clearly that the self-hosting team owns final policy language, retention rules, and customer-facing notices."},
                            ]
                        ),
                    ],
                    primary_button=_button("Open PosterPro", "/"),
                    secondary_button=_button("Read trust center", "/site/trust-center"),
                ),
            ),
            "trust_center": _default_page_entry(
                kind="trust_center",
                slug="trust-center",
                route_group="site",
                snapshot=_page_snapshot(
                    title=f"{app_name} Trust Center",
                    summary="A public overview of account-connect posture, workflow controls, and operational readiness.",
                    hero_eyebrow="Security + operations",
                    hero_title="A clean trust center for marketplace teams",
                    hero_body="Show how operators connect accounts, how listings move through review, and what support standards the deployment follows.",
                    blocks=[
                        _features_block(
                            [
                                {"title": "Centralized workflow", "body": "Listings, approval queues, marketplace connections, and automation settings are managed from one operator workspace."},
                                {"title": "Visible account handoffs", "body": "OAuth and browser-assisted connection flows are exposed through explicit status pages instead of generic redirects."},
                                {"title": "Admin-owned controls", "body": "Themes, hosted pages, and channel rules can be managed inside the same backend without a second CMS stack."},
                            ]
                        ),
                        _rich_text_block(
                            "<p>Use this page to describe security posture, escalation paths, support hours, workflow review checkpoints, and what a marketplace operator should expect during setup.</p>"
                            "<p>For self-hosted installations, this page is also the right place to explain who administers the deployment and how account access is provisioned or removed.</p>"
                        ),
                        {
                            "type": "cta",
                            "title": "Need a guided setup path?",
                            "body": "Pair the trust center with a simple onboarding page so account owners can move from confidence to action without leaving the hosted CMS flow.",
                            "button": _button("Open onboarding", "/site/operator-onboarding"),
                        },
                    ],
                    primary_button=_button("View onboarding", "/site/operator-onboarding"),
                    secondary_button=_button("Open PosterPro", "/"),
                ),
            ),
            "operator_onboarding": _default_page_entry(
                kind="operator_onboarding",
                slug="operator-onboarding",
                route_group="site",
                snapshot=_page_snapshot(
                    title=f"Get Started With {app_name}",
                    summary="A guided onboarding page that explains the account setup flow before an operator signs in.",
                    hero_eyebrow="Operator onboarding",
                    hero_title="Start with a predictable setup workflow",
                    hero_body="Walk a new operator through sign-in, marketplace connection, and the first review steps before they enter the live workspace.",
                    blocks=[
                        _steps_block(
                            [
                                "Sign in with the operator account or invite issued by your team.",
                                "Connect the required marketplace accounts or complete any browser-assisted login steps.",
                                "Review the draft workflow, pricing posture, and publishing approvals before going live.",
                            ]
                        ),
                        _features_block(
                            [
                                {"title": "Preflight before publish", "body": "Explain what needs to be configured before drafts can move into queueing or live publication."},
                                {"title": "Clear role boundaries", "body": "Tell operators which actions are automated, which remain manual, and where human approval is still required."},
                                {"title": "Reusable onboarding shell", "body": "This page can be tailored for customers, internal operators, or partner teams without spinning up a separate site."},
                            ]
                        ),
                    ],
                    primary_button=_button("Open PosterPro", "/login"),
                    secondary_button=_button("Read trust center", "/site/trust-center"),
                ),
            ),
            "ebay_auth_accepted": _default_page_entry(
                kind="ebay_auth_accepted",
                slug="ebay-auth-complete",
                route_group="connect",
                snapshot=_page_snapshot(
                    title="eBay Connection Complete",
                    summary="Success page shown when eBay returns the operator to PosterPro after approval.",
                    hero_eyebrow="Account connection",
                    hero_title="Authorization approved",
                    hero_body="PosterPro is finishing the eBay account connection and syncing the operator workspace.",
                    blocks=[
                        _steps_block(
                            [
                                "PosterPro exchanges the authorization response for account tokens.",
                                "The operator workspace refreshes the connection state and stores the linked seller account details.",
                                "If the connection does not appear, return to Settings and retry the OAuth flow from the eBay panel.",
                            ]
                        )
                    ],
                    primary_button=_button("Return to settings", "/settings?tab=ebay"),
                    secondary_button=_button("Open PosterPro", "/"),
                ),
            ),
            "ebay_auth_declined": _default_page_entry(
                kind="ebay_auth_declined",
                slug="ebay-auth-declined",
                route_group="connect",
                snapshot=_page_snapshot(
                    title="eBay Access Declined",
                    summary="Fallback page shown when the operator cancels or declines eBay authorization.",
                    hero_eyebrow="Account connection",
                    hero_title="Authorization was canceled",
                    hero_body="Return to PosterPro when you are ready to restart the eBay connection workflow.",
                    blocks=[
                        _rich_text_block(
                            "<p>The authorization request ended before PosterPro could finish connecting the eBay account.</p>"
                            "<p>Return to Settings to start the connection again after confirming you are signed in to the correct seller account.</p>"
                        ),
                        {
                            "type": "cta",
                            "title": "Ready to try again?",
                            "body": "Restart the connection flow from the eBay settings panel once the operator is back in the correct account context.",
                            "button": _button("Go to settings", "/settings?tab=ebay"),
                        },
                    ],
                    primary_button=_button("Return to settings", "/settings?tab=ebay"),
                    secondary_button=_button("Open trust center", "/site/trust-center"),
                ),
            ),
        },
    }


def _normalize_theme(theme: dict[str, Any], default_theme: dict[str, Any]) -> dict[str, Any]:
    theme_id = str(theme.get("id") or default_theme["id"]).strip().lower().replace(" ", "-")
    palette = theme.get("palette") if isinstance(theme.get("palette"), dict) else {}
    layout = theme.get("layout") if isinstance(theme.get("layout"), dict) else {}
    typography = theme.get("typography") if isinstance(theme.get("typography"), dict) else {}
    chrome = theme.get("chrome") if isinstance(theme.get("chrome"), dict) else {}
    return {
        "id": theme_id or default_theme["id"],
        "name": str(theme.get("name") or default_theme["name"]).strip() or default_theme["name"],
        "description": str(theme.get("description") or default_theme["description"]).strip() or default_theme["description"],
        "hero_eyebrow": str(theme.get("hero_eyebrow") or default_theme["hero_eyebrow"]).strip() or default_theme["hero_eyebrow"],
        "hero_title": str(theme.get("hero_title") or default_theme["hero_title"]).strip() or default_theme["hero_title"],
        "hero_body": str(theme.get("hero_body") or default_theme["hero_body"]).strip() or default_theme["hero_body"],
        "layout": {
            "align": str(layout.get("align") or default_theme["layout"]["align"]).strip() or default_theme["layout"]["align"],
            "content_width": str(layout.get("content_width") or default_theme["layout"]["content_width"]).strip() or default_theme["layout"]["content_width"],
            "hero_style": str(layout.get("hero_style") or default_theme["layout"]["hero_style"]).strip() or default_theme["layout"]["hero_style"],
            "card_style": str(layout.get("card_style") or default_theme["layout"]["card_style"]).strip() or default_theme["layout"]["card_style"],
            "show_brand_badge": bool(layout.get("show_brand_badge", default_theme["layout"]["show_brand_badge"])),
        },
        "palette": {key: str(palette.get(key) or value).strip() or value for key, value in default_theme["palette"].items()},
        "typography": {key: str(typography.get(key) or value).strip() or value for key, value in default_theme["typography"].items()},
        "chrome": {key: str(chrome.get(key) or value).strip() or value for key, value in default_theme["chrome"].items()},
    }


def _normalize_button(payload: Any) -> dict[str, str]:
    payload = payload if isinstance(payload, dict) else {}
    return {
        "label": str(payload.get("label") or "").strip(),
        "href": str(payload.get("href") or "").strip(),
    }


def _normalize_blocks(blocks: Any, fallback_html: str = "") -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "rich_text").strip().lower()
            if block_type == "rich_text":
                html = str(block.get("html") or "").strip()
                if html:
                    normalized.append({"type": "rich_text", "html": html})
            elif block_type in {"feature_list", "steps"}:
                items = block.get("items")
                if isinstance(items, list) and items:
                    normalized.append({"type": block_type, "items": items})
            elif block_type == "cta":
                normalized.append(
                    {
                        "type": "cta",
                        "title": str(block.get("title") or "").strip(),
                        "body": str(block.get("body") or "").strip(),
                        "button": _normalize_button(block.get("button")),
                    }
                )
    if normalized:
        return normalized
    return [_rich_text_block(fallback_html)] if fallback_html.strip() else []


def _normalize_snapshot(payload: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    hero_payload = payload.get("hero") if isinstance(payload.get("hero"), dict) else {}
    return {
        "title": str(payload.get("title") or fallback["title"]).strip() or fallback["title"],
        "summary": str(payload.get("summary") or fallback.get("summary") or "").strip() or fallback.get("summary", ""),
        "hero": {
            "eyebrow": str(hero_payload.get("eyebrow") or fallback["hero"]["eyebrow"]).strip() or fallback["hero"]["eyebrow"],
            "title": str(hero_payload.get("title") or fallback["hero"]["title"]).strip() or fallback["hero"]["title"],
            "body": str(hero_payload.get("body") or fallback["hero"]["body"]).strip() or fallback["hero"]["body"],
        },
        "primary_button": _normalize_button(payload.get("primary_button") or fallback.get("primary_button")),
        "secondary_button": _normalize_button(payload.get("secondary_button") or fallback.get("secondary_button")),
        "blocks": _normalize_blocks(payload.get("blocks"), fallback_html=""),
    }


def _legacy_snapshot_from_page(page_payload: dict[str, Any], default_page: dict[str, Any]) -> dict[str, Any]:
    html = str(page_payload.get("html") or default_page["published"]["blocks"][0].get("html") or "").strip()
    title = str(page_payload.get("title") or default_page["draft"]["title"]).strip() or default_page["draft"]["title"]
    summary = str(page_payload.get("summary") or default_page["draft"]["summary"]).strip() or default_page["draft"]["summary"]
    return _page_snapshot(
        title=title,
        summary=summary,
        hero_eyebrow=default_page["draft"]["hero"]["eyebrow"],
        hero_title=title,
        hero_body=summary,
        blocks=_normalize_blocks(page_payload.get("blocks"), fallback_html=html),
        primary_button=default_page["draft"]["primary_button"],
        secondary_button=default_page["draft"]["secondary_button"],
    )


def _normalize_page(kind: str, payload: Any, default_page: dict[str, Any]) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    slug = str(payload.get("slug") or default_page["slug"]).strip().strip("/") or default_page["slug"]
    route_group = str(payload.get("route_group") or default_page["route_group"]).strip() or default_page["route_group"]
    legacy_snapshot = _legacy_snapshot_from_page(payload, default_page)
    fallback_draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else legacy_snapshot
    fallback_published = payload.get("published") if isinstance(payload.get("published"), dict) else fallback_draft
    draft = _normalize_snapshot(fallback_draft, default_page["draft"])
    published = _normalize_snapshot(fallback_published, draft)
    status = str(payload.get("status") or default_page.get("status") or "published").strip().lower()
    if status not in {"draft", "published"}:
        status = "published"
    return {
        "kind": kind,
        "slug": slug,
        "route_group": route_group,
        "status": status,
        "updated_at": str(payload.get("updated_at") or default_page.get("updated_at") or _utcnow_iso()),
        "published_at": str(payload.get("published_at") or default_page.get("published_at") or _utcnow_iso()),
        "draft": draft,
        "published": published,
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
    raw_themes = raw.get("themes") if isinstance(raw.get("themes"), list) else []
    merged["themes"] = [_normalize_theme(theme, base["themes"][0]) for theme in raw_themes if isinstance(theme, dict)] or [deepcopy(base["themes"][0])]
    theme_ids = {theme["id"] for theme in merged["themes"]}
    requested_active = str(raw.get("active_theme_id") or base["active_theme_id"]).strip()
    merged["active_theme_id"] = requested_active if requested_active in theme_ids else merged["themes"][0]["id"]
    raw_pages = raw.get("pages") if isinstance(raw.get("pages"), dict) else {}
    for kind, default_page in base["pages"].items():
        merged["pages"][kind] = _normalize_page(kind, raw_pages.get(kind), default_page)
    return merged


def save_site_content(payload: dict[str, Any]) -> dict[str, Any]:
    base = _default_site_content()
    current = load_site_content()
    raw_pages = payload.get("pages") if isinstance(payload.get("pages"), dict) else {}
    raw_themes = payload.get("themes") if isinstance(payload.get("themes"), list) else current["themes"]

    next_themes = [_normalize_theme(theme, base["themes"][0]) for theme in raw_themes if isinstance(theme, dict)] or [deepcopy(base["themes"][0])]
    theme_ids = {theme["id"] for theme in next_themes}
    requested_active = str(payload.get("active_theme_id") or current.get("active_theme_id") or next_themes[0]["id"]).strip()

    next_payload = {
        "brand_name": str(payload.get("brand_name") or current["brand_name"]).strip() or base["brand_name"],
        "active_theme_id": requested_active if requested_active in theme_ids else next_themes[0]["id"],
        "themes": next_themes,
        "pages": {},
    }
    for kind, default_page in base["pages"].items():
        page_payload = raw_pages.get(kind) if isinstance(raw_pages.get(kind), dict) else current["pages"].get(kind, {})
        next_payload["pages"][kind] = _normalize_page(kind, page_payload, default_page)

    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DATA_PATH.write_text(json.dumps(next_payload, indent=2) + "\n", encoding="utf-8")
    return load_site_content()


def save_draft_pages(*, brand_name: str | None, active_theme_id: str | None, pages: dict[str, Any]) -> dict[str, Any]:
    current = load_site_content()
    next_payload = {
        "brand_name": brand_name if brand_name is not None else current["brand_name"],
        "active_theme_id": active_theme_id if active_theme_id is not None else current["active_theme_id"],
        "themes": current["themes"],
        "pages": {},
    }
    now = _utcnow_iso()
    for kind, current_page in current["pages"].items():
        incoming = pages.get(kind) if isinstance(pages.get(kind), dict) else {}
        next_payload["pages"][kind] = {
            **current_page,
            "slug": str(incoming.get("slug") or current_page["slug"]).strip().strip("/") or current_page["slug"],
            "route_group": str(incoming.get("route_group") or current_page["route_group"]).strip() or current_page["route_group"],
            "status": "draft",
            "updated_at": now,
            "published_at": current_page.get("published_at"),
            "draft": _normalize_snapshot(incoming.get("draft"), current_page["draft"]),
            "published": current_page["published"],
        }
    return save_site_content(next_payload)


def publish_draft_pages(page_keys: list[str] | None = None) -> dict[str, Any]:
    current = load_site_content()
    selected = {str(key).strip() for key in (page_keys or []) if str(key).strip()}
    publish_all = not selected
    now = _utcnow_iso()
    next_payload = {
        "brand_name": current["brand_name"],
        "active_theme_id": current["active_theme_id"],
        "themes": current["themes"],
        "pages": {},
    }
    for kind, page in current["pages"].items():
        should_publish = publish_all or kind in selected
        if should_publish:
            next_payload["pages"][kind] = {
                **page,
                "status": "published",
                "updated_at": now,
                "published_at": now,
                "draft": page["draft"],
                "published": deepcopy(page["draft"]),
            }
        else:
            next_payload["pages"][kind] = page
    return save_site_content(next_payload)


def import_theme_pack(raw_text: str, *, replace_existing: bool = False, activate_imported: bool = False) -> dict[str, Any]:
    parsed = json.loads(raw_text)
    if not isinstance(parsed, dict):
        raise ValueError("Theme pack must be a JSON object.")
    if not isinstance(parsed.get("themes"), list) or not parsed["themes"]:
        raise ValueError("Theme pack must contain a non-empty themes array.")

    current = load_site_content()
    current_themes = [] if replace_existing else deepcopy(current["themes"])
    seen = {theme["id"] for theme in current_themes}
    normalized_imports: list[dict[str, Any]] = []
    for theme in parsed["themes"]:
        if not isinstance(theme, dict):
            continue
        normalized = _normalize_theme(theme, _default_theme())
        if normalized["id"] in seen:
            current_themes = [item for item in current_themes if item["id"] != normalized["id"]]
        seen.add(normalized["id"])
        normalized_imports.append(normalized)

    merged_themes = current_themes + normalized_imports
    activate_id = str(parsed.get("activate_theme_id") or "").strip()
    if activate_imported and normalized_imports:
        activate_id = activate_id or normalized_imports[0]["id"]

    return save_site_content(
        {
            **current,
            "themes": merged_themes,
            "active_theme_id": activate_id or current.get("active_theme_id"),
        }
    )


def get_active_theme(content: dict[str, Any] | None = None) -> dict[str, Any]:
    current = content or load_site_content()
    active_id = current.get("active_theme_id")
    for theme in current.get("themes", []):
        if theme.get("id") == active_id:
            return theme
    return deepcopy(_default_theme())


def get_public_page_by_slug(slug: str) -> dict[str, Any] | None:
    normalized = str(slug or "").strip().strip("/")
    if not normalized:
        return None
    content = load_site_content()
    theme = get_active_theme(content)
    for page in content["pages"].values():
        if page["slug"] == normalized:
            snapshot = page["published"]
            return {
                "brand_name": content["brand_name"],
                "active_theme_id": content["active_theme_id"],
                "route_group": page["route_group"],
                "theme": theme,
                "kind": page["kind"],
                "slug": page["slug"],
                "status": page["status"],
                "published_at": page.get("published_at"),
                **snapshot,
            }
    return None


def build_public_page_urls(base_url: str | None = None) -> dict[str, str]:
    content = load_site_content()
    root = (base_url or settings.app_base_url or "").strip().rstrip("/")
    pages = content["pages"]
    if not root:
        return {
            "privacy_policy_url": "",
            "trust_center_url": "",
            "operator_onboarding_url": "",
            "auth_accepted_url": "",
            "auth_declined_url": "",
        }
    return {
        "privacy_policy_url": f"{root}/legal/{pages['privacy_policy']['slug']}",
        "trust_center_url": f"{root}/site/{pages['trust_center']['slug']}",
        "operator_onboarding_url": f"{root}/site/{pages['operator_onboarding']['slug']}",
        "auth_accepted_url": f"{root}/connect/{pages['ebay_auth_accepted']['slug']}",
        "auth_declined_url": f"{root}/connect/{pages['ebay_auth_declined']['slug']}",
    }
