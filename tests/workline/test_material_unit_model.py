"""MaterialUnit 料盘根实体模型测试。

验证：
- 模型可正常 CRUD
- status 枚举正确（5 态）
- 默认 status=IN_TRANSIT
"""

import pytest
import pytest_asyncio
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select

from src.app.workline.models import MaterialUnit, MaterialUnitStatus
from src.app.workline.models.session import WorklineSession
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


def test_workline_session_current_material_unit_id_uses_sql_compatible_bigint() -> None:
    """current_material_unit_id 与迁移保持 BigInteger 兼容，并保留 SQLite 内存库兼容性。"""
    column = WorklineSession.__table__.c.current_material_unit_id

    assert column.type.compile(dialect=postgresql.dialect()) == "BIGINT"
    assert column.type.compile(dialect=sqlite.dialect()) == "INTEGER"


def test_material_unit_current_session_id_uses_sql_compatible_bigint() -> None:
    """current_session_id 与 WorklineSession 主键宽度一致，并兼容 SQLite。"""
    column = MaterialUnit.__table__.c.current_session_id

    assert column.type.compile(dialect=postgresql.dialect()) == "BIGINT"
    assert column.type.compile(dialect=sqlite.dialect()) == "INTEGER"


def test_material_unit_pkg_code_has_unique_index() -> None:
    """pkg_code 是单盘物理唯一业务键，模型层必须声明唯一索引/约束。"""
    pkg_code_indexes = [
        index for index in MaterialUnit.__table__.indexes if "pkg_code" in [column.name for column in index.columns]
    ]

    assert len(pkg_code_indexes) == 1
    assert pkg_code_indexes[0].name == "ix_material_units_pkg_code"
    assert [column.name for column in pkg_code_indexes[0].columns] == ["pkg_code"]
    assert pkg_code_indexes[0].unique is True


def test_material_unit_declares_only_expected_indexes() -> None:
    """索引声明集中在 __table_args__，避免 Field(index=True) 生成重复索引。"""
    expected_indexes = {
        ("ix_material_units_pkg_code", ("pkg_code",), True),
        ("ix_material_units_status", ("status",), False),
        ("ix_material_units_current_session_id", ("current_session_id",), False),
    }
    actual_indexes = {
        (index.name, tuple(column.name for column in index.columns), bool(index.unique))
        for index in MaterialUnit.__table__.indexes
        if tuple(column.name for column in index.columns) != ("id",)
    }

    assert actual_indexes == expected_indexes


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
