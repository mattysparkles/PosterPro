from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.shipping_profitability_policy import repair_unpublished_under_threshold_shipping


def _listing(*, price=24.99, status="draft", free=True, parcel=None):
    return SimpleNamespace(
        id=77,
        source_type="amazon_vine",
        status=status,
        ebay_publish_status="draft",
        ebay_listing_id=None,
        marketplace_listings=[],
        listing_price=price,
        suggested_price=None,
        buy_it_now_price=None,
        marketplace_data={"shipping": {"free_shipping": free, "mode": "included"}},
        shipping_profile=parcel or {},
    )


def test_under_25_draft_requires_buyer_shipping_but_not_unverified_service():
    listing = _listing()
    db = MagicMock()
    db.query.return_value.all.return_value = [listing]

    result = repair_unpublished_under_threshold_shipping(db, apply=True)

    assert result["corrected_count"] == 1
    shipping = listing.marketplace_data["shipping"]
    assert shipping["buyer_pays_shipping"] is True
    assert shipping["free_shipping"] is False
    assert shipping["shipping_setup_required"] is True
    assert shipping["mode"] == "setup_required"
    assert shipping["requested_domestic_service"] == "USPSGroundAdvantage"
    assert "shipping_service" not in shipping
    db.commit.assert_called_once()


def test_threshold_and_already_remote_records_are_not_rewritten():
    at_threshold = _listing(price=25.00)
    remote = _listing()
    remote.ebay_listing_id = "remote-1"
    db = MagicMock()
    db.query.return_value.all.return_value = [at_threshold, remote]

    result = repair_unpublished_under_threshold_shipping(db, apply=True)

    assert result["candidate_count"] == 0
    assert result["corrected_count"] == 0
    db.commit.assert_not_called()
    assert at_threshold.marketplace_data["shipping"]["free_shipping"] is True
    assert remote.marketplace_data["shipping"]["free_shipping"] is True
