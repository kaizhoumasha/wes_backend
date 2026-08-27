"""DeviceCommand 应用端口的创建边界。"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from src.app.device.contracts import DeviceCommandRequest, EcsDeviceStatus
from src.app.device.event_block_contracts import EventDebugCommandBlocked, EventDebugCommandReady
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.services.device_command_service import (
    DeviceCommandCapacityError,
    DeviceCommandDeadlineError,
    DeviceCommandIdentityConflictError,
    DeviceCommandService,
    DeviceNotFoundError,
)
from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
)
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding


class FakeBegin(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSessionFactory:
    def __init__(self) -> None:
        self.begin_count = 0

    def begin(self) -> FakeBegin:
        self.begin_count += 1
        return FakeBegin()


class FakeCommandRepository:
    def __init__(self) -> None:
        self.unclosed: dict[str, DeviceCommand] = {}
        self.created: list[DeviceCommand] = []

    async def lock_creation_for_device(self, _db: object, _device_code: str) -> None:
        return None

    async def lock_manual_debug_identity(self, _db: object, _client_request_id: str) -> None:
        return None

    async def get_unclosed_for_device_for_update(self, _db: object, device_code: str) -> DeviceCommand | None:
        return self.unclosed.get(device_code)

    async def get_by_execution_ref_for_update(
        self,
        _db: object,
        *,
        line_run_epoch_id: int,
        device_code: str,
        execution_ref_type: str,
        execution_ref_id: str,
    ) -> DeviceCommand | None:
        return next(
            (
                command
                for command in self.created
                if command.line_run_epoch_id == line_run_epoch_id
                and command.device_code == device_code
                and command.execution_ref_type == execution_ref_type
                and command.execution_ref_id == execution_ref_id
            ),
            None,
        )

    async def get_manual_debug_by_client_request_id_for_update(
        self,
        _db: object,
        client_request_id: str,
    ) -> DeviceCommand | None:
        return next(
            (
                command
                for command in self.created
                if command.execution_ref_type == "MANUAL_DEBUG" and command.execution_ref_id == client_request_id
            ),
            None,
        )

    async def add(self, _db: object, command: DeviceCommand) -> DeviceCommand:
        command.id = len(self.created) + 1
        self.created.append(command)
        self.unclosed[command.device_code] = command
        return command

    async def get_by_command_code(
        self,
        _db: object,
        command_code: str,
        *,
        for_update: bool = False,
    ) -> DeviceCommand | None:
        del for_update
        return next((command for command in self.created if command.command_code == command_code), None)


class FakeEpochRepository:
    def __init__(self, bindings: dict[tuple[int, str], LineRunEpochDeviceBinding]) -> None:
        self.bindings = bindings

    async def get_binding_for_command_creation(
        self,
        _db: object,
        *,
        line_run_epoch_id: int,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None:
        return self.bindings.get((line_run_epoch_id, device_code))


class FakeEvidenceRepository:
    def __init__(self, evidence: InboundEvidence | None) -> None:
        self.evidence = evidence

    async def get_device_result_for_command(self, _db: object, command_code: str) -> InboundEvidence | None:
        if self.evidence is not None and self.evidence.command_code == command_code:
            return self.evidence
        return None


def _ecs_status(
    device_code: str,
    *,
    is_online: bool = True,
    supported_commands: tuple[str, ...] = ("PICK_AND_PUT",),
) -> EcsDeviceStatus:
    return EcsDeviceStatus.model_validate(
        {
            "device": {
                "device_code": device_code,
                "device_name": device_code,
                "device_type": "ROBOTIC_ARM",
                "role": "PLACEMENT_DEVICE",
                "supported_commands": supported_commands,
                "supported_events": [],
            },
            "state": {
                "device_code": device_code,
                "mode": "AUTO",
                "status": "IDLE",
                "is_online": is_online,
                "current_command_code": None,
                "scenario": "success",
                "updated_at": 1_787_475_600_000,
            },
        }
    )


class FakeAdapter:
    def __init__(self, statuses: tuple[EcsDeviceStatus, ...] | None = None) -> None:
        self.statuses = statuses
        self.fetch_statuses_calls = 0
        self.fetch_status_calls: list[str] = []

    async def fetch_statuses(self) -> tuple[EcsDeviceStatus, ...]:
        self.fetch_statuses_calls += 1
        return self.statuses or (
            _ecs_status("RS-MOCK-PLACEMENT-01"),
            _ecs_status("RS-MOCK-OFFLINE-01", is_online=False),
        )

    async def fetch_status(self, device_code: str) -> EcsDeviceStatus:
        self.fetch_status_calls.append(device_code)
        return _ecs_status(device_code)


class FakeAdapterProvider:
    def __init__(self, adapter: FakeAdapter | None = None) -> None:
        self.adapter = adapter or FakeAdapter()
        self.requested: list[str] = []

    async def get_adapter(self, endpoint_base_url: str) -> FakeAdapter:
        self.requested.append(endpoint_base_url)
        return self.adapter


def _binding(device_code: str = "ARM-01") -> LineRunEpochDeviceBinding:
    return LineRunEpochDeviceBinding(
        id=21,
        line_run_epoch_id=11,
        device_id=7,
        device_code=device_code,
        device_role="PLACEMENT_DEVICE",
        endpoint_base_url="http://ecs-command:8080",
        contract_key="arm.pick",
        contract_version="2.0",
        status_max_age_ms=1_000,
        command_timeout_ms=30_000,
    )


def _request(device_code: str = "ARM-01") -> DeviceCommandRequest:
    return DeviceCommandRequest(
        device_code=device_code,
        line_run_epoch_id=11,
        execution_ref_type="MATERIAL_EXECUTION",
        execution_ref_id="EXEC-001",
        material_execution_id=21,
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        params={"source_location": "STATION-A"},
        deadline_at=datetime(2026, 8, 13, 0, 0, 30),
        trace_id="TRACE-001",
    )


def _service(*bindings: LineRunEpochDeviceBinding) -> tuple[DeviceCommandService, FakeCommandRepository]:
    command_repository = FakeCommandRepository()
    epoch_repository = FakeEpochRepository(
        {(binding.line_run_epoch_id, binding.device_code): binding for binding in bindings}
    )
    service = DeviceCommandService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        command_repository=command_repository,  # type: ignore[arg-type]
        epoch_repository=epoch_repository,  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13),
    )
    return service, command_repository


@pytest.mark.asyncio
async def test_same_session_creation_does_not_open_an_independent_transaction() -> None:
    binding = _binding()
    command_repository = FakeCommandRepository()
    session_factory = FakeSessionFactory()
    service = DeviceCommandService(
        session_factory=session_factory,  # type: ignore[arg-type]
        command_repository=command_repository,  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository({(binding.line_run_epoch_id, binding.device_code): binding}),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13),
    )

    handle = await service.create_command_in_session(object(), _request())

    assert handle.command_code == command_repository.created[0].command_code
    assert session_factory.begin_count == 0


@pytest.mark.asyncio
async def test_unbound_device_fails_before_creating_command() -> None:
    service, repository = _service()

    with pytest.raises(DeviceNotFoundError):
        await service.create_command(_request())

    assert repository.created == []


@pytest.mark.asyncio
async def test_same_device_rejects_second_unclosed_command() -> None:
    service, _ = _service(_binding())

    await service.create_command(_request())

    with pytest.raises(DeviceCommandCapacityError):
        await service.create_command(replace(_request(), execution_ref_id="EXEC-002"))


@pytest.mark.asyncio
async def test_same_execution_identity_and_payload_returns_original_handle() -> None:
    service, repository = _service(_binding())

    first = await service.create_command(_request())
    duplicate = await service.create_command(replace(_request(), trace_id="TRACE-RETRY"))

    assert duplicate == first
    assert len(repository.created) == 1


@pytest.mark.asyncio
async def test_same_execution_identity_with_different_payload_is_conflict() -> None:
    service, repository = _service(_binding())
    await service.create_command(_request())

    with pytest.raises(DeviceCommandIdentityConflictError):
        await service.create_command(replace(_request(), params={"source_location": "STATION-B"}))

    assert len(repository.created) == 1


@pytest.mark.asyncio
async def test_same_execution_identity_with_different_material_execution_is_conflict() -> None:
    service, repository = _service(_binding())
    await service.create_command(_request())

    with pytest.raises(DeviceCommandIdentityConflictError):
        await service.create_command(replace(_request(), material_execution_id=22))

    assert len(repository.created) == 1


@pytest.mark.asyncio
async def test_different_devices_create_independent_commands() -> None:
    service, repository = _service(_binding("ARM-01"), _binding("ARM-02"))

    first = await service.create_command(_request("ARM-01"))
    second = await service.create_command(_request("ARM-02"))

    assert first.command_code != second.command_code
    assert {command.device_code for command in repository.created} == {"ARM-01", "ARM-02"}
    assert all(len(command.payload_digest) == 64 for command in repository.created)


@pytest.mark.asyncio
async def test_deadline_cannot_exceed_frozen_binding_timeout() -> None:
    service, repository = _service(_binding())

    with pytest.raises(DeviceCommandDeadlineError, match="deadline"):
        await service.create_command(replace(_request(), deadline_at=datetime(2026, 8, 13, 0, 0, 31)))

    assert repository.created == []


@pytest.mark.asyncio
async def test_aware_deadline_is_rejected_as_database_contract_violation() -> None:
    service, repository = _service(_binding())
    with pytest.raises(DeviceCommandDeadlineError, match="naive UTC"):
        await service.create_command(replace(_request(), deadline_at=datetime(2026, 8, 13, tzinfo=UTC)))
    assert repository.created == []


@pytest.mark.asyncio
async def test_manual_debug_command_freezes_endpoint_without_epoch_or_device_master() -> None:
    service, repository = _service()

    handle = await service.create_manual_debug_command(
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
        endpoint_base_url="http://ECS-MOCK:8080/",
        device_code="RS-MOCK-PLACEMENT-01",
        contract_key="rough_sorter.placement_device",
        contract_version="1.0",
        command_timeout_ms=30_000,
        task_type="PICK_AND_PUT",
        params={"target_code": "OUTLET-1"},
        trace_id="TRACE-MANUAL-DEBUG-001",
        execution_reason="现场供应商联调",
        created_by=42,
    )

    command = repository.created[0]
    assert handle.command_code == command.command_code
    assert command.execution_ref_type == "MANUAL_DEBUG"
    assert command.execution_ref_id == "019f12d0-58d7-7b4d-a23a-1b90aa5d4471"
    assert command.line_run_epoch_id is None
    assert command.device_binding_id is None
    assert command.material_execution_id is None
    assert command.endpoint_base_url == "http://ecs-mock:8080"
    assert command.command_timeout_ms == 30_000
    assert command.deadline_at == datetime(2026, 8, 13, 0, 0, 30)
    assert command.execution_reason == "现场供应商联调"
    assert command.created_by == 42


@pytest.mark.asyncio
async def test_manual_debug_idempotency_includes_endpoint_and_command_contract() -> None:
    repository = FakeCommandRepository()
    adapter = FakeAdapter()
    provider = FakeAdapterProvider(adapter)
    service = DeviceCommandService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        command_repository=repository,  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository({}),  # type: ignore[arg-type]
        evidence_repository=FakeEvidenceRepository(None),  # type: ignore[arg-type]
        adapter_provider=provider,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13),
    )
    request = {
        "client_request_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
        "endpoint_base_url": "http://ecs-mock:8080",
        "device_code": "RS-MOCK-PLACEMENT-01",
        "contract_key": "rough_sorter.placement_device",
        "contract_version": "1.0",
        "command_timeout_ms": 30_000,
        "task_type": "PICK_AND_PUT",
        "params": {"target_code": "OUTLET-1"},
        "trace_id": None,
        "execution_reason": "现场供应商联调",
        "created_by": 42,
    }

    first = await service.create_manual_debug_command(**request)
    duplicate = await service.create_manual_debug_command(**request)

    assert duplicate == first
    assert len(repository.created) == 1
    assert adapter.fetch_status_calls == ["RS-MOCK-PLACEMENT-01"]
    assert provider.requested == ["http://ecs-mock:8080"]
    with pytest.raises(DeviceCommandIdentityConflictError):
        await service.create_manual_debug_command(**{**request, "endpoint_base_url": "http://ecs-other:8080"})
    with pytest.raises(DeviceCommandIdentityConflictError):
        await service.create_manual_debug_command(**{**request, "execution_reason": "另一次联调"})
    with pytest.raises(DeviceCommandIdentityConflictError):
        await service.create_manual_debug_command(**{**request, "created_by": 43})


@pytest.mark.asyncio
async def test_manual_debug_rejects_non_lan_endpoint_before_persistence() -> None:
    service, repository = _service()

    with pytest.raises(ValueError, match="局域网"):
        await service.create_manual_debug_command(
            client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
            endpoint_base_url="https://public.example.com/api",
            device_code="RS-MOCK-PLACEMENT-01",
            contract_key="rough_sorter.placement_device",
            contract_version="1.0",
            command_timeout_ms=30_000,
            task_type="PICK_AND_PUT",
            params={},
            trace_id=None,
            execution_reason="现场供应商联调",
            created_by=42,
        )

    assert repository.created == []


@pytest.mark.asyncio
async def test_manual_debug_snapshot_reads_normalized_callback_evidence() -> None:
    command_repository = FakeCommandRepository()
    evidence = InboundEvidence(
        id=71,
        kind=InboundEvidenceKind.DEVICE_RESULT,
        source_identity="RESULT-CMD-MANUAL-001",
        payload_digest="b" * 64,
        normalized_payload={
            "command_code": "CMD-MANUAL-001",
            "device_code": "RS-MOCK-PLACEMENT-01",
            "contract_key": "rough_sorter.placement_device",
            "contract_version": "1.0",
            "result": "SUCCESS",
            "finish_time": 1_787_475_602_000,
            "source_event_id": "RESULT-CMD-MANUAL-001",
            "data": {"outlet": "OUTLET-1"},
            "error_detail": None,
        },
        received_at=datetime(2026, 8, 23, 10, 0, 2),
        device_code="RS-MOCK-PLACEMENT-01",
        command_code="CMD-MANUAL-001",
        contract_key="rough_sorter.placement_device",
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus.APPLIED,
    )
    service = DeviceCommandService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        command_repository=command_repository,  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository({}),  # type: ignore[arg-type]
        evidence_repository=FakeEvidenceRepository(evidence),  # type: ignore[arg-type]
        adapter_provider=FakeAdapterProvider(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13),
    )
    await service.create_manual_debug_command(
        client_request_id="019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
        endpoint_base_url="http://ecs-mock:8080",
        device_code="RS-MOCK-PLACEMENT-01",
        contract_key="rough_sorter.placement_device",
        contract_version="1.0",
        command_timeout_ms=30_000,
        task_type="PICK_AND_PUT",
        params={"target_code": "OUTLET-1"},
        trace_id=None,
        execution_reason="现场供应商联调",
        created_by=42,
    )
    command = command_repository.created[0]
    command.command_code = "CMD-MANUAL-001"
    command.transition_to(CommandStatus.DISPATCHING)
    command.transition_to(CommandStatus.SUCCEEDED)

    snapshot = await service.get_command_snapshot("CMD-MANUAL-001")

    assert snapshot.status is CommandStatus.SUCCEEDED
    assert snapshot.callback is not None
    assert snapshot.callback.result == "SUCCESS"
    assert snapshot.callback.data == {"outlet": "OUTLET-1"}
    assert snapshot.callback.source_event_id == "RESULT-CMD-MANUAL-001"
    assert snapshot.callback.apply_status == "APPLIED"


@pytest.mark.asyncio
async def test_manual_debug_preflight_returns_all_devices_with_runtime_rejection() -> None:
    adapter = FakeAdapter(
        (
            _ecs_status("ARM-01", supported_commands=("PICK", "MOVE")),
            _ecs_status("ARM-02", is_online=False, supported_commands=("PICK",)),
        )
    )
    provider = FakeAdapterProvider(adapter)
    service = DeviceCommandService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        command_repository=FakeCommandRepository(),  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository({}),  # type: ignore[arg-type]
        evidence_repository=FakeEvidenceRepository(None),  # type: ignore[arg-type]
        adapter_provider=provider,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 13),
    )

    snapshot = await service.preflight_manual_debug("http://ECS-MOCK:8080/")

    assert snapshot.endpoint_base_url == "http://ecs-mock:8080"
    assert tuple(item.status.device.device_code for item in snapshot.devices) == ("ARM-01", "ARM-02")
    assert tuple(item.rejection_code for item in snapshot.devices) == (None, "DEVICE_OFFLINE")
    assert provider.requested == ["http://ecs-mock:8080"]
    assert adapter.fetch_statuses_calls == 1


@pytest.mark.asyncio
async def test_event_debug_command_uses_fixed_endpoint_and_event_data_without_business_binding() -> None:
    repository = FakeCommandRepository()
    service = DeviceCommandService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        command_repository=repository,  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository({}),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 25),
    )
    source_identity = "EVENT:" + "a" * 64
    evidence = InboundEvidence(
        id=91,
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=source_identity,
        payload_digest="b" * 64,
        normalized_payload={
            "device_code": "STATION_SCAN11",
            "contract_key": "third_party_integration",
            "contract_version": "1.1",
            "event_type": "SCAN_COMPLETED",
            "timestamp": 1_787_589_900_163,
            "source_event_id": source_identity,
            "is_debug": True,
            "data": {"event_id": "EVT-1", "location": "STATION_SCAN11", "barcode": "NHW002069-B"},
        },
        received_at=datetime(2026, 8, 25),
        device_code="STATION_SCAN11",
        contract_key="third_party_integration",
        contract_version="1.1",
    )

    handle = await service.create_event_debug_command_in_session(object(), evidence=evidence)
    duplicate = await service.create_event_debug_command_in_session(object(), evidence=evidence)

    command = repository.created[0]
    assert handle == EventDebugCommandReady(
        command_code=command.command_code,
        status=CommandStatus.PENDING,
        created=True,
    )
    assert duplicate == EventDebugCommandReady(
        command_code=command.command_code,
        status=CommandStatus.PENDING,
        created=False,
    )
    assert len(repository.created) == 1
    assert handle.command_code == command.command_code
    assert command.execution_ref_type == "EVENT_DEBUG"
    assert command.execution_ref_id == evidence.source_identity
    assert command.endpoint_base_url == "http://10.24.209.26:8080"
    assert command.command_timeout_ms == 30_000
    assert command.task_type == "MOVE_FORWARD"
    assert command.params == evidence.normalized_payload["data"]
    assert command.execution_reason == f"ECS_EVENT_DEBUG:{evidence.source_identity}"
    assert command.created_by is None


@pytest.mark.asyncio
async def test_event_debug_command_records_existing_command_without_creating_placeholder() -> None:
    repository = FakeCommandRepository()
    blocking_command = DeviceCommand(
        id=41,
        command_code="CMD-OLD-001",
        device_code="STATION_SCAN11",
        line_run_epoch_id=11,
        device_binding_id=21,
        execution_ref_type="MATERIAL_EXECUTION",
        execution_ref_id="EXEC-OLD-001",
        material_execution_id=31,
        contract_key="third_party_integration",
        contract_version="1.1",
        task_type="MOVE_FORWARD",
        params={},
        payload_digest="c" * 64,
        deadline_at=datetime(2026, 8, 25, 0, 0, 30),
        status=CommandStatus.RECONCILING,
        reconciliation_reason="DELIVERY_UNKNOWN",
    )
    repository.unclosed["STATION_SCAN11"] = blocking_command
    service = DeviceCommandService(
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
        command_repository=repository,  # type: ignore[arg-type]
        epoch_repository=FakeEpochRepository({}),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 25),
    )
    evidence = InboundEvidence(
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity="EVT-BUSY",
        payload_digest="a" * 64,
        normalized_payload={
            "device_code": "STATION_SCAN11",
            "contract_key": "third_party_integration",
            "contract_version": "1.1",
            "event_type": "SCAN_COMPLETED",
            "timestamp": 1_787_589_900_163,
            "source_event_id": "EVT-BUSY",
            "is_debug": True,
            "data": {},
        },
        received_at=datetime(2026, 8, 25),
        device_code="STATION_SCAN11",
        contract_key="third_party_integration",
        contract_version="1.1",
    )

    result = await service.create_event_debug_command_in_session(object(), evidence=evidence)

    assert result == EventDebugCommandBlocked(
        blocking_command_id=41,
        blocking_command_code="CMD-OLD-001",
        blocking_command_status=CommandStatus.RECONCILING,
        blocking_reconciliation_reason="DELIVERY_UNKNOWN",
    )
    assert repository.created == []
