"""
查询构建器

支持复杂查询条件的构建，包括字段过滤、关联过滤和排序。

ILIKE 操作符约定：
    调用方需传入完整 pattern（例如 %foo%、foo%、%foo）
    使用反斜杠转义特殊字符（%、_、\\），支持字面量搜索
"""

from collections.abc import Callable
from typing import Any, ClassVar, cast

import sqlalchemy as sa

from src.core.logger import logger
from src.core.query_models import FilterCondition, FilterGroup, FilterOperator, SortField

# LIKE/ILIKE 转义字符：使用反斜杠支持字面量 %、_、\\ 搜索
_SQL_ESCAPE_CHAR: str = "\\"
FilterClause = sa.ColumnElement[bool]
FieldOperator = Callable[[Any, Any], FilterClause]
RelationOperator = Callable[[Any, Any, Any], FilterClause]


class QueryBuilder:
    """查询构建器"""

    # 字段操作符映射：operator -> lambda function
    _FIELD_OPERATOR_MAP: ClassVar[dict[FilterOperator, FieldOperator]] = {
        FilterOperator.EQ: lambda f, v: f == v,
        FilterOperator.NE: lambda f, v: f != v,
        FilterOperator.GT: lambda f, v: f > v,
        FilterOperator.GE: lambda f, v: f >= v,
        FilterOperator.LT: lambda f, v: f < v,
        FilterOperator.LE: lambda f, v: f <= v,
        FilterOperator.IN: lambda f, v: f.in_(v),
        FilterOperator.NIN: lambda f, v: ~f.in_(v),
        FilterOperator.ILIKE: lambda f, v: f.ilike(v, escape=_SQL_ESCAPE_CHAR),
        FilterOperator.BETWEEN: lambda f, v: f.between(v[0], v[1]),
        FilterOperator.IS_NULL: lambda f, _: f.is_(None),
        FilterOperator.NOT_NULL: lambda f, _: f.is_not(None),
    }

    # 关联操作符映射
    _REL_OPERATOR_MAP: ClassVar[dict[FilterOperator, RelationOperator]] = {
        FilterOperator.EQ: lambda ra, rf, v: ra.any(rf == v),
        FilterOperator.IN: lambda ra, rf, v: ra.any(rf.in_(v)),
        FilterOperator.ILIKE: lambda ra, rf, v: ra.any(rf.ilike(v, escape=_SQL_ESCAPE_CHAR)),
    }

    def __init__(self, model: type):
        self.model = model

    def build_filters(self, filter_group: FilterGroup) -> FilterClause | None:
        """构建过滤条件"""
        clauses: list[FilterClause] = []
        for condition in filter_group.conditions:
            if isinstance(condition, FilterGroup):
                nested_clause = self.build_filters(condition)
                if nested_clause is not None:
                    clauses.append(nested_clause)
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
        return None

    def _build_condition(self, condition: FilterCondition) -> FilterClause | None:
        """构建单个条件"""
        if condition.field.startswith("$rel."):
            return self._build_relation_condition(condition)

        if not hasattr(self.model, condition.field):
            logger.warning(f"字段 {condition.field} 不存在于模型 {self.model.__name__}")
            return None

        field = cast("Any", getattr(self.model, condition.field))
        builder = self._FIELD_OPERATOR_MAP.get(condition.op)

        return builder(field, condition.value) if builder else None

    def _build_relation_condition(self, condition: FilterCondition) -> FilterClause | None:
        """构建关联条件"""
        rel_path = condition.field[5:].split(".")
        if len(rel_path) != 2:
            logger.warning(f"关联字段格式错误: {condition.field}")
            return None

        rel_name, rel_field = rel_path
        if not hasattr(self.model, rel_name):
            logger.warning(f"关联 {rel_name} 不存在于模型 {self.model.__name__}")
            return None

        rel_attr = cast("Any", getattr(self.model, rel_name))
        rel_property = cast("Any", getattr(rel_attr, "property", None))
        if rel_property is None or not hasattr(rel_property, "mapper"):
            logger.warning(f"关联 {rel_name} 不是有效的关系字段")
            return None

        rel_model = cast("type[Any]", rel_property.mapper.class_)
        if not hasattr(rel_model, rel_field):
            logger.warning(f"字段 {rel_field} 不存在于关联模型 {rel_model.__name__}")
            return None

        rel_field_attr = cast("Any", getattr(rel_model, rel_field))
        builder = self._REL_OPERATOR_MAP.get(condition.op)

        return builder(rel_attr, rel_field_attr, condition.value) if builder else None

    def build_sort(self, sort_fields: list[SortField]) -> list[sa.ColumnElement[Any]]:
        """构建排序"""
        order_by: list[sa.ColumnElement[Any]] = []
        for sort_field in sort_fields:
            if not hasattr(self.model, sort_field.field):
                logger.warning(f"排序字段 {sort_field.field} 不存在于模型 {self.model.__name__}")
                continue

            field = cast("Any", getattr(self.model, sort_field.field))
            if sort_field.order == "desc":
                order_by.append(field.desc())
            else:
                order_by.append(field.asc())

        return order_by


__ALL__ = ["QueryBuilder"]
