from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import BadStatusLine
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
    def __init__(self, status: int, body: bytes | BaseException) -> None:
        self.status = status
        self._body = body
        self.read_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class _QueuedConnection:
    def __init__(self, response: _Response | BaseException, requests: list[_Request]) -> None:
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
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response

    def close(self) -> None:
        self.closed = True


def _connection_factory(
    responses: list[_Response | BaseException], requests: list[_Request]
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


def _responses_for_stage(stage: str, failure: _Response | BaseException) -> list[_Response | BaseException]:
    if stage == "login":
        return [failure]
    return [_json_response(200, _login_body()), failure]


def _assert_stage_status(error: BaseException, stage: str, status: str) -> None:
    assert getattr(error, "stage", None) == stage
    assert getattr(error, "status", None) == status
    for prohibited in ("configured-secret", "server-secret", "response-secret", "access-token", "Set-Cookie"):
        assert prohibited not in str(error)


def _assert_cli_failure(
    error: BaseException,
    stage: str,
    status: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import check_bootstrap_admin_login

    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "configured-secret")

    def failed(*_args: object, **_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(check_bootstrap_admin_login, "check_bootstrap_admin_login", failed)

    assert check_bootstrap_admin_login.main(["--base-url", "http://api:8080"]) == 1
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == f"ADMIN_LOGIN_GATE_FAILED stage={stage} status={status}\n"
    assert len(captured.err) <= 96
    for prohibited in (
        "configured-secret",
        "server-secret",
        "response-secret",
        "access-token",
        "refresh_token",
        "Set-Cookie",
        '{"code"',
    ):
        assert prohibited not in captured.err


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
    ("response", "status", "expects_logout"),
    [
        (_json_response(401, {"detail": "configured-secret"}), "HTTP_401", False),
        (_json_response(200, _login_body(code="2000")), "CONTRACT_REJECTED", False),
        (_json_response(200, _login_body(token="")), "CONTRACT_REJECTED", False),
        (
            _json_response(200, _login_body(user={"id": 42, "username": "admin", "is_superuser": False})),
            "CONTRACT_REJECTED",
            True,
        ),
        (
            _json_response(200, _login_body(user={"id": 42, "username": "other", "is_superuser": True})),
            "CONTRACT_REJECTED",
            True,
        ),
        (
            _json_response(200, _login_body(user={"id": True, "username": "admin", "is_superuser": True})),
            "CONTRACT_REJECTED",
            True,
        ),
    ],
)
def test_login_gate_rejects_invalid_login_contract(
    response: _Response,
    status: str,
    expects_logout: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    requests: list[_Request] = []

    with pytest.raises(RuntimeError) as caught:
        check_bootstrap_admin_login(
            "http://api:8080/",
            "admin",
            "configured-secret",
            connection_factory=_connection_factory(
                [response, *([_json_response(200, _logout_body())] if expects_logout else [])], requests
            ),
        )

    _assert_stage_status(caught.value, "login", status)
    _assert_cli_failure(caught.value, "login", status, monkeypatch, capsys)
    assert [request.path for request in requests] == [
        "/api/v1/auth/login",
        *(["/api/v1/auth/logout"] if expects_logout else []),
    ]


def test_login_gate_rejects_a_login_body_over_256_kib_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    requests: list[_Request] = []
    response = _Response(200, b"{" + b"x" * (256 * 1024))

    with pytest.raises(RuntimeError) as caught:
        check_bootstrap_admin_login(
            "http://api:8080",
            "admin",
            "configured-secret",
            connection_factory=_connection_factory([response], requests),
        )

    _assert_stage_status(caught.value, "login", "CONTRACT_REJECTED")
    _assert_cli_failure(caught.value, "login", "CONTRACT_REJECTED", monkeypatch, capsys)
    assert response.read_sizes == [256 * 1024 + 1]


def test_login_gate_rejects_malformed_login_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    with pytest.raises(RuntimeError) as caught:
        check_bootstrap_admin_login(
            "http://api:8080",
            "admin",
            "configured-secret",
            connection_factory=_connection_factory([_Response(200, b"not-json")], []),
        )

    _assert_stage_status(caught.value, "login", "CONTRACT_REJECTED")
    _assert_cli_failure(caught.value, "login", "CONTRACT_REJECTED", monkeypatch, capsys)


@pytest.mark.parametrize(
    ("logout_response", "status"),
    [
        (_json_response(500, {"detail": "refresh_token"}), "HTTP_500"),
        (_json_response(200, _logout_body(revoked_count=0)), "CONTRACT_REJECTED"),
    ],
)
def test_login_gate_rejects_logout_failures(
    logout_response: _Response,
    status: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    with pytest.raises(RuntimeError) as caught:
        check_bootstrap_admin_login(
            "http://api:8080",
            "admin",
            "configured-secret",
            connection_factory=_connection_factory([_json_response(200, _login_body()), logout_response], []),
        )

    _assert_stage_status(caught.value, "logout", status)
    _assert_cli_failure(caught.value, "logout", status, monkeypatch, capsys)


@pytest.mark.parametrize(
    ("stage", "failure", "status"),
    [
        ("login", OSError("configured-secret response-secret"), "CONNECTION_ERROR"),
        ("logout", OSError("configured-secret response-secret"), "CONNECTION_ERROR"),
        ("login", BadStatusLine("Set-Cookie: access-token=server-secret"), "PROTOCOL_ERROR"),
        ("logout", BadStatusLine("Set-Cookie: access-token=server-secret"), "PROTOCOL_ERROR"),
        ("login", _Response(200, OSError("response-secret")), "CONNECTION_ERROR"),
        ("logout", _Response(200, OSError("response-secret")), "CONNECTION_ERROR"),
    ],
)
def test_login_gate_classifies_transport_protocol_and_read_failures_by_stage(
    stage: str,
    failure: _Response | BaseException,
    status: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    with pytest.raises(RuntimeError) as caught:
        check_bootstrap_admin_login(
            "http://api:8080",
            "admin",
            "configured-secret",
            connection_factory=_connection_factory(_responses_for_stage(stage, failure), []),
        )

    _assert_stage_status(caught.value, stage, status)
    _assert_cli_failure(caught.value, stage, status, monkeypatch, capsys)


@pytest.mark.parametrize(
    "logout_response",
    [
        _Response(200, b"not-json"),
        _Response(200, b"{" + b"x" * (256 * 1024)),
    ],
)
def test_login_gate_classifies_malformed_and_oversize_logout_bodies_as_logout(
    logout_response: _Response,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.check_bootstrap_admin_login import check_bootstrap_admin_login

    with pytest.raises(RuntimeError) as caught:
        check_bootstrap_admin_login(
            "http://api:8080",
            "admin",
            "configured-secret",
            connection_factory=_connection_factory([_json_response(200, _login_body()), logout_response], []),
        )

    _assert_stage_status(caught.value, "logout", "CONTRACT_REJECTED")
    _assert_cli_failure(caught.value, "logout", "CONTRACT_REJECTED", monkeypatch, capsys)


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


def test_load_credentials_treats_an_empty_mapping_as_explicit_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.check_bootstrap_admin_login import _load_credentials

    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "environment-admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "environment-secret")

    with pytest.raises(ValueError, match="credentials are invalid"):
        _load_credentials({})


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
