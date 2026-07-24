"""Task 9 联调发布证据、QUERY/EFFECT replay 与切换边界合同。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.app.wms_integration.ports.effect_status import (
    ConfirmInboundResultIdentity,
    FullBoxExchangeResultIdentity,
    NotifyPackageBindingResultIdentity,
    WmsEffectStatusRequest,
    parse_wms_effect_status_snapshot,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = ROOT / "tests/fixtures/wms_provider_conformance"
RELEASE_EVIDENCE = ROOT / "docs/operations/wms-northbound-acceptance-and-cutover.md"
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

    assert "开发 mock 验证：`PASS`" in content
    assert "真实 WMS 联调验收：`PENDING`" in content
    assert "联调测试数据清理：`BLOCKED`" in content
    assert "整体切换：`BLOCKED`" in content
    assert "不得用 mock 结果替代真实 WMS 联调验收" in content
    assert "不得预填确认人、验收时间、WMS 构建版本" in content
    assert "首个真实 EFFECT" in content
    assert all(operation_identity in content for operation_identity in EFFECT_REPLAY_ASSETS)


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
    assert "观测映射与采集验证：`BLOCKED`" in release
    assert "mock/replay 不能关闭该门禁" in release
