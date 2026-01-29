"""
Schema Mixin

为 SQLModel 表模型提供 PostgreSQL schema 支持。

默认 schema 为 wes_sys（系统管理表），业务表需要显式设置为 wes_biz。

使用示例:
    from src.core.mixins import SchemaMixin
    from src.database.schema_conf import SchemaType

    # 系统管理表（使用默认 schema）
    class User(SchemaMixin, table=True):
        username: str

    # 或显式设置为 sys schema
    class User(SchemaMixin, table=True):
        __schema__ = SchemaType.SYS.value
        username: str

    # 业务表（需要显式设置）
    class Product(SchemaMixin, table=True):
        __schema__ = SchemaType.BIZ.value
        name: str
"""

from typing import Any, ClassVar

from sqlmodel import SQLModel

from src.database.schema_conf import SchemaType


class SchemaMixin(SQLModel):
    """
    Schema Mixin

    为表模型指定 PostgreSQL schema。

    通过设置类变量 __schema__ 来指定表所属的 schema。
    子类在定义时会自动将 schema 添加到 __table_args__ 中。

    Attributes:
        __schema__: 表所属的 schema 名称（如 "wes_sys", "wes_biz"）

    Example:
        # 简单用法（无额外的 table_args）
        class User(SchemaMixin, DataTableMixin, table=True):
            __schema__ = SchemaType.SYS.value
            username: str
            email: str

        # 带索引的用法（需要显式包含 schema）
        class Permission(SchemaMixin, DataTableMixin, table=True):
            __schema__ = SchemaType.SYS.value
            __table_args__ = (
                Index("ix_perm_name", "name"),
                {"schema": SchemaType.SYS.value}  # 必须显式包含 schema
            )
    """

    __schema__: ClassVar[str] = SchemaType.SYS.value  # 默认为 sys schema（系统管理表）

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        子类化时自动设置 __table_args__ 的 schema 参数

        这确保了表在创建时会被放置到正确的 schema 中。
        """
        super().__init_subclass__(**kwargs)

        # 只处理真正的表模型（table=True 的类）
        # 检查是否是表模型：如果 kwargs 中有 table=True 或者类已经有 __tablename__
        is_table = kwargs.get("table", False) or hasattr(cls, "__tablename__")

        if not is_table:
            return

        # 获取现有的 __table_args__
        existing_table_args = cls.__dict__.get("__table_args__", None)

        # 获取 schema 值
        schema = cls.__schema__

        # 如果没有定义 __table_args__，创建新的
        if existing_table_args is None:
            cls.__table_args__ = {"schema": schema}
            return

        # 如果是字典形式
        if isinstance(existing_table_args, dict):
            # 如果已经有 schema，不覆盖（允许子类显式设置）
            if "schema" not in existing_table_args:
                existing_table_args["schema"] = schema
            return

        # 如果是元组形式（包含索引、约束等）
        if isinstance(existing_table_args, tuple):
            # 检查最后一个元素是否是字典
            if len(existing_table_args) > 0 and isinstance(existing_table_args[-1], dict):
                # 最后一个元素是字典，添加 schema（如果不存在）
                table_args_dict = existing_table_args[-1]
                if "schema" not in table_args_dict:
                    table_args_dict["schema"] = schema
            else:
                # 没有字典元素，创建一个并添加到元组末尾
                cls.__table_args__ = (*existing_table_args, {"schema": schema})
