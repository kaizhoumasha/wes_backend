"""WMS operation 的静态 Definition 合同。

本模块只描述不可配置的北向语义；endpoint 编译、认证和运行时派发由后续任务实现。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field, model_validator

OperationIdentity = Annotated[str, StringConstraints(pattern=r"^wms\.[a-z0-9_]+\.[a-z0-9_]+@v[1-9][0-9]*$")]
StableCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]
TerminalIdentityValidator = Callable[[BaseModel, BaseModel], object]


class WmsOperationMode(str, Enum):
    """operation 是否改变 WMS 业务状态。"""

    QUERY = "QUERY"
    EFFECT = "EFFECT"


class WmsHttpMethod(str, Enum):
    """合同允许的 HTTP method。"""

    GET = "GET"
    POST = "POST"


class WmsCompletionMode(str, Enum):
    """EFFECT 的唯一完成口径。"""

    SYNC_RESULT = "SYNC_RESULT"
    ASYNC_TASK = "ASYNC_TASK"


class WmsExecutionLane(str, Enum):
    """静态执行 lane，不允许 Provider profile 覆盖。"""

    WMS_DATA = "wms-data"
    WMS_FULFILLMENT = "wms-fulfillment"


class WmsDomainProjectionKind(str, Enum):
    """需要本地履约投影的 WMS operation 类型。"""

    RACK_SUPPLY_DEMAND = "RACK_SUPPLY_DEMAND"


class WmsOperationBudget(BaseModel):
    """覆盖全部 attempts、分页和 backoff 的总预算。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    deadline_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_attempts: int = Field(ge=1, le=10)
    backoff_seconds: tuple[float, ...]
    max_wire_bytes: int = Field(gt=0)
    max_decoded_bytes: int = Field(gt=0)
    max_rows: int | None = Field(default=None, gt=0)

    @property
    def max_chunk_bytes(self) -> int:
        return min(262_144, self.max_wire_bytes)

    @property
    def max_compression_ratio(self) -> float:
        return 20.0

    @property
    def allowed_content_encodings(self) -> tuple[str, ...]:
        return ("identity", "gzip", "deflate")

    @property
    def max_json_depth(self) -> int:
        return 12

    @property
    def max_field_length(self) -> int:
        return 16_384

    @model_validator(mode="after")
    def validate_attempt_budget(self) -> WmsOperationBudget:
        if len(self.backoff_seconds) != self.max_attempts - 1:
            raise ValueError("backoff_seconds count must equal max_attempts - 1")
        if any(not math.isfinite(value) or value <= 0 for value in self.backoff_seconds):
            raise ValueError("backoff_seconds must contain finite positive values")
        if self.max_decoded_bytes < self.max_wire_bytes:
            raise ValueError("max_decoded_bytes must cover max_wire_bytes")
        return self


class WmsPaginationConstraint(BaseModel):
    """列表 QUERY 的 cursor 和总量上限。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_cursor_field: str = "cursor"
    response_cursor_field: str = "next_cursor"
    response_items_field: str = "items"
    max_pages: int = Field(ge=1)
    max_rows: int = Field(ge=1)
    max_page_size: int = Field(ge=1)


class WmsOperationDefinition(BaseModel):
    """单项 operation 的唯一静态声明。"""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    identity: OperationIdentity
    mode: WmsOperationMode
    request_model: type[BaseModel]
    result_model: type[BaseModel]
    http_method: WmsHttpMethod
    path_template: str = Field(pattern=r"^/")
    target_code: str = Field(pattern=r"^WMS_[A-Z0-9_]+$")
    execution_lane: WmsExecutionLane
    completion_mode: WmsCompletionMode | None
    side_effect_free: bool
    budget: WmsOperationBudget
    pagination: WmsPaginationConstraint | None
    error_codes: tuple[StableCode, ...] = Field(min_length=1)
    reject_codes: tuple[StableCode, ...] = Field(min_length=1)
    terminal_identity_validator: TerminalIdentityValidator | None = Field(default=None, exclude=True)
    domain_projection_kind: WmsDomainProjectionKind | None = None

    @computed_field
    @property
    def supports_status_query(self) -> bool:
        return self.completion_mode is WmsCompletionMode.ASYNC_TASK

    @model_validator(mode="after")
    def validate_static_semantics(self) -> WmsOperationDefinition:  # 静态合同集中闭合
        if self.request_model is self.result_model:
            raise ValueError("request_model and result_model must be operation-specific")
        for model in (self.request_model, self.result_model):
            if not model.__module__.startswith("src.app.wms_integration.ports."):
                raise ValueError("typed models must be owned by wms_integration.ports")
            if model.model_config.get("extra") != "forbid":
                raise ValueError("typed models must reject extra fields")
        if len(self.error_codes) != len(set(self.error_codes)):
            raise ValueError("error_codes must not contain duplicates")
        if len(self.reject_codes) != len(set(self.reject_codes)):
            raise ValueError("reject_codes must not contain duplicates")
        if self.mode is WmsOperationMode.QUERY:
            if self.domain_projection_kind is not None:
                raise ValueError("QUERY must not declare domain_projection_kind")
            if self.terminal_identity_validator is not None:
                raise ValueError("QUERY must not declare terminal_identity_validator")
            if self.completion_mode is not None or self.execution_lane is not WmsExecutionLane.WMS_DATA:
                raise ValueError("QUERY must use wms-data without completion_mode")
            if not self.side_effect_free:
                raise ValueError("QUERY must be side-effect free")
            if self.pagination is None and self.budget.max_rows is not None:
                raise ValueError("single-result QUERY must not declare row budget")
            if self.pagination is not None:
                if self.budget.max_rows != self.pagination.max_rows:
                    raise ValueError("pagination and transport row budgets must match")
                required_fields = {
                    self.pagination.response_items_field,
                    self.pagination.response_cursor_field,
                }
                if not required_fields <= set(self.result_model.model_fields):
                    raise ValueError("list QUERY result must expose items and cursor")
            return self
        if self.http_method is not WmsHttpMethod.POST or self.side_effect_free:
            raise ValueError("EFFECT must use POST and may change WMS state")
        if self.completion_mode is None or self.pagination is not None or self.budget.max_rows is not None:
            raise ValueError("EFFECT requires completion_mode and forbids pagination")
        if self.completion_mode is WmsCompletionMode.SYNC_RESULT and self.terminal_identity_validator is None:
            raise ValueError("SYNC_RESULT EFFECT requires terminal_identity_validator")
        if self.completion_mode is WmsCompletionMode.ASYNC_TASK and self.terminal_identity_validator is not None:
            raise ValueError("ASYNC_TASK EFFECT must not declare terminal_identity_validator")
        expected_domain_projection = {
            "wms.fulfillment.request_rack_supply@v1": WmsDomainProjectionKind.RACK_SUPPLY_DEMAND,
        }.get(self.identity)
        if self.domain_projection_kind is not expected_domain_projection:
            raise ValueError("domain_projection_kind is only valid for its authored fulfillment operation")
        return self


COMMON_ERROR_CODES = (
    "WMS_PROVIDER_TIMEOUT",
    "WMS_RATE_LIMITED",
    "WMS_UNAVAILABLE",
    "WMS_MALFORMED_RESPONSE",
    "WMS_WIRE_BUDGET_EXCEEDED",
    "WMS_EVIDENCE_WRITE_FAILED",
)
EFFECT_ERROR_CODES = (
    *COMMON_ERROR_CODES,
    "IDEMPOTENCY_CONFLICT",
    "IDEMPOTENCY_REQUEST_IN_PROGRESS",
)
SINGLE_QUERY_BUDGET = WmsOperationBudget(
    deadline_seconds=10,
    max_attempts=3,
    backoff_seconds=(1, 2),
    max_wire_bytes=1_048_576,
    max_decoded_bytes=4_194_304,
)
LIST_QUERY_BUDGET = WmsOperationBudget(
    deadline_seconds=10,
    max_attempts=3,
    backoff_seconds=(1, 2),
    max_wire_bytes=1_048_576,
    max_decoded_bytes=4_194_304,
    max_rows=10_000,
)
EFFECT_BUDGET = WmsOperationBudget(
    deadline_seconds=10,
    max_attempts=3,
    backoff_seconds=(1, 2),
    max_wire_bytes=262_144,
    max_decoded_bytes=262_144,
)
STANDARD_LIST_PAGINATION = WmsPaginationConstraint(max_pages=100, max_rows=10_000, max_page_size=500)


def query_operation(
    *,
    identity: str,
    request_model: type[BaseModel],
    result_model: type[BaseModel],
    path_template: str,
    target_code: str,
    reject_codes: tuple[str, ...],
    list_result: bool = False,
    http_method: WmsHttpMethod = WmsHttpMethod.GET,
) -> WmsOperationDefinition:
    """构造一项静态 QUERY Definition。"""

    return WmsOperationDefinition(
        identity=identity,
        mode=WmsOperationMode.QUERY,
        request_model=request_model,
        result_model=result_model,
        http_method=http_method,
        path_template=path_template,
        target_code=target_code,
        execution_lane=WmsExecutionLane.WMS_DATA,
        completion_mode=None,
        side_effect_free=True,
        budget=LIST_QUERY_BUDGET if list_result else SINGLE_QUERY_BUDGET,
        pagination=STANDARD_LIST_PAGINATION if list_result else None,
        error_codes=COMMON_ERROR_CODES,
        reject_codes=reject_codes,
    )


def effect_operation(
    *,
    identity: str,
    request_model: type[BaseModel],
    result_model: type[BaseModel],
    path_template: str,
    target_code: str,
    reject_codes: tuple[str, ...],
    completion_mode: WmsCompletionMode,
    execution_lane: WmsExecutionLane,
    terminal_identity_validator: TerminalIdentityValidator | None = None,
    domain_projection_kind: WmsDomainProjectionKind | None = None,
) -> WmsOperationDefinition:
    """构造一项静态 EFFECT Definition。"""

    return WmsOperationDefinition(
        identity=identity,
        mode=WmsOperationMode.EFFECT,
        request_model=request_model,
        result_model=result_model,
        http_method=WmsHttpMethod.POST,
        path_template=path_template,
        target_code=target_code,
        execution_lane=execution_lane,
        completion_mode=completion_mode,
        side_effect_free=False,
        budget=EFFECT_BUDGET,
        pagination=None,
        error_codes=EFFECT_ERROR_CODES,
        reject_codes=reject_codes,
        terminal_identity_validator=terminal_identity_validator,
        domain_projection_kind=domain_projection_kind,
    )


__all__ = [
    "COMMON_ERROR_CODES",
    "EFFECT_ERROR_CODES",
    "WmsCompletionMode",
    "WmsDomainProjectionKind",
    "WmsExecutionLane",
    "WmsHttpMethod",
    "WmsOperationBudget",
    "WmsOperationDefinition",
    "WmsOperationMode",
    "WmsPaginationConstraint",
    "effect_operation",
    "query_operation",
]
