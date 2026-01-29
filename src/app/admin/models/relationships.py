"""
关联表定义

包含所有多对多关联表的定义
"""

from sqlalchemy import BigInteger, Column, ForeignKey, Table

from src.core.mixins import DataTableMixin
from src.database.schema_conf import SchemaType

# User-Role 多对多关联表
# 注意：外键列使用 BigInteger 以匹配主键类型
# 注意：由于关联表和目标表在同一 schema 中，外键引用只需使用表名
user_role = Table(
    "user_roles",
    DataTableMixin.metadata,
    Column(
        "user_id",
        BigInteger,
        ForeignKey("wes_sys.users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        BigInteger,
        ForeignKey("wes_sys.roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    schema=SchemaType.SYS.value,
    comment="用户-角色关联表",
)


# Role-Permission 多对多关联表
# 注意：外键列使用 BigInteger 以匹配主键类型
# 注意：由于关联表和目标表在同一 schema 中，外键引用只需使用表名
role_permission = Table(
    "role_permissions",
    DataTableMixin.metadata,
    Column(
        "role_id",
        BigInteger,
        ForeignKey("wes_sys.roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        BigInteger,
        ForeignKey("wes_sys.permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    schema=SchemaType.SYS.value,
    comment="角色-权限关联表",
)
