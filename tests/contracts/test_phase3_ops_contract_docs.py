"""Phase 3 observability and toggle governance documentation contracts."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_observability_contract_declares_stable_runtime_signals() -> None:
    path = REPO_ROOT / "docs" / "contracts" / "observability-contract.md"
    text = path.read_text(encoding="utf-8")

    for signal in (
        "callback.normalize",
        "runtime_inbox.claim",
        "runtime_intent.dispatch",
        "device_command.ack",
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
        "inbox_id",
        "evidence_key",
        "reason_code",
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
