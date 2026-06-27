"""
数据库 Schema 配置

定义项目中使用的所有 PostgreSQL schema，用于组织和管理数据库对象。
"""

from enum import Enum


class SchemaType(str, Enum):
    """Schema 类型枚举"""

    # 系统管理相关
    SYS = "wes_sys"  # 系统配置、权限、角色、菜单等

    # 业务相关
    BIZ = "wes_biz"  # 通用业务表

    # 运行时编排相关
    RUNTIME = "wes_runtime"  # Runtime/orchestration 会话、correlation、inbox、timeline 等


# Schema 描述信息
SCHEMA_DESCRIPTIONS = {
    SchemaType.SYS: "系统管理相关表 - 配置、权限、角色、菜单等",
    SchemaType.BIZ: "业务相关表 - 通用业务数据",
    SchemaType.RUNTIME: "运行时编排相关表 - session、correlation、inbox、timeline 等",
}


# 所有 schema 的搜索路径（按优先级排序）
# 注意：PostgreSQL 会按照列表顺序搜索 schema
SCHEMA_SEARCH_PATH = [SchemaType.SYS.value, SchemaType.BIZ.value, SchemaType.RUNTIME.value]


def get_all_schemas() -> list[str]:
    """获取所有 schema 名称"""
    return [schema.value for schema in SchemaType]


def get_schema_search_path() -> str:
    """
    获取 schema 搜索路径字符串

    用于 PostgreSQL 的 search_path 配置

    Returns:
        逗号分隔的 schema 搜索路径，如: "wes_sys, wes_biz"
    """
    return ", ".join(SCHEMA_SEARCH_PATH)


def validate_schema(schema_name: str) -> bool:
    """
    验证 schema 名称是否有效

    Args:
        schema_name: 要验证的 schema 名称

    Returns:
        如果 schema 有效返回 True，否则返回 False
    """
    return schema_name in get_all_schemas()
