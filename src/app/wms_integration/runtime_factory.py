"""WMS EFFECT status runtime composition helper。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.sys.external_http_credentials import AuditedVersionedCredentialProvider
from src.app.sys.external_http_credentials import (
    build_environment_external_http_credential_provider as build_environment_credential_provider,
)
from src.app.wms_integration.adapters import WmsEffectStatusQueryAdapter
from src.app.wms_integration.query_evidence import (
    WmsEffectStatusCallEvidenceWriter,
    WmsEffectStatusEvidenceWriter,
)
from src.app.wms_integration.services.circuit_breaker_service import wms_circuit_breaker_service
from src.app.wms_integration.services.evidence_service import wms_call_evidence_service
from src.core.conf import settings
from src.database.db import get_db_context

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from src.app.sys.external_http_credentials import VersionedCredentialProvider
    from src.app.wms_integration.ports.effect_status import FrozenWmsEffectStatusBinding, WmsEffectStatusQueryPort


def build_effect_status_query_port_factory(
    *,
    binding: FrozenWmsEffectStatusBinding,
    credential_provider: VersionedCredentialProvider | None = None,
    evidence_writer: WmsEffectStatusEvidenceWriter | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
    settings_source: Any | None = None,
) -> Callable[[], WmsEffectStatusQueryPort]:
    """仅从 Intent 冻结 binding 装配状态查询 adapter，不回查 active endpoint。"""

    active_settings = settings if settings_source is None else settings_source
    resolved_credential_provider = credential_provider or build_environment_credential_provider(
        settings_source=active_settings
    )
    if not isinstance(resolved_credential_provider, AuditedVersionedCredentialProvider):
        resolved_credential_provider = AuditedVersionedCredentialProvider(
            resolved_credential_provider,
            provider_kind="custom",
        )
    writer = evidence_writer or WmsEffectStatusCallEvidenceWriter(
        session_factory=get_db_context,
        provider_profile_identity=binding.provider_profile_identity,
        evidence_service=wms_call_evidence_service,
        breaker_service=wms_circuit_breaker_service,
    )

    def factory() -> WmsEffectStatusQueryPort:
        return WmsEffectStatusQueryAdapter(
            binding=binding,
            credential_provider=resolved_credential_provider,
            evidence_writer=writer,
            transport=transport,
            initial_backoff_seconds=initial_backoff_seconds,
            max_backoff_seconds=max_backoff_seconds,
        )

    return factory


__all__ = ["build_effect_status_query_port_factory"]
