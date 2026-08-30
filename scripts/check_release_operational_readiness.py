#!/usr/bin/env python3
"""输出发布静默门禁的 canonical JSON 与机器退出码。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, TextIO

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.app.runtime.orchestration.services.query.release_operational_readiness_service import (  # noqa: E402
    ReleaseOperationalReadinessService,
)

EXIT_CODES = {"READY": 0, "BLOCK": 2, "WAIT_DRAIN": 3}


def _canonical_payload(result: object) -> dict[str, object]:
    state = getattr(result, "state", None)
    raw_counts = getattr(result, "counts", None)
    wait_drain_total = getattr(result, "wait_drain_total", None)
    block_total = getattr(result, "block_total", None)
    generated_at = getattr(result, "generated_at", None)
    if state not in EXIT_CODES or not isinstance(raw_counts, dict) or not isinstance(generated_at, str):
        raise ValueError("invalid readiness result")
    if not isinstance(wait_drain_total, int) or isinstance(wait_drain_total, bool) or wait_drain_total < 0:
        raise ValueError("invalid readiness result")
    if not isinstance(block_total, int) or isinstance(block_total, bool) or block_total < 0:
        raise ValueError("invalid readiness result")
    counts: dict[str, int] = {}
    for key, value in raw_counts.items():
        if not isinstance(key, str) or not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("invalid readiness result")
        if key.endswith(("_unknown", "_invalid")) and value:
            raise ValueError("invalid readiness result")
        counts[key] = value
    if sum(value for key, value in counts.items() if key.endswith("_wait_drain")) != wait_drain_total:
        raise ValueError("invalid readiness result")
    if sum(value for key, value in counts.items() if key.endswith("_block")) != block_total:
        raise ValueError("invalid readiness result")
    return {
        "state": state,
        "counts": counts,
        "wait_drain_total": wait_drain_total,
        "block_total": block_total,
        "generated_at": generated_at,
    }


async def run(
    *,
    service: object | None = None,
    session_factory: Callable[[], Any] | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    owns_database = session_factory is None
    try:
        try:
            if session_factory is None:
                from src.database import db as db_module

                await db_module.init_db()
                if db_module.AsyncSessionLocal is None:
                    raise RuntimeError("database unavailable")
                session_factory = db_module.AsyncSessionLocal
            readiness_service = service or ReleaseOperationalReadinessService()
            async with session_factory() as db:
                result = await readiness_service.check(db)  # type: ignore[attr-defined]
            payload = _canonical_payload(result)
        finally:
            if owns_database:
                from src.database import db as db_module

                await db_module.close_db()
        stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return EXIT_CODES[str(payload["state"])]
    except Exception:
        stderr.write("RELEASE_OPERATIONAL_READINESS_QUERY_FAILED\n")
        return 1


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
