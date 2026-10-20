"""正常料箱回程的当前事实读口与工作线冻结命令映射。"""

import re
from datetime import timedelta

from sqlalchemy import or_, select

from automatic_picking.definition import DEFINITION
from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest, EcsCommandResult, EcsDeviceEvent
from src.app.device.models.command import DeviceCommand
from src.app.execution.models import InboundEvidence
from src.app.workline.installed_plugin import parse_device_bindings
from src.app.workline.models import WorkLine
from src.app.workline.repositories.workline_repository import workline_repository
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

from .return_flow import AutomaticPickingReturnFlow, MatchedReturnDirection, MatchedScan3, MatchedScan4
from .return_model import AutomaticPickingReturn
from .work_model import AutomaticPickingWork


class ReturnCommandMapper:
    def __init__(self, *, worklines=None, clock=timezone.now_for_db):
        self.worklines = worklines or workline_repository
        self.clock = clock

    async def exit_request(self, db, *, line, work, execution_ref_id):
        if work.workline_id != line.id:
            raise ValueError("exit Work differs from its WorkLine")
        return await self._request(db, line=line, role="SCAN2", execution_ref_id=execution_ref_id)

    async def direction_request(self, db, *, line, current, role, execution_ref_id):
        if current.workline_id != line.id or role not in {"SCAN3", "SCAN4"}:
            raise ValueError("direction Return/role differs from its WorkLine")
        return await self._request(db, line=line, role=role, execution_ref_id=execution_ref_id)

    async def _request(self, db, *, line, role, execution_ref_id):
        device_code = parse_device_bindings(line.config, DEFINITION.device_roles)[role]
        binding = await self.worklines.get_binding_for_command_creation(
            db, workline_id=line.id, device_code=device_code
        )
        if binding is None:
            raise ValueError("扫码设备缺少冻结命令合同")
        prior = await db.scalar(
            select(DeviceCommand).where(
                DeviceCommand.workline_id == line.id,
                DeviceCommand.device_code == device_code,
                DeviceCommand.execution_ref_type == WORKLINE_BUSINESS_REF_TYPE,
                DeviceCommand.execution_ref_id == execution_ref_id,
            )
        )
        return DeviceCommandRequest(
            device_code=device_code,
            workline_id=line.id,
            execution_ref_type=WORKLINE_BUSINESS_REF_TYPE,
            execution_ref_id=execution_ref_id,
            material_execution_id=None,
            contract_key=binding.contract_key,
            contract_version=binding.contract_version,
            task_type="MOVE_FORWARD",
            params={},
            deadline_at=(
                prior.deadline_at
                if prior is not None
                else self.clock() + timedelta(milliseconds=binding.command_timeout_ms)
            ),
        )


class ReturnScanReader:
    def __init__(self, role):
        if role not in {"SCAN3", "SCAN4"}:
            raise ValueError("normal Return scan role must be SCAN3 or SCAN4")
        self.role = role

    async def read_in_session(self, db, *, evidence_id):
        evidence = await db.get(InboundEvidence, evidence_id)
        if evidence is None or evidence.kind != "DEVICE_EVENT":
            return None
        try:
            event = EcsDeviceEvent.model_validate(evidence.normalized_payload)
        except ValueError:
            return None
        line = await db.get(WorkLine, evidence.workline_id)
        raw = event.data.get("bin_code")
        if (
            line is None
            or event.event_type != "SCAN_COMPLETED"
            or event.is_debug
            or not isinstance(raw, str)
            or re.fullmatch(r"A[0-9]{9}-B", raw) is None
            or event.device_code != parse_device_bindings(line.config, DEFINITION.device_roles).get(self.role)
            or event.device_code != evidence.device_code
            or event.contract_key != evidence.contract_key
            or event.contract_version != evidence.contract_version
            or canonical_json_digest(evidence.normalized_payload) != evidence.payload_digest
        ):
            return None
        code = raw[:-2]
        if self.role == "SCAN3":
            return await self._scan3(
                db,
                evidence=evidence,
                event=event,
                code=code,
                exit_device=parse_device_bindings(line.config, DEFINITION.device_roles)["SCAN2"],
            )
        return await self._scan4(db, evidence=evidence, event=event, code=code)

    async def _scan4(self, db, *, evidence, event, code):
        rows = (
            await db.scalars(
                select(AutomaticPickingReturn).where(
                    AutomaticPickingReturn.workline_id == evidence.workline_id,
                    AutomaticPickingReturn.bin_code == code,
                    AutomaticPickingReturn.archived_at.is_(None),
                )
            )
        ).all()
        if len(rows) != 1:
            return None
        current = rows[0]
        if current.scan4_evidence_id not in (None, evidence.id):
            return None
        arrival = await db.get(InboundEvidence, current.scan3_evidence_id)
        if arrival is None:
            return None
        try:
            scan3 = EcsDeviceEvent.model_validate(arrival.normalized_payload)
        except ValueError:
            return None
        if event.timestamp < scan3.timestamp:
            return None
        return MatchedScan4(evidence.id, current.id, evidence.workline_id, code, event.device_code, event.timestamp)

    async def _scan3(self, db, *, evidence, event, code, exit_device):
        # 原 SCAN3 重放由当前 Return 自含关联识别，闭合 Work 可独立清理。
        rows = (
            await db.scalars(
                select(AutomaticPickingReturn).where(AutomaticPickingReturn.scan3_evidence_id == evidence.id)
            )
        ).all()
        if rows:
            if len(rows) != 1 or (rows[0].workline_id, rows[0].bin_code) != (evidence.workline_id, code):
                return None
            current = rows[0]
            return MatchedScan3(
                evidence.id,
                current.source_work_id,
                current.workline_id,
                code,
                current.entry_command_code,
                event.device_code,
                event.timestamp,
            )
        rows = (
            await db.scalars(
                select(AutomaticPickingWork).where(
                    AutomaticPickingWork.workline_id == evidence.workline_id,
                    AutomaticPickingWork.bin_code == code,
                    AutomaticPickingWork.phase.in_(("EXIT_PENDING", "RECONCILING")),
                    AutomaticPickingWork.archived_at.is_(None),
                    AutomaticPickingWork.authorization_evidence_id.is_(None),
                    AutomaticPickingWork.exit_command_code.is_not(None),
                    AutomaticPickingWork.scanned_at <= event.timestamp,
                )
            )
        ).all()
        if len(rows) != 1:
            return None
        work = rows[0]
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == work.exit_command_code))
        if (
            command is None
            or command.task_type != "MOVE_FORWARD"
            or command.device_code != exit_device
            or command.material_execution_id is not None
            or command.workline_id != work.workline_id
            or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or command.execution_ref_id != AutomaticPickingReturnFlow._ref(work)
        ):
            return None
        return MatchedScan3(
            evidence.id, work.id, work.workline_id, code, work.exit_command_code, event.device_code, event.timestamp
        )


class ReturnDirectionReader:
    async def read_in_session(self, db, *, evidence_id):
        evidence = await db.get(InboundEvidence, evidence_id)
        if evidence is None or evidence.kind != "DEVICE_RESULT":
            return None
        try:
            result = EcsCommandResult.model_validate(evidence.normalized_payload)
        except ValueError:
            return None
        rows = (
            await db.scalars(
                select(AutomaticPickingReturn).where(
                    or_(
                        AutomaticPickingReturn.scan3_command_code == result.command_code,
                        AutomaticPickingReturn.scan4_command_code == result.command_code,
                    )
                )
            )
        ).all()
        if len(rows) != 1:
            return None
        current = rows[0]
        role = "SCAN3" if current.scan3_command_code == result.command_code else "SCAN4"
        command = await db.scalar(select(DeviceCommand).where(DeviceCommand.command_code == result.command_code))
        if (
            command is None
            or result.result != "SUCCESS"
            or command.task_type != "MOVE_FORWARD"
            or command.status != "SUCCEEDED"
            or command.result_evidence_id != evidence.id
            or command.workline_id != current.workline_id
            or evidence.workline_id != current.workline_id
            or evidence.command_code != command.command_code
            or command.material_execution_id is not None
            or command.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or command.execution_ref_id != AutomaticPickingReturnFlow.direction_ref(current, role)
        ):
            return None
        return MatchedReturnDirection(
            evidence.id,
            current.id,
            current.workline_id,
            current.bin_code,
            role,
            command.command_code,
            command.device_code,
            role == "SCAN4",
        )
