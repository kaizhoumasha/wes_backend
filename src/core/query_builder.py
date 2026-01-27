"""
查询构建器
"""

from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.orm import InstrumentedAttribute

from src.core.logger import logger
from src.core.query_models import FilterCondition, FilterGroup, FilterOperator, SortField


class QueryBuilder:
    """查询构建器"""

    # 字段操作符映射：operator -> lambda function
    _FIELD_OPERATOR_MAP: ClassVar[dict] = {
        FilterOperator.EQ: lambda f, v: f == v,
        FilterOperator.NE: lambda f, v: f != v,
        FilterOperator.GT: lambda f, v: f > v,
        FilterOperator.GE: lambda f, v: f >= v,
        FilterOperator.LT: lambda f, v: f < v,
        FilterOperator.LE: lambda f, v: f <= v,
        FilterOperator.IN: lambda f, v: f.in_(v),
        FilterOperator.NIN: lambda f, v: ~f.in_(v),
        FilterOperator.ILIKE: lambda f, v: f.ilike(f"%{v}%"),
        FilterOperator.BETWEEN: lambda f, v: f.between(v[0], v[1]),
        FilterOperator.IS_NULL: lambda f, _: f.is_(None),
        FilterOperator.NOT_NULL: lambda f, _: f.is_not(None),
    }

    # 关联操作符映射
    _REL_OPERATOR_MAP: ClassVar[dict] = {
        FilterOperator.EQ: lambda ra, rf, v: ra.any(rf == v),
        FilterOperator.IN: lambda ra, rf, v: ra.any(rf.in_(v)),
        FilterOperator.ILIKE: lambda ra, rf, v: ra.any(rf.ilike(f"%{v}%")),
    }

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
        builder = self._FIELD_OPERATOR_MAP.get(condition.op)

        return builder(field, condition.value) if builder else None

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
            builder = self._REL_OPERATOR_MAP.get(condition.op)

            return builder(rel_attr, rel_field_attr, condition.value) if builder else None

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


__ALL__ = ["QueryBuilder"]
