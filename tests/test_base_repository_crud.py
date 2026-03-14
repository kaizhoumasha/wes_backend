"""
测试 BaseRepository 的 CRUD 操作

验证基础的增删改查功能：
- create: 创建记录
- update: 更新记录
- delete: 删除记录
- get_by_id: 根据 ID 获取
- get_by_field: 根据字段获取
- get_list: 获取记录列表
- get_list: 分页获取列表
- exists: 检查是否存在
- count: 统计数量
- bulk_create: 批量创建
"""

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from src.core.mixins import SoftDeleteMixin
from src.core.query_models import FilterCondition, FilterGroup, FilterOperator, SortField
from src.database.base_repository import BaseRepository


class CrudTestModel(SQLModel, table=True):
    """CRUD 测试用模型"""

    __tablename__ = "crud_test_model"

    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True)
    name: str
    value: int = 0
    is_deleted: bool = False


class CrudTestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    value: int
    is_deleted: bool


class SoftDeleteCrudModel(SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "soft_delete_crud_test_model"

    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True)
    name: str
    value: int = 0


class TestCrudOperations:
    """测试 CRUD 操作"""

    def setup_method(self):
        """设置测试环境"""
        self.repo = BaseRepository[CrudTestModel](CrudTestModel)

    @pytest.mark.asyncio
    async def test_create(self, db_session: AsyncSession):
        """测试创建记录"""
        data = {"code": "TEST001", "name": "Test Item", "value": 100}
        instance = await self.repo.create(db_session, data)

        assert instance.id is not None
        assert instance.code == "TEST001"
        assert instance.name == "Test Item"
        assert instance.value == 100
        assert instance.is_deleted is False  # 默认值

    @pytest.mark.asyncio
    async def test_get_by_id(self, db_session: AsyncSession):
        """测试根据 ID 获取记录"""
        # 先创建一条记录
        instance = await self.repo.create(db_session, {"code": "TEST001", "name": "Test Item"})
        await db_session.commit()

        # 获取记录
        found = await self.repo.get_by_id(db_session, instance.id)  # type: ignore[arg-type]

        assert found is not None
        assert found.id == instance.id
        assert found.code == "TEST001"

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, db_session: AsyncSession):
        """测试获取不存在的记录"""
        found = await self.repo.get_by_id(db_session, 99999)
        assert found is None

    @pytest.mark.asyncio
    async def test_get_by_id_with_schema_respects_include_deleted(self, db_session: AsyncSession):
        """测试 schema 查询分支正确处理软删除过滤"""
        repo = BaseRepository[SoftDeleteCrudModel](SoftDeleteCrudModel)
        instance = await repo.create(db_session, {"code": "TEST001", "name": "Test Item"})
        instance.soft_delete()
        await db_session.commit()

        found = await repo.get_by_id(db_session, instance.id, schema=CrudTestResponse)  # type: ignore[arg-type]
        deleted = await repo.get_by_id(
            db_session,
            instance.id,
            schema=CrudTestResponse,
            include_deleted=True,
        )  # type: ignore[arg-type]

        assert found is None
        assert deleted is not None
        assert deleted.id == instance.id
        assert deleted.is_deleted is True

    @pytest.mark.asyncio
    async def test_get_by_field(self, db_session: AsyncSession):
        """测试根据字段获取记录"""
        # 先创建一条记录
        await self.repo.create(db_session, {"code": "TEST001", "name": "Test Item"})
        await db_session.commit()

        # 根据 code 字段获取
        found = await self.repo.get_by_field(db_session, "code", "TEST001")

        assert found is not None
        assert found.code == "TEST001"

    @pytest.mark.asyncio
    async def test_get_list_basic(self, db_session: AsyncSession):
        """测试获取记录列表（基本功能）"""
        # 创建多条记录
        await self.repo.create(db_session, {"code": "TEST001", "name": "Item 1"})
        await self.repo.create(db_session, {"code": "TEST002", "name": "Item 2"})
        await self.repo.create(db_session, {"code": "TEST003", "name": "Item 3"})
        await db_session.commit()

        # 获取所有记录
        total, items = await self.repo.get_list(db_session, limit=100)

        assert total == 3
        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_get_list_with_filter_raw(self, db_session: AsyncSession):
        """测试带原始过滤条件获取列表"""
        # 创建多条记录
        await self.repo.create(db_session, {"code": "TEST001", "name": "Item 1", "is_deleted": False})
        await self.repo.create(db_session, {"code": "TEST002", "name": "Item 2", "is_deleted": True})
        await self.repo.create(db_session, {"code": "TEST003", "name": "Item 3", "is_deleted": False})
        await db_session.commit()

        # 只获取未删除的记录
        total, items = await self.repo.get_list(
            db_session,
            limit=100,
            where_clauses_raw=[CrudTestModel.is_deleted == False],  # noqa: E712
        )

        assert total == 2
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_get_list_with_pagination(self, db_session: AsyncSession):
        """测试分页获取记录"""
        # 创建多条记录
        for i in range(10):
            await self.repo.create(db_session, {"code": f"TEST{i:03d}", "name": f"Item {i}"})
        await db_session.commit()

        # 分页获取
        total, items = await self.repo.get_list(db_session, limit=5, offset=3)

        assert total == 10
        assert len(items) == 5

    @pytest.mark.asyncio
    async def test_get_list(self, db_session: AsyncSession):
        """测试获取列表（带总数）"""
        # 创建多条记录
        for i in range(15):
            await self.repo.create(db_session, {"code": f"TEST{i:03d}", "name": f"Item {i}"})
        await db_session.commit()

        # 获取列表
        total, items = await self.repo.get_list(db_session, limit=10, offset=0)

        assert total == 15
        assert len(items) == 10

    @pytest.mark.asyncio
    async def test_get_list_with_filters(self, db_session: AsyncSession):
        """测试带过滤条件获取列表"""
        # 创建多条记录
        for i in range(10):
            await self.repo.create(
                db_session,
                {
                    "code": f"TEST{i:03d}",
                    "name": f"Item {i}",
                    "is_deleted": i % 2 != 0,
                },
            )
        await db_session.commit()

        # 使用过滤条件
        filters = FilterGroup(conditions=[FilterCondition(field="is_deleted", op=FilterOperator.EQ, value=False)])

        total, items = await self.repo.get_list(db_session, filters=filters)

        assert total == 5  # 只有未删除的记录
        assert all(not item.is_deleted for item in items)

    @pytest.mark.asyncio
    async def test_get_list_with_ilike_prefix_pattern(self, db_session: AsyncSession):
        """测试 ILIKE 前缀模式按完整 pattern 生效"""
        await self.repo.create(db_session, {"code": "TEST001", "name": "admin"})
        await self.repo.create(db_session, {"code": "TEST002", "name": "admin-user"})
        await self.repo.create(db_session, {"code": "TEST003", "name": "super-admin"})
        await db_session.commit()

        filters = FilterGroup(
            conditions=[FilterCondition(field="name", op=FilterOperator.ILIKE, value="admin%")]
        )

        total, items = await self.repo.get_list(db_session, filters=filters)

        assert total == 2
        assert {item.name for item in items} == {"admin", "admin-user"}

    @pytest.mark.asyncio
    async def test_get_list_with_ilike_suffix_pattern(self, db_session: AsyncSession):
        """测试 ILIKE 后缀模式按完整 pattern 生效"""
        await self.repo.create(db_session, {"code": "TEST001", "name": "admin"})
        await self.repo.create(db_session, {"code": "TEST002", "name": "super-admin"})
        await self.repo.create(db_session, {"code": "TEST003", "name": "admin-user"})
        await db_session.commit()

        filters = FilterGroup(
            conditions=[FilterCondition(field="name", op=FilterOperator.ILIKE, value="%admin")]
        )

        total, items = await self.repo.get_list(db_session, filters=filters)

        assert total == 2
        assert {item.name for item in items} == {"admin", "super-admin"}

    @pytest.mark.asyncio
    async def test_get_list_with_ilike_escaped_literal_percent(self, db_session: AsyncSession):
        """测试 ILIKE 使用反斜杠转义字面量 %"""
        await self.repo.create(db_session, {"code": "TEST001", "name": "100% match"})
        await self.repo.create(db_session, {"code": "TEST002", "name": "100 percent"})
        await self.repo.create(db_session, {"code": "TEST003", "name": "foo100%bar"})
        await db_session.commit()

        filters = FilterGroup(
            conditions=[FilterCondition(field="name", op=FilterOperator.ILIKE, value=r"%100\%%")]
        )

        total, items = await self.repo.get_list(db_session, filters=filters)

        assert total == 2
        assert {item.name for item in items} == {"100% match", "foo100%bar"}

    @pytest.mark.asyncio
    async def test_get_list_with_sort(self, db_session: AsyncSession):
        """测试带排序获取列表"""
        # 创建多条记录
        await self.repo.create(db_session, {"code": "C", "name": "Item C", "value": 3})
        await self.repo.create(db_session, {"code": "A", "name": "Item A", "value": 1})
        await self.repo.create(db_session, {"code": "B", "name": "Item B", "value": 2})
        await db_session.commit()

        # 按 value 升序排序
        sort = [SortField(field="value", order="asc")]

        total, items = await self.repo.get_list(db_session, sort=sort)

        assert total == 3
        assert items[0].value == 1
        assert items[1].value == 2
        assert items[2].value == 3

    @pytest.mark.asyncio
    async def test_update(self, db_session: AsyncSession):
        """测试更新记录"""
        # 先创建一条记录
        instance = await self.repo.create(db_session, {"code": "TEST001", "name": "Old Name", "value": 100})
        await db_session.commit()

        # 更新记录
        updated = await self.repo.update(
            db_session,
            instance.id,
            {"name": "New Name", "value": 200},  # type: ignore[arg-type]
        )

        assert updated.name == "New Name"
        assert updated.value == 200
        assert updated.code == "TEST001"  # 未修改的字段保持不变

    @pytest.mark.asyncio
    async def test_update_not_found(self, db_session: AsyncSession):
        """测试更新不存在的记录"""
        with pytest.raises(ValueError, match="不存在"):
            await self.repo.update(db_session, 99999, {"name": "New Name"})

    @pytest.mark.asyncio
    async def test_delete(self, db_session: AsyncSession):
        """测试删除记录"""
        # 先创建一条记录
        instance = await self.repo.create(db_session, {"code": "TEST001", "name": "Test Item"})
        await db_session.commit()

        # 删除记录
        result = await self.repo.delete(db_session, instance.id)  # type: ignore[arg-type]

        assert result is True

        # 验证记录已删除
        found = await self.repo.get_by_id(db_session, instance.id)  # type: ignore[arg-type]
        assert found is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self, db_session: AsyncSession):
        """测试删除不存在的记录"""
        result = await self.repo.delete(db_session, 99999)
        assert result is False

    @pytest.mark.asyncio
    async def test_exists(self, db_session: AsyncSession):
        """测试检查记录是否存在"""
        # 先创建一条记录
        await self.repo.create(db_session, {"code": "TEST001", "name": "Test Item"})
        await db_session.commit()

        # 检查存在
        exists = await self.repo.exists(db_session, code="TEST001")
        assert exists is True

        # 检查不存在
        not_exists = await self.repo.exists(db_session, code="NOTEXIST")
        assert not_exists is False

    @pytest.mark.asyncio
    async def test_count(self, db_session: AsyncSession):
        """测试统计记录数量"""
        # 创建多条记录
        for i in range(5):
            await self.repo.create(db_session, {"code": f"TEST{i:03d}", "name": f"Item {i}"})
        await db_session.commit()

        # 统计总数
        total = await self.repo.count(db_session)
        assert total == 5

    @pytest.mark.asyncio
    async def test_count_with_filter(self, db_session: AsyncSession):
        """测试带过滤条件统计数量"""
        # 创建多条记录
        for i in range(10):
            await self.repo.create(
                db_session,
                {
                    "code": f"TEST{i:03d}",
                    "name": f"Item {i}",
                    "is_deleted": i % 2 != 0,
                },
            )
        await db_session.commit()

        # 统计未删除记录的数量
        count = await self.repo.count(
            db_session,
            where_clauses=[CrudTestModel.is_deleted == False],  # noqa: E712
        )
        assert count == 5

    @pytest.mark.asyncio
    async def test_bulk_create(self, db_session: AsyncSession):
        """测试批量创建记录"""
        items = [
            {"code": "TEST001", "name": "Item 1"},
            {"code": "TEST002", "name": "Item 2"},
            {"code": "TEST003", "name": "Item 3"},
        ]

        instances = await self.repo.bulk_create(db_session, items)

        assert len(instances) == 3
        assert all(instance.id is not None for instance in instances)
        assert instances[0].code == "TEST001"
        assert instances[1].code == "TEST002"
        assert instances[2].code == "TEST003"
