from app.services.photo_enrichment import PhotoEnrichmentService


def test_group_vision_visits_every_six_photo_batch_and_keeps_conflicts_explicit(tmp_path, monkeypatch):
    paths = []
    for index in range(14):
        path = tmp_path / f"photo-{index}.jpg"
        path.write_bytes(b"fixture")
        paths.append(str(path))

    service = PhotoEnrichmentService()
    batches = []

    def fake_batch(batch_paths, **_kwargs):
        batches.append(list(batch_paths))
        index = len(batches)
        return {
            "identity": {"brand": "Rawlings", "product_type": "Baseball" if index < 3 else "Bat"},
            "item_specifics": {"Quantity": "2"},
            "category": "Sports Memorabilia",
            "confidence": 0.8,
        }

    monkeypatch.setattr(service, "_group_evidence_vision_batch", fake_batch)
    result = service._group_evidence_vision(paths, db=object(), user_id=2, lineage={})

    assert [len(batch) for batch in batches] == [6, 6, 2]
    assert result["vision_photos_processed"] == 14
    assert result["identity"]["brand"] == "Rawlings"
    assert "product_type" not in result["identity"]
    assert "product_type" in result["conflicting_fields"]
    assert result["item_specifics"]["Quantity"] == "2"


def test_group_vision_marks_failed_batches_without_fabricating_coverage(tmp_path, monkeypatch):
    paths = []
    for index in range(8):
        path = tmp_path / f"photo-{index}.jpg"
        path.write_bytes(b"fixture")
        paths.append(str(path))
    service = PhotoEnrichmentService()
    monkeypatch.setattr(service, "_group_evidence_vision_batch", lambda batch, **kwargs: {"identity": {"product_type": "ball"}} if len(batch) == 6 else {})

    result = service._group_evidence_vision(paths, db=object(), user_id=2, lineage={})

    assert result["vision_photos_processed"] == 6
    assert result["successful_batches"] == 1
    assert result["failed_batches"] == [{"photo_indices": [7, 8], "status": "not_analyzed_or_unavailable"}]
