"""Backward-compatible import shim for legacy marketplace publisher references."""

from app.services.multi_platform_publisher import (  # noqa: F401
    PublishResult,
    get_enabled_platforms,
    multi_platform_publisher,
    upsert_marketplace_listing,
)
