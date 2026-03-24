"""
关系 CRUD 操作

负责处理关联对象的创建、更新和删除操作。
"""

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.database.relation_metadata import RelationMetadata, RelationType


class RelationCRUD:
    """
    关系 CRUD 操作

    负责关联对象的增删改操作，包括 Diff 算法和外键自动设置
    """

    def __init__(self, model: type[Any]):
        self.model = model

    @staticmethod
    def _item_id(item_data: dict[str, Any] | object) -> int | None:
        raw_id = (
            cast("dict[str, Any]", item_data).get("id") if isinstance(item_data, dict) else getattr(item_data, "id", None)
        )
        return raw_id if isinstance(raw_id, int) else None

    @staticmethod
    def _model_data(item_data: dict[str, Any] | object) -> dict[str, Any]:
        if isinstance(item_data, dict):
            return cast("dict[str, Any]", item_data)
        if hasattr(item_data, "model_dump"):
            return cast("dict[str, Any]", cast("Any", item_data).model_dump(exclude_unset=True))
        model_data = item_data.__dict__ if hasattr(item_data, "__dict__") else {}
        return cast("dict[str, Any]", model_data)

    @staticmethod
    def _relation_model(relation_attr: Any) -> type[Any]:
        return cast("type[Any]", relation_attr.property.mapper.class_)

    def _iter_relation_entries(self, data: dict[str, Any]) -> list[tuple[str, Any, str]]:
        if not RelationMetadata.has_relations(self.model):
            return []

        relation_info = RelationMetadata.get_relation_info(self.model)
        entries: list[tuple[str, Any, str]] = []
        for relation_name, info in relation_info.items():
            if relation_name not in data:
                continue

            relation_data = data[relation_name]
            if relation_data is None:
                continue

            relation_type = cast("str", info.get("relation_type", "ONETOMANY"))
            entries.append((relation_name, relation_data, relation_type))
        return entries

    def _split_relation_changes(
        self, relation_data: Sequence[dict[str, Any] | object]
    ) -> tuple[set[int], list[dict[str, Any] | object], list[dict[str, Any] | object]]:
        new_ids: set[int] = set()
        to_create: list[dict[str, Any] | object] = []
        to_update: list[dict[str, Any] | object] = []

        for item_data in relation_data:
            item_id = self._item_id(item_data)
            if item_id is None:
                to_create.append(item_data)
                continue

            new_ids.add(item_id)
            to_update.append(item_data)

        return new_ids, to_create, to_update

    def _apply_update_data(self, db_obj: Any, obj_data: dict[str, Any] | object) -> None:
        update_data = self._model_data(obj_data)
        for field, value in update_data.items():
            if field == "id" or field.endswith("_id"):
                continue
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)

    @staticmethod
    def _parent_table_name(parent_obj: Any) -> str | None:
        return cast("str | None", getattr(parent_obj.__class__, "__tablename__", None))

    def _foreign_key_field(self, relation_model: type[Any], parent_obj: Any) -> tuple[str | None, str | None]:
        parent_tablename = self._parent_table_name(parent_obj)
        if not parent_tablename:
            return None, parent_tablename

        foreign_key_field = RelationMetadata.find_foreign_key_for_table(relation_model, parent_tablename)
        return foreign_key_field, parent_tablename

    async def handle_relations(self, db: AsyncSession, instance: Any, data: dict[str, Any]) -> None:
        for relation_name, relation_data, relation_type in self._iter_relation_entries(data):
            if relation_type == RelationType.ONETOMANY:
                await self.handle_one_to_many_relation(db, instance, relation_name, relation_data)

    async def handle_one_to_many_relation(
        self,
        db: AsyncSession,
        instance: Any,
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
        instance: Any,
        data: dict[str, Any],
    ) -> None:
        for relation_name, new_relation_data, relation_type in self._iter_relation_entries(data):
            if relation_type != RelationType.ONETOMANY:
                continue

            relation_attr = getattr(self.model, relation_name, None)
            if relation_attr is None:
                continue

            current_relations = cast("list[Any]", getattr(instance, relation_name, []))
            current_ids = {rel.id for rel in current_relations if hasattr(rel, "id") and rel.id is not None}
            new_ids, to_create, to_update = self._split_relation_changes(new_relation_data)
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

        relation_model = self._relation_model(relation_attr)

        stmt = delete(relation_model).where(relation_model.id.in_(ids_to_delete))
        _ = await db.execute(stmt)
        await db.flush()

        logger.info(f"删除关联对象: 数量={len(ids_to_delete)}")

    async def update_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        objects_to_update: Sequence[dict[str, Any] | object],
    ) -> None:
        if not objects_to_update:
            return

        relation_model = self._relation_model(relation_attr)

        for obj_data in objects_to_update:
            obj_id = self._item_id(obj_data)
            if obj_id is None:
                continue

            stmt = select(relation_model).where(relation_model.id == obj_id)
            result = await db.execute(stmt)
            db_obj = result.scalar_one_or_none()

            if db_obj:
                self._apply_update_data(db_obj, obj_data)
                db.add(db_obj)

        await db.flush()
        logger.info(f"更新关联对象: 数量={len(objects_to_update)}")

    async def create_relation_objects(
        self,
        db: AsyncSession,
        relation_attr: Any,
        parent_obj: Any,
        objects_to_create: Sequence[dict[str, Any] | object],
    ) -> None:
        if not objects_to_create:
            return

        relation_model = self._relation_model(relation_attr)
        foreign_key_field, parent_tablename = self._foreign_key_field(relation_model, parent_obj)

        for item_data in objects_to_create:
            if foreign_key_field:
                self._set_foreign_key_value(item_data, foreign_key_field, parent_obj, parent_tablename)

            new_obj = self._create_model_instance(relation_model, item_data)
            db.add(new_obj)

        await db.flush()
        logger.info(f"创建关联对象: 数量={len(objects_to_create)}")

    def _set_foreign_key_value(
        self,
        item_data: dict[str, Any] | object,
        foreign_key_field: str,
        parent_obj: Any,
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

    def _create_model_instance(self, model: type, item_data: dict[str, Any] | object) -> Any:
        model_data = self._model_data(item_data)
        return model(**model_data)
