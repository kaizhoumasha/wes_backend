"""外部合同 profile Pydantic 校验模型（测试专用）。

只供 fixture 校验与 contract tests import；
禁止 ``src/app/`` 下任何模块 import 本文件。
生产路径已升级到 ``src/app/contracts/external_contract_profile.py``。

字段定义对齐 ``docs/contracts/external-contract-profile.md`` §2 字段表。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Environment = Literal["sandbox", "staging", "production"]
Direction = Literal["query", "effect", "event", "result"]


class RuntimeCapabilities(BaseModel):
    """provider 支持且可注入 ``RuntimeCapabilityContext`` 的 query/effect 能力。"""

    model_config = ConfigDict(extra="forbid")

    query: list[str] = Field(
        min_length=1,
        description="query port method，例如 WmsMasterDataPort.get_material",
    )
    effect: list[str] = Field(
        default_factory=list,
        description="effect port method，例如 WmsFulfillmentPort.request_transport",
    )


class InboundNormalizers(BaseModel):
    """provider 支持的 callback/event/result normalizer 能力。"""

    model_config = ConfigDict(extra="forbid")

    event: list[str] = Field(default_factory=list, description="provider 允许的 event type")
    result: list[str] = Field(default_factory=list, description="provider 允许的 result/callback type")


class TimeoutRetry(BaseModel):
    """provider 级 timeout、retry、backoff 约束。"""

    model_config = ConfigDict(extra="forbid")

    query_timeout_seconds: int = Field(ge=1, description="query 超时，必须大于 0")
    effect_timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description="effect port 存在时必填",
    )
    retry_backoff_seconds: list[int] = Field(min_length=1, description="递增短退避数组")
    cache_ttl_seconds: int | None = Field(
        default=None,
        ge=0,
        description="query cache 存在时必填；0 表示禁用",
    )

    @field_validator("retry_backoff_seconds")
    @classmethod
    def _must_be_increasing(cls, v: list[int]) -> list[int]:
        for i in range(1, len(v)):
            if v[i] < v[i - 1]:
                raise ValueError(f"retry_backoff_seconds 必须递增: {v}")
        return v


class FixtureSet(BaseModel):
    """contract tests 与 simulator 使用的 fixture 集声明。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=1,
        description="tests/fixtures/external_contracts/<provider>/<profile>",
    )
    required_cases: list[str] = Field(
        min_length=1,
        description="至少覆盖 success/reject/timeout/duplicate/missing_event_id 中适用场景",
    )


class ExternalContractProfile(BaseModel):
    """按 provider_code + contract_version 描述 WMS/ECS/RCS provider 的外部合同。

    详见 ``docs/contracts/external-contract-profile.md``。
    """

    model_config = ConfigDict(extra="forbid")

    provider_code: str = Field(min_length=1, max_length=60)
    contract_version: str = Field(min_length=1, max_length=60)
    environment: Environment
    runtime_capabilities: RuntimeCapabilities
    inbound_normalizers: InboundNormalizers
    field_mapping: dict[str, dict] = Field(
        default_factory=dict,
        description="event/result 到 typed envelope 字段的映射",
    )
    timeout_retry: TimeoutRetry
    fixture_set: FixtureSet
    unsupported_actions: list[str] = Field(default_factory=list)
    security_profile: dict | None = Field(
        default=None,
        description="测试 fixture 只占位，不展开 HMAC canonical",
    )
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _effect_timeout_required(self) -> ExternalContractProfile:
        if self.runtime_capabilities.effect and self.timeout_retry.effect_timeout_seconds is None:
            raise ValueError("effect_timeout_seconds 必填当 runtime_capabilities.effect 非空")
        return self


class FixtureCase(BaseModel):
    """单个 contract test fixture。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    provider_code: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    expected_port: str = Field(min_length=1, description="Port.method")
    direction: Direction
    raw_request: dict | None = None
    raw_response: dict | None = None
    raw_callback: dict | None = None
    expected_typed: dict = Field(default_factory=dict)
    expected_error: dict | None = None

    @model_validator(mode="after")
    def _direction_payload(self) -> FixtureCase:
        if self.direction in ("query", "effect") and (self.raw_request is None or self.raw_response is None):
            raise ValueError(f"direction={self.direction} 必填 raw_request 和 raw_response")
        if self.direction in ("event", "result") and self.raw_callback is None:
            raise ValueError(f"direction={self.direction} 必填 raw_callback")
        return self


__all__ = [
    "Direction",
    "Environment",
    "ExternalContractProfile",
    "FixtureCase",
    "FixtureSet",
    "InboundNormalizers",
    "RuntimeCapabilities",
    "TimeoutRetry",
]
