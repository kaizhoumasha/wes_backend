"""
关系 CRUD 操作

负责处理关联对象的创建、更新和删除操作。
"""

from typing import Any, TypeVar

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.database.relation_metadata import RelationMetadata, RelationType

T = TypeVar("T")


class RelationCRUD:
    """
    关系 CRUD 操作

    负责关联对象的增删改操作，包括 Diff 算法和外键自动设置
    """

    def __init__(self, model: type[T]):
        self.model = model

    async def handle_relations(self, db: AsyncSession, instance: T, data: dict[str, Any]) -> None:
        if not RelationMetadata.has_relations(self.model):
            return

        relation_info = RelationMetadata.get_relation_info(self.model)

        for relation_name, info in relation_info.items():
            if relation_name not in data:
                continue

            relation_data = data[relation_name]
            if relation_data is None:
                continue

            relation_type = info.get("relation_type", "ONETOMANY")

            if relation_type == RelationType.ONETOMANY:
                await self.handle_one_to_many_relation(db, instance, relation_name, relation_data)

    async def handle_one_to_many_relation(
        self,
        db: AsyncSession,
        instance: T,
        relation_name: str,
        relation_data: list[dict[str, Any]],
    ) -> None:
        relation_attr = getattr(self.model, relation_name, None)
        if relation_attr is None:
            return

        await self.create_relation_objects(db, relation_attr, instance, relation_data)

    async def update_relations(
        self,
        db: AsyncSession,
        instance: T,
        data: dict[str, Any],
    ) -> None:
        if not RelationMetadata.has_relations(self.model):
            return

        relation_info = RelationMetadata.get_relation_info(self.model)

        for relation_name, info in relation_info.items():
            if relation_name not in data:
                continue

            new_relation_data = data[relation_name]
            if new_relation_data is None:
                continue

            relation_type = info.get("relation_type", "ONETOMANY")

            if relation_type != RelationType.ONETOMANY:
                continue

            relation_attr = getattr(self.model, relation_name, None)
            if relation_attr is None:
                continue

            current_relations = getattr(instance, relation_name, [])
            current_ids = {rel.id for rel in current_relations if hasattr(rel, "id") and rel.id is not None}

            new_ids = set()
            to_create = []
            to_update = []

            for item_data in new_relation_data:
                item_id = item_data.get("id") if isinstance(item_data, dict) else getattr(item_data, "id", None)

                if item_id is None:
                    to_create.append(item_data)
                else:
                    new_ids.add(item_id)
                    to_update.append(item_data)

            to_delete_ids = current_ids - new_ids

            if to_delete_ids:
                await self.delete_relation_objects(db, relation_attr, to_delete_ids)

            if to_update:
                await self.update_relation_objects(db, relation_attr, to_update)

            if to_create:
                await self.create_relation_objects(db, relation_attr, instance, to_create)

    async def delete_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        ids_to_delete: set[int],
    ) -> None:
        if not ids_to_delete:
            return

        relation_model = relation_attr.property.mapper.class_

        stmt = delete(relation_model).where(relation_model.id.in_(ids_to_delete))
        await db.execute(stmt)
        await db.flush()

        logger.info(f"删除关联对象: 数量={len(ids_to_delete)}")

    async def update_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        objects_to_update: list[dict[str, Any]],
    ) -> None:
        if not objects_to_update:
            return

        relation_model = relation_attr.property.mapper.class_

        for obj_data in objects_to_update:
            obj_id = obj_data.get("id") if isinstance(obj_data, dict) else obj_data.id

            stmt = select(relation_model).where(relation_model.id == obj_id)
            result = await db.execute(stmt)
            db_obj = result.scalar_one_or_none()

            if db_obj:
                update_data = obj_data if isinstance(obj_data, dict) else obj_data.model_dump(exclude_unset=True)
                for field, value in update_data.items():
                    if field == "id" or field.endswith("_id"):
                        continue
                    if hasattr(db_obj, field):
                        setattr(db_obj, field, value)
                db.add(db_obj)

        await db.flush()
        logger.info(f"更新关联对象: 数量={len(objects_to_update)}")

    async def create_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        parent_obj: T,
        objects_to_create: list[dict[str, Any]],
    ) -> None:
        if not objects_to_create:
            return

        relation_model = relation_attr.property.mapper.class_
        parent_tablename = getattr(parent_obj.__class__, "__tablename__", None)

        foreign_key_field = None
        if parent_tablename:
            foreign_key_field = RelationMetadata.find_foreign_key_for_table(relation_model, parent_tablename)

        for item_data in objects_to_create:
            if foreign_key_field:
                self._set_foreign_key_value(item_data, foreign_key_field, parent_obj, parent_tablename)

            new_obj = self._create_model_instance(relation_model, item_data)
            db.add(new_obj)

        await db.flush()
        logger.info(f"创建关联对象: 数量={len(objects_to_create)}")

    def _set_foreign_key_value(
        self,
        item_data: dict[str, Any] | Any,
        foreign_key_field: str,
        parent_obj: T,
        parent_tablename: str | None,
    ) -> None:
        parent_value = getattr(parent_obj, "id", None)
        if parent_value is None:
            return

        if isinstance(item_data, dict):
            item_data[foreign_key_field] = parent_value
            logger.debug(f"自动设置外键: {foreign_key_field}={parent_value} (从 {parent_tablename})")
        elif not hasattr(item_data, foreign_key_field) or getattr(item_data, foreign_key_field) is None:
            setattr(item_data, foreign_key_field, parent_value)
            logger.debug(f"自动设置外键: {foreign_key_field}={parent_value} (从 {parent_tablename})")

    def _create_model_instance(self, model: type, item_data: dict[str, Any] | Any) -> Any:
        if isinstance(item_data, dict):
            return model(**item_data)
        if hasattr(item_data, "model_dump"):
            return model(**item_data.model_dump())
        model_data = item_data.__dict__ if hasattr(item_data, "__dict__") else {}
        return model(**model_data)
