"""
关联对象元数据系统

提供主从表关联关系的元数据定义和处理工具。

设计理念：
- 直接使用 SQLAlchemy 的 inspect() 获取关联关系信息
- 支持 ONETOMANY、ONETOONE、MANYTOMANY 关系类型
- 自动从 SQLAlchemy 的 Relationship 中提取信息
- 遵循 DRY 原则，避免信息冗余

使用示例：
    # 主表模型定义（只需定义 Relationship）
    class ProjectPlan(SQLModel, table=True):
        __tablename__ = "pm_project_plan"

        # 只需定义 Relationship，不需要 __relation_info__
        items: list["ProjectPlanItem"] = Relationship(back_populates="plan")

    # 从表模型定义
    class ProjectPlanItem(SQLModel, table=True):
        __tablename__ = "pm_project_plan_item"

        # 只需定义外键，不需要 __foreign_info__
        plan_id: int = Field(foreign_key="pm_project_plan.id")

        plan: Optional["ProjectPlan"] = Relationship(back_populates="items")

    # RelationMetadata 会自动从 SQLAlchemy 获取所有信息
    relation_info = RelationMetadata.get_relation_info(ProjectPlan)
    # 返回: {"items": {"relation_model": ProjectPlanItem, "relation_type": "ONETOMANY", ...}}
"""

from enum import Enum
from typing import TypedDict

from sqlalchemy import inspect
from sqlalchemy.orm import RelationshipDirection


class RelationType(str, Enum):
    """关系类型枚举"""

    ONETOONE = "ONETOONE"  # 一对一
    ONETOMANY = "ONETOMANY"  # 一对多
    MANYTOMANY = "MANYTOMANY"  # 多对多
    MANYTOONE = "MANYTOONE"  # 多对一


class RelationInfo(TypedDict, total=False):
    """
    关联关系信息

    Attributes:
        relation_model: 关联模型类（实际的类，不是字符串）
        relation_type: 关系类型（ONETOONE/ONETOMANY/MANYTOMANY/MANYTOONE）
        uselist: 是否是集合（True for list, False for single）
        cascade: 级联操作配置（可选）
    """

    relation_model: type
    relation_type: str
    uselist: bool
    cascade: str | None


class ForeignKeyInfo(TypedDict):
    """
    外键信息

    Attributes:
        target_table: 目标表名
        target_column: 目标列名
    """

    target_table: str
    target_column: str


class RelationMetadata:
    """
    关联关系元数据工具类

    提供关联关系元数据的读取和验证功能。
    直接从 SQLAlchemy 的 inspect() 获取信息，无需自定义元数据。
    """

    @staticmethod
    def get_relation_info(model: type) -> dict[str, RelationInfo]:
        """
        获取模型的关联关系信息（从 SQLAlchemy 获取）

        Args:
            model: SQLModel 类

        Returns:
            关联关系信息字典，键为关联属性名，值为 RelationInfo

        Example:
            >>> relation_info = RelationMetadata.get_relation_info(Inbound)
            >>> print(relation_info)
            {
                "items": {
                    "relation_model": <class 'InboundItem'>,
                    "relation_type": "ONETOMANY",
                    "uselist": True
                }
            }
        """
        # 向后兼容：如果模型定义了 __relation_info__，优先使用
        if hasattr(model, "__relation_info__"):
            return model.__relation_info__  # type: ignore[attr-defined]

        # 从 SQLAlchemy 获取关系信息
        try:
            mapper = inspect(model)
        except Exception:
            return {}

        relation_info: dict[str, RelationInfo] = {}

        for rel_name, rel in mapper.relationships.items():
            # 将 SQLAlchemy 的 RelationshipDirection 转换为我们的 RelationType
            # 注意：SQLAlchemy 没有 ONETOONE，一对一通过 ONETOMANY + uselist=False 表示
            if rel.direction == RelationshipDirection.ONETOMANY:
                relation_type = (
                    RelationType.ONETOMANY.value if rel.uselist else RelationType.ONETOONE.value
                )
            elif rel.direction == RelationshipDirection.MANYTOONE:
                # MANYTOONE 总是 uselist=False，这是正常的多对一关系
                relation_type = RelationType.MANYTOONE.value
            elif rel.direction == RelationshipDirection.MANYTOMANY:
                relation_type = RelationType.MANYTOMANY.value
            else:
                relation_type = RelationType.ONETOMANY.value  # 默认

            relation_info[rel_name] = {
                "relation_model": rel.mapper.class_,  # 实际的类，不是字符串
                "relation_type": relation_type,
                "uselist": rel.uselist,
            }

        return relation_info

    @staticmethod
    def get_foreign_info(model: type) -> dict[str, ForeignKeyInfo]:
        """
        获取模型的外键信息（从 SQLAlchemy 获取）

        Args:
            model: SQLModel 类

        Returns:
            外键信息字典，键为外键字段名，值为 ForeignKeyInfo

        Example:
            >>> foreign_info = RelationMetadata.get_foreign_info(InboundItem)
            >>> print(foreign_info)
            {
                "inbound_id": {
                    "target_table": "wms_inbound",
                    "target_column": "id"
                }
            }
        """
        # 向后兼容：如果模型定义了 __foreign_info__，优先使用
        if hasattr(model, "__foreign_info__"):
            return model.__foreign_info__  # type: ignore[attr-defined]

        # 从 SQLAlchemy 获取外键信息
        try:
            mapper = inspect(model)
        except Exception:
            return {}

        foreign_info: dict[str, ForeignKeyInfo] = {}

        for column in mapper.columns:
            if column.foreign_keys:
                # 只取第一个外键（通常一个列只有一个外键）
                for fk in column.foreign_keys:
                    foreign_info[column.name] = {
                        "target_table": fk.column.table.name,
                        "target_column": fk.column.name,
                    }
                    break

        return foreign_info

    @staticmethod
    def has_relations(model: type) -> bool:
        """
        检查模型是否有关联关系

        Args:
            model: SQLModel 类

        Returns:
            是否有关联关系

        Example:
            >>> RelationMetadata.has_relations(Inbound)
            True
            >>> RelationMetadata.has_relations(User)
            False
        """
        # 向后兼容：先检查 __relation_info__
        if hasattr(model, "__relation_info__") and bool(model.__relation_info__):  # type: ignore[attr-defined]
            return True

        # 从 SQLAlchemy 检查
        try:
            mapper = inspect(model)
            return len(mapper.relationships) > 0
        except Exception:
            return False

    @staticmethod
    def get_relation_type(model: type, relation_name: str) -> RelationType | None:
        """
        获取指定关联关系的类型

        Args:
            model: SQLModel 类
            relation_name: 关联属性名

        Returns:
            关系类型或 None

        Example:
            >>> RelationMetadata.get_relation_type(Inbound, "items")
            <RelationType.ONETOMANY: 'ONETOMANY'>
        """
        relation_info = RelationMetadata.get_relation_info(model)
        if relation_name in relation_info:
            relation_type_str = relation_info[relation_name].get("relation_type", "ONETOMANY")
            try:
                return RelationType(relation_type_str)
            except ValueError:
                return RelationType.ONETOMANY
        return None

    @staticmethod
    def is_one_to_many(model: type, relation_name: str) -> bool:
        """
        检查是否为一对多关系

        Args:
            model: SQLModel 类
            relation_name: 关联属性名

        Returns:
            是否为一对多关系

        Example:
            >>> RelationMetadata.is_one_to_many(Inbound, "items")
            True
        """
        relation_type = RelationMetadata.get_relation_type(model, relation_name)
        return relation_type == RelationType.ONETOMANY

    @staticmethod
    def is_one_to_one(model: type, relation_name: str) -> bool:
        """
        检查是否为一对一关系

        Args:
            model: SQLModel 类
            relation_name: 关联属性名

        Returns:
            是否为一对一关系

        Example:
            >>> RelationMetadata.is_one_to_one(Order, "payment")
            True
        """
        relation_type = RelationMetadata.get_relation_type(model, relation_name)
        return relation_type == RelationType.ONETOONE

    @staticmethod
    def find_foreign_key_for_table(model: type, target_table: str) -> str | None:
        """
        查找指向目标表的外键字段名

        Args:
            model: SQLModel 类
            target_table: 目标表名

        Returns:
            外键字段名或 None

        Example:
            >>> RelationMetadata.find_foreign_key_for_table(InboundItem, "wms_inbound")
            'inbound_id'
        """
        foreign_info = RelationMetadata.get_foreign_info(model)
        for foreign_key, info in foreign_info.items():
            if info["target_table"] == target_table:
                return foreign_key
        return None


__all__ = [
    "ForeignKeyInfo",
    "RelationInfo",
    "RelationMetadata",
    "RelationType",
]
