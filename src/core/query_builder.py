"""
查询构建器

支持复杂查询条件的构建，包括字段过滤、关联过滤和排序。

ILIKE 操作符约定：
    调用方需传入完整 pattern（例如 %foo%、foo%、%foo）
    使用反斜杠转义特殊字符（%、_、\\），支持字面量搜索
"""

from collections.abc import Callable
from datetime import date, datetime
from enum import Enum
from typing import Any, ClassVar, cast
from uuid import UUID

import sqlalchemy as sa

from src.core.exceptions import InvalidParameterException
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
        value = self._coerce_value_for_field(field, condition)

        return builder(field, value) if builder else None

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

    def _coerce_value_for_field(self, field: Any, condition: FilterCondition) -> Any:
        if condition.op in {FilterOperator.IS_NULL, FilterOperator.NOT_NULL}:
            return None

        column = self._resolve_column(field)
        if column is None:
            return condition.value

        python_type = self._resolve_python_type(column.type)
        if python_type is None:
            return condition.value

        if condition.op == FilterOperator.BETWEEN and isinstance(condition.value, (list, tuple)):
            return [self._coerce_scalar_value(item, python_type, condition.field) for item in condition.value]

        if condition.op in {FilterOperator.IN, FilterOperator.NIN} and isinstance(condition.value, (list, tuple, set)):
            return [self._coerce_scalar_value(item, python_type, condition.field) for item in condition.value]

        return self._coerce_scalar_value(condition.value, python_type, condition.field)

    def _resolve_column(self, field: Any) -> Any | None:
        property_obj = getattr(field, "property", None)
        columns = getattr(property_obj, "columns", None)
        if not columns:
            return None
        return columns[0]

    def _resolve_python_type(self, column_type: Any) -> type[Any] | None:
        try:
            return cast("type[Any] | None", column_type.python_type)
        except (AttributeError, NotImplementedError):
            return None

    def _coerce_scalar_value(self, value: Any, python_type: type[Any], field_name: str) -> Any:
        if value is None or isinstance(value, python_type):
            return value

        try:
            if python_type is datetime:
                return self._parse_datetime(value, field_name)
            if python_type is date:
                return self._parse_date(value, field_name)
            if python_type is bool:
                return self._parse_bool(value, field_name)
            if python_type is UUID:
                return UUID(str(value))
            if isinstance(python_type, type) and issubclass(python_type, Enum):
                return python_type(value)
            if python_type in {int, float, str}:
                return python_type(value)
        except (TypeError, ValueError) as exc:
            raise InvalidParameterException(field=field_name, message=f"字段 '{field_name}' 的筛选值格式无效") from exc

        return value

    def _parse_datetime(self, value: Any, field_name: str) -> datetime:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            raise InvalidParameterException(field=field_name, message=f"字段 '{field_name}' 需要 datetime 值")

        normalized = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)

    def _parse_date(self, value: Any, field_name: str) -> date:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            raise InvalidParameterException(field=field_name, message=f"字段 '{field_name}' 需要 date 值")
        return date.fromisoformat(value.strip())

    def _parse_bool(self, value: Any, field_name: str) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        raise InvalidParameterException(field=field_name, message=f"字段 '{field_name}' 需要布尔值")

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


__all__ = ["QueryBuilder"]
