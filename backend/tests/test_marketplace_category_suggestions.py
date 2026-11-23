from types import SimpleNamespace

from app.services import marketplace_category_suggestions as suggestions
from app.api.taxonomy_registry import CategorySuggestionJobRequest, create_category_suggestion_job
from app.models.models import BulkJob, Listing, User
from app.models.enums import ListingStatus


def test_bulk_suggestions_are_separate_review_candidates_and_preserve_confirmed_mapping(monkeypatch):
    listing = SimpleNamespace(
        id=17, user_id=2, title="Acme Wireless Headphones Model X Size Large Black Pack of 2",
        suggested_title=None, description="", category_suggestion="Consumer Electronics",
        item_specifics={"Brand": "Acme", "Model": "X", "Size": "Large", "Color": "Black"},
        source_metadata={"asin": "B012345678", "amazon_product_facts": {"brand": "Acme", "model": "X"}},
        marketplace_data={"marketplace_category_mappings": {"ebay": {
            "mapping_id": "confirmed-ebay", "verification_status": "operator_confirmed",
            "category_path": "Electronics > Headphones", "taxonomy_version_id": "ebay-v1",
        }}},
    )
    def ranked(db, **kwargs):
        return {"marketplaces": [{
            "marketplace": "facebook", "status": "review_required", "taxonomy_versions": [{"version_id": "fb-v2"}],
            "candidates": [{
                "category_id": "reference-11", "category_key": "fb:11", "full_path": "Electronics > Headphones",
                "registry_version_id": "fb-v2", "taxonomy_version": "reference-v2", "data_source": "meta_reference",
                "verification_status": "imported_unverified", "staleness_status": "unknown",
                "terminal_status": "unknown", "publishability_status": "reference_only",
                "candidate_retrieval_score": 0.3, "mapping_confidence": 0.21,
                "required_aspects": [{"localizedAspectName": "Brand"}, {"localizedAspectName": "Condition"}],
            }],
        }]}
    monkeypatch.setattr(suggestions, "rank_taxonomy_candidates", ranked)
    result = suggestions.build_suggestions(object(), listing, owner_user_id=2, marketplaces=["facebook"])
    assert set(result["marketplaces"]) == {"facebook"}
    candidate = result["marketplaces"]["facebook"]["candidates"][0]
    assert candidate["category_id"] == "reference-11"
    assert candidate["reference_only"] is True
    assert candidate["selectable_for_confirmation"] is False
    assert candidate["missing_attributes"] == ["Condition"]
    assert candidate["confidence_kind"] == "uncalibrated_retrieval_score"
    assert result["assignment_state"] == "suggestions_only_operator_confirmation_required"
    assert result["published_or_queued"] is False

    suggestions.persist_suggestions(listing, result)
    saved = listing.marketplace_data
    assert saved["marketplace_category_mappings"]["ebay"]["mapping_id"] == "confirmed-ebay"
    assert saved["marketplace_category_suggestions"]["marketplaces"]["facebook"]["candidates"][0]["category_id"] == "reference-11"


def test_suggestion_worker_refuses_cross_owner_listing():
    listing = SimpleNamespace(id=4, user_id=3, title="Acme headphones", source_metadata={}, item_specifics={})
    try:
        suggestions.build_suggestions(object(), listing, owner_user_id=2)
    except ValueError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("cross-owner suggestions must be rejected")


def test_category_suggestion_job_is_owner_scoped_persistent_and_never_publishes(db_session, monkeypatch):
    from app.workers.tasks import process_marketplace_taxonomy_suggestion_job
    queued = []
    monkeypatch.setattr(process_marketplace_taxonomy_suggestion_job, "delay", lambda job_id: queued.append(job_id))
    user = User(email="category-job-owner@example.com")
    other = User(email="category-job-other@example.com")
    db_session.add_all([user, other]); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.draft, title="Acme Headphones",
                      source_metadata={"asin": "B012345678"}, marketplace_data={})
    foreign = Listing(user_id=other.id, status=ListingStatus.draft, title="Acme Headphones",
                      source_metadata={"asin": "B012345678"}, marketplace_data={})
    db_session.add_all([listing, foreign]); db_session.commit()
    response = create_category_suggestion_job(CategorySuggestionJobRequest(
        listing_ids=[listing.id], marketplaces=["facebook", "ebay"]), db_session, user)
    job = db_session.get(BulkJob, response["job_id"])
    assert response["status"] == "queued"
    assert response["publishing_performed"] is False
    assert job.payload["listing_ids"] == [listing.id]
    assert job.payload["cursor"] == 0
    assert job.payload["marketplaces"] == ["facebook", "ebay"]
    assert queued == [job.id]

    from fastapi import HTTPException
    try:
        create_category_suggestion_job(CategorySuggestionJobRequest(
            listing_ids=[foreign.id], marketplaces=["mercari"]), db_session, user)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("cross-owner listing IDs must not enter a suggestion job")


def test_suggestion_candidate_persists_in_listing_json_without_assigning_category(db_session):
    user = User(email="category-suggestion-persistence@example.com")
    db_session.add(user); db_session.flush()
    listing = Listing(user_id=user.id, status=ListingStatus.draft, title="Acme headphones",
                      category_id=None, category_suggestion="Electronics",
                      marketplace_data={"marketplace_category_mappings": {"ebay": {"mapping_id": "manual", "category_id": "old"}}})
    db_session.add(listing); db_session.commit()
    result = {"version": 1, "marketplaces": {"facebook": {
        "state": "review_required", "candidates": [{"category_key": "fb:leaf", "taxonomy_version_id": "fb:v1"}],
    }}, "assignment_state": "suggestions_only_operator_confirmation_required", "published_or_queued": False}
    suggestions.persist_suggestions(listing, result)
    db_session.commit(); db_session.refresh(listing)
    assert listing.marketplace_data["marketplace_category_mappings"]["ebay"]["mapping_id"] == "manual"
    assert listing.marketplace_data["marketplace_category_suggestions"]["marketplaces"]["facebook"]["candidates"][0]["category_key"] == "fb:leaf"
    assert listing.category_id is None
