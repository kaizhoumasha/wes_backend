"""单次访问的纯技术观察，由宿主显式拥有；不依赖诊断存储。"""

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError

from src.utils.timezone import timezone


@dataclass(slots=True)
class WmsCallObservation:
    direction: Literal["WMS_TO_WES", "WES_TO_WMS"]
    operation: str | None = None
    operation_id: str | None = None
    attempt_id: str = field(default_factory=lambda: uuid4().hex)
    observed_at: str = field(default_factory=lambda: timezone.now_utc().isoformat())
    business_reference: str | None = None
    method: str = "POST"
    path: str | None = None
    request_body: bytes | None = field(default=None, repr=False)
    response_body: bytes | None = field(default=None, repr=False)
    request_headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    response_headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    request_source: Literal["WIRE", "FROZEN_PAYLOAD", "NOT_CAPTURED"] = "NOT_CAPTURED"
    status_code: int | None = None
    elapsed_ms: float | None = None
    result: str = "NOT_OBSERVED"
    error_code: str | None = None
    request_schema: dict[str, Any] | None = field(default=None, repr=False)
    response_schema: dict[str, Any] | None = field(default=None, repr=False)
    request_contract: Any = field(default=None, repr=False)
    response_contract: Any = field(default=None, repr=False)
    request_errors: tuple[dict[str, Any], ...] = field(default=(), repr=False)
    response_errors: tuple[dict[str, Any], ...] = field(default=(), repr=False)
    request_validated: bool = False
    response_validated: bool = False
    contract_source: str = "unknown"
    incomplete: bool = False
    finished: bool = False


def capture(observation: WmsCallObservation | None, **facts: Any) -> None:
    """仅隔离新增观察写入；不拦截原 HTTP/校验异常。"""
    if observation is None:
        return
    try:
        for name, value in facts.items():
            setattr(observation, name, value)
    except Exception:
        observation.incomplete = True


def observed_contract_error(
    observation: WmsCallObservation | None,
    message: str,
    *,
    path: tuple[str | int, ...] = (),
    expected_value: object = None,
) -> ValueError:
    """记录原响应校验分支并返回同类异常；不重新执行任何合同规则。"""
    capture(
        observation,
        response_validated=False,
        response_errors=({"loc": path, "type": "contract_error", "msg": message, "expected_value": expected_value},),
    )
    return ValueError(message)


def validate_observed[T: BaseModel](
    contract: type[T] | TypeAdapter[T],
    value: object,
    *,
    observation: WmsCallObservation | None,
    side: Literal["request", "response"],
) -> T:
    """在原校验调用处保留合同引用与错误；schema 展示延后到诊断预算内。"""
    capture(observation, **{f"{side}_contract": contract})
    try:
        result = (
            contract.validate_python(value) if isinstance(contract, TypeAdapter) else contract.model_validate(value)
        )
    except ValidationError as error:
        if observation is not None:
            try:
                capture(
                    observation,
                    **{
                        f"{side}_errors": tuple(
                            error.errors(include_input=False, include_context=False, include_url=False)[:256]
                        )
                    },
                )
            except Exception:
                observation.incomplete = True
        raise
    capture(observation, **{f"{side}_validated": True, f"{side}_contract": type(result)})
    return result
