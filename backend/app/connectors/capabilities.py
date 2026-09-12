from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class MarketplaceCapabilities:
    supports_direct_create: bool = False
    supports_direct_update: bool = False
    supports_direct_end: bool = False
    supports_extension_create: bool = False
    supports_extension_update: bool = False
    supports_extension_end: bool = False
    supports_sale_polling: bool = False
    supports_external_status: bool = False

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


DIRECT_EBAY = MarketplaceCapabilities(
    supports_direct_create=True,
    supports_direct_update=True,
    supports_sale_polling=True,
    supports_external_status=True,
)

ASSISTED_MARKETPLACE = MarketplaceCapabilities(
    supports_extension_create=True,
    # END opens the confirmed external listing and pauses for the operator to
    # end it in the marketplace UI; it never submits or deletes automatically.
    supports_extension_end=True,
)
