"""Runtime observability and toggle governance documentation contracts."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_observability_contract_declares_stable_runtime_signals() -> None:
    path = REPO_ROOT / "docs" / "contracts" / "observability-contract.md"
    text = path.read_text(encoding="utf-8")

    for signal in (
        "callback.normalize",
        "runtime_inbox.claim_batch",
        "runtime_inbox.processing",
        "runtime_inbox.lease_reclaim",
        "runtime_inbox.fencing_reject",
        "runtime_inbox.resource_wait",
        "runtime_inbox.dead_letter",
        "runtime_intent.dispatch",
        "device_command.ack",
        "device_command.dispatch_policy",
        "device_command.result",
        "wms_breaker.transition",
        "wms_evidence.persistence_failure",
    ):
        assert signal in text
    for attribute in (
        "trace_id",
        "correlation_id",
        "provider_code",
        "operation_kind",
        "command_code",
        "device_code",
        "inbox_id",
        "evidence_key",
        "policy_decision",
        "dispatch_allowed",
        "runtime_hold_required",
        "reason_code",
        "claimed_count",
        "duration_ms",
        "reclaimed_count",
        "target_state",
    ):
        assert attribute in text
    assert "RuntimeOpenTelemetryBridge" in text
    assert "RuntimeOpenTelemetryHttpExporter" in text
    assert "WES_RUNTIME_OTEL_ENABLED" in text
    assert "WES_RUNTIME_OTEL_ENDPOINT" in text


def test_runtime_toggle_governance_blocks_security_bypass() -> None:
    path = REPO_ROOT / "docs" / "contracts" / "runtime-toggle-governance.md"
    text = path.read_text(encoding="utf-8")

    for field in ("owner", "expiry", "scope", "default", "rollback", "test_matrix"):
        assert field in text
    for forbidden in ("HMAC", "nonce", "idempotency", "RuntimeHold", "evidence"):
        assert forbidden in text


def test_runtime_toggle_governance_declares_release_gate_entrypoint() -> None:
    path = REPO_ROOT / "docs" / "contracts" / "runtime-toggle-governance.md"
    text = path.read_text(encoding="utf-8")

    for token in (
        "RuntimeToggleReleaseGate",
        "runtime-toggle-release",
        "WES_RUNTIME_TOGGLE_PASSED_CHECKS",
        "--passed-check",
    ):
        assert token in text


def test_runtime_current_docs_match_execution_session_and_generated_plugin_contracts() -> None:
    current_docs = {
        path.relative_to(REPO_ROOT): path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "docs").rglob("*.md")
        if "archive" not in path.relative_to(REPO_ROOT / "docs").parts
    }
    file_index = current_docs[Path("docs/architecture/file_index.md")]

    for false_uniqueness in (
        "按 `trace_id`/`session_id`/`business_key` 唯一",
        "按 `trace_id` / `business_key` 唯一",
        "`workline_id` + `business_key` 业务唯一",
    ):
        assert all(false_uniqueness not in text for text in current_docs.values())
    for legacy_decorator in ("@on_event()", "@on_command()", "@step()"):
        assert all(legacy_decorator not in text for text in current_docs.values())
    for current_dispatch_contract in ("Definition", "ROUTE_HANDLERS", "generated index", "handler registry"):
        assert current_dispatch_contract in file_index


def test_runtime_orchestration_spec_lists_mandatory_binding_revision() -> None:
    runtime_spec = (REPO_ROOT / "docs" / "architecture" / "runtime-orchestration-spec.md").read_text(encoding="utf-8")

    assert "20260727_1742_be496b91f3e3" in runtime_spec
    assert "mandatory binding" in runtime_spec.lower()
