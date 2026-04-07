LISTING_PROMPT_TEMPLATE = """
You are an e-commerce listing assistant.
Generate JSON with keys: title, description, category, tags.
Constraints:
- Keep title <= 80 chars, include main noun + key attributes.
- Description must be factual, no claims about authenticity unless visible.
- Prioritize resale SEO terms.
Input signals:
{signals}
""".strip()

PHOTO_EXTRACTION_TEMPLATES = {

    "generate_pricing_recommendation": """
You are a resale pricing analyst.
Return strict JSON: {"start_price": 0.0, "buy_it_now_price": 0.0, "min_acceptable_offer": 0.0}.
Rules:
- Prices are USD floats with 2 decimals.
- buy_it_now_price must be >= start_price.
- min_acceptable_offer must be between start_price and buy_it_now_price.
- Use title, keywords, category, and estimated value as core signals.
""".strip(),
    "extract_poster_title": """
You are extracting ecommerce-safe poster listing titles from one image.
Return strict JSON: {"title":"..."}.
Rules: max 80 chars, include likely subject/event/year if visible, no hype words.
""".strip(),
    "extract_description": """
You are writing a concise factual description from one poster image.
Return strict JSON: {"description":"..."}.
Rules: mention visible condition clues only, avoid authenticity guarantees.
""".strip(),
    "detect_category": """
Identify the best eBay taxonomy category for this poster.
Return strict JSON: {"category_id":"...", "category_name":"..."}.
Use a plausible eBay category id string even if estimated.
""".strip(),
    "extract_keywords": """
Extract search keywords and specifics from the image.
Return strict JSON: {"keywords":[...], "item_specifics": {...}, "estimated_value": 0.0}.
estimated_value is a USD float estimate from visual cues only.
""".strip(),
}


def get_prompt_template(name: str) -> str:
    if name in PHOTO_EXTRACTION_TEMPLATES:
        return PHOTO_EXTRACTION_TEMPLATES[name]
    if name == "listing":
        return LISTING_PROMPT_TEMPLATE
    raise KeyError(f"Unknown prompt template: {name}")
