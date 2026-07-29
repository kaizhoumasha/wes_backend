"""Station Outbox 创建边界必须完整保存共享 EXTERNAL_HTTP binding。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.runtime.capabilities.material_flow.station_lease_service import (
    StationLeaseResult,
    WorklineStationLeaseService,
)
from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportResult,
)
from src.app.sys.models import DispatchEnvelope, SystemOutboxDispatchType, SystemOutboxTargetType
from src.app.sys.services.outbox_delivery import dispatch_external_http
from tests.support.external_http import frozen_external_http_binding


class _RecordingDb:
    def __init__(self) -> None:
        self.added = []

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_station_outbox_persists_none_network_trust_for_restart_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WorklineStationLeaseService(
        rack_position_service=SimpleNamespace(),
        rack_placement_repository=SimpleNamespace(),
        outbox_repository=SimpleNamespace(),
        session_repository=SimpleNamespace(),
    )

    async def available_status(*_args, **_kwargs) -> StationLeaseResult:
        return StationLeaseResult(workline_code="LINE-1", position_code="STATION-1", available=True)

    monkeypatch.setattr(service, "_build_station_lease_status", available_status)
    projection = {
        "request_id": "REQ-NONE-RESTART",
        "workline_code": "LINE-1",
        "position_code": "STATION-1",
        "station": {"workline_code": "LINE-1", "position_code": "STATION-1"},
    }
    canonical = CanonicalPayload.from_projection(projection)
    binding = frozen_external_http_binding(
        target_url="http://factory-wms/effect",
        operation_identity="wms.inventory.confirm_inbound@v1",
        auth_scheme="NONE",
        network_trust_mode="isolated_lan",
        credential_reference=None,
    )
    envelope = DispatchEnvelope(
        dispatch_key="station:none:restart",
        idempotency_key="intent:none:restart",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code=binding.target_snapshot.code,
        provider_profile_identity=binding.provider_profile_identity,
        operation_identity=binding.operation_identity,
        payload_json=projection,
        operation_domain="WMS_INVENTORY",
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        frozen_binding=binding,
    )
    db = _RecordingDb()

    persisted = await service.claim_station_dispatch_lease(
        db,
        workline_id=1,
        workline_code="LINE-1",
        position_code="STATION-1",
        envelope=envelope,
    )

    assert persisted is db.added[0]
    assert persisted.network_trust_mode == "isolated_lan"
    assert persisted.auth_scheme == "NONE"
    assert persisted.credential_reference is None
    assert persisted.idempotency_key == "intent:none:restart"

    # 模拟 profile 已切到另一 endpoint；重启恢复仍只能读取已持久化 binding。
    rotated = frozen_external_http_binding(
        target_url="http://factory-wms-v2/effect",
        operation_identity=binding.operation_identity,
        auth_scheme="NONE",
        network_trust_mode="isolated_lan",
        credential_reference=None,
    )
    assert rotated.target_snapshot_hash != persisted.target_snapshot_hash

    from src.app.runtime.system_capabilities.wms import provider_catalog

    def live_profile_must_not_be_read(*_args, **_kwargs):
        raise AssertionError("restart recovery must not read live compiled profile")

    monkeypatch.setattr(provider_catalog, "build_wms_provider_catalog", live_profile_must_not_be_read)

    class CredentialProviderMustNotBeRead:
        def resolve(self, _credential_reference: str) -> bytes:
            raise AssertionError("NONE restart recovery must not resolve credentials")

    requests = []

    async def sender(request):
        requests.append(request)
        return ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )

    result = await dispatch_external_http(persisted, CredentialProviderMustNotBeRead(), sender)

    assert result.outcome is ExternalHttpTransportOutcome.ACCEPTED
    assert requests[0].endpoint.url == "http://factory-wms/effect"
