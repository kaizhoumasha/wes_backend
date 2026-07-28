"""WMS conformance 的构建期 manifest；不进入生产运行时 profile。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILE
from src.app.wms_integration.operation_contract import (  # noqa: TC001 - Pydantic 运行时解析字段类型。
    WmsOperationDefinition,
)
from src.app.wms_integration.provider_manifest import WMS_CONFORMANCE_REQUIREMENTS


class OperationConformanceRequirement(BaseModel):
    """单 operation 必须通过的统一试卷。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: WmsOperationDefinition
    required_cases: tuple[str, ...] = Field(min_length=1)


class WmsConformanceManifest(BaseModel):
    """仅供 CI、simulator、replay 与双方联调使用的构建期清单。"""

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


WMS_CONFORMANCE_MANIFEST = WmsConformanceManifest(
    profile_identity=WMS_PROVIDER_PROFILE.identity.identity,
    fixture_root="tests/fixtures/external_contracts/wms/northbound",
    operations=tuple(
        OperationConformanceRequirement(
            operation=requirement.operation,
            required_cases=requirement.required_cases,
        )
        for requirement in WMS_CONFORMANCE_REQUIREMENTS
    ),
)

__all__ = ["WMS_CONFORMANCE_MANIFEST", "OperationConformanceRequirement", "WmsConformanceManifest"]
