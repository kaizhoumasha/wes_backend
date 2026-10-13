"""宿主提供给插件的不可变完成事实；不含 wire、数据库或工作线流程。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReturnRackTaskCompletedFact:
    evidence_id: int
    picking_task_id: int
    task_id: str
    rack_id: str
    through_plan_revision: int
    completed_at_ms: int
    operation_id: str
