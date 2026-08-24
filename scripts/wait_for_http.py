from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from http.client import HTTPException
from time import sleep
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen

UrlOpener = Callable[..., AbstractContextManager[Any]]
Sleeper = Callable[[float], None]


def wait_for_http(
    url: str,
    *,
    attempts: int,
    timeout_seconds: float,
    interval_seconds: float,
    opener: UrlOpener = urlopen,
    sleeper: Sleeper = sleep,
) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must be an absolute HTTP URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("url must not contain query or fragment")
    if attempts <= 0 or timeout_seconds <= 0 or interval_seconds < 0:
        raise ValueError("invalid retry configuration")

    last_error = "not ready"
    for attempt in range(1, attempts + 1):
        try:
            with opener(url, timeout=timeout_seconds) as response:
                if 200 <= response.status < 400:
                    return
                last_error = f"HTTP {response.status}"
        except HTTPError as exc:
            if exc.fp is not None:
                exc.close()
            if 200 <= exc.code < 400:
                return
            last_error = f"HTTP {exc.code}"
        except (HTTPException, OSError) as exc:
            last_error = type(exc).__name__
        if attempt < attempts:
            sleeper(interval_seconds)
    raise RuntimeError(f"HTTP endpoint did not become ready after {attempts} attempts: {last_error}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wait for an HTTP endpoint to become ready")
    parser.add_argument("--url", required=True)
    parser.add_argument("--attempts", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--interval-seconds", type=float, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        wait_for_http(
            args.url,
            attempts=args.attempts,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"HTTP readiness failed: {exc}", file=sys.stderr)
        return 1
    print("HTTP endpoint ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
