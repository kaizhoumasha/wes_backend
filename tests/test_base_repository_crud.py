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

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError
from sqlmodel import Field, SQLModel

from src.core.exceptions import OptimisticLockException
from src.core.mixins import SoftDeleteMixin
from src.core.query_models import FilterCondition, FilterGroup, FilterOperator, SortField
from src.database.base_repository import BaseRepository
from src.database.relation_metadata import RelationType


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
    async def test_create_stale_data_raises_optimistic_lock_without_invalid_resource_id(self):
        """测试 create 的 StaleDataError 分支不会错误引用内置 id。"""
        db = AsyncMock(spec=AsyncSession)
        db.add = Mock()
        db.flush = AsyncMock(side_effect=StaleDataError("stale on create"))
        db.rollback = AsyncMock()

        with pytest.raises(OptimisticLockException) as exc_info:
            await self.repo.create(db, {"code": "TEST001", "name": "Test Item", "version": 1})

        db.rollback.assert_awaited_once()
        assert "built-in function id" not in str(exc_info.value)
        detail = getattr(exc_info.value, "detail", None) or {}
        assert detail.get("resource_id") is None

    @pytest.mark.asyncio
    async def test_create_with_relation_payload_skips_redundant_direct_refresh(self, monkeypatch: pytest.MonkeyPatch):
        """测试带关联数据创建时不再做冗余的直接 refresh。"""
        db = AsyncMock(spec=AsyncSession)
        db.add = Mock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        self.repo._handle_relations = AsyncMock()  # type: ignore[method-assign]
        self.repo._refresh_with_relations = AsyncMock()  # type: ignore[method-assign]

        from src.database.relation_metadata import RelationMetadata

        monkeypatch.setattr(
            RelationMetadata,
            "get_relation_info",
            lambda model: {"children": {"relation_type": RelationType.ONETOMANY}},
        )

        instance = await self.repo.create(
            db,
            {"code": "TEST001", "name": "Test Item", "children": [{"name": "Child Item"}]},
        )

        assert instance is not None
        assert db.flush.await_count == 2
        db.refresh.assert_not_awaited()
        self.repo._handle_relations.assert_awaited_once()
        self.repo._refresh_with_relations.assert_awaited_once()

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
    async def test_get_by_field_with_soft_delete(self, db_session: AsyncSession):
        """测试 get_by_field 的软删除过滤功能"""
        repo = BaseRepository[SoftDeleteCrudModel](SoftDeleteCrudModel)

        # 创建两条记录，一条软删除
        await repo.create(db_session, {"code": "ACTIVE", "name": "Active Item"})
        deleted = await repo.create(db_session, {"code": "DELETED", "name": "Deleted Item"})
        deleted.soft_delete()
        await db_session.commit()

        # 默认不包含已删除记录
        found_active = await repo.get_by_field(db_session, "code", "ACTIVE")
        found_deleted = await repo.get_by_field(db_session, "code", "DELETED")
        assert found_active is not None
        assert found_active.name == "Active Item"
        assert found_deleted is None  # 软删除记录被过滤

        # 使用 include_deleted=True 可以查询到已删除记录
        found_deleted_with_flag = await repo.get_by_field(db_session, "code", "DELETED", include_deleted=True)
        assert found_deleted_with_flag is not None
        assert found_deleted_with_flag.name == "Deleted Item"

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
    async def test_get_list_with_schema_respects_include_deleted(self, db_session: AsyncSession):
        """测试 get_list 的 schema 分支正确处理软删除过滤。"""
        repo = BaseRepository[SoftDeleteCrudModel](SoftDeleteCrudModel)
        first = await repo.create(db_session, {"code": "TEST001", "name": "Item 1"})
        second = await repo.create(db_session, {"code": "TEST002", "name": "Item 2"})
        first.soft_delete()
        await db_session.commit()

        visible_total, visible_items = await repo.get_list(
            db_session,
            limit=10,
            offset=0,
            schema=CrudTestResponse,
        )
        all_total, all_items = await repo.get_list(
            db_session,
            limit=10,
            offset=0,
            schema=CrudTestResponse,
            include_deleted=True,
        )

        assert visible_total == 1
        assert [item.id for item in visible_items] == [second.id]
        assert all_total == 2
        assert {item.id for item in all_items} == {first.id, second.id}

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

        filters = FilterGroup(conditions=[FilterCondition(field="name", op=FilterOperator.ILIKE, value="admin%")])

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

        filters = FilterGroup(conditions=[FilterCondition(field="name", op=FilterOperator.ILIKE, value="%admin")])

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

        filters = FilterGroup(conditions=[FilterCondition(field="name", op=FilterOperator.ILIKE, value=r"%100\%%")])

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
    async def test_soft_delete_repository_method(self, db_session: AsyncSession):
        """测试仓储 soft_delete() 会标记删除并默认从查询结果中过滤。"""
        repo = BaseRepository[SoftDeleteCrudModel](SoftDeleteCrudModel)
        instance = await repo.create(db_session, {"code": "SOFT001", "name": "Soft Item"})
        await db_session.commit()

        deleted = await repo.soft_delete(db_session, instance.id, deleted_by=123)  # type: ignore[arg-type]

        assert deleted is not None
        assert deleted.is_deleted is True
        assert deleted.deleted_by == 123
        assert await repo.get_by_id(db_session, instance.id) is None  # type: ignore[arg-type]
        assert await repo.get_by_id(db_session, instance.id, include_deleted=True) is not None  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_restore_repository_method(self, db_session: AsyncSession):
        """测试仓储 restore() 会恢复已软删除记录。"""
        repo = BaseRepository[SoftDeleteCrudModel](SoftDeleteCrudModel)
        instance = await repo.create(db_session, {"code": "REST001", "name": "Restore Item"})
        await db_session.commit()
        await repo.soft_delete(db_session, instance.id, deleted_by=456)  # type: ignore[arg-type]
        await db_session.commit()

        restored = await repo.restore(db_session, instance.id)  # type: ignore[arg-type]

        assert restored is not None
        assert restored.is_deleted is False
        assert restored.deleted_by is None
        assert await repo.get_by_id(db_session, instance.id) is not None  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_get_deleted_returns_only_deleted_items(self, db_session: AsyncSession):
        """测试仓储 get_deleted() 只返回已删除记录。"""
        repo = BaseRepository[SoftDeleteCrudModel](SoftDeleteCrudModel)
        first = await repo.create(db_session, {"code": "DEL001", "name": "Deleted 1"})
        second = await repo.create(db_session, {"code": "DEL002", "name": "Deleted 2"})
        _ = await repo.create(db_session, {"code": "LIVE001", "name": "Live"})
        await db_session.commit()

        await repo.soft_delete(db_session, first.id)  # type: ignore[arg-type]
        await repo.soft_delete(db_session, second.id)  # type: ignore[arg-type]
        await db_session.commit()

        total, items = await repo.get_deleted(db_session, limit=10, offset=0)

        assert total == 2
        assert len(items) == 2
        assert {item.code for item in items} == {"DEL001", "DEL002"}
        assert all(item.is_deleted for item in items)

    @pytest.mark.asyncio
    async def test_permanent_delete_removes_soft_deleted_record(self, db_session: AsyncSession):
        """测试仓储 permanent_delete() 会物理删除已软删除记录。"""
        repo = BaseRepository[SoftDeleteCrudModel](SoftDeleteCrudModel)
        instance = await repo.create(db_session, {"code": "PERM001", "name": "Permanent Item"})
        await db_session.commit()
        await repo.soft_delete(db_session, instance.id)  # type: ignore[arg-type]
        await db_session.commit()

        success = await repo.permanent_delete(db_session, instance.id)  # type: ignore[arg-type]

        assert success is True
        assert await repo.get_by_id(db_session, instance.id, include_deleted=True) is None  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_delete_related_objects_only_handles_one_to_many(self, monkeypatch: pytest.MonkeyPatch):
        """测试 _delete_related_objects() 只清理一对多关系中的有效关联 ID。"""

        class FakeParentModel:
            id = "id"
            children = object()
            owner = object()

        class FakeChild:
            def __init__(self, relation_id: int | None):
                self.id = relation_id

        class FakeParentInstance:
            def __init__(self):
                self.children = [FakeChild(1), FakeChild(None), FakeChild(2)]
                self.owner = object()

        repo: BaseRepository[Any] = BaseRepository(FakeParentModel)  # type: ignore[type-arg,arg-type]
        db = AsyncMock(spec=AsyncSession)
        repo._delete_relation_objects = AsyncMock()  # type: ignore[method-assign]

        from src.database.relation_metadata import RelationMetadata

        monkeypatch.setattr(RelationMetadata, "has_relations", lambda model: True)
        monkeypatch.setattr(
            RelationMetadata,
            "get_relation_info",
            lambda model: {
                "children": {"relation_type": RelationType.ONETOMANY},
                "owner": {"relation_type": RelationType.MANYTOONE},
            },
        )

        await repo._delete_related_objects(db, FakeParentInstance())

        repo._delete_relation_objects.assert_awaited_once_with(db, FakeParentModel.children, {1, 2})

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
