"""系统能力封闭 outcome 合同。"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, TypeAdapter, ValidationError

StableCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Success[T](BaseModel):
    """带类型 payload 的成功结果。"""

    model_config = ConfigDict(frozen=True)

    kind: Literal["success"] = "success"
    payload: T


class BusinessReject(BaseModel):
    """不应通过异常表达的稳定业务拒绝。"""

    model_config = ConfigDict(frozen=True)

    kind: Literal["business_reject"] = "business_reject"
    reason_code: StableCode
    message: StableCode
    retryable: Literal[False] = False
    details: dict[str, JsonValue] = Field(default_factory=dict)


class RetryableFailure(BaseModel):
    """允许 runtime 重试的技术失败。"""

    model_config = ConfigDict(frozen=True)

    kind: Literal["retryable_failure"] = "retryable_failure"
    error_code: StableCode
    message: StableCode
    retryable: Literal[True] = True
    retry_after_seconds: float | None = Field(default=None, gt=0)
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ContractViolation(BaseModel):
    """未知或不合法 outcome 的封闭边界结果。"""

    model_config = ConfigDict(frozen=True)

    kind: Literal["contract_violation"] = "contract_violation"
    error_code: StableCode
    message: StableCode
    retryable: Literal[False] = False
    details: dict[str, JsonValue] = Field(default_factory=dict)


def parse_outcome[T](
    raw: str | bytes | dict[str, Any], *, payload_type: type[T]
) -> Success[T] | BusinessReject | RetryableFailure | ContractViolation:
    """解析封闭 outcome；未知第五种类型和非法合同统一映射为 ContractViolation。"""

    try:
        data = json.loads(raw) if isinstance(raw, str | bytes) else raw
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ContractViolation(error_code="INVALID_OUTCOME_JSON", message=str(exc))
    if not isinstance(data, dict):
        return ContractViolation(error_code="INVALID_OUTCOME_CONTRACT", message="outcome must be an object")
    kind = data.get("kind")
    if not isinstance(kind, str):
        return ContractViolation(error_code="INVALID_OUTCOME_CONTRACT", message="outcome kind must be a string")
    if kind not in {"success", "business_reject", "retryable_failure", "contract_violation"}:
        return ContractViolation(
            error_code="UNKNOWN_OUTCOME_KIND",
            message="outcome kind is not part of the closed contract",
            details={"received_kind": kind},
        )

    outcome_type = Annotated[
        Success[payload_type] | BusinessReject | RetryableFailure | ContractViolation,
        Field(discriminator="kind"),
    ]
    try:
        return TypeAdapter(outcome_type).validate_python(data)
    except ValidationError as exc:
        validation_errors = [
            {
                "type": str(error["type"]),
                "loc": list(error["loc"]),
                "message": str(error["msg"]),
            }
            for error in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        return ContractViolation(
            error_code="INVALID_OUTCOME_CONTRACT",
            message="outcome does not satisfy its declared contract",
            details={"validation_errors": validation_errors},
        )


__all__ = ["BusinessReject", "ContractViolation", "RetryableFailure", "Success", "parse_outcome"]
