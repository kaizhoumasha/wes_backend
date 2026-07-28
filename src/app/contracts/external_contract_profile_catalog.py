"""外部合同 profile 的确定性只读目录。"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from src.app.contracts.external_contract_profile import ExternalContractProfile, SecurityProfile

if TYPE_CHECKING:
    from collections.abc import Iterable


class ExternalContractProfileCatalog:
    """按 provider/version/environment 精确解析并快照外部合同。"""

    def __init__(self, profiles: Iterable[ExternalContractProfile]) -> None:
        catalog: dict[str, ExternalContractProfile] = {}
        for profile in profiles:
            identity = _canonical_profile_identity(profile)
            if identity in catalog:
                raise ValueError(f"重复 external contract profile identity: {identity}")
            catalog[identity] = profile
        self._profiles = MappingProxyType(catalog)

    def resolve(
        self,
        *,
        provider_code: str,
        environment: str,
        contract_version: str | None = None,
    ) -> ExternalContractProfile:
        canonical_provider = _canonical_provider_code(provider_code)
        matches = [
            profile
            for profile in self._profiles.values()
            if _canonical_provider_code(profile.provider_code) == canonical_provider
            and profile.environment == environment
            and (contract_version is None or profile.contract_version == contract_version)
        ]
        if len(matches) != 1:
            raise LookupError(
                f"provider profile 必须唯一: provider={provider_code}, version={contract_version}, environment={environment}"
            )
        return matches[0]

    def resolve_identity(self, identity: str) -> ExternalContractProfile:
        """按不可变 profile identity 精确解析，供 Definition admission 校验。"""

        requested_identity = identity.strip()
        matches = []
        for profile in self._profiles.values():
            exact_suffix = f".{profile.contract_version}.{profile.environment}"
            if not requested_identity.endswith(exact_suffix):
                continue
            requested_provider = requested_identity[: -len(exact_suffix)]
            if _canonical_provider_code(requested_provider) == _canonical_provider_code(profile.provider_code):
                matches.append(profile)
        if len(matches) != 1:
            raise LookupError(f"provider profile identity 必须唯一: {identity}")
        return matches[0]


def _canonical_provider_code(provider_code: str) -> str:
    """Provider identity 仅规范首尾空白与大小写；version/environment 保持精确。"""

    return provider_code.strip().lower()


def _canonical_profile_identity(profile: ExternalContractProfile) -> str:
    return f"{_canonical_provider_code(profile.provider_code)}.{profile.contract_version}.{profile.environment}"


WMS_MATERIAL_FLOW_SANDBOX_PROFILE = ExternalContractProfile(
    provider_code="WMS",
    contract_version="2026-07-06.material-flow",
    environment="sandbox",
    inbound_normalizers_event=[
        "WMS_GRN_RECEIVED",
        "WMS_PALLET_ARRIVED",
        "WMS_INVENTORY_UPDATED",
        "WMS_PDA_OPERATION_RECORDED",
    ],
    inbound_normalizers_result=["WMS_EFFECT_STATUS_HINT"],
    timeout_retry_query_timeout_seconds=10,
    timeout_retry_effect_timeout_seconds=30,
    timeout_retry_retry_backoff_seconds=[1, 2, 4],
    fixture_set_path="tests/fixtures/external_contracts/wms/default",
    fixture_set_required_cases=["success"],
)

WMS_MATERIAL_FLOW_STAGING_PROFILE = ExternalContractProfile.model_validate(
    {**WMS_MATERIAL_FLOW_SANDBOX_PROFILE.model_dump(mode="python"), "environment": "staging"}
)
WMS_MATERIAL_FLOW_PRODUCTION_KID = "wms-material-flow-production"
WMS_MATERIAL_FLOW_PRODUCTION_PROFILE = ExternalContractProfile(
    provider_code="WMS",
    contract_version="2026-07-06.material-flow",
    environment="production",
    inbound_normalizers_event=[
        "WMS_GRN_RECEIVED",
        "WMS_PALLET_ARRIVED",
        "WMS_INVENTORY_UPDATED",
        "WMS_PDA_OPERATION_RECORDED",
    ],
    inbound_normalizers_result=["WMS_EFFECT_STATUS_HINT"],
    timeout_retry_query_timeout_seconds=10,
    timeout_retry_effect_timeout_seconds=30,
    timeout_retry_retry_backoff_seconds=[1, 2, 4],
    fixture_set_path="tests/fixtures/external_contracts/wms/default",
    fixture_set_required_cases=["success"],
    security_profile=SecurityProfile(
        secret_kid=WMS_MATERIAL_FLOW_PRODUCTION_KID,
        signature_algo="HS256",
        placeholder_notes="生产 profile 仅持久化密钥标识；密钥材料由部署环境的 secret provider 解析",
    ),
)

external_contract_profile_catalog = ExternalContractProfileCatalog(
    (
        WMS_MATERIAL_FLOW_SANDBOX_PROFILE,
        WMS_MATERIAL_FLOW_STAGING_PROFILE,
        WMS_MATERIAL_FLOW_PRODUCTION_PROFILE,
    )
)


def list_external_contract_profiles() -> tuple[ExternalContractProfile, ...]:
    return tuple(external_contract_profile_catalog._profiles.values())


__all__ = [
    "WMS_MATERIAL_FLOW_PRODUCTION_KID",
    "WMS_MATERIAL_FLOW_PRODUCTION_PROFILE",
    "WMS_MATERIAL_FLOW_SANDBOX_PROFILE",
    "WMS_MATERIAL_FLOW_STAGING_PROFILE",
    "ExternalContractProfileCatalog",
    "external_contract_profile_catalog",
    "list_external_contract_profiles",
]
