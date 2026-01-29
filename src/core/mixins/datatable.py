"""
基础表模型 Mixin

提供 SQLModel 表模型的基础配置和元数据访问。
遵循 DRY 原则，通过委托模式将元数据获取委托给 RelationMetadata。

使用示例：
    from src.core.mixins.datatable import DataTableMixin

    class User(DataTableMixin, table=True):
        username: str
        email: str

    # 无需定义 id、表名，自动继承
    # 元数据通过委托获取，带缓存性能优化
"""

from typing import Any

from sqlalchemy.orm import declared_attr

from src.core.mixins.primary_key import PrimaryKeyMixin
from src.core.mixins.schema import SchemaMixin
from src.core.mixins.timestamp import TimestampMixin
from src.database.relation_metadata import RelationMetadata


class DataTableMixin(SchemaMixin, PrimaryKeyMixin, TimestampMixin):
    """
    基础表模型 Mixin

    组合了:
    - 主键（自增/雪花）- 来自 PrimaryKeyMixin
    - Pydantic 配置
    - 表名自动生成
    - 元数据访问（委托给 RelationMetadata，带缓存）

    设计原则:
    - 单一职责：只负责表模型的基础配置和元数据访问
    - DRY：通过委托模式避免代码重复
    - 性能优化：元数据使用 @lru_cache 缓存
    """

    __abstract__ = True

    class Config:
        from_attributes = True
        use_enum_values = True
        validate_assignment = True
        arbitrary_types_allowed = True

    @declared_attr.directive
    def __tablename__(self) -> str:
        """自动生成表名（类名转小写）"""
        return self.__name__.lower()

    @property
    def __unique_info__(self) -> list[dict[str, Any]]:
        """
        获取唯一约束信息（委托给 RelationMetadata）

        Returns:
            唯一约束信息列表

        Example:
            >>> user = User()
            >>> user.__unique_info__
            [{"columns": ["username"]}, {"columns": ["email"]}]
        """
        return RelationMetadata.get_unique_info(self.__class__)  # type: ignore[arg-type]

    @property
    def __foreign_info__(self) -> dict[str, Any]:
        """
        获取外键信息（委托给 RelationMetadata，带缓存）

        Returns:
            外键信息字典

        Example:
            >>> item = InboundItem()
            >>> item.__foreign_info__
            {"inbound_id": {"target_table": "wms_inbound", "target_column": "id"}}
        """
        return RelationMetadata.get_foreign_info(self.__class__)  # type: ignore[arg-type]

    @property
    def __relation_info__(self) -> dict[str, Any]:
        """
        获取关系信息（委托给 RelationMetadata，带缓存）

        Returns:
            关系信息字典

        Example:
            >>> plan = ProjectPlan()
            >>> plan.__relation_info__
            {
                "items": {
                    "relation_model": ProjectPlanItem,
                    "relation_type": "ONETOMANY",
                    "relation_table": "pm_project_plan_item",
                    ...
                }
            }
        """
        return RelationMetadata.get_relation_info(self.__class__)  # type: ignore[arg-type]

    @property
    def __field_info__(self) -> dict[str, Any]:
        """
        获取字段信息（委托给 RelationMetadata，带缓存）

        Returns:
            字段信息字典

        Example:
            >>> user = User()
            >>> user.__field_info__
            {
                "id": {"type": "BIGINT", "nullable": false, "primary_key": true, ...},
                "username": {"type": "VARCHAR", "nullable": false, ...}
            }
        """
        return RelationMetadata.get_field_info(self.__class__)  # type: ignore[arg-type]

    @property
    def __nested_field_info__(self) -> dict[str, Any]:
        """
        获取嵌套字段信息（组合所有元数据）

        Returns:
            包含外键、关系、字段信息的嵌套字典

        Example:
            >>> user = User()
            >>> user.__nested_field_info__
            {
                "foreign_info": {...},
                "relation_info": {...},
                "field_info": {...}
            }
        """
        return {
            "foreign_info": self.__foreign_info__,
            "relation_info": self.__relation_info__,
            "field_info": self.__field_info__,
        }
