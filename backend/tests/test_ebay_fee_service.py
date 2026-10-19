from types import SimpleNamespace

import pytest

from app.services.ebay_fee_service import calculate_expected_ebay_fee, select_rule


def listing(price, category="Home & Garden", shipping=None, **kwargs):
    return SimpleNamespace(
        sale_price=None,
        listing_price=price,
        suggested_price=None,
        shipping_profile={"buyer_paid_shipping": shipping} if shipping is not None else {},
        marketplace_data=kwargs.pop("marketplace_data", {}),
        category_suggestion=category,
        detected_category_guess="",
        category_id=kwargs.pop("category_id", None),
        title=kwargs.pop("title", "Test item"),
    )


def test_no_store_default_includes_shipping_tax_and_order_fee():
    result = calculate_expected_ebay_fee(listing(100, shipping=10), {"estimated_sales_tax_rate": 9.5 / 110})
    assert result["feeBase"] == pytest.approx(119.5)
    assert result["totalExpectedFee"] == pytest.approx(16.65)
    assert result["sellerPlan"] == "NONE"


def test_no_store_default_progressive_threshold():
    result = calculate_expected_ebay_fee(listing(10000))
    assert result["totalExpectedFee"] == pytest.approx(1079.15)


def test_store_qualifying_desktop_rate():
    result = calculate_expected_ebay_fee(listing(1000, "Computers / Tablets / Networking > Desktops & All-In-Ones"), {"store_plan": "BASIC"})
    assert result["totalExpectedFee"] == pytest.approx(73.90)


def test_store_default_and_progressive():
    assert calculate_expected_ebay_fee(listing(1000), {"store_plan": "BASIC"})["totalExpectedFee"] == pytest.approx(127.40)
    assert calculate_expected_ebay_fee(listing(3000), {"store_plan": "BASIC"})["totalExpectedFee"] == pytest.approx(329.65)


def test_store_womens_bag_is_whole_sale_threshold():
    result = calculate_expected_ebay_fee(listing(2500, "Clothing, Shoes & Accessories > Women's Bags & Handbags"), {"store_plan": "BASIC"})
    assert result["totalExpectedFee"] == pytest.approx(175.40)
    assert result["calculationType"] == "WHOLE_SALE_THRESHOLD"


def test_store_bullion_uses_three_whole_sale_thresholds():
    result = calculate_expected_ebay_fee(listing(12000, "Coins & Paper Money > Bullion"), {"store_plan": "BASIC"})
    assert result["totalExpectedFee"] == pytest.approx(540.40)
    assert result["calculationType"] == "WHOLE_SALE_THRESHOLD"


def test_starter_uses_standard_table_and_missing_category_is_safe():
    result = calculate_expected_ebay_fee(listing(1000, category=""), {"store_plan": "STARTER"})
    assert result["ruleId"] == "standard-default"
    assert result["totalExpectedFee"] == pytest.approx(136.40)


def test_specific_rule_beats_default():
    assert select_rule(listing(1000, "Computers / Tablets / Networking > Cables"), "BASIC").id == "store-computer-accessory"


def test_athletic_shoes_under_150_use_normal_rate_and_over_150_exempt_order_fee():
    under = calculate_expected_ebay_fee(listing(100, "Clothing, Shoes & Accessories > Athletic Shoes"), {"store_plan": "BASIC"})
    over = calculate_expected_ebay_fee(listing(150, "Clothing, Shoes & Accessories > Athletic Shoes"), {"store_plan": "BASIC"})
    assert under["ruleId"] == "store-default"
    assert under["orderFee"] == pytest.approx(0.40)
    assert over["ruleId"] == "store-athletic-shoes"
    assert over["orderFee"] == pytest.approx(0.0)
