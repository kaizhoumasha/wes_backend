"""
API 权限模型

后端 API 访问控制，保护接口安全

## 权限类型

- **user_api**: 用户 API 权限（内部管理系统 API）
- **app_api**: 应用 API 权限（外部应用 API）

## 核心设计

1. **职责单一**: 仅控制后端 API 访问权限，不涉及前端 UI
2. **安全第一**: API 权限是最终安全防线，不可绕过
3. **命名规范**: {module}:{resource}:{action}
   - 示例: `admin:user:create`, `biz:warehouse:update`

## 字段说明

- **name**: 权限唯一标识（如 admin:role:create）
- **type**: 权限类型（user_api/app_api）
- **method**: HTTP 方法（GET/POST/PUT/DELETE 等）
- **path**: API 路径（如 /admin/users/{id}）
- **resource**: 资源类型（如 user、role、warehouse）
- **action**: 操作类型（如 create、read、update、delete）

## 使用示例

```python
# 在 API 路由中使用
@router.post("/users",
    dependencies=[Depends(RequirePermission("admin:user:create"))]
)
async def create_user(data: UserCreate):
    pass
```
"""

from typing import ClassVar, Literal, cast

from pydantic import field_validator, model_validator
from sqlalchemy import Index
from sqlalchemy.orm import relationship
from sqlmodel import Field

from src.core.mixins import BaseMixin, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, TreeMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType

# ==================== Permission 模型 ====================


class PermissionBase(TreeMixin, BaseMixin):
    """API 权限基础字段"""

    parent_id: int | None = Field(
        default=None,
        foreign_key="wes_sys.permissions.id",
        index=True,
        sa_type=SQL_COMPAT_BIGINT,
    )

    # 权限标识
    name: str = Field(max_length=100, description="权限标识，如 admin:role:create")
    description: str | None = Field(default=None, max_length=255, description="权限描述")

    # 权限类型（仅支持 API 类型）
    type: str = Field(
        max_length=20,
        default="user_api",
        index=True,
        description="权限类型：user_api（内部管理API）、app_api（外部应用API）",
    )

    # 权限分类（用于分组管理）
    category: str | None = Field(default=None, max_length=50, description="权限分类：admin、system、business 等")

    # API 权限核心字段
    resource: str | None = Field(
        default=None, max_length=50, description="资源类型：user、role、permission、warehouse 等"
    )
    action: str | None = Field(default=None, max_length=50, description="操作：create、read、update、delete、list 等")
    method: str | None = Field(default=None, max_length=10, description="HTTP 方法：GET、POST、PUT、DELETE、PATCH 等")
    path: str | None = Field(
        default=None, max_length=255, description="API 路径：/admin/users/{id}、/api/v1/warehouses 等"
    )

    # ==================== 验证器 ====================

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        """验证权限名称格式：module:resource:action"""
        if not v:
            raise ValueError("name 不能为空")
        parts = v.split(":")
        if len(parts) < 2:
            raise ValueError("name 格式错误，应为 'module:resource:action'（如 admin:user:create）")
        return v

    @field_validator("type", mode="before")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """验证权限类型：只能是 user_api 或 app_api"""
        if v not in ("user_api", "app_api"):
            raise ValueError("type 必须是 'user_api' 或 'app_api'")
        return v

    def _get_validation_context(self) -> tuple[set[str], bool]:
        """获取验证上下文（DRY 优化：避免重复获取 fields_set 和 is_create）"""
        default_fields_set: set[str] = set()
        fields_set = cast("set[str]", getattr(self, "__pydantic_fields_set__", default_fields_set))
        is_create = self.__class__.__name__.endswith("Create")
        return fields_set, is_create

    @model_validator(mode="after")
    def validate_api_fields(self) -> "PermissionBase":
        """API 权限字段验证：所有 API 类型必须指定 method 和 path"""
        fields_set, is_create = self._get_validation_context()

        # 创建时必须指定 method 和 path
        if is_create:
            if not self.method:
                raise ValueError("API 权限必须指定 method（HTTP 方法）")
            if not self.path:
                raise ValueError("API 权限必须指定 path（API 路径）")

        # 更新时如果修改了这些字段，必须提供有效值
        if "method" in fields_set and not self.method:
            raise ValueError("API 权限必须指定 method（HTTP 方法）")
        if "path" in fields_set and not self.path:
            raise ValueError("API 权限必须指定 path（API 路径）")

        return self


class Permission(PermissionBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):  # type: ignore[misc]
    """API 权限表

    仅用于后端 API 访问控制，不包含前端菜单/按钮相关功能
    """

    __tablename__: ClassVar[Literal["permissions"]] = "permissions"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.SYS.value  # 系统管理表

    # 复合索引优化
    __table_args__ = (
        # 类型查询优化：type + is_deleted
        Index("ix_perm_type_deleted", "type", "is_deleted"),
        # 树形结构查询优化：parent_id + sort_order + is_deleted
        Index("ix_perm_parent_sort_deleted", "parent_id", "sort_order", "is_deleted"),
        # API 权限匹配优化：method + path + is_deleted（用于权限验证）
        Index("ix_perm_api_deleted", "method", "path", "is_deleted"),
        # 权限名称唯一索引（软删除后可重用名称）
        Index(
            "ux_perm_name_deleted",
            "name",
            unique=True,
            postgresql_where="NOT is_deleted",
        ),
    )


# ==================== Schemas ====================


class PermissionCreate(ModelFactory(PermissionBase).for_create()):
    """API 权限创建 Schema"""


class PermissionUpdate(ModelFactory(PermissionBase).for_optimistic_update()):
    """API 权限更新 Schema"""


class PermissionResponse(PermissionBase):
    """API 权限响应 Schema（完整版）"""

    id: int
    version: int


class PermissionResponseSimple(PermissionBase):
    """API 权限响应 Schema（简化版，用于列表展示）"""

    id: int


class PermissionTree(PermissionBase):
    """API 权限树形结构 Schema

    用于权限分组展示和管理（如按模块分组）
    """

    id: int
    children: list["PermissionTree"] = Field(default_factory=list, description="子权限列表")

    # 树形结构深度限制（防止层级过深导致性能问题）
    MAX_TREE_DEPTH: ClassVar[int] = 5

    @field_validator("children")
    @classmethod
    def validate_tree_depth(cls, v: list["PermissionTree"]) -> list["PermissionTree"]:
        """限制树形结构深度（最多 5 层）"""
        if v:
            max_depth = cls._calculate_max_depth(v)
            if max_depth > cls.MAX_TREE_DEPTH:
                raise ValueError(f"权限层级超过最大深度 {cls.MAX_TREE_DEPTH}，当前深度: {max_depth}")
        return v

    @classmethod
    def _calculate_max_depth(cls, nodes: list["PermissionTree"], current_depth: int = 1) -> int:
        """递归计算子树的最大深度"""
        if not nodes:
            return current_depth
        return max(cls._calculate_max_depth(node.children, current_depth + 1) for node in nodes)


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
