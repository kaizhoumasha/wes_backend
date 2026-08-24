"""验证 bootstrap 超级管理员可登录并仅登出本次验证会话。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException, HTTPResponse
from urllib.parse import urlsplit

ConnectionFactory = Callable[[str, int, float], HTTPConnection]
_MAX_RESPONSE_BYTES = 256 * 1024


class _LoginGateFailure(RuntimeError):
    def __init__(self, stage: str, status: str) -> None:
        super().__init__(f"{stage}:{status}")
        self.stage = stage
        self.status = status


@dataclass(frozen=True, slots=True)
class LoginGateResult:
    username: str
    user_id: int


def _read_json_response(response: HTTPResponse, *, stage: str) -> dict[str, object]:
    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise _LoginGateFailure(stage, "CONTRACT_REJECTED")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _LoginGateFailure(stage, "CONTRACT_REJECTED") from None
    if not isinstance(payload, dict):
        raise _LoginGateFailure(stage, "CONTRACT_REJECTED")
    return payload


def check_bootstrap_admin_login(
    base_url: str,
    username: str,
    password: str,
    *,
    connection_factory: ConnectionFactory = HTTPConnection,
) -> LoginGateResult:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("base URL must be an absolute HTTP origin")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, query, or fragment")

    try:
        connection = connection_factory(parsed.hostname, parsed.port or 80, 10)
        try:
            connection.request(
                "POST",
                "/api/v1/auth/login",
                body=json.dumps({"username": username, "password": password}, separators=(",", ":")).encode(),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                response.read(_MAX_RESPONSE_BYTES + 1)
                raise _LoginGateFailure("login", f"HTTP_{response.status}")
            envelope = _read_json_response(response, stage="login")
        finally:
            connection.close()
    except _LoginGateFailure:
        raise
    except HTTPException:
        raise _LoginGateFailure("login", "PROTOCOL_ERROR") from None
    except OSError:
        raise _LoginGateFailure("login", "CONNECTION_ERROR") from None

    data = envelope.get("data")
    if envelope.get("code") != "1000" or not isinstance(data, dict):
        raise _LoginGateFailure("login", "CONTRACT_REJECTED")
    user = data.get("user")
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise _LoginGateFailure("login", "CONTRACT_REJECTED")

    try:
        logout_connection = connection_factory(parsed.hostname, parsed.port or 80, 10)
        try:
            logout_connection.request(
                "POST",
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )
            logout_response = logout_connection.getresponse()
            if logout_response.status != 200:
                logout_response.read(_MAX_RESPONSE_BYTES + 1)
                raise _LoginGateFailure("logout", f"HTTP_{logout_response.status}")
            logout_envelope = _read_json_response(logout_response, stage="logout")
        finally:
            logout_connection.close()
    except _LoginGateFailure:
        raise
    except HTTPException:
        raise _LoginGateFailure("logout", "PROTOCOL_ERROR") from None
    except OSError:
        raise _LoginGateFailure("logout", "CONNECTION_ERROR") from None
    logout_data = logout_envelope.get("data")
    if (
        logout_envelope.get("code") != "1000"
        or not isinstance(logout_data, dict)
        or logout_data.get("revoked_count") != 1
    ):
        raise _LoginGateFailure("logout", "CONTRACT_REJECTED")

    if not isinstance(user, dict) or user.get("username") != username or user.get("is_superuser") is not True:
        raise _LoginGateFailure("login", "CONTRACT_REJECTED")
    user_id = user.get("id")
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise _LoginGateFailure("login", "CONTRACT_REJECTED")
    return LoginGateResult(username=username, user_id=user_id)


def _load_credentials(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    values = os.environ if env is None else env
    username = values.get("BOOTSTRAP_ADMIN_USERNAME", "")
    password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not username or not password or len(password) < 8:
        raise ValueError("bootstrap administrator credentials are invalid")
    return username, password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="New API container HTTP origin")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        username, password = _load_credentials()
        result = check_bootstrap_admin_login(args.base_url, username, password)
    except ValueError:
        print("ADMIN_LOGIN_GATE_FAILED stage=configuration status=INVALID", file=sys.stderr)
        return 1
    except _LoginGateFailure as exc:
        print(f"ADMIN_LOGIN_GATE_FAILED stage={exc.stage} status={exc.status}", file=sys.stderr)
        return 1

    print(f"ADMIN_LOGIN_GATE_OK username={result.username} user_id={result.user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
