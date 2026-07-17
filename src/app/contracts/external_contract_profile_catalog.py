"""外部合同 profile 的确定性只读目录。"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.app.contracts.external_contract_profile import ExternalContractProfile


class ExternalContractProfileCatalog:
    """按 provider/version/environment 精确解析并快照外部合同。"""

    def __init__(self, profiles: Iterable[ExternalContractProfile]) -> None:
        catalog: dict[tuple[str, str, str], ExternalContractProfile] = {}
        for profile in profiles:
            key = (profile.provider_code, profile.contract_version, profile.environment)
            if key in catalog:
                raise ValueError(f"重复 external contract profile: {key}")
            catalog[key] = profile
        self._profiles = MappingProxyType(catalog)

    def resolve(
        self,
        *,
        provider_code: str,
        environment: str,
        contract_version: str | None = None,
    ) -> ExternalContractProfile:
        matches = [
            profile
            for (code, version, profile_environment), profile in self._profiles.items()
            if code == provider_code
            and profile_environment == environment
            and (contract_version is None or version == contract_version)
        ]
        if len(matches) != 1:
            raise LookupError(
                f"provider profile 必须唯一: provider={provider_code}, version={contract_version}, environment={environment}"
            )
        return matches[0]

    def resolve_identity(self, identity: str) -> ExternalContractProfile:
        """按不可变 profile identity 精确解析，供 Definition admission 校验。"""

        matches = [profile for profile in self._profiles.values() if profile.identity == identity]
        if len(matches) != 1:
            raise LookupError(f"provider profile identity 必须唯一: {identity}")
        return matches[0]

    @staticmethod
    def assert_ports_declared(
        profile: ExternalContractProfile, required_port_types: tuple[type[object], ...]
    ) -> list[str]:
        declared = sorted((*profile.runtime_capabilities_query, *profile.runtime_capabilities_effect))
        required_methods: list[str] = []
        for port_type in required_port_types:
            prefix = f"{port_type.__name__}."
            matches = [entry for entry in declared if entry.startswith(prefix)]
            if not matches:
                raise LookupError(f"provider profile 未声明 Port: {port_type.__name__}")
            required_methods.extend(matches)
        return sorted(set(required_methods))


__all__ = ["ExternalContractProfileCatalog"]
