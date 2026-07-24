"""对 WMS 最小联调 stub 执行脱敏的北向合同黑盒探针。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import uuid4

import httpx

from src.app.sys.canonical_dispatch import canonical_json_bytes, payload_sha256

_STATES = frozenset({"ACCEPTED", "PROCESSING", "COMPLETED", "REJECTED", "NOT_FOUND"})
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_OPAQUE_REFERENCE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_MAX_SAFE_RESPONSE_BYTES = 1024 * 1024
_REJECTED_REASON_CODES = {
    "wms.fulfillment.notify_pkg_binding@v1": frozenset({"WMS_BUSINESS_REJECTED"}),
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


def _payload(*, fingerprint: str = "payload-a") -> dict[str, Any]:
    return {
        "dispatch_key": "dispatch-001",
        "package_id": "package-001",
        "pallet_id": "pallet-001",
        "station_code": "station-a" if fingerprint == "payload-a" else "station-b",
    }


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
            response._content = bytes(content)  # 让后续严格 JSON 验证只读取已封顶内容
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


def _positive_contract_values(contract: object) -> dict[str, int] | None:
    """远端合同数值先完整验型，避免字符串/bool 参与算术或出现在输出中。"""

    required = (
        "retention_seconds",
        "wes_max_confirmation_age_seconds",
        "safety_margin_seconds",
        "visibility_sla_seconds",
        "not_found_grace_period_seconds",
        "max_response_bytes",
    )
    if not isinstance(contract, dict):
        return None
    values = {name: contract.get(name) for name in required}
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values.values()):
        return None
    return values


def _is_snapshot(snapshot: object) -> bool:  # noqa: PLR0911 - 每个 return 对应一个合同失败边界
    """严格验证 wire snapshot 的字段 allowlist、类型和状态约束。"""

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
    return (
        isinstance(result, dict)
        and set(result)
        == {
            "accepted",
            "bound_at",
            "dispatch_key",
            "package_id",
            "pallet_id",
            "reason_code",
            "source_version",
        }
        and result["accepted"] is True
        and result["dispatch_key"] == "dispatch-001"
        and result["package_id"] == "package-001"
        and result["pallet_id"] == "pallet-001"
        and result["reason_code"] is None
        and result["source_version"] == str(source_version)
    )


def _stable_error(
    response: httpx.Response | None, *, status_code: int, error_code: str, max_response_bytes: int
) -> bool:
    payload = _json_object(response, max_response_bytes=max_response_bytes)
    return (
        response is not None
        and response.status_code == status_code
        and isinstance(payload, dict)
        and payload.get("detail") == {"error_code": error_code}
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


async def run_probe(
    client: httpx.AsyncClient,
    *,
    operation_identity: str,
    request_timeout_seconds: float = 2.0,
) -> FeasibilityReport:
    """通过公开 HTTP 面验证 WMS 开发 stub 的最小合同。"""

    results: list[ProbeCaseResult] = []
    bootstrap_limit = 64 * 1024
    contract_response = await _request(
        client, "GET", "/northbound/contract", request_timeout_seconds=request_timeout_seconds
    )
    contract = _json_object(contract_response, max_response_bytes=bootstrap_limit)
    contract_values = _positive_contract_values(contract)
    max_response_bytes = min(contract_values["max_response_bytes"], _MAX_SAFE_RESPONSE_BYTES) if contract_values else 0
    contract_ok = (
        contract_response is not None
        and contract_response.status_code == 200
        and contract_values is not None
        and contract_values["retention_seconds"]
        >= contract_values["wes_max_confirmation_age_seconds"] + contract_values["safety_margin_seconds"]
        and contract_values["visibility_sla_seconds"] <= contract_values["not_found_grace_period_seconds"]
    )
    results.append(_result("retention_and_visibility_boundaries", contract_ok))
    safe_values = contract_values or {
        "retention_seconds": 0,
        "visibility_sla_seconds": 0,
        "not_found_grace_period_seconds": 0,
    }

    async def submit(
        key: str,
        *,
        scenario: str = "success",
        fingerprint: str = "payload-a",
        content_hash: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response | None:
        body = canonical_json_bytes(_payload(fingerprint=fingerprint))
        payload_hash = payload_sha256(body)
        wire_headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": key,
            "X-Probe-Scenario": scenario,
            "X-WES-Content-SHA256": content_hash or payload_hash,
            "X-WES-Operation-Identity": operation_identity,
            **(headers or {}),
        }
        return await _request(
            client,
            "POST",
            "/northbound/operations",
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            content=body,
            headers=wire_headers,
        )

    async def status(key: str, *, headers: dict[str, str] | None = None) -> httpx.Response | None:
        return await _request(
            client,
            "GET",
            "/northbound/operations/status",
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            params={"operation_identity": operation_identity, "idempotency_key": key},
            headers=headers,
        )

    async def effect_count(key: str) -> int | None:
        response = await _request(
            client,
            "GET",
            "/northbound/operations/effects",
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            params={"operation_identity": operation_identity, "idempotency_key": key},
        )
        payload = _json_object(response, max_response_bytes=max_response_bytes)
        value = payload.get("effect_count") if payload else None
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    async def advance(seconds: int) -> bool:
        response = await _request(
            client,
            "POST",
            "/northbound/test-clock/advance",
            request_timeout_seconds=request_timeout_seconds,
            max_response_bytes=max_response_bytes,
            json={"seconds": seconds},
        )
        return (
            response is not None
            and response.status_code == 200
            and _json_object(response, max_response_bytes=max_response_bytes) is not None
        )

    normal_key = f"probe-{uuid4().hex}"
    first = await submit(normal_key)
    first_snapshot = _json_object(first, max_response_bytes=max_response_bytes)
    results.append(
        _result("first_submit", first is not None and first.status_code == 202 and _is_snapshot(first_snapshot))
    )

    in_progress = await submit(normal_key)
    results.append(
        _result(
            "in_progress_replay",
            _stable_error(
                in_progress,
                status_code=409,
                error_code="IDEMPOTENCY_REQUEST_IN_PROGRESS",
                max_response_bytes=max_response_bytes,
            ),
        )
    )

    snapshots = [_json_object(await status(normal_key), max_response_bytes=max_response_bytes) for _ in range(3)]
    results.append(
        _result(
            "source_version_and_typed_result",
            all(_is_snapshot(snapshot) for snapshot in snapshots)
            and [snapshot["state"] for snapshot in snapshots] == ["ACCEPTED", "PROCESSING", "COMPLETED"]
            and snapshots[0]["source_version"] < snapshots[1]["source_version"] < snapshots[2]["source_version"],
        )
    )

    completed_replay = await submit(normal_key)
    completed_snapshot = _json_object(completed_replay, max_response_bytes=max_response_bytes)
    results.append(
        _result(
            "completed_replay",
            completed_replay is not None
            and completed_replay.status_code == 200
            and _is_snapshot(completed_snapshot)
            and completed_snapshot == snapshots[-1],
        )
    )

    conflict = await submit(normal_key, fingerprint="payload-b")
    results.append(
        _result(
            "idempotency_conflict",
            _stable_error(
                conflict,
                status_code=422,
                error_code="IDEMPOTENCY_CONFLICT",
                max_response_bytes=max_response_bytes,
            ),
        )
    )

    tampered_key = f"probe-tampered-{uuid4().hex}"
    tampered = await submit(tampered_key, content_hash="0" * 64)
    results.append(
        _result(
            "content_hash_tamper_rejected",
            _stable_error(
                tampered,
                status_code=422,
                error_code="CONTENT_HASH_MISMATCH",
                max_response_bytes=max_response_bytes,
            )
            and await effect_count(tampered_key) == 0,
        )
    )

    rejected_key = f"probe-rejected-{uuid4().hex}"
    await submit(rejected_key, scenario="rejected")
    rejected = _json_object(await status(rejected_key), max_response_bytes=max_response_bytes)
    rejected_replay = _json_object(await status(rejected_key), max_response_bytes=max_response_bytes)
    results.append(
        _result(
            "rejected_reason_code",
            _is_snapshot(rejected)
            and rejected.get("state") == "REJECTED"
            and rejected == rejected_replay
            and rejected.get("reason_code") in _REJECTED_REASON_CODES.get(operation_identity, frozenset()),
        )
    )

    missing = _json_object(await status(f"missing-{uuid4().hex}"), max_response_bytes=max_response_bytes)
    results.append(_result("not_found_empty_version", _is_snapshot(missing) and missing.get("state") == "NOT_FOUND"))

    not_arrived_key = f"probe-not-arrived-{uuid4().hex}"
    first_not_arrived = await submit(not_arrived_key, headers={"X-First-Attempt-Dropped": "true"})
    retry_after_not_arrived = await submit(not_arrived_key)
    results.append(
        _result(
            "first_submit_not_arrived_retry_creates",
            first_not_arrived is None
            and retry_after_not_arrived is not None
            and retry_after_not_arrived.status_code == 202
            and await effect_count(not_arrived_key) == 1,
        )
    )

    recovery_key = f"probe-recovery-{uuid4().hex}"
    await submit(recovery_key, scenario="recoverable_not_found")
    no_visible_before_recovery = _json_object(await status(recovery_key), max_response_bytes=max_response_bytes)
    recovery_after_grace = await advance(safe_values["not_found_grace_period_seconds"] + 1)
    controlled_replay = await submit(recovery_key, scenario="recoverable_not_found")
    results.append(
        _result(
            "controlled_recovery_replay_preserves_wire_identity",
            _is_snapshot(no_visible_before_recovery)
            and no_visible_before_recovery.get("state") == "NOT_FOUND"
            and recovery_after_grace
            and _stable_error(
                controlled_replay,
                status_code=409,
                error_code="IDEMPOTENCY_REQUEST_IN_PROGRESS",
                max_response_bytes=max_response_bytes,
            )
            and await effect_count(recovery_key) == 1,
        )
    )

    visible_missing_key = f"probe-visible-missing-{uuid4().hex}"
    await submit(visible_missing_key, scenario="visible_then_missing")
    previously_visible = _json_object(await status(visible_missing_key), max_response_bytes=max_response_bytes)
    missing_after_visible = _json_object(await status(visible_missing_key), max_response_bytes=max_response_bytes)
    results.append(
        _result(
            "visible_then_not_found_requires_reconciliation",
            _is_snapshot(previously_visible)
            and previously_visible.get("state") == "ACCEPTED"
            and _is_snapshot(missing_after_visible)
            and missing_after_visible.get("state") == "NOT_FOUND"
            and await effect_count(visible_missing_key) == 1,
        )
    )

    not_visible_key = f"probe-not-visible-{uuid4().hex}"
    await submit(not_visible_key, scenario="not_visible")
    missing_before_visible = _json_object(await status(not_visible_key), max_response_bytes=max_response_bytes)
    replay_while_not_visible = await submit(not_visible_key, scenario="not_visible")
    visibility_elapsed = await advance(safe_values["visibility_sla_seconds"])
    visible_after_replay = _json_object(await status(not_visible_key), max_response_bytes=max_response_bytes)
    results.append(
        _result(
            "accepted_not_visible_replay_has_one_effect",
            _is_snapshot(missing_before_visible)
            and missing_before_visible.get("state") == "NOT_FOUND"
            and _stable_error(
                replay_while_not_visible,
                status_code=409,
                error_code="IDEMPOTENCY_REQUEST_IN_PROGRESS",
                max_response_bytes=max_response_bytes,
            )
            and visibility_elapsed
            and _is_snapshot(visible_after_replay)
            and visible_after_replay.get("state") == "ACCEPTED"
            and await effect_count(not_visible_key) == 1,
        )
    )

    retention_key = f"probe-retention-{uuid4().hex}"
    await submit(retention_key, scenario="success")
    for _ in range(3):
        await status(retention_key)
    within_retention = await submit(retention_key, scenario="success")
    before_retention_elapsed = await advance(max(safe_values["retention_seconds"] - 1, 0))
    before_retention_replay = await submit(retention_key, scenario="success")
    results.append(
        _result(
            "retention_boundary_observed",
            within_retention is not None
            and within_retention.status_code == 200
            and before_retention_elapsed
            and before_retention_replay is not None
            and before_retention_replay.status_code == 200
            and await effect_count(retention_key) == 1,
        )
    )

    rate_limited_delta = await status(normal_key, headers={"X-Probe-Fault": "rate_limit_delta"})
    rate_limited_date = await status(normal_key, headers={"X-Probe-Fault": "rate_limit_date"})
    results.append(
        _result(
            "rate_limit_retry_after",
            rate_limited_delta is not None
            and rate_limited_delta.status_code == 429
            and _retry_after_is_valid(rate_limited_delta.headers.get("Retry-After"))
            and rate_limited_date is not None
            and rate_limited_date.status_code == 429
            and _retry_after_is_valid(rate_limited_date.headers.get("Retry-After")),
        )
    )

    unavailable = await status(normal_key, headers={"X-Probe-Fault": "unavailable"})
    results.append(
        _result(
            "wms_5xx_shape",
            _stable_error(
                unavailable,
                status_code=503,
                error_code="TEMPORARILY_UNAVAILABLE",
                max_response_bytes=max_response_bytes,
            ),
        )
    )

    timed_out = await status(normal_key, headers={"X-Probe-Fault": "timeout"})
    results.append(_result("status_query_timeout", timed_out is None))

    oversized = await _request(
        client,
        "GET",
        "/northbound/test/oversized-response",
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
    )
    results.append(
        _result("maximum_response_body", _json_object(oversized, max_response_bytes=max_response_bytes) is None)
    )

    return FeasibilityReport(cases=tuple(results))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 WMS 北向最小交互合同")
    parser.add_argument("--base-url", default=os.getenv("WMS_NORTHBOUND_STUB_BASE_URL"))
    parser.add_argument("--operation-identity", default="wms.fulfillment.notify_pkg_binding@v1")
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    if not args.base_url:
        raise SystemExit("需要 --base-url 或 WMS_NORTHBOUND_STUB_BASE_URL；不得将认证信息写入命令行。")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(args.timeout_seconds)) as client:
        report = await run_probe(
            client,
            operation_identity=args.operation_identity,
            request_timeout_seconds=args.timeout_seconds,
        )
    print(json.dumps({"passed": report.passed, "cases": [asdict(case) for case in report.cases]}, ensure_ascii=False))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
