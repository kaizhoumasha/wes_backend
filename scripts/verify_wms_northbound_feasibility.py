"""通过实际 WMS Mock 的公开 HTTP 面执行脱敏北向合同探针。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.runtime.system_capabilities.wms.provider_catalog import validate_wms_transport_configuration  # noqa: E402
from src.app.sys.canonical_dispatch import canonical_json_bytes, payload_sha256  # noqa: E402
from src.app.sys.external_http_credentials import EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE  # noqa: E402
from src.app.wms_integration.operation_contract import WmsCompletionMode  # noqa: E402
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS  # noqa: E402
from src.app.wms_integration.ports.fulfillment_operations import (  # noqa: E402
    BATCH_FULFILLMENT_OPERATION_IDENTITIES,
    WmsAcceptedScope,
    WmsEffectAck,
    accepted_scope_digest,
)
from src.core.conf import settings  # noqa: E402
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES  # noqa: E402

if TYPE_CHECKING:
    from src.app.wms_integration.endpoint_compiler import CompiledWmsProviderProfile

_STATES = frozenset({"ACCEPTED", "PROCESSING", "COMPLETED", "REJECTED", "NOT_FOUND"})
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_OPAQUE_REFERENCE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_MAX_SAFE_RESPONSE_BYTES = 1024 * 1024
_RESPONSE_CLOSE_TIMEOUT_SECONDS = 1.0
_ASYNC_OPERATIONS = tuple(
    operation for operation in WMS_OPERATIONS if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
)
_OPERATION_SPECS: dict[str, dict[str, Any]] = {
    operation.identity: {
        "payload": REQUEST_FIXTURES[operation.identity],
        "rejection": operation.reject_codes[0],
    }
    for operation in _ASYNC_OPERATIONS
}
_EXPECTED_STATUS_DEADLINE_SECONDS = float(settings.WMS_EFFECT_STATUS_TIMEOUT_SECONDS)


@dataclass(frozen=True)
class ProbeCaseResult:
    """仅含本地枚举和布尔结论的探针结果。"""

    case_id: str
    passed: bool
    detail: str = "CONTRACT_ASSERTION"


@dataclass(frozen=True)
class FeasibilityReport:
    """可写入可行性报告的最小探针输出。"""

    cases: tuple[ProbeCaseResult, ...]

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    request_timeout_seconds: float,
    max_response_bytes: int = _MAX_SAFE_RESPONSE_BYTES,
    **kwargs: Any,
) -> httpx.Response | None:
    """请求 deadline 覆盖 send 与流式 body；清理使用独立短期限保证连接关闭。"""

    response: httpx.Response | None = None
    deadline = asyncio.get_running_loop().time() + request_timeout_seconds
    try:
        async with asyncio.timeout_at(deadline):
            request = client.build_request(method, path, **kwargs)
            response = await client.send(request, stream=True)
            content = bytearray()
            async for chunk in response.aiter_raw(chunk_size=8192):
                content.extend(chunk)
                if len(content) > max_response_bytes:
                    response.extensions["probe_body_exceeded"] = True
                    response._content = b""  # 关闭 stream 前仅保留本地失败标记
                    await response.aclose()
                    return response
            response._content = bytes(content)
            await response.aclose()
            return response
    except (httpx.HTTPError, TimeoutError):
        return None
    finally:
        if response is not None:
            try:
                async with asyncio.timeout(_RESPONSE_CLOSE_TIMEOUT_SECONDS):
                    await response.aclose()
            except (httpx.HTTPError, TimeoutError):
                pass


def _json_object(response: httpx.Response | None, *, max_response_bytes: int) -> dict[str, Any] | None:
    """拒绝超限、非对象或畸形 JSON；不传递远端 body 到报告。"""

    if response is None or response.extensions.get("probe_body_exceeded") or len(response.content) > max_response_bytes:
        return None
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_aware_rfc3339(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(parsed)


def _contract_values(
    contract: object,
    *,
    compiled_profile: CompiledWmsProviderProfile,
) -> dict[str, Any] | None:
    """仅接受实际 Mock 声明且覆盖 WES 安全窗口的公开承诺参数。"""

    if not isinstance(contract, dict):
        return None
    required_positive_integers = (
        "idempotency_retention_seconds",
        "max_response_bytes",
    )
    required_positive_times = (
        "status_visibility_sla_seconds",
        "submit_deadline_seconds",
        "status_deadline_seconds",
    )
    values = {name: contract.get(name) for name in (*required_positive_integers, *required_positive_times)}
    credential_reference = contract.get("credential_reference")
    if any(
        isinstance(values[name], bool) or not isinstance(values[name], int) or values[name] <= 0
        for name in required_positive_integers
    ):
        return None
    if any(
        isinstance(values[name], bool)
        or not isinstance(values[name], (int, float))
        or not math.isfinite(float(values[name]))
        or values[name] <= 0
        for name in required_positive_times
    ):
        return None
    minimum_retention = (
        settings.WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS + settings.WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS
    )
    if values["idempotency_retention_seconds"] < minimum_retention:
        return None
    if values["status_visibility_sla_seconds"] > settings.WES_EFFECT_NOT_FOUND_GRACE_SECONDS:
        return None
    active_credential_reference = compiled_profile.profile.outbound_auth.credential_reference
    if credential_reference != active_credential_reference:
        return None
    return {**values, "credential_reference": credential_reference}


def _is_snapshot(snapshot: object, *, operation_identity: str, payload: dict[str, Any]) -> bool:  # noqa: PLR0911
    """严格验证五态快照及对应 operation 的 typed completed result。"""

    if not isinstance(snapshot, dict) or set(snapshot) != {
        "state",
        "provider_reference",
        "accepted_scope",
        "reason_code",
        "updated_at",
        "source_version",
        "result_payload",
    }:
        return False
    state = snapshot["state"]
    if state not in _STATES:
        return False
    if state == "NOT_FOUND":
        return all(
            snapshot[field] is None
            for field in (
                "provider_reference",
                "accepted_scope",
                "reason_code",
                "updated_at",
                "source_version",
                "result_payload",
            )
        )
    accepted_scope_payload = snapshot["accepted_scope"]
    if operation_identity in BATCH_FULFILLMENT_OPERATION_IDENTITIES:
        try:
            accepted_scope = WmsAcceptedScope.model_validate(accepted_scope_payload)
        except ValueError:
            return False
        if accepted_scope.scope_digest != accepted_scope_digest(accepted_scope.object_keys):
            return False
    elif accepted_scope_payload is not None:
        return False
    source_version = snapshot["source_version"]
    if isinstance(source_version, bool) or not isinstance(source_version, int) or not 0 <= source_version < 2**63:
        return False
    if not isinstance(snapshot["provider_reference"], str) or not _OPAQUE_REFERENCE.fullmatch(
        snapshot["provider_reference"]
    ):
        return False
    if not _is_aware_rfc3339(snapshot["updated_at"]):
        return False
    if state == "REJECTED":
        return (
            isinstance(snapshot["reason_code"], str)
            and bool(_REASON_CODE.fullmatch(snapshot["reason_code"]))
            and snapshot["result_payload"] is None
        )
    if snapshot["reason_code"] is not None:
        return False
    if state in {"ACCEPTED", "PROCESSING"}:
        return snapshot["result_payload"] is None
    result = snapshot["result_payload"]
    if not isinstance(result, dict):
        return False
    try:
        typed_result = WMS_OPERATION_BY_IDENTITY[operation_identity].result_model.model_validate(result)
    except (KeyError, ValueError):
        return False
    normalized = typed_result.model_dump(mode="json")
    return (
        normalized["dispatch_key"] == payload["dispatch_key"]
        and isinstance(normalized["provider_reference"], str)
        and bool(normalized["provider_reference"])
        and normalized["source_version"] == str(source_version)
    )


def _is_ack(
    value: object,
    *,
    operation_identity: str,
    idempotency_key: str,
) -> bool:
    """验证 E08–E14 共享 ACK，并拒绝任何终态字段混入受理响应。"""

    try:
        ack = WmsEffectAck.model_validate(value)
    except ValueError:
        return False
    return ack.operation_identity == operation_identity and ack.idempotency_key == idempotency_key


def _retry_after_is_valid(value: str | None) -> bool:
    if value is None or len(value) > 64:
        return False
    if value.isdecimal():
        return True
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC) >= datetime.now(UTC)


def _result(case_id: str, passed: bool) -> ProbeCaseResult:
    return ProbeCaseResult(case_id=case_id, passed=passed)


def _signature(secret: bytes, canonical: str) -> str:
    return hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _submit_headers(
    *,
    secret: bytes,
    credential_reference: str,
    path: str,
    body: bytes,
    operation_identity: str,
    key: str,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = timestamp or str(int(datetime.now(UTC).timestamp()))
    nonce = nonce or uuid4().hex
    body_hash = payload_sha256(body)
    canonical = f"POST\n{path}\n{timestamp}\n{nonce}\n{body_hash}\n{operation_identity}\n{key}"
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": key,
        "X-WES-Content-SHA256": body_hash,
        "X-WES-Credential-Reference": credential_reference,
        "X-WES-Nonce": nonce,
        "X-WES-Operation-Identity": operation_identity,
        "X-WES-Signature": _signature(secret, canonical),
        "X-WES-Signature-Algorithm": "HMAC_SHA256",
        "X-WES-Timestamp": timestamp,
    }


def _status_headers(*, secret: bytes, credential_reference: str, raw_path: str) -> dict[str, str]:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    nonce = uuid4().hex
    body_hash = hashlib.sha256(b"").hexdigest()
    canonical = f"GET\n{raw_path}\n{timestamp}\n{nonce}\n{body_hash}"
    return {
        "X-WMS-Content-SHA256": body_hash,
        "X-WMS-Credential-Reference": credential_reference,
        "X-WMS-Nonce": nonce,
        "X-WMS-Signature": _signature(secret, canonical),
        "X-WMS-Signature-Algorithm": "HMAC_SHA256",
        "X-WMS-Timestamp": timestamp,
    }


async def run_probe(
    client: httpx.AsyncClient,
    *,
    compiled_profile: CompiledWmsProviderProfile,
    operation_identity: str | None = None,
    request_timeout_seconds: float = 2.0,
    submit_timeout_seconds: float | None = None,
    status_timeout_seconds: float | None = None,
) -> FeasibilityReport:
    """通过实际 Mock 的公开路由验收三类 typed EFFECT，绝不读取其内部状态。"""

    effective_submit_timeout = submit_timeout_seconds or request_timeout_seconds
    effective_status_timeout = status_timeout_seconds or request_timeout_seconds
    active_credential_reference = compiled_profile.profile.outbound_auth.credential_reference
    if active_credential_reference is None:
        raise ValueError("WMS northbound feasibility probe requires outbound HMAC authentication")
    try:
        active_hmac_secret_env = EXTERNAL_HTTP_CREDENTIAL_ENV_BY_REFERENCE[active_credential_reference]
    except KeyError as exc:
        raise ValueError("WMS northbound feasibility probe credential reference is not resolvable") from exc
    operation_specs = {
        identity: {
            **spec,
            "submit_path": urlsplit(compiled_profile.operations[identity].endpoint_template).path,
        }
        for identity, spec in _OPERATION_SPECS.items()
    }
    typed_effect_submit_deadlines = {
        float(compiled_profile.operations[identity].budget.deadline_seconds) for identity in operation_specs
    }
    if len(typed_effect_submit_deadlines) != 1:
        raise RuntimeError("typed WMS EFFECT operations must share one submit deadline")
    expected_submit_deadline_seconds = next(iter(typed_effect_submit_deadlines))
    status_endpoints = {
        endpoint.status_endpoint
        for identity, endpoint in compiled_profile.operations.items()
        if identity in operation_specs
    }
    if len(status_endpoints) != 1 or None in status_endpoints:
        raise RuntimeError("typed WMS EFFECT operations must share one status endpoint")
    status_target = urlsplit(next(iter(status_endpoints))).path
    results: list[ProbeCaseResult] = []
    bootstrap_limit = 64 * 1024
    contract_response = await _request(
        client, "GET", "/northbound/contract", request_timeout_seconds=request_timeout_seconds
    )
    contract = _json_object(contract_response, max_response_bytes=bootstrap_limit)
    contract_values = _contract_values(contract, compiled_profile=compiled_profile)
    contract_ok = contract_response is not None and contract_response.status_code == 200 and contract_values is not None
    results.append(_result("public_contract_parameters", contract_ok))
    if contract_values is None:
        return FeasibilityReport(cases=tuple(results))
    results.append(
        _result(
            "public_contract_deadline_alignment",
            contract_values["submit_deadline_seconds"] == expected_submit_deadline_seconds
            and contract_values["status_deadline_seconds"] == _EXPECTED_STATUS_DEADLINE_SECONDS,
        )
    )

    configured_secret = os.getenv(active_hmac_secret_env) or getattr(settings, active_hmac_secret_env, "")
    secret = configured_secret.encode("utf-8")
    results.append(_result("active_v2_hmac_secret_available", bool(secret)))
    if not secret:
        return FeasibilityReport(cases=tuple(results))
    max_response_bytes = min(contract_values["max_response_bytes"], _MAX_SAFE_RESPONSE_BYTES)
    current_material_path = "/api/wms/master-data/materials/MAT001"
    current_material = await _request(
        client,
        "GET",
        current_material_path,
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
        headers=_status_headers(
            secret=secret,
            credential_reference=contract_values["credential_reference"],
            raw_path=current_material_path,
        ),
    )
    legacy_material = await _request(
        client,
        "GET",
        "/api/wms/materials/MAT001",
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
    )
    results.append(
        _result(
            "current_master_data_route_is_live_and_legacy_route_is_removed",
            current_material is not None
            and current_material.status_code == 200
            and legacy_material is not None
            and legacy_material.status_code == 404,
        )
    )
    selected = (operation_identity,) if operation_identity else tuple(operation_specs)
    if any(identity not in operation_specs for identity in selected):
        results.append(_result("requested_operation_supported", False))
        return FeasibilityReport(cases=tuple(results))

    async def submit(
        identity: str, key: str, payload: dict[str, Any], *, header_overrides: dict[str, str] | None = None
    ) -> httpx.Response | None:
        spec = operation_specs[identity]
        body = canonical_json_bytes(payload)
        headers = _submit_headers(
            secret=secret,
            credential_reference=contract_values["credential_reference"],
            path=spec["submit_path"],
            body=body,
            operation_identity=identity,
            key=key,
        )
        headers.update(header_overrides or {})
        return await _request(
            client,
            "POST",
            spec["submit_path"],
            request_timeout_seconds=effective_submit_timeout,
            max_response_bytes=max_response_bytes,
            content=body,
            headers=headers,
        )

    async def status(identity: str, key: str) -> httpx.Response | None:
        raw_path = status_target + "?" + urlencode((("operation_identity", identity), ("idempotency_key", key)))
        return await _request(
            client,
            "GET",
            raw_path,
            request_timeout_seconds=effective_status_timeout,
            max_response_bytes=max_response_bytes,
            headers=_status_headers(
                secret=secret, credential_reference=contract_values["credential_reference"], raw_path=raw_path
            ),
        )

    async def effect_count(identity: str, key: str) -> int | None:
        response = await _request(
            client,
            "GET",
            "/debug/northbound/effects",
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            params={"operation_identity": identity, "idempotency_key": key},
        )
        payload = _json_object(response, max_response_bytes=max_response_bytes)
        value = payload.get("effect_count") if payload else None
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    async def configure_fault(**payload: Any) -> bool:
        response = await _request(
            client,
            "POST",
            "/debug/northbound/faults",
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            json=payload,
        )
        return response is not None and response.status_code == 200

    async def configure_visibility(identity: str, key: str, *, delay_seconds: float) -> bool:
        response = await _request(
            client,
            "POST",
            "/debug/northbound/visibility",
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            json={
                "operation_identity": identity,
                "idempotency_key": key,
                "delay_seconds": delay_seconds,
            },
        )
        payload = _json_object(response, max_response_bytes=max_response_bytes)
        return (
            response is not None
            and response.status_code == 200
            and payload
            == {
                "operation_identity": identity,
                "idempotency_key": key,
                "delay_seconds": delay_seconds,
            }
        )

    async def configure_clock(now: datetime | None) -> bool:
        response = await _request(
            client,
            "POST",
            "/debug/northbound/clock",
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            json={"now": now.isoformat() if now is not None else None},
        )
        payload = _json_object(response, max_response_bytes=max_response_bytes)
        expected = now.astimezone(UTC).isoformat() if now is not None else None
        return (
            response is not None
            and response.status_code == 200
            and payload is not None
            and payload.get("data", {}).get("now") == expected
        )

    async def callback_hints(identity: str, key: str) -> dict[str, Any] | None:
        response = await _request(
            client,
            "GET",
            "/debug/northbound/callback-hints",
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            params={"operation_identity": identity, "idempotency_key": key},
        )
        return _json_object(response, max_response_bytes=max_response_bytes)

    for identity in selected:
        spec = operation_specs[identity]
        payload = dict(spec["payload"])
        key = f"probe-{uuid4().hex}"
        first = await submit(identity, key, payload)
        first_body = _json_object(first, max_response_bytes=max_response_bytes)
        first_ack = first_body.get("data") if first_body else None
        results.append(
            _result(
                f"{identity}:first_submit",
                first is not None
                and first.status_code == 202
                and _is_ack(first_ack, operation_identity=identity, idempotency_key=key),
            )
        )

        processing_replay = await submit(identity, key, payload)
        processing_body = _json_object(processing_replay, max_response_bytes=max_response_bytes)
        results.append(
            _result(
                f"{identity}:in_progress_replay",
                processing_replay is not None
                and processing_replay.status_code == 409
                and processing_body is not None
                and processing_body.get("code") == "IDEMPOTENCY_REQUEST_IN_PROGRESS",
            )
        )
        hint_evidence = await callback_hints(identity, key)

        snapshots = [_json_object(await status(identity, key), max_response_bytes=max_response_bytes) for _ in range(3)]
        results.append(
            _result(
                f"{identity}:five_state_progression_and_typed_result",
                all(_is_snapshot(snapshot, operation_identity=identity, payload=payload) for snapshot in snapshots)
                and [snapshot["state"] for snapshot in snapshots] == ["ACCEPTED", "PROCESSING", "COMPLETED"]
                and snapshots[0]["source_version"] < snapshots[1]["source_version"] < snapshots[2]["source_version"],
            )
        )

        completed_replay = await submit(identity, key, payload)
        completed_body = _json_object(completed_replay, max_response_bytes=max_response_bytes)
        completed_ack = completed_body.get("data") if completed_body else None
        results.append(
            _result(
                f"{identity}:completed_replay",
                completed_replay is not None
                and completed_replay.status_code == 200
                and _is_ack(completed_ack, operation_identity=identity, idempotency_key=key),
            )
        )

        conflicting_payload = {**payload, "dispatch_key": f"{payload['dispatch_key']}-conflict"}
        conflict = await submit(identity, key, conflicting_payload)
        conflict_body = _json_object(conflict, max_response_bytes=max_response_bytes)
        results.append(
            _result(
                f"{identity}:idempotency_conflict",
                conflict is not None
                and conflict.status_code == 422
                and conflict_body is not None
                and conflict_body.get("code") == "IDEMPOTENCY_CONFLICT"
                and await effect_count(identity, key) == 1,
            )
        )

        required_field = next(
            field_name
            for field_name, field_info in WMS_OPERATION_BY_IDENTITY[identity].request_model.model_fields.items()
            if field_info.is_required() and field_name != "dispatch_key"
        )
        missing_payload = {field: value for field, value in payload.items() if field != required_field}
        invalid_payloads = (
            missing_payload,
            {**payload, "unexpected_wire_field": "forbidden"},
        )
        validation_responses = []
        for invalid_index, invalid_payload in enumerate(invalid_payloads):
            validation_responses.append(
                await submit(
                    identity,
                    f"probe-invalid-{invalid_index}-{uuid4().hex}",
                    invalid_payload,
                )
            )
        recovery_key = f"probe-invalid-recovery-{uuid4().hex}"
        rejected_before_write = await submit(identity, recovery_key, missing_payload)
        accepted_after_rejection = await submit(identity, recovery_key, payload)
        results.append(
            _result(
                f"{identity}:typed_request_validation",
                all(
                    response is not None
                    and response.status_code == 422
                    and _json_object(response, max_response_bytes=max_response_bytes)
                    == {"code": "INVALID_TYPED_REQUEST"}
                    for response in (*validation_responses, rejected_before_write)
                )
                and accepted_after_rejection is not None
                and accepted_after_rejection.status_code == 202
                and await effect_count(identity, recovery_key) == 1,
            )
        )

        rejected_key = f"probe-rejected-{uuid4().hex}"
        await submit(identity, rejected_key, payload)
        rejected = await _request(
            client,
            "POST",
            "/debug/northbound/reject",
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            json={"operation_identity": identity, "idempotency_key": rejected_key, "reason_code": spec["rejection"]},
        )
        rejected_snapshots = [
            _json_object(await status(identity, rejected_key), max_response_bytes=max_response_bytes) for _ in range(2)
        ]
        results.append(
            _result(
                f"{identity}:rejected_stable_reason",
                rejected is not None
                and rejected.status_code == 200
                and all(
                    _is_snapshot(snapshot, operation_identity=identity, payload=payload)
                    for snapshot in rejected_snapshots
                )
                and rejected_snapshots[0] == rejected_snapshots[1]
                and rejected_snapshots[0]["state"] == "REJECTED"
                and rejected_snapshots[0]["reason_code"] == spec["rejection"],
            )
        )

        missing = _json_object(await status(identity, f"missing-{uuid4().hex}"), max_response_bytes=max_response_bytes)
        results.append(
            _result(
                f"{identity}:not_found",
                _is_snapshot(missing, operation_identity=identity, payload=payload) and missing["state"] == "NOT_FOUND",
            )
        )

        hint = hint_evidence.get("hints") if hint_evidence else None
        expected_hint = {
            "callback_type": "WMS_EFFECT_STATUS_HINT",
            "dispatch_key": payload["dispatch_key"],
            "idempotency_key": key,
            "operation_identity": identity,
        }
        # Submit 仅记录脱敏 hint；最终状态仍只由后续 status 公开面提供。
        results.append(
            _result(
                f"{identity}:callback_hint_evidence_and_status_authority",
                isinstance(hint, list)
                and hint == [expected_hint]
                and all(set(item) == set(expected_hint) for item in hint if isinstance(item, dict))
                and snapshots[-1].get("state") == "COMPLETED",
            )
        )

        visibility_key = f"probe-visibility-{uuid4().hex}"
        visibility_sla = contract_values["status_visibility_sla_seconds"]
        retention = contract_values["idempotency_retention_seconds"]
        # 使用探针执行时刻，避免固定历史时钟复位后让本轮更早创建的记录被误判为已过保留期。
        accepted_at = datetime.now(UTC)
        clock_started = await configure_clock(accepted_at)
        visibility_configured = await configure_visibility(
            identity,
            visibility_key,
            delay_seconds=visibility_sla,
        )
        first_visibility_submit = await submit(identity, visibility_key, payload)
        hidden_at_accept = _json_object(await status(identity, visibility_key), max_response_bytes=max_response_bytes)
        before_sla_clock = await configure_clock(accepted_at + timedelta(seconds=max(visibility_sla - 1, 0)))
        hidden_before_sla = _json_object(await status(identity, visibility_key), max_response_bytes=max_response_bytes)
        at_sla_clock = await configure_clock(accepted_at + timedelta(seconds=visibility_sla))
        visible_at_sla = _json_object(await status(identity, visibility_key), max_response_bytes=max_response_bytes)
        before_retention_clock = await configure_clock(accepted_at + timedelta(seconds=retention - 1))
        replay_before_retention = await submit(identity, visibility_key, payload)
        at_retention_clock = await configure_clock(accepted_at + timedelta(seconds=retention))
        expired_at_retention = _json_object(
            await status(identity, visibility_key),
            max_response_bytes=max_response_bytes,
        )
        recovered_at_retention = await submit(identity, visibility_key, payload)
        effect_count_after_recovery = await effect_count(identity, visibility_key)
        clock_restored = await configure_clock(None)
        results.append(
            _result(
                f"{identity}:visibility_sla_and_retention_boundaries",
                clock_started
                and visibility_configured
                and first_visibility_submit is not None
                and first_visibility_submit.status_code == 202
                and _is_snapshot(hidden_at_accept, operation_identity=identity, payload=payload)
                and hidden_at_accept["state"] == "NOT_FOUND"
                and before_sla_clock
                and _is_snapshot(hidden_before_sla, operation_identity=identity, payload=payload)
                and hidden_before_sla["state"] == "NOT_FOUND"
                and at_sla_clock
                and _is_snapshot(visible_at_sla, operation_identity=identity, payload=payload)
                and visible_at_sla["state"] == "ACCEPTED"
                and before_retention_clock
                and replay_before_retention is not None
                and replay_before_retention.status_code == 409
                and at_retention_clock
                and _is_snapshot(expired_at_retention, operation_identity=identity, payload=payload)
                and expired_at_retention["state"] == "NOT_FOUND"
                and recovered_at_retention is not None
                and recovered_at_retention.status_code == 202
                and effect_count_after_recovery == 2
                and clock_restored,
            )
        )

        visible_then_lost_configured = await configure_fault(
            status=200,
            target_path=status_target,
            method="GET",
            operation_identity=identity,
            not_found=True,
        )
        lost_after_visible = _json_object(await status(identity, key), max_response_bytes=max_response_bytes)
        visible_again = _json_object(await status(identity, key), max_response_bytes=max_response_bytes)
        results.append(
            _result(
                f"{identity}:visible_then_lost_is_independent_fault",
                visible_then_lost_configured
                and _is_snapshot(lost_after_visible, operation_identity=identity, payload=payload)
                and lost_after_visible["state"] == "NOT_FOUND"
                and visible_again == snapshots[-1],
            )
        )

        signature_tamper = await submit(
            identity,
            f"probe-submit-hmac-{uuid4().hex}",
            payload,
            header_overrides={"X-WES-Signature": "0" * 64},
        )
        signature_tamper_body = _json_object(signature_tamper, max_response_bytes=max_response_bytes)
        results.append(
            _result(
                f"{identity}:submit_hmac_signature_tamper",
                signature_tamper is not None
                and signature_tamper.status_code == 401
                and signature_tamper_body == {"code": "INVALID_HMAC_SIGNATURE"},
            )
        )

    fault_identity = selected[0]
    fault_key = f"probe-fault-{uuid4().hex}"
    fault_payload = dict(operation_specs[fault_identity]["payload"])
    await submit(fault_identity, fault_key, fault_payload)
    rate_configured = await configure_fault(
        status=429,
        retry_after=2,
        target_path=status_target,
        method="GET",
        operation_identity=fault_identity,
    )
    rate_limited = await status(fault_identity, fault_key)
    unavailable_configured = await configure_fault(
        status=503,
        target_path=status_target,
        method="GET",
        operation_identity=fault_identity,
    )
    unavailable = await status(fault_identity, fault_key)
    unavailable_body = _json_object(unavailable, max_response_bytes=max_response_bytes)
    results.append(
        _result(
            "fault_matrix_rate_limit_and_fixed_5xx",
            rate_configured
            and rate_limited is not None
            and rate_limited.status_code == 429
            and _retry_after_is_valid(rate_limited.headers.get("Retry-After"))
            and unavailable_configured
            and unavailable is not None
            and unavailable.status_code == 503
            and unavailable_body == {"code": "TEMPORARILY_UNAVAILABLE"},
        )
    )

    scope_configured = await configure_fault(
        status=503,
        target_path=status_target,
        method="GET",
        operation_identity=fault_identity,
    )
    health = await _request(
        client,
        "GET",
        "/",
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
    )
    current_inventory = await _request(
        client,
        "GET",
        current_material_path,
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
        headers=_status_headers(
            secret=secret,
            credential_reference=contract_values["credential_reference"],
            raw_path=current_material_path,
        ),
    )
    unregistered = await _request(
        client,
        "POST",
        "/api/wms/fulfillment/unregistered-operation",
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
        json={"dispatch_key": f"probe-unregistered-scope-{uuid4().hex}"},
    )
    scoped_fault = await status(fault_identity, fault_key)
    results.append(
        _result(
            "northbound_fault_scope_excludes_health_inventory_and_unregistered_paths",
            scope_configured
            and health is not None
            and health.status_code == 200
            and current_inventory is not None
            and current_inventory.status_code == 200
            and unregistered is not None
            and unregistered.status_code == 404
            and scoped_fault is not None
            and scoped_fault.status_code == 503,
        )
    )

    submit_deadline_key = f"probe-submit-deadline-{uuid4().hex}"
    submit_deadline_configured = await configure_fault(
        status=200,
        target_path=operation_specs[fault_identity]["submit_path"],
        method="POST",
        operation_identity=fault_identity,
        delay=max(effective_submit_timeout * 2, 0.05),
        after_response=True,
    )
    ambiguous_submit = await submit(fault_identity, submit_deadline_key, fault_payload)
    ambiguous_retry = await submit(fault_identity, submit_deadline_key, fault_payload)
    ambiguous_retry_body = _json_object(ambiguous_retry, max_response_bytes=max_response_bytes)
    results.append(
        _result(
            "submit_deadline_ambiguous_retry_one_effect",
            submit_deadline_configured
            and ambiguous_submit is None
            and ambiguous_retry is not None
            and ambiguous_retry.status_code == 409
            and ambiguous_retry_body is not None
            and ambiguous_retry_body.get("code") == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
            and await effect_count(fault_identity, submit_deadline_key) == 1,
        )
    )

    status_deadline_key = f"probe-status-deadline-{uuid4().hex}"
    status_deadline_submit = await submit(fault_identity, status_deadline_key, fault_payload)
    status_deadline_configured = await configure_fault(
        status=200,
        target_path=status_target,
        method="GET",
        operation_identity=fault_identity,
        delay=max(effective_status_timeout * 2, 0.05),
    )
    timed_out_status = await status(fault_identity, status_deadline_key)
    status_after_timeout = _json_object(
        await status(fault_identity, status_deadline_key),
        max_response_bytes=max_response_bytes,
    )
    results.append(
        _result(
            "status_deadline",
            status_deadline_submit is not None
            and status_deadline_submit.status_code == 202
            and status_deadline_configured
            and timed_out_status is None
            and _is_snapshot(status_after_timeout, operation_identity=fault_identity, payload=fault_payload)
            and status_after_timeout["state"] == "ACCEPTED",
        )
    )

    stale_submit_path = operation_specs[fault_identity]["submit_path"]
    stale_submit_body = canonical_json_bytes(fault_payload)
    stale_submit_key = f"probe-stale-submit-{uuid4().hex}"
    stale_submit = await _request(
        client,
        "POST",
        stale_submit_path,
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
        content=stale_submit_body,
        headers=_submit_headers(
            secret=secret,
            credential_reference=contract_values["credential_reference"],
            path=stale_submit_path,
            body=stale_submit_body,
            operation_identity=fault_identity,
            key=stale_submit_key,
            timestamp="1721865600",
        ),
    )
    results.append(
        _result(
            "submit_stale_timestamp_rejected_without_remote_echo",
            stale_submit is not None
            and stale_submit.status_code == 401
            and _json_object(stale_submit, max_response_bytes=max_response_bytes)
            == {"code": "SIGNATURE_TIMESTAMP_OUT_OF_WINDOW"},
        )
    )

    replay_raw_path = (
        status_target + "?" + urlencode((("operation_identity", fault_identity), ("idempotency_key", fault_key)))
    )
    replay_headers = _status_headers(
        secret=secret,
        credential_reference=contract_values["credential_reference"],
        raw_path=replay_raw_path,
    )
    first_status_attempt = await _request(
        client,
        "GET",
        replay_raw_path,
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
        headers=replay_headers,
    )
    replayed_status_attempt = await _request(
        client,
        "GET",
        replay_raw_path,
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
        headers=replay_headers,
    )
    results.append(
        _result(
            "status_nonce_replay_rejected_without_remote_echo",
            first_status_attempt is not None
            and first_status_attempt.status_code == 200
            and replayed_status_attempt is not None
            and replayed_status_attempt.status_code == 401
            and _json_object(replayed_status_attempt, max_response_bytes=max_response_bytes)
            == {"code": "HMAC_NONCE_REPLAYED"},
        )
    )

    tampered_raw_path = (
        status_target + "?" + urlencode((("operation_identity", fault_identity), ("idempotency_key", fault_key)))
    )
    tampered_headers = _status_headers(
        secret=secret, credential_reference=contract_values["credential_reference"], raw_path=tampered_raw_path
    )
    tampered_headers["X-WMS-Signature"] = "0" * 64
    tampered = await _request(
        client,
        "GET",
        tampered_raw_path,
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
        headers=tampered_headers,
    )
    results.append(
        _result("status_hmac_tamper_rejected_without_remote_echo", tampered is not None and tampered.status_code == 401)
    )

    oversized_configured = await configure_fault(
        status=503,
        target_path=status_target,
        method="GET",
        operation_identity=fault_identity,
        response_body_bytes=contract_values["max_response_bytes"] + 1,
    )
    oversized = await status(fault_identity, fault_key)
    results.append(
        _result(
            "response_body_budget_exceeded_without_remote_echo",
            oversized_configured
            and oversized is not None
            and oversized.status_code == 503
            and oversized.extensions.get("probe_body_exceeded") is True
            and oversized.content == b""
            and _json_object(oversized, max_response_bytes=max_response_bytes) is None,
        )
    )

    reset = await _request(
        client,
        "POST",
        "/debug/reset",
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
    )
    after_reset = _json_object(await status(fault_identity, fault_key), max_response_bytes=max_response_bytes)
    results.append(
        _result(
            "public_reset_clears_observable_operation",
            reset is not None
            and reset.status_code == 200
            and _is_snapshot(after_reset, operation_identity=fault_identity, payload=fault_payload)
            and after_reset["state"] == "NOT_FOUND",
        )
    )
    return FeasibilityReport(cases=tuple(results))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证实际 WMS Mock 北向交互合同")
    parser.add_argument("--base-url", default=os.getenv("WMS_NORTHBOUND_STUB_BASE_URL"))
    parser.add_argument("--operation-identity", choices=tuple(_OPERATION_SPECS))
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    parser.add_argument("--submit-timeout-seconds", type=float)
    parser.add_argument("--status-timeout-seconds", type=float)
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    if not args.base_url:
        raise SystemExit("需要 --base-url 或 WMS_NORTHBOUND_STUB_BASE_URL；不得将认证信息写入命令行。")
    startup = validate_wms_transport_configuration(settings_source=settings)
    client_timeout = max(
        args.timeout_seconds,
        args.submit_timeout_seconds or 0,
        args.status_timeout_seconds or 0,
    )
    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=httpx.Timeout(client_timeout),
        trust_env=False,
    ) as client:
        report = await run_probe(
            client,
            compiled_profile=startup.compiled_profile,
            operation_identity=args.operation_identity,
            request_timeout_seconds=args.timeout_seconds,
            submit_timeout_seconds=args.submit_timeout_seconds,
            status_timeout_seconds=args.status_timeout_seconds,
        )
    print(json.dumps({"passed": report.passed, "cases": [asdict(case) for case in report.cases]}, ensure_ascii=False))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
