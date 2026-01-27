"""
关系加载器

负责处理关联对象的加载策略选择和预加载逻辑。
"""

from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.database.relation_metadata import RelationMetadata, RelationType

T = TypeVar("T")


class RelationLoader:
    """
    关系加载器

    负责选择最优的关联对象加载策略（joinedload vs selectinload）
    """

    def __init__(self, model: type[T], pk_column: str = "id"):
        self.model = model
        self._pk_column = pk_column

    def add_relation_load(self, statement: Any, relation_name: str, relation_info: Any) -> Any:
        relation_attr = getattr(self.model, relation_name, None)
        if relation_attr is None:
            return statement

        relation_type = relation_info.get("relation_type", "ONETOMANY")

        if relation_type == RelationType.ONETOONE:
            statement = statement.options(joinedload(relation_attr))
        else:
            statement = statement.options(selectinload(relation_attr))

        return statement

    def add_all_relation_loads(self, statement: Any) -> Any:
        if not RelationMetadata.has_relations(self.model):
            return statement

        relation_info = RelationMetadata.get_relation_info(self.model)

        for relation_name, info in relation_info.items():
            statement = self.add_relation_load(statement, relation_name, info)

        return statement

    def add_specific_relation_loads(self, statement: Any, relation_names: list[str]) -> Any:
        relation_info = RelationMetadata.get_relation_info(self.model)

        for relation_name in relation_names:
            if relation_name in relation_info:
                info = relation_info[relation_name]
                statement = self.add_relation_load(statement, relation_name, info)

        return statement

    async def preload_relations(self, db: AsyncSession, instance: T, relation_info: dict[str, Any]) -> None:
        if not relation_info:
            return

        options = [
            selectinload(getattr(self.model, relation_name))
            for relation_name in relation_info
            if hasattr(self.model, relation_name)
        ]

        if options:
            stmt = select(self.model).where(getattr(self.model, self._pk_column) == getattr(instance, self._pk_column))
            for option in options:
                stmt = stmt.options(option)

            result = await db.execute(stmt)
            loaded_instance = result.scalar_one_or_none()

            if loaded_instance:
                for relation_name in relation_info:
                    if hasattr(loaded_instance, relation_name):
                        setattr(instance, relation_name, getattr(loaded_instance, relation_name))

    async def refresh_with_relations(self, db: AsyncSession, instance: T, relation_info: dict[str, Any]) -> None:
        if relation_info:
            relation_names = list(relation_info.keys())
            await db.refresh(instance, attribute_names=relation_names)
        else:
            await db.refresh(instance)
