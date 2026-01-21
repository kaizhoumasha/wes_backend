"""
关联表定义

包含所有多对多关联表的定义
"""

from sqlalchemy import BigInteger, Column, ForeignKey, Table

from src.core.mixins import BaseTableModelMixin

# User-Role 多对多关联表
# 注意：外键列使用 BigInteger 以匹配主键类型
user_role = Table(
    "user_roles",
    BaseTableModelMixin.metadata,
    Column(
        "user_id",
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        BigInteger,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    comment="用户-角色关联表",
)


# Role-Permission 多对多关联表
# 注意：外键列使用 BigInteger 以匹配主键类型
role_permission = Table(
    "role_permissions",
    BaseTableModelMixin.metadata,
    Column(
        "role_id",
        BigInteger,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        BigInteger,
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    comment="角色-权限关联表",
)
