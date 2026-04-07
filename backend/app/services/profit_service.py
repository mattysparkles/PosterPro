from __future__ import annotations

from app.models.models import Listing


class ProfitService:
    MARKETPLACE_FEE_RULES = {
        "ebay": {"final_value_pct": 0.1325, "fixed": 0.30},
        "mercari": {"final_value_pct": 0.10, "fixed": 0.0},
        "facebook": {"final_value_pct": 0.05, "fixed": 0.0},
    }

    def estimate_fees_by_marketplace(self, listing: Listing, marketplace: str = "ebay") -> float:
        sale_price = listing.sale_price or listing.listing_price or listing.suggested_price or 0.0
        rule = self.MARKETPLACE_FEE_RULES.get(marketplace.lower(), {"final_value_pct": 0.12, "fixed": 0.30})
        return round((sale_price * rule["final_value_pct"]) + rule["fixed"], 2)

    def estimate_shipping_cost(self, listing: Listing) -> float:
        data = listing.marketplace_data or {}
        if data.get("shipping_mode") == "flat":
            return float(data.get("shipping_flat_cost", 6.99))

        price_reference = listing.sale_price or listing.listing_price or listing.suggested_price or 0.0
        # simple rule-based shipping curve
        if price_reference >= 100:
            return 12.99
        if price_reference >= 40:
            return 8.99
        return 5.99

    def calculate_profit(self, listing: Listing, marketplace: str = "ebay") -> dict:
        sale_price = listing.sale_price or 0.0
        purchase_cost = listing.purchase_cost or 0.0
        fees = listing.fees_actual if listing.fees_actual is not None else self.estimate_fees_by_marketplace(listing, marketplace)
        shipping = listing.shipping_cost if listing.shipping_cost is not None else self.estimate_shipping_cost(listing)

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
        if listing.shipping_cost is None:
            listing.shipping_cost = metrics["shipping_cost"]
        return listing
