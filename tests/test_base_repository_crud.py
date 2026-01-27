"""
测试 BaseRepository 的 CRUD 操作

验证基础的增删改查功能：
- create: 创建记录
- update: 更新记录
- delete: 删除记录
- get_by_id: 根据 ID 获取
- get_by_field: 根据字段获取
- get_all: 获取所有记录
- get_list: 分页获取列表
- exists: 检查是否存在
- count: 统计数量
- bulk_create: 批量创建
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel

from src.core.query_models import FilterCondition, FilterGroup, FilterOperator, SortField
from src.database.base_repository import BaseRepository


class CrudTestModel(SQLModel, table=True):
    """CRUD 测试用模型"""

    __tablename__ = "crud_test_model"

    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True)
    name: str
    value: int = 0
    is_active: bool = True


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
        assert instance.is_active is True  # 默认值

    @pytest.mark.asyncio
    async def test_get_by_id(self, db_session: AsyncSession):
        """测试根据 ID 获取记录"""
        # 先创建一条记录
        instance = await self.repo.create(
            db_session, {"code": "TEST001", "name": "Test Item"}
        )
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
    async def test_get_all(self, db_session: AsyncSession):
        """测试获取所有记录"""
        # 创建多条记录
        await self.repo.create(db_session, {"code": "TEST001", "name": "Item 1"})
        await self.repo.create(db_session, {"code": "TEST002", "name": "Item 2"})
        await self.repo.create(db_session, {"code": "TEST003", "name": "Item 3"})
        await db_session.commit()

        # 获取所有记录
        items = await self.repo.get_all(db_session)

        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_get_all_with_filter(self, db_session: AsyncSession):
        """测试带过滤条件获取记录"""
        # 创建多条记录
        await self.repo.create(
            db_session, {"code": "TEST001", "name": "Item 1", "is_active": True}
        )
        await self.repo.create(
            db_session, {"code": "TEST002", "name": "Item 2", "is_active": False}
        )
        await self.repo.create(
            db_session, {"code": "TEST003", "name": "Item 3", "is_active": True}
        )
        await db_session.commit()

        # 只获取 is_active=True 的记录
        items = await self.repo.get_all(
            db_session, where_clauses=[CrudTestModel.is_active == True]  # noqa: E712
        )

        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_get_all_with_pagination(self, db_session: AsyncSession):
        """测试分页获取记录"""
        # 创建多条记录
        for i in range(10):
            await self.repo.create(
                db_session, {"code": f"TEST{i:03d}", "name": f"Item {i}"}
            )
        await db_session.commit()

        # 分页获取
        items = await self.repo.get_all(db_session, limit=5, offset=3)

        assert len(items) == 5

    @pytest.mark.asyncio
    async def test_get_list(self, db_session: AsyncSession):
        """测试获取列表（带总数）"""
        # 创建多条记录
        for i in range(15):
            await self.repo.create(
                db_session, {"code": f"TEST{i:03d}", "name": f"Item {i}"}
            )
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
                    "is_active": i % 2 == 0,
                },
            )
        await db_session.commit()

        # 使用过滤条件
        filters = FilterGroup(
            conditions=[
                FilterCondition(field="is_active", op=FilterOperator.EQ, value=True)
            ]
        )

        total, items = await self.repo.get_list(db_session, filters=filters)

        assert total == 5  # 只有偶数索引的记录
        assert all(item.is_active for item in items)

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
        instance = await self.repo.create(
            db_session, {"code": "TEST001", "name": "Old Name", "value": 100}
        )
        await db_session.commit()

        # 更新记录
        updated = await self.repo.update(
            db_session, instance.id, {"name": "New Name", "value": 200}  # type: ignore[arg-type]
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
        instance = await self.repo.create(
            db_session, {"code": "TEST001", "name": "Test Item"}
        )
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
            await self.repo.create(
                db_session, {"code": f"TEST{i:03d}", "name": f"Item {i}"}
            )
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
                    "is_active": i % 2 == 0,
                },
            )
        await db_session.commit()

        # 统计 is_active=True 的数量
        count = await self.repo.count(
            db_session, where_clauses=[CrudTestModel.is_active == True]  # noqa: E712
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
