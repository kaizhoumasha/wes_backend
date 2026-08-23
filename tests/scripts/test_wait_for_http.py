from __future__ import annotations

import subprocess
import sys
from urllib.error import URLError

import pytest

from scripts.wait_for_http import wait_for_http


class _Response:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_wait_for_http_retries_until_success() -> None:
    probes: list[str] = []
    sleeps: list[float] = []

    def opener(url: str, *, timeout: float) -> _Response:
        probes.append(f"{url}|{timeout}")
        if len(probes) < 3:
            raise URLError("not ready")
        return _Response(200)

    wait_for_http(
        "http://127.0.0.1/health",
        attempts=3,
        timeout_seconds=2,
        interval_seconds=0.1,
        opener=opener,
        sleeper=sleeps.append,
    )

    assert len(probes) == 3
    assert sleeps == [0.1, 0.1]


def test_wait_for_http_accepts_success_status_range() -> None:
    for status in (200, 302, 399):
        wait_for_http(
            "https://127.0.0.1/health",
            attempts=1,
            timeout_seconds=1,
            interval_seconds=0,
            opener=lambda _url, *, timeout, status=status: _Response(status),
        )


def test_wait_for_http_retries_persistent_failure_status() -> None:
    probes: list[float] = []
    sleeps: list[float] = []

    with pytest.raises(RuntimeError, match="after 3 attempts: HTTP 503"):
        wait_for_http(
            "http://127.0.0.1/health",
            attempts=3,
            timeout_seconds=2,
            interval_seconds=0.1,
            opener=lambda _url, *, timeout: probes.append(timeout) or _Response(503),
            sleeper=sleeps.append,
        )

    assert len(probes) == 3
    assert sleeps == [0.1, 0.1]


def test_wait_for_http_reports_exhausted_url_error() -> None:
    probes: list[str] = []

    with pytest.raises(RuntimeError, match="after 2 attempts: URLError"):
        wait_for_http(
            "http://127.0.0.1/health",
            attempts=2,
            timeout_seconds=1,
            interval_seconds=0,
            opener=lambda url, *, timeout: probes.append(url) or (_ for _ in ()).throw(URLError("down")),
        )

    assert probes == ["http://127.0.0.1/health", "http://127.0.0.1/health"]


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://admin:secret@127.0.0.1/health", "must not contain credentials"),
        ("ftp://127.0.0.1/health", "absolute HTTP URL"),
        ("/health", "absolute HTTP URL"),
        ("http://127.0.0.1/health?ready=1", "must not contain query or fragment"),
        ("http://127.0.0.1/health#ready", "must not contain query or fragment"),
    ],
)
def test_wait_for_http_rejects_invalid_urls(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        wait_for_http(url, attempts=1, timeout_seconds=1, interval_seconds=0)


@pytest.mark.parametrize(
    ("attempts", "timeout_seconds", "interval_seconds"),
    [(0, 1, 0), (-1, 1, 0), (1, 0, 0), (1, -1, 0), (1, 1, -1)],
)
def test_wait_for_http_rejects_invalid_retry_configuration(
    attempts: int, timeout_seconds: float, interval_seconds: float
) -> None:
    with pytest.raises(ValueError, match="invalid retry configuration"):
        wait_for_http(
            "http://127.0.0.1/health",
            attempts=attempts,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
        )


def test_cli_does_not_echo_credentials_in_error() -> None:
    url = "http://admin:secret@127.0.0.1/health"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/wait_for_http.py",
            "--url",
            url,
            "--attempts",
            "1",
            "--timeout-seconds",
            "1",
            "--interval-seconds",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "admin:secret" not in result.stderr
    assert "127.0.0.1" not in result.stderr
    assert len(result.stderr.splitlines()) == 1
