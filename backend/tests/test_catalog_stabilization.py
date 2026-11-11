from app.models.enums import MarketplaceName
from app.models.models import Listing, MarketplaceAccount, User
from app.services.ebay_service import _make_oauth_state, parse_oauth_state, summarize_ebay_account_health


def test_marketplace_tokens_encrypt_at_rest_and_decrypt_on_load(db_session):
    user = User(email="catalog-token@example.com")
    db_session.add(user); db_session.flush()
    account = MarketplaceAccount(user_id=user.id, marketplace=MarketplaceName.ebay, external_account_id="seller", access_token="access-secret", refresh_token="refresh-secret")
    db_session.add(account); db_session.commit(); account_id = account.id
    raw_access, raw_refresh = db_session.connection().exec_driver_sql("select access_token, refresh_token from marketplace_accounts where id = ?", (account_id,)).one()
    assert raw_access.startswith("fernet1:") and raw_refresh.startswith("fernet1:")
    db_session.expire_all()
    loaded = db_session.get(MarketplaceAccount, account_id)
    assert loaded.access_token == "access-secret" and loaded.refresh_token == "refresh-secret"


def test_oauth_state_is_signed_and_rejects_tampering():
    state = _make_oauth_state(42)
    assert parse_oauth_state(state) == 42
    try:
        parse_oauth_state(state[:-1] + ("A" if state[-1] != "A" else "B"))
    except Exception:
        pass
    else:
        raise AssertionError("tampered OAuth state must be rejected")


def test_all_source_types_can_coexist_for_one_operator(db_session):
    user = User(email="catalog-all@example.com")
    db_session.add(user); db_session.flush()
    db_session.add_all([
        Listing(user_id=user.id, source_type="media_inventory_recovery", title="Recovery"),
        Listing(user_id=user.id, source_type="amazon_vine", title="Vine"),
        Listing(user_id=user.id, source_type="manual", title="Manual"),
    ])
    db_session.commit()
    assert db_session.query(Listing).filter_by(user_id=user.id).count() == 3


def test_expired_refreshable_account_remains_connected():
    account = MarketplaceAccount(user_id=1, marketplace=MarketplaceName.ebay, external_account_id="seller", access_token="access", refresh_token="refresh")
    health = summarize_ebay_account_health(account)
    assert health["connected"] and health["has_refresh_token"]
