"""ExternalContractProfile 生产路径 — @yagni: 全量联调前为占位合同。

当前状态: 被 capability_dispatcher 和 runtime_capability_catalog 引用，
但所有 WMS/ECS provider 使用默认 profile。当前里程碑的粗分机/分拣机流程
不需要动态合同切换能力。

激活条件: 多 provider 并行联调或 WMS/ECS 合同版本差异需要运行时切换。

从 tests/support/external_contract_profile.py 升级到 src/app/contracts/
共享层, 供 wms_integration / device / runtime/orchestration 域 import 使用。

设计理由 (AP1 ADR-0009):
- 共享 contract 层避免 capability implementation import boundary 误报。
  如果 tests/support/ 升级到 src/app/wms_integration/models/, 会让
  wms_integration 内部域反向被 runtime 引用, 触发 WMS integration 和
  capability implementation import boundaries。
- 三个 typed DTO 共享同一包, 避免 InboundNormalizerProfile 与 RuntimeCapabilityProfile
  分散在不同域导致 capability dependency type guard 难以统一维护
- security_profile 占位: external-callback-auth-spec.md 完整落地时填充 HMAC canonical
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Environment = Literal["sandbox", "staging", "production"]
Direction = Literal["query", "effect", "event", "result"]
PORT_METHOD_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*Port\.[a-z_][A-Za-z0-9_]*$")


class SecurityProfile(BaseModel):
    """Provider 通信安全配置。

    external-callback-auth-spec.md 落地时填充: secret_kid / signature_algo /
    clock_skew_seconds / nonce_ttl_seconds / canonical_string 模板。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    secret_kid: str | None = Field(
        default=None,
        description="密钥 ID（生产认证必填）",
    )
    signature_algo: Literal["HS256", "HS512", "RS256"] | None = Field(
        default=None,
        description="签名算法（生产认证必填）",
    )
    clock_skew_seconds: int = Field(
        default=30,
        ge=0,
        le=300,
        description="时钟偏差容忍窗口 (主计划 §5.3: 30s)",
    )
    nonce_ttl_seconds: int = Field(
        default=300,
        ge=60,
        description="nonce TTL (主计划 §5.3: 5 分钟)",
    )
    placeholder_notes: str = Field(
        default="安全 profile 占位, external-callback-auth-spec.md 完整实现后删除",
        description="占位说明, 外部 callback 签名落地后删除",
    )


class ExternalContractProfile(BaseModel):
    """按 provider_code + contract_version 描述 WMS/ECS provider 外部合同。

    主计划 §3.5.1 + §5.1: 锁定 provider 的 query/effect/normalizer 能力 + 字段映射 +
    timeout/retry/fixture + 不支持动作; Runtime capability admission 和 callback normalizer
    必须在合同约束下工作, 不依赖供应商 DTO/SDK。

    工厂方法校验:
    - query 只能列 query port method
    - effect 只能列 effect port method
    - environment=production 时, security_profile 必填 (当前占位暂时允许)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_code: str = Field(min_length=1, max_length=60, description="稳定 provider ID")
    contract_version: str = Field(min_length=1, max_length=60, description="合同版本")
    environment: Environment
    runtime_capabilities_query: list[str] = Field(
        min_length=1,
        description="query port method, e.g. WmsMasterDataPort.get_material",
    )
    runtime_capabilities_effect: list[str] = Field(
        default_factory=list,
        description="effect port method, e.g. WmsFulfillmentPort.request_transport",
    )
    inbound_normalizers_event: list[str] = Field(
        default_factory=list,
        description="provider 允许的 event type, e.g. WMS_GRN_RECEIVED",
    )
    inbound_normalizers_result: list[str] = Field(
        default_factory=list,
        description="provider 允许的 result/callback type",
    )
    field_mapping: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description="event/result 到 typed envelope 字段的映射",
    )
    timeout_retry_query_timeout_seconds: int = Field(ge=1, description="query 超时")
    timeout_retry_effect_timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description="effect 超时 (effect 非空时必填)",
    )
    timeout_retry_retry_backoff_seconds: list[int] = Field(
        min_length=1,
        description="递增短退避数组",
    )
    cache_ttl_seconds: int = Field(default=30, ge=0, description="query cache TTL; 0=禁用")
    fixture_set_path: str = Field(
        min_length=1,
        description="tests/fixtures/external_contracts/<provider>/<profile>",
    )
    fixture_set_required_cases: list[str] = Field(
        min_length=1,
        description="至少覆盖 success/reject/timeout/duplicate/missing_event_id",
    )
    unsupported_actions: list[str] = Field(
        default_factory=list,
        description="未支持动作, e.g. direct_rcs_dispatch",
    )
    security_profile: SecurityProfile = Field(default_factory=SecurityProfile)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _effect_timeout_required_after(self) -> ExternalContractProfile:
        """effect 非空时 effect_timeout_seconds 必填 (model_validator 访问完整实例)。"""
        if self.runtime_capabilities_effect and self.timeout_retry_effect_timeout_seconds is None:
            raise ValueError("effect_timeout_seconds 必填当 runtime_capabilities_effect 非空")
        return self

    @field_validator("runtime_capabilities_query")
    @classmethod
    def _query_method_format(cls, v: list[str]) -> list[str]:
        """query 元素必须匹配 'ClassName.method' 格式 (Port.method 合同)。

        字符类含下划线, 支持 snake_case 方法名 (如 WmsFulfillmentPort.request_transport)。
        """
        return _validate_port_method_entries(v, direction="query")

    @field_validator("runtime_capabilities_effect")
    @classmethod
    def _effect_method_format(cls, v: list[str]) -> list[str]:
        """effect 元素必须匹配 'ClassName.method' 格式 (Port.method 合同)。"""

        return _validate_port_method_entries(v, direction="effect")

    def ensure_runtime_capability_declared(self, capability: str, *, direction: Literal["query", "effect"]) -> None:
        """校验 provider profile 已声明指定 query/effect capability。"""

        declared = self.runtime_capabilities_query if direction == "query" else self.runtime_capabilities_effect
        if capability in declared:
            return
        raise PermissionError(f"provider={self.provider_code} 未声明 {direction} capability: {capability}")

    def ensure_inbound_normalizer_declared(
        self,
        callback_type: str,
        *,
        direction: Literal["event", "result"],
    ) -> None:
        """校验 provider profile 已声明指定 callback/event/result normalizer。"""

        declared = self.inbound_normalizers_event if direction == "event" else self.inbound_normalizers_result
        if callback_type in declared:
            return
        raise PermissionError(f"provider={self.provider_code} 未声明 {direction} normalizer: {callback_type}")


class RuntimeCapabilityProfile(BaseModel):
    """Runtime capability 注入合同。

    主计划 §3.5: capability 只能拿到 query/effect port contract;
    InboundEventPort / WmsEventPort / DeviceEventPort / RuntimeInbox consumer
    不在业务 capability 注册表。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_name: str = Field(min_length=1, max_length=80, description="capability 名")
    query_ports: list[str] = Field(
        default_factory=list,
        description="只允许 abstract Protocol 引用, e.g. WmsMasterDataPort",
    )
    effect_ports: list[str] = Field(
        default_factory=list,
        description="只允许 abstract Protocol 引用, e.g. WmsFulfillmentPort",
    )
    forbidden_injection_types: list[str] = Field(
        default_factory=list,
        description="显式禁止注入的 inbound normalizer 类型, e.g. WmsEventPort",
    )


def _validate_port_method_entries(entries: list[str], *, direction: Literal["query", "effect"]) -> list[str]:
    for entry in entries:
        if not PORT_METHOD_RE.match(entry):
            raise ValueError(f"{direction} 元素必须为 'ClassName.method' 格式 (Port.method 合同), got: {entry}")
    return entries


class InboundNormalizerProfile(BaseModel):
    """inbound normalizer (callback event/result → RuntimeInbox) 合同。

    与 RuntimeCapabilityProfile 严格分离: normalizer 是入站边界, capability
    是出站业务调用。I3 不变量 + capability dependency guardrails
    禁止 normalizer 进入业务 capability。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalizer_name: str = Field(min_length=1, max_length=80, description="normalizer 名")
    source_provider: str = Field(description="源 provider (WMS/ECS)")
    event_type: str = Field(description="WMS_GRN_RECEIVED 等")
    correlation_resolution: str = Field(
        default="manual",
        description="source_event_id 解析 correlation 策略: manual / auto / hybrid",
    )

    @model_validator(mode="after")
    def _normalizer_injection_boundary(self) -> InboundNormalizerProfile:
        """inbound normalizer 静态校验。

        主计划 §3.5.1 + H2 黑名单: 拒绝不合规输入, 防止业务 capability 错误
        注入 inbound normalizer
        (capability dependency and inbound normalizer ownership guardrails)。
        """
        valid_prefixes = ("WMS_", "ECS_", "DEVICE_")
        if not any(self.event_type.startswith(p) for p in valid_prefixes):
            raise ValueError(f"event_type 必须以 {valid_prefixes} 之一开头, got: {self.event_type}")
        provider_to_prefix = {"wms": "WMS_", "ecs": "ECS_", "device": "DEVICE_"}
        expected_prefix = provider_to_prefix.get(self.source_provider.lower())
        if expected_prefix is None:
            raise ValueError(f"source_provider 必为 wms/ecs/device 之一, got: {self.source_provider}")
        if not self.event_type.startswith(expected_prefix):
            raise ValueError(
                f"source_provider={self.source_provider} 与 event_type={self.event_type} 前缀不一致, "
                f"应为 {expected_prefix}*"
            )
        valid_resolutions = ("manual", "auto", "hybrid")
        if self.correlation_resolution not in valid_resolutions:
            raise ValueError(
                f"correlation_resolution 必为 {valid_resolutions} 之一, got: {self.correlation_resolution}"
            )
        return self


class FixtureSet(BaseModel):
    """contract tests 与 simulator 使用的 fixture 集声明。

    从 tests/support/external_contract_profile.py 升级到共享层,
    供 wms_integration / device / runtime 域 import (不再是测试专用)。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(
        min_length=1,
        description="tests/fixtures/external_contracts/<provider>/<profile>",
    )
    required_cases: list[str] = Field(
        min_length=1,
        description="至少覆盖 success/reject/timeout/duplicate/missing_event_id",
    )


class FixtureCase(BaseModel):
    """单个 contract test fixture。

    升级到共享层, 供 wms_integration simulator registry
    和 contract tests 共同使用。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    provider_code: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    expected_port: str = Field(
        min_length=1,
        description="Port.method 格式, 如 WmsFulfillmentPort.request_transport",
    )
    direction: Direction  # forward ref, defined below
    raw_request: dict | None = None
    raw_response: dict | None = None
    raw_callback: dict | None = None
    expected_typed: dict = Field(default_factory=dict)
    expected_error: dict | None = None


__all__ = [
    "Environment",
    "ExternalContractProfile",
    "FixtureCase",
    "FixtureSet",
    "InboundNormalizerProfile",
    "RuntimeCapabilityProfile",
    "SecurityProfile",
]
