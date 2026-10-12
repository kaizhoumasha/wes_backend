"""新增人工拣料排空执行及原返料成员。

Revision ID: 9d4e6b7a120c
Revises: a16c9b2e7d04
"""

import hashlib
import json

import sqlalchemy as sa
from alembic import op

from src.app.wms_adapter.return_buffer_drain.wire import parse_request, parse_response
from src.utils.snowflake import generate_snowflake_id
from src.utils.timezone import timezone

revision = "9d4e6b7a120c"
down_revision = "a16c9b2e7d04"
branch_labels = None
depends_on = None

_EXECUTION = "manual_picking_drain_executions"
_MEMBER = "manual_picking_drain_return_members"
_SCHEMA = "wes_biz"
_DRAIN = "workline.return_buffer.drain_rack_decide@v1"
_RETURN = "outbound.bin.return_batch@v1"
_STEPS = ("MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN", "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_ROTATE",
          "MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT")
_LIFECYCLE = (
    "(status = 'ACTIVE' AND closed_at IS NULL AND reason_code IS NULL "
    "AND blocker_client_request_id IS NULL AND trigger_picking_task_id IS NULL) OR "
    "(status = 'COMPLETED' AND closed_at IS NOT NULL AND reason_code IS NULL "
    "AND blocker_client_request_id IS NULL AND trigger_picking_task_id IS NULL) OR "
    "(status = 'EXCEPTION_RETIRED' AND closed_at IS NOT NULL AND reason_code IS NOT NULL "
    "AND reason_code IN ('DRAIN_ACTION_TERMINAL_EXCEPTION', 'DRAIN_RESULT_OVERDUE', 'DRAIN_RESULT_UNKNOWN') "
    "AND blocker_client_request_id IS NOT NULL AND trigger_picking_task_id IS NOT NULL)"
)


def _audit_columns():
    return [sa.Column("id", sa.BigInteger(), nullable=False),
            sa.Column("version", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column("updated_by", sa.BigInteger(), nullable=True)]


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _fail(row, detail):
    raise RuntimeError(f"drain migration preflight: workline={row['workline_id']} "
                       f"operation={row['operation_id']} evidence={row['response_evidence_id']}: {detail}")


def _runtime_chains(connection):
    """只保留原绑定证明的运行链；无唯一因果身份时禁止启用新选择。"""
    drains = connection.execute(sa.text(
        "SELECT c.*, e.normalized_payload, e.payload_digest, e.operation_id AS evidence_operation_id, "
        "e.workline_id AS evidence_workline_id, e.operation AS evidence_operation, e.kind, e.apply_status, e.published_at, w.line_code AS owner_line_code "
        "FROM wes_biz.wms_confirmations c JOIN wes_biz.inbound_evidences e ON e.id=c.response_evidence_id "
        "JOIN wes_biz.work_lines w ON w.id=c.workline_id "
        "WHERE c.operation=:operation AND c.status='COMPLETED' AND c.response_result='READY' "
        "AND w.plugin_key='manual-picking'"
    ), {"operation": _DRAIN}).mappings().all()
    chains = []
    for row in drains:
        bindings = connection.execute(sa.text(
            "SELECT b.*, t.status AS transport_status, t.result_deadline_at, t.reason_code, "
            "t.outcome_version, t.published_outcome_version, t.request_json, t.authority_workline_id "
            "FROM wes_biz.transport_decision_bindings b LEFT JOIN wes_biz.transport_tasks t "
            "ON t.client_request_id=b.client_request_id "
            "WHERE b.workline_id=:workline AND b.source_evidence_id=:evidence "
            "AND b.picking_task_id IS NULL AND b.step IN (:in_step, :rotate_step, :out_step)"
        ), {"workline": row["workline_id"], "evidence": row["response_evidence_id"],
            "in_step": _STEPS[0], "rotate_step": _STEPS[1], "out_step": _STEPS[2]}).mappings().all()
        if not bindings:
            unfinished = connection.execute(sa.text(
                "SELECT id FROM wes_biz.bin_line_returns WHERE workline_id=:workline "
                "AND return_state NOT IN ('EXITED','VOIDED') LIMIT 1"
            ), {"workline": row["workline_id"]}).scalar_one_or_none()
            if unfinished is not None:
                _fail(row, "READY without original binding cannot prove current runtime owner")
            continue
        if (row["completed_at"] is None or row["kind"] != "WMS_RESULT" or row["apply_status"] != "APPLIED"
            or row["published_at"] is None or row["evidence_operation_id"] != row["operation_id"]
            or row["evidence_workline_id"] != row["workline_id"] or row["evidence_operation"] != _DRAIN
            or _digest(row["request_payload"]) != row["request_digest"]
            or _digest(row["normalized_payload"]) != row["payload_digest"]):
            _fail(row, "frozen READY owner/Evidence/payload mismatch")
        payload = row["normalized_payload"]
        if payload.get("operation_id") != row["operation_id"] or row["request_payload"].get("operation_id") != row["operation_id"]:
            _fail(row, "frozen operation identity drift")
        request = parse_request(row["request_payload"])
        if request.data.workline_code != row["owner_line_code"]:
            _fail(row, "original request WorkLine code differs from frozen owner")
        response = parse_response(200, payload, request=request)
        if response.data.result != "READY":
            _fail(row, "completed READY differs from frozen Evidence")
        racks = payload["data"]["racks"]
        rack_ids = {rack["rack_id"] for rack in racks}
        for binding in bindings:
            if binding["resource_fence_id"] not in rack_ids:
                _fail(row, "binding rack does not belong to original READY")
            if binding["transport_status"] is not None and (
                binding["authority_workline_id"] != row["workline_id"]
                or binding["request_json"].get("rack_id") != binding["resource_fence_id"]
            ):
                _fail(row, "Transport frozen owner/rack mismatch")
        departures = [binding for binding in bindings if binding["step"] == _STEPS[2]]
        # 同架业务重试按原绑定身份检查；不按时间或 MAX(id) 推测当前动作。
        completed = all(any(
            departure["resource_fence_id"] == rack and (departure["transport_status"] in {"ACCEPTED", "SUCCEEDED"}
            or (departure["transport_status"] == "FAILED" and departure["reason_code"] != "RCS_TASK_CANCELLED")
            or (departure["transport_status"] == "RECONCILING" and departure["result_deadline_at"] is not None))
            for departure in departures) for rack in rack_ids)
        unclosed = any(binding["transport_status"] != "SUCCEEDED"
                       or binding["outcome_version"] > binding["published_outcome_version"] for binding in bindings)
        if completed and not unclosed:
            continue
        chains.append((row, "COMPLETED" if completed else "ACTIVE"))
    active_lines = [row["workline_id"] for row, status in chains if status == "ACTIVE"]
    if len(active_lines) != len(set(active_lines)):
        raise RuntimeError("drain migration preflight: multiple ACTIVE candidates; original owner is ambiguous")
    for row, _status in chains:
        requests = connection.execute(sa.text(
            "SELECT c.* FROM wes_biz.wms_confirmations c WHERE c.workline_id=:workline AND c.operation=:operation "
            "AND NOT EXISTS (SELECT 1 FROM wes_biz.transport_decision_bindings b "
            "WHERE b.source_evidence_id=c.response_evidence_id AND b.picking_task_id IS NOT NULL) "
            "AND (c.status <> 'COMPLETED' OR EXISTS (SELECT 1 FROM wes_biz.bin_line_returns r "
            "WHERE r.return_batch_evidence_id=c.response_evidence_id AND r.return_state NOT IN ('EXITED','VOIDED')) "
            "OR EXISTS (SELECT 1 FROM wes_biz.inbound_evidences e WHERE e.id=c.response_evidence_id "
            "AND e.published_at IS NULL))"
        ), {"workline": row["workline_id"], "operation": _RETURN}).mappings().all()
        if requests:
            request = requests[0]
            _fail(request, "unclosed return_batch lacks original drain→request→Return identity; no inferred association")
    return chains


def upgrade() -> None:
    connection = op.get_bind()
    chains = _runtime_chains(connection)
    op.create_table(_EXECUTION, *_audit_columns(),
        sa.Column("workline_id", sa.BigInteger(), nullable=False),
        sa.Column("drain_operation_id", sa.String(160), nullable=False),
        sa.Column("decision_evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("blocker_client_request_id", sa.String(160), nullable=True),
        sa.Column("trigger_picking_task_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["workline_id"], ["wes_biz.work_lines.id"]),
        sa.ForeignKeyConstraint(["decision_evidence_id"], ["wes_biz.inbound_evidences.id"]),
        sa.ForeignKeyConstraint(["blocker_client_request_id"], ["wes_biz.transport_tasks.client_request_id"]),
        sa.ForeignKeyConstraint(["trigger_picking_task_id"], ["wes_biz.picking_tasks.id"]),
        sa.UniqueConstraint("drain_operation_id", name="ux_manual_picking_drain_execution_operation"),
        sa.CheckConstraint(_LIFECYCLE, name="manual_picking_drain_execution_lifecycle_valid"), schema=_SCHEMA)
    op.create_index("ix_manual_picking_drain_execution_workline_status", _EXECUTION, ["workline_id", "status"], schema=_SCHEMA)
    op.create_index("ix_manual_picking_drain_execution_evidence", _EXECUTION, ["workline_id", "decision_evidence_id"], schema=_SCHEMA)
    op.create_table(_MEMBER, *_audit_columns(),
        sa.Column("execution_id", sa.BigInteger(), nullable=False),
        sa.Column("return_batch_confirmation_id", sa.BigInteger(), nullable=False),
        sa.Column("return_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["execution_id"], ["wes_biz.manual_picking_drain_executions.id"]),
        sa.ForeignKeyConstraint(["return_batch_confirmation_id"], ["wes_biz.wms_confirmations.id"]),
        sa.ForeignKeyConstraint(["return_id"], ["wes_biz.bin_line_returns.id"]),
        sa.UniqueConstraint("return_batch_confirmation_id", "return_id", name="ux_manual_picking_drain_return_member"),
        sa.UniqueConstraint("return_batch_confirmation_id", "sequence_no", name="ux_manual_picking_drain_return_sequence"),
        sa.CheckConstraint("sequence_no > 0", name="manual_picking_drain_return_sequence_positive"), schema=_SCHEMA)
    op.create_index("ix_manual_picking_drain_return_execution", _MEMBER, ["execution_id"], schema=_SCHEMA)
    op.create_index("ix_manual_picking_drain_return_id", _MEMBER, ["return_id"], schema=_SCHEMA)
    for row, status in chains:
        connection.execute(sa.text(
            "INSERT INTO wes_biz.manual_picking_drain_executions "
            "(id,version,created_at,workline_id,drain_operation_id,decision_evidence_id,status,closed_at) "
            "VALUES (:id,0,:created_at,:workline,:operation,:evidence,:status,:closed_at)"
        ), {"id": generate_snowflake_id(), "created_at": row["created_at"], "workline": row["workline_id"],
            "operation": row["operation_id"], "evidence": row["response_evidence_id"], "status": status,
            "closed_at": timezone.now_for_db() if status == "COMPLETED" else None})


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM wes_biz.manual_picking_drain_executions) "
                                    "OR EXISTS(SELECT 1 FROM wes_biz.manual_picking_drain_return_members)")).scalar():
        raise RuntimeError("drain execution/member records are not empty; downgrade would lose frozen runtime identity")
    op.drop_table(_MEMBER, schema=_SCHEMA)
    op.drop_table(_EXECUTION, schema=_SCHEMA)
