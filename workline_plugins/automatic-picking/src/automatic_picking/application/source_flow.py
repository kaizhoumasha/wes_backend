"""来源授权的事务协调；设备映射和生产持久端口未确认前不进入运行时装配。"""

from wes_plugin_sdk import PickingMaterialAccept

from automatic_picking.handlers.source_authorization import WorkSourceFact, source_pickup_allowed
from src.app.device.contracts import WORKLINE_BUSINESS_REF_TYPE, DeviceCommandRequest
from src.core.uuid7 import new_uuid7


class AutomaticPickingSourceFlow:
    """端口共用调用方事务；source_for_update 按 WorkLine→Work 顺序锁定并读取当前事实。

    ACCEPT 输入须经 typed reader 与本盘身份匹配；映射器只构造请求，不执行 I/O。
    本协调器不提交事务、不派发命令，也不等待设备或另一机械臂。
    """

    def __init__(self, repository, commands, request_factory, *, uuid_factory=new_uuid7):
        self.repository, self.commands, self.request_factory = repository, commands, request_factory
        self.uuid_factory = uuid_factory

    async def _work(self, db, work_id):
        work = await self.repository.source_for_update(db, work_id)
        if type(work) is not WorkSourceFact or work.work_id != work_id:
            raise ValueError("source authorization does not belong to current Work")
        return work

    async def consume_in_session(self, db, *, work_id, authorization_evidence_id):
        work = await self._work(db, work_id)
        if work.authorization_evidence_id != authorization_evidence_id:
            return None
        if work.consumed_pickup_id is not None:
            return work.consumed_pickup_id
        if not source_pickup_allowed(work):
            return None
        pickup_id = self.uuid_factory()
        request = self.request_factory(work=work, pickup_id=pickup_id)
        if (
            type(request) is not DeviceCommandRequest
            or request.workline_id != work.workline_id
            or request.execution_ref_type != WORKLINE_BUSINESS_REF_TYPE
            or request.execution_ref_id != pickup_id
            or request.material_execution_id is not None
        ):
            raise ValueError("source Command must retain WorkLine and prescan Pickup identity")
        command = await self.commands.create_command_in_session(db, request)
        await self.repository.add_pickup(
            db,
            pickup_id=pickup_id,
            work_id=work.work_id,
            authorization_evidence_id=work.authorization_evidence_id,
            source=work.source,
            command_code=command.command_code,
        )
        await self.repository.consume_authorization(
            db, work_id=work.work_id, authorization_evidence_id=work.authorization_evidence_id, pickup_id=pickup_id
        )
        return pickup_id

    async def handoff_in_session(self, db, *, work_id, pickup_id, material_execution_id, evidence_id, outcome):
        if type(outcome) is not PickingMaterialAccept:
            raise TypeError("source handoff requires final typed ACCEPT")
        work = await self._work(db, work_id)
        # 当前 Work 已交接或已有更新授权时，旧 ACCEPT 不再查询/依赖可清理的旧 Pickup。
        if work.consumed_pickup_id != pickup_id:
            return False
        pickup = await self.repository.pickup_for_update(db, pickup_id)
        if (
            not work.work_open
            or pickup.work_id != work_id
            or pickup.authorization_evidence_id != work.authorization_evidence_id
            or pickup.material_execution_id is None
            or pickup.material_execution_id != material_execution_id
            or pickup.accept_evidence_id is not None
            or evidence_id == work.authorization_evidence_id
        ):
            raise ValueError("ACCEPT differs from current Work/Pickup material identity")
        await self.repository.record_accept(db, pickup_id=pickup_id, evidence_id=evidence_id, outcome=outcome)
        # source=None 与原决定 Evidence 一起保存，明确表达无后继；不能用空引用推断业务完成。
        await self.repository.handoff_authorization(
            db, work_id=work_id, evidence_id=evidence_id, source=outcome.next_source_action
        )
        return True
