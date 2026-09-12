#!/usr/bin/env python3
"""输出发布静默门禁的 canonical JSON 与机器退出码。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, TextIO

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.app.runtime.orchestration.repositories.release_operational_readiness_repository import (  # noqa: E402
    ReleaseOperationalReadinessRepository,
)
from src.app.runtime.orchestration.services.query.release_operational_readiness_service import (  # noqa: E402
    ReleaseOperationalReadinessService,
)

EXIT_CODES = {"READY": 0, "BLOCK": 2, "WAIT_DRAIN": 3}
WORKLINE_RETIREMENT_REVISION = "93deacda8c9c"


def _inbound_owner_column_for_revision(current_revision: str) -> str:
    script = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini")))
    script.get_revision(current_revision)
    if current_revision == WORKLINE_RETIREMENT_REVISION:
        return "workline_id"
    try:
        list(script.iterate_revisions(WORKLINE_RETIREMENT_REVISION, current_revision))
    except RangeNotAncestorError:
        try:
            list(script.iterate_revisions(current_revision, WORKLINE_RETIREMENT_REVISION))
        except RangeNotAncestorError as exc:
            raise ValueError("database revision is outside the supported migration lineage") from exc
        return "workline_id"
    return "line_run_epoch_id"


_ERROR_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SQLSTATE_RE = re.compile(r"^[0-9A-Z]{5}$")


def _safe_error_type(error: BaseException) -> str:
    name = type(error).__name__
    return name if _ERROR_TYPE_RE.fullmatch(name) else "UnknownError"


def _safe_diagnostic_attr(candidate: object, name: str) -> object | None:
    try:
        return getattr(candidate, name, None)
    except Exception:
        return None


def _error_diagnostic(error: BaseException) -> str:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)

    sqlstate = "none"
    for candidate in [*chain, *(_safe_diagnostic_attr(item, "orig") for item in chain)]:
        if candidate is None:
            continue
        raw_sqlstate = _safe_diagnostic_attr(candidate, "sqlstate") or _safe_diagnostic_attr(candidate, "pgcode")
        if isinstance(raw_sqlstate, str) and _SQLSTATE_RE.fullmatch(raw_sqlstate.upper()):
            sqlstate = raw_sqlstate.upper()
            break

    return (
        "RELEASE_OPERATIONAL_READINESS_QUERY_FAILED "
        f"error_type={_safe_error_type(chain[0])} "
        f"cause_type={_safe_error_type(chain[-1])} "
        f"sqlstate={sqlstate}\n"
    )


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
            readiness_service = service
            if readiness_service is None:
                database_heads = [value.strip() for value in os.environ.get("DATABASE_HEADS", "").splitlines() if value]
                if len(database_heads) > 1:
                    raise ValueError("multiple database heads are not supported")
                owner_column = (
                    _inbound_owner_column_for_revision(database_heads[0]) if database_heads else "workline_id"
                )
                readiness_service = ReleaseOperationalReadinessService(
                    repository=ReleaseOperationalReadinessRepository(inbound_owner_column=owner_column)
                )
            async with session_factory() as db:
                result = await readiness_service.check(db)  # type: ignore[attr-defined]
            payload = _canonical_payload(result)
        finally:
            if owns_database:
                from src.database import db as db_module

                await db_module.close_db()
        stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return EXIT_CODES[str(payload["state"])]
    except Exception as exc:
        stderr.write(_error_diagnostic(exc))
        return 1


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
