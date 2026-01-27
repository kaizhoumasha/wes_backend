"""
树形结构 Mixin

为模型添加树形结构支持(物化路径模式)
"""

from sqlmodel import Field

from src.core.mixins.base import BaseMixin


class TreeMixin(BaseMixin):
    """
    树形结构 Mixin(物化路径模式)

    为模型添加树形结构支持:
    - parent_id: 父节点 ID
    - tree_path: 节点路径(如 /1/5/12/)
    - level: 节点层级
    - sort_order: 同级排序

    使用示例:
        class Category(TreeMixin, BaseTableModelMixin, table=True):
            name: str
    """

    __abstract__ = True

    parent_id: int | None = Field(
        default=None,
        index=True,
        sa_column_kwargs={"comment": "父节点ID"},
    )
    tree_path: str = Field(
        default="/",
        index=True,
        sa_column_kwargs={"comment": "节点路径"},
    )
    level: int = Field(
        default=1,
        sa_column_kwargs={"comment": "节点层级"},
    )
    sort_order: int = Field(
        default=0,
        sa_column_kwargs={"comment": "排序号"},
    )
