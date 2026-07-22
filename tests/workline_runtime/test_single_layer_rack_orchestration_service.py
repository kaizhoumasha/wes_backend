"""Single-layer rack orchestration station-claim regressions."""

from __future__ import annotations

from types import SimpleNamespace

from src.app.runtime.capabilities.material_flow.single_layer_rack_orchestration_service import (
    _is_active_station_claim_outbox,
)
from src.app.sys.models import SystemOutboxStatus


def test_station_claim_active_status_accepts_system_outbox_status_enum() -> None:
    """station claim 幂等冲突判断必须接受 SQLModel 返回的 Enum 状态。"""

    assert _is_active_station_claim_outbox(SimpleNamespace(status=SystemOutboxStatus.NEW, finished_at=None)) is True
    assert (
        _is_active_station_claim_outbox(SimpleNamespace(status=SystemOutboxStatus.RETRY_WAIT, finished_at=None)) is True
    )


def test_station_claim_does_not_treat_finished_retry_wait_as_active() -> None:
    assert (
        _is_active_station_claim_outbox(SimpleNamespace(status=SystemOutboxStatus.RETRY_WAIT, finished_at=object()))
        is False
    )
