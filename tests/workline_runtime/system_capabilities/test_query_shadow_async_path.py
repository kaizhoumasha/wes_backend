from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


def _draft():
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowComparisonDraft,
        QueryShadowExpected,
        ShadowComparisonStatus,
        ShadowDecision,
        ShadowDifferenceClass,
        ShadowVersionSet,
    )

    expected = QueryShadowExpected(
        shadow_eligible=True,
        comparison_key="c" * 64,
        provider_profile_identity="wms.material-flow.production",
        operation_identity="wms.inventory.query_inventory@v1",
        versions=ShadowVersionSet(
            legacy_policy_version="policy.v1",
            candidate_policy_version="policy.v2",
            legacy_contract_version="inventory.v1",
            candidate_contract_version="inventory.v2",
            normalization_version="normalization.v1",
            evaluator_version="evaluator.v1",
        ),
        observed_at=datetime(2026, 7, 22, tzinfo=UTC),
        evidence_ref="query-evidence:" + "c" * 64,
        input_hash="a" * 64,
        output_hash="b" * 64,
    )
    decision = ShadowDecision(action="ADMIT", reason="WMS_ADMITTED", error_class="NONE")
    return QueryShadowComparisonDraft(
        expected=expected,
        comparison_status=ShadowComparisonStatus.STORED,
        legacy_decision=decision,
        candidate_decision=decision,
        difference_class=ShadowDifferenceClass.MATCH,
        divergence_diff={},
        legacy_policy_duration_ns=1_000,
        candidate_policy_duration_ns=1_100,
        query_end_to_end_duration_ms=10.0,
    )


def _evidence_for_draft(draft):
    from src.app.runtime.system_capabilities.evidence import QueryEvidence

    return QueryEvidence(
        capability_key="wms.inventory.query_inventory",
        contract_version="v1",
        input_hash=draft.expected.input_hash,
        output_hash=draft.expected.output_hash,
        authority="WMS",
        source="material-flow",
        evidence_at=draft.expected.observed_at,
        source_version="inventory-42",
        admission_snapshot={"profile": draft.expected.provider_profile_identity},
        summary={"outcome": {"kind": "success"}},
        shadow_expected=draft.expected,
    )


def test_attempt_write_set_requires_explicit_shadow_comparison_contract() -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptWriteSet

    with pytest.raises(TypeError, match="shadow_comparisons"):
        AttemptWriteSet(evidence=(), next_state={}, intents=())  # type: ignore[call-arg]


def test_all_nested_shadow_task_models_forbid_extra_fields() -> None:
    from src.app.runtime.system_capabilities.shadow_readiness import (
        QueryShadowComparisonDraft,
        QueryShadowExpected,
        ShadowDecision,
        ShadowVersionSet,
    )

    for model in (ShadowVersionSet, QueryShadowExpected, ShadowDecision, QueryShadowComparisonDraft):
        assert model.model_config.get("extra") == "forbid"


@pytest.mark.asyncio
async def test_writeback_enqueues_reference_only_comparison_after_evidence_commit() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet

    events: list[object] = []

    class Db:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=7, plugin_state_version=3, plugin_state_json={}),
                plugin_binding=None,
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("persist-evidence")

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            events.append("terminal")
            return True

    class Queue:
        def enqueue_query_shadow_comparison(self, payload: dict[str, object]) -> None:
            events.append(("enqueue", payload))

    draft = _draft()
    await RuntimeInboxWriteBackService(
        plugin_attempt_repository=Repository(),
        inbox_service=InboxService(),  # type: ignore[arg-type]
        queue_gateway=Queue(),
    ).commit_plugin_attempt(
        Db(),
        expected_snapshot=AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3),
        inbox_id=91,
        session_id=41,
        workline_id=8,
        trace_id="trace-1",
        write_set=AttemptWriteSet(
            evidence=(_evidence_for_draft(draft),),
            next_state={},
            intents=(),
            shadow_comparisons=(draft,),
        ),
    )

    assert events[:3] == ["persist-evidence", "terminal", "commit"]
    assert events[3][0] == "enqueue"
    payload = events[3][1]
    assert payload["comparison_key"] == "c" * 64
    assert "payload" not in payload


@pytest.mark.asyncio
async def test_enqueue_outage_after_commit_does_not_reverse_production_result() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptSnapshot,
        AttemptWriteSet,
        WriteDisposition,
    )

    events: list[str] = []

    class Db:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=7, plugin_state_version=3, plugin_state_json={}),
                plugin_binding=None,
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("persist-evidence")

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            return True

    class Queue:
        def enqueue_query_shadow_comparison(self, payload: dict[str, object]) -> None:
            events.append("enqueue-failed")
            raise RuntimeError("queue unavailable")

    draft = _draft()
    disposition = await RuntimeInboxWriteBackService(
        plugin_attempt_repository=Repository(),
        inbox_service=InboxService(),  # type: ignore[arg-type]
        queue_gateway=Queue(),
    ).commit_plugin_attempt(
        Db(),
        expected_snapshot=AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3),
        inbox_id=91,
        session_id=41,
        workline_id=8,
        trace_id="trace-1",
        write_set=AttemptWriteSet(
            evidence=(_evidence_for_draft(draft),),
            next_state={},
            intents=(),
            shadow_comparisons=(draft,),
        ),
    )

    assert disposition is WriteDisposition.COMMITTED
    assert events == ["persist-evidence", "commit", "enqueue-failed"]


@pytest.mark.asyncio
async def test_commit_crash_never_enqueues_uncommitted_comparison() -> None:
    from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
        RuntimeInboxWriteBackService,
    )
    from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot, AttemptWriteSet

    events: list[str] = []

    class Db:
        async def commit(self) -> None:
            events.append("commit-crash")
            raise RuntimeError("database crash")

        async def rollback(self) -> None:
            events.append("rollback")

    class Repository:
        async def lock_authoritative(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(
                inbox=SimpleNamespace(processor_token="lease-1"),
                session=SimpleNamespace(version=7, plugin_state_version=3, plugin_state_json={}),
                plugin_binding=None,
            )

        async def persist_locked_attempt(self, *_args: object, **_kwargs: object) -> None:
            events.append("persist-evidence")

    class InboxService:
        async def mark_processed(self, *_args: object, **_kwargs: object) -> bool:
            return True

    class Queue:
        def enqueue_query_shadow_comparison(self, _payload: dict[str, object]) -> None:
            events.append("MUST_NOT_ENQUEUE")

    draft = _draft()
    with pytest.raises(RuntimeError, match="database crash"):
        await RuntimeInboxWriteBackService(
            plugin_attempt_repository=Repository(),
            inbox_service=InboxService(),  # type: ignore[arg-type]
            queue_gateway=Queue(),
        ).commit_plugin_attempt(
            Db(),
            expected_snapshot=AttemptSnapshot(processor_token="lease-1", session_version=7, plugin_state_version=3),
            inbox_id=91,
            session_id=41,
            workline_id=8,
            trace_id="trace-1",
            write_set=AttemptWriteSet(
                evidence=(_evidence_for_draft(draft),),
                next_state={},
                intents=(),
                shadow_comparisons=(draft,),
            ),
        )

    assert events == ["persist-evidence", "commit-crash", "rollback"]


def test_task_queue_gateway_uses_named_celery_consumer_without_create_task() -> None:
    from src.core.task_queue_gateway import PROCESS_QUERY_SHADOW_COMPARISON_TASK, CeleryTaskQueueGateway

    calls: list[tuple[str, dict[str, object]]] = []

    class Gateway(CeleryTaskQueueGateway):
        def _send_task(self, task_name: str, *, kwargs: dict[str, object]) -> None:
            calls.append((task_name, kwargs))

    payload = _draft().task_payload()
    Gateway().enqueue_query_shadow_comparison(payload)

    assert calls == [(PROCESS_QUERY_SHADOW_COMPARISON_TASK, {"payload": payload})]


def test_write_set_requires_exact_match_between_eligible_evidence_and_comparison_drafts() -> None:
    from src.app.runtime.workline_plugins.attempt_coordinator import (
        AttemptWriteSet,
        PluginWriteSetLimits,
        bound_attempt_write_set,
    )

    draft = _draft()
    evidence = _evidence_for_draft(draft)
    matched = bound_attempt_write_set(
        AttemptWriteSet(
            evidence=(evidence,),
            next_state={},
            intents=(),
            shadow_comparisons=(draft,),
        ),
        limits=PluginWriteSetLimits(),
        fallback_state={},
    )
    orphan = bound_attempt_write_set(
        AttemptWriteSet(evidence=(), next_state={}, intents=(), shadow_comparisons=(draft,)),
        limits=PluginWriteSetLimits(),
        fallback_state={},
    )

    assert matched.shadow_comparisons == (draft,)
    assert matched.hold_reason is None
    assert orphan.shadow_comparisons == ()
    assert orphan.hold_reason == "PLUGIN_WRITE_SET_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_comparison_consumer_rejects_missing_month_partition_before_insert() -> None:
    from src.app.runtime.system_capabilities.shadow_repository import (
        QueryShadowComparisonRepository,
        QueryShadowPartitionMissing,
    )

    class Result:
        def scalar_one_or_none(self) -> None:
            return None

    class Db:
        def __init__(self) -> None:
            self.added: list[object] = []

        async def execute(self, *_args: object, **_kwargs: object) -> Result:
            return Result()

        def add(self, value: object) -> None:
            self.added.append(value)

    db = Db()
    with pytest.raises(QueryShadowPartitionMissing, match="2026_07"):
        await QueryShadowComparisonRepository().append_from_task(db, payload=_draft().task_payload())

    assert db.added == []

    with pytest.raises(ValueError, match="unexpected fields"):
        await QueryShadowComparisonRepository().append_from_task(
            db,
            payload={**_draft().task_payload(), "request_payload": {"secret": "must-not-enter-queue"}},
        )

    mismatch = _draft().model_copy(
        update={
            "candidate_decision": _draft().candidate_decision.model_copy(update={"action": "HOLD"}),
            "divergence_diff": {"action": ["ADMIT", "HOLD"]},
        }
    )
    tampered = mismatch.task_payload()
    with pytest.raises(ValueError, match="classification"):
        await QueryShadowComparisonRepository().append_from_task(db, payload=tampered)


@pytest.mark.asyncio
async def test_comparison_consumer_rejects_nested_task_payload_smuggling_before_database_access() -> None:
    from src.app.runtime.system_capabilities.shadow_repository import QueryShadowComparisonRepository

    class Db:
        async def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("nested payload must be rejected before partition lookup")

    base = _draft().task_payload()
    payloads = (
        {**base, "versions": {**base["versions"], "authority_snapshot": {"secret": True}}},
        {**base, "legacy_decision": {**base["legacy_decision"], "request_payload": {"secret": True}}},
        {**base, "candidate_decision": {**base["candidate_decision"], "response_payload": {"secret": True}}},
    )

    for payload in payloads:
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            await QueryShadowComparisonRepository().append_from_task(Db(), payload=payload)


@pytest.mark.asyncio
async def test_comparison_store_uses_atomic_conflict_marker_instead_of_silent_do_nothing() -> None:
    from src.app.runtime.system_capabilities.shadow_repository import QueryShadowComparisonRepository

    statements: list[object] = []

    class Result:
        def scalar_one_or_none(self) -> str:
            return "wes_runtime.query_shadow_comparisons_2026_07"

    class Db:
        async def execute(self, statement: object, *_args: object, **_kwargs: object) -> Result:
            statements.append(statement)
            return Result()

    await QueryShadowComparisonRepository().append_from_task(Db(), payload=_draft().task_payload())

    insert_sql = str(statements[-1])
    assert "DO NOTHING" not in insert_sql
    assert "DO UPDATE SET comparison_status" in insert_sql
    assert "WHERE NOT" in insert_sql
    for field in ("input_hash", "output_hash", "candidate_action", "difference_class", "divergence_diff"):
        assert f"{field} IS NOT DISTINCT FROM excluded.{field}" in insert_sql


@pytest.mark.asyncio
async def test_expected_reader_derives_authority_only_from_durable_query_evidence() -> None:
    from src.app.runtime.system_capabilities.shadow_repository import QueryShadowReadinessRepository

    expected = _draft().expected

    class Scalars:
        def all(self) -> list[object]:
            return [
                SimpleNamespace(
                    payload_json={
                        "record_type": "SYSTEM_CAPABILITY_EVIDENCE",
                        "evidence": {"shadow_expected": expected.model_dump(mode="json")},
                    }
                ),
                SimpleNamespace(payload_json={"record_type": "PLUGIN_DECISION", "shadow_expected": {}}),
                SimpleNamespace(
                    payload_json={
                        "record_type": "SYSTEM_CAPABILITY_EVIDENCE",
                        "evidence": {"shadow_expected": None},
                    }
                ),
            ]

    class Result:
        def scalars(self) -> Scalars:
            return Scalars()

    class Db:
        async def execute(self, *_args: object, **_kwargs: object) -> Result:
            return Result()

    samples = await QueryShadowReadinessRepository().list_expected(
        Db(),
        provider_profile_identity=expected.provider_profile_identity,
        operation_identity=expected.operation_identity,
        observed_from=datetime(2026, 7, 1, tzinfo=UTC),
        observed_until=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert samples == [expected]


@pytest.mark.asyncio
async def test_expected_reader_uses_embedded_observed_at_across_month_commit_boundary() -> None:
    from src.app.runtime.system_capabilities.shadow_repository import QueryShadowReadinessRepository

    july_expected = _draft().expected.model_copy(
        update={
            "comparison_key": "1" * 64,
            "evidence_ref": "query-evidence:" + "1" * 64,
            "observed_at": datetime(2026, 7, 31, 23, 59, tzinfo=UTC),
        }
    )
    august_expected = july_expected.model_copy(
        update={
            "comparison_key": "2" * 64,
            "evidence_ref": "query-evidence:" + "2" * 64,
            "observed_at": datetime(2026, 8, 1, tzinfo=UTC),
        }
    )
    rows = [
        # 月末 observed evidence 在次月才持久化；查询窗口仍必须按 embedded observed_at 归属 7 月。
        SimpleNamespace(
            occurred_at=datetime(2026, 8, 1, 0, 5),
            payload_json={
                "record_type": "SYSTEM_CAPABILITY_EVIDENCE",
                "evidence": {"shadow_expected": july_expected.model_dump(mode="json")},
            },
        ),
        SimpleNamespace(
            occurred_at=datetime(2026, 8, 1, 0, 6),
            payload_json={
                "record_type": "SYSTEM_CAPABILITY_EVIDENCE",
                "evidence": {"shadow_expected": august_expected.model_dump(mode="json")},
            },
        ),
    ]
    statements: list[object] = []

    class Scalars:
        def all(self) -> list[object]:
            return rows

    class Result:
        def scalars(self) -> Scalars:
            return Scalars()

    class Db:
        async def execute(self, statement: object, *_args: object, **_kwargs: object) -> Result:
            statements.append(statement)
            return Result()

    samples = await QueryShadowReadinessRepository().list_expected(
        Db(),
        provider_profile_identity=july_expected.provider_profile_identity,
        operation_identity=july_expected.operation_identity,
        observed_from=datetime(2026, 7, 1, tzinfo=UTC),
        observed_until=datetime(2026, 8, 1, tzinfo=UTC),
    )

    where_sql = str(statements[0]).partition("WHERE")[2]
    assert "workline_timelines.occurred_at" not in where_sql
    assert "payload_json" in where_sql
    assert samples == [july_expected]


def test_celery_tasks_include_comparison_consumer_and_partition_maintainer() -> None:
    from src.celery_app.config import beat_schedule
    from src.celery_app.tasks.workline import maintain_query_shadow_partitions, process_query_shadow_comparison

    assert process_query_shadow_comparison.name == "src.celery_app.tasks.workline.process_query_shadow_comparison"
    assert maintain_query_shadow_partitions.name == "src.celery_app.tasks.workline.maintain_query_shadow_partitions"
    assert beat_schedule["maintain-query-shadow-partitions"]["task"] == maintain_query_shadow_partitions.name
