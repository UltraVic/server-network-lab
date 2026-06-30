"""인증/보안 핵심 로직 단위 테스트 (DB 불필요).

login() 은 USERS 검증 + jwt.encode 만, _decode_user() 는 jwt.decode 만 하므로
DB 연결 없이 순수하게 테스트 가능. CI 게이트: 이게 깨지면 배포가 막힌다.
"""
import asyncio

import pytest
from fastapi import HTTPException

import main


def _login(username, password):
    return asyncio.run(main.login(main.LoginIn(username=username, password=password)))


def test_login_success_and_roundtrip():
    tok = _login("admin", "secret")
    assert tok.access_token
    assert tok.token_type == "bearer"
    # 발급된 토큰을 검증하면 같은 사용자가 나와야 한다.
    assert main._decode_user(tok.access_token) == "admin"


def test_login_wrong_password_401():
    with pytest.raises(HTTPException) as e:
        _login("admin", "WRONG")
    assert e.value.status_code == 401


def test_login_unknown_user_401():
    with pytest.raises(HTTPException) as e:
        _login("nobody", "secret")
    assert e.value.status_code == 401


def test_tampered_token_401():
    tok = _login("admin", "secret")
    with pytest.raises(HTTPException) as e:
        main._decode_user(tok.access_token + "x")   # 서명 변조
    assert e.value.status_code == 401


def test_missing_token_401():
    with pytest.raises(HTTPException) as e:
        main._decode_user("")
    assert e.value.status_code == 401


def test_app_version_is_string():
    assert isinstance(main.APP_VERSION, str) and main.APP_VERSION
