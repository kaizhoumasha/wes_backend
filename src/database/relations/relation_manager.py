"""
关系管理器

负责处理模型之间的关联关系，包括加载、创建、更新和删除关联对象。
遵循单一职责原则，将关系管理逻辑从 BaseRepository 中分离出来。

此类现在作为 RelationLoader 和 RelationCRUD 的组合门面。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.relations.relation_crud import RelationCRUD
from src.database.relations.relation_loader import RelationLoader


class RelationManager:
    """
    关系管理器（门面模式）

    组合 RelationLoader 和 RelationCRUD，提供统一的关系管理接口。
    """

    def __init__(self, model: type[Any], pk_column: str = "id"):
        self.model = model
        self._pk_column = pk_column
        self.loader = RelationLoader(model, pk_column)
        self.crud = RelationCRUD(model)

    def add_relation_load(self, statement: Any, relation_name: str, relation_info: Any) -> Any:
        return self.loader.add_relation_load(statement, relation_name, relation_info)

    def add_all_relation_loads(self, statement: Any) -> Any:
        return self.loader.add_all_relation_loads(statement)

    def add_specific_relation_loads(self, statement: Any, relation_names: list[str]) -> Any:
        return self.loader.add_specific_relation_loads(statement, relation_names)

    async def handle_relations(self, db: AsyncSession, instance: Any, data: dict[str, Any]) -> None:
        await self.crud.handle_relations(db, instance, data)

    async def preload_relations(self, db: AsyncSession, instance: Any, relation_info: dict[str, Any]) -> None:
        await self.loader.preload_relations(db, instance, relation_info)

    async def refresh_with_relations(self, db: AsyncSession, instance: Any, relation_info: dict[str, Any]) -> None:
        await self.loader.refresh_with_relations(db, instance, relation_info)

    async def handle_one_to_many_relation(
        self,
        db: AsyncSession,
        instance: Any,
        relation_name: str,
        relation_data: list[dict[str, Any]],
    ) -> None:
        await self.crud.handle_one_to_many_relation(db, instance, relation_name, relation_data)

    async def update_relations(
        self,
        db: AsyncSession,
        instance: Any,
        data: dict[str, Any],
    ) -> None:
        await self.crud.update_relations(db, instance, data)

    async def delete_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        ids_to_delete: set[int],
    ) -> None:
        await self.crud.delete_relation_objects(db, relation_attr, ids_to_delete)

    async def update_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        objects_to_update: list[dict[str, Any]],
    ) -> None:
        await self.crud.update_relation_objects(db, relation_attr, objects_to_update)

    async def create_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        parent_obj: Any,
        objects_to_create: list[dict[str, Any]],
    ) -> None:
        await self.crud.create_relation_objects(db, relation_attr, parent_obj, objects_to_create)

    def _set_foreign_key_value(
        self,
        item_data: dict[str, Any] | Any,
        foreign_key_field: str,
        parent_obj: Any,
        parent_tablename: str | None,
    ) -> None:
        self.crud._set_foreign_key_value(item_data, foreign_key_field, parent_obj, parent_tablename)

    def _create_model_instance(self, model: type, item_data: dict[str, Any] | Any) -> Any:
        return self.crud._create_model_instance(model, item_data)
