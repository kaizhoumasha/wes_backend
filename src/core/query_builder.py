"""
查询构建器
"""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import InstrumentedAttribute

from src.core.logger import logger
from src.core.query_models import FilterCondition, FilterGroup, FilterOperator, SortField


class QueryBuilder:
    """查询构建器"""

    def __init__(self, model: type):
        self.model = model

    def build_filters(self, filter_group: FilterGroup) -> Any:
        """构建过滤条件"""
        clauses = []
        for condition in filter_group.conditions:
            if isinstance(condition, FilterGroup):
                clauses.append(self.build_filters(condition))
            else:
                clause = self._build_condition(condition)
                if clause is not None:
                    clauses.append(clause)

        if not clauses:
            return None

        match filter_group.couple:
            case "and":
                return sa.and_(*clauses)
            case "or":
                return sa.or_(*clauses)
            case "not":
                return sa.not_(clauses[0])

    def _build_condition(self, condition: FilterCondition) -> Any | None:
        """构建单个条件"""
        if condition.field.startswith("$rel."):
            return self._build_relation_condition(condition)

        if not hasattr(self.model, condition.field):
            logger.warning(f"字段 {condition.field} 不存在于模型 {self.model.__name__}")
            return None

        field = getattr(self.model, condition.field)

        match condition.op:
            case FilterOperator.EQ:
                return field == condition.value
            case FilterOperator.NE:
                return field != condition.value
            case FilterOperator.GT:
                return field > condition.value
            case FilterOperator.GE:
                return field >= condition.value
            case FilterOperator.LT:
                return field < condition.value
            case FilterOperator.LE:
                return field <= condition.value
            case FilterOperator.IN:
                return field.in_(condition.value)
            case FilterOperator.NIN:
                return ~field.in_(condition.value)
            case FilterOperator.ILIKE:
                return field.ilike(f"%{condition.value}%")
            case FilterOperator.BETWEEN:
                return field.between(condition.value[0], condition.value[1])
            case FilterOperator.IS_NULL:
                return field.is_(None)
            case FilterOperator.NOT_NULL:
                return field.is_not(None)

    def _build_relation_condition(self, condition: FilterCondition) -> Any | None:
        """构建关联条件"""
        rel_path = condition.field[5:].split(".")
        if len(rel_path) != 2:
            logger.warning(f"关联字段格式错误: {condition.field}")
            return None

        rel_name, rel_field = rel_path
        if not hasattr(self.model, rel_name):
            logger.warning(f"关联 {rel_name} 不存在于模型 {self.model.__name__}")
            return None

        rel_attr = getattr(self.model, rel_name)
        if not isinstance(rel_attr.property, InstrumentedAttribute):
            rel_model = rel_attr.property.mapper.class_
            if not hasattr(rel_model, rel_field):
                logger.warning(f"字段 {rel_field} 不存在于关联模型 {rel_model.__name__}")
                return None

            rel_field_attr = getattr(rel_model, rel_field)

            match condition.op:
                case FilterOperator.EQ:
                    return rel_attr.any(rel_field_attr == condition.value)
                case FilterOperator.IN:
                    return rel_attr.any(rel_field_attr.in_(condition.value))
                case FilterOperator.ILIKE:
                    return rel_attr.any(rel_field_attr.ilike(f"%{condition.value}%"))

        return None

    def build_sort(self, sort_fields: list[SortField]) -> list[Any]:
        """构建排序"""
        order_by = []
        for sort_field in sort_fields:
            if not hasattr(self.model, sort_field.field):
                logger.warning(f"排序字段 {sort_field.field} 不存在于模型 {self.model.__name__}")
                continue

            field = getattr(self.model, sort_field.field)
            if sort_field.order == "desc":
                order_by.append(field.desc())
            else:
                order_by.append(field.asc())

        return order_by
