"""
SmtClassifierStageMachine 单元测试

覆盖所有合法状态迁移、非法迁移拒绝、通配符迁移、
以及完整工作流路径（OK / NG / ERROR）。

状态图：
  IDLE ──scan_ok──► WAITING_INSPECTION ──inspection_ok──► WAITING_CONVEYOR
    │                    │                                      │
    │ scan_ng             │ inspection_ng                        │ conveyor_complete
    ▼                    ▼                                      ▼
  WAITING_PICK_PLACE ◄──                             WAITING_OUTPUT ──output_handled──► COMPLETED
    │                                                      ▲
    │ ng_handled / pick_place_ok           │ agv_requested → WAITING_AGV_DELIVERY
    ▼                                     │                 │ agv_completed ──────────────────────►┘
    ▼
  COMPLETED
  任意状态 ──command_failed/timeout/estop──► ERROR
  任意状态 ──manual_hold/manual_resume──► 自身（保持）
  任意状态 ──manual_cancel──► COMPLETED
  任意状态 ──wcs_task_complete──► 自身（保持）
  任意状态 ──wcs_task_failed──► ERROR
"""

import pytest
from transitions import MachineError

from src.workline_plugins.smt_classifier.state_machine import (
    STAGE_STATES,
    STAGE_TRANSITIONS,
    SmtClassifierStageMachine,
    SmtClassifierStageStatus,
    get_valid_stage_transitions,
)


class ModelWithState:
    """测试用模型，带有 state 属性"""

    def __init__(self, state: str):
        self.state = state


def make_machine(state: str) -> SmtClassifierStageMachine:
    return SmtClassifierStageMachine(ModelWithState(state))


S = SmtClassifierStageStatus


# ---------------------------------------------------------------------------
# 基础结构测试
# ---------------------------------------------------------------------------


class TestStageMachineStructure:
    def test_stage_states_contains_all_expected(self):
        expected = {s.value for s in SmtClassifierStageStatus}
        assert set(STAGE_STATES) == expected

    def test_stage_transitions_is_non_empty(self):
        assert len(STAGE_TRANSITIONS) > 0

    def test_all_triggers_have_source_and_dest(self):
        for t in STAGE_TRANSITIONS:
            assert "trigger" in t
            assert "source" in t
            assert "dest" in t


# ---------------------------------------------------------------------------
# IDLE 状态迁移
# ---------------------------------------------------------------------------


class TestIdleTransitions:
    def test_idle_scan_ok_goes_to_waiting_inspection(self):
        m = make_machine(S.IDLE.value)
        m.trigger("scan_ok")
        assert m.model.state == S.WAITING_INSPECTION.value

    def test_idle_scan_ng_goes_to_waiting_pick_place(self):
        m = make_machine(S.IDLE.value)
        m.trigger("scan_ng")
        assert m.model.state == S.WAITING_PICK_PLACE.value

    def test_idle_command_failed_goes_to_error(self):
        m = make_machine(S.IDLE.value)
        m.trigger("command_failed")
        assert m.model.state == S.ERROR.value

    def test_idle_estop_goes_to_error(self):
        m = make_machine(S.IDLE.value)
        m.trigger("estop")
        assert m.model.state == S.ERROR.value

    def test_idle_timeout_goes_to_error(self):
        m = make_machine(S.IDLE.value)
        m.trigger("timeout")
        assert m.model.state == S.ERROR.value

    def test_idle_invalid_inspection_ok_raises(self):
        m = make_machine(S.IDLE.value)
        with pytest.raises(MachineError):
            m.trigger("inspection_ok")

    def test_idle_invalid_output_handled_raises(self):
        m = make_machine(S.IDLE.value)
        with pytest.raises(MachineError):
            m.trigger("output_handled")


# ---------------------------------------------------------------------------
# WAITING_INSPECTION 状态迁移
# ---------------------------------------------------------------------------


class TestWaitingInspectionTransitions:
    def test_inspection_ok_goes_to_waiting_conveyor(self):
        m = make_machine(S.WAITING_INSPECTION.value)
        m.trigger("inspection_ok")
        assert m.model.state == S.WAITING_CONVEYOR.value

    def test_inspection_ng_goes_to_waiting_pick_place(self):
        m = make_machine(S.WAITING_INSPECTION.value)
        m.trigger("inspection_ng")
        assert m.model.state == S.WAITING_PICK_PLACE.value

    def test_command_failed_goes_to_error(self):
        m = make_machine(S.WAITING_INSPECTION.value)
        m.trigger("command_failed")
        assert m.model.state == S.ERROR.value

    def test_invalid_scan_ok_raises(self):
        m = make_machine(S.WAITING_INSPECTION.value)
        with pytest.raises(MachineError):
            m.trigger("scan_ok")

    def test_invalid_output_handled_raises(self):
        m = make_machine(S.WAITING_INSPECTION.value)
        with pytest.raises(MachineError):
            m.trigger("output_handled")


# ---------------------------------------------------------------------------
# WAITING_CONVEYOR 状态迁移
# ---------------------------------------------------------------------------


class TestWaitingConveyorTransitions:
    def test_conveyor_complete_goes_to_waiting_output(self):
        m = make_machine(S.WAITING_CONVEYOR.value)
        m.trigger("conveyor_complete")
        assert m.model.state == S.WAITING_OUTPUT.value

    def test_agv_requested_goes_to_waiting_agv_delivery(self):
        m = make_machine(S.WAITING_CONVEYOR.value)
        m.trigger("agv_requested")
        assert m.model.state == S.WAITING_AGV_DELIVERY.value

    def test_command_failed_goes_to_error(self):
        m = make_machine(S.WAITING_CONVEYOR.value)
        m.trigger("command_failed")
        assert m.model.state == S.ERROR.value

    def test_invalid_scan_ok_raises(self):
        m = make_machine(S.WAITING_CONVEYOR.value)
        with pytest.raises(MachineError):
            m.trigger("scan_ok")

    def test_invalid_ng_handled_raises(self):
        m = make_machine(S.WAITING_CONVEYOR.value)
        with pytest.raises(MachineError):
            m.trigger("ng_handled")


# ---------------------------------------------------------------------------
# WAITING_AGV_DELIVERY 状态迁移
# ---------------------------------------------------------------------------


class TestWaitingAgvDeliveryTransitions:
    def test_agv_completed_goes_to_waiting_output(self):
        m = make_machine(S.WAITING_AGV_DELIVERY.value)
        m.trigger("agv_completed")
        assert m.model.state == S.WAITING_OUTPUT.value

    def test_command_failed_goes_to_error(self):
        m = make_machine(S.WAITING_AGV_DELIVERY.value)
        m.trigger("command_failed")
        assert m.model.state == S.ERROR.value

    def test_invalid_conveyor_complete_raises(self):
        m = make_machine(S.WAITING_AGV_DELIVERY.value)
        with pytest.raises(MachineError):
            m.trigger("conveyor_complete")

    def test_invalid_output_handled_raises(self):
        m = make_machine(S.WAITING_AGV_DELIVERY.value)
        with pytest.raises(MachineError):
            m.trigger("output_handled")


# ---------------------------------------------------------------------------
# WAITING_PICK_PLACE 状态迁移
# ---------------------------------------------------------------------------


class TestWaitingPickPlaceTransitions:
    def test_pick_place_ok_goes_to_waiting_inspection(self):
        m = make_machine(S.WAITING_PICK_PLACE.value)
        m.trigger("pick_place_ok")
        assert m.model.state == S.WAITING_INSPECTION.value

    def test_ng_handled_goes_to_completed(self):
        m = make_machine(S.WAITING_PICK_PLACE.value)
        m.trigger("ng_handled")
        assert m.model.state == S.COMPLETED.value

    def test_command_failed_goes_to_error(self):
        m = make_machine(S.WAITING_PICK_PLACE.value)
        m.trigger("command_failed")
        assert m.model.state == S.ERROR.value

    def test_invalid_scan_ok_raises(self):
        m = make_machine(S.WAITING_PICK_PLACE.value)
        with pytest.raises(MachineError):
            m.trigger("scan_ok")

    def test_invalid_output_handled_raises(self):
        m = make_machine(S.WAITING_PICK_PLACE.value)
        with pytest.raises(MachineError):
            m.trigger("output_handled")


# ---------------------------------------------------------------------------
# WAITING_OUTPUT 状态迁移
# ---------------------------------------------------------------------------


class TestWaitingOutputTransitions:
    def test_output_handled_goes_to_completed(self):
        m = make_machine(S.WAITING_OUTPUT.value)
        m.trigger("output_handled")
        assert m.model.state == S.COMPLETED.value

    def test_command_failed_goes_to_error(self):
        m = make_machine(S.WAITING_OUTPUT.value)
        m.trigger("command_failed")
        assert m.model.state == S.ERROR.value

    def test_invalid_scan_ok_raises(self):
        m = make_machine(S.WAITING_OUTPUT.value)
        with pytest.raises(MachineError):
            m.trigger("scan_ok")

    def test_invalid_ng_handled_raises(self):
        m = make_machine(S.WAITING_OUTPUT.value)
        with pytest.raises(MachineError):
            m.trigger("ng_handled")


# ---------------------------------------------------------------------------
# 通配符迁移（从每个非终态测试）
# ---------------------------------------------------------------------------


ACTIVE_STATES = [
    S.IDLE,
    S.WAITING_INSPECTION,
    S.WAITING_CONVEYOR,
    S.WAITING_AGV_DELIVERY,
    S.WAITING_PICK_PLACE,
    S.WAITING_OUTPUT,
]


class TestWildcardTransitions:
    @pytest.mark.parametrize("state", ACTIVE_STATES)
    def test_command_failed_always_goes_to_error(self, state: SmtClassifierStageStatus):
        m = make_machine(state.value)
        m.trigger("command_failed")
        assert m.model.state == S.ERROR.value

    @pytest.mark.parametrize("state", ACTIVE_STATES)
    def test_timeout_always_goes_to_error(self, state: SmtClassifierStageStatus):
        m = make_machine(state.value)
        m.trigger("timeout")
        assert m.model.state == S.ERROR.value

    @pytest.mark.parametrize("state", ACTIVE_STATES)
    def test_estop_always_goes_to_error(self, state: SmtClassifierStageStatus):
        m = make_machine(state.value)
        m.trigger("estop")
        assert m.model.state == S.ERROR.value

    @pytest.mark.parametrize("state", ACTIVE_STATES)
    def test_manual_hold_keeps_same_state(self, state: SmtClassifierStageStatus):
        m = make_machine(state.value)
        m.trigger("manual_hold")
        assert m.model.state == state.value

    @pytest.mark.parametrize("state", ACTIVE_STATES)
    def test_manual_resume_keeps_same_state(self, state: SmtClassifierStageStatus):
        m = make_machine(state.value)
        m.trigger("manual_resume")
        assert m.model.state == state.value

    @pytest.mark.parametrize("state", ACTIVE_STATES)
    def test_manual_cancel_goes_to_completed(self, state: SmtClassifierStageStatus):
        m = make_machine(state.value)
        m.trigger("manual_cancel")
        assert m.model.state == S.COMPLETED.value

    @pytest.mark.parametrize("state", ACTIVE_STATES)
    def test_wcs_task_complete_keeps_same_state(self, state: SmtClassifierStageStatus):
        m = make_machine(state.value)
        m.trigger("wcs_task_complete")
        assert m.model.state == state.value

    @pytest.mark.parametrize("state", ACTIVE_STATES)
    def test_wcs_task_failed_goes_to_error(self, state: SmtClassifierStageStatus):
        m = make_machine(state.value)
        m.trigger("wcs_task_failed")
        assert m.model.state == S.ERROR.value


# ---------------------------------------------------------------------------
# ERROR 状态：无恢复迁移
# ---------------------------------------------------------------------------


class TestErrorStateIsFinal:
    def test_error_no_recovery_via_scan_ok(self):
        m = make_machine(S.ERROR.value)
        with pytest.raises(MachineError):
            m.trigger("scan_ok")

    def test_error_no_recovery_via_inspection_ok(self):
        m = make_machine(S.ERROR.value)
        with pytest.raises(MachineError):
            m.trigger("inspection_ok")

    def test_error_manual_cancel_goes_to_completed(self):
        """manual_cancel 是通配符，ERROR 状态下也可触发（关闭作业）"""
        m = make_machine(S.ERROR.value)
        m.trigger("manual_cancel")
        assert m.model.state == S.COMPLETED.value


# ---------------------------------------------------------------------------
# 完整工作流路径测试
# ---------------------------------------------------------------------------


class TestCompleteWorkflows:
    def test_ok_path_idle_to_completed_via_conveyor(self):
        """OK 路径: IDLE→WAITING_INSPECTION→WAITING_CONVEYOR→WAITING_OUTPUT→COMPLETED"""
        m = make_machine(S.IDLE.value)
        m.trigger("scan_ok")
        assert m.model.state == S.WAITING_INSPECTION.value
        m.trigger("inspection_ok")
        assert m.model.state == S.WAITING_CONVEYOR.value
        m.trigger("conveyor_complete")
        assert m.model.state == S.WAITING_OUTPUT.value
        m.trigger("output_handled")
        assert m.model.state == S.COMPLETED.value

    def test_ok_path_idle_to_completed_via_agv(self):
        """AGV 路径: IDLE→WAITING_INSPECTION→WAITING_CONVEYOR→WAITING_AGV_DELIVERY→…→COMPLETED"""
        m = make_machine(S.IDLE.value)
        m.trigger("scan_ok")
        m.trigger("inspection_ok")
        m.trigger("agv_requested")
        assert m.model.state == S.WAITING_AGV_DELIVERY.value
        m.trigger("agv_completed")
        assert m.model.state == S.WAITING_OUTPUT.value
        m.trigger("output_handled")
        assert m.model.state == S.COMPLETED.value

    def test_ng_path_scan_ng_then_ng_handled(self):
        """NG 路径: IDLE→WAITING_PICK_PLACE→COMPLETED (ng_handled)"""
        m = make_machine(S.IDLE.value)
        m.trigger("scan_ng")
        assert m.model.state == S.WAITING_PICK_PLACE.value
        m.trigger("ng_handled")
        assert m.model.state == S.COMPLETED.value

    def test_ng_path_inspection_ng_then_pick_place_retry(self):
        """NG 再检路径: IDLE→WAITING_INSPECTION→WAITING_PICK_PLACE→WAITING_INSPECTION"""
        m = make_machine(S.IDLE.value)
        m.trigger("scan_ok")
        m.trigger("inspection_ng")
        assert m.model.state == S.WAITING_PICK_PLACE.value
        m.trigger("pick_place_ok")
        assert m.model.state == S.WAITING_INSPECTION.value

    def test_error_path_any_state_to_error_no_recovery(self):
        """错误路径: 任意状态→ERROR，ERROR 后无法正常继续"""
        m = make_machine(S.WAITING_INSPECTION.value)
        m.trigger("estop")
        assert m.model.state == S.ERROR.value
        # ERROR 状态下正常业务触发器全部失败
        with pytest.raises(MachineError):
            m.trigger("inspection_ok")

    def test_manual_cancel_mid_workflow(self):
        """中途人工取消：WAITING_CONVEYOR→manual_cancel→COMPLETED"""
        m = make_machine(S.WAITING_CONVEYOR.value)
        m.trigger("manual_cancel")
        assert m.model.state == S.COMPLETED.value


# ---------------------------------------------------------------------------
# may_trigger 测试
# ---------------------------------------------------------------------------


class TestMayTrigger:
    def test_may_trigger_valid_returns_true(self):
        m = make_machine(S.IDLE.value)
        assert m.may_trigger("scan_ok") is True

    def test_may_trigger_invalid_returns_false(self):
        m = make_machine(S.IDLE.value)
        assert m.may_trigger("output_handled") is False

    def test_may_trigger_none_returns_true(self):
        m = make_machine(S.IDLE.value)
        assert m.may_trigger(None) is True  # type: ignore[arg-type]

    def test_may_trigger_empty_string_returns_true(self):
        m = make_machine(S.IDLE.value)
        assert m.may_trigger("") is True


# ---------------------------------------------------------------------------
# get_valid_stage_transitions 辅助函数
# ---------------------------------------------------------------------------


class TestGetValidStageTransitions:
    def test_idle_has_expected_triggers(self):
        triggers = get_valid_stage_transitions(S.IDLE.value)
        assert "scan_ok" in triggers
        assert "scan_ng" in triggers
        assert "command_failed" in triggers
        assert "estop" in triggers
        assert "manual_cancel" in triggers

    def test_waiting_inspection_has_expected_triggers(self):
        triggers = get_valid_stage_transitions(S.WAITING_INSPECTION.value)
        assert "inspection_ok" in triggers
        assert "inspection_ng" in triggers
        assert "command_failed" in triggers

    def test_waiting_output_has_expected_triggers(self):
        triggers = get_valid_stage_transitions(S.WAITING_OUTPUT.value)
        assert "output_handled" in triggers
        assert "command_failed" in triggers
