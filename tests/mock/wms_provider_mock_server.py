"""本机开发专用 full-factory WMS Provider HTTP Mock。"""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path
from types import UnionType
from typing import Any, get_args, get_origin

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.app.wms_integration.operation_contract import WmsCompletionMode, WmsOperationMode
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS
from src.app.wms_integration.provider_profile import load_wms_provider_profile
from tests.mock.wms_mock_server import submit_transport
from tests.support.wms_integration.northbound_contract import (
    NorthboundOperationStore,
    build_typed_ack,
    build_typed_result,
    canonical_payload_bytes,
)

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[2] / "deployment/dev/wms-provider.yaml"


def _validate_idempotency_key(value: str) -> str:
    if not value.strip() or len(value) > 160 or "\r" in value or "\n" in value:
        raise HTTPException(status_code=400, detail="IDEMPOTENCY_KEY_INVALID")
    return value


def _annotation_contains(annotation: object, expected: type[object]) -> bool:
    if annotation is expected:
        return True
    origin = get_origin(annotation)
    return origin in {UnionType, tuple} and any(
        _annotation_contains(argument, expected) for argument in get_args(annotation)
    )


def _query_payload(request: Request, operation_identity: str) -> dict[str, object]:
    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    payload: dict[str, object] = dict(request.path_params)
    for field_name in request.query_params:
        values = request.query_params.getlist(field_name)
        field = operation.request_model.model_fields.get(field_name)
        if field is None:
            payload[field_name] = values[-1]
            continue
        annotation = field.annotation
        if get_origin(annotation) is tuple:
            payload[field_name] = tuple(values)
        elif len(values) != 1:
            raise HTTPException(status_code=422, detail="WMS_QUERY_REQUEST_INVALID")
        elif _annotation_contains(annotation, bool):
            if values[0].lower() not in {"true", "false"}:
                raise HTTPException(status_code=422, detail="WMS_QUERY_REQUEST_INVALID")
            payload[field_name] = values[0].lower() == "true"
        elif _annotation_contains(annotation, int):
            try:
                payload[field_name] = int(values[0])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="WMS_QUERY_REQUEST_INVALID") from exc
        else:
            payload[field_name] = values[0]
    return payload


def _query_handler(operation_identity: str):  # type: ignore[no-untyped-def]
    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]

    async def handle(request: Request) -> dict[str, Any]:
        try:
            typed_request = operation.request_model.model_validate(_query_payload(request, operation_identity))
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="WMS_QUERY_REQUEST_INVALID") from exc
        return build_typed_result(
            operation_identity,
            typed_request.model_dump(mode="json", exclude_none=True),
            source_version=1,
            completed_at="2026-01-01T00:00:00+00:00",
        )

    return handle


def _effect_handler(operation_identity: str, store: NorthboundOperationStore):  # type: ignore[no-untyped-def]
    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]

    async def handle(request: Request) -> JSONResponse:
        idempotency_key = _validate_idempotency_key(request.headers.get("Idempotency-Key", ""))
        supplied_identity = request.headers.get("X-WES-Operation-Identity")
        if supplied_identity != operation_identity:
            raise HTTPException(status_code=400, detail="OPERATION_IDENTITY_MISMATCH")
        try:
            raw_payload = await request.json()
            typed_request = operation.request_model.model_validate(raw_payload)
        except (TypeError, ValueError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail="WMS_EFFECT_REQUEST_INVALID") from exc

        payload = typed_request.model_dump(mode="json", exclude_none=True)
        fingerprint = sha256(canonical_payload_bytes(payload)).hexdigest()
        submission = store.submit(operation_identity, idempotency_key, fingerprint, payload)
        if submission.error_code == "IDEMPOTENCY_CONFLICT":
            return JSONResponse(
                status_code=422,
                content={"protocol_error_code": submission.error_code},
            )
        if operation.completion_mode is WmsCompletionMode.ASYNC_TASK:
            state = {
                202: "ACCEPTED",
                409: "IN_PROGRESS_REPLAY",
                200: "REPLAY",
            }[submission.status_code]
            return JSONResponse(
                status_code=submission.status_code,
                content=build_typed_ack(
                    operation_identity,
                    idempotency_key,
                    payload,
                    submission_state=state,
                ),
            )
        if submission.snapshot is None or submission.snapshot.result_payload is None:
            raise HTTPException(status_code=500, detail="WMS_MOCK_RESULT_MISSING")
        return JSONResponse(status_code=submission.status_code, content=submission.snapshot.result_payload)

    return handle


def create_app(profile_path: Path) -> FastAPI:
    profile = load_wms_provider_profile(profile_path.resolve())
    store = NorthboundOperationStore()
    app = FastAPI(title="WES Dev WMS Provider Mock", version=profile.profile.contract_version)

    @app.get("/")
    async def health() -> dict[str, object]:
        return {"service": "wms-provider-mock", "ready": True, "operations": len(WMS_OPERATIONS)}

    @app.get(profile.effect_status_path)
    async def query_status(operation_identity: str, idempotency_key: str) -> dict[str, Any]:
        try:
            return store.query(operation_identity, _validate_idempotency_key(idempotency_key)).as_dict()
        except HTTPException as exc:
            raise HTTPException(status_code=422, detail="WMS_STATUS_REQUEST_INVALID") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="WMS_STATUS_REQUEST_INVALID") from exc

    app.add_api_route(
        str(profile.transport_submit_path),
        submit_transport,
        methods=["POST"],
        name="transport.task.submit@v1",
    )

    for operation in WMS_OPERATIONS:
        configured = profile.operations[operation.identity]
        if operation.mode is WmsOperationMode.QUERY:
            app.add_api_route(
                str(configured.path),
                _query_handler(operation.identity),
                methods=[operation.http_method.value],
                name=operation.identity,
            )
        else:
            app.add_api_route(
                str(configured.submit_path),
                _effect_handler(operation.identity, store),
                methods=[operation.http_method.value],
                name=operation.identity,
            )

    return app


app = create_app(Path(os.getenv("WMS_PROVIDER_PROFILE_FILE", str(DEFAULT_PROFILE_PATH))))


__all__ = ["app", "create_app"]
