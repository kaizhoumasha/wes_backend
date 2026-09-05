"""LineRunEpoch 生命周期与基础模型边界。"""

from datetime import datetime

import pytest

from src.app.execution.plugin_binding import PluginRuntimeBinding
from src.app.workline.epoch_activation import (
    LineRunEpochDeviceBindingInput,
    LineRunEpochPositionBindingInput,
)
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.models.line_run_epoch import LineRunEpoch, LineRunEpochDeviceBinding, LineRunEpochStatus
from src.app.workline.models.workline import LineType
from src.app.workline.services.line_run_epoch_service import ActiveLineRunEpochExistsError, LineRunEpochService


class FakeLineRunEpochRepository:
    """仅模拟 Service 所需的持久化端口，不复制数据库行为。"""

    def __init__(self) -> None:
        self.active_epoch: LineRunEpoch | None = None
        self.has_unclosed = False
        self.calls: list[str] = []

    async def get_active_for_workline(self, _db: object, workline_id: int) -> LineRunEpoch | None:
        self.calls.append(f"epoch-snapshot:{workline_id}")
        if self.active_epoch is not None and self.active_epoch.workline_id == workline_id:
            return self.active_epoch
        return None

    async def get_active_for_workline_for_update(self, _db: object, workline_id: int) -> LineRunEpoch | None:
        self.calls.append(f"epoch-row:{workline_id}")
        if self.active_epoch is not None and self.active_epoch.workline_id == workline_id:
            return self.active_epoch
        return None

    async def list_active_plugin_identities(self, _db: object) -> list[tuple[str, str]]:
        if self.active_epoch is None:
            return []
        return [(self.active_epoch.plugin_key, self.active_epoch.plugin_version)]

    async def add_complete_epoch(
        self,
        _db: object,
        epoch: LineRunEpoch,
        _device_bindings: tuple[LineRunEpochDeviceBindingInput, ...],
        _position_bindings: tuple[LineRunEpochPositionBindingInput, ...],
    ) -> LineRunEpoch:
        epoch.id = 11
        self.active_epoch = epoch
        return epoch

    async def close_epoch(self, _db: object, epoch: LineRunEpoch, *, closed_at: datetime) -> LineRunEpoch:
        self.calls.append(f"epoch-close:{epoch.id}")
        epoch.status = LineRunEpochStatus.CLOSED
        epoch.closed_at = closed_at
        self.active_epoch = None
        return epoch

    async def has_unclosed_for_epoch_for_update(self, _db: object, _line_run_epoch_id: int) -> bool:
        return self.has_unclosed


class FakePositionProjectionRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def lock_epoch_lifecycle(self, _db: object, line_run_epoch_id: int) -> None:
        self.calls.append(f"epoch-lock:{line_run_epoch_id}")

    async def delete_for_epoch(self, _db: object, line_run_epoch_id: int) -> None:
        self.calls.append(f"projection-delete:{line_run_epoch_id}")


def _service() -> tuple[LineRunEpochService, FakeLineRunEpochRepository]:
    repository = FakeLineRunEpochRepository()
    return (
        LineRunEpochService(
            repository=repository,
            projection_repository=FakePositionProjectionRepository(repository.calls),
        ),
        repository,
    )  # type: ignore[arg-type]


async def _activate(
    service: LineRunEpochService,
    *,
    epoch_code: str,
    workline_id: int = 1,
    plugin_version: str = "1.0.0",
    started_at: datetime = datetime(2026, 8, 13),
) -> LineRunEpoch:
    return await service.activate_epoch(
        object(),
        epoch_code=epoch_code,
        workline_id=workline_id,
        plugin_key="example_plugin",
        plugin_version=plugin_version,
        flow_mode="GENERIC_FLOW",
        configuration_snapshot={"mode": "GENERIC"},
        device_bindings=(),
        position_bindings=(),
        started_at=started_at,
    )


@pytest.mark.asyncio
async def test_same_workline_rejects_second_active_epoch() -> None:
    service, _ = _service()

    first = await _activate(service, epoch_code="EPOCH-LINE-01-0001")
    assert first.status == LineRunEpochStatus.ACTIVE
    with pytest.raises(ActiveLineRunEpochExistsError):
        await _activate(service, epoch_code="EPOCH-LINE-01-0002")


@pytest.mark.asyncio
async def test_close_active_epoch_allows_next_generation() -> None:
    service, repository = _service()
    first = await _activate(service, epoch_code="EPOCH-LINE-01-0001")
    repository.calls.clear()

    closed = await service.close_active_epoch(
        object(), workline_id=1, closed_at=datetime(2026, 8, 13, 0, 1), command_repository=repository
    )
    second = await _activate(
        service,
        epoch_code="EPOCH-LINE-01-0002",
        plugin_version="1.0.1",
        started_at=datetime(2026, 8, 13, 0, 1),
    )

    assert closed is first
    assert closed.status == LineRunEpochStatus.CLOSED
    assert closed.closed_at == datetime(2026, 8, 13, 0, 1)
    assert second.status == LineRunEpochStatus.ACTIVE
    assert repository.calls[:5] == [
        "epoch-snapshot:1",
        "epoch-lock:11",
        "epoch-row:1",
        "projection-delete:11",
        "epoch-close:11",
    ]


@pytest.mark.asyncio
async def test_close_epoch_rejects_commands_not_in_terminal_state() -> None:
    service, repository = _service()
    await _activate(service, epoch_code="EPOCH-LINE-01-0001")
    repository.has_unclosed = True

    with pytest.raises(ActiveLineRunEpochExistsError, match="unclosed"):
        await service.close_active_epoch(
            object(), workline_id=1, closed_at=datetime(2026, 8, 13, 0, 1), command_repository=repository
        )

    assert repository.active_epoch is not None


@pytest.mark.asyncio
async def test_execution_worker_accepts_an_active_epoch_with_the_installed_exact_version() -> None:
    service, _ = _service()
    await _activate(service, epoch_code="EPOCH-LINE-01-0001")
    plugin = InstalledWorkLinePlugin(
        display_name="Example",
        runtime_binding=PluginRuntimeBinding(
            plugin_key="example_plugin",
            plugin_version="1.0.0",
            handlers=(),
            fact_factory=object(),  # type: ignore[arg-type]
        ),
        start_plan_builder=object(),
        supported_line_types=(LineType.AUTO,),
    )

    await service.assert_execution_worker_startable(object(), plugins=(plugin,))


@pytest.mark.asyncio
async def test_execution_worker_rejects_an_active_epoch_whose_plugin_version_is_not_installed() -> None:
    service, _ = _service()
    await _activate(service, epoch_code="EPOCH-LINE-01-0001")

    with pytest.raises(ActiveLineRunEpochExistsError, match="not installed"):
        await service.assert_execution_worker_startable(object(), plugins=())


@pytest.mark.asyncio
async def test_execution_worker_rejects_same_plugin_key_with_a_different_version() -> None:
    service, _ = _service()
    await _activate(service, epoch_code="EPOCH-LINE-01-0001")
    plugin = InstalledWorkLinePlugin(
        display_name="Example",
        runtime_binding=PluginRuntimeBinding(
            plugin_key="example_plugin",
            plugin_version="2.0.0",
            handlers=(),
            fact_factory=object(),  # type: ignore[arg-type]
        ),
        start_plan_builder=object(),
        supported_line_types=(LineType.AUTO,),
    )

    with pytest.raises(ActiveLineRunEpochExistsError, match=r"example_plugin@1\.0\.0"):
        await service.assert_execution_worker_startable(object(), plugins=(plugin,))


@pytest.mark.asyncio
async def test_epoch_freezes_plugin_identity_flow_mode_and_configuration() -> None:
    service, _ = _service()

    epoch = await _activate(service, epoch_code="EPOCH-LINE-01-0001")

    assert epoch.plugin_key == "example_plugin"
    assert epoch.plugin_version == "1.0.0"
    assert epoch.flow_mode == "GENERIC_FLOW"
    assert epoch.configuration_snapshot_json == {"mode": "GENERIC"}
    assert "plugin_state" not in LineRunEpoch.model_fields


def test_epoch_device_binding_contains_only_generic_dispatch_invariants() -> None:
    expected = {
        "line_run_epoch_id",
        "device_id",
        "device_code",
        "device_role",
        "contract_key",
        "contract_version",
        "status_max_age_ms",
        "command_timeout_ms",
    }
    supplier_fields = {
        "ecs_version",
        "gateway_version",
        "device_model",
        "firmware_version",
        "clock_source",
        "retransmission_window",
        "evidence_retention",
    }

    assert expected <= LineRunEpochDeviceBinding.model_fields.keys()
    assert supplier_fields.isdisjoint(LineRunEpochDeviceBinding.model_fields)
