"""统一出站观察只登记既有传输事实，不复制正文或解释业务结果。"""

import pytest

from src.app.sys.services.api_access_log_observer import observe_outbound_api_access
from src.app.sys.services.api_access_log_service import api_access_log_background_scope, api_access_log_service
from src.core.outbound_http import (
    OutboundHttpDeliveryState,
    OutboundHttpFailureKind,
    OutboundHttpMethod,
    OutboundHttpRequest,
    OutboundHttpResult,
)


@pytest.mark.parametrize("system_id", ["wms", "ecs"])
@pytest.mark.parametrize("status_code", [None, 200, 503])
def test_observer_freezes_metadata_and_preserves_transport_classification(monkeypatch, system_id, status_code):
    entries = []
    monkeypatch.setattr(api_access_log_service, "defer_record", entries.append)
    monkeypatch.setattr("src.app.sys.services.api_access_log_observer.get_request_id", lambda: "existing-request")
    result = OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.NOT_SENT
        if status_code is None
        else OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=status_code,
        failure_kind=OutboundHttpFailureKind.CONNECT_ERROR if status_code is None else None,
        decoded_body=None if status_code is None else b'{"code":"REJECTED"}',
    )
    observe_outbound_api_access(
        system_id=system_id,
        peer_address="https://peer.test",
        request=OutboundHttpRequest(
            method=OutboundHttpMethod.POST, path="/events", query=(("secret", "value"),), body=b"private"
        ),
        result=result,
        duration_ms=17,
    )
    assert len(entries) == 1
    entry = entries[0]
    assert (entry.system_id, entry.direction, entry.method, entry.path) == (system_id, "OUTBOUND", "POST", "/events")
    assert entry.peer_address == "https://peer.test" and entry.request_id == "existing-request"
    assert entry.response_time_ms == 17 and entry.status_code == status_code
    assert entry.delivery_state == result.delivery_state.value
    assert entry.error_code == ("CONNECT_ERROR" if status_code is None else None)
    assert entry.details is None
    assert entry.trace_id is entry.event_id is entry.causation_id is None


async def test_observer_only_registers_until_the_existing_host_executes_background_tasks(monkeypatch):
    stored = []

    async def record(value):
        stored.append(value)

    monkeypatch.setattr(api_access_log_service, "record", record)
    with api_access_log_background_scope() as tasks:
        observe_outbound_api_access(
            system_id="ecs",
            peer_address="https://peer.test",
            request=OutboundHttpRequest(method=OutboundHttpMethod.GET, path="/health"),
            result=OutboundHttpResult(
                delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED, status_code=200, decoded_body=b"private"
            ),
            duration_ms=2,
        )
        assert stored == [] and len(tasks.tasks) == 1
        await tasks()
        assert len(stored) == 1 and stored[0].details is None
    assert tasks.tasks == []
