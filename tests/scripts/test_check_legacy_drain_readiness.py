"""Phase 10 legacy drain checker 与 Task 7 manifest 的可执行合同。"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "docs/architecture/phase10-legacy-cutover-manifest.json"


def _contract():
    try:
        from scripts.check_legacy_drain_readiness import _load_manifest, run
    except ModuleNotFoundError:
        pytest.fail("Phase 10 legacy drain checker is missing", pytrace=False)
    return _load_manifest, run


def _result(state: str):
    wait_drain_total = 1 if state == "WAIT_DRAIN" else 0
    block_total = 1 if state == "BLOCK" else 0
    return SimpleNamespace(
        state=state,
        counts={
            "runtime_inbox_processable": wait_drain_total,
            "system_outbox_identity_digest_conflict": block_total,
            "legacy_row_watermark_growth_block": 0,
        },
        wait_drain_total=wait_drain_total,
        block_total=block_total,
        stable_zero_observations=2 if state == "READY" else 0,
        producer_freeze_at="2026-08-29T12:00:00+00:00",
        generated_at="2026-08-29T12:01:00+00:00",
        manual_investigations=(
            {
                "kind": "system_outbox_identity_digest_conflict",
                "dispatch_key": "dispatch-original",
                "operation_identity": "legacy.operation@v1",
            },
        )
        if state == "BLOCK"
        else (),
    )


class _Service:
    def __init__(self, *, result: object | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, object]] = []

    async def check(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._result


def test_manifest_freezes_only_task5_producers_exact_legacy_tasks_and_non_destructive_cutover() -> None:
    load_manifest, _run = _contract()
    manifest = load_manifest(MANIFEST_PATH)

    assert tuple(producer["id"] for producer in manifest.producers) == (
        "callback.external_runtime_inbox_writer",
        "workline.sandbox_external_callback",
        "workline.runtime_inbox_replay",
        "workline.runtime_reconciliation_resolve",
        "workline.effect_reconciliation_resolve",
        "runtime.system_capability_intent_create",
        "runtime.station_lease_outbox_create",
        "core.enqueue_runtime_inbox",
        "core.enqueue_legacy_outbox",
        "core.enqueue_internal_signal",
        "core.enqueue_wms_effect_status",
        "core.direct_celery_send_task",
    )
    task_queue_producers = tuple(
        producer for producer in manifest.producers if producer["source"] == "src/core/task_queue_gateway.py"
    )
    assert tuple((producer["identity"], producer["task5_relation"]) for producer in task_queue_producers) == (
        ("CeleryTaskQueueGateway.enqueue_runtime_inbox", "remove_legacy_method"),
        ("CeleryTaskQueueGateway.enqueue_outbox", "adapt_remove_legacy_targets"),
        ("CeleryTaskQueueGateway.enqueue_internal_signal", "remove_legacy_method"),
        ("CeleryTaskQueueGateway.enqueue_wms_effect_status", "remove_legacy_method"),
        ("CeleryTaskQueueGateway._send_task", "adapt_target_only"),
    )
    assert manifest.excluded_target_producers == (
        "CeleryTaskQueueGateway.enqueue_transport_evidence",
        "CeleryTaskQueueGateway.enqueue_execution_facts",
        "CeleryTaskQueueGateway.enqueue_wms_confirmations",
        "CeleryTaskQueueGateway.enqueue_device_commands",
        "CeleryTaskQueueGateway.enqueue_safety_drain",
    )
    from src.core.task_queue_gateway import CeleryTaskQueueGateway

    assert all(
        not hasattr(CeleryTaskQueueGateway, producer["identity"].rsplit(".", maxsplit=1)[1])
        for producer in task_queue_producers
        if producer["task5_relation"] in {"remove_legacy_method", "adapt_remove_legacy_targets"}
    )
    assert all(
        hasattr(CeleryTaskQueueGateway, producer["identity"].rsplit(".", maxsplit=1)[1])
        for producer in task_queue_producers
        if producer["task5_relation"] == "adapt_target_only"
    )
    assert all(
        hasattr(CeleryTaskQueueGateway, identity.rsplit(".", maxsplit=1)[1])
        for identity in manifest.excluded_target_producers
    )
    assert manifest.legacy_task_names == (
        "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch",
        "src.celery_app.tasks.runtime_inbox.process_signal",
        "src.celery_app.tasks.sys.dispatch_system_outbox_batch",
        "src.celery_app.tasks.sys.dispatch_wms_data_outbox_batch",
        "src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch",
        "src.celery_app.tasks.sys.process_signal",
        "src.celery_app.tasks.workline.check_wms_effect_status",
        "src.celery_app.tasks.workline.scan_wms_effect_status_batch",
        "src.celery_app.tasks.workline.process_signal",
    )
    assert manifest.legacy_beat_schedule == {
        "process-runtime-inbox-batch": "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch",
        "dispatch-outbox-batch": "src.celery_app.tasks.sys.dispatch_system_outbox_batch",
        "dispatch-wms-data-outbox-batch": "src.celery_app.tasks.sys.dispatch_wms_data_outbox_batch",
        "dispatch-wms-fulfillment-outbox-batch": "src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch",
        "scan-wms-effect-status-batch": "src.celery_app.tasks.workline.scan_wms_effect_status_batch",
    }
    assert manifest.broker_states == ("active", "reserved", "scheduled")
    assert manifest.required_worker_nodes == (
        "celery@wes_backend_test-celery-1",
        "celery@wes_backend_test-celery-wms-fulfillment-1",
    )
    from src.app.runtime.orchestration.repositories.legacy_drain_readiness_repository import (
        LEGACY_DRAIN_PAIR_SCOPE,
    )

    assert manifest.paired_outbox_contract is LEGACY_DRAIN_PAIR_SCOPE
    assert manifest.shared_queues == ("celery", "device-command", "wms-fulfillment")
    assert manifest.purge_shared_queues is False
    assert manifest.stable_zero_observations == 2
    assert manifest.candidate_ready_observations == 2
    assert manifest.old_services == {
        "entrypoint": "nginx",
        "api": "api",
        "beat": "celery_beat",
        "workers": ["celery", "celery-wms-fulfillment", "flower"],
    }
    assert manifest.candidate_readiness_command == "scripts/check_release_operational_readiness.py"
    assert manifest.legacy_readiness_command == "scripts/check_legacy_drain_readiness.py"
    assert manifest.immutable_digest_inputs == (
        "DEPLOY_SOURCE_COMMIT_SHA",
        "BACKEND_CANDIDATE_DIGEST",
        "FRONTEND_CANDIDATE_DIGEST",
        "CHECKER_DIGEST_VALUE",
        "effective-facts.json",
    )
    assert all(step["on_failure"] == "CUTOVER_FAILED_MAINTENANCE_HELD" for step in manifest.maintenance_steps)
    assert not any(
        forbidden in json.dumps(manifest.raw, sort_keys=True).lower()
        for forbidden in ("queue purge", "resolve automatically", "cancel automatically", "retry automatically")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state, expected_code", [("READY", 0), ("BLOCK", 2), ("WAIT_DRAIN", 3)])
async def test_checker_emits_canonical_state_and_uses_manifest_interval_and_task_identities(
    state: str,
    expected_code: int,
) -> None:
    _load_manifest, run = _contract()
    service = _Service(result=_result(state))
    stdout = StringIO()
    stderr = StringIO()
    session_factory = object()

    status = await run(
        service=service,
        session_factory=session_factory,
        manifest_path=MANIFEST_PATH,
        producer_freeze_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
        stdout=stdout,
        stderr=stderr,
    )

    assert status == expected_code
    payload = json.loads(stdout.getvalue())
    assert payload["state"] == state
    assert payload["stable_zero_observations"] == (2 if state == "READY" else 0)
    assert payload.get("manual_investigations", []) == list(_result(state).manual_investigations)
    assert stderr.getvalue() == ""
    assert service.calls == [
        {
            "session_factory": session_factory,
            "producer_freeze_at": datetime(2026, 8, 29, 12, tzinfo=UTC),
            "interval_seconds": 30,
            "legacy_task_names": _load_manifest(MANIFEST_PATH).legacy_task_names,
            "required_worker_nodes": _load_manifest(MANIFEST_PATH).required_worker_nodes,
        }
    ]


@pytest.mark.asyncio
async def test_checker_query_or_broker_failure_exposes_no_partial_identity_or_action() -> None:
    _load_manifest, run = _contract()
    stdout = StringIO()
    stderr = StringIO()

    status = await run(
        service=_Service(error=RuntimeError("database DSN and broker secret must stay hidden")),
        session_factory=object(),
        manifest_path=MANIFEST_PATH,
        producer_freeze_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "LEGACY_DRAIN_READINESS_QUERY_FAILED\n"


def test_manifest_loader_rejects_queue_purge_or_automatic_disposition(tmp_path: Path) -> None:
    load_manifest, _run = _contract()
    invalid = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    invalid["broker"]["purge_shared_queues"] = True
    invalid["manual_disposition"]["automatic_actions"] = ["retry"]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(ValueError, match="non-destructive"):
        load_manifest(path)


@pytest.mark.parametrize(
    "drift",
    ("identity_add", "identity_remove", "domain_change", "dispatch_type_change"),
)
def test_manifest_loader_rejects_any_repository_pair_scope_drift(tmp_path: Path, drift: str) -> None:
    load_manifest, _run = _contract()
    invalid = deepcopy(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    paired = invalid["paired_outbox_contract"]
    if drift == "identity_add":
        paired["operation_identities"].append("wms.inventory.unapproved@v1")
    elif drift == "identity_remove":
        paired["operation_identities"].pop()
    elif drift == "domain_change":
        paired["operation_domain"] = "WMS_INVENTORY"
    else:
        paired["dispatch_type"] = "INTERNAL_SIGNAL"
    path = tmp_path / f"pair-scope-{drift}.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(ValueError, match="paired outbox contract does not match repository scope"):
        load_manifest(path)
