from app.services.customer_description import customer_description_is_safe, sanitize_customer_description


def test_operator_observed_internal_guidance_is_removed():
    bad = "Condition: New. Please review the attached product images and confirm packaging, included components, and final measurements before publishing.\nCategory guidance: 30-day refund / replacement"
    clean, removed = sanitize_customer_description(bad)
    assert removed
    assert not customer_description_is_safe(bad)
    assert "Please review" not in clean
    assert "Category guidance" not in clean


def test_buyer_facing_description_remains_safe():
    assert customer_description_is_safe("Pre-owned electronic kit in pictured condition. Includes the components shown.")
