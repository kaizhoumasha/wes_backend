"""NO_WORK/正常逐盘结束的点2放行和首次精确 SCAN3 交接，不解释设备私有报文。"""

from dataclasses import dataclass

from wes_plugin_sdk import BinWorkPlanNoWork

from automatic_picking.definition import DEFINITION
from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest, EcsCommandResult, EcsDeviceEvent
from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload
from src.app.workline.installed_plugin import parse_device_bindings
from src.utils.canonical_json import canonical_json_digest


@dataclass(frozen=True, slots=True)
class MatchedScan3:
    evidence_id: int
    work_id: int
    workline_id: int
    bin_code: str
    command_code: str
    device_code: str
    timestamp: int

    def __post_init__(self):
        for value in (self.evidence_id, self.work_id, self.workline_id):
            if type(value) is not int or value <= 0:
                raise ValueError("SCAN3 identity must be a positive integer")
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ValueError("SCAN3 timestamp must be a nonnegative integer")
        for value in (self.bin_code, self.command_code, self.device_code):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("SCAN3 identity text is required")


@dataclass(frozen=True, slots=True)
class MatchedScan4:
    evidence_id: int
    return_id: int
    workline_id: int
    bin_code: str
    device_code: str
    timestamp: int

    def __post_init__(self):
        for value in (self.evidence_id, self.return_id, self.workline_id, self.timestamp):
            if type(value) is not int or not 0 < value <= 2**63 - 1:
                raise ValueError("SCAN4 identity/time must be a positive bigint")
        for value in (self.bin_code, self.device_code):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("SCAN4 identity text is required")


@dataclass(frozen=True, slots=True)
class MatchedReturnDirection:
    """获批读口解释的精确结果及后置条件；不定义供应商私有 data。"""

    evidence_id: int
    return_id: int
    workline_id: int
    bin_code: str
    role: str
    command_code: str
    device_code: str
    at_outlet: bool

    def __post_init__(self):
        for value in (self.evidence_id, self.return_id, self.workline_id):
            if type(value) is not int or not 0 < value <= 2**63 - 1:
                raise ValueError("direction identity must be a positive bigint")
        if self.role not in {"SCAN3", "SCAN4"} or type(self.at_outlet) is not bool:
            raise ValueError("direction role/postcondition is invalid")
        for value in (self.bin_code, self.command_code, self.device_code):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("direction identity text is required")


class AutomaticPickingReturnFlow:
    def __init__(
        self,
        repository,
        commands,
        work_reader,
        request_mapper,
        scan3_reader,
        *,
        scan4_reader=None,
        direction_mapper=None,
        direction_reader=None,
    ):
        self.repository, self.commands, self.work_reader = repository, commands, work_reader
        self.request_mapper, self.scan3_reader = request_mapper, scan3_reader
        self.scan4_reader = scan4_reader
        self.direction_mapper, self.direction_reader = direction_mapper, direction_reader

    @staticmethod
    def _ref(work):
        if work.authorization_evidence_id is not None:
            return f"automatic-picking:work:{work.id}:NORMAL:{work.authorization_evidence_id}:EXIT"
        return f"automatic-picking:work:{work.id}:NO_WORK:{work.response_evidence_id}:EXIT"

    def _command_matches(self, command, work):
        return (
            command is not None
            and command.command_code == work.exit_command_code
            and command.workline_id == work.workline_id
            and command.material_execution_id is None
            and command.execution_ref_type == WORKLINE_BUSINESS_REF_TYPE
            and command.execution_ref_id == self._ref(work)
        )

    async def advance_in_session(self, db, *, line, work, task):
        if work.phase not in {"READY", "EXIT_PENDING"} or work.archived_at is not None:
            return 0
        if work.exit_command_code is not None:
            command = await self.repository.command(db, work.exit_command_code)
            if not self._command_matches(command, work):
                raise ValueError("exit command differs from its frozen Work")
            if command.status == "FAILED":
                work.phase = "RECONCILING"
                return 1
            return 0
        if work.authorization_evidence_id is not None:
            if not await self.repository.normal_complete(db, work):
                return 0
        else:
            if work.phase != "EXIT_PENDING":
                return 0
            snapshot = await self.work_reader.by_operation_id(
                db, picking_task_id=work.picking_task_id, operation_id=work.operation_id
            )
            if (
                snapshot is None
                or snapshot.status != "COMPLETED"
                or snapshot.outcome is None
                or type(snapshot.outcome.result) is not BinWorkPlanNoWork
                or snapshot.evidence_id != work.response_evidence_id
                or snapshot.intent.task_id != task.task_id
                or task.workline_id != work.workline_id
                or snapshot.intent.operation_id != work.operation_id
                or snapshot.intent.bin_code != work.bin_code
                or snapshot.intent.scanned_at != work.scanned_at
            ):
                raise ValueError("exit requires the current exact NO_WORK result")
        work.phase = "EXIT_PENDING"
        request = await self.request_mapper(db, line=line, work=work, execution_ref_id=self._ref(work))
        if (
            request.device_code != parse_device_bindings(line.config, DEFINITION.device_roles).get("SCAN2")
            or request.workline_id != work.workline_id
            or request.material_execution_id is not None
            or request.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or request.execution_ref_id != self._ref(work)
        ):
            raise ValueError("exit request differs from its frozen Work")
        handle = await self.commands.create_command_in_session(db, request)
        work.exit_command_code = handle.command_code
        await db.flush()
        return 1

    async def scan3_in_session(self, db, *, line, evidence, timestamp):
        matched = await self.scan3_reader.read_in_session(db, evidence_id=evidence.id)
        if matched is None:
            return "WAIT_FOR_EXIT"
        if (
            type(matched) is not MatchedScan3
            or matched.evidence_id != evidence.id
            or matched.workline_id != line.id
            or evidence.workline_id != line.id
            or matched.device_code != evidence.device_code
            or matched.timestamp != timestamp
            or matched.device_code != parse_device_bindings(line.config, DEFINITION.device_roles).get("SCAN3")
            or any(
                evidence.normalized_payload[key] != getattr(evidence, key)
                for key in ("contract_key", "contract_version")
            )
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
        ):
            return "RECONCILING"
        current = await self.repository.by_source_work(db, matched.work_id)
        if current is not None:
            return (
                "EXIT_HANDED_OFF"
                if (current.workline_id, current.bin_code, current.entry_command_code)
                == (line.id, matched.bin_code, matched.command_code)
                else "RECONCILING"
            )
        work = await self.repository.work_for_update(db, matched.work_id)
        if (
            work is None
            or work.workline_id != line.id
            or work.bin_code != matched.bin_code
            or work.exit_command_code != matched.command_code
            or work.archived_at is not None
            or work.phase not in {"EXIT_PENDING", "RECONCILING"}
            or matched.timestamp < work.scanned_at
        ):
            return "RECONCILING"
        command = await self.repository.command(db, matched.command_code)
        if not self._command_matches(command, work):
            return "RECONCILING"
        await self.repository.add_handoff(db, work=work, evidence_id=evidence.id)
        return "EXIT_HANDED_OFF"

    async def scan4_in_session(self, db, *, line, evidence, timestamp):
        if self.scan4_reader is None:
            return "WAIT_FOR_EXIT"
        matched = await self.scan4_reader.read_in_session(db, evidence_id=evidence.id)
        if matched is None:
            return "WAIT_FOR_EXIT"
        if (
            type(matched) is not MatchedScan4
            or matched.evidence_id != evidence.id
            or matched.workline_id != line.id
            or evidence.workline_id != line.id
            or matched.device_code != evidence.device_code
            or matched.timestamp != timestamp
            or matched.device_code != parse_device_bindings(line.config, DEFINITION.device_roles).get("SCAN4")
            or any(
                evidence.normalized_payload[key] != getattr(evidence, key)
                for key in ("contract_key", "contract_version")
            )
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
        ):
            return "RECONCILING"
        current = await self.repository.by_id_for_update(db, matched.return_id)
        if (
            current is None
            or current.archived_at is not None
            or current.workline_id != line.id
            or current.bin_code != matched.bin_code
        ):
            return "RECONCILING"
        owner = await self.repository.scan4_owner(db, evidence.id)
        if owner is not None and owner != current.id:
            return "RECONCILING"
        if current.scan4_evidence_id is None:
            await self.repository.record_scan4(db, current, evidence_id=evidence.id, event_time=matched.timestamp)
        # 首次事实先保存；相同毫秒冲突不能用接收时间或 ID 猜测队列先后。
        if await self.repository.time_conflict(db, current):
            return "RECONCILING"
        return "SCAN4_OBSERVED"

    @staticmethod
    def direction_ref(current, role):
        evidence_id = current.scan3_evidence_id if role == "SCAN3" else current.scan4_evidence_id
        return f"automatic-picking:return:{current.id}:{role}:{evidence_id}:FORWARD"

    async def _arrival(self, db, line, current, role):
        evidence_id = current.scan3_evidence_id if role == "SCAN3" else current.scan4_evidence_id
        evidence = await self.repository.evidence(db, evidence_id) if evidence_id is not None else None
        if evidence is None or evidence.apply_status != "APPLIED" or evidence.published_at is None:
            return None
        event = EcsDeviceEvent.model_validate(evidence.normalized_payload)
        if (
            evidence.workline_id != line.id
            or evidence.kind != "DEVICE_EVENT"
            or evidence.device_code != event.device_code
            or evidence.contract_key != event.contract_key
            or evidence.contract_version != event.contract_version
            or event.device_code != parse_device_bindings(line.config, DEFINITION.device_roles).get(role)
            or event.event_type != "SCAN_COMPLETED"
            or event.is_debug
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
            or (role == "SCAN4" and event.timestamp != current.scan4_event_time)
        ):
            raise ValueError("direction arrival differs from current Return")
        return evidence

    async def advance_returns_in_session(self, db, *, line, current_returns):
        if self.direction_mapper is None or not line.is_active:
            return 0
        count = 0
        for current in current_returns:
            # 后点真实到位已满足前段目标；保留已建命令责任，不补发前段动作。
            role = "SCAN3" if current.scan4_evidence_id is None else "SCAN4"
            code = current.scan3_command_code if role == "SCAN3" else current.scan4_command_code
            if code is not None or current.ready_evidence_id is not None:
                continue
            arrival = await self._arrival(db, line, current, role)
            if arrival is None:
                continue
            ref = self.direction_ref(current, role)
            request = await self.direction_mapper(db, line=line, current=current, role=role, execution_ref_id=ref)
            if (
                type(request) is not DeviceCommandRequest
                or request.workline_id != line.id
                or current.workline_id != line.id
                or request.device_code != arrival.device_code
                or request.contract_key != arrival.contract_key
                or request.contract_version != arrival.contract_version
                or request.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
                or request.execution_ref_id != ref
                or request.material_execution_id is not None
                or request.task_type != "MOVE_FORWARD"
            ):
                raise ValueError("direction request differs from current Return/arrival")
            handle = await self.commands.create_command_in_session(db, request)
            await self.repository.bind_direction(db, current, role=role, command_code=handle.command_code)
            count += 1
        return count

    async def direction_result_in_session(self, db, *, line, evidence):
        current = await self.repository.by_direction_command(db, evidence.command_code)
        if current is None:
            return "IGNORED"
        command = await self.repository.command(db, evidence.command_code)
        role = "SCAN3" if current.scan3_command_code == evidence.command_code else "SCAN4"
        try:
            result = EcsCommandResult.model_validate(evidence.normalized_payload)
        except ValueError:
            return "RECONCILING"
        if (
            command is None
            or current.workline_id != line.id
            or result.result != "SUCCESS"
            or command.status != "SUCCEEDED"
            or command.device_code != parse_device_bindings(line.config, DEFINITION.device_roles).get(role)
            or command.result_evidence_id != evidence.id
            or evidence.kind != "DEVICE_RESULT"
            or evidence.material_execution_id is not None
            or evidence.workline_id != line.id
            or evidence.device_code != command.device_code
            or evidence.contract_key != command.contract_key
            or evidence.contract_version != command.contract_version
            or result.command_code != command.command_code
            or result.device_code != command.device_code
            or result.contract_key != command.contract_key
            or result.contract_version != command.contract_version
            or command.workline_id != line.id
            or command.material_execution_id is not None
            or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or command.execution_ref_id != self.direction_ref(current, role)
            or command.task_type != "MOVE_FORWARD"
            or normalize_payload(evidence.normalized_payload, digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE)[1]
            != evidence.payload_digest
        ):
            return "RECONCILING"
        matched = (
            await self.direction_reader.read_in_session(db, evidence_id=evidence.id)
            if self.direction_reader is not None
            else None
        )
        if matched is None:
            return "WAIT_FOR_EXIT"
        if (
            type(matched) is not MatchedReturnDirection
            or matched.evidence_id != evidence.id
            or matched.return_id != current.id
            or matched.workline_id != line.id
            or matched.bin_code != current.bin_code
            or matched.role != role
            or matched.command_code != command.command_code
            or matched.device_code != command.device_code
            or (role == "SCAN4" and (not matched.at_outlet or current.ready_evidence_id not in (None, evidence.id)))
        ):
            return "RECONCILING"
        if current.archived_at is not None:
            return "DIRECTION_CLOSED"
        arrival = await self._arrival(db, line, current, role)
        if arrival is None:
            return "WAIT_FOR_EXIT"
        if role == "SCAN4":
            await self.repository.qualify(db, current, evidence.id)
        return "RETURN_QUALIFIED" if role == "SCAN4" else "SCAN3_FORWARDED"
