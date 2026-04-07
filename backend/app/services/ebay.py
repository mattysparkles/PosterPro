from datetime import datetime, timedelta


class EbayService:
    def oauth_url(self) -> str:
        return "https://auth.ebay.com/oauth2/authorize"

    def exchange_code(self, code: str) -> dict:
        return {
            "access_token": f"token_{code}",
            "refresh_token": f"refresh_{code}",
            "expires_at": datetime.utcnow() + timedelta(hours=2),
            "external_account_id": "demo-ebay-account",
        }

    def enrich_price(self, title: str, barcode: str | None = None) -> dict:
        if barcode:
            return {"suggested_price": 29.99, "comparables": [{"title": title, "price": 28.5}]}
        return {"suggested_price": 24.0, "comparables": [{"title": f"Sold {title}", "price": 22.0}]}

    def publish_listing(self, listing: dict) -> dict:
        return {"listing_id": f"EBAY-{listing['id']}", "status": "posted"}
