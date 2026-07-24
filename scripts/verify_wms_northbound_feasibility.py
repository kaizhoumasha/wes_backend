"""对 WMS 最小联调 stub 执行北向合同黑盒探针。

本脚本不导入 WES 生产 adapter，也不打印认证信息或任意响应 body。运行时只需提供
``WMS_NORTHBOUND_STUB_BASE_URL``；开发阶段默认指向由 WMS mock 暴露的联调 stub。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

import httpx


@dataclass(frozen=True)
class ProbeCaseResult:
    """一条脱敏的合同断言结果。"""

    case_id: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class FeasibilityReport:
    """可写入可行性报告的最小探针输出。"""

    cases: tuple[ProbeCaseResult, ...]

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)


def _payload(*, scenario: str = "success", fingerprint: str = "payload-a") -> dict[str, Any]:
    return {
        "scenario": scenario,
        "fingerprint": fingerprint,
        "dispatch_key": "dispatch-001",
        "correlation_id": "correlation-001",
    }


def _submit_body(operation_identity: str, idempotency_key: str, canonical_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation_identity": operation_identity,
        "idempotency_key": idempotency_key,
        "canonical_payload": canonical_payload,
    }


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    request_timeout_seconds: float,
    **kwargs: Any,
) -> httpx.Response:
    """以显式 deadline 调用 HTTP，避免 ASGI 测试 transport 忽略 client timeout。"""

    return await asyncio.wait_for(
        client.request(method, path, **kwargs),
        timeout=request_timeout_seconds,
    )


def _snapshot_is_visible(snapshot: dict[str, Any]) -> bool:
    return snapshot.get("state") in {"ACCEPTED", "PROCESSING", "COMPLETED", "REJECTED"}


def _valid_snapshot(snapshot: dict[str, Any]) -> bool:
    state = snapshot.get("state")
    if state not in {"ACCEPTED", "PROCESSING", "COMPLETED", "REJECTED", "NOT_FOUND"}:
        return False
    if state == "NOT_FOUND":
        return snapshot.get("source_version") is None and snapshot.get("updated_at") is None
    if not isinstance(snapshot.get("source_version"), int) or snapshot["source_version"] < 0:
        return False
    return isinstance(snapshot.get("provider_reference"), str) and bool(snapshot["provider_reference"])


def _completed_result_is_typed(snapshot: dict[str, Any]) -> bool:
    result = snapshot.get("result_payload")
    if snapshot.get("state") != "COMPLETED" or not isinstance(result, dict):
        return False
    return (
        result.get("accepted") is True
        and result.get("dispatch_key") == "dispatch-001"
        and result.get("correlation_id") == "correlation-001"
        and result.get("source_version") == snapshot.get("source_version")
    )


def _result(case_id: str, passed: bool, detail: str) -> ProbeCaseResult:
    """只输出状态、稳定错误码与数值，避免泄漏响应内容。"""

    return ProbeCaseResult(case_id=case_id, passed=passed, detail=detail)


async def run_probe(
    client: httpx.AsyncClient,
    *,
    operation_identity: str,
    request_timeout_seconds: float = 2.0,
) -> FeasibilityReport:
    """通过 HTTP 观察最小 WMS stub 是否满足提交/查询合同。"""

    results: list[ProbeCaseResult] = []

    async def submit(
        key: str,
        *,
        scenario: str = "success",
        fingerprint: str = "payload-a",
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return await _request(
            client,
            "POST",
            "/northbound/operations",
            request_timeout_seconds=request_timeout_seconds,
            json=_submit_body(operation_identity, key, _payload(scenario=scenario, fingerprint=fingerprint)),
            headers=headers,
        )

    async def status(key: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        return await _request(
            client,
            "GET",
            "/northbound/operations/status",
            request_timeout_seconds=request_timeout_seconds,
            params={"operation_identity": operation_identity, "idempotency_key": key},
            headers=headers,
        )

    normal_key = f"probe-{uuid4().hex}"
    first = await submit(normal_key)
    results.append(_result("first_submit", first.status_code == 202, f"http_status={first.status_code}"))

    in_progress = await submit(normal_key)
    results.append(
        _result(
            "in_progress_replay",
            in_progress.status_code == 409
            and in_progress.json().get("detail", {}).get("error_code") == "IDEMPOTENCY_REQUEST_IN_PROGRESS",
            f"http_status={in_progress.status_code}; error_code={in_progress.json().get('detail', {}).get('error_code')}",
        )
    )

    snapshots = [(await status(normal_key)).json() for _ in range(3)]
    versions = [snapshot.get("source_version") for snapshot in snapshots]
    results.append(
        _result(
            "source_version_and_typed_result",
            all(_valid_snapshot(snapshot) for snapshot in snapshots)
            and versions == sorted(versions)
            and len(set(versions)) == len(versions)
            and _completed_result_is_typed(snapshots[-1]),
            f"states={','.join(str(snapshot.get('state')) for snapshot in snapshots)}; versions={versions}",
        )
    )

    completed_replay = await submit(normal_key)
    completed_snapshot = completed_replay.json()
    results.append(
        _result(
            "completed_replay",
            completed_replay.status_code == 200 and _completed_result_is_typed(completed_snapshot),
            f"http_status={completed_replay.status_code}; state={completed_snapshot.get('state')}",
        )
    )

    conflict = await submit(normal_key, fingerprint="payload-b")
    results.append(
        _result(
            "idempotency_conflict",
            conflict.status_code == 422
            and conflict.json().get("detail", {}).get("error_code") == "IDEMPOTENCY_CONFLICT",
            f"http_status={conflict.status_code}; error_code={conflict.json().get('detail', {}).get('error_code')}",
        )
    )

    rejected_key = f"probe-rejected-{uuid4().hex}"
    await submit(rejected_key, scenario="rejected")
    rejected = (await status(rejected_key)).json()
    results.append(
        _result(
            "rejected_reason_code",
            _valid_snapshot(rejected)
            and rejected.get("state") == "REJECTED"
            and isinstance(rejected.get("reason_code"), str)
            and bool(rejected["reason_code"])
            and rejected.get("result_payload") is None,
            f"state={rejected.get('state')}; reason_code={rejected.get('reason_code')}",
        )
    )

    missing = (await status(f"missing-{uuid4().hex}")).json()
    results.append(
        _result(
            "not_found_empty_version",
            _valid_snapshot(missing) and missing.get("state") == "NOT_FOUND",
            f"state={missing.get('state')}; source_version={missing.get('source_version')}",
        )
    )

    not_arrived_key = f"probe-not-arrived-{uuid4().hex}"
    first_not_arrived = await submit(not_arrived_key, headers={"X-First-Attempt-Dropped": "true"})
    retry_after_not_arrived = await submit(not_arrived_key)
    results.append(
        _result(
            "first_submit_not_arrived_retry_creates",
            first_not_arrived.status_code == 504 and retry_after_not_arrived.status_code == 202,
            f"first_http_status={first_not_arrived.status_code}; retry_http_status={retry_after_not_arrived.status_code}",
        )
    )

    not_visible_key = f"probe-not-visible-{uuid4().hex}"
    await submit(not_visible_key, scenario="not_visible")
    missing_before_visible = (await status(not_visible_key)).json()
    replay_while_not_visible = await submit(not_visible_key, scenario="not_visible")
    visible_after_replay = (await status(not_visible_key)).json()
    results.append(
        _result(
            "accepted_not_visible_replay_has_no_second_effect",
            missing_before_visible.get("state") == "NOT_FOUND"
            and replay_while_not_visible.status_code == 409
            and visible_after_replay.get("state") in {"ACCEPTED", "PROCESSING"}
            and visible_after_replay.get("provider_reference") == "mock-provider-ref-001",
            f"first_state={missing_before_visible.get('state')}; replay_http_status={replay_while_not_visible.status_code}; visible_state={visible_after_replay.get('state')}",
        )
    )

    commitments_response = await _request(
        client,
        "GET",
        "/northbound/contract",
        request_timeout_seconds=request_timeout_seconds,
    )
    commitments = commitments_response.json()
    results.append(
        _result(
            "retention_and_visibility_commitments",
            commitments_response.status_code == 200
            and commitments.get("retention_seconds", -1)
            >= commitments.get("wes_max_confirmation_age_seconds", 0) + commitments.get("safety_margin_seconds", 0)
            and commitments.get("visibility_sla_seconds", -1) <= commitments.get("not_found_grace_period_seconds", -1)
            and commitments.get("max_response_bytes", 0) > 0,
            f"http_status={commitments_response.status_code}; retention_seconds={commitments.get('retention_seconds')}; visibility_sla_seconds={commitments.get('visibility_sla_seconds')}",
        )
    )

    rate_limited = await status(normal_key, headers={"X-Probe-Fault": "rate_limit"})
    results.append(
        _result(
            "rate_limit_retry_after",
            rate_limited.status_code == 429 and rate_limited.headers.get("Retry-After", "").isdigit(),
            f"http_status={rate_limited.status_code}; retry_after={rate_limited.headers.get('Retry-After')}",
        )
    )

    unavailable = await status(normal_key, headers={"X-Probe-Fault": "unavailable"})
    results.append(
        _result(
            "wms_5xx_shape",
            unavailable.status_code == 503
            and unavailable.json().get("detail", {}).get("error_code") == "TEMPORARILY_UNAVAILABLE",
            f"http_status={unavailable.status_code}; error_code={unavailable.json().get('detail', {}).get('error_code')}",
        )
    )

    try:
        await status(normal_key, headers={"X-Probe-Fault": "timeout"})
    except TimeoutError:
        timeout_passed = True
    else:
        timeout_passed = False
    results.append(_result("status_query_timeout", timeout_passed, f"timeout_observed={timeout_passed}"))

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
