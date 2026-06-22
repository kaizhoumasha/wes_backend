from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select

from src.app.wms_integration.services import WmsTransportContractService
from src.app.workline.models.material_unit import MaterialUnit, MaterialUnitStatus
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemandStatus,
    SmtInboundHandoffSourceItemStatus,
)
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.services import write_back_service as workline_effects
from src.app.workline.services.inbox_batch_processor import _result_requires_outbox_dispatch
from src.app.workline.services.inbox_service import DuplicateInboxError
from src.app.workline.services.ng_return_item_service import NgMaterialConflictError, ng_return_item_service
from src.app.workline.services.runtime_hold_creation_service import runtime_hold_creation_service
from src.app.workline.services.smt_inbound_handoff_service import SmtInboundHandoffService
from src.database.sqlite_schema import configure_sqlite_schemas
from src.workline_plugins.smt_sorting_inbound.constants import (
    COMMAND_NG_PLACE,
    COMMAND_SOURCE_PICK,
    COMMAND_TARGET_PLACE,
    SORTING_CONTEXT_SCHEMA_VERSION,
)
from src.workline_plugins.smt_sorting_inbound.flow_service import SmtSortingInboundFlowService
from src.workline_runtime.effect_result import WriteBackDisposition
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import BlockScope, Destination, RuntimeIntent, RuntimeIntentKind
from src.workline_runtime.runtime_intent_effects import (
    RuntimeIntentEffectApplier,
    _resolve_command_result_timeout_seconds,
)
from src.workline_runtime.trace_context import TraceContext

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
        "awaiting_command_id": 99,
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
        "awaiting_command_id": None,
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


@pytest_asyncio.fixture(scope="function")
async def material_unit_effect_session():
    """独立内存 DB，只建本组回归测试需要的 Session/MaterialUnit 表。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
    )
    configure_sqlite_schemas(engine.sync_engine)
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.create_all,
            tables=[WorkLine.__table__, WorklineSession.__table__, MaterialUnit.__table__],
        )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(
            SQLModel.metadata.drop_all,
            tables=[MaterialUnit.__table__, WorklineSession.__table__, WorkLine.__table__],
        )
    await engine.dispose()


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


def _handoff_sorting_context(*, include_source_pick_request: bool = True) -> dict[str, Any]:
    sorting: dict[str, Any] = {
        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
        "current_material": {
            "source_bin_code": "SRC-BIN-A",
            "source_cell_code": "A01",
            "material_identity_key": "material:PKG-001",
            "reel_thickness_mm": "7.125",
            "evidence": {"pkg_code": "PKG-001", "source_event_id": "source-pick-requested:11:22:1"},
        },
        "pending_target_placement": {
            "target_bin_code": "TARGET-BIN-A",
            "target_cell_code": "1",
            "material_identity_key": "material:PKG-001",
            "reel_thickness_mm": "7.125",
            "allocation_snapshot_version": 3,
            "capacity_evidence": {"capacity_depth_mm": "30"},
        },
        "stations": {"scan_platform": "OCCUPIED"},
    }
    if include_source_pick_request:
        sorting["source_pick_request"] = {
            "handoff_demand_id": 11,
            "handoff_source_item_id": 22,
            "claim_attempt_no": 1,
            "event_id": "source-pick-requested:11:22:1",
            "target_workline_code": "SMT_SORTER_01",
            "manifest_contract_version": "v1",
            "source_rack_position_code": "SINGLE_LAYER_A",
            "target_rack_position_code": "TARGET_STATION",
            "route_evidence": {},
        }
    return sorting


@pytest.mark.asyncio
async def test_empty_intents_complete_new_event_session_as_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    session = _session(
        status="NEW",
        current_wait_type=None,
        awaiting_command_id=None,
        waiting_since=None,
        deadline_at=None,
        current_wait_timeout_seconds=None,
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    ctx["current_status"] = "NEW"
    ctx["inbox"].payload_json = {
        "message_type": "DEVICE_EVENT",
        "device_code": "PIPELINE01",
        "canonical_event_type": "MATERIAL_ARRIVED",
    }

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(ctx, [])

    assert session.status == "COMPLETED"
    assert session.ended_at == ctx["now"]
    assert session.current_wait_type is None
    assert session.awaiting_command_id is None
    assert captured[0]["from_status"] == "NEW"
    assert captured[0]["to_status"] == "COMPLETED"
    assert captured[0]["payload"]["completion_reason"] == "NO_RUNTIME_INTENT"


@pytest.mark.asyncio
async def test_create_material_unit_effect_persists_entity_and_links_session() -> None:
    session = _session(id=901, current_material_unit_id=None)
    db = MaterialUnitDb()
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.create_material_unit(
                pkg_code="PKG-001",
                material_identity_key="MAT:HH-001:MFR-001:260528:LOT-A",
                six_in_one={"PkgID": "PKG-001", "HHPN": "HH-001"},
            )
        ],
    )

    assert db.flushed is True
    assert len(db.added) == 1
    material_unit = db.added[0]
    assert material_unit.pkg_code == "PKG-001"
    assert material_unit.material_identity_key == "MAT:HH-001:MFR-001:260528:LOT-A"
    assert material_unit.six_in_one == {"PkgID": "PKG-001", "HHPN": "HH-001"}
    assert material_unit.status == MaterialUnitStatus.IN_TRANSIT
    assert material_unit.current_session_id == 901
    assert session.current_material_unit_id == material_unit.id


@pytest.mark.asyncio
async def test_create_material_unit_effect_persists_optional_current_location() -> None:
    session = _session(id=901, current_material_unit_id=None)
    db = MaterialUnitDb()
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.create_material_unit(
                pkg_code="PKG-001",
                material_identity_key="MAT:HH-001:MFR-001:260528:LOT-A",
                six_in_one={"PkgID": "PKG-001", "HHPN": "HH-001"},
                status=MaterialUnitStatus.STORED.value,
                current_location="BIN-001:4",
            )
        ],
    )

    assert db.added[0].current_location == "BIN-001:4"


@pytest.mark.asyncio
async def test_create_material_unit_effect_reuses_existing_pkg_code_without_add() -> None:
    existing = SimpleNamespace(
        id=1002,
        pkg_code="PKG-001",
        material_identity_key="old-key",
        six_in_one={"PkgID": "PKG-001"},
        status=MaterialUnitStatus.STORED,
        current_location="OLD-BIN:1",
        current_session_id=800,
    )
    session = _session(id=903, current_material_unit_id=None)
    db = MaterialUnitDb(material_unit=existing)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.create_material_unit(
                pkg_code="PKG-001",
                material_identity_key="MAT:HH-001:MFR-001:260528:LOT-A",
                six_in_one={"PkgID": "PKG-001", "HHPN": "HH-001"},
                status=MaterialUnitStatus.IN_TRANSIT.value,
            )
        ],
    )

    assert db.added == []
    assert db.flushed is True
    assert existing.material_identity_key == "MAT:HH-001:MFR-001:260528:LOT-A"
    assert existing.six_in_one == {"PkgID": "PKG-001", "HHPN": "HH-001"}
    assert existing.status == MaterialUnitStatus.IN_TRANSIT
    assert existing.current_session_id == 903
    assert session.current_material_unit_id == 1002


@pytest.mark.asyncio
async def test_create_material_unit_effect_merges_six_in_one_without_losing_existing_fields() -> None:
    """跨 Session handoff 复用时，SMT 的瘦构造 six_in_one 不得覆盖粗分机写入的完整六合一码。"""
    existing = SimpleNamespace(
        id=1002,
        pkg_code="PKG-001",
        material_identity_key="old-key",
        six_in_one={"PkgID": "PKG-001", "HHPN": "HH-001", "LotCode": "LOT-A", "Vendor": "V1"},
        status=MaterialUnitStatus.STORED,
        current_location="OLD-BIN:1",
        current_session_id=800,
    )
    session = _session(id=903, current_material_unit_id=None)
    db = MaterialUnitDb(material_unit=existing)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.create_material_unit(
                pkg_code="PKG-001",
                material_identity_key="MAT:HH-001:V1:260528:LOT-A",
                # SMT 仅构造 5 字段瘦 dict，缺 HHPN/LotCode/Vendor。
                six_in_one={
                    "PkgID": "PKG-001",
                    "material_identity_key": "MAT:HH-001:V1:260528:LOT-A",
                    "reel_thickness": "7.125",
                    "source_bin_code": "SRC-BIN-A",
                    "source_cell_code": "A01",
                },
                status=MaterialUnitStatus.IN_TRANSIT.value,
            )
        ],
    )

    # 已有字段保留，新字段补充，无字段被丢弃。
    assert existing.six_in_one == {
        "PkgID": "PKG-001",
        "HHPN": "HH-001",
        "LotCode": "LOT-A",
        "Vendor": "V1",
        "material_identity_key": "MAT:HH-001:V1:260528:LOT-A",
        "reel_thickness": "7.125",
        "source_bin_code": "SRC-BIN-A",
        "source_cell_code": "A01",
    }
    assert existing.current_session_id == 903


@pytest.mark.asyncio
async def test_create_material_unit_effect_rejects_reuse_when_owned_by_active_session() -> None:
    """料盘仍被另一非终态 Session 持有时，复用必须拒绝，避免静默窃取所有权。"""
    existing = SimpleNamespace(
        id=1002,
        pkg_code="PKG-001",
        material_identity_key="old-key",
        six_in_one={"PkgID": "PKG-001"},
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location=None,
        current_session_id=800,  # 属于另一活跃 Session
    )

    class ActiveOwnerDb(MaterialUnitDb):
        async def execute(self, statement: Any) -> Any:
            # 复用路径先按 pkg_code 查 MaterialUnit（走父类逻辑），
            # 再按 owner_session_id 查 WorklineSession.status，返回活跃态 RUNNING。
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    all=lambda: [existing],
                    first=lambda: "RUNNING",
                )
            )

    session = _session(id=903, current_material_unit_id=None)
    db = ActiveOwnerDb(material_unit=existing)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)

    with pytest.raises(ValueError, match="refuse silent takeover"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.create_material_unit(
                    pkg_code="PKG-001",
                    material_identity_key="MAT:HH-001:MFR-001:260528:LOT-A",
                    six_in_one={"PkgID": "PKG-001"},
                    status=MaterialUnitStatus.IN_TRANSIT.value,
                )
            ],
        )

    # 被拒绝时不得改写所有权。
    assert existing.current_session_id == 800
    assert session.current_material_unit_id is None


@pytest.mark.asyncio
async def test_create_material_unit_effect_allows_reuse_when_owned_by_terminal_session() -> None:
    """料盘被终态 Session 持有时（正常 handoff），复用应放行并转移所有权。"""
    existing = SimpleNamespace(
        id=1002,
        pkg_code="PKG-001",
        material_identity_key="old-key",
        six_in_one={"PkgID": "PKG-001", "HHPN": "HH-001"},
        status=MaterialUnitStatus.STORED,
        current_location="OLD-BIN:1",
        current_session_id=800,  # 粗分机 Session 已 COMPLETED
    )

    class TerminalOwnerDb(MaterialUnitDb):
        async def execute(self, statement: Any) -> Any:
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    all=lambda: [existing],
                    first=lambda: "COMPLETED",
                )
            )

    session = _session(id=903, current_material_unit_id=None)
    db = TerminalOwnerDb(material_unit=existing)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.create_material_unit(
                pkg_code="PKG-001",
                material_identity_key="MAT:HH-001:MFR-001:260528:LOT-A",
                six_in_one={"PkgID": "PKG-001"},
                status=MaterialUnitStatus.IN_TRANSIT.value,
            )
        ],
    )

    assert existing.current_session_id == 903
    assert session.current_material_unit_id == 1002


@pytest.mark.asyncio
async def test_create_material_unit_effect_warns_when_reusing_pkg_code_outside_manifest_contract(
    caplog: pytest.LogCaptureFixture,
) -> None:
    existing = SimpleNamespace(
        id=1002,
        pkg_code="PKG-001",
        material_identity_key="old-key",
        six_in_one={"PkgID": "PKG-001"},
        status=MaterialUnitStatus.NG,
        current_location="NG-BIN:1",
        current_session_id=800,
    )
    session = _session(id=903, current_material_unit_id=None, plugin_key="rough_sorter")
    db = MaterialUnitDb(material_unit=existing)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)
    ctx["workline"].plugin_key = "rough_sorter"

    with caplog.at_level("WARNING", logger="src.workline_runtime.runtime_intent_effects"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.create_material_unit(
                    pkg_code="PKG-001",
                    material_identity_key="MAT:HH-001:MFR-001:260528:LOT-A",
                    six_in_one={"PkgID": "PKG-001", "HHPN": "HH-001"},
                    status=MaterialUnitStatus.IN_TRANSIT.value,
                )
            ],
        )

    assert existing.status == MaterialUnitStatus.IN_TRANSIT
    warning_text = caplog.text
    assert _MATERIAL_UNIT_STATUS_TRANSITION_WARNING in warning_text
    assert "object_type=REEL" in warning_text
    assert "object_id=1002" in warning_text
    assert "from_state=NG" in warning_text
    assert "to_state=IN_TRANSIT" in warning_text
    assert "pkg_code=PKG-001" in warning_text
    assert "plugin_key=rough_sorter" in warning_text


@pytest.mark.asyncio
async def test_create_material_unit_effect_rejects_duplicate_pkg_code_with_clear_error() -> None:
    duplicates = [
        SimpleNamespace(
            id=1002,
            pkg_code="PKG-001",
            material_identity_key="old-key-1",
            six_in_one={"PkgID": "PKG-001"},
            status=MaterialUnitStatus.STORED,
            current_session_id=800,
        ),
        SimpleNamespace(
            id=1003,
            pkg_code="PKG-001",
            material_identity_key="old-key-2",
            six_in_one={"PkgID": "PKG-001"},
            status=MaterialUnitStatus.IN_TRANSIT,
            current_session_id=801,
        ),
    ]
    session = _session(id=904, current_material_unit_id=None)
    db = MaterialUnitDb(material_units=duplicates)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)

    with pytest.raises(ValueError, match="multiple material units found for pkg_code: PKG-001"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.create_material_unit(
                    pkg_code="PKG-001",
                    material_identity_key="MAT:HH-001:MFR-001:260528:LOT-A",
                    six_in_one={"PkgID": "PKG-001", "HHPN": "HH-001"},
                    status=MaterialUnitStatus.IN_TRANSIT.value,
                )
            ],
        )

    assert db.added == []
    assert db.flushed is False
    assert session.current_material_unit_id is None


@pytest.mark.asyncio
async def test_create_material_unit_effect_persists_session_link_in_db(
    material_unit_effect_session: AsyncSession,
) -> None:
    material_unit_effect_session.add(
        WorkLine(
            id=1,
            line_code="WL-MU-001",
            line_name="Material Unit Test Line",
            line_type=LineType.AUTO,
            plugin_key="smt_sorting_inbound",
        )
    )
    session = WorklineSession(
        session_code="SESSION-MU-001",
        workline_id=1,
        plugin_key="smt_sorting_inbound",
        run_mode=RunMode.AUTO,
        status=SessionStatus.RUNNING,
        context_json={},
    )
    material_unit_effect_session.add(session)
    await material_unit_effect_session.commit()
    await material_unit_effect_session.refresh(session)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=material_unit_effect_session)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.create_material_unit(
                pkg_code="PKG-DB-001",
                material_identity_key="MAT:HH-DB:MFR-001:260528:LOT-A",
                six_in_one={"PkgID": "PKG-DB-001", "HHPN": "HH-DB"},
            )
        ],
    )
    await material_unit_effect_session.commit()

    persisted_session = await material_unit_effect_session.get(WorklineSession, session.id)
    result = await material_unit_effect_session.execute(
        select(MaterialUnit).where(MaterialUnit.pkg_code == "PKG-DB-001")
    )
    persisted_unit = result.scalar_one()

    assert persisted_session is not None
    assert persisted_session.current_material_unit_id == persisted_unit.id
    assert persisted_unit.current_session_id == session.id


@pytest.mark.asyncio
async def test_create_material_unit_effect_recovers_unique_conflict_and_links_session(
    material_unit_effect_session: AsyncSession,
) -> None:
    material_unit_effect_session.add(
        WorkLine(
            id=1,
            line_code="WL-MU-RACE",
            line_name="Material Unit Race Test Line",
            line_type=LineType.AUTO,
            plugin_key="smt_sorting_inbound",
        )
    )
    session = WorklineSession(
        session_code="SESSION-MU-RACE",
        workline_id=1,
        plugin_key="smt_sorting_inbound",
        run_mode=RunMode.AUTO,
        status=SessionStatus.RUNNING,
        context_json={},
    )
    material_unit_effect_session.add(session)
    await material_unit_effect_session.commit()
    await material_unit_effect_session.refresh(session)

    class ConcurrentInsertSession:
        def __init__(self, wrapped: AsyncSession) -> None:
            self.wrapped = wrapped
            self.injected_material_unit_id: int | None = None

        def __getattr__(self, name: str) -> Any:
            return getattr(self.wrapped, name)

        async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
            if self.injected_material_unit_id is None:
                result = await self.wrapped.execute(statement, *args, **kwargs)
                assert result.scalars().all() == []
                existing = MaterialUnit(
                    pkg_code="PKG-DB-RACE",
                    material_identity_key="old-key",
                    six_in_one={"PkgID": "PKG-DB-RACE"},
                    status=MaterialUnitStatus.STORED,
                    current_session_id=777,
                )
                self.wrapped.add(existing)
                await self.wrapped.flush()
                self.injected_material_unit_id = existing.id
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=list))
            return await self.wrapped.execute(statement, *args, **kwargs)

    db = ConcurrentInsertSession(material_unit_effect_session)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.create_material_unit(
                pkg_code="PKG-DB-RACE",
                material_identity_key="MAT:HH-RACE:MFR-001:260528:LOT-A",
                six_in_one={"PkgID": "PKG-DB-RACE", "HHPN": "HH-RACE"},
                status=MaterialUnitStatus.IN_TRANSIT.value,
            )
        ],
    )
    await material_unit_effect_session.commit()

    persisted_session = await material_unit_effect_session.get(WorklineSession, session.id)
    result = await material_unit_effect_session.execute(
        select(MaterialUnit).where(MaterialUnit.pkg_code == "PKG-DB-RACE")
    )
    persisted_unit = result.scalar_one()

    assert persisted_session is not None
    assert db.injected_material_unit_id == persisted_unit.id
    assert persisted_session.current_material_unit_id == persisted_unit.id
    assert persisted_unit.material_identity_key == "MAT:HH-RACE:MFR-001:260528:LOT-A"
    assert persisted_unit.six_in_one == {"PkgID": "PKG-DB-RACE", "HHPN": "HH-RACE"}
    assert persisted_unit.status == MaterialUnitStatus.IN_TRANSIT
    assert persisted_unit.current_session_id == session.id


@pytest.mark.asyncio
async def test_create_material_unit_effect_rejects_unique_conflict_when_owner_is_active(
    material_unit_effect_session: AsyncSession,
) -> None:
    """唯一键竞争回收已存在料盘后，仍要校验活跃 Session 所有权。"""
    material_unit_effect_session.add(
        WorkLine(
            id=1,
            line_code="WL-MU-RACE-ACTIVE",
            line_name="Material Unit Race Active Owner Test Line",
            line_type=LineType.AUTO,
            plugin_key="smt_sorting_inbound",
        )
    )
    owner_session = WorklineSession(
        id=777,
        session_code="SESSION-MU-RACE-OWNER",
        workline_id=1,
        plugin_key="smt_sorting_inbound",
        run_mode=RunMode.AUTO,
        status=SessionStatus.RUNNING,
        context_json={},
    )
    session = WorklineSession(
        session_code="SESSION-MU-RACE-STEALER",
        workline_id=1,
        plugin_key="smt_sorting_inbound",
        run_mode=RunMode.AUTO,
        status=SessionStatus.RUNNING,
        context_json={},
    )
    material_unit_effect_session.add(owner_session)
    material_unit_effect_session.add(session)
    await material_unit_effect_session.commit()
    await material_unit_effect_session.refresh(session)

    class ConcurrentActiveOwnerInsertSession:
        def __init__(self, wrapped: AsyncSession) -> None:
            self.wrapped = wrapped
            self.injected_material_unit_id: int | None = None

        def __getattr__(self, name: str) -> Any:
            return getattr(self.wrapped, name)

        async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
            if self.injected_material_unit_id is None:
                result = await self.wrapped.execute(statement, *args, **kwargs)
                assert result.scalars().all() == []
                existing = MaterialUnit(
                    pkg_code="PKG-DB-RACE-ACTIVE",
                    material_identity_key="old-key",
                    six_in_one={"PkgID": "PKG-DB-RACE-ACTIVE"},
                    status=MaterialUnitStatus.IN_TRANSIT,
                    current_session_id=777,
                )
                self.wrapped.add(existing)
                await self.wrapped.flush()
                self.injected_material_unit_id = existing.id
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=list))
            return await self.wrapped.execute(statement, *args, **kwargs)

    db = ConcurrentActiveOwnerInsertSession(material_unit_effect_session)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)

    with pytest.raises(ValueError, match="refuse silent takeover"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.create_material_unit(
                    pkg_code="PKG-DB-RACE-ACTIVE",
                    material_identity_key="MAT:HH-RACE:MFR-001:260528:LOT-A",
                    six_in_one={"PkgID": "PKG-DB-RACE-ACTIVE", "HHPN": "HH-RACE"},
                    status=MaterialUnitStatus.IN_TRANSIT.value,
                )
            ],
        )

    result = await material_unit_effect_session.execute(
        select(MaterialUnit).where(MaterialUnit.pkg_code == "PKG-DB-RACE-ACTIVE")
    )
    persisted_unit = result.scalar_one()

    assert db.injected_material_unit_id == persisted_unit.id
    assert persisted_unit.current_session_id == 777
    assert session.current_material_unit_id is None


@pytest.mark.asyncio
async def test_create_material_unit_effect_warns_after_unique_conflict_recovery(
    caplog: pytest.LogCaptureFixture,
    material_unit_effect_session: AsyncSession,
) -> None:
    material_unit_effect_session.add(
        WorkLine(
            id=1,
            line_code="WL-MU-RACE-WARN",
            line_name="Material Unit Race Warn Test Line",
            line_type=LineType.AUTO,
            plugin_key="rough_sorter",
        )
    )
    session = WorklineSession(
        session_code="SESSION-MU-RACE-WARN",
        workline_id=1,
        plugin_key="rough_sorter",
        run_mode=RunMode.AUTO,
        status=SessionStatus.RUNNING,
        context_json={},
    )
    material_unit_effect_session.add(session)
    await material_unit_effect_session.commit()
    await material_unit_effect_session.refresh(session)

    class ConcurrentInsertSession:
        def __init__(self, wrapped: AsyncSession) -> None:
            self.wrapped = wrapped
            self.injected_material_unit_id: int | None = None

        def __getattr__(self, name: str) -> Any:
            return getattr(self.wrapped, name)

        async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
            if self.injected_material_unit_id is None:
                result = await self.wrapped.execute(statement, *args, **kwargs)
                assert result.scalars().all() == []
                existing = MaterialUnit(
                    pkg_code="PKG-DB-RACE-WARN",
                    material_identity_key="old-key",
                    six_in_one={"PkgID": "PKG-DB-RACE-WARN"},
                    status=MaterialUnitStatus.NG,
                    current_session_id=777,
                )
                self.wrapped.add(existing)
                await self.wrapped.flush()
                self.injected_material_unit_id = existing.id
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=list))
            return await self.wrapped.execute(statement, *args, **kwargs)

    db = ConcurrentInsertSession(material_unit_effect_session)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)
    ctx["workline"].plugin_key = "rough_sorter"

    with caplog.at_level("WARNING", logger="src.workline_runtime.runtime_intent_effects"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.create_material_unit(
                    pkg_code="PKG-DB-RACE-WARN",
                    material_identity_key="MAT:HH-RACE:MFR-001:260528:LOT-A",
                    six_in_one={"PkgID": "PKG-DB-RACE-WARN", "HHPN": "HH-RACE"},
                    status=MaterialUnitStatus.IN_TRANSIT.value,
                )
            ],
        )
    await material_unit_effect_session.commit()

    result = await material_unit_effect_session.execute(
        select(MaterialUnit).where(MaterialUnit.pkg_code == "PKG-DB-RACE-WARN")
    )
    persisted_unit = result.scalar_one()

    assert db.injected_material_unit_id == persisted_unit.id
    assert persisted_unit.status == MaterialUnitStatus.IN_TRANSIT
    warning_text = caplog.text
    assert _MATERIAL_UNIT_STATUS_TRANSITION_WARNING in warning_text
    assert f"object_id={persisted_unit.id}" in warning_text
    assert "from_state=NG" in warning_text
    assert "to_state=IN_TRANSIT" in warning_text


@pytest.mark.asyncio
async def test_update_material_unit_status_effect_updates_entity_and_links_session() -> None:
    material_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location=None,
        current_session_id=None,
    )
    session = _session(id=902, current_material_unit_id=None)
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status="STORED",
                current_location="BIN-001:4",
            )
        ],
    )

    assert material_unit.status == MaterialUnitStatus.STORED
    assert material_unit.current_location == "BIN-001:4"
    assert material_unit.current_session_id == 902
    assert session.current_material_unit_id == 1001


@pytest.mark.asyncio
async def test_update_material_unit_status_warns_for_transition_outside_manifest_contract(
    caplog: pytest.LogCaptureFixture,
) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.STORED,
        current_location=None,
        current_session_id=None,
    )
    session = _session(id=902, current_material_unit_id=None, plugin_key="rough_sorter")
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)
    ctx["workline"].plugin_key = "rough_sorter"

    with caplog.at_level("WARNING", logger="src.workline_runtime.runtime_intent_effects"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.update_material_unit_status(
                    material_unit_id=1001,
                    status=MaterialUnitStatus.COMPLETED.value,
                )
            ],
        )

    assert material_unit.status == MaterialUnitStatus.COMPLETED
    warning_text = caplog.text
    assert _MATERIAL_UNIT_STATUS_TRANSITION_WARNING in warning_text
    assert "object_type=REEL" in warning_text
    assert "object_id=1001" in warning_text
    assert "from_state=STORED" in warning_text
    assert "to_state=COMPLETED" in warning_text
    assert "pkg_code=PKG-001" in warning_text
    assert "plugin_key=rough_sorter" in warning_text
    assert "suggestion=" in warning_text


@pytest.mark.asyncio
async def test_update_material_unit_status_does_not_warn_for_manifest_transition(
    caplog: pytest.LogCaptureFixture,
) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location=None,
        current_session_id=None,
    )
    session = _session(id=902, current_material_unit_id=None, plugin_key="rough_sorter")
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)
    ctx["workline"].plugin_key = "rough_sorter"

    with caplog.at_level("WARNING", logger="src.workline_runtime.runtime_intent_effects"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.update_material_unit_status(
                    material_unit_id=1001,
                    status=MaterialUnitStatus.STORED.value,
                )
            ],
        )

    assert material_unit.status == MaterialUnitStatus.STORED
    assert _MATERIAL_UNIT_STATUS_TRANSITION_WARNING not in caplog.text


@pytest.mark.asyncio
async def test_update_material_unit_status_records_reconciling_from_state() -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.IN_TRANSIT,
        reconciliation_from_state=None,
        current_location=None,
        current_session_id=None,
    )
    session = _session(id=902, current_material_unit_id=None, plugin_key="rough_sorter")
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)
    ctx["workline"].plugin_key = "rough_sorter"

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.RECONCILING.value,
            )
        ],
    )

    assert material_unit.status == MaterialUnitStatus.RECONCILING
    assert material_unit.reconciliation_from_state == MaterialUnitStatus.IN_TRANSIT


@pytest.mark.asyncio
async def test_update_material_unit_status_persists_reconciling_from_state(
    material_unit_effect_session: AsyncSession,
) -> None:
    material_unit_effect_session.add(
        WorkLine(
            id=1,
            line_code="WL-MU-RECONCILE",
            line_name="Material Unit Reconcile Test Line",
            line_type=LineType.AUTO,
            plugin_key="rough_sorter",
        )
    )
    session = WorklineSession(
        session_code="SESSION-MU-RECONCILE",
        workline_id=1,
        plugin_key="rough_sorter",
        run_mode=RunMode.AUTO,
        status=SessionStatus.RUNNING,
        context_json={},
    )
    material_unit = MaterialUnit(
        pkg_code="PKG-RECONCILE-001",
        material_identity_key="MAT:HH-RECONCILE:MFR-001:260528:LOT-A",
        six_in_one={"PkgID": "PKG-RECONCILE-001", "HHPN": "HH-RECONCILE"},
        status=MaterialUnitStatus.IN_TRANSIT,
        current_session_id=None,
    )
    material_unit_effect_session.add(session)
    material_unit_effect_session.add(material_unit)
    await material_unit_effect_session.commit()
    await material_unit_effect_session.refresh(session)
    await material_unit_effect_session.refresh(material_unit)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=material_unit_effect_session)
    ctx["workline"].plugin_key = "rough_sorter"

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_material_unit_status(
                material_unit_id=material_unit.id,
                status=MaterialUnitStatus.RECONCILING.value,
            )
        ],
    )
    await material_unit_effect_session.commit()

    persisted_unit = await material_unit_effect_session.get(MaterialUnit, material_unit.id)

    assert persisted_unit is not None
    assert persisted_unit.status == MaterialUnitStatus.RECONCILING
    assert persisted_unit.reconciliation_from_state == MaterialUnitStatus.IN_TRANSIT


@pytest.mark.asyncio
async def test_update_material_unit_status_checks_reconciling_exits_against_manifest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.RECONCILING,
        current_location=None,
        current_session_id=None,
    )
    session = _session(id=902, current_material_unit_id=None, plugin_key="rough_sorter")
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)
    ctx["workline"].plugin_key = "rough_sorter"

    with caplog.at_level("WARNING", logger="src.workline_runtime.runtime_intent_effects"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.update_material_unit_status(
                    material_unit_id=1001,
                    status=MaterialUnitStatus.NG.value,
                )
            ],
        )

    assert material_unit.status == MaterialUnitStatus.NG
    assert _MATERIAL_UNIT_STATUS_TRANSITION_WARNING not in caplog.text

    caplog.clear()
    material_unit.status = MaterialUnitStatus.RECONCILING
    with caplog.at_level("WARNING", logger="src.workline_runtime.runtime_intent_effects"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.update_material_unit_status(
                    material_unit_id=1001,
                    status=MaterialUnitStatus.RECONCILING.value,
                )
            ],
        )

    assert material_unit.status == MaterialUnitStatus.RECONCILING
    assert _MATERIAL_UNIT_STATUS_TRANSITION_WARNING in caplog.text
    assert "from_state=RECONCILING" in caplog.text
    assert "to_state=RECONCILING" in caplog.text


@pytest.mark.asyncio
async def test_update_material_unit_status_missing_plugin_manifest_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.STORED,
        current_location=None,
        current_session_id=None,
    )
    session = _session(id=902, current_material_unit_id=None, plugin_key="unknown_plugin")
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)
    ctx["workline"].plugin_key = "unknown_plugin"

    with caplog.at_level("WARNING", logger="src.workline_runtime.runtime_intent_effects"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.update_material_unit_status(
                    material_unit_id=1001,
                    status=MaterialUnitStatus.COMPLETED.value,
                )
            ],
        )

    assert material_unit.status == MaterialUnitStatus.COMPLETED
    assert _MATERIAL_UNIT_STATUS_TRANSITION_WARNING not in caplog.text


@pytest.mark.asyncio
async def test_update_material_unit_status_unavailable_manifest_does_not_block_or_warn(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.STORED,
        current_location=None,
        current_session_id=None,
    )
    session = _session(id=902, current_material_unit_id=None, plugin_key="broken_plugin")
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True), session=session, db=db)
    ctx["workline"].plugin_key = "broken_plugin"

    class BrokenDefinition:
        @property
        def manifest(self) -> object:
            raise ValueError("broken manifest")

    def get_broken_definition(plugin_key: str | None) -> BrokenDefinition | None:
        assert plugin_key == "broken_plugin"
        return BrokenDefinition()

    monkeypatch.setattr(
        "src.workline_runtime.runtime_intent_effects.get_workline_plugin_definition",
        get_broken_definition,
    )

    with caplog.at_level("WARNING", logger="src.workline_runtime.runtime_intent_effects"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.update_material_unit_status(
                    material_unit_id=1001,
                    status=MaterialUnitStatus.COMPLETED.value,
                )
            ],
        )

    assert material_unit.status == MaterialUnitStatus.COMPLETED
    assert _MATERIAL_UNIT_STATUS_TRANSITION_WARNING not in caplog.text


@pytest.mark.asyncio
async def test_update_context_and_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_context({"scan_ok": True}),
            RuntimeIntent.complete({"bin_code": "BIN-001"}),
        ],
    )

    assert session.context_json == {"pkg_id": "PKG-001", "scan_ok": True, "bin_code": "BIN-001"}
    assert session.status == "COMPLETED"
    assert session.current_wait_type is None
    assert session.awaiting_command_id is None
    assert session.ended_at == ctx["now"]
    record_ng_flow.assert_awaited_once()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_intent_persists_session_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier().apply(ctx, [RuntimeIntent.complete({"material_moved": True})])

    assert session.status == "COMPLETED"
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_intent_turns_ng_material_conflict_into_runtime_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    created_holds: list[dict[str, Any]] = []
    long_identity = f"test-material:{'X' * 300}"

    async def raise_conflict(*_args: Any, **_kwargs: Any) -> None:
        raise NgMaterialConflictError(
            material_identity_key=long_identity,
            existing_item=SimpleNamespace(id=8801, created_from_runtime_hold_id=9901),
            evidence={
                "reason_code": "NG_MATERIAL_CONFLICT",
                "material_identity_key": long_identity,
                "new_source_command_id": 8802,
            },
        )

    async def create_hold(_db: Any, **kwargs: Any) -> SimpleNamespace:
        created_holds.append(kwargs)
        return SimpleNamespace(id=7701, **kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", raise_conflict)
    monkeypatch.setattr(runtime_hold_creation_service, "create_for_resource_reconciliation", create_hold)

    await RuntimeIntentEffectApplier().apply(ctx, [RuntimeIntent.complete({"material_moved": True})])

    assert session.status == "MANUAL_HOLD"
    assert session.context_json["ng_material_conflict"]["reason_code"] == "NG_MATERIAL_CONFLICT"
    assert created_holds[0]["source_reason"] == "NG_MATERIAL_CONFLICT"
    assert created_holds[0]["evidence"]["material_identity_key"] == long_identity
    assert created_holds[0]["source_event_id"].startswith("ng-material-conflict:123:8802:")
    assert len(created_holds[0]["source_event_id"]) < 80
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_intent_skips_ng_flow_for_already_completed_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(status=SessionStatus.COMPLETED.value, context_json={"pkg_id": "PKG-001"})
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock(
        side_effect=NgMaterialConflictError(
            material_identity_key="MAT:HH-001:MFR-001:260528:LOT-A",
            existing_item=SimpleNamespace(id=8801, created_from_runtime_hold_id=9901),
            evidence={
                "reason_code": "NG_MATERIAL_CONFLICT",
                "material_identity_key": "MAT:HH-001:MFR-001:260528:LOT-A",
            },
        )
    )
    create_hold = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)
    monkeypatch.setattr(runtime_hold_creation_service, "create_for_resource_reconciliation", create_hold)

    await RuntimeIntentEffectApplier().apply(ctx, [RuntimeIntent.complete({"material_moved": True})])

    assert session.status == SessionStatus.COMPLETED.value
    record_ng_flow.assert_awaited_once()
    create_hold.assert_not_awaited()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_intent_records_ng_flow_for_already_completed_ng_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        status=SessionStatus.COMPLETED.value,
        context_json={
            "ng_reason": "LOCAL_SORTING_NG",
            "source_payload": {
                "current_material": {"pkg_code": "PKG-001", "material_identity_key": "MAT:PKG-001"},
                "ng_command_payload": {"command_code": "CMD-NG-001", "result": "SUCCESS"},
            },
        },
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock(return_value=SimpleNamespace(id=8801))
    create_hold = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)
    monkeypatch.setattr(runtime_hold_creation_service, "create_for_resource_reconciliation", create_hold)

    await RuntimeIntentEffectApplier().apply(ctx, [RuntimeIntent.complete({"material_moved": True})])

    assert session.status == SessionStatus.COMPLETED.value
    record_ng_flow.assert_awaited_once()
    create_hold.assert_not_awaited()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_resource_fact_intent_is_applied_before_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()
    resource_projection = RecordingResourceProjectionService()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={"pkg_code": "PKG-001", "bin_code": "BIN-001", "bin_cell_index": "4"},
                idempotency_key="MATERIAL_MOUNTED:CMD-001:PKG-001:BIN-001:4",
            ),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    assert resource_projection.calls[0]["db"] is ctx["db"]
    assert resource_projection.calls[0]["session"] is session
    assert resource_projection.calls[0]["idempotency_key"] == "MATERIAL_MOUNTED:CMD-001:PKG-001:BIN-001:4"
    assert session.status == "COMPLETED"
    assert session.context_json["material_mounted"] is True
    record_ng_flow.assert_awaited_once()


@pytest.mark.asyncio
async def test_source_pick_complete_does_not_record_handoff_success_without_source_pick_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "source_pick_request": {
                    "handoff_demand_id": 11,
                    "handoff_source_item_id": 22,
                    "claim_attempt_no": 1,
                    "event_id": "source-pick-requested:11:22:1",
                    "target_workline_code": "SMT_SORTER_01",
                    "manifest_contract_version": "v1",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "target_rack_position_code": "TARGET_STATION",
                    "route_evidence": {},
                },
            }
        }
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    calls: list[tuple[str, Any]] = []

    async def record_call(_db: Any, **kwargs: Any) -> SimpleNamespace:
        calls.append(("source_pick_success", session.status, kwargs))
        assert _db is db
        return SimpleNamespace(outcome="advanced", advanced=True, already_terminal=False)

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", AsyncMock())
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_pick_success",
        record_call,
    )

    await RuntimeIntentEffectApplier().apply(ctx, [RuntimeIntent.complete({"material_mounted": True})])

    assert calls == []


@pytest.mark.asyncio
async def test_source_pick_resource_fact_records_handoff_success_after_context_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "source_pick_request": {
                    "handoff_demand_id": 11,
                    "handoff_source_item_id": 22,
                    "claim_attempt_no": 1,
                    "event_id": "source-pick-requested:11:22:1",
                    "target_workline_code": "SMT_SORTER_01",
                    "manifest_contract_version": "v1",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "target_rack_position_code": "TARGET_STATION",
                    "route_evidence": {},
                },
            }
        }
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    calls: list[tuple[str, Any]] = []
    resource_projection = RecordingResourceProjectionService()

    async def record_call(_db: Any, **kwargs: Any) -> SimpleNamespace:
        current_material = session.context_json["sorting"].get("current_material")
        calls.append(("source_pick_success", current_material))
        assert _db is db
        assert kwargs["session"] is session
        assert kwargs["trace_id"] == "trace-runtime"
        assert current_material["material_identity_key"] == "material:PKG-001"
        return SimpleNamespace(outcome="advanced", advanced=True, already_terminal=False)

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_pick_success",
        record_call,
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload={
                    "material_identity_key": "material:PKG-001",
                    "pkg_code": "PKG-001",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "source_pick_request_event_id": "source-pick-requested:11:22:1",
                },
                idempotency_key="MATERIAL_UNMOUNTED:source-pick-requested:11:22:1:PKG-001:SINGLE_LAYER_A",
            ),
            RuntimeIntent.update_context(
                {
                    "sorting": {
                        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                        "source_pick_request": {
                            "handoff_demand_id": 11,
                            "handoff_source_item_id": 22,
                            "claim_attempt_no": 1,
                            "event_id": "source-pick-requested:11:22:1",
                            "target_workline_code": "SMT_SORTER_01",
                            "manifest_contract_version": "v1",
                            "source_rack_position_code": "SINGLE_LAYER_A",
                            "target_rack_position_code": "TARGET_STATION",
                            "route_evidence": {},
                        },
                        "current_material": {
                            "material_identity_key": "material:PKG-001",
                            "pkg_code": "PKG-001",
                            "source_rack_position_code": "SINGLE_LAYER_A",
                        },
                    }
                }
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_UNMOUNTED"
    assert session.context_json["sorting"]["current_material"]["pkg_code"] == "PKG-001"
    assert calls == [
        (
            "source_pick_success",
            {
                "material_identity_key": "material:PKG-001",
                "pkg_code": "PKG-001",
                "source_rack_position_code": "SINGLE_LAYER_A",
            },
        )
    ]


@pytest.mark.asyncio
async def test_source_pick_resource_fact_records_handoff_success_with_command_evidence_when_request_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        awaiting_command_id=88,
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
            }
        },
    )
    inbox = SimpleNamespace(
        id=10,
        command_id=88,
        trace_id="trace-runtime",
        payload_json={
            "command_code": "SOURCE-CMD-001",
            "command_type": COMMAND_SOURCE_PICK,
            "result": "SUCCESS",
            "data": {},
        },
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"] = inbox
    ctx["trace"] = TraceContext.from_runtime(session=session, inbox=inbox, trace_id="trace-runtime")
    resource_projection = RecordingResourceProjectionService()
    calls: list[dict[str, Any]] = []

    async def record_call(_db: Any, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        assert _db is db
        assert kwargs["command_id"] == 88
        assert kwargs["session"] is session
        assert session.context_json["sorting"]["current_material"]["material_identity_key"] == "material:PKG-001"
        return SimpleNamespace(outcome="advanced", advanced=True, already_terminal=False)

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_pick_success",
        record_call,
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload={
                    "material_identity_key": "material:PKG-001",
                    "pkg_code": "PKG-001",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "source_pick_request_event_id": "source-pick-requested:11:22:1",
                },
                idempotency_key="MATERIAL_UNMOUNTED:source-pick-requested:11:22:1:PKG-001:SINGLE_LAYER_A",
            ),
            RuntimeIntent.update_context(
                {
                    "sorting": {
                        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                        "current_material": {
                            "material_identity_key": "material:PKG-001",
                            "pkg_code": "PKG-001",
                            "source_rack_position_code": "SINGLE_LAYER_A",
                        },
                    }
                }
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_UNMOUNTED"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_source_pick_resource_fact_without_handoff_evidence_does_not_record_handoff_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        awaiting_command_id=None,
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
            }
        },
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    resource_projection = RecordingResourceProjectionService()

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    record_call = AsyncMock()
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_pick_success",
        record_call,
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload={
                    "material_identity_key": "material:PKG-001",
                    "pkg_code": "PKG-001",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                },
                idempotency_key="MATERIAL_UNMOUNTED:manual:PKG-001:SINGLE_LAYER_A",
            ),
            RuntimeIntent.update_context(
                {
                    "sorting": {
                        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                        "current_material": {
                            "material_identity_key": "material:PKG-001",
                            "pkg_code": "PKG-001",
                            "source_rack_position_code": "SINGLE_LAYER_A",
                        },
                    }
                }
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_UNMOUNTED"
    record_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciling_source_pick_resource_fact_does_not_record_handoff_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "source_pick_request": {
                    "handoff_demand_id": 11,
                    "handoff_source_item_id": 22,
                    "claim_attempt_no": 1,
                    "event_id": "source-pick-requested:11:22:1",
                    "target_workline_code": "SMT_SORTER_01",
                    "manifest_contract_version": "v1",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "target_rack_position_code": "TARGET_STATION",
                    "route_evidence": {},
                },
            }
        }
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService(status="RECONCILING")

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    record_call = AsyncMock(return_value=SimpleNamespace(outcome="advanced", advanced=True, already_terminal=False))
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_pick_success",
        record_call,
    )
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_manual_hold",
        AsyncMock(),
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload={
                    "material_identity_key": "material:PKG-001",
                    "pkg_code": "PKG-001",
                    "source_rack_position_code": "SINGLE_LAYER_A",
                    "source_pick_request_event_id": "source-pick-requested:11:22:1",
                },
                idempotency_key="MATERIAL_UNMOUNTED:source-pick-requested:11:22:1:PKG-001:SINGLE_LAYER_A",
            ),
            RuntimeIntent.update_context(
                {
                    "sorting": {
                        "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                        "current_material": {"material_identity_key": "material:PKG-001"},
                    }
                }
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_UNMOUNTED"
    assert "current_material" not in session.context_json["sorting"]
    assert session.status == "MANUAL_HOLD"
    record_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_target_place_terminal_success_records_sorted_after_context_cleanup_and_claims_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(context_json={"sorting": _handoff_sorting_context()})
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"].payload_json = {
        "command_code": "TARGET-CMD-001",
        "command_type": COMMAND_TARGET_PLACE,
        "result": "SUCCESS",
        "data": {},
    }
    resource_projection = RecordingResourceProjectionService()
    ledger_calls: list[dict[str, Any]] = []
    claim_calls: list[dict[str, Any]] = []

    async def record_terminal(_db: Any, **kwargs: Any) -> SimpleNamespace:
        ledger_calls.append(kwargs)
        assert _db is db
        assert kwargs["session"] is session
        assert kwargs["terminal_status"] == "SORTED"
        assert "current_material" not in session.context_json["sorting"]
        assert "pending_target_placement" not in session.context_json["sorting"]
        return SimpleNamespace(
            outcome="advanced",
            advanced=len(ledger_calls) == 1,
            already_terminal=len(ledger_calls) > 1,
            current_demand_id=11,
        )

    async def claim_next(_db: Any, **kwargs: Any) -> SimpleNamespace:
        claim_calls.append(kwargs)
        assert _db is db
        assert kwargs["demand_id"] == 11
        return SimpleNamespace(kind="EMPTY")

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        record_terminal,
        raising=False,
    )
    monkeypatch.setattr(smt_inbound_handoff_service, "claim_next_source_item", claim_next)
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    intents = await SmtSortingInboundFlowService().handle_target_place_success(
        SimpleNamespace(session=session),
        ctx["inbox"],
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.RESOURCE_FACT, RuntimeIntentKind.COMPLETE]
    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(ctx, intents)

    assert session.status == "COMPLETED"
    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    assert [call["terminal_status"] for call in ledger_calls] == ["SORTED"]
    assert claim_calls == [{"trace_id": "trace-runtime", "demand_id": 11}]


@pytest.mark.asyncio
async def test_target_place_terminal_success_does_not_complete_session_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = SimpleNamespace(
        id=22,
        handoff_demand_id=11,
        status=SmtInboundHandoffSourceItemStatus.PICKED,
        sorting_session_id=123,
        completed_at=None,
        failure_code="OLD_FAILURE",
        failure_message="旧失败",
        next_attempt_at=datetime(2026, 1, 1, 0, 3, 0),
    )
    demand = SimpleNamespace(
        id=11,
        status=SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS,
        failure_code=None,
        failure_message=None,
    )
    session = _session(
        id=123,
        context_json={"sorting": _handoff_sorting_context()},
    )
    db = RecordingDb(demand)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"].payload_json = {
        "command_code": "TARGET-CMD-001",
        "command_type": COMMAND_TARGET_PLACE,
        "result": "SUCCESS",
        "data": {},
    }
    resource_projection = RecordingResourceProjectionService()
    handoff_service = SmtInboundHandoffService(repository=FakeTerminalRepository(item))
    persisted_completions: list[dict[str, Any]] = []
    emitted_timelines: list[dict[str, Any]] = []

    async def persist_completed(_repo: Any, _db: Any, **kwargs: Any) -> None:
        persisted_completions.append(kwargs)

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        emitted_timelines.append(kwargs)

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        handoff_service.record_source_item_terminal_result,
        raising=False,
    )
    monkeypatch.setattr(
        smt_inbound_handoff_service, "claim_next_source_item", AsyncMock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_completed",
        persist_completed,
    )
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    intents = await SmtSortingInboundFlowService().handle_target_place_success(
        SimpleNamespace(session=session),
        ctx["inbox"],
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(ctx, intents)

    assert session.status == "COMPLETED"
    assert item.status == SmtInboundHandoffSourceItemStatus.SORTED
    assert item.completed_at is not None
    assert item.failure_code is None
    assert demand.failure_code is None
    assert len(persisted_completions) == 1
    assert [timeline["action_type"].value for timeline in emitted_timelines] == ["SESSION_COMPLETED"]


@pytest.mark.asyncio
async def test_ng_place_terminal_success_records_skipped_after_context_cleanup_and_claims_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sorting = _handoff_sorting_context()
    sorting.pop("pending_target_placement", None)
    sorting["current_material"]["ng_status"] = "MOVING_TO_NG"
    session = _session(context_json={"sorting": sorting}, current_material_unit_id=1001)
    material_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location=None,
        current_session_id=123,
    )
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"].payload_json = {
        "command_code": "NG-CMD-001",
        "command_type": COMMAND_NG_PLACE,
        "result": "SUCCESS",
        "data": {},
        "ng_location": "NG-01",
        "ng_reason_code": "LOCAL_SORTING_NG",
    }
    ledger_calls: list[dict[str, Any]] = []
    claim_calls: list[dict[str, Any]] = []

    async def record_terminal(_db: Any, **kwargs: Any) -> SimpleNamespace:
        ledger_calls.append(kwargs)
        assert _db is db
        assert kwargs["session"] is session
        assert kwargs["terminal_status"] == "SKIPPED"
        assert kwargs["terminal_evidence"]["ng_command_payload"]["ng_location"] == "NG-01"
        assert "current_material" not in session.context_json["sorting"]
        return SimpleNamespace(
            outcome="advanced",
            advanced=len(ledger_calls) == 1,
            already_terminal=len(ledger_calls) > 1,
            current_demand_id=11,
        )

    async def claim_next(_db: Any, **kwargs: Any) -> SimpleNamespace:
        claim_calls.append(kwargs)
        return SimpleNamespace(kind="EMPTY")

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        record_terminal,
        raising=False,
    )
    monkeypatch.setattr(smt_inbound_handoff_service, "claim_next_source_item", claim_next)
    record_ng_flow = AsyncMock()
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    intents = await SmtSortingInboundFlowService().handle_ng_place_success(
        SimpleNamespace(session=session), ctx["inbox"]
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS,
        RuntimeIntentKind.COMPLETE,
    ]
    await RuntimeIntentEffectApplier().apply(ctx, intents)

    assert material_unit.status == MaterialUnitStatus.NG
    assert material_unit not in db.deleted
    assert material_unit.current_session_id is None
    assert session.current_material_unit_id is None
    assert session.status == "COMPLETED"
    record_ng_flow.assert_awaited_once()
    assert [call["terminal_status"] for call in ledger_calls] == ["SKIPPED"]
    assert claim_calls == [{"trace_id": "trace-runtime", "demand_id": 11}]


@pytest.mark.asyncio
async def test_cleanup_completed_material_unit_only_clears_current_ng_unit_session_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(id=123, status="COMPLETED", current_material_unit_id=1001)
    completed_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.COMPLETED,
        current_location="TARGET:1",
        current_session_id=123,
    )
    db = MaterialUnitDb(material_unit=completed_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.COMPLETED.value,
                clear_session_reference=True,
            ),
            RuntimeIntent.complete({}),
        ],
    )

    assert completed_unit not in db.deleted
    assert completed_unit.current_session_id == 123
    assert session.current_material_unit_id == 1001


@pytest.mark.asyncio
async def test_ng_cleanup_survives_cross_batch_recovery_from_manual_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """NG 冲突进 MANUAL_HOLD 后跨 inbox 批次恢复到 COMPLETE 时清理 NG 料盘引用。

    待清理 ID 持久化在 session.context_json，不依赖 ctx 跨批次存活。
    """
    # 第一批次：NG 搬运成功，登记清理 ID 并持久化；Session 尚未 COMPLETED（将被 manual_hold）。
    session = _session(id=123, status="WAITING_DEVICE_RESULT", current_material_unit_id=1001)
    ng_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-NG-001",
        material_identity_key="MAT-NG",
        six_in_one={"PkgID": "PKG-NG-001"},
        status=MaterialUnitStatus.NG,
        current_location=None,
        current_session_id=123,
    )
    db = MaterialUnitDb(material_unit=ng_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.NG.value,
                clear_session_reference=True,
            ),
        ],
    )

    # 清理 ID 已持久化到 context_json（本批次未 COMPLETE，不触发清理）。
    assert session.context_json["_runtime_pending_material_unit_cleanup_ids"] == [1001]
    assert ng_unit.current_session_id == 123
    assert session.current_material_unit_id == 1001

    # 第二批次：跨 inbox 恢复，ctx 全新，Session 进入 COMPLETED。
    session.status = "COMPLETED"
    ctx2 = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    await RuntimeIntentEffectApplier().apply(
        ctx2,
        [RuntimeIntent.complete({})],
    )

    # 跨批次恢复后仍清理 NG 料盘的 Session 引用，不残留永久孤儿。
    assert ng_unit.current_session_id is None
    assert session.current_material_unit_id is None
    # 持久化登记清空，避免重复处理。
    assert session.context_json["_runtime_pending_material_unit_cleanup_ids"] == []


@pytest.mark.asyncio
async def test_clear_session_reference_does_not_take_over_other_session_material_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(id=123, current_material_unit_id=None)
    other_session_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.NG,
        current_location=None,
        current_session_id=999,
    )
    db = MaterialUnitDb(material_unit=other_session_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", AsyncMock())
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.NG.value,
                clear_session_reference=True,
            ),
            RuntimeIntent.complete({}),
        ],
    )

    assert other_session_unit.current_session_id == 999
    assert other_session_unit not in db.deleted
    assert session.current_material_unit_id is None


@pytest.mark.asyncio
async def test_ng_place_completion_conflict_keeps_material_unit_for_manual_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sorting = _handoff_sorting_context()
    sorting.pop("pending_target_placement", None)
    sorting["current_material"]["ng_status"] = "MOVING_TO_NG"
    session = _session(context_json={"sorting": sorting}, current_material_unit_id=1001)
    material_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location=None,
        current_session_id=123,
    )
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"].payload_json = {
        "command_code": "NG-CMD-CONFLICT",
        "command_type": COMMAND_NG_PLACE,
        "result": "SUCCESS",
        "data": {},
        "ng_location": "NG-01",
        "ng_reason_code": "LOCAL_SORTING_NG",
    }
    long_identity = f"test-material:{'X' * 300}"

    async def raise_conflict(*_args: Any, **_kwargs: Any) -> None:
        raise NgMaterialConflictError(
            material_identity_key=long_identity,
            existing_item=SimpleNamespace(id=8801, created_from_runtime_hold_id=9901),
            evidence={
                "reason_code": "NG_MATERIAL_CONFLICT",
                "material_identity_key": long_identity,
                "new_source_command_id": 8802,
            },
        )

    async def create_hold(_db: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(id=7701, **kwargs)

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        AsyncMock(),
        raising=False,
    )
    monkeypatch.setattr(smt_inbound_handoff_service, "claim_next_source_item", AsyncMock())
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", raise_conflict)
    monkeypatch.setattr(runtime_hold_creation_service, "create_for_resource_reconciliation", create_hold)
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))

    intents = await SmtSortingInboundFlowService().handle_ng_place_success(
        SimpleNamespace(session=session), ctx["inbox"]
    )

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS,
        RuntimeIntentKind.COMPLETE,
    ]
    await RuntimeIntentEffectApplier().apply(ctx, intents)

    assert session.status == "MANUAL_HOLD"
    assert session.current_material_unit_id == 1001
    assert material_unit.current_session_id == 123
    assert db.deleted == []


@pytest.mark.asyncio
async def test_reconciling_target_place_resource_fact_does_not_record_sorted_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.IN_TRANSIT,
        reconciliation_from_state=None,
        current_location="SORTING:SCAN",
        current_session_id=123,
    )
    session = _session(context_json={"sorting": _handoff_sorting_context()})
    session.current_material_unit_id = 1001
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["inbox"].payload_json = {"command_code": "TARGET-CMD-RECONCILING"}
    resource_projection = RecordingResourceProjectionService(status="RECONCILING")

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    record_terminal = AsyncMock()
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        record_terminal,
        raising=False,
    )
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_manual_hold",
        AsyncMock(),
    )

    intents = await SmtSortingInboundFlowService().handle_target_place_success(
        SimpleNamespace(session=session),
        ctx["inbox"],
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(ctx, intents)

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    assert session.status == "MANUAL_HOLD"
    assert session.context_json["sorting"]["current_material"]["material_identity_key"] == "material:PKG-001"
    assert material_unit.status == MaterialUnitStatus.RECONCILING
    assert material_unit.reconciliation_from_state == MaterialUnitStatus.IN_TRANSIT
    record_terminal.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_smt_material_mounted_does_not_record_smt_terminal_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(context_json={"material_flow": {"context_schema_version": 1}})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService()

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    record_terminal = AsyncMock()
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        record_terminal,
        raising=False,
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={"resource_kind": "BIN_CELL", "resource_key": "generic:cell:1"},
            ),
            RuntimeIntent.update_context({"sorting": {"current_material": {"material_identity_key": "material:GEN"}}}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    record_terminal.assert_not_awaited()


@pytest.mark.asyncio
async def test_smt_material_mounted_without_source_pick_request_does_not_record_terminal_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(context_json={"sorting": _handoff_sorting_context(include_source_pick_request=False)})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService()

    from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service

    record_terminal = AsyncMock()
    monkeypatch.setattr(
        smt_inbound_handoff_service,
        "record_source_item_terminal_result",
        record_terminal,
        raising=False,
    )

    intents = await SmtSortingInboundFlowService().handle_target_place_success(
        SimpleNamespace(session=session),
        ctx["inbox"],
    )

    assert [intent.kind for intent in intents] == [RuntimeIntentKind.RESOURCE_FACT, RuntimeIntentKind.COMPLETE]
    monkeypatch.setattr(workline_effects, "_add_timeline", AsyncMock(return_value=1))
    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(ctx, intents)

    assert session.status == "COMPLETED"
    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    record_terminal.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_terminal_result_rejects_source_item_bound_to_other_session() -> None:
    item = SimpleNamespace(
        id=22,
        handoff_demand_id=11,
        status=SmtInboundHandoffSourceItemStatus.PICKED,
        sorting_session_id=999,
        completed_at=None,
        failure_code="OLD_FAILURE",
        failure_message="旧失败",
        next_attempt_at=datetime(2026, 1, 1, 0, 3, 0),
    )
    demand = SimpleNamespace(
        id=11,
        status=SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS,
        failure_code=None,
        failure_message=None,
    )
    session = _session(
        id=123,
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "source_pick_request": {
                    "handoff_demand_id": demand.id,
                    "handoff_source_item_id": item.id,
                },
            }
        },
    )
    service = SmtInboundHandoffService(repository=FakeTerminalRepository(item))
    db = RecordingDb(demand)

    with pytest.raises(ValueError, match="sorting_session"):
        await service.record_source_item_terminal_result(
            db,
            session=session,
            terminal_status="SORTED",
            trace_id="trace-session-mismatch",
            terminal_evidence={"target_command_payload": {"command_code": "TARGET-CMD-001"}},
        )

    assert item.status == SmtInboundHandoffSourceItemStatus.PICKED
    assert item.completed_at is None
    assert item.failure_code == "OLD_FAILURE"
    assert "handoff_terminal_result" not in session.context_json["sorting"]


@pytest.mark.asyncio
@pytest.mark.parametrize("session_status", ["FAILED", "CANCELLED"])
async def test_terminal_conflict_keeps_terminal_session_status(
    monkeypatch: pytest.MonkeyPatch,
    session_status: str,
) -> None:
    item = SimpleNamespace(
        id=22,
        handoff_demand_id=11,
        status=SmtInboundHandoffSourceItemStatus.SORTED,
        sorting_session_id=123,
        completed_at=datetime(2026, 1, 1, 0, 3, 0),
        failure_code=None,
        failure_message=None,
        next_attempt_at=None,
    )
    demand = SimpleNamespace(
        id=11,
        status=SmtInboundHandoffDemandStatus.SORTING_IN_PROGRESS,
        failure_code=None,
        failure_message=None,
    )
    session = _session(
        id=123,
        status=session_status,
        failure_domain="EXISTING_TERMINAL",
        failure_code="EXISTING_FAILURE",
        failure_message="existing terminal failure",
        context_json={
            "sorting": {
                "context_schema_version": SORTING_CONTEXT_SCHEMA_VERSION,
                "source_pick_request": {
                    "handoff_demand_id": demand.id,
                    "handoff_source_item_id": item.id,
                },
            }
        },
    )
    service = SmtInboundHandoffService(repository=FakeTerminalRepository(item))
    db = RecordingDb(demand)
    persist_manual_hold = AsyncMock()
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_manual_hold",
        persist_manual_hold,
    )

    result = await service.record_source_item_terminal_result(
        db,
        session=session,
        terminal_status="SKIPPED",
        command_id=9002,
        trace_id="trace-terminal-conflict",
        terminal_evidence={"ng_command_payload": {"command_code": "NG-CMD-001"}},
    )

    assert result.outcome == "manual_hold"
    assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
    assert item.failure_code == "PLUGIN_CONTRACT_INVALID"
    assert demand.status == SmtInboundHandoffDemandStatus.MANUAL_HOLD
    assert demand.failure_code == "PLUGIN_CONTRACT_INVALID"
    assert session.status == session_status
    assert session.failure_domain == "EXISTING_TERMINAL"
    assert session.failure_code == "EXISTING_FAILURE"
    assert session.failure_message == "existing terminal failure"
    terminal_result = session.context_json["sorting"]["handoff_terminal_result"]
    assert terminal_result["terminal_status"] == "SKIPPED"
    assert terminal_result["conflict"] is True
    assert terminal_result["evidence"]["ng_command_payload"]["command_code"] == "NG-CMD-001"
    persist_manual_hold.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_fact_syncs_waiting_rack_operation_status() -> None:
    session = _session(
        context_json={
            "waiting_rack_operation_key": "rack-operation:trace-runtime",
            "rack_operation": {"operation_key": "rack-operation:trace-runtime", "status": "PENDING"},
        }
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService()
    rack_operation_service = RecordingRackOperationStatusService()

    await RuntimeIntentEffectApplier(
        resource_projection_service=resource_projection,
        rack_operation_service=rack_operation_service,
    ).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="RACK_ARRIVED",
                payload={"rack_code": "RACK-3CELL-001", "position_code": "SINGLE_LAYER_A"},
                idempotency_key="RACK_ARRIVED:rack-operation:trace-runtime",
            )
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "RACK_ARRIVED"
    assert rack_operation_service.calls == [
        {"db": ctx["db"], "operation_key": "rack-operation:trace-runtime"},
    ]


@pytest.mark.asyncio
async def test_reconciling_resource_fact_stops_following_intents(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(context_json={"pkg_id": "PKG-001"})
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()
    resource_projection = RecordingResourceProjectionService(status="RECONCILING")

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="RACK_ARRIVED",
                payload={"rack_code": "RACK-001", "workline_code": "WL-001", "position_code": "SINGLE_LAYER_A"},
                idempotency_key="RACK_ARRIVED:conflict",
            ),
            RuntimeIntent.update_context({"rack_operation": {"status": "ARRIVED"}}),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "RACK_ARRIVED"
    assert session.context_json == {"pkg_id": "PKG-001"}
    assert session.status == "MANUAL_HOLD"
    assert session.failure_code == "RESOURCE_PROJECTION_RECONCILING"
    record_ng_flow.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_resource_fact_skips_following_material_unit_status_update() -> None:
    material_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location="LATEST-BIN:1",
        current_session_id=801,
    )
    session = _session(id=902, current_material_unit_id=1001, context_json={})
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    resource_projection = RecordingResourceProjectionService(status="DUPLICATE")

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={
                    "bin_code": "OLD-BIN",
                    "bin_cell_index": "9",
                    "material_identity_key": "MAT:OLD",
                    "pkg_code": "PKG-OLD",
                },
                idempotency_key="MATERIAL_MOUNTED:old",
            ),
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.STORED.value,
                current_location="OLD-BIN:9",
            ),
            RuntimeIntent.update_context({"resource_fact_replayed": True}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_MOUNTED"
    assert material_unit.status == MaterialUnitStatus.IN_TRANSIT
    assert material_unit.current_location == "LATEST-BIN:1"
    assert material_unit.current_session_id == 801
    assert session.context_json == {"resource_fact_replayed": True}


@pytest.mark.asyncio
async def test_duplicate_resource_fact_skips_following_material_unit_creation() -> None:
    existing = SimpleNamespace(
        id=1002,
        pkg_code="PKG-001",
        material_identity_key="latest-key",
        six_in_one={"PkgID": "PKG-001", "latest": True},
        status=MaterialUnitStatus.STORED,
        current_location="LATEST-BIN:1",
        current_session_id=800,
    )
    session = _session(id=903, current_material_unit_id=1002, context_json={})
    db = MaterialUnitDb(material_unit=existing)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    resource_projection = RecordingResourceProjectionService(status="DUPLICATE")

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_UNMOUNTED",
                payload={
                    "bin_code": "OLD-BIN",
                    "bin_cell_index": "9",
                    "material_identity_key": "MAT:OLD",
                    "pkg_code": "PKG-001",
                },
                idempotency_key="MATERIAL_UNMOUNTED:old",
            ),
            RuntimeIntent.create_material_unit(
                pkg_code="PKG-001",
                material_identity_key="old-key",
                six_in_one={"PkgID": "PKG-001", "old": True},
                status=MaterialUnitStatus.IN_TRANSIT.value,
            ),
            RuntimeIntent.update_context({"source_pick_replayed": True}),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "MATERIAL_UNMOUNTED"
    assert db.added == []
    assert existing.material_identity_key == "latest-key"
    assert existing.six_in_one == {"PkgID": "PKG-001", "latest": True}
    assert existing.status == MaterialUnitStatus.STORED
    assert existing.current_location == "LATEST-BIN:1"
    assert existing.current_session_id == 800
    assert session.context_json == {"source_pick_replayed": True}


@pytest.mark.asyncio
async def test_duplicate_resource_fact_skip_only_applies_to_immediate_material_unit_intent() -> None:
    material_unit = SimpleNamespace(
        id=1001,
        status=MaterialUnitStatus.IN_TRANSIT,
        current_location=None,
        current_session_id=None,
    )
    session = _session(id=902, current_material_unit_id=1001, context_json={})
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    resource_projection = RecordingResourceProjectionService(status="DUPLICATE")

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="MATERIAL_MOUNTED",
                payload={
                    "bin_code": "OLD-BIN",
                    "bin_cell_index": "9",
                    "material_identity_key": "MAT:OLD",
                    "pkg_code": "PKG-OLD",
                },
                idempotency_key="MATERIAL_MOUNTED:old",
            ),
            RuntimeIntent.update_context({"resource_fact_replayed": True}),
            RuntimeIntent.update_material_unit_status(
                material_unit_id=1001,
                status=MaterialUnitStatus.STORED.value,
                current_location="NEW-BIN:1",
            ),
        ],
    )

    assert session.context_json == {"resource_fact_replayed": True}
    assert material_unit.status == MaterialUnitStatus.STORED
    assert material_unit.current_location == "NEW-BIN:1"
    assert material_unit.current_session_id == 902


@pytest.mark.asyncio
async def test_reconciling_resource_fact_moves_session_to_manual_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.STORED,
        reconciliation_from_state=None,
        current_location="BIN-001:1",
        current_session_id=123,
    )
    session = _session(context_json={"pkg_id": "PKG-001"}, current_material_unit_id=1001)
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    persist_manual_hold = AsyncMock()
    resource_projection = RecordingResourceProjectionService(status="RECONCILING")

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_manual_hold",
        persist_manual_hold,
    )

    await RuntimeIntentEffectApplier(resource_projection_service=resource_projection).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="BIN_MOUNTED",
                payload={"rack_code": "RACK-3CELL-001", "bin_mounts": []},
                idempotency_key="BIN_MOUNTED:conflict",
            ),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert session.status == "MANUAL_HOLD"
    assert session.current_wait_type is None
    assert session.failure_domain == "RESOURCE_RECONCILIATION"
    assert session.failure_code == "RESOURCE_PROJECTION_RECONCILING"
    assert session.failure_message == "资源事实投影进入调和状态，等待人工处理 RuntimeHold"
    assert material_unit.status == MaterialUnitStatus.RECONCILING
    assert material_unit.reconciliation_from_state == MaterialUnitStatus.STORED
    persist_manual_hold.assert_awaited_once_with(
        ctx["db"],
        session_id=session.id,
        occurred_at=ctx["now"],
        failure_domain="RESOURCE_RECONCILIATION",
        failure_code="RESOURCE_PROJECTION_RECONCILING",
        failure_message="资源事实投影进入调和状态，等待人工处理 RuntimeHold",
    )


@pytest.mark.asyncio
async def test_resource_reservation_intent_is_applied_before_command(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="CONV01", device_role="CONVEYOR")
    target = SimpleNamespace(id=2, device_code="OUT01", device_role="OUTPUT_ARM", upstream_device_id=1)
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"CONVEYOR": [source], "OUTPUT_ARM": [target]}
    reservation_service = RecordingBinCellReservationService()

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-OUTPUT",
            task_type="PICK_AND_PUT",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier(bin_cell_reservation_service=reservation_service).apply(
        ctx,
        [
            RuntimeIntent.resource_reservation(
                operation="CLAIM_BIN_CELL",
                payload={"pkg_code": "PKG-001", "bin_code": "BIN-001", "bin_cell_index": "4"},
                idempotency_key="CLAIM_BIN_CELL:123:BIN-001:4:PKG-001",
            ),
            RuntimeIntent.command(
                action="PICK_AND_PUT",
                payload={"barcode": "PKG-001", "bin_id": "BIN-001", "bin_cell_index": "4"},
                destination=Destination.role("OUTPUT_ARM"),
                timeout_seconds=300,
            ),
        ],
    )

    assert reservation_service.calls[0]["operation"] == "CLAIM_BIN_CELL"
    assert reservation_service.calls[0]["session"] is session
    assert created_payloads[0]["device_id"] == 2


@pytest.mark.asyncio
async def test_reconciling_resource_reservation_stops_following_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    material_unit = SimpleNamespace(
        id=1001,
        pkg_code="PKG-001",
        status=MaterialUnitStatus.IN_TRANSIT,
        reconciliation_from_state=None,
        current_location="SORTING:SCAN",
        current_session_id=123,
    )
    session = _session(context_json={"pkg_id": "PKG-001"}, current_material_unit_id=1001)
    db = MaterialUnitDb(material_unit=material_unit)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()
    reservation_service = RecordingBinCellReservationService(status="RECONCILING")

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier(bin_cell_reservation_service=reservation_service).apply(
        ctx,
        [
            RuntimeIntent.resource_reservation(
                operation="CONSUME_BIN_CELL",
                payload={"bin_code": "BIN-001", "bin_cell_index": "4"},
                idempotency_key="CONSUME_BIN_CELL:123:BIN-001:4",
            ),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert reservation_service.calls[0]["operation"] == "CONSUME_BIN_CELL"
    assert session.context_json == {"pkg_id": "PKG-001"}
    assert session.status == "WAITING_DEVICE_RESULT"
    assert material_unit.status == MaterialUnitStatus.RECONCILING
    assert material_unit.reconciliation_from_state == MaterialUnitStatus.IN_TRANSIT
    record_ng_flow.assert_not_awaited()


@pytest.mark.asyncio
async def test_consume_bin_cell_owner_mismatch_creates_hold_and_runtime_does_not_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.workline.models.bin_cell_reservation import BinCellReservationStatus, WorklineBinCellReservation
    from src.app.workline.models.safety import WorkLineRuntimeStatus
    from src.app.workline.services.bin_cell_reservation_service import WorklineBinCellReservationService
    from src.app.workline.services.runtime_hold_creation_service import RuntimeHoldCreationService

    class ReservationRepo:
        def __init__(self) -> None:
            self.consumed: list[WorklineBinCellReservation] = []

        async def get_active_by_bin_cell(
            self,
            _db: object,
            *,
            bin_code: str,
            bin_cell_index: str,
        ) -> WorklineBinCellReservation:
            assert bin_code == "BIN-001"
            assert bin_cell_index == "4"
            return WorklineBinCellReservation(
                reservation_key="reserve:old",
                workline_id=1001,
                workline_code="SMT_SORTER_01",
                session_id=2001,
                trace_id="trace-old",
                pkg_code="PKG-OLD",
                bin_code="BIN-001",
                bin_cell_code="BIN-001-4",
                bin_cell_index="4",
                reservation_status=BinCellReservationStatus.PLANNED,
                reserved_at=datetime(2026, 1, 1, 0, 0, 0),
            )

        async def mark_consumed(
            self,
            _db: object,
            reservation: WorklineBinCellReservation,
            *,
            consumed_at: datetime,
        ) -> WorklineBinCellReservation:
            reservation.consumed_at = consumed_at
            self.consumed.append(reservation)
            return reservation

    class MaterialMountRepo:
        async def get_active_by_bin_cell(self, _db: object, *, bin_code: str, bin_cell_index: str) -> object | None:
            return None

    class RuntimeHoldRepo:
        def __init__(self) -> None:
            self.created: list[dict[str, Any]] = []

        async def create_open_hold(self, _db: object, **data: Any) -> SimpleNamespace:
            self.created.append(data)
            return SimpleNamespace(id=9901, **data)

    class WorkLineRepo:
        def __init__(self, workline: SimpleNamespace) -> None:
            self.workline = workline

        async def get_for_update(self, _db: object, workline_id: int) -> SimpleNamespace:
            assert workline_id == 1001
            return self.workline

    session = _session(id=2002, context_json={"pkg_id": "PKG-001"})
    workline = SimpleNamespace(
        id=1001,
        line_code="SMT_SORTER_01",
        plugin_key="demo_plugin",
        contract_version="1.0",
        runtime_status=WorkLineRuntimeStatus.READY,
        stopped_at=None,
        stopped_reason=None,
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    ctx["workline"] = workline
    emit_timeline = AsyncMock()
    record_ng_flow = AsyncMock()
    reservation_repo = ReservationRepo()
    hold_repo = RuntimeHoldRepo()
    runtime_hold_creator = RuntimeHoldCreationService(
        repository=hold_repo,
        workline_repository=WorkLineRepo(workline),
    )
    reservation_service = WorklineBinCellReservationService(
        reservation_repository=reservation_repo,
        material_mount_repository=MaterialMountRepo(),
        runtime_hold_creator=runtime_hold_creator,
    )

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(ng_return_item_service, "record_completed_ng_flow", record_ng_flow)

    await RuntimeIntentEffectApplier(bin_cell_reservation_service=reservation_service).apply(
        ctx,
        [
            RuntimeIntent.resource_reservation(
                operation="CONSUME_BIN_CELL",
                payload={"bin_code": "BIN-001", "bin_cell_index": "4"},
                idempotency_key="CONSUME_BIN_CELL:2002:CMD-OUTPUT-001:BIN-001:4",
            ),
            RuntimeIntent.complete({"material_mounted": True}),
        ],
    )

    assert hold_repo.created[0]["source_reason"] == "BIN_CELL_RESERVATION_OWNER_MISMATCH"
    assert workline.runtime_status == WorkLineRuntimeStatus.RECONCILING
    assert workline.stopped_reason == "BIN_CELL_RESERVATION_OWNER_MISMATCH"
    assert reservation_repo.consumed == []
    assert session.context_json == {"pkg_id": "PKG-001"}
    assert session.status == "WAITING_DEVICE_RESULT"
    assert getattr(ctx["orch_result"], "complete", False) is False
    record_ng_flow.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_ng_writes_business_decision_timeline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=_session(status="RUNNING"))

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [RuntimeIntent.mark_ng(reason_code="SCAN_NG", message="扫码判定 NG", payload={"barcode": "BAD-001"})],
    )

    assert captured[0]["message"] == "扫码判定 NG"
    assert captured[0]["payload"]["reason_code"] == "SCAN_NG"
    assert captured[0]["payload"]["evidence"] == {"barcode": "BAD-001"}


@pytest.mark.asyncio
async def test_block_intent_holds_session_without_command_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    session = _session()
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.block(
                scope=BlockScope.MATERIAL,
                reason_code="MATERIAL_BLOCKED",
                message="物料需要人工处理",
                suggested_action="检查标签",
                payload={"evidence_key": "EVD-1234"},
            )
        ],
    )

    assert session.status == "MANUAL_HOLD"
    assert session.current_wait_type is None
    assert session.awaiting_command_id is None
    assert session.failure_domain == "MATERIAL"
    assert session.failure_code == "MATERIAL_BLOCKED"
    assert db.add.call_count == 0
    db.execute.assert_awaited_once()
    assert captured[0]["payload"]["suggested_action"] == "检查标签"
    assert captured[0]["payload"]["evidence"] == {"evidence_key": "EVD-1234"}


@pytest.mark.asyncio
async def test_command_intent_creates_command_outbox_and_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    target = SimpleNamespace(id=2, device_code="CONV01", device_role="CONVEYOR", upstream_device_id=1)
    created_command = SimpleNamespace(
        id=88,
        command_code="CMD-TEST-001",
        task_type="MOVE_FORWARD",
        priority=5,
        timeout_ms=30000,
        params={"pkg_id": "PKG-001"},
    )
    created_payloads: list[dict[str, Any]] = []
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source], "CONVEYOR": [target]}
    generated_command_codes: list[tuple[str, int | None]] = []

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return created_command

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr(
        workline_effects,
        "_build_command_code",
        lambda task_type, *, session_id=None: (
            generated_command_codes.append((task_type, session_id)) or "CMD-20260101-S123-MOVE_FORWARD-ABCDEF12"
        ),
    )
    monkeypatch.setattr(
        "src.app.device.repositories.command_repository.DeviceCommandRepository.create",
        fake_create,
    )
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MOVE_FORWARD",
                payload={"pkg_id": "PKG-001"},
                destination=Destination.role("CONVEYOR"),
                timeout_seconds=300,
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 2
    assert created_payloads[0]["command_code"] == "CMD-20260101-S123-MOVE_FORWARD-ABCDEF12"
    assert created_payloads[0]["task_type"] == "MOVE_FORWARD"
    assert generated_command_codes == [("MOVE_FORWARD", 123)]
    assert session.status == "WAITING_DEVICE_RESULT"
    assert session.awaiting_command_id == 88
    db.execute.assert_awaited_once()
    assert db.add.call_count == 1
    assert [timeline["related_command_id"] for timeline in timelines] == [88, 88]
    assert timelines[0]["payload"]["task_type"] == "MOVE_FORWARD"
    assert "command_type" not in timelines[0]["payload"]


@pytest.mark.asyncio
async def test_command_intent_without_destination_uses_device_role_as_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = SimpleNamespace(id=1, device_code="ARM03", device_role="ROUGH_SORTER_INPUT_ARM")
    target = SimpleNamespace(
        id=2,
        device_code="PIPELINE02",
        device_role="ROUGH_SORTER_CONVEYOR",
        upstream_device_id=1,
    )
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"ROUGH_SORTER_INPUT_ARM": [source], "ROUGH_SORTER_CONVEYOR": [target]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-MOVE-FORWARD",
            task_type="MOVE_FORWARD",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MOVE_FORWARD",
                device_role="ROUGH_SORTER_CONVEYOR",
                payload={},
                timeout_seconds=30,
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 2
    assert db.add.call_args.args[0].target_code == "PIPELINE02"


@pytest.mark.asyncio
async def test_command_intent_uses_payload_timeout_when_intent_timeout_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    created_payloads: list[dict[str, Any]] = []
    created_command = SimpleNamespace(
        id=88,
        command_code="CMD-NO-TIMEOUT",
        task_type="MEASUREMENT_REEL",
        priority=5,
        timeout_ms=180000,
        params={"business_key": "PKG-001"},
    )
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(
        status="NEW",
        current_wait_type=None,
        awaiting_command_id=None,
        waiting_since=None,
        deadline_at=None,
        current_wait_timeout_seconds=None,
    )
    source = SimpleNamespace(id=1, device_code="ARM01")
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source]}
    ctx["current_status"] = "NEW"

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return created_command

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr(
        "src.app.device.repositories.command_repository.DeviceCommandRepository.create",
        fake_create,
    )
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"task_type": "MEASUREMENT_REEL", "timeout": 180000, "params": {"business_key": "PKG-001"}},
                destination=Destination.current(),
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 1
    assert created_payloads[0]["task_type"] == "MEASUREMENT_REEL"
    assert created_payloads[0]["params"] == {"business_key": "PKG-001"}
    assert session.status == "WAITING_DEVICE_RESULT"
    assert session.current_wait_type == "COMMAND_RESULT"
    assert session.awaiting_command_id == 88
    assert session.waiting_since == ctx["now"]
    assert session.current_wait_timeout_seconds == 180
    assert session.deadline_at is None
    db.execute.assert_awaited_once()
    assert [timeline["action_type"].value for timeline in timelines] == ["COMMAND_SENT", "WAIT_STARTED"]
    assert timelines[1]["payload"]["wait_token"] == "CMD-NO-TIMEOUT"
    assert timelines[1]["payload"]["deadline_seconds"] == 180


@pytest.mark.asyncio
async def test_external_request_intent_creates_external_outbox_and_immediate_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.external_request(
                dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
                target_code="WMS_RCS_FULL_BOX_EXCHANGE",
                payload={"rack_release_id": "release-001"},
                timeout_seconds=1800,
                source_system="WMS_RCS",
            )
        ],
    )

    outbox = db.add.call_args.args[0]
    assert outbox.dispatch_type == "EXTERNAL_HTTP"
    assert outbox.target_type == "HTTP_ENDPOINT"
    assert outbox.dispatch_key == "external:smt:release-001:FULL_BIN_EXCHANGE"
    assert outbox.target_code == "WMS_RCS_FULL_BOX_EXCHANGE"
    assert outbox.payload_json == {"rack_release_id": "release-001"}
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "EXTERNAL_HTTP"
    assert session.awaiting_command_id is None
    assert session.current_wait_timeout_seconds == 1800
    assert session.deadline_at == ctx["now"] + timedelta(seconds=1800)
    assert [timeline["action_type"].value for timeline in timelines] == ["EXTERNAL_CALL_STARTED", "WAIT_STARTED"]
    assert timelines[1]["payload"]["wait_token"] == "external:smt:release-001:FULL_BIN_EXCHANGE"


@pytest.mark.asyncio
async def test_rack_operation_request_creates_operation_tasks_and_waits_by_operation_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    operation_calls: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["workline"].line_code = "WL-SMT-01"

    class RecordingRackOperationService:
        async def request_operation_tasks(self, db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            operation_calls.append({"db": db, **kwargs})
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=1,
                    task_type="MOVE_RACK",
                    dispatch_key="rack-operation:rack-operation:trace-runtime:1:MOVE_RACK",
                    actions_json={"required": True},
                    rack_code="RACK-OLD",
                    source_position_code="SINGLE_LAYER_A",
                    target_position_code=None,
                ),
                SimpleNamespace(
                    id=902,
                    operation_key=kwargs["operation_key"],
                    sequence_no=2,
                    task_type="ALLOCATE_AND_MOVE_RACK",
                    dispatch_key="rack-operation:rack-operation:trace-runtime:2:ALLOCATE_AND_MOVE_RACK",
                    actions_json={"required": True},
                    rack_code=None,
                ),
            ]

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "work_position_code": "SINGLE_LAYER_A",
                    "new_rack_kind": "SINGLE_LAYER",
                    "move_out_target_position_role": "SMT_EMPTY_RACK_AREA",
                    "rack_tasks": [
                        {
                            "sequence_no": 1,
                            "task_type": "MOVE_RACK",
                            "rack_code": "RACK-OLD",
                            "rack_kind": "SINGLE_LAYER",
                            "source_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_EMPTY_RACK_AREA",
                        },
                        {
                            "sequence_no": 2,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                        },
                    ],
                    "trace_id": "trace-from-payload",
                },
                timeout_seconds=1800,
            )
        ],
    )

    assert db.add.call_count == 0
    assert len(operation_calls) == 1
    assert operation_calls[0]["db"] is db
    assert operation_calls[0]["session"] is session
    assert operation_calls[0]["workline"] is ctx["workline"]
    assert operation_calls[0]["operation_key"] == "rack-operation:trace-runtime"
    assert operation_calls[0]["operation_type"] == "REPLACE_CLASSIFIER_WORK_RACK"
    assert operation_calls[0]["target_code"] == "WMS_RCS_RACK_OPERATION"
    assert operation_calls[0]["task_specs"][0]["task_type"] == "MOVE_RACK"
    assert operation_calls[0]["task_specs"][1]["target_position_code"] == "SINGLE_LAYER_A"
    assert operation_calls[0]["trace_id"] == "trace-from-payload"
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RACK_OPERATION"
    assert session.awaiting_command_id is None
    assert session.current_wait_timeout_seconds == 1800
    assert session.deadline_at == ctx["now"] + timedelta(seconds=1800)
    assert session.context_json["waiting_rack_operation_key"] == "rack-operation:trace-runtime"
    assert session.context_json["rack_operation"]["operation_key"] == "rack-operation:trace-runtime"
    assert session.context_json["rack_operation"]["status"] == "PENDING"
    assert session.context_json["rack_operation"]["task_sequences"] == [1, 2]
    assert session.context_json["rack_operation"]["released_rack_codes"] == ["RACK-OLD"]
    assert [timeline["action_type"].value for timeline in timelines] == ["WAIT_STARTED"]
    assert timelines[0]["payload"]["wait_token"] == "rack-operation:trace-runtime"


@pytest.mark.asyncio
async def test_single_layer_rack_operation_creates_waiting_external_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    operation_calls: list[dict[str, Any]] = []
    created_outboxes: list[SimpleNamespace] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["workline"].line_code = "WL-SMT-01"

    contract = WmsTransportContractService().build_single_layer_rack_operation_request(
        business_demand_key="WMS-DEMAND-001",
        workline_code="WL-SMT-01",
        endpoint_code="SINGLE_LAYER_A",
        rack_kind="SINGLE_LAYER",
        rack_snapshot_ref="snapshot:WL-SMT-01:SINGLE_LAYER_A",
        operation_type="SUPPLY_SINGLE_LAYER_RACK",
        payload={
            "station": {"position_code": "SINGLE_LAYER_A"},
            "target_position_code": "SINGLE_LAYER_A",
            "rack_tasks": [
                {
                    "sequence_no": 1,
                    "task_type": "ALLOCATE_AND_MOVE_RACK",
                    "rack_kind": "SINGLE_LAYER",
                    "target_position_code": "SINGLE_LAYER_A",
                    "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                }
            ],
            "trace_id": "trace-single-layer-001",
        },
        timeout_seconds=1800,
    )

    class RecordingRackOperationService:
        async def request_operation_tasks(self, db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            operation_calls.append({"db": db, **kwargs})
            task_spec = kwargs["task_specs"][0]
            dispatch_key = "rack-operation:{operation_key}:{sequence_no}:{task_type}".format(
                operation_key=kwargs["operation_key"],
                sequence_no=task_spec["sequence_no"],
                task_type=task_spec["task_type"],
            )
            created_outboxes.append(
                SimpleNamespace(
                    dispatch_type="EXTERNAL_HTTP",
                    target_type="HTTP_ENDPOINT",
                    dispatch_key=dispatch_key,
                    target_code=kwargs["target_code"],
                    payload_json=task_spec["request_json"],
                )
            )
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=1,
                    task_type=task_spec["task_type"],
                    dispatch_key=dispatch_key,
                    actions_json={"required": True},
                    rack_code=None,
                    target_position_code=task_spec["target_position_code"],
                )
            ]

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [RuntimeIntent.rack_operation_request(**contract)],
    )

    assert db.add.call_count == 0
    assert operation_calls[0]["operation_key"] == "wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A"
    assert operation_calls[0]["target_code"] == "WMS_RCS_RACK_OPERATION"
    assert operation_calls[0]["trace_id"] == "trace-single-layer-001"
    assert operation_calls[0]["task_specs"][0]["request_json"]["business_demand_key"] == "WMS-DEMAND-001"
    assert operation_calls[0]["task_specs"][0]["request_json"]["station"]["position_code"] == "SINGLE_LAYER_A"
    assert created_outboxes[0].dispatch_type == "EXTERNAL_HTTP"
    assert created_outboxes[0].target_type == "HTTP_ENDPOINT"
    assert created_outboxes[0].dispatch_key == (
        "rack-operation:wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A:1:ALLOCATE_AND_MOVE_RACK"
    )
    assert created_outboxes[0].target_code == "WMS_RCS_RACK_OPERATION"
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RACK_OPERATION"
    assert session.context_json["waiting_rack_operation_key"] == (
        "wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A"
    )
    assert session.context_json["rack_operation"]["task_dispatch_keys"] == [
        "rack-operation:wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A:1:ALLOCATE_AND_MOVE_RACK"
    ]
    assert session.context_json["rack_operation"]["target_position_code"] == "SINGLE_LAYER_A"
    assert timelines[0]["payload"]["wait_token"] == "wms-rack-operation:WMS-DEMAND-001:WL-SMT-01:SINGLE_LAYER_A"


@pytest.mark.asyncio
async def test_rack_operation_station_lease_race_returns_resource_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None, context_json={})
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    persist_external_wait = AsyncMock()
    record_resource_wait = AsyncMock()

    class LosingRackOperationService:
        async def request_operation_tasks(self, db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            _ = db, kwargs
            raise ValueError("station dispatch lease is not available")

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_external_wait",
        persist_external_wait,
    )
    monkeypatch.setattr(
        "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_resource_wait",
        record_resource_wait,
    )

    result = await RuntimeIntentEffectApplier(rack_operation_service=LosingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "trace_id": "trace-runtime",
                    "rack_tasks": [
                        {
                            "sequence_no": 1,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                        }
                    ],
                },
                timeout_seconds=1800,
            )
        ],
    )

    assert result.disposition == WriteBackDisposition.RESOURCE_RETRY
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RESOURCE_WAIT"
    assert session.context_json["resource_wait"]["resource_kind"] == "STATION"
    assert session.context_json["resource_wait"]["resource_key"] == "station:SINGLE_LAYER_A"
    assert session.context_json["resource_wait"]["reason_code"] == "STATION_LEASE_CLAIM_FAILED"
    assert "rack_operation" not in session.context_json
    persist_external_wait.assert_awaited_once()
    record_resource_wait.assert_awaited_once()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_rack_operation_request_stores_operation_wait_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_calls: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(
        status="RUNNING",
        current_wait_type=None,
        awaiting_command_id=None,
        context_json={"rack_operation": {"status": "OLD"}},
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    class RecordingRackOperationService:
        async def request_operation_tasks(self, db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            operation_calls.append({"db": db, **kwargs})
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=2,
                    task_type="ALLOCATE_AND_MOVE_RACK",
                    dispatch_key="rack-operation:rack-operation:trace-runtime:2:ALLOCATE_AND_MOVE_RACK",
                    actions_json={"required": True},
                    rack_code=None,
                )
            ]

    async def capture_timeline(_ctx: dict[str, Any], **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "work_position_code": "SINGLE_LAYER_A",
                    "new_rack_kind": "SINGLE_LAYER",
                    "move_out_target_position_role": "SMT_EMPTY_RACK_AREA",
                    "rack_tasks": [
                        {
                            "sequence_no": 2,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                        }
                    ],
                },
                timeout_seconds=1800,
            )
        ],
    )

    assert operation_calls[0]["trace_id"] == "trace-runtime"
    assert session.context_json["waiting_rack_operation_key"] == "rack-operation:trace-runtime"
    assert session.context_json["rack_operation"]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_rack_operation_request_carries_material_context_into_task_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_calls: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    class RecordingRackOperationService:
        async def request_operation_tasks(self, _db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            operation_calls.append(kwargs)
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=1,
                    task_type="ALLOCATE_AND_MOVE_RACK",
                    dispatch_key="rack-operation:rack-operation:trace-runtime:1:ALLOCATE_AND_MOVE_RACK",
                    actions_json={"required": True},
                    rack_code=None,
                )
            ]

    async def capture_timeline(_ctx: dict[str, Any], **_kwargs: Any) -> None:
        return None

    material = {
        "HHPN": "IC001",
        "LotCode": "LOT-I",
        "DateCode": "20260413",
        "PkgID": "PKG-IC001-LOT-I-001",
        "reel_diameter": "330.0",
        "reel_thickness": "24.0",
    }
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "material": material,
                    "rack_tasks": [
                        {
                            "sequence_no": 1,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                        }
                    ],
                },
                timeout_seconds=1800,
            )
        ],
    )

    assert operation_calls[0]["task_specs"][0]["request_json"]["material"] == material


@pytest.mark.asyncio
async def test_rack_operation_request_persists_external_wait_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(
        status="WAITING_DEVICE_RESULT",
        current_wait_type="COMMAND_RESULT",
        awaiting_command_id=88,
        context_json={},
    )
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    persist_wait = AsyncMock()

    class RecordingRackOperationService:
        async def request_operation_tasks(self, _db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=1,
                    task_type="ALLOCATE_AND_MOVE_RACK",
                    dispatch_key="rack-operation:rack-operation:trace-runtime:1:ALLOCATE_AND_MOVE_RACK",
                    actions_json={"required": True},
                    rack_code=None,
                )
            ]

    class RecordingSessionRepository:
        async def persist_external_wait(self, *args: Any, **kwargs: Any) -> None:
            await persist_wait(*args, **kwargs)

    async def capture_timeline(_ctx: dict[str, Any], **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository",
        RecordingSessionRepository,
    )

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "rack_tasks": [
                        {
                            "sequence_no": 1,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                        }
                    ]
                },
                timeout_seconds=1800,
            )
        ],
    )

    persist_wait.assert_awaited_once_with(
        db,
        session_id=session.id,
        wait_type="RACK_OPERATION",
        occurred_at=ctx["now"],
        timeout_seconds=1800,
        context_json=session.context_json,
    )
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RACK_OPERATION"
    assert session.awaiting_command_id is None


@pytest.mark.asyncio
async def test_rack_operation_request_preserves_operation_metadata_written_by_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    operation_key = "rack-operation:trace-runtime"
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)

    class RecordingRackOperationService:
        async def request_operation_tasks(self, _db: Any, **kwargs: Any) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    id=901,
                    operation_key=kwargs["operation_key"],
                    sequence_no=1,
                    task_type="MOVE_RACK",
                    dispatch_key=f"rack-operation:{kwargs['operation_key']}:1:MOVE_RACK",
                    actions_json={"required": True},
                    rack_code="RACK-OLD",
                    source_position_code="SINGLE_LAYER_A",
                    target_position_code=None,
                ),
                SimpleNamespace(
                    id=902,
                    operation_key=kwargs["operation_key"],
                    sequence_no=2,
                    task_type="ALLOCATE_AND_MOVE_RACK",
                    dispatch_key=f"rack-operation:{kwargs['operation_key']}:2:ALLOCATE_AND_MOVE_RACK",
                    actions_json={"required": True},
                    rack_code=None,
                ),
            ]

    async def capture_timeline(_ctx: dict[str, Any], **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(rack_operation_service=RecordingRackOperationService()).apply(
        ctx,
        [
            RuntimeIntent.update_context(
                {
                    "rack_operation": {
                        "operation_key": operation_key,
                        "status": "REQUESTED",
                        "pkg_id": "PKG-001",
                    },
                    "waiting_rack_operation_key": operation_key,
                }
            ),
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key=operation_key,
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "work_position_code": "SINGLE_LAYER_A",
                    "new_rack_kind": "SINGLE_LAYER",
                    "move_out_target_position_role": "SMT_EMPTY_RACK_AREA",
                    "rack_tasks": [
                        {
                            "sequence_no": 1,
                            "task_type": "MOVE_RACK",
                            "rack_code": "RACK-OLD",
                            "rack_kind": "SINGLE_LAYER",
                            "source_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_EMPTY_RACK_AREA",
                        },
                        {
                            "sequence_no": 2,
                            "task_type": "ALLOCATE_AND_MOVE_RACK",
                            "rack_kind": "SINGLE_LAYER",
                            "target_position_code": "SINGLE_LAYER_A",
                            "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                        },
                    ],
                },
                timeout_seconds=1800,
            ),
        ],
    )

    rack_operation = session.context_json["rack_operation"]
    assert rack_operation["status"] == "PENDING"
    assert rack_operation["pkg_id"] == "PKG-001"
    assert rack_operation["task_sequences"] == [1, 2]
    assert rack_operation["released_rack_codes"] == ["RACK-OLD"]


@pytest.mark.asyncio
async def test_bin_operation_request_calls_handling_service_and_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    service = RecordingHandlingOperationService()

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier(handling_operation_service=service).apply(
        ctx,
        [
            RuntimeIntent.bin_operation_request(
                operation_type="SORTER_FEED_BIN",
                operation_key="bin-operation:trace-runtime",
                moves=[
                    {
                        "sequence_no": 1,
                        "bin_code": "BIN-001",
                        "source_type": "RACK_SLOT",
                        "source_code": "SINGLE_LAYER_A:01",
                        "target_type": "SORTER_STATION",
                        "target_code": "SORTER-01",
                    }
                ],
                carrier_type="CTU",
                carrier_code="CTU-01",
                timeout_seconds=1800,
            )
        ],
    )

    assert len(service.calls) == 1
    assert service.calls[0]["db"] is db
    assert service.calls[0]["workline_id"] == 1
    assert service.calls[0]["workline_code"] is None
    assert service.calls[0]["material_session_id"] == session.id
    assert service.calls[0]["operation_key"] == "bin-operation:trace-runtime"
    assert service.calls[0]["operation_type"] == "SORTER_FEED_BIN"
    assert service.calls[0]["moves"][0]["bin_code"] == "BIN-001"
    assert service.calls[0]["carrier_type"] == "CTU"
    assert service.calls[0]["carrier_code"] == "CTU-01"
    assert service.calls[0]["trace_id"] == "trace-runtime"
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "HANDLING_OPERATION"
    assert session.awaiting_command_id is None
    assert session.current_wait_timeout_seconds == 1800
    assert session.deadline_at == ctx["now"] + timedelta(seconds=1800)
    assert session.context_json["waiting_handling_operation_key"] == "bin-operation:trace-runtime"
    assert session.context_json["handling_operation"]["operation_key"] == "bin-operation:trace-runtime"
    assert session.context_json["handling_operation"]["operation_type"] == "SORTER_FEED_BIN"
    assert session.context_json["handling_operation"]["status"] == "PENDING"
    assert session.context_json["handling_operation"]["move_sequences"] == [1]
    assert timelines[0]["payload"]["wait_type"] == "HANDLING_OPERATION"
    assert timelines[0]["payload"]["wait_token"] == "bin-operation:trace-runtime"


@pytest.mark.asyncio
async def test_rack_bin_exchange_request_uses_same_handling_wait_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    service = RecordingHandlingOperationService()

    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier(handling_operation_service=service).apply(
        ctx,
        [
            RuntimeIntent.rack_bin_exchange_request(
                operation_type="SINGLE_LAYER_FULL_BOX_EXCHANGE",
                operation_key="rack-bin-exchange:release-001",
                moves=[
                    {
                        "sequence_no": 1,
                        "bin_code": "BIN-FULL",
                        "source_type": "RACK_SLOT",
                        "source_code": "SINGLE_LAYER_A:01",
                        "target_type": "BUFFER",
                        "target_code": "FULL_BIN_BUFFER",
                    },
                    {
                        "sequence_no": 2,
                        "placeholder_key": "EMPTY_BIN_FOR:SINGLE_LAYER_A:01",
                        "source_type": "BUFFER",
                        "source_code": "EMPTY_BIN_BUFFER",
                        "target_type": "RACK_SLOT",
                        "target_code": "SINGLE_LAYER_A:01",
                    },
                ],
                rack_code="RACK-SINGLE-01",
                carrier_type="CTU",
                timeout_seconds=1800,
            )
        ],
    )

    assert service.calls[0]["operation_key"] == "rack-bin-exchange:release-001"
    assert service.calls[0]["operation_type"] == "SINGLE_LAYER_FULL_BOX_EXCHANGE"
    assert service.calls[0]["moves"][1]["placeholder_key"] == "EMPTY_BIN_FOR:SINGLE_LAYER_A:01"
    assert session.current_wait_type == "HANDLING_OPERATION"
    assert session.context_json["handling_operation"]["rack_code"] == "RACK-SINGLE-01"
    assert session.context_json["handling_operation"]["move_sequences"] == [1, 2]


def test_rack_operation_wait_released_rack_codes_include_only_move_out_tasks() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)

    RuntimeIntentEffectApplier()._mark_session_waiting_for_rack_operation(
        ctx,
        operation_key="rack-operation:move-in",
        operation_type="RACK_TRANSPORT",
        tasks=[
            SimpleNamespace(
                sequence_no=1,
                task_type="MOVE_RACK",
                dispatch_key="rack-operation:move-in:1:MOVE_RACK",
                actions_json={"required": True},
                rack_code="RACK-INBOUND-1",
                source_position_code="SOURCE-A",
                target_position_code="WORK-POSITION",
            ),
            SimpleNamespace(
                sequence_no=2,
                task_type="MOVE_RACK",
                dispatch_key="rack-operation:move-in:2:MOVE_RACK",
                actions_json={"required": True},
                rack_code="RACK-INBOUND-2",
                source_position_code="SOURCE-B",
                target_position_code="WORK-POSITION",
            ),
            SimpleNamespace(
                sequence_no=3,
                task_type="MOVE_RACK",
                dispatch_key="rack-operation:move-in:3:MOVE_RACK",
                actions_json={"required": True},
                rack_code="RACK-OLD",
                source_position_code="WORK-POSITION",
                target_position_code=None,
            ),
            SimpleNamespace(
                sequence_no=4,
                task_type="MOVE_RACK",
                dispatch_key="rack-operation:move-in:4:MOVE_RACK",
                actions_json={"required": False},
                rack_code="RACK-OPTIONAL",
                source_position_code="WORK-POSITION",
                target_position_code=None,
            ),
        ],
        timeout_seconds=1800,
    )

    rack_operation = session.context_json["rack_operation"]
    assert rack_operation["released_rack_codes"] == ["RACK-OLD"]


def test_rack_operation_wait_infers_target_position_from_returned_tasks() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)

    RuntimeIntentEffectApplier()._mark_session_waiting_for_rack_operation(
        ctx,
        operation_key="rack-operation:custom-target",
        operation_type="RACK_TRANSPORT",
        tasks=[
            SimpleNamespace(
                sequence_no=1,
                task_type="MOVE_RACK",
                dispatch_key="rack-operation:custom-target:1:MOVE_RACK",
                actions_json={"required": True},
                rack_code="RACK-INBOUND",
                source_position_code="BUFFER-A",
                target_position_code="CUSTOM-WORK-POSITION",
            ),
            SimpleNamespace(
                sequence_no=2,
                task_type="ALLOCATE_AND_MOVE_RACK",
                dispatch_key="rack-operation:custom-target:2:ALLOCATE_AND_MOVE_RACK",
                actions_json={"required": True},
                rack_code=None,
                source_position_code=None,
                target_position_code="CUSTOM-WORK-POSITION",
            ),
        ],
        timeout_seconds=1800,
    )

    rack_operation = session.context_json["rack_operation"]
    assert rack_operation["target_position_code"] == "CUSTOM-WORK-POSITION"
    assert rack_operation["work_position_code"] == "CUSTOM-WORK-POSITION"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent_kwargs", "message"),
    [
        (
            {
                "operation_type": "ANY_PLUGIN_OPERATION",
                "payload": {"trace_id": "trace-runtime"},
            },
            "RACK_OPERATION_REQUEST intent requires payload.rack_tasks",
        ),
    ],
)
async def test_rack_operation_request_rejects_invalid_operation_contract(
    intent_kwargs: dict[str, Any],
    message: str,
) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)

    with pytest.raises(ValueError, match=message):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.rack_operation_request(
                    operation_key="rack-operation:trace-runtime",
                    target_code="WMS_RCS_RACK_OPERATION",
                    timeout_seconds=1800,
                    **intent_kwargs,
                )
            ],
        )

    assert session.context_json == {}
    assert session.status == "RUNNING"
    assert ctx["db"].add.call_count == 0


@pytest.mark.asyncio
async def test_rack_operation_request_requires_trace_id() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None, trace_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    ctx["trace"] = SimpleNamespace(trace_id=None)
    ctx["trace_id"] = None

    with pytest.raises(ValueError, match="RACK_OPERATION_REQUEST intent requires trace_id"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.rack_operation_request(
                    operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                    operation_key="rack-operation:trace-runtime",
                    target_code="WMS_RCS_RACK_OPERATION",
                    payload={
                        "rack_tasks": [
                            {
                                "sequence_no": 2,
                                "task_type": "ALLOCATE_AND_MOVE_RACK",
                                "rack_kind": "SINGLE_LAYER",
                                "target_position_code": "SINGLE_LAYER_A",
                                "target_position_role": "SMT_CLASSIFIER_SINGLE_RACK_WORK",
                            }
                        ],
                    },
                    timeout_seconds=1800,
                )
            ],
        )

    assert session.context_json == {}
    assert session.status == "RUNNING"
    assert ctx["db"].add.call_count == 0


@pytest.mark.asyncio
async def test_device_event_intent_creates_device_event_inbox_without_waiting_current_session() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)

    class RecordingInboxService:
        def __init__(self) -> None:
            self.created: dict[str, Any] = {}

        async def create_device_event_inbox(self, **kwargs: Any) -> object:
            self.created = kwargs
            return SimpleNamespace(id=456)

    recording_inbox_service = RecordingInboxService()
    intent = RuntimeIntent.device_event(
        device_code="SMT-RACK-RELEASE",
        event_type="SINGLE_LAYER_RACK_RELEASED",
        timestamp=1770000000000,
        data={"rack_release_id": "release-001"},
        event_id="smt-release:release-001",
        causation_id="scan:event-001",
        canonical_event_type="SINGLE_LAYER_RACK_RELEASED",
    )

    await RuntimeIntentEffectApplier(inbox_service=recording_inbox_service).apply(ctx, [intent])

    assert intent.kind == RuntimeIntentKind.DEVICE_EVENT
    assert recording_inbox_service.created["db"] is ctx["db"]
    assert recording_inbox_service.created["device_code"] == "SMT-RACK-RELEASE"
    assert recording_inbox_service.created["event_type"] == "SINGLE_LAYER_RACK_RELEASED"
    assert recording_inbox_service.created["timestamp"] == 1770000000000
    assert recording_inbox_service.created["data"] == {"rack_release_id": "release-001"}
    assert recording_inbox_service.created["trace_id"] == "trace-runtime"
    assert recording_inbox_service.created["event_id"] == "smt-release:release-001"
    assert recording_inbox_service.created["causation_id"] == "scan:event-001"
    assert recording_inbox_service.created["canonical_event_type"] == "SINGLE_LAYER_RACK_RELEASED"
    assert recording_inbox_service.created["auto_commit"] is False
    assert session.status == "RUNNING"
    assert session.current_wait_type is None
    assert session.awaiting_command_id is None


@pytest.mark.asyncio
async def test_resource_fact_then_device_event_creates_storage_retry_inbox() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService()

    class RecordingInboxService:
        def __init__(self) -> None:
            self.created: dict[str, Any] = {}

        async def create_device_event_inbox(self, **kwargs: Any) -> object:
            self.created = kwargs
            return SimpleNamespace(id=789)

    recording_inbox_service = RecordingInboxService()
    retry_event_id = "rough-sorter-storage-retry:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION:321"

    await RuntimeIntentEffectApplier(
        resource_projection_service=resource_projection,
        inbox_service=recording_inbox_service,
    ).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="RACK_ARRIVED",
                payload={
                    "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
                    "rack_code": "RACK-001",
                },
                idempotency_key="RACK_ARRIVED:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
            ),
            RuntimeIntent.device_event(
                device_code="RS-CONVEYOR-01",
                event_type="ROUGH_SORTER_STORAGE_RETRY",
                timestamp=1770000000000,
                data={"PkgID": "PKG-ROUGH-001", "idempotency_key": retry_event_id},
                event_id=retry_event_id,
                causation_id="wms-rack-arrived-001",
                canonical_event_type="ROUGH_SORTER_STORAGE_RETRY",
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "RACK_ARRIVED"
    assert recording_inbox_service.created["event_type"] == "ROUGH_SORTER_STORAGE_RETRY"
    assert recording_inbox_service.created["event_id"] == retry_event_id
    assert recording_inbox_service.created["data"]["idempotency_key"] == retry_event_id
    assert recording_inbox_service.created["canonical_event_type"] == "ROUGH_SORTER_STORAGE_RETRY"
    assert recording_inbox_service.created["auto_commit"] is False


@pytest.mark.asyncio
async def test_resource_fact_duplicate_storage_retry_device_event_is_treated_as_idempotent() -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    resource_projection = RecordingResourceProjectionService()

    class DuplicateInboxService:
        def __init__(self) -> None:
            self.calls = 0

        async def create_device_event_inbox(self, **_kwargs: Any) -> object:
            self.calls += 1
            raise DuplicateInboxError(
                "设备事件已存在（幂等键重复）: device_event:retry",
                existing_inbox=SimpleNamespace(id=789),
            )

    duplicate_inbox_service = DuplicateInboxService()
    retry_event_id = "rough-sorter-storage-retry:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION:321"

    await RuntimeIntentEffectApplier(
        resource_projection_service=resource_projection,
        inbox_service=duplicate_inbox_service,
    ).apply(
        ctx,
        [
            RuntimeIntent.resource_fact(
                fact_type="RACK_ARRIVED",
                payload={
                    "operation_key": "external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
                    "rack_code": "RACK-001",
                },
                idempotency_key="RACK_ARRIVED:external:smt_rack_bin:trace-rough-sorter-001:RACK_OPERATION",
            ),
            RuntimeIntent.device_event(
                device_code="RS-CONVEYOR-01",
                event_type="ROUGH_SORTER_STORAGE_RETRY",
                timestamp=1770000000000,
                data={"PkgID": "PKG-ROUGH-001", "idempotency_key": retry_event_id},
                event_id=retry_event_id,
                causation_id="wms-rack-arrived-duplicate",
                canonical_event_type="ROUGH_SORTER_STORAGE_RETRY",
            ),
        ],
    )

    assert resource_projection.calls[0]["fact_type"] == "RACK_ARRIVED"
    assert duplicate_inbox_service.calls == 1


@pytest.mark.parametrize(
    ("intent", "expected_timeout_seconds"),
    [
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 1500},
                destination=Destination.current(),
            ),
            2,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 500},
                destination=Destination.current(),
            ),
            1,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 0},
                destination=Destination.current(),
            ),
            1,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": -100},
                destination=Destination.current(),
            ),
            1,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 300000},
                destination=Destination.current(),
            ),
            300,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={},
                destination=Destination.current(),
            ),
            300,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 300000},
                destination=Destination.current(),
                timeout_seconds=42,
            ),
            42,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 300000},
                destination=Destination.current(),
                timeout_seconds=0,
            ),
            1,
        ),
    ],
)
def test_command_result_timeout_resolution(intent: RuntimeIntent, expected_timeout_seconds: int) -> None:
    assert _resolve_command_result_timeout_seconds(intent) == expected_timeout_seconds


@pytest.mark.asyncio
async def test_command_destination_current_targets_source_device(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-CURRENT",
            task_type="SCAN",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [RuntimeIntent.command(action="SCAN", payload={}, destination=Destination.current(), timeout_seconds=30)],
    )

    assert created_payloads[0]["device_id"] == 1


@pytest.mark.asyncio
async def test_command_destination_next_targets_topology_downstream(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    target = SimpleNamespace(id=2, device_code="CONV01", device_role="CONVEYOR", upstream_device_id=1)
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source], "CONVEYOR": [target]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-NEXT",
            task_type="MOVE_FORWARD",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [RuntimeIntent.command(action="MOVE_FORWARD", payload={}, destination=Destination.next(), timeout_seconds=30)],
    )

    assert created_payloads[0]["device_id"] == 2


@pytest.mark.asyncio
async def test_command_destination_device_outside_topology_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source]}

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    create_command = AsyncMock()
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", create_command)
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MOVE_FORWARD", payload={}, destination=Destination.device(99), timeout_seconds=30
            )
        ],
    )

    create_command.assert_not_awaited()
    assert session.status == "MANUAL_HOLD"
    assert session.failure_code == "DESTINATION_UNREACHABLE"
    assert "No destination matched" in timelines[0]["payload"]["message"]


@pytest.mark.asyncio
async def test_command_destination_ng_route_uses_configured_route_role(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    ng_target = SimpleNamespace(id=3, device_code="NG01", device_role="NG_BUFFER", upstream_device_id=1)
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["workline"].config = {"route_roles": {"NG_ROUTE": "NG_BUFFER"}}
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source], "NG_BUFFER": [ng_target]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-NG",
            task_type="PICK_AND_PUT",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="PICK_AND_PUT", payload={}, destination=Destination.ng_route(), timeout_seconds=30
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 3


@pytest.mark.asyncio
async def test_invalid_combinations_are_rejected_before_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)

    with pytest.raises(ValueError, match="terminal RuntimeIntent must be final intent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.complete({"done": True}),
                RuntimeIntent.update_context({"late": True}),
            ],
        )

    assert session.status == "RUNNING"
    assert session.context_json == {}
    emit_timeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_before_terminal_intent_is_rejected_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    create_command = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", create_command)

    with pytest.raises(ValueError, match="terminal RuntimeIntent cannot follow command-producing RuntimeIntent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.command(action="MOVE_FORWARD", payload={}, destination=Destination.current()),
                RuntimeIntent.complete({"done": True}),
            ],
        )

    assert session.status == "RUNNING"
    assert session.ended_at is None
    assert ctx["db"].add.call_count == 0
    create_command.assert_not_awaited()
    emit_timeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_resource_wait_sets_waiting_external_and_returns_resource_retry_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        status="RUNNING",
        current_wait_type=None,
        awaiting_command_id=None,
        context_json={},
    )
    db = SimpleNamespace(execute=AsyncMock())
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    emit_timeline = AsyncMock()
    persist_external_wait = AsyncMock()
    record_resource_wait = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr(
        "src.app.workline.repositories.session_repository.WorklineSessionRepository.persist_external_wait",
        persist_external_wait,
    )
    monkeypatch.setattr(
        "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_resource_wait",
        record_resource_wait,
    )

    result = await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.resource_wait(
                resource_kind="STATION",
                resource_key="station:TARGET_STATION",
                reason_code="STATION_BUSY",
                message="目标 Station 忙",
                payload={"active_session_id": 456},
            )
        ],
    )

    assert result.disposition == WriteBackDisposition.RESOURCE_RETRY
    assert session.status == "WAITING_EXTERNAL"
    assert session.current_wait_type == "RESOURCE_WAIT"
    assert session.context_json["resource_wait"]["inbox_id"] == 10
    assert session.context_json["resource_wait"]["resource_kind"] == "STATION"
    assert session.context_json["resource_wait"]["resource_key"] == "station:TARGET_STATION"
    assert session.context_json["resource_wait"]["wait_count"] == 1
    persist_external_wait.assert_awaited_once()
    record_resource_wait.assert_awaited_once()
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_resource_wait_must_be_final_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)

    with pytest.raises(ValueError, match="terminal RuntimeIntent must be final intent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.resource_wait(
                    resource_kind="STATION",
                    resource_key="station:TARGET_STATION",
                    reason_code="STATION_BUSY",
                    message="目标 Station 忙",
                ),
                RuntimeIntent.update_context({"late": True}),
            ],
        )

    emit_timeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_resource_wait_cannot_follow_command_producing_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    create_command = AsyncMock()
    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", create_command)

    with pytest.raises(ValueError, match="terminal RuntimeIntent cannot follow command-producing RuntimeIntent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.command(action="MOVE_FORWARD", payload={}, destination=Destination.current()),
                RuntimeIntent.resource_wait(
                    resource_kind="STATION",
                    resource_key="station:TARGET_STATION",
                    reason_code="STATION_BUSY",
                    message="目标 Station 忙",
                ),
            ],
        )

    create_command.assert_not_awaited()
    emit_timeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_request_before_terminal_intent_is_rejected_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)

    with pytest.raises(ValueError, match="terminal RuntimeIntent cannot follow command-producing RuntimeIntent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.external_request(
                    dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
                    target_code="WMS_RCS_FULL_BOX_EXCHANGE",
                    payload={"rack_release_id": "release-001"},
                    timeout_seconds=1800,
                ),
                RuntimeIntent.complete({"done": True}),
            ],
        )

    assert session.status == "RUNNING"
    assert ctx["db"].add.call_count == 0
    emit_timeline.assert_not_awaited()


def test_result_requires_outbox_dispatch_for_external_request() -> None:
    result = OrchestratorResult(
        success=True,
        intents=[
            RuntimeIntent.external_request(
                dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
                target_code="WMS_RCS_FULL_BOX_EXCHANGE",
                payload={"rack_release_id": "release-001"},
                timeout_seconds=1800,
            )
        ],
    )

    assert _result_requires_outbox_dispatch(result) is True


def test_result_requires_outbox_dispatch_for_rack_operation_request() -> None:
    result = OrchestratorResult(
        success=True,
        intents=[
            RuntimeIntent.rack_operation_request(
                operation_type="REPLACE_CLASSIFIER_WORK_RACK",
                operation_key="rack-operation:trace-runtime",
                target_code="WMS_RCS_RACK_OPERATION",
                payload={
                    "work_position_code": "SINGLE_LAYER_A",
                    "new_rack_kind": "SINGLE_LAYER",
                    "move_out_target_position_role": "SMT_EMPTY_RACK_AREA",
                },
                timeout_seconds=1800,
            )
        ],
    )

    assert _result_requires_outbox_dispatch(result) is True


def test_wait_session_status_maps_rack_operation_to_external_wait() -> None:
    assert workline_effects._wait_session_status("RACK_OPERATION") == "WAITING_EXTERNAL"


@pytest.mark.asyncio
async def test_apply_orchestrator_effects_dispatches_runtime_intents(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    class CapturingApplier:
        async def apply(self, ctx: dict[str, Any], intents: list[RuntimeIntent]) -> None:
            called["session"] = ctx["session"]
            called["intents"] = intents

    monkeypatch.setattr("src.workline_runtime.runtime_intent_effects.RuntimeIntentEffectApplier", CapturingApplier)

    session = _session(status="RUNNING", current_wait_type=None, awaiting_command_id=None)
    intents = [RuntimeIntent.update_context({"pkg_id": "PKG-001"})]
    from src.app.workline.services.write_back_service import orchestrator_write_back_service

    await orchestrator_write_back_service.write_back(
        SimpleNamespace(add=MagicMock()),
        session=session,
        workline=SimpleNamespace(id=1, plugin_key="demo_plugin"),
        inbox=SimpleNamespace(id=10, trace_id="trace-runtime"),
        devices_by_role={},
        source_device=None,
        orch_result=OrchestratorResult(success=True, intents=intents),
    )

    assert called == {"session": session, "intents": intents}
