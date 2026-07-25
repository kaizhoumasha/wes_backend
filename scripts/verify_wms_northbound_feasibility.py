"""通过实际 WMS Mock 的公开 HTTP 面执行脱敏北向合同探针。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from src.app.sys.canonical_dispatch import canonical_json_bytes, payload_sha256

_STATES = frozenset({"ACCEPTED", "PROCESSING", "COMPLETED", "REJECTED", "NOT_FOUND"})
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_OPAQUE_REFERENCE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_MAX_SAFE_RESPONSE_BYTES = 1024 * 1024
_MOCK_HMAC_SECRET_ENV = "MOCK_WMS_NORTHBOUND_HMAC_SECRET_V1"  # noqa: S105 - 环境变量名，不是密钥值。

_OPERATION_SPECS: dict[str, dict[str, Any]] = {
    "wms.inventory.confirm_inbound@v1": {
        "submit_path": "/api/wms/inventory/confirm-inbound",
        "payload": {
            "dispatch_key": "probe-confirm-inbound",
            "inbound_key": "inbound-probe-001",
            "material_code": "MATERIAL-001",
            "quantity": "1",
        },
        "result_fields": {"accepted", "dispatch_key", "reason_code", "source_version", "inbound_key", "document_no"},
        "rejection": "MATERIAL_BLOCKED",
    },
    "wms.fulfillment.full_box_exchange@v1": {
        "submit_path": "/api/wms/fulfillment/full-box-exchange",
        "payload": {
            "dispatch_key": "probe-full-box-exchange",
            "rack_id": "rack-probe-001",
            "empty_box_id": "empty-box-001",
            "full_box_id": "full-box-001",
        },
        "result_fields": {
            "accepted",
            "dispatch_key",
            "reason_code",
            "source_version",
            "rack_id",
            "empty_box_id",
            "full_box_id",
            "exchange_request_code",
        },
        "rejection": "RACK_LOCKED",
    },
    "wms.fulfillment.notify_pkg_binding@v1": {
        "submit_path": "/api/wms/fulfillment/package-binding",
        "payload": {
            "dispatch_key": "probe-package-binding",
            "package_id": "package-probe-001",
            "pallet_id": "pallet-probe-001",
            "station_code": "station-probe-001",
        },
        "result_fields": {
            "accepted",
            "dispatch_key",
            "reason_code",
            "source_version",
            "bound_at",
            "package_id",
            "pallet_id",
        },
        "rejection": "WMS_BUSINESS_REJECTED",
    },
}


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
    """单一总 deadline 覆盖 send、流式 body 与 close；超限立即关闭连接。"""

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
                async with asyncio.timeout_at(deadline):
                    await response.aclose()
            except TimeoutError:
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


def _contract_values(contract: object) -> dict[str, Any] | None:
    """仅接受实际 Mock 声明的可公开验证承诺参数。"""

    if not isinstance(contract, dict):
        return None
    required_positive = (
        "idempotency_retention_seconds",
        "status_visibility_sla_seconds",
        "max_response_bytes",
        "submit_deadline_seconds",
        "status_deadline_seconds",
    )
    values = {name: contract.get(name) for name in required_positive}
    credential_reference = contract.get("credential_reference")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values.values()):
        return None
    if not isinstance(credential_reference, str) or not credential_reference.startswith("secret://"):
        return None
    return {**values, "credential_reference": credential_reference}


def _is_snapshot(snapshot: object, *, operation_identity: str, payload: dict[str, Any]) -> bool:  # noqa: PLR0911
    """严格验证五态快照及对应 operation 的 typed completed result。"""

    if not isinstance(snapshot, dict) or set(snapshot) != {
        "state",
        "provider_reference",
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
            for field in ("provider_reference", "reason_code", "updated_at", "source_version", "result_payload")
        )
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
    spec = _OPERATION_SPECS[operation_identity]
    return (
        isinstance(result, dict)
        and set(result) == spec["result_fields"]
        and result["accepted"] is True
        and result["dispatch_key"] == payload["dispatch_key"]
        and result["reason_code"] is None
        and result["source_version"] == str(source_version)
    )


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
    *, secret: bytes, credential_reference: str, path: str, body: bytes, operation_identity: str, key: str
) -> dict[str, str]:
    timestamp = str(int(datetime.now(UTC).timestamp()))
    nonce = uuid4().hex
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
    operation_identity: str | None = None,
    request_timeout_seconds: float = 2.0,
) -> FeasibilityReport:
    """通过实际 Mock 的公开路由验收三类 typed EFFECT，绝不读取其内部状态。"""

    results: list[ProbeCaseResult] = []
    bootstrap_limit = 64 * 1024
    contract_response = await _request(
        client, "GET", "/northbound/contract", request_timeout_seconds=request_timeout_seconds
    )
    contract = _json_object(contract_response, max_response_bytes=bootstrap_limit)
    contract_values = _contract_values(contract)
    contract_ok = contract_response is not None and contract_response.status_code == 200 and contract_values is not None
    results.append(_result("public_contract_parameters", contract_ok))
    if contract_values is None:
        return FeasibilityReport(cases=tuple(results))

    secret = os.getenv(_MOCK_HMAC_SECRET_ENV, "").encode("utf-8")
    results.append(_result("mock_hmac_secret_available", bool(secret)))
    if not secret:
        return FeasibilityReport(cases=tuple(results))
    max_response_bytes = min(contract_values["max_response_bytes"], _MAX_SAFE_RESPONSE_BYTES)
    selected = (operation_identity,) if operation_identity else tuple(_OPERATION_SPECS)
    if any(identity not in _OPERATION_SPECS for identity in selected):
        results.append(_result("requested_operation_supported", False))
        return FeasibilityReport(cases=tuple(results))

    async def submit(identity: str, key: str, payload: dict[str, Any]) -> httpx.Response | None:
        spec = _OPERATION_SPECS[identity]
        body = canonical_json_bytes(payload)
        return await _request(
            client,
            "POST",
            spec["submit_path"],
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            content=body,
            headers=_submit_headers(
                secret=secret,
                credential_reference=contract_values["credential_reference"],
                path=spec["submit_path"],
                body=body,
                operation_identity=identity,
                key=key,
            ),
        )

    async def status(identity: str, key: str) -> httpx.Response | None:
        raw_path = "/northbound/operations/status?" + urlencode(
            (("operation_identity", identity), ("idempotency_key", key))
        )
        return await _request(
            client,
            "GET",
            raw_path,
            request_timeout_seconds=request_timeout_seconds,
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

    for identity in selected:
        spec = _OPERATION_SPECS[identity]
        payload = dict(spec["payload"])
        key = f"probe-{uuid4().hex}"
        first = await submit(identity, key, payload)
        first_body = _json_object(first, max_response_bytes=max_response_bytes)
        first_snapshot = first_body.get("data", {}).get("northbound_status") if first_body else None
        results.append(
            _result(
                f"{identity}:first_submit",
                first is not None
                and first.status_code == 202
                and _is_snapshot(first_snapshot, operation_identity=identity, payload=payload),
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
        completed_snapshot = completed_body.get("data", {}).get("northbound_status") if completed_body else None
        results.append(
            _result(
                f"{identity}:completed_replay",
                completed_replay is not None
                and completed_replay.status_code == 200
                and completed_snapshot == snapshots[-1],
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

        # Submit 仅返回 ACCEPTED 快照；最终状态只由后续 status 公开面提供，
        # callback 不具有终态权威。
        results.append(
            _result(
                f"{identity}:callback_hint_requires_status_authority",
                isinstance(first_snapshot, dict)
                and first_snapshot.get("state") == "ACCEPTED"
                and snapshots[-1].get("state") == "COMPLETED",
            )
        )

    fault_identity = selected[0]
    fault_key = f"probe-fault-{uuid4().hex}"
    fault_payload = dict(_OPERATION_SPECS[fault_identity]["payload"])
    await submit(fault_identity, fault_key, fault_payload)
    rate_configured = await configure_fault(status=429, retry_after=2)
    rate_limited = await status(fault_identity, fault_key)
    unavailable_configured = await configure_fault(status=503, max_response_bytes=1)
    unavailable = await status(fault_identity, fault_key)
    timeout_configured = await configure_fault(status=200, delay=max(request_timeout_seconds * 2, 0.05))
    timed_out = await status(fault_identity, fault_key)
    fault_reset = await _request(
        client,
        "POST",
        "/debug/reset",
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
    )
    results.append(
        _result(
            "fault_matrix_rate_limit_5xx_timeout_and_response_budget",
            rate_configured
            and rate_limited is not None
            and rate_limited.status_code == 429
            and _retry_after_is_valid(rate_limited.headers.get("Retry-After"))
            and unavailable_configured
            and unavailable is not None
            and unavailable.status_code == 503
            and _json_object(unavailable, max_response_bytes=max_response_bytes) is None
            and timeout_configured
            and timed_out is None
            and fault_reset is not None
            and fault_reset.status_code == 200,
        )
    )

    tampered_raw_path = "/northbound/operations/status?" + urlencode(
        (("operation_identity", fault_identity), ("idempotency_key", fault_key))
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
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    if not args.base_url:
        raise SystemExit("需要 --base-url 或 WMS_NORTHBOUND_STUB_BASE_URL；不得将认证信息写入命令行。")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(args.timeout_seconds)) as client:
        report = await run_probe(
            client, operation_identity=args.operation_identity, request_timeout_seconds=args.timeout_seconds
        )
    print(json.dumps({"passed": report.passed, "cases": [asdict(case) for case in report.cases]}, ensure_ascii=False))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
