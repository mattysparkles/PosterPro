from app.core import database as database_module
from app import main as main_module
from app.workers import tasks


def test_db_session_uses_isolated_sqlite_database(db_session):
    assert database_module.engine.dialect.name == "sqlite"
    assert tasks.SessionLocal is database_module.SessionLocal
    assert main_module.SessionLocal is database_module.SessionLocal
