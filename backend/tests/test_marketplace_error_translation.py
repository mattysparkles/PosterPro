from app.services.marketplace_error_translation import translate_marketplace_error


def test_original_reproduction_aspect_is_not_mislabeled_as_oauth_environment_error():
    result = translate_marketplace_error(
        "ebay",
        "eBay API request failed (400): The item specific Original/Reproduction is missing. Add it and try again.",
    )
    assert result["code"] == "EBAY_ITEM_SPECIFIC_MISSING"
    assert result["operator_action"] == "fix_aspects"


def test_explicit_sandbox_production_mismatch_remains_oauth_error():
    result = translate_marketplace_error("ebay", "The eBay sandbox environment does not match production.")
    assert result["code"] == "EBAY_CREDENTIAL_ENV_MISMATCH"
