"""ExternalContractProfile 生产路径 — @yagni: 全量联调前为占位合同。

当前状态: 外部 provider 共用严格合同模型；
generic provider 保留环境隔离，WMS 部署合同使用无环境维度的窄 profile。
当前里程碑的粗分机/分拣机流程不需要动态合同切换能力。

激活条件: 多 provider 并行联调或合同版本差异需要运行时切换。

从 tests/support/external_contract_profile.py 升级到 src/app/contracts/
共享层, 供 wms_integration / device / runtime/orchestration 域 import 使用。

设计理由 (AP1 ADR-0009):
- 共享 contract 层避免 capability implementation import boundary 误报。
  如果 tests/support/ 升级到 src/app/wms_integration/models/, 会让
  wms_integration 内部域反向被 runtime 引用, 触发 WMS integration 和
  capability implementation import boundaries。
- 三个 typed DTO 共享同一包, 避免 InboundNormalizerProfile 与 RuntimeCapabilityProfile
  分散在不同域导致 capability dependency type guard 难以统一维护
- generic production 入站 profile 必须声明实际认证材料
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

Direction = Literal["event", "result"]
Environment = Literal["sandbox", "staging", "production"]


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


class _ExternalContractProfileBase(BaseModel):
    """外部 provider 合同的共享字段与 normalizer admission。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_code: str = Field(min_length=1, max_length=60, description="稳定 provider ID")
    contract_version: str = Field(min_length=1, max_length=60, description="合同版本")
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


class ExternalContractProfile(_ExternalContractProfileBase):
    """generic provider 合同，环境隔离属于稳定身份。"""

    environment: Environment

    @model_validator(mode="after")
    def _wms_requires_narrow_profile_after(self) -> ExternalContractProfile:
        """WMS 合同必须使用无 environment 维度的窄 profile。"""

        if self.provider_code.strip().lower() == "wms":
            raise ValueError("WMS provider 必须使用 WmsExternalContractProfile")
        return self

    @property
    def identity(self) -> str:
        return f"{self.provider_code.strip().lower()}.{self.contract_version}.{self.environment}"

    @model_validator(mode="after")
    def _production_security_required_after(self) -> ExternalContractProfile:
        """production 入站 profile 必须固定密钥标识和签名算法。"""

        has_inbound_callbacks = bool(self.inbound_normalizers_event or self.inbound_normalizers_result)
        if self.environment != "production" or not has_inbound_callbacks:
            return self
        security = self.security_profile
        if not (security.secret_kid or "").strip() or security.signature_algo is None:
            raise ValueError(
                "production external contract profile 的 security_profile 必须固定 secret_kid 和 signature_algo"
            )
        return self


class WmsExternalContractProfile(_ExternalContractProfileBase):
    """WMS 单工厂部署合同，身份仅由 provider 与合同版本组成。"""

    provider_code: Literal["WMS"]

    @property
    def identity(self) -> str:
        return f"wms.{self.contract_version}"


type ExternalContractProfileDefinition = ExternalContractProfile | WmsExternalContractProfile
_EXTERNAL_CONTRACT_PROFILE_ADAPTER = TypeAdapter(ExternalContractProfileDefinition)


def parse_external_contract_profile(raw_profile: Any) -> ExternalContractProfileDefinition:
    """按 provider closed union 收敛通用与 WMS 外部合同。"""

    return _EXTERNAL_CONTRACT_PROFILE_ADAPTER.validate_python(raw_profile)


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
        description="只允许当前共享 query Protocol 引用, e.g. WmsQueryExecutionPort",
    )
    effect_ports: list[str] = Field(
        default_factory=list,
        description="只允许当前出站合同引用，不允许已删除的粗粒度 fulfillment port",
    )
    forbidden_injection_types: list[str] = Field(
        default_factory=list,
        description="显式禁止注入的 inbound normalizer 类型, e.g. WmsEventPort",
    )


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
    direction: Literal["query", "effect", "event", "result"]
    raw_request: dict | None = None
    raw_response: dict | None = None
    raw_callback: dict | None = None
    expected_typed: dict = Field(default_factory=dict)
    expected_error: dict | None = None


__all__ = [
    "Environment",
    "ExternalContractProfile",
    "ExternalContractProfileDefinition",
    "FixtureCase",
    "FixtureSet",
    "InboundNormalizerProfile",
    "RuntimeCapabilityProfile",
    "SecurityProfile",
    "WmsExternalContractProfile",
    "parse_external_contract_profile",
]
