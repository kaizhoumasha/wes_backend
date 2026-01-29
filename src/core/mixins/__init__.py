"""
SQLModel Mixin 模块

提供可复用的模型字段和行为,遵循 DRY 原则

模块结构:
    base.py          - 基础 Mixin
    primary_key.py   - 主键 Mixin (PrimaryKeyMixin)
    timestamp.py     - 时间戳 Mixin
    audit.py         - 审计 Mixin (Model 层)
    soft_delete.py   - 软删除 Mixin
    tree.py          - 树形结构 Mixin
    repr.py          - Repr Mixin
    optimistic_lock.py - 乐观锁 Mixin
    schema.py        - Schema Mixin (PostgreSQL schema 支持)
    composite.py     - 组合 Mixin (StandardMixin, AuditableMixin, EnterpriseMixin, DataTableMixin)

使用示例:
    from src.core.mixins import DataTableMixin, SchemaMixin
    from src.database.schema_conf import SchemaType

    class User(SchemaMixin, DataTableMixin, table=True):
        __schema__ = SchemaType.AUTH.value
        username: str
        email: str
"""

# ==================== 基础 Mixin ====================
# ==================== 审计 Mixin (Model 层) ====================
from src.core.mixins.audit import AuditMixin
from src.core.mixins.base import BaseMixin
from src.core.mixins.schema import SchemaMixin

# ==================== 组合 Mixin ====================
from src.core.mixins.composite import (
    AuditableMixin,
    EnterpriseMixin,
    FullModelMixin,
    StandardMixin,
)

# ==================== 实体表 Mixin ====================
from src.core.mixins.datatable import DataTableMixin

# ==================== 乐观锁 Mixin ====================
from src.core.mixins.optimistic_lock import OptimisticLockMixin

# ==================== 主键 Mixin ====================
from src.core.mixins.primary_key import PrimaryKeyMixin

# ==================== Repr Mixin ====================
from src.core.mixins.repr import ReprMixin

# ==================== 软删除 Mixin ====================
from src.core.mixins.soft_delete import SoftDeleteMixin

# ==================== 时间戳 Mixin ====================
from src.core.mixins.timestamp import TimestampMixin

# ==================== 树形结构 Mixin ====================
from src.core.mixins.tree import TreeMixin

__all__ = [
    "AuditMixin",
    "AuditableMixin",
    "BaseMixin",
    "DataTableMixin",
    "EnterpriseMixin",
    "FullModelMixin",
    "OptimisticLockMixin",
    "PrimaryKeyMixin",
    "ReprMixin",
    "SchemaMixin",
    "SoftDeleteMixin",
    "StandardMixin",
    "TimestampMixin",
    "TreeMixin",
]
