"""Legacy cleanup matrix audit trace 守护(F-5)。

验证 cleanup matrix 的 audit trace 维度：
- 全字段一致性(现有仅比 5 个迁移字段,F-5 比其余 8 个审计字段)
- entry_id 格式 `legacy:<relative_path>:<symbol>` 与列一致
- allowlist 的 legacy_entry_id 反向引用必须在 CSV 中存在
- classification_status 枚举收敛到 {final, pending-review}

这些校验锁定 CSV 作为 audit trace 的完整性:任何手动编辑 CSV 漂移、
allowlist 引用孤儿 entry、或 classification_status 越界都会被捕获。
"""

from __future__ import annotations

import ast
import csv
import re
from pathlib import Path

import pytest

from scripts.generate_legacy_matrix import PHASE10_PRELOCK_SPECS, _validate_phase10_prelock_spec, parse_entries

REPO_ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = REPO_ROOT / "docs" / "architecture" / "legacy-cleanup-matrix.csv"
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"

_ENTRY_ID_RE = re.compile(r"^legacy:(?P<path>[^:]+):(?P<symbol>.+)$")
_VALID_CLASSIFICATION_STATUS = frozenset({"final", "pending-review"})

_TASK5_LEGACY_IMPORT_ROOTS = frozenset(
    {
        "src.app.runtime.orchestration.consumers.callback_runtime_inbox_writer",
        "src.app.runtime.orchestration.effect_bridges",
        "src.app.runtime.orchestration.effect_result",
        "src.app.runtime.orchestration.effect_state_contract",
        "src.app.runtime.orchestration.integration_lab",
        "src.app.runtime.orchestration.repository_wiring",
        "src.app.runtime.orchestration.runtime_intent",
        "src.app.runtime.orchestration.runtime_intent_effects",
        "src.app.runtime.orchestration.scenario_replay",
        "src.app.runtime.orchestration.repositories.bin_cell_reservation_repository",
        "src.app.runtime.orchestration.repositories.runtime_hold_repository",
        "src.app.runtime.orchestration.repositories.runtime_inbox_repository",
        "src.app.runtime.orchestration.repositories.runtime_intent_log_repository",
        "src.app.runtime.orchestration.repositories.wms_effect_status_repository",
        "src.app.runtime.orchestration.services.effect_reconciliation_resolution_service",
        "src.app.runtime.orchestration.services.effect_reducer_service",
        "src.app.runtime.orchestration.services.hold",
        "src.app.runtime.orchestration.services.inbox.outbox_dispatch_service",
        "src.app.runtime.orchestration.services.inbox.wms_runtime_inbox_handler",
        "src.app.runtime.orchestration.services.inbox.wms_typed_effect_callback_router",
        "src.app.runtime.orchestration.services.intent",
        "src.app.runtime.orchestration.services.reconciliation",
        "src.app.runtime.orchestration.services.runtime_inbox",
        "src.app.runtime.orchestration.services.runtime_snapshot_assembler",
        "src.app.runtime.orchestration.services.system_outbox_cancellation_service",
        "src.app.runtime.orchestration.system_capability_effect_claim",
        "src.app.sys.dispatch_concurrency",
        "src.app.sys.canonical_dispatch",
        "src.app.sys.external_http_binding",
        "src.app.sys.external_http_credentials",
        "src.app.sys.external_http_dispatch_faults",
        "src.app.sys.external_http_evidence",
        "src.app.sys.external_http_transport",
        "src.app.sys.repositories.outbox_repository",
        "src.app.sys.services.endpoint_registry",
        "src.app.sys.services.outbox_delivery",
        "src.app.sys.services.outbox_engine",
        "src.app.wms_integration.adapters",
        "src.app.wms_integration.deployment_attestation",
        "src.app.wms_integration.effect_lane_runtime",
        "src.app.wms_integration.effect_preparation_runtime",
        "src.app.wms_integration.effect_runtime",
        "src.app.wms_integration.endpoint_compiler",
        "src.app.wms_integration.evidence",
        "src.app.wms_integration.models.ports",
        "src.app.wms_integration.operation_registry",
        "src.app.wms_integration.ports.document_operations",
        "src.app.wms_integration.ports.effect_preparation",
        "src.app.wms_integration.ports.effect_status",
        "src.app.wms_integration.ports.event",
        "src.app.wms_integration.ports.master_data_operations",
        "src.app.wms_integration.ports.query_execution",
        "src.app.wms_integration.ports.query_outcome",
        "src.app.wms_integration.ports.reconciliation_operations",
        "src.app.wms_integration.provider_manifest",
        "src.app.wms_integration.provider_profile",
        "src.app.wms_integration.provider_readiness",
        "src.app.wms_integration.provider_simulator_registry",
        "src.app.wms_integration.provider_startup",
        "src.app.wms_integration.query_evidence",
        "src.app.wms_integration.query_executor",
        "src.app.wms_integration.query_projection",
        "src.app.wms_integration.query_response",
        "src.app.wms_integration.query_runtime",
        "src.app.wms_integration.repositories",
        "src.app.wms_integration.runtime_factory",
        "src.app.wms_integration.services",
        "src.app.wms_integration.state_machine",
        "src.app.wms_integration.transport_url",
        "src.celery_app.outbox_dispatch_composition",
    }
)

_SCHEMA_DEFERRED_MODULE_ROOTS = frozenset(
    {
        "src.app.runtime.orchestration.workline_runtime_status_projection",
    }
)

_TASK5_LEGACY_TASK_NAMES = frozenset(
    {
        "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch",
        "src.celery_app.tasks.runtime_inbox.process_signal",
        "src.celery_app.tasks.sys.dispatch_system_outbox_batch",
        "src.celery_app.tasks.sys.dispatch_wms_data_outbox_batch",
        "src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch",
        "src.celery_app.tasks.sys.process_signal",
        "src.celery_app.tasks.workline.check_wms_effect_status",
        "src.celery_app.tasks.workline.process_signal",
        "src.celery_app.tasks.workline.scan_wms_effect_status_batch",
    }
)

_BOUNDED_TEXT_PATTERNS = (
    re.compile(r"src\.app\.runtime\.orchestration\.(?:runtime_inbox|execution_session|runtime_intent)"),
    re.compile(r"src\.app\.sys\.(?:repositories\.outbox_repository|services\.outbox_engine)"),
    re.compile(r"src\.app\.wms_integration\.(?:operation_registry|provider_[a-z_]+|effect_[a-z_]+)"),
    re.compile(r"src\.celery_app\.tasks\.(?:runtime_inbox|sys\.dispatch_|workline\.(?:check|scan)_wms_effect)"),
    re.compile(r"\b(?:RUNTIME_INBOX|SYSTEM_OUTBOX|WMS_PROVIDER|WMS_EFFECT|HMAC_SHA256)[A-Z0-9_]*\b"),
    re.compile(r"\b(?:RuntimeInbox|SystemOutbox|WmsProviderProfile|WmsEffect)\b"),
    re.compile(r"\b(?:runtime_inbox|system_outbox|wms_provider_profile|wms_effect)\b"),
)

_BOUNDED_TEXT_EXCLUSIONS = frozenset(
    {
        "docs/architecture/legacy-cleanup-matrix.csv",
        "scripts/generate_legacy_matrix.py",
    }
)

_CANONICAL_SYMBOL_DISPOSITIONS = {
    "CanonicalPayload": "delete",
    "EndpointDefinition": "delete",
    "ExternalHttpDispatchRequest": "delete",
    "CanonicalPayload.sign_hmac_sha256": "delete",
    "_persisted_bytes": "delete",
    "canonical_json_bytes": "delete",
    "payload_sha256": "delete",
}

_LEGACY_EXTERNAL_HTTP_TRANSPORT_SYMBOLS = frozenset(
    {
        "ExternalHttpProtocolResult",
        "ExternalHttpSender",
        "ExternalHttpTransportOutcome",
        "ExternalHttpTransportPhase",
        "ExternalHttpTransportResult",
    }
)

_PHASE2_OUTBOUND_HTTP_SYMBOLS = frozenset(
    {
        "OutboundHttpDeliveryState",
        "OutboundHttpFailureKind",
        "OutboundHttpMethod",
        "OutboundHttpRequest",
        "OutboundHttpResponseLimits",
        "OutboundHttpResult",
        "OutboundHttpTransport",
    }
)

_LEGACY_WMS_OPERATION_CONTRACT_SYMBOLS = frozenset(
    {
        "WmsCompletionMode",
        "WmsDomainProjectionKind",
        "WmsExecutionLane",
        "WmsHttpMethod",
        "WmsOperationBudget",
        "WmsOperationDefinition",
        "WmsOperationMode",
        "WmsPaginationConstraint",
        "effect_operation",
        "query_operation",
    }
)

# Independent Task 5 consumer decisions:
# path -> (symbol, category, disposition, real target capability).
_DIRECT_CONSUMER_EXPECTED = {
    "src/app/callback/contracts/external_callbacks.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/callback/services/callback_ingress_service.py": (
        "CallbackProviderProfileAdmissionService",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/callback/services/callback_orchestration_service.py": (
        "CallbackOrchestrationService",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/device/services/device_context_service.py": ("<file>", "runtime", "switch", "DeviceContextService"),
    "src/app/resource/services/projection_service.py": (
        "ResourceProjectionService",
        "runtime",
        "switch",
        "ResourceProjectionService",
    ),
    "src/app/resource/services/relation_service.py": (
        "ResourceRelationService",
        "runtime",
        "switch",
        "ResourceRelationService",
    ),
    "src/app/runtime/orchestration/bin_route_instance.py": (
        "<file>",
        "runtime",
        "switch",
        "BinRouteInstance",
    ),
    "src/app/runtime/orchestration/conveyor_queue_membership.py": (
        "<file>",
        "runtime",
        "switch",
        "ConveyorQueueMembership",
    ),
    "src/app/runtime/orchestration/execution_correlation.py": (
        "<file>",
        "runtime",
        "switch",
        "ExecutionCorrelation",
    ),
    "src/app/runtime/orchestration/execution_session.py": (
        "<file>",
        "runtime",
        "switch",
        "ExecutionSession",
    ),
    "src/app/runtime/orchestration/execution_work_item.py": (
        "<file>",
        "runtime",
        "switch",
        "ExecutionWorkItem",
    ),
    "src/app/runtime/orchestration/idempotency_key.py": ("<file>", "runtime", "switch", "IdempotencyKey"),
    "src/app/runtime/orchestration/material_flow_owner.py": (
        "<file>",
        "runtime",
        "switch",
        "MaterialFlowOwner",
    ),
    "src/app/runtime/orchestration/models/bin_cell_reservation.py": (
        "<file>",
        "runtime",
        "switch",
        "WorklineBinCellReservation",
    ),
    "src/app/runtime/orchestration/models/diagnostic.py": (
        "<file>",
        "runtime",
        "switch",
        "WorklineDiagnostic",
    ),
    "src/app/runtime/orchestration/models/dispatch_attempt.py": (
        "<file>",
        "runtime",
        "switch",
        "WorklineDispatchAttempt",
    ),
    "src/app/runtime/orchestration/models/runtime_hold.py": ("<file>", "runtime", "switch", "RuntimeHold"),
    "src/app/runtime/orchestration/reconciliation_case.py": (
        "<file>",
        "runtime",
        "switch",
        "ReconciliationCase",
    ),
    "src/app/runtime/orchestration/runtime_hold.py": ("<file>", "runtime", "switch", "RuntimeHold"),
    "src/app/runtime/orchestration/runtime_inbox.py": ("<file>", "runtime", "switch", "RuntimeInbox"),
    "src/app/runtime/orchestration/runtime_intent_log.py": (
        "<file>",
        "runtime",
        "switch",
        "RuntimeIntentLog",
    ),
    "src/app/runtime/orchestration/runtime_timeline.py": (
        "<file>",
        "runtime",
        "switch",
        "RuntimeTimeline",
    ),
    "src/app/runtime/orchestration/wms_rack_demand.py": (
        "<file>",
        "runtime",
        "switch",
        "WmsRackDemand",
    ),
    "src/app/runtime/orchestration/workline_runtime_status_projection.py": (
        "<file>",
        "runtime",
        "switch",
        "WorklineRuntimeStatusProjection",
    ),
    "src/app/runtime/capabilities/material_flow/bin_cell_reservation_service.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/capabilities/material_flow/station_lease_service.py": ("<file>", "runtime", "delete", "NONE"),
    "src/app/runtime/orchestration/material_target_resolver.py": ("<file>", "runtime", "delete", "NONE"),
    "src/app/runtime/orchestration/observability.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/orchestration/operation_observability.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/orchestration/repositories/conveyor_queue_membership_repository.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/repositories/diagnostic_repository.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/repositories/dispatch_attempt_repository.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/repositories/effect_reducer_repository.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/repositories/idempotency_key_repository.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/repositories/northbound_operations_repository.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/repositories/session_execution_anchor_repository.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/repositories/wms_fulfillment_domain_repository.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/repositories/wms_putaway_sync_barrier_repository.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/repositories/workline_runtime_status_projection_repository.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/hold/wms_putaway_sync_barrier_service.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/inbox/__init__.py": (
        "<file>",
        "runtime",
        "switch",
        "ObjectTransitionEventService",
    ),
    "src/app/runtime/orchestration/services/inbox/dispatch_attempt_service.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/inbox/external_http_lease_loss_service.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/inbox/non_http_lease_exhaustion_service.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/inbox/outbox_dispatch_service.py": (
        "OutboxDispatchService",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/intent/operation_service.py": (
        "WorklineOperationService",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/conveyor_queue_membership_writer_service.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/idempotency_guard.py": ("<file>", "runtime", "delete", "NONE"),
    "src/app/runtime/orchestration/services/rack_demand_service.py": ("<file>", "runtime", "delete", "NONE"),
    "src/app/runtime/orchestration/services/workline_runtime_status_projection_service.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/material_unit_mutation_service.py": (
        "<file>",
        "runtime",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/query/material_location_query_service.py": (
        "<file>",
        "runtime",
        "switch",
        "MaterialLocationQueryService",
    ),
    "src/app/runtime/orchestration/services/session/session_resolver.py": (
        "<file>",
        "runtime",
        "switch",
        "SessionResolver",
    ),
    "src/app/runtime/orchestration/services/wms_effect_status_service.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/orchestration/repositories/wms_effect_status_repository.py": (
        "WmsEffectStatusRepository",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/services/wms_fulfillment_domain_projector.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/orchestration/wms_effect_observability.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/conformance_manifest.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/conformance_matrix.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/contracts.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/document/get_grn/definition.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/document/get_outbound_order/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/document/get_pick_order/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/document/get_task_snapshot/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/document/get_wave/definition.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/document/list_grn_packages/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/effect_runtime.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/generated_operation_index.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/master_data/get_bin/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/master_data/get_material/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/master_data/get_rack/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/master_data/list_locations/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/master_data/list_materials/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/master_data/list_racks/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/master_data/list_zones/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/provider_catalog.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/query_definition.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/query_handler.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/runtime/system_capabilities/wms/reconciliation/check_bin_drift/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/reconciliation/check_full_drift/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/reconciliation/check_rack_drift/definition.py": (
        "<file>",
        "wms",
        "delete",
        "NONE",
    ),
    "src/app/runtime/system_capabilities/wms/scheduling_identity.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/transport/composition.py": ("<file>", "wms", "switch", "build_transport_runtime"),
    "src/app/sys/models/operation_completion.py": ("<file>", "sys", "delete", "NONE"),
    "src/app/sys/models/outbox.py": ("<file>", "sys", "switch", "SystemOutbox"),
    "src/app/wms_integration/models/circuit_breaker.py": (
        "<file>",
        "wms",
        "switch",
        "WmsCircuitBreakerState",
    ),
    "src/app/wms_integration/models/evidence.py": ("<file>", "wms", "switch", "WmsCallEvidence"),
    "src/app/workline/services/diagnostic_service.py": ("<file>", "runtime", "delete", "NONE"),
    "src/app/workline/runtime_services.py": ("<file>", "wms", "delete", "NONE"),
    "src/app/workline/services/safety_service.py": ("<file>", "runtime", "switch", "WorkLineSafetyService"),
    "src/app/workline/services/workline_service.py": ("<file>", "runtime", "switch", "WorkLineService"),
    "src/app/workline/services/workline_start_service.py": ("<file>", "runtime", "switch", "WorkLineStartService"),
    "src/app/workline/unit_of_work.py": ("<file>", "runtime", "switch", "WorklineUnitOfWork"),
}

_BOUNDED_TEXT_EXPECTED = {
    "src/app/callback/models/external.py": ("wms", "delete"),
    "src/app/contracts/external_contract_profile.py": ("wms", "delete"),
    "src/app/contracts/external_contract_profile_catalog.py": ("wms", "delete"),
    "src/app/contracts/runtime_inbox_query.py": ("runtime", "delete"),
    "src/app/contracts/wms_inbound.py": ("wms", "delete"),
    "docs/architecture/heavy-test-impact.toml": ("deployment", "switch"),
    "scripts/architecture-guardrails.sh": ("deployment", "switch"),
    "scripts/classify_runtime_inbox_acceptance.py": ("runtime", "delete"),
    "scripts/data/reset_runtime_data.py": ("runtime", "switch"),
    "scripts/dev-env.sh": ("deployment", "switch"),
    "scripts/git-quality-gate.sh": ("deployment", "switch"),
    "scripts/run_runtime_inbox_postgresql_acceptance.py": ("runtime", "delete"),
    "scripts/run_runtime_inbox_postgresql_acceptance_ci.sh": ("runtime", "delete"),
    "scripts/run_selected_heavy_local.sh": ("deployment", "switch"),
    "scripts/run_wms_conformance.py": ("wms", "delete"),
    "src/core/conf.py": ("deployment", "switch"),
    "src/core/task_queue_gateway.py": ("celery-boot", "switch"),
}

_FK_DEPENDENT_SCHEMA_EXPECTED: dict[str, str] = {}

_PHASE10_PRELOCK_ENTRY_IDS_BY_CATEGORY = {
    "runtime": {
        "legacy:src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py:CallbackRuntimeInboxWriter",
        "legacy:src/app/runtime/orchestration/repositories/bin_cell_reservation_repository.py:WorklineBinCellReservationRepository",
        "legacy:src/app/runtime/orchestration/repositories/release_operational_readiness_repository.py:ReleaseOperationalReadinessRepository",
        "legacy:src/app/runtime/orchestration/repositories/runtime_hold_repository.py:RuntimeHoldRepository",
        "legacy:src/app/runtime/orchestration/repositories/runtime_inbox_repository.py:RuntimeInboxRepository",
        "legacy:src/app/runtime/orchestration/repositories/runtime_intent_log_repository.py:RuntimeIntentLogRepository",
        "legacy:src/app/runtime/orchestration/repositories/runtime_location_event_repository.py:RuntimeLocationEventRepository",
        "legacy:src/app/runtime/orchestration/repositories/session_repository.py:WorklineSessionRepository",
        "legacy:src/app/runtime/orchestration/repositories/timeline_sequence_repository.py:TimelineSequenceRepository",
        "legacy:src/app/runtime/orchestration/effect_bridges.py:<file>",
        "legacy:src/app/runtime/orchestration/effect_result.py:<file>",
        "legacy:src/app/runtime/orchestration/effect_state_contract.py:<file>",
        "legacy:src/app/runtime/orchestration/integration_lab.py:<file>",
        "legacy:src/app/runtime/orchestration/repository_wiring.py:<file>",
        "legacy:src/app/runtime/orchestration/runtime_intent.py:<file>",
        "legacy:src/app/runtime/orchestration/runtime_intent_effects.py:<file>",
        "legacy:src/app/runtime/orchestration/scenario_replay.py:<file>",
        "legacy:src/app/runtime/orchestration/services/effect_reconciliation_resolution_service.py:EffectReconciliationResolutionService",
        "legacy:src/app/runtime/orchestration/services/effect_reducer_service.py:EffectReducer",
        "legacy:src/app/runtime/orchestration/services/hold/runtime_hold_creation_service.py:RuntimeHoldCreationService",
        "legacy:src/app/runtime/orchestration/services/hold/runtime_hold_query_service.py:RuntimeHoldQueryService",
        "legacy:src/app/runtime/orchestration/services/hold/runtime_hold_release_service.py:RuntimeHoldReleaseService",
        "legacy:src/app/runtime/orchestration/services/inbox/object_transition_event_service.py:ObjectTransitionEventService",
        "legacy:src/app/runtime/orchestration/services/inbox/wms_runtime_inbox_handler.py:WmsRuntimeInboxHandler",
        "legacy:src/app/runtime/orchestration/services/inbox/wms_typed_effect_callback_router.py:WmsTypedEffectCallbackRouter",
        "legacy:src/app/runtime/orchestration/services/intent/system_capability_effect_service.py:SystemCapabilityEffectService",
        "legacy:src/app/runtime/orchestration/services/intent/system_capability_intent_service.py:SystemCapabilityIntentService",
        "legacy:src/app/runtime/orchestration/services/query/runtime_query_service.py:RuntimeQueryService",
        "legacy:src/app/runtime/orchestration/services/query/workline_active_objects_service.py:WorklineActiveObjectsService",
        "legacy:src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py:WorklineRuntimeReconciliationService",
        "legacy:src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py:RuntimeInboxService",
        "legacy:src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_context_loader.py:<file>",
        "legacy:src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py:RuntimeInboxProcessorBridge",
        "legacy:src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_validation_service.py:RuntimeInboxValidationService",
        "legacy:src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_writeback_service.py:RuntimeInboxWriteBackService",
        "legacy:src/app/runtime/orchestration/services/runtime_location_event_service.py:RuntimeLocationEventService",
        "legacy:src/app/runtime/orchestration/services/runtime_snapshot_assembler.py:RuntimeSnapshotAssembler",
        "legacy:src/app/runtime/orchestration/services/trace/timeline_sequence_service.py:<file>",
        "legacy:src/app/runtime/orchestration/services/trace/trace_query_service.py:TraceQueryService",
        "legacy:src/app/runtime/orchestration/system_capability_effect_claim.py:<file>",
        "legacy:src/app/runtime/orchestration/models/object_transition_event.py:ObjectTransitionEvent",
        "legacy:src/app/runtime/orchestration/models/runtime_location_event.py:RuntimeLocationEvent",
        "legacy:src/app/runtime/orchestration/models/session.py:WorklineSession",
        "legacy:src/app/runtime/orchestration/models/timeline.py:WorklineTimeline",
        "legacy:src/app/runtime/orchestration/__init__.py:<file>",
        "legacy:src/app/runtime/orchestration/consumers/__init__.py:<file>",
        "legacy:src/app/runtime/orchestration/models/__init__.py:<file>",
        "legacy:src/app/runtime/orchestration/models/operation.py:<file>",
        "legacy:src/app/runtime/orchestration/repositories/__init__.py:<file>",
        "legacy:src/app/runtime/orchestration/services/__init__.py:<file>",
        "legacy:src/app/runtime/orchestration/services/hold/__init__.py:<file>",
        "legacy:src/app/runtime/orchestration/services/intent/__init__.py:<file>",
        "legacy:src/app/runtime/orchestration/services/query/__init__.py:<file>",
        "legacy:src/app/runtime/orchestration/services/runtime_inbox/__init__.py:<file>",
        "legacy:src/app/runtime/orchestration/services/trace/__init__.py:<file>",
        "legacy:src/app/workline/v1/active_objects.py:<file>",
        "legacy:src/app/workline/v1/operation.py:<file>",
        "legacy:src/app/workline/v1/runtime_operations.py:<file>",
    },
    "sys": {
        "legacy:src/app/runtime/orchestration/services/system_outbox_cancellation_service.py:SystemOutboxCancellationService",
        "legacy:src/app/sys/canonical_dispatch.py:<file>",
        "legacy:src/app/sys/dispatch_concurrency.py:<file>",
        "legacy:src/app/sys/external_http_binding.py:<file>",
        "legacy:src/app/sys/external_http_credentials.py:<file>",
        "legacy:src/app/sys/external_http_dispatch_faults.py:<file>",
        "legacy:src/app/sys/external_http_evidence.py:<file>",
        "legacy:src/app/sys/external_http_transport.py:<file>",
        "legacy:src/app/sys/repositories/outbox_repository.py:SystemOutboxRepository",
        "legacy:src/app/sys/services/outbox_delivery.py:<file>",
        "legacy:src/app/sys/services/outbox_engine.py:SystemOutboxEngine",
        "legacy:src/app/sys/services/endpoint_registry.py:<file>",
        "legacy:src/app/sys/models/__init__.py:<file>",
        "legacy:src/app/sys/repositories/__init__.py:<file>",
        "legacy:src/app/sys/services/__init__.py:<file>",
    },
    "wms": {
        "legacy:src/app/wms_adapter/client.py:WmsClient",
        "legacy:src/app/wms_adapter/execution_confirmation_adapter.py:<file>",
        "legacy:src/app/wms_adapter/inbound_adapter.py:WmsInboundAdapter",
        "legacy:src/app/wms_adapter/inbound_auth.py:WmsInboundAuthPolicy",
        "legacy:src/app/wms_adapter/transport_adapter.py:<file>",
        "legacy:src/app/wms_integration/effect_lane_runtime.py:<file>",
        "legacy:src/app/wms_integration/deployment_attestation.py:<file>",
        "legacy:src/app/wms_integration/effect_preparation_runtime.py:<file>",
        "legacy:src/app/wms_integration/effect_runtime.py:<file>",
        "legacy:src/app/wms_integration/endpoint_compiler.py:<file>",
        "legacy:src/app/wms_integration/operation_registry.py:<file>",
        "legacy:src/app/wms_integration/provider_manifest.py:<file>",
        "legacy:src/app/wms_integration/provider_profile.py:<file>",
        "legacy:src/app/wms_integration/provider_readiness.py:<file>",
        "legacy:src/app/wms_integration/provider_simulator_registry.py:<file>",
        "legacy:src/app/wms_integration/provider_startup.py:<file>",
        "legacy:src/app/wms_integration/query_evidence.py:<file>",
        "legacy:src/app/wms_integration/query_executor.py:<file>",
        "legacy:src/app/wms_integration/query_projection.py:<file>",
        "legacy:src/app/wms_integration/query_response.py:<file>",
        "legacy:src/app/wms_integration/query_runtime.py:<file>",
        "legacy:src/app/wms_integration/runtime_factory.py:<file>",
        "legacy:src/app/wms_integration/state_machine.py:<file>",
        "legacy:src/app/wms_integration/transport_url.py:<file>",
        "legacy:src/app/wms_integration/adapters/effect_status_query_adapter.py:<file>",
        "legacy:src/app/wms_integration/evidence/catalog.py:<file>",
        "legacy:src/app/wms_integration/evidence/envelope.py:<file>",
        "legacy:src/app/wms_integration/models/ports.py:<file>",
        "legacy:src/app/wms_integration/ports/document_operations.py:<file>",
        "legacy:src/app/wms_integration/ports/effect_preparation.py:<file>",
        "legacy:src/app/wms_integration/ports/effect_status.py:<file>",
        "legacy:src/app/wms_integration/ports/event.py:<file>",
        "legacy:src/app/wms_integration/ports/fulfillment_operations.py:<file>",
        "legacy:src/app/wms_integration/ports/fulfillment_operations.py:NotifyPkgBindingRequest",
        "legacy:src/app/wms_integration/ports/fulfillment_operations.py:NotifyPkgBindingResult",
        "legacy:src/app/wms_integration/ports/fulfillment_operations.py:validate_notify_pkg_binding_terminal_identity",
        "legacy:src/app/wms_integration/ports/inventory_operations.py:<file>",
        "legacy:src/app/wms_integration/ports/inventory_operations.py:ConfirmInboundRequest",
        "legacy:src/app/wms_integration/ports/inventory_operations.py:ConfirmInboundResult",
        "legacy:src/app/wms_integration/ports/inventory_operations.py:validate_confirm_inbound_terminal_identity",
        "legacy:src/app/wms_integration/ports/master_data_operations.py:<file>",
        "legacy:src/app/wms_integration/ports/operation_common.py:<file>",
        "legacy:src/app/wms_integration/ports/operation_common.py:validate_json_payload",
        "legacy:src/app/wms_integration/ports/query_execution.py:<file>",
        "legacy:src/app/wms_integration/ports/query_outcome.py:<file>",
        "legacy:src/app/wms_integration/ports/reconciliation_operations.py:<file>",
        "legacy:src/app/wms_integration/operation_contract.py:<file>",
        "legacy:src/app/wms_integration/repositories/circuit_breaker_repository.py:<file>",
        "legacy:src/app/wms_integration/repositories/evidence_repository.py:<file>",
        "legacy:src/app/wms_integration/services/callback_normalizer.py:<file>",
        "legacy:src/app/wms_integration/services/circuit_breaker_service.py:<file>",
        "legacy:src/app/wms_integration/services/evidence_service.py:<file>",
        "legacy:src/app/wms_integration/services/fulfillment_lifecycle.py:<file>",
        "legacy:src/app/wms_integration/services/http_transport.py:<file>",
        "legacy:src/app/wms_integration/services/redaction.py:<file>",
        "legacy:src/app/wms_integration/services/wms_event_normalizer.py:<file>",
        "legacy:src/app/wms_integration/services/exceptions.py:<file>",
        "legacy:src/app/wms_integration/__init__.py:<file>",
        "legacy:src/app/wms_integration/adapters/__init__.py:<file>",
        "legacy:src/app/wms_integration/evidence/__init__.py:<file>",
        "legacy:src/app/wms_integration/models/__init__.py:<file>",
        "legacy:src/app/wms_integration/ports/__init__.py:<file>",
        "legacy:src/app/wms_integration/repositories/__init__.py:<file>",
        "legacy:src/app/wms_integration/services/__init__.py:<file>",
    },
    "celery-boot": {
        "legacy:src/celery_app/app.py:<file>",
        "legacy:src/celery_app/async_runtime.py:<file>",
        "legacy:src/celery_app/config.py:<file>",
        "legacy:src/celery_app/config.py:queue:wms-fulfillment",
        "legacy:src/celery_app/tasks/runtime_inbox.py:src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch",
        "legacy:src/celery_app/tasks/runtime_inbox.py:src.celery_app.tasks.runtime_inbox.process_signal",
        "legacy:src/celery_app/tasks/sys.py:src.celery_app.tasks.sys.dispatch_system_outbox_batch",
        "legacy:src/celery_app/tasks/sys.py:src.celery_app.tasks.sys.dispatch_wms_data_outbox_batch",
        "legacy:src/celery_app/tasks/sys.py:src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch",
        "legacy:src/celery_app/tasks/sys.py:src.celery_app.tasks.sys.process_signal",
        "legacy:src/celery_app/tasks/workline.py:src.celery_app.tasks.workline.check_wms_effect_status",
        "legacy:src/celery_app/tasks/workline.py:src.celery_app.tasks.workline.scan_wms_effect_status_batch",
        "legacy:src/celery_app/tasks/workline.py:src.celery_app.tasks.workline.process_signal",
        "legacy:src/celery_app/outbox_dispatch_composition.py:<file>",
        "legacy:src/celery_app/tasks/runtime_inbox.py:<file>",
        "legacy:src/celery_app/tasks/sys.py:<file>",
        "legacy:src/celery_app/tasks/workline.py:<file>",
        "legacy:src/celery_app/tasks/__init__.py:<file>",
        "legacy:src/register.py:register_init",
    },
    "deployment": {
        "legacy:.env.dev:<file>",
        "legacy:.env.prod:<file>",
        "legacy:.env.test:<file>",
        "legacy:Jenkinsfile.backend-ci:<file>",
        "legacy:Jenkinsfile.release-checker-ci:<file>",
        "legacy:Jenkinsfile.test-deploy:<file>",
        "legacy:docker-compose.deploy.yml:<file>",
        "legacy:docker-compose.frontend.yml:<file>",
        "legacy:docker-compose.test-deploy.yml:<file>",
        "legacy:docker-compose.wms-acceptance.yml:<file>",
        "legacy:docker-compose.yml:<file>",
    },
    "schema-deferred": {
        "legacy:src/app/runtime/orchestration/workline_runtime_status_projection.py:WorklineRuntimeStatusProjection",
    },
}

for _path, (_symbol, _category, _disposition, _target_capability) in _DIRECT_CONSUMER_EXPECTED.items():
    _PHASE10_PRELOCK_ENTRY_IDS_BY_CATEGORY[_category].add(f"legacy:{_path}:{_symbol}")
for _path, (_category, _disposition) in _BOUNDED_TEXT_EXPECTED.items():
    _PHASE10_PRELOCK_ENTRY_IDS_BY_CATEGORY[_category].add(f"legacy:{_path}:<file>")
for _symbol in _CANONICAL_SYMBOL_DISPOSITIONS:
    _PHASE10_PRELOCK_ENTRY_IDS_BY_CATEGORY["sys"].add(f"legacy:src/app/sys/canonical_dispatch.py:{_symbol}")
for _symbol in _LEGACY_EXTERNAL_HTTP_TRANSPORT_SYMBOLS:
    _PHASE10_PRELOCK_ENTRY_IDS_BY_CATEGORY["sys"].add(f"legacy:src/app/sys/external_http_transport.py:{_symbol}")
for _symbol in _PHASE2_OUTBOUND_HTTP_SYMBOLS:
    _PHASE10_PRELOCK_ENTRY_IDS_BY_CATEGORY["sys"].add(f"legacy:src/core/outbound_http/contracts.py:{_symbol}")
for _symbol in _LEGACY_WMS_OPERATION_CONTRACT_SYMBOLS:
    _PHASE10_PRELOCK_ENTRY_IDS_BY_CATEGORY["wms"].add(f"legacy:src/app/wms_integration/operation_contract.py:{_symbol}")
_PHASE10_PRELOCK_ENTRY_IDS_BY_CATEGORY["schema-deferred"].update(_FK_DEPENDENT_SCHEMA_EXPECTED)

_SCHEMA_BLOCKING_TESTS = {
    "legacy:src/app/runtime/orchestration/workline_runtime_status_projection.py:WorklineRuntimeStatusProjection": (
        "tests/architecture/test_runtime_status_owner_guardrail.py"
    )
}

_REVIEW_EXPECTED_DETAILS = {
    "legacy:src/app/sys/canonical_dispatch.py:<file>": (
        "sys",
        "delete",
        "sys",
        "",
        "NONE",
        "tests/architecture/test_outbound_http_boundary_guardrail.py",
    ),
    "legacy:src/app/wms_integration/deployment_attestation.py:<file>": (
        "wms",
        "delete",
        "wms_integration",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
    ),
    "legacy:src/app/wms_integration/effect_preparation_runtime.py:<file>": (
        "wms",
        "delete",
        "wms_integration",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
    ),
    "legacy:src/celery_app/tasks/sys.py:src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch": (
        "celery-boot",
        "delete",
        "celery_app",
        "",
        "NONE",
        "tests/deployment/test_execution_worker_startup.py;tests/deployment/test_wms_confirmation_dispatcher.py",
    ),
    "legacy:src/app/wms_integration/operation_contract.py:<file>": (
        "wms",
        "delete",
        "wms_integration",
        "",
        "NONE",
        "tests/architecture/test_legacy_absence_guardrail.py",
    ),
    "legacy:src/app/wms_integration/ports/fulfillment_operations.py:<file>": (
        "wms",
        "switch",
        "wms_integration",
        "src/app/wms_integration/ports/fulfillment_operations.py",
        "NotifyPkgBindingRequest",
        "tests/runtime/execution/test_wms_confirmation_service.py",
    ),
    "legacy:src/app/wms_integration/ports/inventory_operations.py:<file>": (
        "wms",
        "switch",
        "wms_integration",
        "src/app/wms_integration/ports/inventory_operations.py",
        "ConfirmInboundRequest",
        "tests/runtime/execution/test_wms_confirmation_service.py",
    ),
    "legacy:src/app/wms_integration/ports/operation_common.py:<file>": (
        "wms",
        "switch",
        "wms_integration",
        "src/app/wms_integration/ports/operation_common.py",
        "validate_json_payload",
        "tests/runtime/execution/test_wms_confirmation_service.py",
    ),
}


def _read_csv_rows() -> list[dict[str, str]]:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_csv_full_field_consistency_with_parse_entries():
    """CSV 全字段必须与 parse_entries() 输出一致(补现有 5 字段比较的缺口)。

    现有 `test_generated_csv_matches_parse_entries_for_required_fields` 仅比
    strategy/target_path/target_capability/blocking_tests/drop_phase。本测试补全
    entry_type / relative_path / symbol_or_route / current_owner /
    business_semantics / phase4_carrier / classification_status / risk 共 8 个
    审计字段,防止手动编辑 CSV 字段漂移。
    """
    expected = {entry.entry_id: entry for entry in parse_entries()}
    rows = {row["entry_id"]: row for row in _read_csv_rows()}

    assert rows.keys() == expected.keys(), "CSV entry_id 集合与生成器输出不一致"

    for entry_id, entry in expected.items():
        row = rows[entry_id]
        assert row["entry_type"] == entry.entry_type, f"{entry_id}: entry_type 漂移"
        assert row["relative_path"] == entry.relative_path, f"{entry_id}: relative_path 漂移"
        assert row["symbol_or_route"] == entry.symbol_or_route, f"{entry_id}: symbol_or_route 漂移"
        assert row["current_owner"] == entry.current_owner, f"{entry_id}: current_owner 漂移"
        assert row["business_semantics"] == entry.business_semantics, f"{entry_id}: business_semantics 漂移"
        assert row["phase4_carrier"] == str(entry.phase4_carrier), f"{entry_id}: phase4_carrier 漂移"
        assert row["classification_status"] == entry.classification_status, f"{entry_id}: classification_status 漂移"
        assert row["risk"] == entry.risk, f"{entry_id}: risk 漂移"


def test_entry_id_format_matches_columns():
    """每个 entry_id 必须为 `legacy:<relative_path>:<symbol>` 格式,
    且 path / symbol 部分与 CSV 的 relative_path / symbol_or_route 列一致。"""
    for row in _read_csv_rows():
        entry_id = row["entry_id"]
        m = _ENTRY_ID_RE.match(entry_id)
        assert m is not None, f"entry_id 格式非法: {entry_id}"
        assert m.group("path") == row["relative_path"], f"entry_id path 部分与 relative_path 列不一致: {entry_id}"
        assert m.group("symbol") == row["symbol_or_route"], (
            f"entry_id symbol 部分与 symbol_or_route 列不一致: {entry_id}"
        )


def test_entry_ids_unique():
    """entry_id 必须唯一(防 audit trace 重复记账)。"""
    rows = _read_csv_rows()
    ids = [row["entry_id"] for row in rows]
    duplicates = {eid for eid in ids if ids.count(eid) > 1}
    assert not duplicates, f"entry_id 重复: {duplicates}"


def test_classification_status_enum():
    """classification_status 必须收敛到 {final, pending-review}。"""
    for row in _read_csv_rows():
        status = row["classification_status"]
        assert status in _VALID_CLASSIFICATION_STATUS, f"entry {row['entry_id']} classification_status 越界: {status}"


def test_phase10_prelock_registry_covers_frozen_categories_with_final_dispositions() -> None:
    """Phase 10 Execution Lock 前的六类身份必须进入唯一 matrix 且全部终裁。"""

    entries = [entry for entry in parse_entries() if entry.notes.startswith("phase10-prelock:")]
    actual_by_category: dict[str, set[str]] = {}
    for entry in entries:
        marker, category, disposition = entry.notes.split(":", maxsplit=2)
        assert marker == "phase10-prelock"
        actual_by_category.setdefault(category, set()).add(entry.entry_id)
        assert disposition == entry.strategy
        assert entry.phase4_carrier is False
        assert entry.classification_status == "final"
        assert entry.strategy in {"delete", "switch", "retain", "schema-deferred"}
        assert entry.target_capability
        if entry.strategy == "delete":
            assert entry.target_capability == "NONE"
        else:
            assert entry.target_path

    for category, actual_ids in actual_by_category.items():
        assert actual_ids <= _PHASE10_PRELOCK_ENTRY_IDS_BY_CATEGORY[category]
    assert actual_by_category["schema-deferred"] == _PHASE10_PRELOCK_ENTRY_IDS_BY_CATEGORY["schema-deferred"]
    assert sum(len(entry_ids) for entry_ids in actual_by_category.values()) == 84
    assert not any(entry.classification_status == "pending-review" for entry in parse_entries())


def _top_level_python_symbols(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    symbols = {
        node.name for node in module.body if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for node in module.body:
        if isinstance(node, ast.Assign):
            symbols.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            symbols.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Import):
            symbols.update(alias.asname or alias.name.split(".", maxsplit=1)[0] for alias in node.names)
    return symbols


def _qualified_python_symbols(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    symbols = _top_level_python_symbols(path)
    for node in module.body:
        if isinstance(node, ast.ClassDef):
            symbols.update(
                f"{node.name}.{child.name}"
                for child in node.body
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            )
    return symbols


def _import_base(relative_path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    module_parts = list(relative_path.with_suffix("").parts)
    module_parts.pop()
    ascend = node.level - 1
    prefix = module_parts[: len(module_parts) - ascend] if ascend <= len(module_parts) else []
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _direct_legacy_import_consumers() -> dict[str, set[str]]:
    consumers: dict[str, set[str]] = {}
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        relative_path = path.relative_to(REPO_ROOT)
        module = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for node in ast.walk(module):
            imported_modules: list[str] = []
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = _import_base(relative_path, node)
                imported_modules.append(base)
                imported_modules.extend(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
            matched = {
                root
                for root in _TASK5_LEGACY_IMPORT_ROOTS | _SCHEMA_DEFERRED_MODULE_ROOTS
                for imported in imported_modules
                if imported == root or imported.startswith(f"{root}.")
            }
            if matched:
                consumers.setdefault(relative_path.as_posix(), set()).update(matched)
    return consumers


def _task5_legacy_root_paths() -> set[str]:
    paths: set[str] = set()
    for root in _TASK5_LEGACY_IMPORT_ROOTS:
        module_path = REPO_ROOT / f"{root.replace('.', '/')}.py"
        package_path = REPO_ROOT / root.replace(".", "/") / "__init__.py"
        if module_path.exists():
            paths.add(module_path.relative_to(REPO_ROOT).as_posix())
        elif package_path.exists():
            paths.add(package_path.relative_to(REPO_ROOT).as_posix())
        elif package_path.parent.is_dir():
            paths.update(path.relative_to(REPO_ROOT).as_posix() for path in package_path.parent.rglob("*.py"))
        else:
            continue
    return paths


def _bounded_text_paths() -> set[Path]:
    paths = {
        REPO_ROOT / ".env.dev",
        REPO_ROOT / ".env.prod",
        REPO_ROOT / ".env.test",
        REPO_ROOT / "src" / "core" / "conf.py",
        REPO_ROOT / "src" / "core" / "task_queue_gateway.py",
    }
    for pattern in (
        "Jenkinsfile*",
        "docker-compose*.yml",
        "scripts/*.py",
        "scripts/*.sh",
        "scripts/data/*.py",
        "src/app/**/contracts/*.py",
        "src/app/**/models/*.py",
        "src/app/**/v1/*.py",
        "src/app/contracts/**/*.py",
        "docs/architecture/heavy-test-impact.toml",
    ):
        paths.update(path for path in REPO_ROOT.glob(pattern) if path.is_file())
    return paths


def _registered_celery_task_names(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    names: set[str] = set()
    for node in module.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for keyword in decorator.keywords:
                if (
                    keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    names.add(keyword.value.value)
    return names


def _configured_queue_names(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    return {
        value.value
        for node in ast.walk(module)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant)
        and key.value == "queue"
        and isinstance(value, ast.Constant)
        and isinstance(value.value, str)
    }


def test_phase10_prelock_registry_details_are_independent_and_structurally_valid() -> None:
    """Manifest 逐项可解析，review 关键字段与 schema test owner 不由生产常量自证。"""

    entries = {entry.entry_id: entry for entry in parse_entries() if entry.notes.startswith("phase10-prelock:")}

    for entry_id, expected in _REVIEW_EXPECTED_DETAILS.items():
        entry = entries.get(entry_id)
        if entry is None:
            relative_path = entry_id.removeprefix("legacy:").rsplit(":", maxsplit=1)[0]
            assert not (REPO_ROOT / relative_path).exists()
            continue
        category = entry.notes.split(":", maxsplit=2)[1]
        assert (
            category,
            entry.strategy,
            entry.current_owner,
            entry.target_path,
            entry.target_capability,
            entry.blocking_tests,
        ) == expected

    for path, (symbol, category, disposition, target_capability) in _DIRECT_CONSUMER_EXPECTED.items():
        entry = entries.get(f"legacy:{path}:{symbol}")
        if entry is None:
            assert disposition in {"delete", "switch"}
            assert not (REPO_ROOT / path).exists() or symbol not in _qualified_python_symbols(REPO_ROOT / path)
            continue
        assert entry.notes == f"phase10-prelock:{category}:{disposition}"
        assert entry.current_owner == path.split("/")[2]
        assert entry.strategy == disposition
        assert entry.target_path == (path if disposition == "switch" else "")
        assert entry.target_capability == target_capability
        assert entry.blocking_tests == "tests/architecture/test_cleanup_matrix_guardrail.py"

    python_wiring_capabilities = {
        "scripts/data/reset_runtime_data.py": "reset_runtime_data",
        "src/core/conf.py": "Settings",
        "src/core/task_queue_gateway.py": "CeleryTaskQueueGateway",
    }
    for path, (category, disposition) in _BOUNDED_TEXT_EXPECTED.items():
        entry = entries.get(f"legacy:{path}:<file>")
        if entry is None:
            assert disposition in {"delete", "switch"}
            assert not (REPO_ROOT / path).exists()
            continue
        assert entry.notes == f"phase10-prelock:{category}:{disposition}"
        assert entry.strategy == disposition
        assert entry.target_path == (path if disposition == "switch" else "")
        expected_capability = (
            python_wiring_capabilities.get(path, f"file:{path}") if disposition == "switch" else "NONE"
        )
        assert entry.target_capability == expected_capability
        assert entry.blocking_tests == "tests/architecture/test_cleanup_matrix_guardrail.py"

    for entry_id, blocking_tests in _SCHEMA_BLOCKING_TESTS.items():
        entry = entries[entry_id]
        assert entry.strategy == "schema-deferred"
        assert entry.blocking_tests == blocking_tests

    for entry in entries.values():
        source_path = REPO_ROOT / entry.relative_path
        assert source_path.exists(), entry.entry_id
        if entry.entry_type == "celery_task":
            assert entry.symbol_or_route in _registered_celery_task_names(source_path), entry.entry_id
        elif entry.symbol_or_route not in {"<file>", "wms-fulfillment"} and entry.entry_type != "queue":
            assert entry.symbol_or_route in _qualified_python_symbols(source_path), entry.entry_id

        if entry.strategy != "delete":
            target_path = REPO_ROOT / entry.target_path
            assert target_path.exists(), entry.entry_id
            if (
                target_path.suffix == ".py"
                and entry.target_capability == entry.symbol_or_route
                and entry.target_capability.isidentifier()
            ):
                assert entry.target_capability in _qualified_python_symbols(target_path), entry.entry_id

        for test_path in entry.blocking_tests.split(";"):
            assert test_path
            assert (REPO_ROOT / test_path).exists(), f"{entry.entry_id}: missing blocking test {test_path}"


def test_phase10_prelock_covers_direct_production_consumers_of_task5_legacy_import_roots() -> None:
    """Task 5 legacy root 的每个直接 production consumer 都必须已有 disposition。"""

    prelock_entries = [entry for entry in parse_entries() if entry.notes.startswith("phase10-prelock:")]
    disposition_paths = {entry.relative_path for entry in prelock_entries}
    application_disposition_paths = {
        entry.relative_path for entry in prelock_entries if entry.strategy in {"delete", "switch"}
    }
    missing_roots = _task5_legacy_root_paths() - disposition_paths
    assert not missing_roots, "Task 5 legacy import roots missing prelock disposition: " + ", ".join(
        sorted(missing_roots)
    )
    consumers = _direct_legacy_import_consumers()
    missing = {path: roots for path, roots in consumers.items() if path not in application_disposition_paths}

    assert not missing, "direct production consumers missing prelock disposition:\n" + "\n".join(
        f"  {path}: {', '.join(sorted(roots))}" for path, roots in sorted(missing.items())
    )


def test_phase10_prelock_covers_registered_legacy_tasks_and_bounded_executable_wiring() -> None:
    """Registered task 与 bounded machine config/source/script wiring 不得漏出 prelock。"""

    entries = [entry for entry in parse_entries() if entry.notes.startswith("phase10-prelock:")]
    entry_ids = {entry.entry_id for entry in entries}
    disposition_paths = {entry.relative_path for entry in entries}

    registered_names: set[str] = set()
    for path in sorted((REPO_ROOT / "src" / "celery_app" / "tasks").glob("*.py")):
        registered_names.update(_registered_celery_task_names(path))
    assert registered_names.isdisjoint(_TASK5_LEGACY_TASK_NAMES)
    assert {
        f"legacy:{name.rsplit('.', maxsplit=1)[0].replace('.', '/')}.py:{name}" for name in _TASK5_LEGACY_TASK_NAMES
    }.isdisjoint(entry_ids)

    missing_text_paths: dict[str, list[str]] = {}
    for path in sorted(_bounded_text_paths()):
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        if relative_path in _BOUNDED_TEXT_EXCLUSIONS:
            continue
        text = path.read_text(encoding="utf-8")
        matched = [pattern.pattern for pattern in _BOUNDED_TEXT_PATTERNS if pattern.search(text)]
        if matched and relative_path not in disposition_paths:
            missing_text_paths[relative_path] = matched
    assert not missing_text_paths, "bounded legacy wiring missing prelock disposition:\n" + "\n".join(
        f"  {path}: {', '.join(patterns)}" for path, patterns in sorted(missing_text_paths.items())
    )


def test_phase10_schema_deferred_metadata_identity_and_fk_reverse_closure() -> None:
    """冻结 schema identity 必须存在于 metadata，并覆盖所有直接 FK dependent table。"""

    from scripts.generate_legacy_matrix import load_phase10_schema_snapshot, validate_phase10_schema_identity

    snapshot = load_phase10_schema_snapshot()
    entries = [entry for entry in parse_entries() if entry.notes.startswith("phase10-prelock:schema-deferred:")]
    frozen_tables = {entry.target_capability for entry in entries}

    for entry in entries:
        validate_phase10_schema_identity(entry.relative_path, entry.symbol_or_route, entry.target_capability)

    inbound_dependents = {
        table_identity
        for table_identity, foreign_key_targets in snapshot.foreign_key_targets
        if frozen_tables.intersection(foreign_key_targets)
    }
    assert inbound_dependents <= frozen_tables, "schema-deferred FK reverse closure missing tables: " + ", ".join(
        sorted(inbound_dependents - frozen_tables)
    )
    assert all(entry.blocking_tests == "tests/architecture/test_runtime_status_owner_guardrail.py" for entry in entries)


def test_phase10_generator_rejects_schema_identity_not_backed_by_model_metadata() -> None:
    """Schema target 必须是 source model 的真实 table fullname，不接受仅格式合法的字符串。"""

    schema_spec = next(spec for spec in PHASE10_PRELOCK_SPECS if spec[5] == "schema-deferred")
    invalid_spec = (*schema_spec[:7], "wes_runtime.not_a_real_table", *schema_spec[8:])

    with pytest.raises(RuntimeError, match="schema identity"):
        _validate_phase10_prelock_spec(invalid_spec)


def test_phase10_schema_validation_ignores_parent_process_metadata_pollution() -> None:
    """父 pytest 已注册的 table 不能污染 fresh child 的 migration import provenance。"""

    from sqlmodel import SQLModel

    from scripts.generate_legacy_matrix import collect_isolated_phase10_schema_snapshot
    from src.app.runtime.orchestration.workline_runtime_status_projection import WorklineRuntimeStatusProjection

    path = "src/app/runtime/orchestration/workline_runtime_status_projection.py"
    symbol = "WorklineRuntimeStatusProjection"
    table_identity = WorklineRuntimeStatusProjection.__table__.fullname
    assert table_identity in SQLModel.metadata.tables

    snapshot = collect_isolated_phase10_schema_snapshot(
        (("from", "src.app.admin.models", (("Permission", ""),)),),
        ((path.removesuffix(".py").replace("/", "."), symbol),),
    )
    with pytest.raises(RuntimeError, match="absent from isolated migration metadata"):
        _validate_phase10_prelock_spec(
            (
                "schema-deferred",
                path,
                symbol,
                "model",
                "runtime",
                "schema-deferred",
                "migrations/env.py",
                table_identity,
                "tests/architecture/test_cleanup_matrix_guardrail.py",
                "HIGH",
            ),
            schema_snapshot=snapshot,
        )


def test_phase10_isolated_collector_validates_imported_names_and_import_form() -> None:
    """Canonical import spec 必须验证 from-import name，并等价处理 ast.Import。"""

    from scripts.generate_legacy_matrix import collect_isolated_phase10_schema_snapshot

    with pytest.raises(RuntimeError):
        collect_isolated_phase10_schema_snapshot(
            import_specs=(("from", "src.app.admin.models", (("MissingMigrationModel", ""),)),),
            source_models=(),
        )

    snapshot = collect_isolated_phase10_schema_snapshot(
        import_specs=(
            (
                "import",
                "src.app.admin.models",
                (("src.app.admin.models", "admin_models"),),
            ),
        ),
        source_models=(),
    )
    assert "wes_sys.users" in snapshot.tables


def test_phase10_wms_operation_contract_is_fully_deleted_without_virtual_target() -> None:
    """旧静态 operation Definition 平台无 target caller，文件及泛型符号均 delete。"""

    path = "src/app/wms_integration/operation_contract.py"
    entries = {
        entry.symbol_or_route: entry
        for entry in parse_entries()
        if entry.notes.startswith("phase10-prelock:") and entry.relative_path == path
    }
    assert not (REPO_ROOT / path).exists()
    assert entries == {}


def test_phase10_canonical_dispatch_and_legacy_transport_are_replaced_by_real_phase2_contracts() -> None:
    """旧 canonical/transport 全删，Phase 2 retained owner 必须是 core 中的真实 symbol。"""

    path = "src/app/sys/canonical_dispatch.py"
    entries = {
        entry.symbol_or_route: entry
        for entry in parse_entries()
        if entry.notes.startswith("phase10-prelock:") and entry.relative_path == path
    }
    assert not (REPO_ROOT / path).exists()
    assert entries == {}

    legacy_transport_path = "src/app/sys/external_http_transport.py"
    transport_entries = {
        entry.symbol_or_route: entry
        for entry in parse_entries()
        if entry.notes.startswith("phase10-prelock:") and entry.relative_path == legacy_transport_path
    }
    assert not (REPO_ROOT / legacy_transport_path).exists()
    assert transport_entries == {}

    phase2_path = "src/core/outbound_http/contracts.py"
    phase2_entries = {
        entry.symbol_or_route: entry
        for entry in parse_entries()
        if entry.notes.startswith("phase10-prelock:") and entry.relative_path == phase2_path
    }
    for symbol in _PHASE2_OUTBOUND_HTTP_SYMBOLS:
        entry = phase2_entries[symbol]
        assert entry.strategy == "retain"
        assert entry.target_path == phase2_path
        assert entry.target_capability == symbol
        assert symbol in _qualified_python_symbols(REPO_ROOT / phase2_path)


def test_phase10_non_delete_targets_are_real_symbols_or_validated_machine_identities() -> None:
    """Non-delete capability 禁止用无法解析的任意描述字符串绕过。"""

    for entry in parse_entries():
        if not entry.notes.startswith("phase10-prelock:") or entry.strategy == "delete":
            continue
        if entry.strategy == "schema-deferred":
            assert re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*", entry.target_capability)
            continue
        target_path = REPO_ROOT / entry.target_path
        if entry.entry_type == "queue":
            assert entry.target_capability == f"queue:{entry.symbol_or_route.removeprefix('queue:')}"
            assert entry.target_capability.removeprefix("queue:") in _configured_queue_names(target_path)
        elif target_path.suffix == ".py":
            assert entry.target_capability in _qualified_python_symbols(target_path), entry.entry_id
        else:
            assert entry.target_capability == f"file:{entry.target_path}", entry.entry_id


def test_allowlist_legacy_entry_ids_exist_in_csv():
    """allowlist 第 5 列 legacy_entry_id 必须在 CSV entry_id 集合中存在。

    防止 allowlist 引用孤儿 entry(legacy_entry_id 指向已删除或拼错的 CSV 行),
    确保 allowlist 豁免与 audit trace 双向可追溯。
    """
    csv_ids = {row["entry_id"] for row in _read_csv_rows()}

    missing: list[str] = []
    for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("|")
        if len(parts) < 5:
            continue
        legacy_entry_id = parts[4].strip()
        if not legacy_entry_id:
            continue
        if legacy_entry_id not in csv_ids:
            missing.append(f"{parts[0]}|{parts[1]} → {legacy_entry_id}")

    assert not missing, "allowlist legacy_entry_id 反向引用孤儿(在 CSV 中不存在):\n  " + "\n  ".join(missing)
