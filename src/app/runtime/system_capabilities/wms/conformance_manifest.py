"""WMS conformance 的构建期 manifest；不进入生产运行时 profile。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.app.runtime.system_capabilities.wms.contracts import (  # noqa: TC001 - Pydantic 运行时解析字段类型。
    WmsOperationContract,
)
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILE
from src.app.runtime.system_capabilities.wms.provider_conformance import QUERY_INVENTORY_CONFORMANCE_CASES


class OperationConformanceRequirement(BaseModel):
    """单 operation 必须通过的统一试卷。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: WmsOperationContract
    required_cases: tuple[str, ...] = Field(min_length=1)


class WmsConformanceManifest(BaseModel):
    """仅供 CI/simulator/staging conformance 使用的构建期清单。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_identity: str = Field(min_length=1)
    fixture_root: str = Field(min_length=1)
    operations: tuple[OperationConformanceRequirement, ...]

    @model_validator(mode="after")
    def require_unique_operations(self) -> WmsConformanceManifest:
        identities = tuple(item.operation.identity for item in self.operations)
        if len(identities) != len(set(identities)):
            raise ValueError("conformance manifest contains duplicate operation identity")
        return self


_CORE_QUERY_CASES = tuple(case.case_id for case in QUERY_INVENTORY_CONFORMANCE_CASES)
_CORE_EFFECT_CASES = (
    "success",
    "reject",
    "timeout",
    "unavailable",
    "malformed",
    "idempotency",
    "status_query",
    "callback_timing",
)

WMS_CONFORMANCE_MANIFEST = WmsConformanceManifest(
    profile_identity=WMS_PROVIDER_PROFILE.identity.identity,
    fixture_root="tests/fixtures/external_contracts/wms/northbound",
    operations=tuple(
        OperationConformanceRequirement(
            operation=binding.operation,
            required_cases=_CORE_QUERY_CASES if binding.operation.mode.value == "QUERY" else _CORE_EFFECT_CASES,
        )
        for binding in WMS_PROVIDER_PROFILE.bindings
    ),
)

__all__ = ["WMS_CONFORMANCE_MANIFEST", "OperationConformanceRequirement", "WmsConformanceManifest"]
