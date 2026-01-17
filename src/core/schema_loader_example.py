"""
schema_loader 使用示例

演示如何使用自动关系加载工具
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.models import Role, RoleRead
from src.core.schema_loader import (
    apply_schema_loads,
    get_all_with_schema,
    get_with_schema,
)


async def example_manual_apply(db: AsyncSession):
    """示例1: 手动应用加载选项"""
    query = select(Role)
    query = apply_schema_loads(query, Role, RoleRead)
    result = await db.execute(query)
    return result.scalars().all()


async def example_get_single(db: AsyncSession, role_id: int):
    """示例2: 使用便捷函数查询单个对象"""
    return await get_with_schema(db, Role, RoleRead, Role.id == role_id)


async def example_get_multiple(db: AsyncSession):
    """示例3: 使用便捷函数查询多个对象"""
    return await get_all_with_schema(db, Role, RoleRead, Role.is_active, limit=10)


async def example_nested_relationships(db: AsyncSession):
    """
    示例4: 自动处理嵌套关系

    如果 RoleRead 包含:
        permissions: list[PermissionRead]

    则会自动添加:
        .options(selectinload(Role.permissions))
    """
    return await get_all_with_schema(db, Role, RoleRead)
