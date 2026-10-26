"""正常回程的真实读口及冻结命令映射；不复制共享领取/派发矩阵。"""

from datetime import datetime, timedelta
from types import SimpleNamespace as N

import pytest
from automatic_picking.application.return_device_ports import (
    ReturnCommandMapper,
    ReturnDirectionReader,
    ReturnScanReader,
)
from automatic_picking.application.return_flow import AutomaticPickingReturnFlow
from automatic_picking.definition import DEFINITION

from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE
from src.app.execution.models import InboundEvidence
from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload
from src.app.workline.models import WorkLine


class Database:
    def __init__(self, objects=(), rows=()):
        self.objects = dict(objects)
        self.rows = iter(rows)

    async def get(self, model, key):
        return self.objects.get((model, key))

    async def scalars(self, query):
        return N(all=lambda: next(self.rows))

    async def scalar(self, query):
        return next(self.rows)


def scan(role="SCAN3", **changes):
    payload = {
        "device_code": role,
        "contract_key": "SCAN",
        "contract_version": "1",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 2000,
        "source_event_id": "event-1",
        "is_debug": False,
        "trace_id": "scan-trace",
        "data": {"bin_code": "A123456789-B"},
    }
    payload.update(changes)
    evidence = N(
        id=1,
        kind="DEVICE_EVENT",
        workline_id=2,
        device_code=role,
        contract_key="SCAN",
        contract_version="1",
        normalized_payload=payload,
        payload_digest=normalize_payload(payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1],
    )
    line = N(id=2, config={"device_bindings": {role.role_key: role.role_key for role in DEFINITION.device_roles}})
    return evidence, line


def current(**changes):
    value = {
        "id": 3,
        "workline_id": 2,
        "bin_code": "A123456789",
        "source_work_id": 4,
        "entry_command_code": "exit-1",
        "scan3_evidence_id": 1,
        "scan4_evidence_id": None,
        "scan4_event_time": None,
        "archived_at": None,
        "scan3_command_code": "forward-1",
        "scan4_command_code": None,
    }
    value.update(changes)
    return N(**value)


async def test_scan3_replay_uses_current_return_without_closed_work():
    evidence, line = scan()
    db = Database([((InboundEvidence, 1), evidence), ((WorkLine, 2), line)], rows=[[current()]])
    matched = await ReturnScanReader("SCAN3").read_in_session(db, evidence_id=1)
    assert (matched.work_id, matched.command_code, matched.bin_code) == (4, "exit-1", "A123456789")


async def test_scan3_matches_current_no_work_command_without_requiring_ack():
    evidence, line = scan()
    work = N(id=4, workline_id=2, response_evidence_id=8, authorization_evidence_id=None, exit_command_code="exit-1")
    command = N(
        task_type="MOVE_FORWARD",
        device_code="SCAN2",
        workline_id=2,
        material_execution_id=None,
        execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
        execution_ref_id=AutomaticPickingReturnFlow._ref(work),
    )
    db = Database([((InboundEvidence, 1), evidence), ((WorkLine, 2), line)], rows=[[], [work], command])
    matched = await ReturnScanReader("SCAN3").read_in_session(db, evidence_id=1)
    assert matched.work_id == work.id and matched.command_code == "exit-1"
    command.execution_ref_id = "old-work"
    db = Database([((InboundEvidence, 1), evidence), ((WorkLine, 2), line)], rows=[[], [work], command])
    assert await ReturnScanReader("SCAN3").read_in_session(db, evidence_id=1) is None


@pytest.mark.parametrize(
    "changes", [{"data": {"bin_code": "A123456789-C"}}, {"is_debug": True}, {"event_type": "OTHER"}]
)
async def test_scan_does_not_guess_ng_or_debug_identity(changes):
    evidence, line = scan(**changes)
    db = Database([((InboundEvidence, 1), evidence), ((WorkLine, 2), line)])
    assert await ReturnScanReader("SCAN3").read_in_session(db, evidence_id=1) is None


@pytest.mark.parametrize("role", ["SCAN3", "SCAN4"])
@pytest.mark.parametrize("field,value", [("timestamp", 3000), ("contract_key", "OTHER"), ("contract_version", "2")])
async def test_scan_rejects_semantic_or_contract_drift_after_ingress(role, field, value):
    evidence, line = scan(role)
    evidence.normalized_payload[field] = value
    db = Database([((InboundEvidence, 1), evidence), ((WorkLine, 2), line)])
    assert await ReturnScanReader(role).read_in_session(db, evidence_id=1) is None


async def test_scan4_rejects_ambiguous_current():
    evidence, line = scan("SCAN4")
    db = Database([((InboundEvidence, 1), evidence), ((WorkLine, 2), line)], rows=[[current(), current(id=5)]])
    assert await ReturnScanReader("SCAN4").read_in_session(db, evidence_id=1) is None


async def test_scan4_matches_current_bin_after_original_scan3_and_rejects_rebinding():
    evidence, line = scan("SCAN4")
    arrival, _ = scan(timestamp=1000)
    row = current(scan3_evidence_id=9)
    objects = [((InboundEvidence, 1), evidence), ((InboundEvidence, 9), arrival), ((WorkLine, 2), line)]
    matched = await ReturnScanReader("SCAN4").read_in_session(Database(objects, rows=[[row]]), evidence_id=1)
    assert (matched.return_id, matched.timestamp) == (row.id, 2000)
    row.scan4_evidence_id = 10
    assert await ReturnScanReader("SCAN4").read_in_session(Database(objects, rows=[[row]]), evidence_id=1) is None
    arrival, _ = scan(timestamp=3000)
    row = current(scan3_evidence_id=9)
    db = Database(
        [((InboundEvidence, 1), evidence), ((InboundEvidence, 9), arrival), ((WorkLine, 2), line)], rows=[[row]]
    )
    assert await ReturnScanReader("SCAN4").read_in_session(db, evidence_id=1) is None


async def test_binding_controls_command_contract_and_timeout_and_replay_deadline():
    _, line = scan()
    now = datetime(2026, 10, 21)
    binding = N(contract_key="FROZEN", contract_version="7", command_timeout_ms=1234)

    class Worklines:
        async def get_binding_for_command_creation(self, db, *, workline_id, device_code):
            assert (workline_id, device_code) == (2, "SCAN2")
            return binding

    mapper = ReturnCommandMapper(worklines=Worklines(), clock=lambda: now)
    request = await mapper.exit_request(
        Database(rows=[None]), line=line, work=N(workline_id=2), execution_ref_id="original"
    )
    assert (request.contract_key, request.contract_version, request.deadline_at) == (
        "FROZEN",
        "7",
        now + timedelta(milliseconds=1234),
    )
    assert request.params == {} and request.task_type == "MOVE_FORWARD"
    assert request.material_execution_id is None and request.execution_ref_id == "original"
    prior = N(deadline_at=now - timedelta(seconds=1))
    assert (
        await mapper.exit_request(Database(rows=[prior]), line=line, work=N(workline_id=2), execution_ref_id="original")
    ).deadline_at == prior.deadline_at


async def test_missing_command_binding_never_invents_contract_or_timeout():
    _, line = scan()

    class Worklines:
        async def get_binding_for_command_creation(self, db, **kwargs):
            return None

    with pytest.raises(ValueError, match="冻结命令合同"):
        await ReturnCommandMapper(worklines=Worklines()).direction_request(
            Database(), line=line, current=current(), role="SCAN4", execution_ref_id="original"
        )


async def test_success_direction_uses_exact_original_return_command():
    row = current(scan4_evidence_id=9, scan4_command_code="forward-4")
    payload = {
        "command_code": "forward-4",
        "device_code": "SCAN4",
        "contract_key": "SCAN",
        "contract_version": "1",
        "result": "SUCCESS",
        "finish_time": 3000,
        "source_event_id": "result-1",
        "data": {},
        "error_detail": None,
    }
    evidence = N(
        id=10,
        kind="DEVICE_RESULT",
        command_code="forward-4",
        workline_id=2,
        normalized_payload=payload,
        payload_digest=normalize_payload(payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1],
    )
    command = N(
        command_code="forward-4",
        task_type="MOVE_FORWARD",
        status="SUCCEEDED",
        result_evidence_id=10,
        workline_id=2,
        device_code="SCAN4",
        execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
        execution_ref_id=AutomaticPickingReturnFlow.direction_ref(row, "SCAN4"),
        material_execution_id=None,
    )
    db = Database([((InboundEvidence, 10), evidence)], rows=[[row], command])
    matched = await ReturnDirectionReader().read_in_session(db, evidence_id=10)
    assert matched.return_id == row.id and matched.at_outlet is True
    command.task_type = "MOVE_LEFT"
    db = Database([((InboundEvidence, 10), evidence)], rows=[[row], command])
    assert await ReturnDirectionReader().read_in_session(db, evidence_id=10) is None
