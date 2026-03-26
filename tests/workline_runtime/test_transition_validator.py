"""
TransitionValidator 单元测试

测试状态迁移校验器：
- Phase 2 默认行为（无状态机时允许所有迁移）
- Phase 3 状态机校验（使用 transitions 库）

设计参考: 设计文档 phase2-orchestrator
"""

from unittest.mock import MagicMock

import pytest


class TestTransitionValidatorPhase2:
    """TransitionValidator Phase 2 行为测试"""

    @pytest.fixture
    def validator(self):
        """创建 TransitionValidator 实例"""
        from src.workline_runtime.transition_validator import TransitionValidator

        return TransitionValidator()

    def test_validate_returns_true_when_no_state_machine(self, validator):
        """测试无状态机时允许所有迁移"""
        is_valid, error = validator.validate(
            current_status="RUNNING",
            transition="scan_ok",
            state_machine_class=None,
        )
        assert is_valid is True
        assert error is None

    def test_validate_allows_any_transition_without_state_machine(self, validator):
        """测试无状态机时任意 transition 都被允许"""
        transitions = ["scan_ok", "scan_ng", "detect_ok", "detect_ng", "complete"]

        for transition in transitions:
            is_valid, error = validator.validate(
                current_status="NEW",
                transition=transition,
                state_machine_class=None,
            )
            assert is_valid is True, f"Transition {transition} should be allowed"
            assert error is None

    def test_validate_allows_any_status_without_state_machine(self, validator):
        """测试无状态机时任意当前状态都允许迁移"""
        statuses = ["NEW", "RUNNING", "WAITING_DEVICE_RESULT", "MANUAL_HOLD"]

        for status in statuses:
            is_valid, error = validator.validate(
                current_status=status,
                transition="proceed",
                state_machine_class=None,
            )
            assert is_valid is True
            assert error is None


class TestTransitionValidatorPhase3:
    """TransitionValidator Phase 3 行为测试（使用 transitions 库）"""

    @pytest.fixture
    def validator(self):
        """创建 TransitionValidator 实例"""
        from src.workline_runtime.transition_validator import TransitionValidator

        return TransitionValidator()

    @pytest.fixture
    def mock_state_machine_class(self):
        """创建模拟的状态机类"""

        class MockStateMachine:
            """模拟 transitions 库的状态机"""

            def __init__(self, model):
                self.model = model
                self._valid_transitions = {
                    "NEW": ["start"],
                    "RUNNING": ["scan_ok", "scan_ng", "complete"],
                    "WAITING_DEVICE_RESULT": ["result_ok", "result_ng", "timeout"],
                }

            def may_trigger(self, transition: str) -> bool:
                current = self.model.state
                valid = self._valid_transitions.get(current, [])
                return transition in valid

        return MockStateMachine

    def test_validate_valid_transition(self, validator, mock_state_machine_class):
        """测试有效的状态迁移"""
        is_valid, error = validator.validate(
            current_status="NEW",
            transition="start",
            state_machine_class=mock_state_machine_class,
        )
        assert is_valid is True
        assert error is None

    def test_validate_valid_transition_from_running(self, validator, mock_state_machine_class):
        """测试从 RUNNING 状态的有效迁移"""
        valid_transitions = ["scan_ok", "scan_ng", "complete"]

        for transition in valid_transitions:
            is_valid, error = validator.validate(
                current_status="RUNNING",
                transition=transition,
                state_machine_class=mock_state_machine_class,
            )
            assert is_valid is True, f"Transition {transition} should be valid from RUNNING"
            assert error is None

    def test_validate_invalid_transition(self, validator, mock_state_machine_class):
        """测试无效的状态迁移"""
        is_valid, error = validator.validate(
            current_status="NEW",
            transition="scan_ok",  # NEW 状态不允许 scan_ok
            state_machine_class=mock_state_machine_class,
        )
        assert is_valid is False
        assert "Invalid transition" in error
        assert "scan_ok" in error
        assert "NEW" in error

    def test_validate_invalid_transition_from_running(self, validator, mock_state_machine_class):
        """测试从 RUNNING 状态的无效迁移"""
        is_valid, error = validator.validate(
            current_status="RUNNING",
            transition="start",  # RUNNING 状态不允许 start
            state_machine_class=mock_state_machine_class,
        )
        assert is_valid is False
        assert "Invalid transition" in error

    def test_validate_handles_state_machine_exception(self, validator):
        """测试状态机抛出异常时的处理"""

        class BrokenStateMachine:
            def __init__(self, model):
                raise ValueError("State machine initialization failed")

        is_valid, error = validator.validate(
            current_status="RUNNING",
            transition="proceed",
            state_machine_class=BrokenStateMachine,
        )
        assert is_valid is False
        assert "Transition validation error" in error


class TestTransitionValidatorEdgeCases:
    """TransitionValidator 边界情况测试"""

    @pytest.fixture
    def validator(self):
        """创建 TransitionValidator 实例"""
        from src.workline_runtime.transition_validator import TransitionValidator

        return TransitionValidator()

    def test_validate_empty_transition_without_state_machine(self, validator):
        """测试空 transition 无状态机时"""
        is_valid, error = validator.validate(
            current_status="RUNNING",
            transition="",
            state_machine_class=None,
        )
        # Phase 2 允许空 transition（插件可能不触发迁移）
        assert is_valid is True
        assert error is None

    def test_validate_none_transition_without_state_machine(self, validator):
        """测试 None transition 无状态机时"""
        is_valid, error = validator.validate(
            current_status="RUNNING",
            transition=None,  # type: ignore[arg-type]
            state_machine_class=None,
        )
        # Phase 2 允许 None transition
        assert is_valid is True
        assert error is None

    def test_validate_empty_current_status(self, validator):
        """测试空当前状态"""
        is_valid, error = validator.validate(
            current_status="",
            transition="start",
            state_machine_class=None,
        )
        # Phase 2 允许
        assert is_valid is True
        assert error is None
