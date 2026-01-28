"""
权限相关模型

包含 Permission 数据库表模型和相关的 Pydantic Schemas

## 权限类型

- **api**: API 接口权限（后端安全边界）
- **menu**: 菜单权限（控制前端路由和导航菜单）
- **button**: 按钮权限（控制操作按钮显示）

## 核心设计

1. **职责分离**: API 权限（后端安全）、Menu 权限（前端路由）、Button 权限（UI 控制）
2. **安全第一**: API 权限是最终安全保障，前端隐藏 ≠ 后端安全
3. **命名规范**: {module}:{resource}:{action}

## 文档

完整设计文档请参考: [docs/permission-model.md](../../../docs/permission-model.md)

包含内容：
- 详细的字段说明和示例
- FastAPI 集成指南
- Vue Router 完整集成方案
- TypeScript 类型定义
- 安全考虑和性能优化
"""

from typing import Any, ClassVar, Literal

from pydantic import ValidationInfo, computed_field, field_validator, model_validator
from sqlalchemy import JSON, Index
from sqlalchemy.orm import relationship
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin, TreeMixin
from src.database.model_factory import ModelFactory

# ==================== Permission 模型 ====================


class PermissionBase(TreeMixin, BaseMixin):
    """权限基础字段"""

    parent_id: int | None = Field(default=None, foreign_key="permissions.id", index=True)

    name: str = Field(max_length=100, unique=True, index=True, description="权限标识，如 admin:role:create")
    description: str | None = Field(default=None, max_length=255, description="权限描述")

    # 基础分类
    type: str = Field(
        default="api",
        index=True,
        description="权限类型：api（API接口）、menu（菜单）、button（按钮）",
    )
    category: str | None = Field(default=None, max_length=50, description="权限分类：admin、system、business 等")

    # API 权限字段
    resource: str | None = Field(default=None, max_length=50, description="资源类型：user、role、permission 等")
    action: str | None = Field(default=None, max_length=50, description="操作：create、read、update、delete 等")
    method: str | None = Field(default=None, max_length=10, description="HTTP 方法：GET、POST、PUT、DELETE 等")
    path: str | None = Field(default=None, max_length=255, description="API 路径或前端路由路径")

    # 菜单权限字段（Vue Router 兼容）
    component: str | None = Field(default=None, max_length=255, description="前端组件路径")
    icon: str | None = Field(default=None, max_length=50, description="菜单图标")
    redirect: str | None = Field(default=None, max_length=255, description="重定向路径")
    title: str | None = Field(default=None, max_length=100, description="菜单标题/页面标题（用于前端显示）")

    # 状态控制
    is_active: bool = Field(default=True, description="是否启用")
    is_hidden: bool = Field(default=False, description="是否隐藏（用于菜单）")
    is_cached: bool = Field(default=False, description="是否缓存路由（用于菜单 keepAlive）")
    is_affix: bool = Field(default=False, description="是否固定标签页（affix）")
    is_external: bool = Field(default=False, description="是否外部链接")

    # 外部链接地址（当 is_external=true 时使用）
    external_url: str | None = Field(default=None, max_length=500, description="外部链接 URL")

    # 元数据（JSON 存储扩展信息）
    meta: dict[str, Any] | None = Field(
        sa_type=JSON,
        default=None,
        description="扩展元数据（JSON 格式），可存储：badge、closeable 等",
    )

    # Button 权限关联的 API 权限（仅用于 type=button）
    # 用于前端按钮权限与后端 API 权限的映射
    api_permissions: list[str] | None = Field(
        sa_type=JSON,
        default=None,
        description="关联的 API 权限列表（仅用于 button 类型，如 ['admin:role:update', 'admin:role:delete']）",
    )

    # ==================== Pydantic v2 验证器 ====================

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        """验证权限名称格式：module:resource:action"""
        if not v:
            raise ValueError("name 不能为空")
        parts = v.split(":")
        if len(parts) < 2:
            raise ValueError("name 格式错误，应为 'module:resource:action'")
        return v

    @field_validator("type", mode="before")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """验证权限类型"""
        if v not in ("api", "menu", "button"):
            raise ValueError("type 必须是 'api', 'menu', 或 'button'")
        return v

    @field_validator("external_url")
    @classmethod
    def validate_external_url(cls, v: str | None, info: ValidationInfo) -> str | None:
        """外部链接必须与 is_external 配合使用"""
        if v is not None:
            # 获取当前字段值（在验证后）
            data = info.data
            is_external = data.get("is_external") if isinstance(data, dict) else None
            if not is_external:
                # 如果设置了 external_url 但 is_external 为 False，发出警告但允许通过
                # (某些场景可能需要预先设置 URL)
                pass
        return v

    def _get_validation_context(self) -> tuple[set[str], bool]:
        """获取验证上下文（DRY 优化：避免重复获取 fields_set 和 is_create）"""
        fields_set = getattr(self, "__pydantic_fields_set__", set())
        is_create = self.__class__.__name__.endswith("Create")
        return fields_set, is_create

    @model_validator(mode="after")
    def validate_button_permissions(self) -> "PermissionBase":
        """button 类型必须有关联的 API 权限"""
        fields_set, is_create = self._get_validation_context()
        if self.type == "button" and (is_create or "api_permissions" in fields_set) and not self.api_permissions:
            raise ValueError("button 类型必须指定 api_permissions")
        return self

    @model_validator(mode="after")
    def validate_external_link_consistency(self) -> "PermissionBase":
        """外部链接一致性验证"""
        fields_set, is_create = self._get_validation_context()
        if self.is_external and (is_create or "external_url" in fields_set) and not self.external_url:
            raise ValueError("is_external=True 时必须指定 external_url")
        return self

    @model_validator(mode="after")
    def validate_menu_fields(self) -> "PermissionBase":
        """菜单类型字段验证"""
        fields_set, is_create = self._get_validation_context()
        if self.type == "menu" and (is_create or "path" in fields_set) and not self.path:
            raise ValueError("menu 类型必须指定 path")
        return self

    @model_validator(mode="after")
    def validate_api_fields(self) -> "PermissionBase":
        """API 类型字段验证"""
        fields_set, is_create = self._get_validation_context()
        if self.type == "api":
            if is_create and (not self.method or not self.path):
                raise ValueError("api 类型必须指定 method 和 path")
            if "method" in fields_set and not self.method:
                raise ValueError("api 类型必须指定 method")
            if "path" in fields_set and not self.path:
                raise ValueError("api 类型必须指定 path")
        return self


class Permission(PermissionBase, DataTableMixin, table=True):  # type: ignore[misc]
    """权限表"""

    __tablename__: Literal["permissions"] = "permissions"

    # 复合索引优化
    __table_args__ = (
        # 组合查询优化：type + is_active
        Index("ix_permissions_type_active", "type", "is_active"),
        # 菜单排序优化：parent_id + sort_order
        Index("ix_permissions_parent_sort", "parent_id", "sort_order"),
        # API 权限查询优化：method + path
        Index("ix_permissions_api", "method", "path"),
    )


# ==================== Schemas ====================


# 使用 ModelFactory 创建 Permission Schema（单例模式）
class PermissionCreate(ModelFactory(PermissionBase).for_create()):
    """权限创建 Schema"""


class PermissionUpdate(ModelFactory(PermissionBase).for_update()):
    """权限更新 Schema"""


class PermissionResponse(PermissionBase):
    """权限响应 Schema（完整版，含子权限信息）"""

    id: int

    @computed_field
    @property
    def full_name(self) -> str:
        """生成完整的权限标识（包含类型前缀）"""
        return f"[{self.type.upper()}] {self.name}"


class PermissionResponseSimple(PermissionBase):
    """权限响应 Schema（简化版，不含子权限）"""

    id: int

    @computed_field
    @property
    def display_name(self) -> str:
        """生成显示名称（优先使用 title，其次 description）"""
        return self.title or self.description or self.name


class PermissionTree(PermissionBase):
    """权限树形结构 Schema（用于前端菜单树）

    支持 Vue Router 动态路由生成，包含深度限制和路由配置计算
    """

    id: int
    children: list["PermissionTree"] = Field(default_factory=list, description="子权限列表")

    # 树形结构深度限制（防止菜单层级过深导致性能问题）
    MAX_TREE_DEPTH: ClassVar[int] = 5

    @field_validator("children")
    @classmethod
    def validate_tree_depth(cls, v: list["PermissionTree"]) -> list["PermissionTree"]:
        """限制树形结构深度（最多 5 层）"""
        if v:
            max_depth = cls._calculate_max_depth(v)
            if max_depth > cls.MAX_TREE_DEPTH:
                raise ValueError(f"菜单层级超过最大深度 {cls.MAX_TREE_DEPTH}，当前深度: {max_depth}")
        return v

    @classmethod
    def _calculate_max_depth(cls, nodes: list["PermissionTree"], current_depth: int = 1) -> int:
        """递归计算子树的最大深度"""
        if not nodes:
            return current_depth
        return max(cls._calculate_max_depth(node.children, current_depth + 1) for node in nodes)

    @computed_field
    @property
    def is_leaf(self) -> bool:
        """是否为叶子节点（无子权限）"""
        return len(self.children) == 0

    @computed_field
    @property
    def has_children(self) -> bool:
        """是否有子权限"""
        return len(self.children) > 0

    @computed_field
    @property
    def route_config(self) -> dict[str, Any]:
        """生成 Vue Router 配置对象

        可直接用于前端动态路由生成：
        ```typescript
        const routes = permissions.map(p => p.route_config)
        router.addRoute(routes)
        ```
        """
        config: dict[str, Any] = {
            "path": self.path,
            "name": self.name,
            "meta": {
                "title": self.title or self.description,
                "icon": self.icon,
                "hidden": self.is_hidden,
                "keepAlive": self.is_cached,
                "affix": self.is_affix,
                "orderNo": self.sort_order,
            },
        }

        # 组件路径（仅非外部链接）
        if self.component and not self.is_external:
            config["component"] = self.component

        # 重定向
        if self.redirect:
            config["redirect"] = self.redirect

        # 外部链接
        if self.is_external:
            config["meta"]["isExternal"] = True
            config["meta"]["externalUrl"] = self.external_url

        # 扩展元数据
        if self.meta:
            config["meta"].update(self.meta)

        # 子路由
        if self.children:
            config["children"] = [child.route_config for child in self.children]

        return config

    @computed_field
    @property
    def breadcrumb(self) -> list[dict[str, Any]]:
        """生成面包屑导航数据

        返回从根到当前节点的路径，用于面包屑导航
        """
        return [
            {
                "title": self.title or self.description,
                "name": self.name,
                "path": self.path,
                "icon": self.icon,
            }
        ]


# ==================== Relationships ====================
# 在类外部定义关系（SQLModel 兼容方式）


# 自引用关系（父子关系）
Permission.parent = relationship(
    "Permission",
    remote_side="Permission.id",
    back_populates="children",
    foreign_keys="Permission.parent_id",  # type: ignore[arg-type]
)

Permission.children = relationship(
    "Permission",
    back_populates="parent",
    foreign_keys="Permission.parent_id",  # type: ignore[arg-type]
    order_by="Permission.sort_order",
)
