"""
菜单/路由模型

提供基于角色的动态菜单路由系统
"""

from typing import TYPE_CHECKING, ClassVar, Literal

from sqlalchemy import Index
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, TreeMixin
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

if TYPE_CHECKING:
    from src.app.admin.models.role import RoleResponse


# ==================== Menu 模型 ====================


class MenuBase(TreeMixin, BaseMixin):
    """菜单基础字段（TreeMixin 提供：parent_id, tree_path, level, sort_order）"""

    name: str = Field(max_length=50, description="菜单标识，如 system:users")
    title: str = Field(max_length=50, description="显示标题")
    path: str = Field(max_length=200, description="路由路径，如 /system/users")
    component: str | None = Field(default=None, max_length=200, description="组件路径，如 views/system/users.vue")
    icon: str | None = Field(default=None, max_length=50, description="图标")
    is_hidden: bool = Field(default=False, description="是否隐藏")


class Menu(MenuBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):  # type: ignore[misc]
    """菜单数据库表"""

    __tablename__: ClassVar[Literal["menus"]] = "menus"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.SYS.value

    __table_args__ = (
        # 菜单名称唯一索引（软删除后可重用名称）
        Index(
            "ux_menus_name_deleted",
            "name",
            unique=True,
            postgresql_where="NOT is_deleted",
        ),
    )

    # 关联关系在 src/app/admin/models/__init__.py 中统一绑定


# ==================== Schemas ====================


class MenuCreate(ModelFactory(MenuBase).for_create()):
    """菜单创建 Schema"""


class MenuUpdate(ModelFactory(MenuBase).for_optimistic_update()):
    """菜单更新 Schema"""


class MenuResponse(MenuBase):
    """菜单响应 Schema"""

    id: int
    version: int
    roles: list["RoleResponse"] = Field(default_factory=list)  # 关联角色


class MenuTreeResponse(MenuResponse):
    """菜单树响应 Schema"""

    children: list["MenuResponse"] = Field(default_factory=list)


class MenuTreeResponseSimple(MenuBase):
    """菜单树响应 Schema"""

    id: int
    version: int
    children: list["MenuTreeResponseSimple"] = Field(default_factory=list)


__all__ = [
    "Menu",
    "MenuBase",
    "MenuCreate",
    "MenuResponse",
    "MenuTreeResponse",
    "MenuTreeResponseSimple",
    "MenuUpdate",
]
