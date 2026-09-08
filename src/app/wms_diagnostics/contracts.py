"""诊断展示合同；不承载业务权威状态。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FieldComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    side: Literal["request", "response"]
    path: str
    expected_rule: str
    expected_value: Any = None
    actual_present: bool
    actual_value: Any = None
    verdict: Literal["PASS", "ERROR", "NOT_VALIDATED"]
    source: str


class WirePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    body: str | None = None
    headers: list[tuple[str, str]] = Field(default_factory=list, max_length=16)
    state: Literal["CAPTURED", "TRUNCATED", "UNSAFE_JSON", "EMPTY", "NOT_CAPTURED", "NO_RESPONSE"] = "NOT_CAPTURED"
    source: Literal["WIRE", "FROZEN_PAYLOAD", "NOT_CAPTURED"] = "NOT_CAPTURED"


class ExchangeFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str = Field(max_length=64)
    observed_at: str = Field(max_length=40)
    direction: Literal["WMS_TO_WES", "WES_TO_WMS"]
    operation: str | None = Field(default=None, max_length=128)
    operation_id: str | None = Field(default=None, max_length=128)
    business_reference: str | None = Field(default=None, max_length=128)
    method: str = Field(default="POST", max_length=8)
    path: str | None = Field(default=None, max_length=256)
    status_code: int | None = None
    elapsed_ms: float | None = None
    result: str = Field(default="NOT_OBSERVED", max_length=64)
    contract_status: Literal["PASS", "ERROR", "NOT_VALIDATED"] = "NOT_VALIDATED"
    error_code: str | None = Field(default=None, max_length=128)
    incomplete: bool = False


class ExchangeSummary(ExchangeFacts):
    exchange_id: str | None = None


class ExchangeContent(ExchangeFacts):
    request: WirePreview = Field(default_factory=WirePreview)
    response: WirePreview = Field(default_factory=WirePreview)
    comparisons: list[FieldComparison] = Field(default_factory=list, max_length=256)
    build_version: str = Field(default="unknown", max_length=64)


class ExchangeObservation(ExchangeContent):
    exchange_id: str | None = None


class ExchangeDetail(ExchangeContent):
    exchange_id: str


class ExchangeFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    direction: Literal["WMS_TO_WES", "WES_TO_WMS"] | None = None
    operation: str | None = Field(default=None, max_length=128)
    operation_id: str | None = Field(default=None, max_length=128)
    business_reference: str | None = Field(default=None, max_length=128)
    only_errors: bool = False


class ExchangeQuery(ExchangeFilters):
    from_ms: int | None = Field(default=None, ge=0)
    to_ms: int | None = Field(default=None, ge=0)
    cursor: str | None = Field(default=None, max_length=48, pattern=r"^\d+-\d+$")
    page_size: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.from_ms is not None and self.to_ms is not None and self.from_ms > self.to_ms:
            raise ValueError("from_ms must not exceed to_ms")
        return self


class ExchangePage(BaseModel):
    items: list[ExchangeSummary]
    next_cursor: str | None
    scan_incomplete: bool
    retention_hours: int
