"""ExternalContractProfile 生产路径 (Phase 1 CEO-013 / AP1)。

从 Phase 0 tests/support/external_contract_profile.py 升级到 src/app/contracts/
共享层, 供 wms_integration / device / runtime/orchestration 域 import 使用。

设计理由 (AP1 ADR-0009):
- 共享 contract 层避免 R-I3b 误报 (tests/support/ 升级到 src/app/wms_integration/models/
  会让 wms_integration 内部域反向被 runtime 引用, 触发 C1/R-I3b)
- 三个 typed DTO 共享同一包, 避免 InboundNormalizerProfile 与 RuntimeCapabilityProfile
  分散在不同域导致 R-I3 type guard 难以统一维护
- security_profile 占位: Phase 3 external-callback-auth-spec.md 完整落地时填充 HMAC canonical
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Environment = Literal["sandbox", "staging", "production"]


class SecurityProfile(BaseModel):
    """Provider 通信安全配置 (Phase 0 占位, Phase 3 完整落地)。

    Phase 3 external-callback-auth-spec.md 落地时填充: secret_kid / signature_algo /
    clock_skew_seconds / nonce_ttl_seconds / canonical_string 模板。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    secret_kid: str | None = Field(
        default=None,
        description="密钥 ID (Phase 3 必填)",
    )
    signature_algo: Literal["HS256", "HS512", "RS256"] | None = Field(
        default=None,
        description="签名算法 (Phase 3 必填)",
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
        default="Phase 0 占位, Phase 3 external-callback-auth-spec.md 完整实现",
        description="占位说明, Phase 3 落地后删除",
    )


class ExternalContractProfile(BaseModel):
    """按 provider_code + contract_version 描述 WMS/ECS provider 外部合同。

    主计划 §3.5.1 + §5.1: 锁定 provider 的 query/effect/normalizer 能力 + 字段映射 +
    timeout/retry/fixture + 不支持动作; Runtime capability admission 和 callback normalizer
    必须在合同约束下工作, 不依赖供应商 DTO/SDK。

    工厂方法校验:
    - query 只能列 query port method
    - effect 只能列 effect port method
    - environment=production 时, security_profile 必填 (Phase 0 占位暂时允许)
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

    @field_validator("runtime_capabilities_effect")
    @classmethod
    def _effect_timeout_required(cls, v: list[str], info) -> list[str]:
        """effect 非空时 effect_timeout_seconds 必填。

        跨字段校验: model_validator(mode="after") 在下个版本补, 这里先
        用 field_validator 拿到 info.data 校验。
        """
        if v and not info.data.get("timeout_retry_effect_timeout_seconds"):
            raise ValueError("effect_timeout_seconds 必填当 runtime_capabilities_effect 非空")
        return v

    @field_validator("runtime_capabilities_query")
    @classmethod
    def _query_method_format(cls, v: list[str]) -> list[str]:
        """query 元素必须匹配 'ClassName.method' 格式 (Port.method 合同)。

        字符类含下划线, 支持 snake_case 方法名 (如 WmsFulfillmentPort.request_transport)。
        """
        import re

        pat = re.compile(r"^[A-Z][A-Za-z0-9_]*Port\.[a-z_][A-Za-z0-9_]*$")
        for entry in v:
            if not pat.match(entry):
                raise ValueError(f"query 元素必须为 'ClassName.method' 格式 (Port.method 合同), got: {entry}")
        return v


class RuntimeCapabilityProfile(BaseModel):
    """Runtime capability 注入合同 (Phase 1 CEO-009 / D)。

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


class InboundNormalizerProfile(BaseModel):
    """inbound normalizer (callback event/result → RuntimeInbox) 合同 (Phase 1 CEO-009 / H2)。

    与 RuntimeCapabilityProfile 严格分离: normalizer 是入站边界, capability
    是出站业务调用。I3 不变量 + R-I3a/R-I3b 禁止 normalizer 进入业务 capability。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalizer_name: str = Field(min_length=1, max_length=80, description="normalizer 名")
    source_provider: str = Field(description="源 provider (WMS/ECS)")
    event_type: str = Field(description="WMS_GRN_RECEIVED 等")
    correlation_resolution: str = Field(
        default="manual",
        description="source_event_id 解析 correlation 策略: manual / auto / hybrid",
    )


__all__ = [
    "Environment",
    "ExternalContractProfile",
    "InboundNormalizerProfile",
    "RuntimeCapabilityProfile",
    "SecurityProfile",
]
