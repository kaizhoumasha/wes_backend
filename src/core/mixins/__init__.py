"""
SQLModel Mixin 模块

提供可复用的模型字段和行为,遵循 DRY 原则

模块结构:
    base.py          - 基础 Mixin
    primary_key.py   - 主键 Mixin (IntPKMixin, SnowflakePKMixin, PrimaryKeyMixin)
    timestamp.py     - 时间戳 Mixin
    audit.py         - 审计 Mixin (Model 层)
    soft_delete.py   - 软删除 Mixin
    tree.py          - 树形结构 Mixin
    repr.py          - Repr Mixin
    composite.py     - 组合 Mixin (BaseModelMixin, AuditModelMixin, FullModelMixin, BaseTableModelMixin)

使用示例:
    from src.core.mixins import BaseTableModelMixin

    class User(BaseTableModelMixin, table=True):
        username: str
        email: str
"""  # noqa: W505

# ==================== 基础 Mixin ====================
# ==================== 审计 Mixin (Model 层) ====================
from src.core.mixins.audit import AuditMixin
from src.core.mixins.base import BaseMixin

# ==================== 组合 Mixin ====================
from src.core.mixins.composite import (
    AuditModelMixin,
    BaseModelMixin,
    BaseTableModelMixin,
    FullModelMixin,
)

# ==================== 主键 Mixin ====================
from src.core.mixins.primary_key import (
    IntPKMixin,
    PrimaryKeyMixin,
    SnowflakePKMixin,
)

# ==================== Repr Mixin ====================
from src.core.mixins.repr import ReprMixin

# ==================== 软删除 Mixin ====================
from src.core.mixins.soft_delete import SoftDeleteMixin

# ==================== 时间戳 Mixin ====================
from src.core.mixins.timestamp import TimestampMixin

# ==================== 树形结构 Mixin ====================
from src.core.mixins.tree import TreeMixin

__all__ = [
    # 审计 (Model 层)
    "AuditMixin",
    "AuditModelMixin",
    # 基础
    "BaseMixin",
    # 组合
    "BaseModelMixin",
    "BaseTableModelMixin",
    "FullModelMixin",
    # 主键
    "IntPKMixin",
    "PrimaryKeyMixin",
    # Repr
    "ReprMixin",
    "SnowflakePKMixin",
    # 软删除
    "SoftDeleteMixin",
    # 时间戳
    "TimestampMixin",
    # 树形结构
    "TreeMixin",
]
