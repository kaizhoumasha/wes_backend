"""MaterialUnit 料盘根实体模型测试。

验证：
- 模型可正常 CRUD
- status 枚举正确（5 态）
- 默认 status=IN_TRANSIT
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select

from src.app.workline.models import MaterialUnit, MaterialUnitStatus
from src.database.sqlite_schema import configure_sqlite_schemas


@pytest_asyncio.fixture(scope="function")
async def material_unit_session():
    """独立内存 DB，只建 material_units 表（避免完整 schema 外键依赖）。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
    )
    configure_sqlite_schemas(engine.sync_engine)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[MaterialUnit.__table__])
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all, tables=[MaterialUnit.__table__])
    await engine.dispose()


@pytest.mark.asyncio
async def test_material_unit_crud(material_unit_session):
    """MaterialUnit 可正常 CRUD。"""
    unit = MaterialUnit(
        pkg_code="PKG-TEST-001",
        material_identity_key="MAT:HHPN1:V1:DC1:LOT1",
        six_in_one={"PkgID": "PKG-TEST-001", "HHPN": "HHPN1"},
        status=MaterialUnitStatus.IN_TRANSIT,
    )
    material_unit_session.add(unit)
    await material_unit_session.commit()
    await material_unit_session.refresh(unit)

    assert unit.id is not None
    assert unit.pkg_code == "PKG-TEST-001"
    assert unit.status == MaterialUnitStatus.IN_TRANSIT
    assert unit.current_location is None
    assert unit.current_session_id is None

    # 更新
    unit.status = MaterialUnitStatus.STORED
    unit.current_location = "BIN001:3"
    await material_unit_session.commit()
    await material_unit_session.refresh(unit)
    assert unit.status == MaterialUnitStatus.STORED
    assert unit.current_location == "BIN001:3"

    # 查询
    result = await material_unit_session.execute(select(MaterialUnit).where(MaterialUnit.pkg_code == "PKG-TEST-001"))
    found = result.scalar_one()
    assert found.id == unit.id
    assert found.material_identity_key == "MAT:HHPN1:V1:DC1:LOT1"

    # 删除
    await material_unit_session.delete(found)
    await material_unit_session.commit()
    result = await material_unit_session.execute(select(MaterialUnit).where(MaterialUnit.pkg_code == "PKG-TEST-001"))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_material_unit_default_status(material_unit_session):
    """默认 status=IN_TRANSIT。"""
    unit = MaterialUnit(
        pkg_code="PKG-TEST-002",
        material_identity_key="MAT:HHPN2:V2:DC2:LOT2",
    )
    material_unit_session.add(unit)
    await material_unit_session.commit()
    await material_unit_session.refresh(unit)
    assert unit.status == MaterialUnitStatus.IN_TRANSIT


@pytest.mark.asyncio
async def test_material_unit_all_statuses(material_unit_session):
    """所有 5 个 status 枚举值都可写入。"""
    for status in MaterialUnitStatus:
        unit = MaterialUnit(
            pkg_code=f"PKG-{status.value}",
            material_identity_key=f"MAT:{status.value}",
            status=status,
        )
        material_unit_session.add(unit)
    await material_unit_session.commit()

    result = await material_unit_session.execute(select(MaterialUnit))
    units = result.scalars().all()
    assert len(units) == 5
    statuses = {u.status for u in units}
    assert statuses == {
        MaterialUnitStatus.IN_TRANSIT,
        MaterialUnitStatus.STORED,
        MaterialUnitStatus.COMPLETED,
        MaterialUnitStatus.NG,
        MaterialUnitStatus.RECONCILING,
    }
