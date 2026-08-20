"""本机 full-factory WMS Provider Mock 的公开 HTTP 合同。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from src.app.wms_integration.endpoint_compiler import compile_wms_provider_profile
from src.app.wms_integration.operation_contract import WmsCompletionMode, WmsOperationMode
from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck
from src.app.wms_integration.provider_profile import load_wms_provider_profile
from tests.mock.wms_provider_mock_server import create_app
from tests.mock.wms_transport_mock_openapi import rack_move
from tests.support.wms_integration.operation_fixtures import REQUEST_FIXTURES

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = BACKEND_ROOT / "deployment/dev/wms-provider.yaml"
COMPILED_PROFILE = compile_wms_provider_profile(load_wms_provider_profile(PROFILE_PATH))


@pytest.fixture
async def provider_client():  # type: ignore[no-untyped-def]
    app = create_app(PROFILE_PATH)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mock-wms-provider.test",
    ) as client:
        yield client


@pytest.mark.parametrize("operation", WMS_OPERATIONS, ids=lambda operation: operation.identity)
async def test_all_profile_operations_are_callable_over_public_http(provider_client, operation) -> None:  # type: ignore[no-untyped-def]
    endpoint = COMPILED_PROFILE.operations[operation.identity]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    payload = request.model_dump(mode="json", exclude_none=True)
    path = urlsplit(endpoint.render_endpoint(request)).path

    if operation.mode is WmsOperationMode.QUERY:
        query = {name: value for name, value in payload.items() if name not in endpoint.placeholder_names}
        response = await provider_client.get(path, params=query)
        assert response.status_code == 200, response.text
        operation.result_model.model_validate(response.json())
        return

    idempotency_key = f"dev-mock-{operation.identity}"
    response = await provider_client.post(
        path,
        json=payload,
        headers={
            "Idempotency-Key": idempotency_key,
            "X-WES-Operation-Identity": operation.identity,
        },
    )
    if operation.completion_mode is WmsCompletionMode.ASYNC_TASK:
        assert response.status_code == 202, response.text
        ack = WmsEffectAck.model_validate(response.json())
        assert ack.operation_identity == operation.identity
        assert ack.idempotency_key == idempotency_key
    else:
        assert response.status_code == 200, response.text
        operation.result_model.model_validate(response.json())


async def test_configured_transport_submit_path_is_callable_over_public_http(provider_client) -> None:  # type: ignore[no-untyped-def]
    response = await provider_client.post(COMPILED_PROFILE.transport_submit_path, json=rack_move)

    assert response.status_code == 202, response.text
    assert response.json()["code"] == "RECEIVED"


async def test_configured_transport_submit_path_rejects_oversized_body_before_parsing(provider_client) -> None:  # type: ignore[no-untyped-def]
    response = await provider_client.post(
        COMPILED_PROFILE.transport_submit_path,
        content=b"x" * (256 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.content == b""


async def test_async_effect_reaches_a_typed_terminal_status(provider_client) -> None:  # type: ignore[no-untyped-def]
    operation = next(
        operation for operation in WMS_OPERATIONS if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
    )
    endpoint = COMPILED_PROFILE.operations[operation.identity]
    payload = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity]).model_dump(
        mode="json", exclude_none=True
    )
    idempotency_key = "dev-mock-status"

    submitted = await provider_client.post(
        urlsplit(endpoint.render_endpoint(operation.request_model.model_validate(payload))).path,
        json=payload,
        headers={
            "Idempotency-Key": idempotency_key,
            "X-WES-Operation-Identity": operation.identity,
        },
    )
    assert submitted.status_code == 202

    states: list[str] = []
    for _ in range(3):
        status = await provider_client.get(
            "/api/wms/operations/status",
            params={"operation_identity": operation.identity, "idempotency_key": idempotency_key},
        )
        assert status.status_code == 200, status.text
        states.append(status.json()["state"])

    assert states == ["ACCEPTED", "PROCESSING", "COMPLETED"]
    operation.result_model.model_validate(status.json()["result_payload"])


@pytest.mark.parametrize("idempotency_key", [" ", "x" * 161])
async def test_effect_rejects_invalid_idempotency_key_before_storing(provider_client, idempotency_key: str) -> None:  # type: ignore[no-untyped-def]
    operation = next(
        operation for operation in WMS_OPERATIONS if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
    )
    endpoint = COMPILED_PROFILE.operations[operation.identity]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])

    response = await provider_client.post(
        urlsplit(endpoint.render_endpoint(request)).path,
        json=request.model_dump(mode="json", exclude_none=True),
        headers={
            "Idempotency-Key": idempotency_key,
            "X-WES-Operation-Identity": operation.identity,
        },
    )

    assert response.status_code == 400


async def test_status_rejects_invalid_idempotency_key_contract(provider_client) -> None:  # type: ignore[no-untyped-def]
    operation = next(
        operation for operation in WMS_OPERATIONS if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
    )

    response = await provider_client.get(
        "/api/wms/operations/status",
        params={"operation_identity": operation.identity, "idempotency_key": "x" * 161},
    )

    assert response.status_code == 422


async def test_effect_replay_rejects_payload_drift_for_the_same_key(provider_client) -> None:  # type: ignore[no-untyped-def]
    operation = next(
        operation for operation in WMS_OPERATIONS if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
    )
    endpoint = COMPILED_PROFILE.operations[operation.identity]
    request = operation.request_model.model_validate(REQUEST_FIXTURES[operation.identity])
    path = urlsplit(endpoint.render_endpoint(request)).path
    headers = {
        "Idempotency-Key": "dev-mock-conflict",
        "X-WES-Operation-Identity": operation.identity,
    }
    payload = request.model_dump(mode="json", exclude_none=True)
    changed_payload = dict(payload)
    changed_field = next(name for name, value in payload.items() if isinstance(value, str))
    changed_payload[changed_field] = f"{payload[changed_field]}-changed"

    first = await provider_client.post(path, json=payload, headers=headers)
    conflict = await provider_client.post(path, json=changed_payload, headers=headers)

    assert first.status_code == 202
    assert conflict.status_code == 422
    assert conflict.json() == {"protocol_error_code": "IDEMPOTENCY_CONFLICT"}
