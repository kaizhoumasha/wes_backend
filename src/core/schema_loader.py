"""
自动关系加载工具

根据 Pydantic ResponseSchema 自动推断并加载 SQLAlchemy 关系。
"""

from typing import Type, Any, get_origin, get_args, TypeVar
from pydantic import BaseModel
from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.inspection import inspect as sa_inspect

T = TypeVar("T")


def get_relationship_fields(schema: Type[BaseModel]) -> dict[str, Type[BaseModel] | None]:
    relationships = {}
    
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
                        arg = schema.model_fields[field_name].annotation.__args__[0]
                        if hasattr(schema, '__annotations__'):
                            import sys
                            if sys.version_info >= (3, 10):
                                from typing import get_type_hints
                                hints = get_type_hints(schema)
                                if field_name in hints:
                                    hint_origin = get_origin(hints[field_name])
                                    if hint_origin is list:
                                        hint_args = get_args(hints[field_name])
                                        if hint_args and isinstance(hint_args[0], type) and issubclass(hint_args[0], BaseModel):
                                            relationships[field_name] = hint_args[0]
                    except:
                        pass
                elif isinstance(arg, type) and issubclass(arg, BaseModel):
                    relationships[field_name] = arg
    
    return relationships


def apply_schema_loads(
    query: Select,
    model: Type[Any],
    schema: Type[BaseModel],
    strategy: str = "selectin",
    max_depth: int = 2
) -> Select:
    load_func = selectinload if strategy == "selectin" else joinedload
    
    def _build_loaders(current_model, current_schema, current_depth):
        if current_depth > max_depth:
            return []
            
        loaders = []
        relationships = get_relationship_fields(current_schema)
        
        for field_name, nested_schema in relationships.items():
            if not hasattr(current_model, field_name):
                continue
                
            rel_attr = getattr(current_model, field_name)
            
            if current_depth < max_depth and nested_schema:
                nested_model = rel_attr.property.mapper.class_
                loader = load_func(rel_attr)
                
                nested_rels = get_relationship_fields(nested_schema)
                for nested_field, _ in nested_rels.items():
                    if hasattr(nested_model, nested_field):
                        nested_attr = getattr(nested_model, nested_field)
                        loader = loader.selectinload(nested_attr)
                
                loaders.append(loader)
            else:
                loaders.append(load_func(rel_attr))
        
        return loaders
    
    loaders = _build_loaders(model, schema, 1)
    for loader in loaders:
        query = query.options(loader)
    
    return query


async def get_with_schema(
    db: AsyncSession,
    model: Type[T],
    schema: Type[BaseModel],
    *where_clauses,
    strategy: str = "selectin",
    max_depth: int = 2
) -> T | None:
    """
    根据 schema 自动加载关系并查询单个对象
    
    Args:
        db: 数据库会话
        model: SQLAlchemy 模型类
        schema: Pydantic ResponseSchema 类
        *where_clauses: WHERE 条件
        strategy: 加载策略
        max_depth: 最大递归深度
        
    Returns:
        查询结果或 None
        
    Example:
        user = await get_with_schema(db, User, UserRead, User.id == 1)
    """
    query = select(model).where(*where_clauses)
    query = apply_schema_loads(query, model, schema, strategy, max_depth)
    result = await db.execute(query)
    return result.scalars().first()


async def get_all_with_schema(
    db: AsyncSession,
    model: Type[T],
    schema: Type[BaseModel],
    *where_clauses,
    strategy: str = "selectin",
    max_depth: int = 2,
    limit: int | None = None,
    offset: int | None = None
) -> list[T]:
    """
    根据 schema 自动加载关系并查询多个对象
    
    Args:
        db: 数据库会话
        model: SQLAlchemy 模型类
        schema: Pydantic ResponseSchema 类
        *where_clauses: WHERE 条件
        strategy: 加载策略
        max_depth: 最大递归深度
        limit: 限制数量
        offset: 偏移量
        
    Returns:
        查询结果列表
        
    Example:
        users = await get_all_with_schema(db, User, UserRead, limit=10)
    """
    query = select(model)
    if where_clauses:
        query = query.where(*where_clauses)
    query = apply_schema_loads(query, model, schema, strategy, max_depth)
    if offset:
        query = query.offset(offset)
    if limit:
        query = query.limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


def model_to_schema(obj: Any, schema: Type[BaseModel]) -> BaseModel:
    """
    将 SQLAlchemy 模型转换为 Pydantic schema，只序列化已加载的关系
    
    Args:
        obj: SQLAlchemy 模型实例
        schema: Pydantic ResponseSchema 类
        
    Returns:
        Pydantic schema 实例
        
    Example:
        user_read = model_to_schema(user, UserRead)
    """
    data = {}
    insp = sa_inspect(obj)
    
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
                relationships = get_relationship_fields(schema)
                if field_name in relationships and relationships[field_name]:
                    nested_schema = relationships[field_name]
                    data[field_name] = [
                        model_to_schema(item, nested_schema).__dict__ 
                        for item in value
                    ]
                else:
                    data[field_name] = value
            else:
                relationships = get_relationship_fields(schema)
                if field_name in relationships and relationships[field_name]:
                    nested_schema = relationships[field_name]
                    data[field_name] = model_to_schema(value, nested_schema).__dict__
                else:
                    data[field_name] = value
    
    return schema(**data)
