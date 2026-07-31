from datetime import timedelta

import pytest
from sqlalchemy import select

from scripts.data.seed_runtime_monitor_smoke import seed_runtime_monitor_smoke
from scripts.data.sync_test_workline_devices import TEST_SMT_SORTING_INBOUND_LINE_CODE
from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold, RuntimeHoldType
from src.app.runtime.orchestration.models.session import (
    RuntimeReconciliationReason,
    RuntimeReconciliationResolution,
    RuntimeReconciliationSourceKind,
    RuntimeReconciliationState,
    SessionStatus,
    WorklineSession,
)
from src.app.runtime.orchestration.services.query.runtime_query_service import RuntimeQueryService
from src.app.runtime.orchestration.workline_runtime_status_projection import WorklineRuntimeStatusProjection
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX, WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.runtime.workline_plugins.smt_sorting_inbound.definition import DEFINITION as SMT_SORTING_INBOUND_DEFINITION
from src.app.wms_integration.ports.event import WmsInventoryUpdatedEvent
from src.app.workline.models import LineType, WorkLine, WorklinePluginBinding, WorkLineRunMode
from src.app.workline.services.plugin_binding_service import (
    WorklinePluginBindingService,
    workline_plugin_binding_service,
)
from src.core.conf import settings
from src.utils.timezone import timezone


@pytest.mark.asyncio
async def test_seed_runtime_monitor_smoke_never_activates_missing_generated_manifest(db_session) -> None:
    await seed_runtime_monitor_smoke(db_session, commit=False)

    binding = (
        await db_session.execute(
            select(WorklinePluginBinding).where(
                WorklinePluginBinding.plugin_key == "runtime_monitor_smoke_missing_manifest"
            )
        )
    ).scalar_one_or_none()
    fallback_workline = (
        await db_session.execute(select(WorkLine).where(WorkLine.line_code == "WL-RUNTIME-MONITOR-FALLBACK-SMOKE"))
    ).scalar_one_or_none()

    assert binding is None
    assert fallback_workline is None


@pytest.mark.parametrize(
    "stale_kind",
    ("disabled", "revoked", "expired", "environment", "config-changed", "active-pin"),
)
@pytest.mark.asyncio
async def test_seed_runtime_monitor_smoke_reactivates_non_admitted_or_stale_binding(
    db_session,
    monkeypatch,
    stale_kind: str,
) -> None:
    first = await seed_runtime_monitor_smoke(db_session, commit=False)
    workline = await db_session.get(WorkLine, first["single_layer_workline"]["id"])
    assert workline is not None
    binding = await db_session.get(WorklinePluginBinding, workline.active_plugin_binding_id)
    assert binding is not None and binding.id is not None
    original_binding_id = binding.id
    now = timezone.now_for_db()

    if stale_kind == "disabled":
        binding.is_enabled = False
        binding.disabled_at = now
    elif stale_kind == "revoked":
        binding.is_revoked = True
        binding.revoked_at = now
    elif stale_kind == "expired":
        binding.valid_until = now - timedelta(seconds=1)
    elif stale_kind == "environment":
        binding.environment = "production"
    elif stale_kind == "config-changed":
        monkeypatch.setattr(
            "scripts.data.seed_runtime_monitor_smoke.SMOKE_CTU_BASKET_CAPACITY",
            7,
        )
    else:
        workline.active_plugin_binding_version = binding.binding_version + 100
    await db_session.flush()

    second = await seed_runtime_monitor_smoke(db_session, commit=False)
    refreshed_workline = await db_session.get(WorkLine, second["single_layer_workline"]["id"])
    assert refreshed_workline is not None
    replacement = await db_session.get(WorklinePluginBinding, refreshed_workline.active_plugin_binding_id)
    assert replacement is not None and replacement.id is not None

    assert replacement.id != original_binding_id
    assert (
        refreshed_workline.active_plugin_binding_id,
        refreshed_workline.active_plugin_binding_version,
        refreshed_workline.active_plugin_config_hash,
        refreshed_workline.active_plugin_index_digest,
    ) == (
        replacement.id,
        replacement.binding_version,
        replacement.typed_config_hash,
        replacement.generated_index_digest,
    )
    desired_config = SMT_SORTING_INBOUND_DEFINITION.config_model.model_validate(refreshed_workline.config).model_dump(
        mode="json"
    )
    assert replacement.typed_config_hash == sha256_digest(desired_config)
    workline_plugin_binding_service.assert_execution_admitted(
        replacement,
        environment=WorklinePluginBindingService.resolve_runtime_environment(settings.APP_ENV),
        now=timezone.now_for_db(),
    )
    sessions = (
        await db_session.execute(select(WorklineSession).where(WorklineSession.workline_id == refreshed_workline.id))
    ).scalars()
    assert {session.plugin_binding_id for session in sessions} == {replacement.id}


@pytest.mark.asyncio
async def test_seed_runtime_monitor_smoke_creates_runtime_projection_scenarios(db_session) -> None:
    result = await seed_runtime_monitor_smoke(db_session, commit=False)
    service = RuntimeQueryService()

    single_layer_projection = await service.get_workline_monitor_projection(
        db_session,
        result["single_layer_workline"]["id"],
    )
    workline = await db_session.get(WorkLine, result["single_layer_workline"]["id"])
    assert workline is not None
    binding = await db_session.get(WorklinePluginBinding, workline.active_plugin_binding_id)

    assert single_layer_projection is not None
    assert binding is not None
    assert (binding.plugin_key, binding.contract_version) in WORKLINE_PLUGIN_INDEX
    assert binding.generated_index_digest == WORKLINE_PLUGIN_INDEX_DIGEST
    assert binding.activated_by == "runtime-monitor-smoke"
    assert single_layer_projection.boundary.workline_readiness == "READY"
    # Smoke seed 使用 SMT generated Definition 声明的 source station boundary；
    # 监控投影应呈现 ACTIVE_DISPATCH_LEASE，而不是从 session context 推断临时状态。
    assert single_layer_projection.boundary.station_lease == "ACTIVE_DISPATCH_LEASE"
    assert single_layer_projection.boundary.rack_operation_wait == "WAITING_WMS"
    callback_session = (
        await db_session.execute(
            select(WorklineSession).where(
                WorklineSession.session_code == "runtime-monitor-smoke:single-layer:wms-callback"
            )
        )
    ).scalar_one()
    event = WmsInventoryUpdatedEvent.model_validate(callback_session.context_json["wms_inventory_updated_event"])
    assert event.source_system == "WMS"
    assert event.event_type == "WMS_INVENTORY_UPDATED"
    assert event.source_version == "1"
    assert event.data.inventory_reference == "runtime-monitor-smoke:inventory-updated"
    assert "rack_operation" not in callback_session.context_json
    assert single_layer_projection.resource_evidence.total_count > 50
    assert single_layer_projection.resource_evidence.truncated is True
    assert len(single_layer_projection.resource_evidence.items) == 50
    assert any(
        item.evidence_kind == "TRACE_RESOURCE_EVIDENCE" for item in single_layer_projection.resource_evidence.items
    )
    assert any(item.slot_code == "A" for item in single_layer_projection.resource_evidence.items)
    assert any(item.cell_code == "CELL-SMOKE-1" for item in single_layer_projection.resource_evidence.items)
    smoke_pkg = next(
        item for item in single_layer_projection.resource_evidence.items if item.resource_code == "PKG-SMOKE-001"
    )
    assert smoke_pkg.rack_code == "RACK-SMOKE-INVENTORY"
    assert smoke_pkg.bin_code == "BIN-SMOKE-INVENTORY"
    assert smoke_pkg.slot_code == "A"
    assert smoke_pkg.cell_code == "CELL-SMOKE-1"
    assert smoke_pkg.material_code == "620100L00-011-G"
    assert smoke_pkg.date_code == "2401"
    assert smoke_pkg.lot_code == "LOT-A"
    assert smoke_pkg.reel_code == "REEL-SMOKE-001"
    assert smoke_pkg.position_index == 1


@pytest.mark.asyncio
async def test_seed_runtime_monitor_smoke_ignores_soft_deleted_worklines(db_session) -> None:
    soft_deleted_base = WorkLine(
        line_code=TEST_SMT_SORTING_INBOUND_LINE_CODE,
        line_name="soft deleted base line",
        line_type=LineType.AUTO,
        run_mode=WorkLineRunMode.SIMULATION,
    )
    soft_deleted_base.soft_delete()
    db_session.add(soft_deleted_base)
    await db_session.flush()

    result = await seed_runtime_monitor_smoke(db_session, commit=False)

    assert result["single_layer_workline"]["id"] != soft_deleted_base.id


@pytest.mark.asyncio
async def test_seed_runtime_monitor_smoke_clears_terminal_session_state(db_session) -> None:
    await seed_runtime_monitor_smoke(db_session, commit=False)
    result = await db_session.execute(
        select(WorklineSession).where(WorklineSession.session_code == "runtime-monitor-smoke:single-layer:waiting-wms")
    )
    session = result.scalar_one()
    now = timezone.now_for_db()
    session.status = SessionStatus.FAILED
    session.barcode = "OLD-BARCODE"
    session.ended_at = now
    session.awaiting_device_command_code = "CMD-OLD"
    session.failure_domain = "DEVICE"
    session.failure_code = "OLD_FAILURE"
    session.failure_message = "old failure"
    session.ingress_count = 99
    session.last_inbox_id = 456
    session.reconciliation_state = RuntimeReconciliationState.PENDING
    session.reconciliation_reason = RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED
    session.reconciliation_source_kind = RuntimeReconciliationSourceKind.TIMER_TIMEOUT
    session.reconciliation_source_inbox_id = 11
    session.reconciliation_source_outbox_id = 12
    session.reconciliation_command_id = 13
    session.reconciliation_device_id = 14
    session.reconciliation_wait_token = "old-token"
    session.reconciliation_ack_received_at = now
    session.reconciliation_deadline_at = now
    session.reconciliation_occurred_at = now
    session.reconciliation_late_evidence_received = True
    session.reconciliation_resolution = RuntimeReconciliationResolution.FAILED
    session.reconciliation_resolved_at = now
    await db_session.flush()

    await seed_runtime_monitor_smoke(db_session, commit=False)
    await db_session.refresh(session)

    assert session.status == SessionStatus.WAITING_EXTERNAL
    assert session.barcode is None
    assert session.ended_at is None
    assert session.awaiting_device_command_code is None
    assert session.failure_domain is None
    assert session.failure_code is None
    assert session.failure_message is None
    assert session.ingress_count == 1
    assert session.last_inbox_id is None
    assert session.reconciliation_state is None
    assert session.reconciliation_reason is None
    assert session.reconciliation_source_kind is None
    assert session.reconciliation_source_inbox_id is None
    assert session.reconciliation_source_outbox_id is None
    assert session.reconciliation_command_id is None
    assert session.reconciliation_device_id is None
    assert session.reconciliation_wait_token is None
    assert session.reconciliation_ack_received_at is None
    assert session.reconciliation_deadline_at is None
    assert session.reconciliation_occurred_at is None
    assert session.reconciliation_late_evidence_received is False
    assert session.reconciliation_resolution is None
    assert session.reconciliation_resolved_at is None


@pytest.mark.asyncio
async def test_seed_runtime_monitor_smoke_rejects_prod_env(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "prod")

    with pytest.raises(RuntimeError, match="不允许同步开发/测试调试"):
        await seed_runtime_monitor_smoke(db_session, commit=False)

    result = await db_session.execute(select(WorkLine).where(WorkLine.line_code == TEST_SMT_SORTING_INBOUND_LINE_CODE))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_seed_runtime_monitor_smoke_rejects_active_safety_incident(db_session) -> None:
    result = await seed_runtime_monitor_smoke(db_session, commit=False)
    workline = await db_session.get(WorkLine, result["single_layer_workline"]["id"])
    assert workline is not None
    projection = (
        await db_session.execute(
            select(WorklineRuntimeStatusProjection).where(WorklineRuntimeStatusProjection.workline_id == workline.id)
        )
    ).scalar_one()
    projection.active_safety_incident_id = 1001
    await db_session.flush()

    with pytest.raises(RuntimeError, match="active safety incident"):
        await seed_runtime_monitor_smoke(db_session, commit=False)


@pytest.mark.asyncio
async def test_seed_runtime_monitor_smoke_rejects_active_runtime_hold(db_session) -> None:
    result = await seed_runtime_monitor_smoke(db_session, commit=False)
    workline_id = result["single_layer_workline"]["id"]
    hold = RuntimeHold(
        hold_type=RuntimeHoldType.SAFETY_ESTOP,
        workline_id=workline_id,
        source_kind="SAFETY_ESTOP",
        source_reason="ESTOP_PRESSED",
        source_idempotency_key="runtime-monitor-smoke:active-hold",
    )
    db_session.add(hold)
    await db_session.flush()

    with pytest.raises(RuntimeError, match="active runtime hold"):
        await seed_runtime_monitor_smoke(db_session, commit=False)
