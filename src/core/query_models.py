"""
查询过滤模型定义
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FilterOperator(str, Enum):
    """过滤操作符"""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"
    IN = "in"
    NIN = "nin"
    ILIKE = "ilike"
    BETWEEN = "between"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"


class FilterCondition(BaseModel):
    """单个过滤条件"""

    field: str
    op: FilterOperator
    value: Any | None = None


class FilterGroup(BaseModel):
    """过滤条件组"""

    couple: Literal["and", "or", "not"] = "and"
    conditions: list[FilterCondition | FilterGroup] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "conditions": [
                    {
                        "field": "id",
                        "op": "gt",
                        "value": 1,
                    }
                ],
                "couple": "and",
            }
        }
    )


class SortField(BaseModel):
    """排序字段"""

    field: str
    order: Literal["asc", "desc"] = "desc"

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "field": "id",
                "order": "desc",
            }
        }
    )


class QueryOptions(BaseModel):
    """查询选项"""

    filters: FilterGroup | None = None
    sort: list[SortField] | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=100)
    max_depth: int = Field(default=1, ge=0, le=3)
    include_deleted: bool = Field(default=False, description="是否包含已删除记录")


__all__ = ["FilterCondition", "FilterGroup", "FilterOperator", "QueryOptions", "SortField"]
