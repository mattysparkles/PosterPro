from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError
from pydantic import ValidationError

from app.api.marketplace_extension import MercariInventoryDryRunRequest
from app.models.models import Listing
from app.services import mercari_inventory_dry_run as dry_run


class NoTaxonomyDatabase:
    def execute(self, *_args, **_kwargs):
        raise SQLAlchemyError("taxonomy unavailable")


def test_mercari_dry_run_request_normalizes_optional_weight_and_validates_values():
    request = MercariInventoryDryRunRequest
    assert request().package_weight is None
    assert request(package_weight=None).package_weight is None
    assert request(package_weight="").package_weight is None
    assert request(package_weight="  \t ").package_weight is None
    assert request(package_weight="12").package_weight == 12
    assert request(package_weight=" 1.25 ").package_weight == 1.25
    for value in (0, -1, "not-a-number"):
        try:
            request(package_weight=value)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"invalid packed weight accepted: {value!r}")
    for unit in ("stone", "", "LB"):
        try:
            request(package_weight_unit=unit)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"invalid packed weight unit accepted: {unit!r}")


def test_missing_mercari_weight_is_review_not_zero(monkeypatch):
    listing = Listing(
        id=3460, user_id=2, title="Closet organizer", description="Item description",
        listing_price=27.55, condition="New", condition_data={}, item_specifics={},
        source_metadata={}, shipping_profile={}, category_suggestion="Closet Organizers",
    )
    monkeypatch.setattr(dry_run, "build_extension_marketplace_payload", lambda *_a, **_k: {
        "title": "Closet organizer", "description": "Item description", "price": 27.55,
        "condition": "New", "item_specifics": {}, "image_urls": ["https://media.example/item.jpg"],
        "shipping": {"shipping_payer": None},
    })
    prepared = dry_run.build_mercari_inventory_dry_run_payload(
        NoTaxonomyDatabase(), user=SimpleNamespace(id=2), listing=listing,
        operator_inputs=MercariInventoryDryRunRequest(package_weight="   ").model_dump(),
    )
    shipping = prepared["marketplace_payload"]["shipping"]
    assert shipping["package_weight_value"] is None
    assert shipping["package_weight_ounces"] is None
    assert "packed_weight_review" in prepared["review_fields"]
    assert prepared["operator_inputs"]["package_weight"] is None
    assert listing.shipping_profile == {}


def test_inventory_projection_keeps_source_price_deduplicates_photos_and_marks_unknowns(monkeypatch):
    listing = Listing(
        id=3460,
        user_id=2,
        title="Hanging Closet Organizer, Foldable Mesh Travel Storage Bag with Zipper Bag",
        description="Existing source-backed product description.",
        listing_price=27.55,
        condition="New",
        condition_data={"operator_review_required": True},
        item_specifics={"Size": "One Size", "Color": "Multicolor"},
        source_metadata={},
        shipping_profile={"estimated": True},
        category_suggestion="Closet Organizers",
    )
    monkeypatch.setattr(
        dry_run,
        "build_extension_marketplace_payload",
        lambda *_args, **_kwargs: {
            "title": "T" * 100,
            "description": "D" * 1200,
            "price": 1.00,  # Destination override must not reprice the source.
            "condition": "New",
            "item_specifics": {"Size": "One Size", "Color": "Multicolor"},
            "image_urls": ["https://media.example/1.jpg", "https://media.example/1.jpg", "https://media.example/2.jpg"],
            "shipping": {"shipping_payer": None, "parcel_size": None, "parcel_weight": None},
        },
    )

    prepared = dry_run.build_mercari_inventory_dry_run_payload(
        NoTaxonomyDatabase(), user=SimpleNamespace(id=2), listing=listing
    )

    payload = prepared["marketplace_payload"]
    assert len(payload["title"]) == dry_run.TITLE_LIMIT
    assert len(payload["description"]) == dry_run.DESCRIPTION_LIMIT
    assert payload["price"] == 27.55
    assert payload["brand"] is None
    assert payload["size"] is None
    assert payload["color"] == "Multicolor"
    assert payload["category"] == "Home > Storage & Organization > Clothing & closet storage"
    assert payload["category_path"] == ["Home", "Storage & Organization", "Clothing & closet storage"]
    assert payload["category_resolution"]["status"] == "unverified_observed_intermediate"
    assert payload["image_urls"] == ["https://media.example/1.jpg", "https://media.example/2.jpg"]
    assert prepared["review_required"] is True
    assert {"category_confirmation", "condition_packaging_review", "shipping_measurements_review", "packed_weight_review", "shoebox_fit_review", "shipping_service_review", "condition_choice_review"} <= set(prepared["review_fields"])
    assert "brand" not in prepared["review_fields"]  # A legitimate no-brand choice is offered at the destination.
    assert prepared["submission_performed"] is False
    assert listing.listing_price == 27.55  # Source record remains untouched.


def test_packed_weight_conversion_rejects_unknown_or_unsafe_weights():
    convert = dry_run.convert_packed_weight_to_ounces
    assert convert(1, "lb") == 16
    assert convert(1.25, "lb") == 20
    assert convert(16, "oz") == 16
    assert abs(convert(453.59237, "g") - 16) < 0.0001
    assert abs(convert(0.45359237, "kg") - 16) < 0.0001
    for value, unit in ((0, "oz"), (-1, "lb"), (1, "stone"), (3000, "oz"), (float("nan"), "oz")):
        try:
            convert(value, unit)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe packed weight accepted: {value} {unit}")
