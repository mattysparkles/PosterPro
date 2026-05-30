from contextlib import contextmanager

import app.main as main_module


@contextmanager
def _fake_begin():
    yield object()


def test_bootstrap_database_reports_applied_legacy_columns(monkeypatch):
    monkeypatch.setattr(main_module.Base.metadata, "create_all", lambda bind: None)
    monkeypatch.setattr(main_module.engine, "begin", _fake_begin)
    monkeypatch.setattr(main_module.settings, "startup_schema_compat_enabled", True)

    applied = {"users.full_name"}

    def fake_add_column_if_missing(_connection, table_name, column_name, _ddl):
        return f"{table_name}.{column_name}" in applied

    monkeypatch.setattr(main_module, "_add_column_if_missing", fake_add_column_if_missing)

    summary = main_module._bootstrap_database()

    assert summary == {
        "startup_schema_compat_enabled": True,
        "legacy_schema_columns_applied": ["users.full_name"],
    }


def test_health_reports_startup_schema_compat_summary():
    main_module.app.state.database_ready = True
    main_module.app.state.database_error = None
    main_module.app.state.bootstrap_summary = {
        "startup_schema_compat_enabled": True,
        "legacy_schema_columns_applied": ["users.full_name"],
    }

    payload = main_module.health()

    assert payload == {
        "ok": True,
        "database_ready": True,
        "database_error": None,
        "startup_schema_compat_enabled": True,
        "legacy_schema_columns_applied": ["users.full_name"],
    }
