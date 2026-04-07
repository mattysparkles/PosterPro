from app.services.offer_service import OfferService


class DummyListing:
    def __init__(self, min_offer=None, buy_now=100, listing_price=None, suggested_price=None, marketplace_data=None):
        self.min_acceptable_offer = min_offer
        self.buy_it_now_price = buy_now
        self.listing_price = listing_price
        self.suggested_price = suggested_price
        self.marketplace_data = marketplace_data or {}


def test_offer_rule_accepts_on_min_acceptable_offer():
    service = OfferService()
    listing = DummyListing(min_offer=75)

    decision = service.evaluate_offer(listing, {"price": {"value": "80", "currency": "USD"}})

    assert decision.decision == "accept"
    assert "min_acceptable_offer" in decision.reason


def test_offer_rule_accepts_on_buy_it_now_ratio():
    service = OfferService()
    listing = DummyListing(
        min_offer=110,
        buy_now=120,
        marketplace_data={"offer_auto_rules": {"accept_over_buy_it_now_ratio": 0.8}},
    )

    decision = service.evaluate_offer(listing, {"offeredAmount": {"value": "99", "currency": "USD"}})

    assert decision.decision == "accept"
    assert "80.0%" in decision.reason


def test_offer_rule_rejects_when_below_thresholds():
    service = OfferService()
    listing = DummyListing(
        min_offer=90,
        buy_now=120,
        marketplace_data={"offer_auto_rules": {"accept_over_buy_it_now_ratio": 0.8}},
    )

    decision = service.evaluate_offer(listing, {"price": {"value": "70", "currency": "USD"}})

    assert decision.decision == "reject"
