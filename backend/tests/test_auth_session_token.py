from app.core.auth import issue_session_token, parse_session_token


def test_parse_session_token_accepts_unpadded_base64() -> None:
    token = issue_session_token(42, view_as_regular=True)
    unpadded = token.rstrip("=")
    user_id, view_as_regular = parse_session_token(unpadded)
    assert user_id == 42
    assert view_as_regular is True
