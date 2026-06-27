from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from src.workline_runtime.trace_context import TraceContext

if TYPE_CHECKING:
    from src.workline_runtime.orchestrator import OrchestratorResult

_MATERIAL_UNIT_STATUS_TRANSITION_WARNING = "material unit status transition is outside manifest contract"


def _session(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": 123,
        "workline_id": 1,
        "status": "WAITING_DEVICE_RESULT",
        "context_json": {},
        "trace_id": None,
        "last_inbox_id": None,
        "plugin_key": None,
        "contract_version": None,
        "current_wait_type": "COMMAND_RESULT",
        "waiting_since": datetime(2026, 1, 1, 0, 0, 0),
        "deadline_at": datetime(2026, 1, 1, 0, 1, 0),
        "current_wait_timeout_seconds": 60,
        "awaiting_device_command_code": "CMD-AWAITING-099",
        "ended_at": None,
        "failure_domain": None,
        "failure_code": None,
        "failure_message": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ctx(orch_result: OrchestratorResult, *, session: Any | None = None, db: Any | None = None) -> dict[str, Any]:
    resolved_session = session or _session()
    return {
        "db": db or SimpleNamespace(add=MagicMock(), execute=AsyncMock()),
        "session": resolved_session,
        "workline": SimpleNamespace(id=1, plugin_key="demo_plugin", contract_version="1.0"),
        "inbox": SimpleNamespace(id=10, trace_id="trace-runtime", payload_json={"canonical_event_type": "SCAN"}),
        "devices_by_role": {},
        "source_device": None,
        "orch_result": orch_result,
        "current_status": "WAITING_DEVICE_RESULT",
        "trace_id": "trace-runtime",
        "trace": TraceContext.from_runtime(session=resolved_session, trace_id="trace-runtime"),
        "session_ctx": dict(getattr(resolved_session, "context_json", {}) or {}),
        "now": datetime(2026, 1, 1, 0, 2, 0),
        "awaiting_device_command_code": None,
        "awaiting_command_code": None,
        "next_timeline_seq_no": None,
    }


class RecordingResourceProjectionService:
    def __init__(self, *, status: str = "PROJECTED") -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = status

    async def record_resource_fact(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(status=self.status)


class RecordingBinCellReservationService:
    def __init__(self, *, status: str = "CLAIMED") -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = status

    async def apply_runtime_reservation(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(status=self.status)


class RecordingHandlingOperationService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request_bin_operation(
        self,
        db: Any,
        *,
        operation_type: str,
        operation_key: str,
        moves: list[dict[str, Any]],
        trace_id: str,
        workline_id: int | None = None,
        workline_code: str | None = None,
        material_session_id: int | None = None,
        carrier_type: str = "CTU",
        carrier_code: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SimpleNamespace:
        self.calls.append(
            {
                "db": db,
                "operation_type": operation_type,
                "operation_key": operation_key,
                "moves": moves,
                "trace_id": trace_id,
                "workline_id": workline_id,
                "workline_code": workline_code,
                "material_session_id": material_session_id,
                "carrier_type": carrier_type,
                "carrier_code": carrier_code,
                "timeout_seconds": timeout_seconds,
            }
        )
        return SimpleNamespace(
            id=701,
            operation_key=operation_key,
            operation_type=operation_type,
            operation_status="REQUESTED",
        )


class RecordingDb:
    def __init__(self, demand: Any) -> None:
        self.demand = demand
        self.added: list[Any] = []
        self.flushed = False
        self.completed_persists: list[dict[str, Any]] = []

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushed = True

    async def get(self, model: Any, identity: Any) -> Any:
        _ = model, identity
        return self.demand

    async def execute(self, statement: Any) -> Any:
        _ = statement
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=list))


class MaterialUnitDb:
    def __init__(self, material_unit: Any | None = None, material_units: list[Any] | None = None) -> None:
        self.added: list[Any] = []
        self.deleted: list[Any] = []
        self.flushed = False
        self.material_unit = material_unit
        self.material_units = material_units

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def delete(self, value: Any) -> None:
        self.deleted.append(value)

    async def flush(self) -> None:
        self.flushed = True
        for index, value in enumerate(self.added, start=1):
            if getattr(value, "id", None) is None:
                value.id = index

    async def get(self, model: Any, identity: Any) -> Any:
        _ = model
        if self.material_unit is not None and identity == self.material_unit.id:
            return self.material_unit
        return None

    async def execute(self, statement: Any) -> Any:
        _ = statement
        material_units = self.material_units
        if material_units is None:
            material_units = [] if self.material_unit is None else [self.material_unit]
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(
                all=lambda: material_units,
                first=lambda: material_units[0] if material_units else None,
            )
        )


class FakeTerminalRepository:
    def __init__(self, item: Any) -> None:
        self.item = item

    async def get_source_item_for_update(self, _db: Any, source_item_id: int) -> Any:
        assert source_item_id == self.item.id
        return self.item

    async def list_source_items(self, _db: Any, demand_id: int) -> list[Any]:
        assert demand_id == self.item.handoff_demand_id
        return [self.item]


class RecordingRackOperationStatusService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def sync_operation_status(self, db: Any, *, operation_key: str) -> str:
        self.calls.append({"db": db, "operation_key": operation_key})
        return "SUCCEEDED"
