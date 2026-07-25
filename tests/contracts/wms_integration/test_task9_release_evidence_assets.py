"""Task 9 联调发布证据、QUERY/EFFECT replay 与切换边界合同。"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.app.wms_integration.ports.confirm_inbound_operation import ConfirmInboundOperationRequest
from src.app.wms_integration.ports.effect_status import (
    ConfirmInboundResultIdentity,
    FullBoxExchangeResultIdentity,
    NotifyPackageBindingResultIdentity,
    WmsEffectStatusRequest,
    parse_wms_effect_status_snapshot,
)
from src.app.wms_integration.ports.full_box_exchange_operation import FullBoxExchangeOperationRequest
from src.app.wms_integration.ports.notify_pkg_binding_operation import NotifyPackageBindingOperationRequest
from src.app.wms_integration.services.http_transport import sign_wms_hmac_request

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "tests/fixtures/wms_provider_conformance"
RELEASE_EVIDENCE = ROOT / "docs/operations/wms-northbound-acceptance-and-cutover.md"
FEASIBILITY_REPORT = ROOT / "docs/operations/wms-northbound-feasibility-report.md"
SLO_CATALOG = ROOT / "docs/operations/northbound-operation-slo-catalog.md"
RUNBOOK = ROOT / "docs/runbooks/northbound-operation-observability.md"
INTERACTION_CONTRACT = ROOT / "docs/contracts/wms-northbound-interaction-contract.md"
OBSERVABILITY_CONTRACT = ROOT / "docs/contracts/observability-contract.md"
FEASIBILITY_PROBE = ROOT / "scripts/verify_wms_northbound_feasibility.py"

EFFECT_REPLAY_ASSETS = {
    "wms.inventory.confirm_inbound@v1": (
        FIXTURE_ROOT / "confirm_inbound_status_replay.v1.json",
        ConfirmInboundResultIdentity,
    ),
    "wms.fulfillment.full_box_exchange@v1": (
        FIXTURE_ROOT / "full_box_exchange_status_replay.v1.json",
        FullBoxExchangeResultIdentity,
    ),
    "wms.fulfillment.notify_pkg_binding@v1": (
        FIXTURE_ROOT / "notify_pkg_binding_status_replay.v1.json",
        NotifyPackageBindingResultIdentity,
    ),
}


def _asset_digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        {key: value for key, value in payload.items() if key != "digest"},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("operation_identity", "asset_path", "identity_type"),
    [
        (operation_identity, asset_path, identity_type)
        for operation_identity, (asset_path, identity_type) in EFFECT_REPLAY_ASSETS.items()
    ],
)
def test_each_effect_operation_has_deterministic_status_replay(
    operation_identity: str,
    asset_path: Path,
    identity_type: type,
) -> None:
    payload = json.loads(asset_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "wms-effect-status-replay.v1"
    assert payload["operation_identity"] == operation_identity
    assert payload["digest"] == _asset_digest(payload)
    assert [case["case_id"] for case in payload["cases"]] == [
        "accepted",
        "processing",
        "completed",
        "rejected",
        "not_found",
    ]

    request = WmsEffectStatusRequest(
        operation_identity=operation_identity,
        idempotency_key=payload["request"]["idempotency_key"],
        expected_result_identity=identity_type.model_validate(payload["request"]["expected_result_identity"]),
    )
    snapshots = [
        parse_wms_effect_status_snapshot(request=request, raw_response=case["response"]) for case in payload["cases"]
    ]

    assert [snapshot.state.value for snapshot in snapshots] == [
        "ACCEPTED",
        "PROCESSING",
        "COMPLETED",
        "REJECTED",
        "NOT_FOUND",
    ]
    assert snapshots[2].result is not None
    assert snapshots[2].result.accepted is True
    assert snapshots[4].source_version is None


def test_replay_assets_are_synthetic_and_do_not_contain_secret_material() -> None:
    paths = [
        FIXTURE_ROOT / "query_inventory_replay.v1.json",
        *(asset_path for asset_path, _identity_type in EFFECT_REPLAY_ASSETS.values()),
    ]
    forbidden = ("authorization", "bearer", "password", "credential", "secret", "signature")

    for path in paths:
        serialized = path.read_text(encoding="utf-8").lower()
        assert all(token not in serialized for token in forbidden), path
        assert "example" in serialized or "replay" in serialized, path


def test_release_evidence_keeps_real_acceptance_and_cutover_explicitly_blocked() -> None:
    content = RELEASE_EVIDENCE.read_text(encoding="utf-8")

    assert "实际开发 Mock 验证：`PASS/GO`（当前 P0 门禁已关闭）" in content
    assert "外部 WMS 联调验收模板：`PENDING`（后续，不阻塞当前 Mock 验收）" in content
    assert "外部 WMS 联调测试数据清理模板：`BLOCKED`" in content
    assert "外部 WMS 整体切换模板：`BLOCKED`" in content
    assert "不得把该结论替代未来外部 WMS 的联调验收" in content
    assert "不得预填外部确认人、验收时间或构建版本" in content
    assert "首个真实 EFFECT" in content
    assert all(operation_identity in content for operation_identity in EFFECT_REPLAY_ASSETS)


def test_mock_feasibility_go_is_backed_by_live_compose_and_active_wes_credential() -> None:
    report = FEASIBILITY_REPORT.read_text(encoding="utf-8")
    release = RELEASE_EVIDENCE.read_text(encoding="utf-8")

    assert "sha256:2b8f8ff7336213ce0292f25de6d656537b377fb10bcd520264435f92efcdc180" in report
    assert "tests/integration/test_wms_mock_northbound_live.py" in report
    assert "45 个 case 全部 `passed=true`" in report
    assert "`secret://wms/material-flow-sandbox-hmac@v2`" in release
    assert "Mock 专用 credential" in release


def test_active_operations_docs_use_submit_status_callback_facts_not_shadow_readiness() -> None:
    content = "\n".join(
        (
            SLO_CATALOG.read_text(encoding="utf-8"),
            RUNBOOK.read_text(encoding="utf-8"),
        )
    )

    assert "Shadow/readiness" not in content
    assert "最新 readiness" not in content
    assert "submit accepted/ambiguous/not-sent" in content
    assert "status query backlog" in content
    assert "callback hint" in content
    assert "不推断 WMS 内部" in content


def test_submit_wire_contract_matches_canonical_dispatch_without_binding_wrapper() -> None:
    contract = INTERACTION_CONTRACT.read_text(encoding="utf-8")
    probe_source = FEASIBILITY_PROBE.read_text(encoding="utf-8")

    assert "HTTP body 是 operation-specific typed payload 的 canonical JSON" in contract
    assert "`Idempotency-Key` 和 `X-WES-Operation-Identity` 是 HTTP header" in contract
    assert "frozen binding 仅为 WES 内部持久化事实，绝不进入 HTTP body、header 或 query" in contract
    assert "method → path → timestamp → nonce → payload hash → operation identity → idempotency key" in contract
    assert '"frozen_binding"' not in probe_source
    assert '"canonical_payload"' not in probe_source
    assert '"Idempotency-Key"' in probe_source
    assert '"X-WES-Operation-Identity"' in probe_source


def test_interaction_contract_freezes_each_effect_wire_body_schema() -> None:
    contract = INTERACTION_CONTRACT.read_text(encoding="utf-8")

    expected_sections = {
        "### `wms.inventory.confirm_inbound@v1`": {
            "`dispatch_key` | string | 是 | 1..240",
            "`inbound_key` | string | 是 | 1..120",
            "`material_code` | string | 是 | 1..120",
            "`quantity` | decimal string | 是 | `> 0`",
            "`warehouse_code` | string | 否 | 1..120",
            "`owner_code` | string | 否 | 1..120",
            "`lot_no` | string | 否 | 1..120",
            "不发送：`workline_id`、`session_id`、`trace_id`",
        },
        "### `wms.fulfillment.full_box_exchange@v1`": {
            "`dispatch_key` | string | 是 | 1..240",
            "`rack_id` | string | 是 | 1..120",
            "`empty_box_id` | string | 是 | 1..120",
            "`full_box_id` | string | 是 | 1..120",
            "不发送：`provider_code`、`workline_id`、`session_id`、`trace_id`",
        },
        "### `wms.fulfillment.notify_pkg_binding@v1`": {
            "`dispatch_key` | string | 是 | 1..240",
            "`package_id` | string | 是 | 1..120",
            "`pallet_id` | string | 是 | 1..120",
            "`station_code` | string | 是 | 1..120",
            "不发送：`provider_code`、`workline_id`、`session_id`、`trace_id`",
        },
    }
    for heading, facts in expected_sections.items():
        assert heading in contract
        section = contract.split(heading, maxsplit=1)[1].split("### ", maxsplit=1)[0]
        assert all(fact in section for fact in facts)
        assert "当前无枚举字段" in section


def test_documented_wire_fields_partition_each_request_model() -> None:
    partitions = (
        (
            ConfirmInboundOperationRequest,
            {
                "dispatch_key",
                "inbound_key",
                "material_code",
                "quantity",
                "warehouse_code",
                "owner_code",
                "lot_no",
            },
            {"workline_id", "session_id", "trace_id"},
        ),
        (
            FullBoxExchangeOperationRequest,
            {"dispatch_key", "rack_id", "empty_box_id", "full_box_id"},
            {"provider_code", "workline_id", "session_id", "trace_id"},
        ),
        (
            NotifyPackageBindingOperationRequest,
            {"dispatch_key", "package_id", "pallet_id", "station_code"},
            {"provider_code", "workline_id", "session_id", "trace_id"},
        ),
    )

    for request_model, wire_fields, internal_fields in partitions:
        assert set(request_model.model_fields) == wire_fields | internal_fields


def test_contract_separates_submit_and_status_hmac_wire_schemes() -> None:
    contract = INTERACTION_CONTRACT.read_text(encoding="utf-8")
    release = RELEASE_EVIDENCE.read_text(encoding="utf-8")

    submit = contract.split("### Submit 请求认证", maxsplit=1)[1].split("### Status query 请求认证", maxsplit=1)[0]
    status = contract.split("### Status query 请求认证", maxsplit=1)[1].split("## ", maxsplit=1)[0]
    assert all(
        header in submit
        for header in (
            "`X-WES-Content-SHA256`",
            "`X-WES-Credential-Reference`",
            "`X-WES-Nonce`",
            "`X-WES-Signature`",
            "`X-WES-Signature-Algorithm`",
            "`X-WES-Timestamp`",
            "`X-WES-Operation-Identity`",
            "`Idempotency-Key`",
        )
    )
    assert "method → path → timestamp → nonce → payload hash → operation identity → idempotency key" in submit
    assert all(
        header in status
        for header in (
            "`X-WMS-Content-SHA256`",
            "`X-WMS-Credential-Reference`",
            "`X-WMS-Nonce`",
            "`X-WMS-Signature`",
            "`X-WMS-Signature-Algorithm`",
            "`X-WMS-Timestamp`",
        )
    )
    assert "method → path → timestamp → nonce → body hash" in status
    assert "GET 的 request body 为空 bytes" in status
    assert "TLS 仅提供传输保护" in contract
    assert "HMAC-SHA256 是当前应用层认证" in contract
    assert "Bearer/OAuth 不是当前合同" in contract
    assert "Submit 签名证据" in release
    assert "Status query 签名证据" in release
    assert "七项 canonical" in release
    assert "五项 canonical" in release


def test_status_hmac_uses_x_wms_headers_and_five_exact_raw_fields() -> None:
    secret = b"status-secret"
    request = httpx.Request(
        "GET",
        "https://wms.example/northbound/operations/status"
        "?operation_identity=wms.fulfillment.notify_pkg_binding%40v1&idempotency_key=key-001",
    )

    sign_wms_hmac_request(
        request,
        credential_reference="wms-status-ref",
        auth_scheme="HMAC_SHA256",
        secret=secret,
        now=lambda: datetime(2026, 7, 24, 8, 0, tzinfo=UTC),
        nonce_factory=lambda: "status-nonce",
    )

    empty_body_hash = hashlib.sha256(b"").hexdigest()
    canonical = "\n".join(
        (
            "GET",
            request.url.raw_path.decode("ascii"),
            str(int(datetime(2026, 7, 24, 8, 0, tzinfo=UTC).timestamp())),
            "status-nonce",
            empty_body_hash,
        )
    )
    assert request.headers["X-WMS-Content-SHA256"] == empty_body_hash
    assert request.headers["X-WMS-Credential-Reference"] == "wms-status-ref"
    assert request.headers["X-WMS-Signature-Algorithm"] == "HMAC_SHA256"
    assert request.headers["X-WMS-Signature"] == hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    assert not any(header.lower().startswith("x-wes-") for header in request.headers)


def test_feasibility_probe_hashes_exact_raw_body_and_declares_auth_limit() -> None:
    probe_source = FEASIBILITY_PROBE.read_text(encoding="utf-8")
    report = (ROOT / "docs/operations/wms-northbound-feasibility-report.md").read_text(encoding="utf-8")

    assert "canonical_json_bytes" in probe_source
    assert "payload_sha256" in probe_source
    assert '"X-WES-Content-SHA256"' in probe_source
    assert "canonical_payload" not in probe_source
    assert "_submit_headers" in probe_source
    assert "`X-WES-*` 七项" in report
    assert "已验证 Submit 七项与 Status query 五项" in report


def test_observability_docs_distinguish_current_signals_from_target_cutover_evidence() -> None:
    observability = OBSERVABILITY_CONTRACT.read_text(encoding="utf-8")
    slo = SLO_CATALOG.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")
    release = RELEASE_EVIDENCE.read_text(encoding="utf-8")

    stable_signals, target_mapping = observability.split("## WMS EFFECT 目标采集口径", maxsplit=1)
    assert "`wms_effect.submit`" not in stable_signals
    assert "`wms_effect.status_query`" not in stable_signals
    assert "`wms_effect.submit`" in target_mapping
    assert "尚未成为当前 `RuntimeObservabilityRegistry` 的可执行 signal" in target_mapping
    assert "当前只读 API 不返回这些目标字段" in slo
    assert "当前已配置告警" in slo
    assert "目标告警候选" in slo
    assert "目标口径不是已存在的生产指标" in runbook
    assert "外部 WMS 观测映射与采集模板：`BLOCKED`" in release
    assert "mock/replay 不能关闭该门禁" in release
