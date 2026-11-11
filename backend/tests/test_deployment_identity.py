from app.main import deployment_identity


def test_deployment_identity_reports_release_metadata_without_internal_paths(monkeypatch):
    monkeypatch.setenv("POSTERPRO_RELEASE_ID", "posterpro-20261110-abc123")
    monkeypatch.setenv("POSTERPRO_BACKEND_COMMIT", "abc123")
    monkeypatch.setenv("POSTERPRO_FRONTEND_BUILD_ID", "web456")
    monkeypatch.setenv("POSTERPRO_SCHEMA_VERSION", "20261109_shelf_census.sql")
    monkeypatch.setenv("POSTERPRO_DEPLOYMENT_TIMESTAMP", "2026-11-10T18:00:00Z")

    result = deployment_identity()

    assert result["release_id"] == "posterpro-20261110-abc123"
    assert result["backend_build_id"] == "abc123"
    assert result["frontend_build_id"] == "web456"
    assert result["schema_version"] == "20261109_shelf_census.sql"
    assert "source_repo" not in result
