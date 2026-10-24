"""当前 Work 来源事实的纯快照，不是新的授权主账或 Cell 库存。"""

from dataclasses import dataclass

from wes_plugin_sdk import PickingCellSource


@dataclass(frozen=True, slots=True)
class WorkSourceFact:
    work_id: int
    workline_id: int
    authorization_evidence_id: int | None
    source: PickingCellSource | None
    consumed_pickup_id: str | None
    is_initial: bool
    # 只表示匹配当前栈顶的权威取出事实；ACK、结果未知及位置未知均不能给出 True。
    top_removed: bool | None
    work_open: bool
    requirement_active: bool  # 直接业务依据仍有效，不能由父任务状态代替。
    source_ready: bool  # 当前来源/栈顶身份与权威位置均匹配；缺失或未知不得给出 True。


def source_pickup_allowed(fact: WorkSourceFact) -> bool:
    return (
        fact.work_open
        and fact.requirement_active
        and fact.source_ready
        and fact.authorization_evidence_id is not None
        and fact.source is not None
        and fact.consumed_pickup_id is None
        and (fact.is_initial or fact.top_removed is True)
    )
