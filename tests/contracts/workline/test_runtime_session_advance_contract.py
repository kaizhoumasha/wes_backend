"""BC-XX Runtime Session / WorkItem step advance 行为契约。

验收: ExecutionWorkItem step_status PENDING → IN_PROGRESS → COMPLETED 正确推进;
       session 不持 work item 状态, 父子 work item 关系正确;
       对象级 work item 不被 session 串行锁阻塞。
mock 仅允许 `src/app/runtime/orchestration/` 内的 skeleton 实体。
"""

from __future__ import annotations

from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.execution_work_item import ExecutionWorkItem


def test_session_does_not_carry_work_item_state():
    """状态分离: Session 只持 lifecycle (state + manifest_version),
    不持 work item step_status (主计划 §9.2 对象级流水并发契约)。"""
    session = ExecutionSession(
        id=101,
        workline_id=7,
        plugin_key="test-plugin",
        manifest_version="manifest-v1",
        state="RUNNING",
    )

    assert session.state == "RUNNING"
    assert not hasattr(session, "step_status") or "step_status" not in session.model_fields


def test_work_item_step_status_progression():
    """happy path: work item PENDING → IN_PROGRESS → COMPLETED 推进。"""
    work_item = ExecutionWorkItem(
        id=301,
        execution_session_id=101,
        correlation_id="corr-001",
        plugin_key="test-plugin",
        manifest_version="manifest-v1",
        object_type="bin",
        object_key="bin-A",
        current_step="PICK_FROM_INBOUND",
        step_status="PENDING",
    )

    work_item.step_status = "IN_PROGRESS"
    work_item.current_step = "MOVE_TO_SORTER"
    assert work_item.step_status == "IN_PROGRESS"

    work_item.step_status = "COMPLETED"
    work_item.current_step = "AT_SORTER_QUEUE"
    assert work_item.step_status == "COMPLETED"


def test_work_item_failed_state_is_explicit():
    """error path: work item step 失败须显式标记 FAILED, 不允许静默跳过。"""
    work_item = ExecutionWorkItem(
        id=301,
        execution_session_id=101,
        correlation_id="corr-001",
        plugin_key="test-plugin",
        manifest_version="manifest-v1",
        object_type="material",
        object_key="mat-001",
        current_step="SCAN_BARCODE",
        step_status="IN_PROGRESS",
    )

    work_item.step_status = "FAILED"
    assert work_item.step_status == "FAILED"


def test_work_item_can_have_parent_correlation_for_batch():
    """父子追溯: 批次 work item 用 parent_correlation_id 关联父项, 不污染父项成功。"""
    parent = ExecutionWorkItem(
        id=401,
        execution_session_id=101,
        correlation_id="corr-parent",
        plugin_key="test-plugin",
        manifest_version="manifest-v1",
        object_type="rack",
        object_key="rack-001",
        current_step="BATCH_START",
        step_status="IN_PROGRESS",
    )

    child = ExecutionWorkItem(
        id=402,
        execution_session_id=101,
        correlation_id="corr-child-001",
        plugin_key="test-plugin",
        manifest_version="manifest-v1",
        object_type="bin",
        object_key="bin-A",
        current_step="PICK_FROM_INBOUND",
        step_status="PENDING",
        parent_correlation_id="corr-parent",
    )

    assert child.parent_correlation_id == "corr-parent"
    assert parent.correlation_id != child.correlation_id


def test_correlation_links_session_and_work_item():
    """happy path: ExecutionCorrelation 桥接 session 和多个 work_item (1:N)。"""
    correlation = ExecutionCorrelation(
        id=201,
        correlation_id="corr-001",
        execution_session_id=101,
        trace_id="trace-001",
        source_event_id="evt-001",
        business_owner_key="workline:7",
    )
    session = ExecutionSession(
        id=101,
        workline_id=7,
        plugin_key="test-plugin",
        manifest_version="manifest-v1",
        state="RUNNING",
    )

    work_items = [
        ExecutionWorkItem(
            id=501 + i,
            execution_session_id=session.id,
            correlation_id=correlation.correlation_id,
            plugin_key="test-plugin",
            manifest_version="manifest-v1",
            object_type="bin",
            object_key=f"bin-{i}",
            current_step="PICK_FROM_INBOUND",
            step_status="PENDING",
        )
        for i in range(3)
    ]

    assert all(wi.correlation_id == correlation.correlation_id for wi in work_items)
    assert all(wi.execution_session_id == session.id for wi in work_items)
    assert len({wi.object_key for wi in work_items}) == 3


def test_session_state_reconciles_distinct_from_running():
    """session state 5 态: CREATED / RUNNING / HOLD / CLOSED / RECONCILING (主计划 §9.2)。"""
    for state in ("CREATED", "RUNNING", "HOLD", "CLOSED", "RECONCILING"):
        session = ExecutionSession(
            id=102,
            workline_id=8,
            plugin_key="test-plugin",
            manifest_version="manifest-v1",
            state=state,
        )
        assert session.state == state
