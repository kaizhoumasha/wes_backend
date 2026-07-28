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


def test_active_runtime_docs_do_not_describe_removed_plugin_execution_chain() -> None:
    active_runtime_docs = (
        "docs/architecture/file_index.md",
        "docs/architecture/runtime-orchestration-spec.md",
        "docs/architecture/runtime-ownership-map.md",
        "docs/business/workline_plugin_architecture_design.md",
        "docs/business/workline_runtime_workflow_guide.md",
        "docs/plugin_development_guide.md",
    )
    removed_chain_tokens = (
        "Workline" + chr(73) + "nbox",
        "workline_" + "orchestrator",
        "src." + "workline_runtime",
        "Null" + "Plugin",
        "Orchestrator" + "Service",
        "Inbox" + "Consumer",
        "src/" + "workline_plugins",
    )

    for relative_path in active_runtime_docs:
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for removed_token in removed_chain_tokens:
            assert removed_token not in text, f"{relative_path} 仍包含已删除链路 {removed_token}"


def test_runtime_orchestration_spec_matches_canonical_migration_and_schema_facts() -> None:
    runtime_spec = (REPO_ROOT / "docs" / "architecture" / "runtime-orchestration-spec.md").read_text(encoding="utf-8")
    file_index = (REPO_ROOT / "docs" / "architecture" / "file_index.md").read_text(encoding="utf-8")

    assert "FK 到 `wes_biz.work_lines`" not in runtime_spec
    for expected_migration_fact in (
        "`20260626_1140_c0bccb9de6f3` | ExecutionSession + ExecutionCorrelation 建表；"
        "`workline_id` 仅建查询索引，不建立跨 schema WorkLine FK",
        "`20260626_1719_f04718a3f04f` | 新增 ExecutionWorkItem、RuntimeInbox、RuntimeIntentLog、"
        "RuntimeTimeline、RuntimeHold、ConveyorQueueMembership、IdempotencyKey",
        "`20260702_1913_f88092809f4b` | DeviceRuntimeProjection 建表",
        "`20260717_0739_fa15ba0aef65` | WorklinePluginBinding 建表，并为三类运行态记录增加 Binding FK 与"
        "可空 snapshot pins",
        "`20260727_1742_be496b91f3e3` | 三类运行态记录的既有 binding snapshot pins 改为 NOT NULL；"
        "ExecutionWorkItem 新增 mandatory `manifest_version`",
    ):
        assert expected_migration_fact in runtime_spec
    assert (
        "Runtime schema revision canonical inventory：`docs/architecture/runtime-orchestration-spec.md` §4.2"
        in file_index
    )
    assert "versions/20260626_1719_f04718a3f04f_add_remaining_runtime_orchestration_.py" not in file_index

    migration_root = REPO_ROOT / "migrations" / "versions"
    revision_1140 = (migration_root / "20260626_1140_c0bccb9de6f3_add_execution_session_correlation.py").read_text(
        encoding="utf-8"
    )
    assert '"execution_sessions"' in revision_1140
    assert '"execution_correlations"' in revision_1140
    assert '"ix_wes_runtime_execution_sessions_workline_id"' in revision_1140
    assert "wes_biz.work_lines" not in revision_1140

    revision_1719 = (migration_root / "20260626_1719_f04718a3f04f_add_remaining_runtime_orchestration_.py").read_text(
        encoding="utf-8"
    )
    for table_name in (
        "execution_work_items",
        "runtime_inbox",
        "runtime_intent_logs",
        "runtime_timelines",
        "runtime_holds",
        "conveyor_queue_memberships",
        "idempotency_keys",
    ):
        assert f'op.create_table(\n        "{table_name}"' in revision_1719
    assert 'op.create_table(\n        "execution_sessions"' not in revision_1719
    assert 'op.create_table(\n        "execution_correlations"' not in revision_1719

    revision_1913 = (migration_root / "20260702_1913_f88092809f4b_add_device_runtime_projection.py").read_text(
        encoding="utf-8"
    )
    assert 'op.create_table(\n        "device_runtime_projections"' in revision_1913

    revision_0739 = (migration_root / "20260717_0739_fa15ba0aef65_add_workline_plugin_runtime_binding.py").read_text(
        encoding="utf-8"
    )
    assert '"workline_plugin_bindings"' in revision_0739
    assert "op.create_foreign_key(" in revision_0739
    assert '["plugin_binding_id"]' in revision_0739

    revision_1742 = (migration_root / "20260727_1742_be496b91f3e3_enforce_runtime_plugin_binding.py").read_text(
        encoding="utf-8"
    )
    assert 'sa.Column("manifest_version", sa.String(length=60), nullable=False)' in revision_1742
    assert "op.create_foreign_key(" not in revision_1742
    for table_name in ("workline_sessions", "execution_sessions", "execution_work_items"):
        assert f'"{table_name}"' in revision_1742
