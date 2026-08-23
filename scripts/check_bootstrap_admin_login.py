"""验证 bootstrap 超级管理员可登录并仅登出本次验证会话。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPResponse
from urllib.parse import urlsplit

ConnectionFactory = Callable[[str, int, float], HTTPConnection]
_MAX_RESPONSE_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class LoginGateResult:
    username: str
    user_id: int


def _read_json_response(response: HTTPResponse) -> dict[str, object]:
    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("response body exceeds 256 KiB")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("response body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("response JSON must be an object")  # noqa: TRY004
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
            raise RuntimeError(f"login returned HTTP {response.status}")
        envelope = _read_json_response(response)
    finally:
        connection.close()

    data = envelope.get("data")
    if envelope.get("code") != "1000" or not isinstance(data, dict):
        raise RuntimeError("login contract rejected")
    user = data.get("user")
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("login response is missing an access token")

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
            raise RuntimeError(f"logout returned HTTP {logout_response.status}")
        logout_envelope = _read_json_response(logout_response)
    finally:
        logout_connection.close()
    logout_data = logout_envelope.get("data")
    if (
        logout_envelope.get("code") != "1000"
        or not isinstance(logout_data, dict)
        or logout_data.get("revoked_count") != 1
    ):
        raise RuntimeError("logout did not revoke the verification session")

    if not isinstance(user, dict) or user.get("username") != username or user.get("is_superuser") is not True:
        raise RuntimeError("login user is not the configured superadministrator")
    user_id = user.get("id")
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise RuntimeError("login response is missing a numeric user ID")  # noqa: TRY004
    return LoginGateResult(username=username, user_id=user_id)


def _load_credentials(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    values = env or os.environ
    username = values.get("BOOTSTRAP_ADMIN_USERNAME", "")
    password = values.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not username or not password or len(password) < 8:
        raise ValueError("bootstrap administrator credentials are invalid")
    return username, password


def _classify_failure(error: RuntimeError) -> tuple[str, str]:
    message = str(error)
    for stage in ("login", "logout"):
        match = re.fullmatch(rf"{stage} returned HTTP (\d{{3}})(?: .*)?", message)
        if match:
            return stage, f"HTTP_{match.group(1)}"
    return ("logout" if message.startswith("logout") else "login"), "CONTRACT_REJECTED"


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
    except OSError:
        print("ADMIN_LOGIN_GATE_FAILED stage=login status=CONNECTION_ERROR", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        stage, status = _classify_failure(exc)
        print(f"ADMIN_LOGIN_GATE_FAILED stage={stage} status={status}", file=sys.stderr)
        return 1

    print(f"ADMIN_LOGIN_GATE_OK username={result.username} user_id={result.user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
