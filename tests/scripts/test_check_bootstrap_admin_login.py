from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class _Request:
    method: str
    path: str
    body: bytes | None
    headers: dict[str, str]


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.read_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self._body


class _QueuedConnection:
    def __init__(self, response: _Response, requests: list[_Request]) -> None:
        self._response = response
        self._requests = requests
        self.closed = False

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._requests.append(_Request(method, path, body, headers or {}))

    def getresponse(self) -> _Response:
        return self._response

    def close(self) -> None:
        self.closed = True


def _connection_factory(
    responses: list[_Response], requests: list[_Request]
) -> Callable[[str, int, float], _QueuedConnection]:
    def factory(host: str, port: int, timeout: float) -> _QueuedConnection:
        assert (host, port, timeout) == ("api", 8080, 10)
        return _QueuedConnection(responses.pop(0), requests)

    return factory


def _json_response(status: int, body: object) -> _Response:
    return _Response(status, json.dumps(body).encode())


def _login_body(*, user: object | None = None, token: object | None = None, code: str = "1000") -> dict[str, object]:
    return {
        "code": code,
        "data": {
            "access_token": token if token is not None else "access-token",
            "user": user if user is not None else {"id": 42, "username": "admin", "is_superuser": True},
        },
    }


def _logout_body(*, code: str = "1000", revoked_count: object = 1) -> dict[str, object]:
    return {"code": code, "data": {"message": "登出成功", "revoked_count": revoked_count}}


def test_login_gate_uses_public_auth_contract_and_revokes_the_verification_session() -> None:
    from scripts.check_bootstrap_admin_login import LoginGateResult, check_bootstrap_admin_login

    requests: list[_Request] = []
    result = check_bootstrap_admin_login(
        "http://api:8080",
        "admin",
        "configured-secret",
        connection_factory=_connection_factory(
            [_json_response(200, _login_body()), _json_response(200, _logout_body())], requests
        ),
    )

    assert result == LoginGateResult(username="admin", user_id=42)
    assert requests[0].method == "POST"
    assert requests[0].path == "/api/v1/auth/login"
    assert json.loads(requests[0].body) == {"username": "admin", "password": "configured-secret"}
    assert requests[1].method == "POST"
    assert requests[1].path == "/api/v1/auth/logout"
    assert requests[1].headers["Authorization"] == "Bearer access-token"


@pytest.mark.parametrize(
    ("response", "message", "expects_logout"),
    [
        (_json_response(401, {"detail": "configured-secret"}), "login returned HTTP 401", False),
        (_json_response(200, _login_body(code="2000")), "login contract rejected", False),
        (_json_response(200, _login_body(token="")), "login response is missing an access token", False),
        (
            _json_response(200, _login_body(user={"id": 42, "username": "admin", "is_superuser": False})),
            "login user is not the configured superadministrator",
            True,
        ),
        (
            _json_response(200, _login_body(user={"id": 42, "username": "other", "is_superuser": True})),
            "login user is not the configured superadministrator",
            True,
        ),
        (
            _json_response(200, _login_body(user={"id": True, "username": "admin", "is_superuser": True})),
            "login response is missing a numeric user ID",
            True,
        ),
    ],
)
def test_login_gate_rejects_invalid_login_contract(response: _Response, message: str, expects_logout: bool) -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    requests: list[_Request] = []

    with pytest.raises(RuntimeError, match=message):
        check_bootstrap_admin_login(
            "http://api:8080/",
            "admin",
            "configured-secret",
            connection_factory=_connection_factory(
                [response, *([_json_response(200, _logout_body())] if expects_logout else [])], requests
            ),
        )

    assert [request.path for request in requests] == [
        "/api/v1/auth/login",
        *(["/api/v1/auth/logout"] if expects_logout else []),
    ]


def test_login_gate_rejects_a_login_body_over_256_kib_before_parsing() -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    requests: list[_Request] = []
    response = _Response(200, b"{" + b"x" * (256 * 1024))

    with pytest.raises(RuntimeError, match="response body exceeds 256 KiB"):
        check_bootstrap_admin_login(
            "http://api:8080",
            "admin",
            "configured-secret",
            connection_factory=_connection_factory([response], requests),
        )

    assert response.read_sizes == [256 * 1024 + 1]


def test_login_gate_rejects_malformed_login_json() -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    with pytest.raises(RuntimeError, match="response body is not valid JSON"):
        check_bootstrap_admin_login(
            "http://api:8080",
            "admin",
            "configured-secret",
            connection_factory=_connection_factory([_Response(200, b"not-json")], []),
        )


@pytest.mark.parametrize(
    ("logout_response", "message"),
    [
        (_json_response(500, {"detail": "refresh_token"}), "logout returned HTTP 500"),
        (_json_response(200, _logout_body(revoked_count=0)), "logout did not revoke the verification session"),
    ],
)
def test_login_gate_rejects_logout_failures(logout_response: _Response, message: str) -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    with pytest.raises(RuntimeError, match=message):
        check_bootstrap_admin_login(
            "http://api:8080",
            "admin",
            "configured-secret",
            connection_factory=_connection_factory([_json_response(200, _login_body()), logout_response], []),
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api:8080",
        "http://admin:configured-secret@api:8080",
        "http://api:8080/api",
        "http://api:8080?token=access-token",
        "http://api:8080#refresh_token",
    ],
)
def test_login_gate_rejects_non_origin_http_urls(base_url: str) -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    with pytest.raises(ValueError, match="base URL"):
        check_bootstrap_admin_login(base_url, "admin", "configured-secret")


def test_cli_accepts_only_configured_password_and_never_prints_credentials_or_responses(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import check_bootstrap_admin_login

    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "configured-secret")
    monkeypatch.setattr(
        check_bootstrap_admin_login,
        "check_bootstrap_admin_login",
        lambda *_args, **_kwargs: check_bootstrap_admin_login.LoginGateResult(username="admin", user_id=42),
    )

    assert check_bootstrap_admin_login.main(["--base-url", "http://api:8080"]) == 0
    captured = capsys.readouterr()

    assert captured.out == "ADMIN_LOGIN_GATE_OK username=admin user_id=42\n"
    assert captured.err == ""
    for prohibited in ("configured-secret", "access-token", "refresh_token", "Set-Cookie", '{"code"'):
        assert prohibited not in captured.out + captured.err


def test_load_credentials_preserves_configured_username_and_password_whitespace() -> None:
    from scripts.check_bootstrap_admin_login import _load_credentials

    username, password = _load_credentials(
        {
            "BOOTSTRAP_ADMIN_USERNAME": " admin ",
            "BOOTSTRAP_ADMIN_PASSWORD": "  StrongPassw0rd!  ",
        }
    )

    assert username == " admin "
    assert password == "  StrongPassw0rd!  "


@pytest.mark.parametrize("password", ["", "short"])
def test_cli_rejects_missing_or_short_password_without_leaking_it(
    password: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import check_bootstrap_admin_login

    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", password)

    assert check_bootstrap_admin_login.main(["--base-url", "http://api:8080"]) == 1
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "ADMIN_LOGIN_GATE_FAILED stage=configuration status=INVALID\n"
    for prohibited in ("access-token", "refresh_token", "Set-Cookie", '{"code"'):
        assert prohibited not in captured.out + captured.err
    if password:
        assert password not in captured.out + captured.err


def test_cli_reports_only_login_http_classification_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import check_bootstrap_admin_login

    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "configured-secret")

    def rejected(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("login returned HTTP 401 with configured-secret and access-token")

    monkeypatch.setattr(check_bootstrap_admin_login, "check_bootstrap_admin_login", rejected)

    assert check_bootstrap_admin_login.main(["--base-url", "http://api:8080"]) == 1
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "ADMIN_LOGIN_GATE_FAILED stage=login status=HTTP_401\n"
    for prohibited in ("configured-secret", "access-token", "refresh_token", "Set-Cookie", '{"code"'):
        assert prohibited not in captured.out + captured.err
