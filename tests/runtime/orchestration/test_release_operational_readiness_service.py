"""发布静默门禁 Service 的判定、输出与超时合同。"""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

COUNT_KEYS = (
    "device_command_wait_drain",
    "device_command_block",
    "device_command_unknown",
    "device_command_invalid",
    "transport_task_wait_drain",
    "transport_task_block",
    "transport_task_unknown",
    "transport_task_invalid",
    "inbound_evidence_wait_drain",
    "inbound_evidence_block",
    "inbound_evidence_unknown",
    "inbound_evidence_invalid",
    "wms_confirmation_wait_drain",
    "wms_confirmation_block",
    "wms_confirmation_unknown",
    "wms_confirmation_invalid",
)
FAIL_CLOSED_KEYS = tuple(key for key in COUNT_KEYS if key.endswith(("_unknown", "_invalid")))


def _service_module():
    return importlib.import_module("src.app.runtime.orchestration.services.query.release_operational_readiness_service")


def _counts(**overrides: int) -> SimpleNamespace:
    values = dict.fromkeys(COUNT_KEYS, 0)
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("counts", "expected_state", "wait_total", "block_total"),
    [
        (_counts(), "READY", 0, 0),
        (_counts(device_command_wait_drain=2, transport_task_wait_drain=3), "WAIT_DRAIN", 5, 0),
        (_counts(device_command_wait_drain=2, inbound_evidence_block=1), "BLOCK", 2, 1),
    ],
)
async def test_block_precedes_wait_drain_and_ready(
    counts: SimpleNamespace,
    expected_state: str,
    wait_total: int,
    block_total: int,
) -> None:
    module = _service_module()
    repository = SimpleNamespace(load_counts=AsyncMock(return_value=counts))

    result = await module.ReleaseOperationalReadinessService(repository=repository).check(SimpleNamespace())

    assert result.state == expected_state
    assert result.wait_drain_total == wait_total
    assert result.block_total == block_total
    assert set(result.counts) == set(COUNT_KEYS)
    assert result.generated_at.endswith("+00:00")


@pytest.mark.asyncio
@pytest.mark.parametrize("key", FAIL_CLOSED_KEYS)
async def test_every_unknown_or_invalid_category_fails_closed(key: str) -> None:
    module = _service_module()
    repository = SimpleNamespace(load_counts=AsyncMock(return_value=_counts(**{key: 1})))

    with pytest.raises(module.ReleaseOperationalReadinessQueryError):
        await module.ReleaseOperationalReadinessService(repository=repository).check(SimpleNamespace())


@pytest.mark.asyncio
@pytest.mark.parametrize("key", COUNT_KEYS)
async def test_every_category_rejects_negative_count(key: str) -> None:
    module = _service_module()
    repository = SimpleNamespace(load_counts=AsyncMock(return_value=_counts(**{key: -1})))

    with pytest.raises(module.ReleaseOperationalReadinessQueryError):
        await module.ReleaseOperationalReadinessService(repository=repository).check(SimpleNamespace())


@pytest.mark.asyncio
async def test_service_uses_ten_second_cancellation_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _service_module()
    assert module.READINESS_QUERY_TIMEOUT_SECONDS == 10
    cancelled = asyncio.Event()

    async def never_returns(_db: object) -> None:
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(module, "READINESS_QUERY_TIMEOUT_SECONDS", 0.01)
    repository = SimpleNamespace(load_counts=never_returns)

    with pytest.raises(module.ReleaseOperationalReadinessQueryError):
        await module.ReleaseOperationalReadinessService(repository=repository).check(SimpleNamespace())
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_repository_error_is_mapped_without_leaking_detail() -> None:
    module = _service_module()
    repository = SimpleNamespace(load_counts=AsyncMock(side_effect=RuntimeError("database failure secret payload-42")))

    with pytest.raises(module.ReleaseOperationalReadinessQueryError) as raised:
        await module.ReleaseOperationalReadinessService(repository=repository).check(SimpleNamespace())

    assert "secret" not in str(raised.value)
    assert "payload-42" not in str(raised.value)
