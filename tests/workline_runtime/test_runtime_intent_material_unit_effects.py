from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select

from src.app.workline.models.material_unit import MaterialUnit, MaterialUnitStatus
from src.app.workline.models.session import RunMode, SessionStatus, WorklineSession
from src.app.workline.models.workline import LineType, WorkLine
from src.database.sqlite_schema import configure_sqlite_schemas
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import RuntimeIntent
from src.workline_runtime.runtime_intent_effects import (
    RuntimeIntentEffectApplier,
)
from tests.workline_runtime.support.runtime_intent_effects import (
    _MATERIAL_UNIT_STATUS_TRANSITION_WARNING,
    MaterialUnitDb,
    _ctx,
    _session,
)


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
