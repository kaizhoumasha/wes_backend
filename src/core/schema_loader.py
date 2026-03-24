"""
自动关系加载工具

根据 Pydantic ResponseSchema 自动推断并加载 SQLAlchemy 关系。
"""

from typing import Any, TypeVar, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import joinedload, selectinload

T = TypeVar("T")


def get_relationship_fields(
    schema: type[BaseModel],
) -> dict[str, type[BaseModel] | None]:
    relationships: dict[str, type[BaseModel] | None] = {}

    for field_name, field_info in schema.model_fields.items():
        annotation = field_info.annotation

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            relationships[field_name] = annotation
            continue

        origin = get_origin(annotation)
        if origin is list:
            args = get_args(annotation)
            if args:
                arg = args[0]
                if isinstance(arg, str):
                    try:
                        field_annotation = schema.model_fields[field_name].annotation
                        if field_annotation is not None and hasattr(field_annotation, "__args__"):
                            arg = field_annotation.__args__[0]
                        if hasattr(schema, "__annotations__"):
                            hints = get_type_hints(schema)
                            if field_name in hints:
                                hint_origin = get_origin(hints[field_name])
                                if hint_origin is list:
                                    hint_args = get_args(hints[field_name])
                                    if (
                                        hint_args
                                        and isinstance(hint_args[0], type)
                                        and issubclass(hint_args[0], BaseModel)
                                    ):
                                        relationships[field_name] = hint_args[0]
                    except (AttributeError, IndexError, TypeError):
                        pass
                elif isinstance(arg, type) and issubclass(arg, BaseModel):
                    relationships[field_name] = arg

    return relationships


def apply_schema_loads(
    query: Select[Any],
    model: type[Any],
    schema: type[BaseModel],
    max_depth: int = 2,
) -> Select[Any]:
    """
    根据 schema 应用智能关系加载策略

    自动根据关系类型选择最优加载策略：
    - 一对一关系：使用 joinedload（单次 JOIN 查询）
    - 一对多/多对多：使用 selectinload（避免笛卡尔积）

    Args:
        query: SQLAlchemy 查询对象
        model: SQLAlchemy 模型类
        schema: Pydantic ResponseSchema 类
        max_depth: 最大递归深度

    Returns:
        应用了加载选项的查询对象
    """
    from src.database.relation_metadata import RelationMetadata, RelationType

    def _get_optimal_loader(relation_name: str, current_model: type[Any]) -> Any:
        """根据关系类型选择最优加载策略"""
        relation_type = RelationMetadata.get_relation_type(current_model, relation_name)
        if relation_type == RelationType.ONETOONE:
            return joinedload  # 一对一：单次 JOIN，性能最优
        return selectinload  # 一对多/多对多：避免笛卡尔积

    def _build_loaders(current_model: type[Any], current_schema: type[BaseModel], current_depth: int) -> list[Any]:
        """递归构建关系加载器"""
        if current_depth > max_depth:
            return []

        # 验证模型是否有关系
        if not RelationMetadata.has_relations(current_model):
            return []

        loaders: list[Any] = []
        schema_relationships = get_relationship_fields(current_schema)
        model_relationships = RelationMetadata.get_relation_info(current_model)

        for field_name, nested_schema in schema_relationships.items():
            # 验证关系在模型中真实存在
            if field_name not in model_relationships:
                continue

            rel_attr = cast("Any", getattr(current_model, field_name))
            load_func = _get_optimal_loader(field_name, current_model)

            if current_depth < max_depth and nested_schema:
                # 递归处理嵌套关系
                nested_model = cast("type[Any]", rel_attr.property.mapper.class_)
                loader = load_func(rel_attr)

                # 为嵌套关系也应用智能加载
                nested_schema_rels = get_relationship_fields(nested_schema)
                nested_model_rels = RelationMetadata.get_relation_info(nested_model)

                for nested_field in nested_schema_rels:
                    if nested_field in nested_model_rels:
                        nested_attr = getattr(nested_model, nested_field)
                        nested_load_func = _get_optimal_loader(nested_field, nested_model)
                        loader = loader.options(nested_load_func(nested_attr))

                loaders.append(loader)
            else:
                loaders.append(load_func(rel_attr))

        return loaders

    loaders = _build_loaders(model, schema, 1)
    for loader in loaders:
        query = query.options(loader)

    return query


async def get_with_schema[T](
    db: AsyncSession,
    model: type[T],
    schema: type[BaseModel],
    *where_clauses: Any,
    max_depth: int = 2,
) -> T | None:
    """
    根据 schema 自动加载关系并查询单个对象

    自动根据关系类型选择最优加载策略：
    - 一对一关系：使用 joinedload（单次 JOIN 查询）
    - 一对多/多对多：使用 selectinload（避免笛卡尔积）

    Args:
        db: 数据库会话
        model: SQLAlchemy 模型类
        schema: Pydantic ResponseSchema 类
        *where_clauses: WHERE 条件
        max_depth: 最大递归深度

    Returns:
        查询结果或 None

    Example:
        user = await get_with_schema(db, User, UserResponse, User.id == 1)
    """
    query = cast("Select[Any]", select(model).where(*where_clauses))
    query = apply_schema_loads(query, model, schema, max_depth)
    result = await db.execute(query)
    return cast("T | None", result.scalars().first())


async def get_all_with_schema[T](
    db: AsyncSession,
    model: type[T],
    schema: type[BaseModel],
    *where_clauses: Any,
    max_depth: int = 2,
    limit: int | None = None,
    offset: int | None = None,
    order_by: list[Any] | None = None,
) -> list[T]:
    """
    根据 schema 自动加载关系并查询多个对象

    自动根据关系类型选择最优加载策略：
    - 一对一关系：使用 joinedload（单次 JOIN 查询）
    - 一对多/多对多：使用 selectinload（避免笛卡尔积）

    Args:
        db: 数据库会话
        model: SQLAlchemy 模型类
        schema: Pydantic ResponseSchema 类
        *where_clauses: WHERE 条件
        max_depth: 最大递归深度
        limit: 限制数量
        offset: 偏移量
        order_by: 排序条件列表

    Returns:
        查询结果列表

    Example:
        users = await get_all_with_schema(db, User, UserResponse, limit=10)
        users = await get_all_with_schema(db, User, UserResponse, order_by=[User.id.desc()])
    """
    query = cast("Select[Any]", select(model))
    if where_clauses:
        query = query.where(*where_clauses)
    query = apply_schema_loads(query, model, schema, max_depth)
    if order_by:
        query = query.order_by(*order_by)
    if offset:
        query = query.offset(offset)
    if limit:
        query = query.limit(limit)
    result = await db.execute(query)
    return cast("list[T]", list(result.scalars().all()))


def model_to_schema(obj: Any, schema: type[BaseModel]) -> BaseModel:
    """
    将 SQLAlchemy 模型转换为 Pydantic schema，只序列化已加载的关系

    Args:
        obj: SQLAlchemy 模型实例
        schema: Pydantic ResponseSchema 类

    Returns:
        Pydantic schema 实例

    Example:
        user_read = model_to_schema(user, UserResponse)
    """
    data: dict[str, Any] = {}
    insp = sa_inspect(obj)
    relationships = get_relationship_fields(schema)

    for field_name, field_info in schema.model_fields.items():
        if field_name in insp.unloaded:
            annotation = field_info.annotation
            origin = get_origin(annotation)
            if origin is list:
                data[field_name] = []
            else:
                data[field_name] = None
        else:
            value = getattr(obj, field_name)

            if value is None:
                data[field_name] = None
            elif isinstance(value, list):
                nested_schema = relationships.get(field_name)
                if nested_schema:
                    items = cast("list[Any]", value)
                    data[field_name] = [model_to_schema(item, nested_schema).__dict__ for item in items]
                else:
                    data[field_name] = value
            else:
                nested_schema = relationships.get(field_name)
                if nested_schema:
                    data[field_name] = model_to_schema(value, nested_schema).__dict__
                else:
                    data[field_name] = value

    return schema(**data)
