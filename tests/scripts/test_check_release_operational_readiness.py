"""发布静默门禁 CLI 的机器输出、退出码和脱敏合同。"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_release_operational_readiness.py"


def _load_script():
    assert SCRIPT_PATH.is_file(), "release readiness CLI is not implemented"
    spec = importlib.util.spec_from_file_location("check_release_operational_readiness", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SessionContext:
    async def __aenter__(self) -> object:
        return SimpleNamespace()

    async def __aexit__(self, *_args: object) -> None:
        return None


def _session_factory() -> _SessionContext:
    return _SessionContext()


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "exit_code"), [("READY", 0), ("BLOCK", 2), ("WAIT_DRAIN", 3)])
async def test_cli_emits_one_canonical_json_line_and_state_exit_code(state: str, exit_code: int) -> None:
    module = _load_script()
    result = SimpleNamespace(
        state=state,
        counts={"device_command_wait_drain": 0, "device_command_block": 0},
        wait_drain_total=0,
        block_total=0,
        generated_at="2026-08-29T15:00:00+00:00",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    actual = await module.run(
        service=SimpleNamespace(check=AsyncMock(return_value=result)),
        session_factory=_session_factory,
        stdout=stdout,
        stderr=stderr,
    )

    assert actual == exit_code
    assert stderr.getvalue() == ""
    assert stdout.getvalue().count("\n") == 1
    payload = json.loads(stdout.getvalue())
    assert payload == {
        "block_total": 0,
        "counts": {"device_command_block": 0, "device_command_wait_drain": 0},
        "generated_at": "2026-08-29T15:00:00+00:00",
        "state": state,
        "wait_drain_total": 0,
    }
    assert stdout.getvalue() == json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


@pytest.mark.asyncio
async def test_cli_query_error_exits_one_and_redacts_sensitive_detail() -> None:
    module = _load_script()
    stdout = io.StringIO()
    stderr = io.StringIO()
    error = RuntimeError("postgresql://operator:secret@db payload={private-device-parameter}")

    actual = await module.run(
        service=SimpleNamespace(check=AsyncMock(side_effect=error)),
        session_factory=_session_factory,
        stdout=stdout,
        stderr=stderr,
    )

    assert actual == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue().count("\n") == 1
    assert "secret" not in stderr.getvalue()
    assert "private-device-parameter" not in stderr.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_result",
    [
        SimpleNamespace(
            state="UNKNOWN",
            counts={},
            wait_drain_total=0,
            block_total=0,
            generated_at="2026-08-29T15:00:00+00:00",
        ),
        SimpleNamespace(
            state="READY",
            counts={"device_command_invalid": 1},
            wait_drain_total=0,
            block_total=0,
            generated_at="2026-08-29T15:00:00+00:00",
        ),
    ],
)
async def test_cli_invalid_service_result_exits_one_and_emits_no_payload(invalid_result: SimpleNamespace) -> None:
    module = _load_script()
    stdout = io.StringIO()
    stderr = io.StringIO()

    actual = await module.run(
        service=SimpleNamespace(check=AsyncMock(return_value=invalid_result)),
        session_factory=_session_factory,
        stdout=stdout,
        stderr=stderr,
    )

    assert actual == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue().count("\n") == 1
    assert "UNKNOWN" not in stderr.getvalue()
    assert "device_command_invalid" not in stderr.getvalue()
