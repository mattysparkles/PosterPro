from app.prompts.templates import LISTING_PROMPT_TEMPLATE


class ListingAIService:
    def generate(self, image_signals: dict) -> dict:
        title = image_signals.get("title_hint") or "Pre-owned Item - Excellent Condition"
        return {
            "title": title[:80],
            "description": "Condition: used. Reviewed from clustered photos. Please inspect all images.",
            "category_suggestion": "Collectibles",
            "tags": ["resale", "preowned", "marketplace"],
            "prompt_used": LISTING_PROMPT_TEMPLATE,
        }
