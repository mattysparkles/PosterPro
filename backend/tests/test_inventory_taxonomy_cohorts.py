from types import SimpleNamespace

from app.services.inventory_taxonomy_cohorts import _identity


def test_inventory_cohorts_prefer_exact_source_identifiers_and_otherwise_exact_titles():
    by_asin = SimpleNamespace(
        source_metadata={"asin": "B000123456"}, title="Different title enrichment", category_suggestion="Kitchen")
    key, label, basis = _identity(by_asin)
    assert key == "id:asin:b000123456"
    assert label == "Different title enrichment"
    assert "ASIN" in basis

    without_id = SimpleNamespace(source_metadata={}, title="  Blue Widget, Model 2!", category_suggestion="Tools")
    key, _, basis = _identity(without_id)
    assert key == "title:tools:blue widget model 2"
    assert basis == "exact normalized title and source category"
