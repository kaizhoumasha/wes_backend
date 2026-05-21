from sqlalchemy import Index, UniqueConstraint

from src.app.workline.models.outbox import WorklineOutbox
from src.app.workline.models.runtime_hold import (
    MaterialDisposition,
    NgReasonSource,
    NgReturnItem,
    NgReturnItemStatus,
    RuntimeHold,
    RuntimeHoldStatus,
    RuntimeHoldType,
)


def _constraint_names(model: type) -> set[str]:
    return {constraint.name for constraint in model.__table__.constraints if constraint.name}


def _index_names(model: type) -> set[str]:
    return {index.name for index in model.__table__.indexes if index.name}


def test_runtime_hold_enum_values_are_stable() -> None:
    assert [item.value for item in RuntimeHoldType] == [
        "RUNTIME_RECONCILIATION",
        "SAFETY_ESTOP",
        "MANUAL_HOLD",
    ]
    assert [item.value for item in RuntimeHoldStatus] == [
        "OPEN",
        "IN_PROGRESS",
        "RESOLVED",
        "VOIDED",
        "REOPENED",
    ]
    assert [item.value for item in MaterialDisposition] == ["CONTINUE", "RETURN_TO_NG"]
    assert [item.value for item in NgReasonSource] == ["PLUGIN", "DEVICE_ERROR", "RUNTIME", "MANUAL"]
    assert [item.value for item in NgReturnItemStatus] == [
        "WAITING_REWORK",
        "REWORKING",
        "REWORKED",
        "CANCELLED",
    ]


def test_runtime_hold_defaults_and_active_blocking_helper() -> None:
    hold = RuntimeHold(
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=1,
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key="hold:timeout:1",
    )

    assert hold.status == RuntimeHoldStatus.OPEN
    assert hold.blocking is True
    assert hold.material_disposition is None
    assert hold.is_active_blocking is True

    hold.status = RuntimeHoldStatus.RESOLVED
    assert hold.is_active_blocking is False

    hold.status = RuntimeHoldStatus.REOPENED
    hold.blocking = False
    assert hold.is_active_blocking is False


def test_runtime_hold_constraints_and_indexes_document_blocking_queries() -> None:
    assert "uq_runtime_holds_source_idempotency_key" in _constraint_names(RuntimeHold)
    assert "ix_runtime_holds_active_blocking" in _index_names(RuntimeHold)
    assert "ix_runtime_holds_source_refs" in _index_names(RuntimeHold)

    active_index = next(
        index for index in RuntimeHold.__table__.indexes if index.name == "ix_runtime_holds_active_blocking"
    )
    assert isinstance(active_index, Index)
    assert [column.name for column in active_index.columns] == ["workline_id", "status", "blocking"]


def test_ng_return_item_defaults_and_idempotency_constraint() -> None:
    item = NgReturnItem(
        source_workline_id=1,
        source_session_id=2,
        material_identity_key="sku=A|lot=B|sn=C",
        material_identity_json={"sku": "A", "lot": "B", "serial": "C"},
        created_from_runtime_hold_id=3,
    )

    assert item.status == NgReturnItemStatus.WAITING_REWORK
    assert item.disposition == MaterialDisposition.RETURN_TO_NG
    assert item.ng_reason_source is None
    assert item.ng_reason_code is None
    assert item.physical_handoff_evidence_json == {}
    assert item.operator_note is None

    constraints = [
        constraint for constraint in NgReturnItem.__table__.constraints if isinstance(constraint, UniqueConstraint)
    ]
    assert any(
        constraint.name == "uq_ng_return_items_hold_material_identity"
        and [column.name for column in constraint.columns]
        == [
            "created_from_runtime_hold_id",
            "material_identity_key",
        ]
        for constraint in constraints
    )


def test_outbox_can_reference_runtime_hold_blocker() -> None:
    assert "blocked_by_runtime_hold_id" in WorklineOutbox.model_fields
    field = WorklineOutbox.model_fields["blocked_by_runtime_hold_id"]
    assert field.default is None
    assert WorklineOutbox.__table__.c.blocked_by_runtime_hold_id.nullable is True
