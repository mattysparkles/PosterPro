from __future__ import annotations

from app.models.models import Listing
from app.services.ebay_fee_service import calculate_expected_ebay_fee


class ProfitService:
    MARKETPLACE_FEE_RULES = {
        "ebay": {"final_value_pct": 0.1325, "fixed": 0.30},
        "mercari": {"final_value_pct": 0.10, "fixed": 0.0},
        "facebook": {"final_value_pct": 0.05, "fixed": 0.0},
    }

    def estimate_fees_by_marketplace(self, listing: Listing, marketplace: str = "ebay") -> float:
        if marketplace.lower() == "ebay":
            return float(calculate_expected_ebay_fee(listing).get("totalExpectedFee") or 0.0)
        sale_price = listing.sale_price or listing.listing_price or listing.suggested_price or 0.0
        rule = self.MARKETPLACE_FEE_RULES.get(marketplace.lower(), {"final_value_pct": 0.12, "fixed": 0.30})
        return round((sale_price * rule["final_value_pct"]) + rule["fixed"], 2)

    def estimate_expected_ebay_fee(self, listing: Listing, seller_config: dict | None = None) -> dict:
        """Return the shared, category-aware eBay sold-fee estimate."""
        return calculate_expected_ebay_fee(listing, seller_config)

    def estimate_shipping_cost(self, listing: Listing) -> float:
        return self.estimate_shipping_quote(listing)["mid"]

    def estimate_shipping_quote(self, listing: Listing) -> dict:
        """Estimate the operator's carrier cost, independent of buyer postage.

        ``free_shipping`` describes what the buyer pays, not what PosterPro
        spends.  When no live carrier quote is configured, use the selected
        service, packaged weight, and dimensional weight to produce a bounded
        domestic estimate.  The estimate is deliberately labeled; an actual
        shipment cost can replace it later.
        """
        data = listing.marketplace_data if isinstance(listing.marketplace_data, dict) else {}
        shipping = listing.shipping_profile if isinstance(listing.shipping_profile, dict) else {}
        service = str(
            shipping.get("shipping_service_code")
            or shipping.get("shipping_class_suggestion")
            or (data.get("shipping") or {}).get("domestic_service")
            or "usps_ground_advantage"
        ).lower()
        try:
            weight = float(shipping.get("package_weight") or shipping.get("item_weight") or 1.0)
        except (TypeError, ValueError):
            weight = 1.0
        dims = shipping.get("package_dimensions") if isinstance(shipping.get("package_dimensions"), dict) else {}
        try:
            dimensional = (float(dims.get("length") or 0) * float(dims.get("width") or 0) * float(dims.get("height") or 0)) / 139.0
        except (TypeError, ValueError):
            dimensional = 0.0
        billable_weight = max(weight, dimensional, 0.25)
        if "dhl" in service:
            base = 10.50 + max(0.0, billable_weight - 1.0) * 1.50
        elif "ups" in service:
            base = 9.50 + max(0.0, billable_weight - 1.0) * 1.40
        elif "fedex" in service:
            base = 9.75 + max(0.0, billable_weight - 1.0) * 1.45
        elif "ontrac" in service or "lasership" in service:
            base = 8.75 + max(0.0, billable_weight - 1.0) * 1.30
        elif "canada post" in service or "canadapost" in service:
            base = 10.25 + max(0.0, billable_weight - 1.0) * 1.45
        elif "priority" in service:
            base = 9.85 + max(0.0, billable_weight - 1.0) * 1.55
        elif "ground advantage" in service or "usps" in service or "ground" in service:
            base = 6.25 + max(0.0, billable_weight - 1.0) * 1.15
        else:
            # Unknown configured carrier/service: retain a conservative
            # domestic parcel estimate rather than reporting zero.
            base = 7.50 + max(0.0, billable_weight - 1.0) * 1.25
        # Residential zone and fuel variation; keep the range useful without
        # pretending a carrier quote was obtained.
        minimum = round(max(4.50, base * 0.85), 2)
        maximum = round(base * 1.20, 2)
        return {
            "min": minimum,
            "mid": round(base, 2),
            "max": maximum,
            "service": service,
            "billable_weight_lb": round(billable_weight, 2),
            "basis": "carrier-rate estimate from package weight/dimensional weight; buyer shipping setting does not change operator cost",
        }

    def calculate_profit(self, listing: Listing, marketplace: str = "ebay") -> dict:
        sale_price = listing.sale_price or 0.0
        purchase_cost = listing.purchase_cost or 0.0
        fees = listing.fees_actual if listing.fees_actual is not None else self.estimate_fees_by_marketplace(listing, marketplace)
        # A zero is legacy/unknown data, not a free operator shipment.  Buyer
        # free-shipping settings still incur a carrier cost for PosterPro.
        shipping = listing.shipping_cost if listing.shipping_cost is not None and float(listing.shipping_cost or 0) > 0 else self.estimate_shipping_cost(listing)

        profit = round(sale_price - purchase_cost - fees - shipping, 2)
        invested = purchase_cost + fees + shipping
        roi_percentage = round((profit / invested) * 100, 2) if invested > 0 else 0.0

        return {
            "profit": profit,
            "roi_percentage": roi_percentage,
            "fees_estimated": round(self.estimate_fees_by_marketplace(listing, marketplace), 2),
            "shipping_cost": round(shipping, 2),
        }

    def update_profit_on_sale_event(self, listing: Listing, marketplace: str = "ebay") -> Listing:
        metrics = self.calculate_profit(listing, marketplace)
        listing.profit = metrics["profit"]
        listing.roi_percentage = metrics["roi_percentage"]
        listing.fees_estimated = metrics["fees_estimated"]
        if listing.shipping_cost is None or float(listing.shipping_cost or 0) <= 0:
            listing.shipping_cost = metrics["shipping_cost"]
        return listing
