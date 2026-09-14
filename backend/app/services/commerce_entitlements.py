from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.models.models import User


def _subscription(user: User | None) -> tuple[dict[str, Any], dict[str, Any]]:
    root = user.settings_json if user and isinstance(user.settings_json, dict) else {}
    subscription = root.get("subscription") if isinstance(root.get("subscription"), dict) else {}
    grants = subscription.get("entitlements") if isinstance(subscription.get("entitlements"), dict) else {}
    return subscription, grants


def commerce_entitlement(user: User | None, feature: str) -> dict[str, Any]:
    """Fail-closed tenant commerce entitlement; only server-stored active grants count."""
    subscription, grants = _subscription(user)
    plan = str(subscription.get("plan") or "FREE").upper()
    active = str(subscription.get("status") or "").lower() == "active"
    billing_ready = bool(getattr(settings, "commerce_billing_enabled", False))
    free_features = {"storefront.public_catalog", "storefront.external_purchase_links"}
    requested_grant = grants.get(feature)
    if requested_grant is None:
        requested_grant = feature in free_features if plan == "FREE" else False
    requires_paid_entitlement = feature not in free_features
    entitled = bool(requested_grant is True and (not requires_paid_entitlement or (active and billing_ready)))
    return {
        "feature": feature,
        "plan": plan,
        "subscription_active": active,
        "billing_ready": billing_ready,
        "entitled": entitled,
        "status": "AVAILABLE" if entitled else "COMING_SOON" if plan in {"BASIC", "PREMIUM"} else "LOCKED",
    }


def public_commerce_entitlements(user: User | None) -> dict[str, dict[str, Any]]:
    return {
        feature: commerce_entitlement(user, feature)
        for feature in (
            "storefront.public_catalog",
            "storefront.external_purchase_links",
            "storefront.direct_checkout",
            "payments.stripe",
            "payments.paypal",
            "payments.manual_methods",
            "payments.crypto",
            "affiliate.custom_ids",
            "analytics.commerce_advanced",
        )
    }
