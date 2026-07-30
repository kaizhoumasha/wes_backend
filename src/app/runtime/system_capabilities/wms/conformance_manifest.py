"""WMS conformance 的构建期 manifest；不进入生产运行时 profile。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.app.wms_integration.operation_contract import (  # noqa: TC001 - Pydantic 运行时解析字段类型。
    WmsOperationDefinition,
)
from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from src.app.wms_integration.provider_manifest import (
    WMS_CONFORMANCE_REQUIREMENTS,
    conformance_cases_for_operation,
)

if TYPE_CHECKING:
    from src.app.wms_integration.endpoint_compiler import CompiledWmsProviderProfile


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
        if identities != tuple(operation.identity for operation in WMS_OPERATIONS):
            raise ValueError("conformance manifest must cover the exact 35-operation registry")
        if any(item.required_cases != conformance_cases_for_operation(item.operation) for item in self.operations):
            raise ValueError("conformance manifest operation question bank differs from its mode family")
        return self


def build_wms_conformance_manifest(compiled_profile: CompiledWmsProviderProfile) -> WmsConformanceManifest:
    """从显式 compiled profile 构造 conformance manifest。"""

    return WmsConformanceManifest(
        profile_identity=compiled_profile.profile.profile.identity,
        fixture_root="tests/fixtures/external_contracts/wms/northbound",
        operations=tuple(
            OperationConformanceRequirement(
                operation=requirement.operation,
                required_cases=requirement.required_cases,
            )
            for requirement in WMS_CONFORMANCE_REQUIREMENTS
        ),
    )


__all__ = [
    "OperationConformanceRequirement",
    "WmsConformanceManifest",
    "build_wms_conformance_manifest",
]
