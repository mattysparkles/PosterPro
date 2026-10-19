import asyncio
from types import SimpleNamespace

import pytest

from app.models.enums import EbayPublishStatus, ListingStatus, MarketplaceListingStatus, MarketplaceName
from app.models.models import Listing, MarketplaceListing, User
from app.services import ebay_service


class DummyListing:
    def __init__(self):
        self.id = 5
        self.user_id = 1
        self.title = "Vintage Lamp"
        self.description = "Great condition"
        self.category_suggestion = "171485"
        self.category_id = None
        self.suggested_price = 44.5
        self.ebay_publish_status = EbayPublishStatus.DRAFT
        self.marketplace_data = None
        self.item_specifics = {}
        self.listing_images = []
        self.image_urls = []
        self.shipping_profile = {}
        self.condition_data = {"condition_bucket": "open_box", "item_condition_notes": "Open box"}
        self.source_metadata = {}
        # The default fixture represents an operator-managed catalog item.
        # Tests for photo/recovery sources opt into those source types
        # explicitly so their evidence gate remains exercised.
        self.source_type = "manual"
        self.status = "draft"
        self.needs_review = False
        self.ebay_listing_id = None
        self.publish_attempts = []

    def __getattr__(self, name):
        return None


@pytest.mark.anyio
async def test_trading_get_item_extracts_shipping_business_policy_identity(monkeypatch):
    xml = b'''<?xml version="1.0"?><GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">
      <Ack>Success</Ack><Item><ItemID>123</ItemID><DispatchTimeMax>1</DispatchTimeMax>
        <SellerProfiles><SellerShippingProfile><ShippingProfileID>profile-7</ShippingProfileID>
          <ShippingProfileName>Standard shipping</ShippingProfileName></SellerShippingProfile></SellerProfiles>
      </Item></GetItemResponse>'''

    class Response:
        status_code = 200
        content = xml

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def post(self, *_args, **_kwargs): return Response()

    async def no_wait(*_args, **_kwargs): return None
    monkeypatch.setattr(ebay_service.httpx, "AsyncClient", Client)
    monkeypatch.setattr(ebay_service.rate_limiter, "acquire_async", no_wait)
    monkeypatch.setattr(ebay_service, "reload_settings", lambda: SimpleNamespace(environment="production"))

    result = await ebay_service.get_ebay_trading_item(SimpleNamespace(access_token="test-token"), "123")
    assert result["handling_time_days"] == "1"
    assert result["shipping_policy_id"] == "profile-7"
    assert result["shipping_policy_name"] == "Standard shipping"


@pytest.mark.anyio
async def test_trading_get_ebay_details_extracts_site_supported_dispatch_values(monkeypatch):
    xml = b'''<?xml version="1.0"?><GeteBayDetailsResponse xmlns="urn:ebay:apis:eBLBaseComponents">
      <Ack>Success</Ack><DispatchTimeMaxDetails><DispatchTimeMax>0</DispatchTimeMax>
      <DispatchTimeMax>1</DispatchTimeMax><DispatchTimeMax>2</DispatchTimeMax>
      <DispatchTimeMax>5</DispatchTimeMax><ExtendedHandling>false</ExtendedHandling>
      <UpdateTime>2026-10-01T00:00:00.000Z</UpdateTime></DispatchTimeMaxDetails>
      </GeteBayDetailsResponse>'''

    class Response:
        status_code = 200
        content = xml

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def post(self, *_args, **_kwargs): return Response()

    async def no_wait(*_args, **_kwargs): return None
    monkeypatch.setattr(ebay_service.httpx, "AsyncClient", Client)
    monkeypatch.setattr(ebay_service.rate_limiter, "acquire_async", no_wait)
    monkeypatch.setattr(ebay_service, "reload_settings", lambda: SimpleNamespace(environment="production"))

    result = await ebay_service.get_ebay_supported_handling_times(SimpleNamespace(access_token="test-token"))
    assert result["values_days"] == [0, 1, 2, 5]
    assert result["extended_handling"] is False
    assert result["source"] == "ebay_trading_get_ebay_details"


@pytest.mark.anyio
async def test_trading_active_operations_snapshot_extracts_handling_and_policy_in_one_page(monkeypatch):
    xml = b'''<?xml version="1.0"?><GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">
      <Ack>Success</Ack><ActiveList><ItemArray><Item><ItemID>98765</ItemID><Title>Exact item</Title>
        <SellingStatus><CurrentPrice currencyID="USD">18.50</CurrentPrice><ListingStatus>Active</ListingStatus></SellingStatus>
        <DispatchTimeMax>0</DispatchTimeMax><SKU>PP2A123456</SKU>
        <SellerProfiles><SellerShippingProfile><ShippingProfileID>fulfill-2</ShippingProfileID>
          <ShippingProfileName>Economy</ShippingProfileName></SellerShippingProfile></SellerProfiles>
        <ShippingDetails><ShippingType>Calculated</ShippingType><ShippingServiceOptions>
          <ShippingService>USPSGroundAdvantage</ShippingService><FreeShipping>false</FreeShipping>
        </ShippingServiceOptions></ShippingDetails>
      </Item></ItemArray><PaginationResult><TotalNumberOfPages>1</TotalNumberOfPages></PaginationResult></ActiveList>
      </GetMyeBaySellingResponse>'''

    class Response:
        status_code = 200
        content = xml

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def post(self, *_args, **_kwargs): return Response()

    async def no_wait(*_args, **_kwargs): return None
    monkeypatch.setattr(ebay_service.httpx, "AsyncClient", Client)
    monkeypatch.setattr(ebay_service.rate_limiter, "acquire_async", no_wait)
    monkeypatch.setattr(ebay_service, "reload_settings", lambda: SimpleNamespace(environment="production"))

    rows = await ebay_service.get_ebay_trading_active_operations(SimpleNamespace(access_token="test-token"), limit=50)
    assert len(rows) == 1
    assert rows[0]["listing_price"] == "18.50"
    assert rows[0]["handling_time_days"] == "0"
    assert rows[0]["operation_inspection"]["status"] == "confirmed"
    assert rows[0]["operation_inspection"]["fulfillment_policy_id"] == "fulfill-2"
    assert rows[0]["raw_offer"]["listingPolicies"]["fulfillmentPolicyId"] == "fulfill-2"
    assert rows[0]["shipping"]["calculated"] is True


@pytest.mark.anyio
async def test_exact_sku_offer_lookup_is_read_only_and_does_not_use_catalog_scan(monkeypatch):
    calls = []

    class Client:
        def __init__(self, token):
            assert token == "read-token"
        async def request(self, method, path, *, params=None, **kwargs):
            calls.append((method, path, params))
            return {"offers": [{"offerId": "o-1", "listingStatus": "UNPUBLISHED"}]}

    monkeypatch.setattr(ebay_service, "EbayAPIClient", Client)
    offers = await ebay_service.get_ebay_offers_for_sku(SimpleNamespace(access_token="read-token"), "PP2ABC123")
    assert offers == [{"offerId": "o-1", "listingStatus": "UNPUBLISHED"}]
    assert calls == [("GET", "/sell/inventory/v1/offer", {"sku": "PP2ABC123", "limit": 100, "offset": 0})]


@pytest.mark.anyio
async def test_exact_sku_offer_lookup_rejects_invalid_sku_before_provider_call(monkeypatch):
    def no_client(*_args, **_kwargs): raise AssertionError("provider must not be called")
    monkeypatch.setattr(ebay_service, "EbayAPIClient", no_client)
    with pytest.raises(ebay_service.EbayIntegrationError):
        await ebay_service.get_ebay_offers_for_sku(SimpleNamespace(access_token="token"), "x" * 51)


class DummyAccount:
    def __init__(self):
        self.id = 1
        self.access_token = "token"
        self.refresh_token = "refresh-token"


def test_ebay_effective_price_honors_destination_override_without_changing_canonical():
    listing = DummyListing()
    listing.listing_price = 49.99
    listing.marketplace_data = {"marketplace_overrides": {"ebay": {"price": 25.0}, "mercari": {"price": 28.0}}}
    assert ebay_service._ebay_effective_price(listing) == 25.0
    assert listing.listing_price == 49.99


def test_required_specific_fallback_derives_product_semantics_from_evidence():
    curtain = DummyListing()
    curtain.title = '2 Pcs RV Windshield Curtain for Class A Motorhomes, 120"x40"'
    assert ebay_service._fallback_aspect_value(curtain, "Type", curtain.title) == "Set"

    door_curtain = DummyListing()
    door_curtain.title = 'Magnetic Insulated RV Door Curtain for Winter, 33" W x 75" H'
    assert ebay_service._fallback_aspect_value(door_curtain, "Type", door_curtain.title) == "Door Curtains"

    organizer = DummyListing()
    organizer.title = "Hanging Closet Organizer, Foldable Mesh Travel Storage Bag"
    assert ebay_service._fallback_aspect_value(organizer, "Type", organizer.title) == "Closet Organizer"

    transceiver = DummyListing()
    transceiver.title = "LONELY BINARY RS485 Transceiver Modules, MAX485 & MAX3485 Chips"
    assert ebay_service._fallback_aspect_value(transceiver, "Product", transceiver.title) == "RS485 Transceiver Module"

    handbag = DummyListing()
    handbag.title = "Coach Red Leather Clutch"
    assert ebay_service._fallback_aspect_value(handbag, "Exterior Material", handbag.title) == "Leather"
    assert ebay_service._fallback_aspect_value(handbag, "Exterior Color", handbag.title) == "Red"
    assert ebay_service._fallback_aspect_value(handbag, "Style", handbag.title) == "Clutch"
    assert ebay_service._fallback_aspect_value(handbag, "Department", handbag.title) == "Women"


def _trading_motors_plan():
    return {
        "sku": "posterprou2l7751",
        "category": {
            "category_id": "263320", "taxonomy_marketplace": "EBAY_MOTORS_US",
            "taxonomy_tree_id": "100", "category_path": "Root > eBay Motors > ATV Parts > Turn Signals",
            "ownership_verified": True, "semantic_motors_classification": "TRUE_MOTORS",
        },
        "aspect_summary": {"missing_required": []},
        "policy_ids": {"paymentPolicyId": "p1", "fulfillmentPolicyId": "f1", "returnPolicyId": "r1"},
        "policy_settings": {"merchant_location_verified": True, "merchant_location_city": "North Bergen", "merchant_location_state_or_province": "NJ", "merchant_location_postal_code": "07047", "merchant_location_country": "US", "handling_time_days": 1},
        "offer_payload": {"availableQuantity": 2, "pricingSummary": {"price": {"value": "79.99"}}},
        "inventory_item_payload": {"condition": "NEW", "product": {
            "title": "UTV Turn Signal Kit", "description": "Verified fitment kit",
            "imageUrls": ["https://example.com/photo.jpg"], "aspects": {"Brand": ["Aepiraza"]},
        }},
        "payload_preview": {"item_specifics": {"Brand": ["Aepiraza"]}},
    }


def test_trading_motors_xml_preserves_canonical_fields_and_structured_fitment():
    listing = DummyListing()
    listing.title = "UTV Turn Signal Kit"
    listing.quantity = 2
    listing.listing_price = 79.99
    listing.source_metadata = {"vehicle_fitment": [{"Make": "Polaris", "Model": "Ranger 570", "Year": "2024"}]}
    xml = ebay_service._trading_add_fixed_price_item_xml(listing, _trading_motors_plan())
    assert "AddFixedPriceItemRequest" in xml
    assert "263320" in xml and "UTV Turn Signal Kit" in xml
    assert "PaymentProfileID" in xml and "ShippingProfileID" in xml and "ReturnProfileID" in xml
    assert "Aepiraza" in xml and "https://example.com/photo.jpg" in xml
    assert "Ranger 570" in xml and "Polaris" in xml and "1000" in xml


@pytest.mark.anyio
async def test_trading_add_fixed_price_rejection_retains_post_method_and_provider_errors(monkeypatch):
    xml = b'''<?xml version="1.0"?><AddFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">
      <Ack>Failure</Ack><Errors><ShortMessage>Invalid category</ShortMessage>
      <LongMessage>The category is not valid; Condition is not applicable; package weight is missing.</LongMessage></Errors>
      </AddFixedPriceItemResponse>'''

    class Response:
        status_code = 200
        content = xml

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def post(self, *_args, **_kwargs): return Response()

    async def no_wait(*_args, **_kwargs): return None
    monkeypatch.setattr(ebay_service.httpx, "AsyncClient", Client)
    monkeypatch.setattr(ebay_service.rate_limiter, "acquire_async", no_wait)
    monkeypatch.setattr(ebay_service, "reload_settings", lambda: SimpleNamespace(environment="production"))
    with pytest.raises(ebay_service.EbayIntegrationError) as raised:
        await ebay_service._add_fixed_price_item_via_trading_api(DummyAccount(), DummyListing(), _trading_motors_plan())
    assert raised.value.method == "POST"
    assert raised.value.endpoint == "AddFixedPriceItem"
    assert raised.value.provider_response["provider_rejected"] is True


def test_trading_motors_publisher_stores_remote_identity(monkeypatch, db_session):
    user = User(email="motors@example.test", password_hash="x", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, title="UTV Turn Signal Kit", description="Test listing",
        image_urls=["https://example.com/photo.jpg"], listing_images=[], category_id="263320",
        category_suggestion="Turn Signal Light Assemblies", listing_price=79.99, quantity=1,
        status=ListingStatus.draft, source_metadata={"taxonomy_marketplace": "EBAY_MOTORS_US", "taxonomy_tree_id": "100"},
        marketplace_data={}, item_specifics={"Brand": "Aepiraza"},
    )
    db_session.add(listing)
    db_session.commit()

    async def fake_account(_user_id, _db): return DummyAccount()
    async def fake_plan(*_args, **_kwargs): return _trading_motors_plan()
    async def no_active(*_args, **_kwargs): return []
    async def create(*_args, **_kwargs): return {"listingId": "987654321", "status": "PUBLISHED", "publisher_path": "TRADING_API_MOTORS"}
    async def verify(*_args, **_kwargs): return {"ack": "Success", "listing_status": "Active", "item_id": "987654321", "sku": "posterprou2l7751"}

    monkeypatch.setattr(ebay_service, "build_ebay_publish_plan", fake_plan)
    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_service, "_get_active_ebay_listings_via_trading_api", no_active)
    monkeypatch.setattr(ebay_service, "_add_fixed_price_item_via_trading_api", create)
    monkeypatch.setattr(ebay_service, "get_ebay_trading_item", verify)
    monkeypatch.setattr(ebay_service, "_apply_ebay_plan_repairs_to_listing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ebay_service, "_sync_ebay_marketplace_listing", lambda *_args, **_kwargs: None)

    result = asyncio.run(ebay_service.publish_listing_to_ebay(listing, db_session))
    assert result["listing_id"] == "987654321"
    assert result["publisher_path"] == "TRADING_API_MOTORS"
    assert listing.ebay_listing_id == "987654321"
    assert listing.marketplace_data["publisher_path"] == "TRADING_API_MOTORS"
    assert listing.marketplace_data["ebay_url"] == "https://www.ebay.com/itm/987654321"


def test_ambiguous_trading_motors_create_is_not_replayed(monkeypatch, db_session):
    user = User(email="motors-reconcile@example.test", password_hash="x", role="operator")
    db_session.add(user)
    db_session.flush()
    listing = Listing(
        user_id=user.id, title="UTV Turn Signal Kit", description="Test listing",
        image_urls=["https://example.com/photo.jpg"], listing_images=[], category_id="263320",
        category_suggestion="Turn Signal Light Assemblies", listing_price=79.99, quantity=1,
        status=ListingStatus.draft, source_metadata={"taxonomy_marketplace": "EBAY_MOTORS_US", "taxonomy_tree_id": "100"},
        marketplace_data={}, item_specifics={"Brand": "Aepiraza"},
    )
    db_session.add(listing)
    db_session.flush()
    db_session.add(ebay_service.MarketplacePublishAttempt(
        listing_id=listing.id, user_id=user.id, marketplace=MarketplaceName.ebay,
        started_at=ebay_service.datetime.now(ebay_service.UTC).replace(tzinfo=None), dry_run=False,
        marketplace_status="REMOTE_STATE_UNKNOWN", retry_count=0,
    ))
    db_session.commit()
    creates = {"count": 0}

    async def fake_account(_user_id, _db): return DummyAccount()
    async def fake_plan(*_args, **_kwargs): return _trading_motors_plan()
    async def no_active(*_args, **_kwargs): return []
    async def create(*_args, **_kwargs):
        creates["count"] += 1
        return {"listingId": "should-not-create"}

    monkeypatch.setattr(ebay_service, "build_ebay_publish_plan", fake_plan)
    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_service, "_get_active_ebay_listings_via_trading_api", no_active)
    monkeypatch.setattr(ebay_service, "_add_fixed_price_item_via_trading_api", create)
    monkeypatch.setattr(ebay_service, "_apply_ebay_plan_repairs_to_listing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ebay_service, "_sync_ebay_marketplace_listing", lambda *_args, **_kwargs: None)

    with pytest.raises(ebay_service.EbayIntegrationError, match="REMOTE_STATE_UNKNOWN"):
        asyncio.run(ebay_service.publish_listing_to_ebay(listing, db_session))
    assert creates["count"] == 0
    assert listing.processing_state == "remote_state_unknown"


def test_ebay_effective_price_prefers_current_listing_price_over_stale_suggestion():
    listing = DummyListing()
    listing.listing_price = 37.50
    listing.suggested_price = 22.00
    assert ebay_service._ebay_effective_price(listing) == 37.50


def test_ebay_effective_price_never_uses_estimated_value_or_unapproved_fallback():
    listing = DummyListing()
    listing.suggested_price = None
    listing.estimated_value = 19.99
    assert ebay_service._ebay_effective_price(listing) is None

    listing.listing_price = 24.99
    listing.marketplace_data = {
        "pricing_analysis": {
            "provenance": "category_model_estimate",
            "recommendation_eligible": False,
        }
    }
    assert ebay_service._ebay_effective_price(listing) is None

    listing.marketplace_data["manual_price_override"] = 23.5
    listing.marketplace_data["manual_price_override_reason"] = "Operator reviewed local sold history"
    assert ebay_service._ebay_effective_price(listing) == 23.5


def test_ebay_effective_price_blocks_legacy_photo_intake_price_without_provenance():
    listing = DummyListing()
    listing.source_type = "intake_head_slate"
    listing.listing_price = 21.99
    listing.suggested_price = 21.99
    listing.marketplace_data = {}
    assert ebay_service._ebay_effective_price(listing) is None


@pytest.mark.anyio
async def test_ebay_offer_without_price_is_blocked_before_provider_call(monkeypatch):
    listing = DummyListing()
    listing.suggested_price = None
    listing.estimated_value = 19.99

    def no_client(*_args, **_kwargs):
        raise AssertionError("provider must not be called without an authorized price")

    monkeypatch.setattr(ebay_service, "EbayAPIClient", no_client)
    with pytest.raises(ebay_service.EbayIntegrationError, match="EBAY_PRICE_MISSING"):
        await ebay_service.create_offer_for_item(
            listing, DummyAccount(), "PP2ABC123", category_id="12345",
        )


@pytest.mark.anyio
async def test_ebay_publish_plan_blocks_unapproved_estimate_before_account_lookup(monkeypatch):
    listing = DummyListing()
    listing.listing_price = 24.99
    listing.marketplace_data = {
        "pricing_analysis": {
            "provenance": "category_model_estimate",
            "recommendation_eligible": False,
        }
    }

    async def no_account(*_args, **_kwargs):
        raise AssertionError("account/provider lookup must not occur for an unapproved estimate")

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", no_account)
    with pytest.raises(ebay_service.EbayIntegrationError, match="EBAY_PRICE_MISSING"):
        await ebay_service.build_ebay_publish_plan(listing, None)


@pytest.mark.anyio
async def test_ebay_provider_error_retains_sanitized_submitted_field_names(monkeypatch):
    class Response:
        status_code = 400
        text = "provider rejected"

        def json(self):
            return {"errors": [{"errorId": 25002, "message": "Missing Product aspect"}]}

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def request(self, *_args, **_kwargs): return Response()

    async def no_wait(*_args, **_kwargs): return None

    monkeypatch.setattr(ebay_service.httpx, "AsyncClient", Client)
    monkeypatch.setattr(ebay_service.rate_limiter, "acquire_async", no_wait)
    payload = {
        "sku": "private-sku",
        "product": {"title": "Private title", "description": "Private description", "aspects": {"Brand": ["Private brand"]}},
    }
    with pytest.raises(ebay_service.EbayIntegrationError) as raised:
        await ebay_service.EbayAPIClient("test-token").request(
            "POST", "/sell/inventory/v1/offer/offer-id/publish", payload=payload,
        )

    assert raised.value.method == "POST"
    assert raised.value.request_summary == {
        "top_level_fields": ["product", "sku"],
        "product_fields": ["aspects", "description", "title"],
        "product_aspect_names": ["Brand"],
        "package_fields": [],
    }
    assert "Private title" not in repr(raised.value.request_summary)
    assert "Private brand" not in repr(raised.value.request_summary)


def test_vine_ebay_taxonomy_rejects_first_semantically_wrong_suggestion(monkeypatch):
    listing = DummyListing()
    listing.source_type = "amazon_vine"
    listing.title = "Motorized Roller Shades with Remote for Bedroom Windows"
    listing.category_suggestion = "Home & Garden > Window Treatments > Blinds & Shades"
    listing.source_metadata = {"raw_row_json": {"Product Name": listing.title}}
    account = DummyAccount()
    calls = []

    async def fake_request(self, method, path, params=None, payload=None, headers=None):
        calls.append(params)
        if path.endswith("get_default_category_tree_id"):
            return {"categoryTreeId": "0"}
        if "get_category_subtree" in path:
            return {"categorySubtreeNode": {"childCategoryTreeNodes": []}}
        if len(calls) == 2:
            return {"categorySuggestions": [{"category": {"categoryId": "11525", "categoryName": "Pantyhose & Tights"}}]}
        return {"categorySuggestions": [{"category": {"categoryId": "20464", "categoryName": "Blinds & Shades"}}]}

    monkeypatch.setattr(ebay_service.EbayAPIClient, "request", fake_request)
    result = asyncio.run(ebay_service.suggest_ebay_category(listing, account))

    assert result["categoryId"] == "20464"
    assert result["categoryName"] == "Blinds & Shades"
    assert len(calls) == 4
    assert ebay_service._vine_category_match_score(listing.category_suggestion, "Pantyhose & Tights") == 0


def test_non_vine_ebay_category_skips_wrong_top_suggestion_for_verified_leaf(monkeypatch):
    listing = DummyListing()
    listing.source_type = "intake_head_slate"
    listing.title = "Dorman 4WD Actuator 600-103"
    listing.category_suggestion = "Automotive Parts"
    account = DummyAccount()
    calls = []

    async def fake_request(self, method, path, params=None, payload=None, headers=None):
        calls.append(path)
        if path.endswith("get_default_category_tree_id"):
            return {"categoryTreeId": "0"}
        if path.endswith("get_category_suggestions"):
            return {"categorySuggestions": [
                {"category": {"categoryId": "111", "categoryName": "Other US License Plates"}},
                {"category": {"categoryId": "222", "categoryName": "Automotive Replacement Parts"}},
            ]}
        if "get_category_subtree" in path:
            return {"categorySubtreeNode": {"childCategoryTreeNodes": []}}
        raise AssertionError(f"Unexpected taxonomy request: {path}")

    monkeypatch.setattr(ebay_service.EbayAPIClient, "request", fake_request)
    result = asyncio.run(ebay_service.suggest_ebay_category(listing, account))

    assert result["categoryId"] == "222"
    assert result["leafVerified"] is True
    assert len(calls) == 3


def test_non_vine_ebay_category_fails_closed_on_only_wrong_top_suggestion(monkeypatch):
    listing = DummyListing()
    listing.source_type = "intake_head_slate"
    listing.title = "Dorman 4WD Actuator 600-103"
    listing.category_suggestion = "Automotive Parts"
    account = DummyAccount()

    async def fake_request(self, method, path, params=None, payload=None, headers=None):
        if path.endswith("get_default_category_tree_id"):
            return {"categoryTreeId": "0"}
        if path.endswith("get_category_suggestions"):
            return {"categorySuggestions": [{"category": {"categoryId": "111", "categoryName": "Other US License Plates"}}]}
        raise AssertionError(f"No unrelated candidate should be inspected: {path}")

    monkeypatch.setattr(ebay_service.EbayAPIClient, "request", fake_request)
    with pytest.raises(ebay_service.EbayIntegrationError, match="operator category review is required"):
        asyncio.run(ebay_service.suggest_ebay_category(listing, account))


@pytest.mark.parametrize(
    ("semantic_category", "candidate_name"),
    [
        ("Home & Garden > Home Décor > Mirrors", "Mirrors"),
        ("Consumer Electronics > Multipurpose Batteries & Power > Battery Chargers", "Battery Chargers"),
    ],
)
def test_vine_taxonomy_semantic_matching_accepts_verified_mirror_and_charger_leaves(semantic_category, candidate_name):
    assert ebay_service._vine_category_match_score(semantic_category, candidate_name) > 0


def test_ebay_image_builder_uses_normalized_listing_images(monkeypatch):
    listing = DummyListing()
    listing.image_urls = []
    listing.listing_images = [{"storage_path": "/media/vine/item-1.jpg", "operator_state": "approved"}]
    monkeypatch.setattr(ebay_service, "_image_meets_ebay_policy", lambda _path: True)
    monkeypatch.setattr(ebay_service.settings, "app_base_url", "https://posterpro.example")
    assert ebay_service._build_ebay_image_urls(listing) == ["https://posterpro.example/media/vine/item-1.jpg"]


class DummyDB:
    def add(self, _obj):
        return None

    def flush(self):
        return None

    def commit(self):
        return None

    def refresh(self, _obj):
        return None


def test_apply_ebay_plan_repairs_to_listing_persists_safe_category_and_specifics():
    listing = DummyListing()
    plan = {
        "category": {"category_id": "171485", "category_name": "Lamps", "source": "ebay_taxonomy"},
        "payload_preview": {
            "item_specifics": {"Brand": ["Acme"], "Type": ["Desk Lamp"], "Features": ["Dimmable", "LED"]},
            "item_specifics_provenance": {"Brand": "derived", "Type": "approximate"},
            "item_specifics_approximate": ["Brand", "Type"],
        },
    }

    ebay_service._apply_ebay_plan_repairs_to_listing(listing, plan)

    assert listing.category_suggestion == "171485"
    assert listing.item_specifics == {"Brand": "Acme", "Type": "Desk Lamp", "Features": ["Dimmable", "LED"]}
    assert listing.marketplace_data["ebay_last_resolved_category"]["category_id"] == "171485"
    assert listing.marketplace_data["ebay_item_specifics_provenance"]["Brand"] == "derived"


def test_build_ebay_package_weight_and_size_does_not_send_explicit_estimate():
    listing = DummyListing()
    listing.shipping_profile = {
        "package_weight": "9 lb (estimated)",
        "package_dimensions": {"length": 16, "width": 14, "height": 12},
        "provenance": {"package_dimensions": "operator_measured_packed_parcel"},
    }

    payload = ebay_service._build_ebay_package_weight_and_size(listing)

    assert payload == {"dimensions": {"length": 16, "width": 14, "height": 12, "unit": "INCH"}}


def test_build_ebay_package_weight_and_size_omits_estimated_category_profile():
    listing = DummyListing()
    listing.shipping_profile = {
        "package_weight": 2.0,
        "package_dimensions": {"length": 12, "width": 10, "height": 6},
        "estimated": True,
        "estimated_fields": ["package_weight", "package_dimensions"],
        "provenance": {"package_weight": "estimated_category_profile", "package_dimensions": "estimated_category_profile"},
    }
    assert ebay_service._build_ebay_package_weight_and_size(listing) is None


def test_build_ebay_package_weight_and_size_omits_unverified_measurements():
    listing = DummyListing()
    listing.shipping_profile = {
        "package_weight": 2.0,
        "package_dimensions": {"length": 12, "width": 10, "height": 6},
    }
    assert ebay_service._build_ebay_package_weight_and_size(listing) is None


def test_publish_failure_repair_parses_shipping_weight_and_sets_retryable_state(monkeypatch):
    listing = DummyListing()
    listing.shipping_profile = {
        "package_weight": "3 lb (estimated)",
        "package_dimensions": {"length": 12, "width": 10, "height": 8},
    }
    db = DummyDB()

    class FakePreflightService:
        def apply_repair_actions(self, _db, _listing, *, apply_category_suggestion=False, validate_images=False):
            return {"applied": ["category_suggestion"] if apply_category_suggestion else [], "skipped": []}

        def preflight_listing(self, _db, _listing, _marketplace):
            return {
                "status": "ready",
                "blockers": [],
                "warnings": [],
            }

        def cache_preflight_summary(self, _db, _listing, _preflight):
            return {"cached": True}

    monkeypatch.setattr("app.services.marketplace_preflight.MarketplacePreflightService", FakePreflightService)

    repair = ebay_service._apply_publish_failure_repair(
        db,
        listing,
        translated_error={"code": "EBAY_SHIPPING_DATA_INVALID", "operator_action": "fix_shipping"},
        raw_error="eBay rejected the shipping or package details.",
    )

    assert repair["applied_actions"][0] == "parsed_shipping_weight"
    assert listing.shipping_profile["package_weight"] == 3.0
    assert listing.status == "ready"
    assert listing.needs_review is False
    assert listing.marketplace_data["publish_failure_repair"]["preflight_after"]["status"] == "ready"


def test_publish_failure_repair_sets_condition_override_for_condition_incompatibility(monkeypatch):
    listing = DummyListing()
    db = DummyDB()

    class FakePreflightService:
        def apply_repair_actions(self, _db, _listing, *, apply_category_suggestion=False, validate_images=False):
            return {"applied": ["category_suggestion"] if apply_category_suggestion else [], "skipped": []}

        def preflight_listing(self, _db, _listing, _marketplace):
            return {
                "status": "needs_review",
                "blockers": ["CONDITION_MISSING"],
                "warnings": [],
            }

        def cache_preflight_summary(self, _db, _listing, _preflight):
            return {"cached": True}

    monkeypatch.setattr("app.services.marketplace_preflight.MarketplacePreflightService", FakePreflightService)

    repair = ebay_service._apply_publish_failure_repair(
        db,
        listing,
        translated_error={"code": "EBAY_CONDITION_INCOMPATIBLE_WITH_CATEGORY", "operator_action": "fix_condition"},
        raw_error="condition id is invalid for the selected primary category id",
    )

    assert "ebay_condition_override" in listing.condition_data
    assert repair["applied_actions"]
    assert listing.status == "draft"
    assert listing.needs_review is True


def test_create_offer_for_item_uses_policy_ids(monkeypatch):
    listing = DummyListing()
    account = DummyAccount()
    calls = {"attempt": 0}

    async def fake_request(self, method, path, **kwargs):
        payload = kwargs["payload"]
        if path == "/sell/inventory/v1/offer" and calls["attempt"] == 0:
            calls["attempt"] += 1
            raise ebay_service.EbayIntegrationError("not eligible for business policy")
        assert method == "POST"
        assert path == "/sell/inventory/v1/offer"
        assert payload["pricingSummary"]["price"]["currency"] == "USD"
        assert payload["listingPolicies"]["paymentPolicyId"] == "PAY-1"
        return {"offerId": "offer-123"}

    async def fake_policies(_token, marketplace_id="EBAY_US", create_if_missing=False):
        assert marketplace_id == "EBAY_US"
        assert create_if_missing is True
        return {
            "paymentPolicyId": "PAY-1",
            "fulfillmentPolicyId": "SHIP-1",
            "returnPolicyId": "RET-1",
        }

    monkeypatch.setattr(ebay_service.EbayAPIClient, "request", fake_request)
    monkeypatch.setattr(ebay_service, "get_business_policy_ids", fake_policies)

    result = asyncio.run(ebay_service.create_offer_for_item(listing, account, "sku-1", category_id="171485"))
    assert result["offerId"] == "offer-123"


def test_existing_ebay_offer_is_verified_and_reused_without_delete_or_duplicate_post(monkeypatch):
    listing = DummyListing()
    account = DummyAccount()
    calls = []

    async def fake_request(self, method, path, **kwargs):
        calls.append((method, path))
        if method == "POST" and path == "/sell/inventory/v1/offer":
            raise ebay_service.EbayIntegrationError(
                '{"errors":[{"errorId":25002,"message":"Offer entity already exists.",'
                '"parameters":[{"name":"offerId","value":"offer-123"}]}]}'
            )
        if method == "GET" and path == "/sell/inventory/v1/offer/offer-123":
            return {"offerId": "offer-123", "sku": "sku-1", "marketplaceId": "EBAY_US", "status": "UNPUBLISHED"}
        raise AssertionError(f"Unexpected eBay request: {method} {path}")

    monkeypatch.setattr(ebay_service.EbayAPIClient, "request", fake_request)
    result = asyncio.run(ebay_service.create_offer_for_item(listing, account, "sku-1", category_id="171485"))

    assert result["offerId"] == "offer-123"
    assert result["reusedExisting"] is True
    assert result["existingListingId"] is None
    assert calls == [
        ("POST", "/sell/inventory/v1/offer"),
        ("GET", "/sell/inventory/v1/offer/offer-123"),
    ]


def test_existing_ebay_offer_with_different_sku_is_not_reused_or_deleted(monkeypatch):
    calls = []

    async def fake_request(self, method, path, **kwargs):
        calls.append((method, path))
        if method == "POST":
            raise ebay_service.EbayIntegrationError(
                '{"errors":[{"errorId":25002,"message":"Offer entity already exists.",'
                '"parameters":[{"name":"offerId","value":"offer-123"}]}]}'
            )
        return {"offerId": "offer-123", "sku": "other-sku", "marketplaceId": "EBAY_US"}

    monkeypatch.setattr(ebay_service.EbayAPIClient, "request", fake_request)
    with pytest.raises(ebay_service.EbayIntegrationError, match="different SKU"):
        asyncio.run(ebay_service.create_offer_for_item(DummyListing(), DummyAccount(), "sku-1", category_id="171485"))
    assert calls == [
        ("POST", "/sell/inventory/v1/offer"),
        ("GET", "/sell/inventory/v1/offer/offer-123"),
    ]


def test_publish_listing_to_ebay_failure_sets_failed(monkeypatch):
    listing = DummyListing()
    db = DummyDB()

    async def fake_get_account(_user_id, _db):
        return DummyAccount()

    async def fake_plan(_listing, _db, allow_create_policies=True):
        return {
            "category": {"category_id": "171485", "category_name": "Parts", "metadata_available": True},
            "policy_settings": {"merchant_location_key": "posterpro-1"},
            "inventory_item_payload": {"sku": "sku-1", "product": {"imageUrls": ["https://example.com/image.jpg"]}},
            "offer_payload": {"sku": "sku-1", "categoryId": "171485"},
            "payload_preview": {"item_specifics": {"Brand": ["Lamp"]}},
        }

    async def boom(*_args, **_kwargs):
        raise ebay_service.EbayIntegrationError("upstream down")

    monkeypatch.setattr(ebay_service, "_sync_ebay_marketplace_listing", lambda *args, **kwargs: None)
    monkeypatch.setattr(ebay_service, "_start_publish_attempt", lambda *args, **kwargs: object())
    monkeypatch.setattr(ebay_service, "_finish_publish_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_get_account)
    monkeypatch.setattr(ebay_service, "build_ebay_publish_plan", fake_plan)
    monkeypatch.setattr(ebay_service, "create_inventory_location", boom)

    try:
        asyncio.run(ebay_service.publish_listing_to_ebay(listing, db))
    except ebay_service.EbayIntegrationError:
        pass

    assert listing.ebay_publish_status == EbayPublishStatus.FAILED
    assert "upstream down" in listing.marketplace_data["error"]


def test_publish_listing_to_ebay_invalid_item_error_sets_failed(monkeypatch):
    listing = DummyListing()
    db = DummyDB()
    account = DummyAccount()
    calls = {"item": 0}

    async def fake_get_account(_user_id, _db):
        return account

    async def fake_location(_user_id, _db, **kwargs):
        return {"merchantLocationKey": "posterpro-1"}

    async def fake_plan(_listing, _db, allow_create_policies=True):
        return {
            "category": {"category_id": "171485", "category_name": "Parts", "metadata_available": True},
            "policy_settings": {"merchant_location_key": "posterpro-1"},
            "inventory_item_payload": {"sku": "sku-1", "product": {"imageUrls": ["https://example.com/image.jpg"]}},
            "offer_payload": {"sku": "sku-1", "categoryId": "171485"},
            "payload_preview": {"item_specifics": {"Brand": ["Lamp"]}},
        }

    async def fake_item(_listing, _account, **kwargs):
        calls["item"] += 1
        raise ebay_service.EbayIntegrationError("Invalid access token")

    async def fake_offer(_listing, _account, _sku, **kwargs):
        return {"offerId": "offer-1", "response": {}}

    async def fake_publish(_listing, _account, _offer_id):
        return {"listingId": "12345", "response": {}}

    monkeypatch.setattr(ebay_service, "_sync_ebay_marketplace_listing", lambda *args, **kwargs: None)
    monkeypatch.setattr(ebay_service, "_start_publish_attempt", lambda *args, **kwargs: object())
    monkeypatch.setattr(ebay_service, "_finish_publish_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_get_account)
    monkeypatch.setattr(ebay_service, "build_ebay_publish_plan", fake_plan)
    monkeypatch.setattr(ebay_service, "create_inventory_location", fake_location)
    monkeypatch.setattr(ebay_service, "create_or_replace_item", fake_item)
    monkeypatch.setattr(ebay_service, "create_offer_for_item", fake_offer)
    monkeypatch.setattr(ebay_service, "publish_offer", fake_publish)

    try:
        asyncio.run(ebay_service.publish_listing_to_ebay(listing, db))
    except ebay_service.EbayIntegrationError:
        pass

    assert calls["item"] == 1
    assert listing.ebay_publish_status == EbayPublishStatus.FAILED


def test_read_only_active_listing_check_refreshes_stale_invalid_access_token(monkeypatch):
    account = DummyAccount()
    refreshed = DummyAccount()
    refreshed.access_token = "fresh-token"
    calls = {"requests": 0, "refreshes": 0}

    async def fake_get_account(_user_id, _db):
        return account

    async def fake_refresh(_user_id, _db):
        calls["refreshes"] += 1
        return refreshed

    async def fake_request(self, method, path, **_kwargs):
        assert method == "GET" and path == "/sell/inventory/v1/offer"
        calls["requests"] += 1
        if calls["requests"] == 1:
            raise ebay_service.EbayIntegrationError("eBay API request failed (401) for /sell/inventory/v1/offer")
        assert self.access_token == "fresh-token"
        return {"offers": []}

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_get_account)
    monkeypatch.setattr(ebay_service, "refresh_ebay_token", fake_refresh)
    monkeypatch.setattr(ebay_service.EbayAPIClient, "request", fake_request)

    assert asyncio.run(ebay_service.get_active_ebay_listings(1, DummyDB())) == []
    assert calls == {"requests": 2, "refreshes": 1}


def test_read_only_ebay_sync_creates_idempotent_local_history_for_unmatched_remote_offer(db_session, monkeypatch):
    user = User(email="ebay-history@example.com")
    db_session.add(user)
    db_session.commit()

    async def fake_get_account(_user_id, _db):
        return DummyAccount()

    remote = {
        "source_url": "https://www.ebay.com/itm/123456789012",
        "title": "Remote eBay item",
        "description": "Imported from active eBay inventory.",
        "listing_price": 24.99,
        "quantity": 1,
        "image_urls": ["https://i.ebayimg.com/images/g/example/s-l1600.jpg"],
        "item_specifics": {"Brand": "Acme"},
        "category_id": "123",
        "condition": "New",
        "source_identifiers": {"ebay_listing_id": "123456789012", "offer_id": "offer-1", "sku": "remote-sku-1"},
    }

    sync_limits = []

    async def fake_active(_user_id, _db, **kwargs):
        sync_limits.append(kwargs.get("limit"))
        return [remote]

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_get_account)
    monkeypatch.setattr(ebay_service, "get_active_ebay_listings", fake_active)

    first = asyncio.run(ebay_service.sync_ebay_active_listings(user.id, db_session))
    second = asyncio.run(ebay_service.sync_ebay_active_listings(user.id, db_session))
    listing = db_session.query(Listing).filter_by(user_id=user.id, ebay_listing_id="123456789012").one()

    assert first["created"] == 1
    assert second["created"] == 0
    assert listing.source_type == "ebay_history_reconciliation"
    assert listing.ebay_publish_status == EbayPublishStatus.POSTED
    assert sync_limits == [1000, 1000]


def test_ebay_sync_archives_local_listing_missing_from_complete_active_snapshot(db_session, monkeypatch):
    user = User(email="ebay-archive-sync@example.com")
    listing = Listing(
        user_id=1,
        status=ListingStatus.posted,
        source_type="amazon_vine",
        title="Ended item",
        description="Keep this description for relisting.",
        listing_price=29.99,
        image_urls=["https://example.test/item.jpg"],
        listing_images=[{"source_url": "https://example.test/item.jpg"}],
        ebay_listing_id="999000111222",
        marketplace_data={"custom": "preserve-me"},
        custom_labels=["vine"],
    )
    user.id = 1
    db_session.add_all([user, listing])
    db_session.flush()
    projection = MarketplaceListing(
        listing_id=listing.id,
        marketplace=MarketplaceName.ebay,
        marketplace_listing_id="999000111222",
        status=MarketplaceListingStatus.PUBLISHED,
        raw_response={"remote": {"title": "Ended item"}},
    )
    db_session.add(projection)
    db_session.commit()

    async def fake_get_account(_user_id, _db):
        return DummyAccount()

    async def fake_active(_user_id, _db, **_kwargs):
        return []  # complete successful remote snapshot: no active IDs

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_get_account)
    monkeypatch.setattr(ebay_service, "get_active_ebay_listings", fake_active)
    result = asyncio.run(ebay_service.sync_ebay_active_listings(user.id, db_session))
    db_session.refresh(listing)
    db_session.refresh(projection)

    assert result["archived"] == 1
    assert listing.status == ListingStatus.archived
    assert "archived_ebay" in listing.custom_labels
    assert listing.description == "Keep this description for relisting."
    assert listing.image_urls == ["https://example.test/item.jpg"]
    assert listing.marketplace_data["custom"] == "preserve-me"
    assert listing.marketplace_data["archive_state"]["status"] == "ARCHIVED"
    assert projection.status == MarketplaceListingStatus.CLOSED
    assert projection.marketplace_listing_id == "999000111222"


def test_end_ebay_listing_withdraws_only_the_confirmed_external_identity(monkeypatch):
    listing = DummyListing()
    listing.ebay_listing_id = "123456789012"
    listing.marketplace_data = {"offer": {"offerId": "offer-abc"}}
    calls = []

    class FakeClient:
        def __init__(self, _token):
            pass

        async def request(self, method, path, **_kwargs):
            calls.append((method, path))
            if method == "GET":
                if len([call for call in calls if call[0] == "GET"]) == 1:
                    return {"status": "PUBLISHED", "listing": {"listingId": "123456789012"}}
                return {"status": "UNPUBLISHED", "listing": {"listingId": "123456789012"}}
            return {}

    async def fake_account(_user_id, _db):
        return DummyAccount()

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_service, "EbayAPIClient", FakeClient)
    monkeypatch.setattr(ebay_service, "_sync_ebay_marketplace_listing", lambda *_args, **_kwargs: None)
    result = asyncio.run(ebay_service.end_ebay_listing(
        listing,
        DummyDB(),
        expected_external_listing_id="123456789012",
    ))

    assert result["status"] == "ENDED"
    assert calls == [
        ("GET", "/sell/inventory/v1/offer/offer-abc"),
        ("POST", "/sell/inventory/v1/offer/offer-abc/withdraw"),
        ("GET", "/sell/inventory/v1/offer/offer-abc"),
    ]
    assert listing.marketplace_data["ebay_status"] == "ENDED"


def test_end_ebay_listing_rejects_identity_mismatch_before_api_call(monkeypatch):
    listing = DummyListing()
    listing.ebay_listing_id = "123456789012"
    listing.marketplace_data = {"offer": {"offerId": "offer-abc"}}
    calls = []

    async def fail_if_called(*_args, **_kwargs):
        calls.append(True)
        return DummyAccount()

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fail_if_called)
    with pytest.raises(ebay_service.EbayIntegrationError, match="identity"):
        asyncio.run(ebay_service.end_ebay_listing(
            listing,
            DummyDB(),
            expected_external_listing_id="different-listing",
        ))
    assert calls == []


def test_end_ebay_listing_requires_post_withdraw_remote_verification(monkeypatch):
    listing = DummyListing()
    listing.ebay_listing_id = "123456789012"
    listing.marketplace_data = {"offer": {"offerId": "offer-live"}}
    calls = []

    class FakeClient:
        def __init__(self, _token):
            pass

        async def request(self, method, path, **_kwargs):
            calls.append((method, path))
            if method == "GET":
                return {"status": "PUBLISHED", "listing": {"listingId": "123456789012"}}
            return {"accepted": True}

    async def fake_account(_user_id, _db):
        return DummyAccount()

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_service, "EbayAPIClient", FakeClient)
    monkeypatch.setattr(ebay_service, "_sync_ebay_marketplace_listing", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not close an unverified offer")))
    result = asyncio.run(ebay_service.end_ebay_listing(listing, DummyDB(), expected_external_listing_id="123456789012"))
    assert result["status"] == "REMOTE_STATE_UNKNOWN"
    assert calls[-1] == ("GET", "/sell/inventory/v1/offer/offer-live")


def test_end_ebay_listing_treats_unpublished_offer_as_idempotent(monkeypatch):
    listing = DummyListing()
    listing.ebay_listing_id = "123456789012"
    listing.marketplace_data = {"offer": {"offerId": "offer-abc"}}
    calls = []

    class FakeClient:
        def __init__(self, _token):
            pass

        async def request(self, method, path, **_kwargs):
            calls.append((method, path))
            return {"status": "UNPUBLISHED"}

    async def fake_account(_user_id, _db):
        return DummyAccount()

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_service, "EbayAPIClient", FakeClient)
    monkeypatch.setattr(ebay_service, "_sync_ebay_marketplace_listing", lambda *_args, **_kwargs: None)
    result = asyncio.run(ebay_service.end_ebay_listing(
        listing,
        DummyDB(),
        expected_external_listing_id="123456789012",
    ))
    assert result["status"] == "ALREADY_ENDED"
    assert calls == [("GET", "/sell/inventory/v1/offer/offer-abc")]


def test_resolve_existing_offer_recovers_historical_marketplace_sync_identity():
    listing = DummyListing()
    listing.ebay_listing_id = "188693164224"
    listing.marketplace_listings = [SimpleNamespace(
        marketplace="MarketplaceName.ebay",
        raw_response={"offer_id": "215920750011", "ebay_listing_id": "188693164224"},
    )]

    assert ebay_service._resolve_existing_ebay_offer_id(listing) == "215920750011"


def test_revise_ebay_listing_uses_recovered_offer_without_creating_new_offer(monkeypatch):
    listing = DummyListing()
    listing.id = 1063
    listing.ebay_listing_id = "188693164224"
    listing.marketplace_data = {}
    listing.marketplace_listings = [SimpleNamespace(
        marketplace="MarketplaceName.ebay",
        marketplace_listing_id="188693164224",
        status="MarketplaceListingStatus.PUBLISHED",
        raw_response={"offer_id": "215920750011"},
    )]
    calls = []

    class FakeClient:
        def __init__(self, _token):
            pass

        async def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs.get("payload")))
            if method == "GET":
                return {"listing": {"listingId": "188693164224"}, "status": "PUBLISHED"}
            assert method == "PUT" and path == "/sell/inventory/v1/offer/215920750011"
            return {"status": "UPDATED"}

    async def fake_account(_user_id, _db):
        return DummyAccount()

    async def fake_plan(*_args, **_kwargs):
        return {"payload_preview": {"item_specifics": {}}, "inventory_item_payload": {"sku": "sku-1063"}, "offer_payload": {"pricingSummary": {"price": {"value": "29.99"}}}}

    async def fake_item(*_args, **_kwargs):
        return {"sku": "sku-1063"}

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_service, "build_ebay_publish_plan", fake_plan)
    monkeypatch.setattr(ebay_service, "create_or_replace_item", fake_item)
    monkeypatch.setattr(ebay_service, "EbayAPIClient", FakeClient)
    monkeypatch.setattr(ebay_service, "_sync_ebay_marketplace_listing", lambda *_args, **_kwargs: None)

    result = asyncio.run(ebay_service.revise_ebay_listing(listing, DummyDB(), expected_external_listing_id="188693164224"))

    assert result["status"] == "UPDATED"
    assert calls[0][:2] == ("GET", "/sell/inventory/v1/offer/215920750011")
    assert calls[1][:2] == ("PUT", "/sell/inventory/v1/offer/215920750011")


def test_revise_ebay_listing_never_creates_replacement_when_offer_identity_is_missing(monkeypatch):
    listing = DummyListing()
    listing.ebay_listing_id = "123456789012"
    listing.marketplace_data = {}

    async def fake_account(_user_id, _db):
        return DummyAccount()

    async def fake_plan(*_args, **_kwargs):
        return {"payload_preview": {"item_specifics": {}}, "inventory_item_payload": {}, "offer_payload": {}, "category": {"category_id": "123"}}

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("an update must not create a replacement offer")

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_service, "build_ebay_publish_plan", fake_plan)
    monkeypatch.setattr(ebay_service, "create_offer_for_item", fail_if_called)
    with pytest.raises(ebay_service.EbayIntegrationError, match="no replacement offer was created"):
        asyncio.run(ebay_service.revise_ebay_listing(listing, DummyDB()))


def test_revise_ebay_listing_checks_remote_identity_before_writing(monkeypatch):
    listing = DummyListing()
    listing.ebay_listing_id = "123456789012"
    listing.marketplace_data = {"offer": {"offerId": "offer-abc"}}

    class FakeClient:
        def __init__(self, _token):
            pass

        async def request(self, method, path, **_kwargs):
            assert method == "GET"
            assert path == "/sell/inventory/v1/offer/offer-abc"
            return {"status": "PUBLISHED", "listing": {"listingId": "different-listing"}}

    async def fake_account(_user_id, _db):
        return DummyAccount()

    async def fake_plan(*_args, **_kwargs):
        return {"payload_preview": {"item_specifics": {}}, "inventory_item_payload": {}, "offer_payload": {}, "category": {"category_id": "123"}}

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("inventory item update must not run on identity mismatch")

    monkeypatch.setattr(ebay_service, "get_or_refresh_account", fake_account)
    monkeypatch.setattr(ebay_service, "build_ebay_publish_plan", fake_plan)
    monkeypatch.setattr(ebay_service, "EbayAPIClient", FakeClient)
    monkeypatch.setattr(ebay_service, "create_or_replace_item", fail_if_called)
    with pytest.raises(ebay_service.EbayIntegrationError, match="exact external listing identity"):
        asyncio.run(ebay_service.revise_ebay_listing(listing, DummyDB(), expected_external_listing_id="123456789012"))


@pytest.mark.parametrize(("marketplace_id", "tree_id"), [("EBAY_US", "0"), ("EBAY_MOTORS_US", "100")])
def test_required_aspects_uses_explicit_supported_tree_without_default_tree_lookup(monkeypatch, marketplace_id, tree_id):
    calls = []

    class FakeClient:
        def __init__(self, _token):
            pass

        async def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            assert method == "GET"
            assert path == f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category"
            assert kwargs["params"] == {"category_id": "12345"}
            return {"aspects": []}

    monkeypatch.setattr(ebay_service, "EbayAPIClient", FakeClient)
    payload = asyncio.run(ebay_service.get_required_item_specifics(
        "token", "12345", marketplace_id=marketplace_id, tree_id=tree_id,
    ))

    assert payload == {"aspects": []}
    assert len(calls) == 1
    assert "get_default_category_tree_id" not in calls[0][1]
