"""
SMT 粗分机状态机单元测试

测试所有状态迁移：
- 有效迁移被接受
- 无效迁移被拒绝
- 边界情况处理

设计参考: 设计文档 phase2-orchestrator
"""

import pytest
from transitions import MachineError

from src.workline_plugins.smt_classifier.state_machine import (
    STATES,
    TRANSITIONS,
    SmtClassifierStateMachine,
    SmtClassifierStatus,
    get_valid_transitions,
)


class ModelWithState:
    """测试用模型，带有 state 属性"""

    def __init__(self, state: str):
        self.state = state


class TestSmtClassifierStatus:
    """SmtClassifierStatus 枚举测试"""

    def test_status_values(self):
        """测试状态枚举值"""
        assert SmtClassifierStatus.NEW == "NEW"
        assert SmtClassifierStatus.RUNNING == "RUNNING"
        assert SmtClassifierStatus.WAITING_SCAN_RESULT == "WAITING_SCAN_RESULT"
        assert SmtClassifierStatus.WAITING_DETECT_RESULT == "WAITING_DETECT_RESULT"
        assert SmtClassifierStatus.WAITING_MOVE_RESULT == "WAITING_MOVE_RESULT"
        assert SmtClassifierStatus.WAITING_PUT_RESULT == "WAITING_PUT_RESULT"
        assert SmtClassifierStatus.MANUAL_HOLD == "MANUAL_HOLD"
        assert SmtClassifierStatus.COMPLETED == "COMPLETED"
        assert SmtClassifierStatus.FAILED == "FAILED"
        assert SmtClassifierStatus.CANCELLED == "CANCELLED"

    def test_status_count(self):
        """测试状态数量"""
        assert len(SmtClassifierStatus) == 10

    def test_status_is_string(self):
        """测试状态是字符串枚举"""
        assert isinstance(SmtClassifierStatus.NEW.value, str)
        assert SmtClassifierStatus.RUNNING.value == "RUNNING"


class TestStatesAndTransitions:
    """状态和迁移定义测试"""

    def test_states_count(self):
        """测试状态列表数量"""
        assert len(STATES) == 10

    def test_states_match_enum(self):
        """测试状态列表与枚举匹配"""
        enum_values = [s.value for s in SmtClassifierStatus]
        assert set(STATES) == set(enum_values)

    def test_transitions_exist(self):
        """测试迁移定义存在"""
        assert len(TRANSITIONS) > 0

    def test_transitions_have_required_keys(self):
        """测试迁移定义包含必需字段"""
        required_keys = {"trigger", "source", "dest"}
        for transition in TRANSITIONS:
            assert required_keys <= set(transition.keys())


class TestStateMachineNewState:
    """NEW 状态的迁移测试"""

    @pytest.fixture
    def state_machine(self):
        """创建 NEW 状态的状态机"""
        model = ModelWithState("NEW")
        return SmtClassifierStateMachine(model)

    def test_valid_transition_start(self, state_machine):
        """测试 NEW -> RUNNING 迁移"""
        assert state_machine.may_trigger("start") is True
        assert state_machine.trigger("start") is True
        assert state_machine.model.state == "RUNNING"

    def test_valid_transition_fail(self, state_machine):
        """测试 NEW -> FAILED 迁移（任意状态可失败）"""
        assert state_machine.may_trigger("fail") is True
        assert state_machine.trigger("fail") is True
        assert state_machine.model.state == "FAILED"

    def test_valid_transition_estop(self, state_machine):
        """测试 NEW -> MANUAL_HOLD 迁移（任意状态可急停）"""
        assert state_machine.may_trigger("estop") is True
        assert state_machine.trigger("estop") is True
        assert state_machine.model.state == "MANUAL_HOLD"

    def test_invalid_transition_scan_ok(self, state_machine):
        """测试无效迁移 scan_ok"""
        assert state_machine.may_trigger("scan_ok") is False

    def test_invalid_transition_wait_scan(self, state_machine):
        """测试无效迁移 wait_scan"""
        assert state_machine.may_trigger("wait_scan") is False

    def test_get_valid_transitions(self, state_machine):
        """测试获取有效迁移列表"""
        transitions = state_machine.get_valid_transitions()
        assert "start" in transitions
        assert "fail" in transitions
        assert "estop" in transitions
        assert "scan_ok" not in transitions


class TestStateMachineRunningState:
    """RUNNING 状态的迁移测试"""

    @pytest.fixture
    def state_machine(self):
        """创建 RUNNING 状态的状态机"""
        model = ModelWithState("RUNNING")
        return SmtClassifierStateMachine(model)

    def test_valid_transition_wait_scan(self, state_machine):
        """测试 RUNNING -> WAITING_SCAN_RESULT 迁移"""
        assert state_machine.may_trigger("wait_scan") is True
        assert state_machine.trigger("wait_scan") is True
        assert state_machine.model.state == "WAITING_SCAN_RESULT"

    def test_valid_transition_wait_detect(self, state_machine):
        """测试 RUNNING -> WAITING_DETECT_RESULT 迁移"""
        assert state_machine.may_trigger("wait_detect") is True
        assert state_machine.trigger("wait_detect") is True
        assert state_machine.model.state == "WAITING_DETECT_RESULT"

    def test_valid_transition_wait_move(self, state_machine):
        """测试 RUNNING -> WAITING_MOVE_RESULT 迁移"""
        assert state_machine.may_trigger("wait_move") is True
        assert state_machine.trigger("wait_move") is True
        assert state_machine.model.state == "WAITING_MOVE_RESULT"

    def test_valid_transition_wait_put(self, state_machine):
        """测试 RUNNING -> WAITING_PUT_RESULT 迁移"""
        assert state_machine.may_trigger("wait_put") is True
        assert state_machine.trigger("wait_put") is True
        assert state_machine.model.state == "WAITING_PUT_RESULT"

    def test_valid_transition_estop(self, state_machine):
        """测试 RUNNING -> MANUAL_HOLD 迁移"""
        assert state_machine.may_trigger("estop") is True
        assert state_machine.trigger("estop") is True
        assert state_machine.model.state == "MANUAL_HOLD"

    def test_valid_transition_fail(self, state_machine):
        """测试 RUNNING -> FAILED 迁移"""
        assert state_machine.may_trigger("fail") is True
        assert state_machine.trigger("fail") is True
        assert state_machine.model.state == "FAILED"

    def test_valid_transition_cancel(self, state_machine):
        """测试 RUNNING -> CANCELLED 迁移"""
        assert state_machine.may_trigger("cancel") is True
        assert state_machine.trigger("cancel") is True
        assert state_machine.model.state == "CANCELLED"

    def test_invalid_transition_start(self, state_machine):
        """测试无效迁移 start（已经在 RUNNING）"""
        assert state_machine.may_trigger("start") is False

    def test_invalid_transition_scan_ok(self, state_machine):
        """测试无效迁移 scan_ok（需要先进入等待状态）"""
        assert state_machine.may_trigger("scan_ok") is False


class TestStateMachineWaitingScanResult:
    """WAITING_SCAN_RESULT 状态的迁移测试"""

    @pytest.fixture
    def state_machine(self):
        """创建 WAITING_SCAN_RESULT 状态的状态机"""
        model = ModelWithState("WAITING_SCAN_RESULT")
        return SmtClassifierStateMachine(model)

    def test_valid_transition_scan_ok(self, state_machine):
        """测试 WAITING_SCAN_RESULT -> RUNNING 迁移"""
        assert state_machine.may_trigger("scan_ok") is True
        assert state_machine.trigger("scan_ok") is True
        assert state_machine.model.state == "RUNNING"

    def test_valid_transition_scan_ng(self, state_machine):
        """测试 WAITING_SCAN_RESULT -> FAILED 迁移"""
        assert state_machine.may_trigger("scan_ng") is True
        assert state_machine.trigger("scan_ng") is True
        assert state_machine.model.state == "FAILED"

    def test_valid_transition_estop(self, state_machine):
        """测试 WAITING_SCAN_RESULT -> MANUAL_HOLD 迁移"""
        assert state_machine.may_trigger("estop") is True
        assert state_machine.trigger("estop") is True
        assert state_machine.model.state == "MANUAL_HOLD"

    def test_invalid_transition_start(self, state_machine):
        """测试无效迁移 start"""
        assert state_machine.may_trigger("start") is False

    def test_invalid_transition_detect_ok(self, state_machine):
        """测试无效迁移 detect_ok"""
        assert state_machine.may_trigger("detect_ok") is False


class TestStateMachineWaitingDetectResult:
    """WAITING_DETECT_RESULT 状态的迁移测试"""

    @pytest.fixture
    def state_machine(self):
        """创建 WAITING_DETECT_RESULT 状态的状态机"""
        model = ModelWithState("WAITING_DETECT_RESULT")
        return SmtClassifierStateMachine(model)

    def test_valid_transition_detect_ok(self, state_machine):
        """测试 WAITING_DETECT_RESULT -> RUNNING 迁移（OK）"""
        assert state_machine.may_trigger("detect_ok") is True
        assert state_machine.trigger("detect_ok") is True
        assert state_machine.model.state == "RUNNING"

    def test_valid_transition_detect_ng(self, state_machine):
        """测试 WAITING_DETECT_RESULT -> RUNNING 迁移（NG，特殊处理后继续）"""
        assert state_machine.may_trigger("detect_ng") is True
        assert state_machine.trigger("detect_ng") is True
        assert state_machine.model.state == "RUNNING"

    def test_valid_transition_estop(self, state_machine):
        """测试 WAITING_DETECT_RESULT -> MANUAL_HOLD 迁移"""
        assert state_machine.may_trigger("estop") is True
        assert state_machine.trigger("estop") is True
        assert state_machine.model.state == "MANUAL_HOLD"

    def test_invalid_transition_scan_ok(self, state_machine):
        """测试无效迁移 scan_ok"""
        assert state_machine.may_trigger("scan_ok") is False


class TestStateMachineWaitingMoveResult:
    """WAITING_MOVE_RESULT 状态的迁移测试"""

    @pytest.fixture
    def state_machine(self):
        """创建 WAITING_MOVE_RESULT 状态的状态机"""
        model = ModelWithState("WAITING_MOVE_RESULT")
        return SmtClassifierStateMachine(model)

    def test_valid_transition_move_ok(self, state_machine):
        """测试 WAITING_MOVE_RESULT -> RUNNING 迁移"""
        assert state_machine.may_trigger("move_ok") is True
        assert state_machine.trigger("move_ok") is True
        assert state_machine.model.state == "RUNNING"

    def test_valid_transition_estop(self, state_machine):
        """测试 WAITING_MOVE_RESULT -> MANUAL_HOLD 迁移"""
        assert state_machine.may_trigger("estop") is True
        assert state_machine.trigger("estop") is True
        assert state_machine.model.state == "MANUAL_HOLD"

    def test_invalid_transition_scan_ok(self, state_machine):
        """测试无效迁移 scan_ok"""
        assert state_machine.may_trigger("scan_ok") is False


class TestStateMachineWaitingPutResult:
    """WAITING_PUT_RESULT 状态的迁移测试"""

    @pytest.fixture
    def state_machine(self):
        """创建 WAITING_PUT_RESULT 状态的状态机"""
        model = ModelWithState("WAITING_PUT_RESULT")
        return SmtClassifierStateMachine(model)

    def test_valid_transition_put_ok(self, state_machine):
        """测试 WAITING_PUT_RESULT -> COMPLETED 迁移"""
        assert state_machine.may_trigger("put_ok") is True
        assert state_machine.trigger("put_ok") is True
        assert state_machine.model.state == "COMPLETED"

    def test_valid_transition_estop(self, state_machine):
        """测试 WAITING_PUT_RESULT -> MANUAL_HOLD 迁移"""
        assert state_machine.may_trigger("estop") is True
        assert state_machine.trigger("estop") is True
        assert state_machine.model.state == "MANUAL_HOLD"

    def test_invalid_transition_scan_ok(self, state_machine):
        """测试无效迁移 scan_ok"""
        assert state_machine.may_trigger("scan_ok") is False


class TestStateMachineManualHold:
    """MANUAL_HOLD 状态的迁移测试"""

    @pytest.fixture
    def state_machine(self):
        """创建 MANUAL_HOLD 状态的状态机"""
        model = ModelWithState("MANUAL_HOLD")
        return SmtClassifierStateMachine(model)

    def test_valid_transition_retry(self, state_machine):
        """测试 MANUAL_HOLD -> RUNNING 迁移"""
        assert state_machine.may_trigger("retry") is True
        assert state_machine.trigger("retry") is True
        assert state_machine.model.state == "RUNNING"

    def test_valid_transition_fail(self, state_machine):
        """测试 MANUAL_HOLD -> FAILED 迁移"""
        assert state_machine.may_trigger("fail") is True
        assert state_machine.trigger("fail") is True
        assert state_machine.model.state == "FAILED"

    def test_invalid_transition_start(self, state_machine):
        """测试无效迁移 start"""
        assert state_machine.may_trigger("start") is False

    def test_invalid_transition_scan_ok(self, state_machine):
        """测试无效迁移 scan_ok"""
        assert state_machine.may_trigger("scan_ok") is False


class TestStateMachineTerminalStates:
    """终态测试（COMPLETED, FAILED, CANCELLED）"""

    def test_completed_no_valid_transitions(self):
        """测试 COMPLETED 状态无有效迁移"""
        model = ModelWithState("COMPLETED")
        sm = SmtClassifierStateMachine(model)

        # 只有 fail 和 estop 可用（来自通配符规则）
        valid_transitions = sm.get_valid_transitions()
        # COMPLETED 不应该有常规迁移，但通配符迁移仍然有效
        assert "fail" in valid_transitions
        assert "estop" in valid_transitions
        # 业务迁移不应该存在
        assert "start" not in valid_transitions
        assert "scan_ok" not in valid_transitions

    def test_failed_state_can_still_estop(self):
        """测试 FAILED 状态仍可触发 fail（幂等）"""
        model = ModelWithState("FAILED")
        sm = SmtClassifierStateMachine(model)

        # fail 迁移在 FAILED 状态仍然有效（通配符）
        assert sm.may_trigger("fail") is True
        assert sm.trigger("fail") is True
        assert sm.model.state == "FAILED"

    def test_cancelled_state_can_fail(self):
        """测试 CANCELLED 状态可触发 fail"""
        model = ModelWithState("CANCELLED")
        sm = SmtClassifierStateMachine(model)

        assert sm.may_trigger("fail") is True
        assert sm.trigger("fail") is True
        assert sm.model.state == "FAILED"


class TestStateMachineWorkflow:
    """完整工作流测试"""

    def test_happy_path_scan_detect_put(self):
        """测试正常流程：扫码 -> 检测 -> 放置"""
        model = ModelWithState("NEW")
        sm = SmtClassifierStateMachine(model)

        # 启动
        assert sm.trigger("start") is True
        assert model.state == "RUNNING"

        # 扫码
        assert sm.trigger("wait_scan") is True
        assert model.state == "WAITING_SCAN_RESULT"
        assert sm.trigger("scan_ok") is True
        assert model.state == "RUNNING"

        # 检测
        assert sm.trigger("wait_detect") is True
        assert model.state == "WAITING_DETECT_RESULT"
        assert sm.trigger("detect_ok") is True
        assert model.state == "RUNNING"

        # 放置
        assert sm.trigger("wait_put") is True
        assert model.state == "WAITING_PUT_RESULT"
        assert sm.trigger("put_ok") is True
        assert model.state == "COMPLETED"

    def test_scan_ng_flow(self):
        """测试扫码失败流程"""
        model = ModelWithState("NEW")
        sm = SmtClassifierStateMachine(model)

        sm.trigger("start")
        sm.trigger("wait_scan")
        assert sm.trigger("scan_ng") is True
        assert model.state == "FAILED"

    def test_detect_ng_continue_flow(self):
        """测试检测 NG 后继续流程"""
        model = ModelWithState("NEW")
        sm = SmtClassifierStateMachine(model)

        sm.trigger("start")
        sm.trigger("wait_detect")
        # NG 也返回 RUNNING（特殊处理）
        assert sm.trigger("detect_ng") is True
        assert model.state == "RUNNING"

    def test_estop_and_retry_flow(self):
        """测试急停和重试流程"""
        model = ModelWithState("NEW")
        sm = SmtClassifierStateMachine(model)

        sm.trigger("start")
        sm.trigger("wait_scan")

        # 急停
        assert sm.trigger("estop") is True
        assert model.state == "MANUAL_HOLD"

        # 重试
        assert sm.trigger("retry") is True
        assert model.state == "RUNNING"

    def test_cancel_flow(self):
        """测试取消流程"""
        model = ModelWithState("NEW")
        sm = SmtClassifierStateMachine(model)

        sm.trigger("start")
        assert sm.trigger("cancel") is True
        assert model.state == "CANCELLED"


class TestGetValidTransitionsFunction:
    """get_valid_transitions 工具函数测试"""

    def test_new_state_transitions(self):
        """测试 NEW 状态的有效迁移"""
        transitions = get_valid_transitions("NEW")
        assert "start" in transitions
        assert "fail" in transitions
        assert "estop" in transitions

    def test_running_state_transitions(self):
        """测试 RUNNING 状态的有效迁移"""
        transitions = get_valid_transitions("RUNNING")
        assert "wait_scan" in transitions
        assert "wait_detect" in transitions
        assert "wait_move" in transitions
        assert "wait_put" in transitions
        assert "estop" in transitions
        assert "fail" in transitions
        assert "cancel" in transitions

    def test_waiting_scan_state_transitions(self):
        """测试 WAITING_SCAN_RESULT 状态的有效迁移"""
        transitions = get_valid_transitions("WAITING_SCAN_RESULT")
        assert "scan_ok" in transitions
        assert "scan_ng" in transitions
        assert "estop" in transitions
        assert "fail" in transitions

    def test_manual_hold_state_transitions(self):
        """测试 MANUAL_HOLD 状态的有效迁移"""
        transitions = get_valid_transitions("MANUAL_HOLD")
        assert "retry" in transitions
        assert "fail" in transitions
        assert "estop" in transitions


class TestStateMachineException:
    """状态机异常测试"""

    def test_trigger_invalid_transition_raises_exception(self):
        """测试触发无效迁移抛出异常"""
        model = ModelWithState("NEW")
        sm = SmtClassifierStateMachine(model)

        # 使用 MachineError 检查无效迁移
        with pytest.raises(MachineError):
            sm.trigger("scan_ok")

    def test_trigger_multiple_invalid_raises_exception(self):
        """测试多次触发无效迁移"""
        model = ModelWithState("COMPLETED")
        sm = SmtClassifierStateMachine(model)

        # COMPLETED 状态不应该有常规业务迁移
        with pytest.raises(MachineError):
            sm.trigger("start")


class TestStateMachineWildcardTransitions:
    """通配符迁移测试"""

    def test_fail_from_any_state(self):
        """测试从任意状态可以 fail"""
        for state in STATES:
            model = ModelWithState(state)
            sm = SmtClassifierStateMachine(model)
            assert sm.may_trigger("fail") is True, f"fail should be valid from {state}"

    def test_estop_from_any_state(self):
        """测试从任意状态可以 estop"""
        for state in STATES:
            model = ModelWithState(state)
            sm = SmtClassifierStateMachine(model)
            # 注意：MANUAL_HOLD 状态本身不能再次 estop
            if state != "MANUAL_HOLD":
                assert sm.may_trigger("estop") is True, f"estop should be valid from {state}"
