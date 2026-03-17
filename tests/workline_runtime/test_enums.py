"""
作业线运行时枚举类单元测试

测试覆盖：
- 枚举值的完整性和正确性
- 枚举值的唯一性
- 枚举值的字符串表示
- 枚举值的业务语义

参考文档：
- docs/workline_plugin_architecture_design.md 第 6 章（运行时契约）
- docs/workline_plugin_architecture_design.md 第 8 章（领域模型）
- docs/workline_plugin_architecture_design.md 第 12 章（故障归因）
"""

import pytest

from workline_runtime.enums import (
    DecisionType,
    FailureDomain,
    InboundKind,
    InboxStatus,
    ManualOperationType,
    OutboxDispatchType,
    OutboxStatus,
    SessionStatus,
    TimelineStage,
)


class TestInboundKind:
    """测试 InboundKind 枚举"""

    def test_should_have_all_required_kinds(self):
        """应包含所有必需的输入类型（架构 6.1 节）"""
        expected_kinds = [
            "DEVICE_EVENT",
            "COMMAND_RESULT",
            "EXTERNAL_CALLBACK",
            "TIMEOUT",
            "MANUAL_OPERATION",
        ]
        actual_kinds = [kind.value for kind in InboundKind]
        assert sorted(actual_kinds) == sorted(expected_kinds)

    def test_should_have_unique_values(self):
        """所有枚举值应该唯一"""
        values = [kind.value for kind in InboundKind]
        assert len(values) == len(set(values))

    def test_should_be_string_enum(self):
        """枚举值应该是字符串类型"""
        assert isinstance(InboundKind.DEVICE_EVENT.value, str)
        assert isinstance(InboundKind.COMMAND_RESULT.value, str)


class TestSessionStatus:
    """测试 SessionStatus 枚举"""

    def test_should_have_all_required_statuses(self):
        """应包含所有必需的会话状态（架构 8.3 节）"""
        expected_statuses = [
            "NEW",
            "RUNNING",
            "WAITING_DEVICE_RESULT",
            "WAITING_EXTERNAL",
            "MANUAL_HOLD",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        ]
        actual_statuses = [status.value for status in SessionStatus]
        assert sorted(actual_statuses) == sorted(expected_statuses)

    def test_should_have_initial_state(self):
        """应该有明确的初始状态"""
        assert SessionStatus.NEW.value == "NEW"

    def test_should_have_terminal_states(self):
        """应该有明确的终态"""
        terminal_states = [
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        ]
        assert len(terminal_states) == 3


class TestFailureDomain:
    """测试 FailureDomain 枚举"""

    def test_should_have_all_required_domains(self):
        """应包含所有故障域（架构 12.1 节）"""
        expected_domains = [
            "HARDWARE",
            "NETWORK",
            "SOFTWARE",
            "ORCHESTRATION",
            "ALGORITHM",
            "UPSTREAM",
            "DOWNSTREAM",
            "CONFIG",
            "DATA",
            "TIMEOUT",
            "MANUAL_INTERVENTION",
        ]
        actual_domains = [domain.value for domain in FailureDomain]
        assert sorted(actual_domains) == sorted(expected_domains)

    def test_should_have_external_system_domains(self):
        """应该包含外部系统相关故障域"""
        assert FailureDomain.UPSTREAM.value == "UPSTREAM"
        assert FailureDomain.DOWNSTREAM.value == "DOWNSTREAM"


class TestTimelineStage:
    """测试 TimelineStage 枚举"""

    def test_should_have_all_required_stages(self):
        """应包含所有时间线阶段（架构 8.4 节）"""
        expected_stages = [
            "INGEST",
            "ROUTE",
            "DECISION",
            "DISPATCH_PREPARE",
            "WAITING",
            "CALLBACK",
            "MANUAL",
            "TIMEOUT",
            "COMPENSATION",
            "COMPLETE",
            "FAIL",
        ]
        actual_stages = [stage.value for stage in TimelineStage]
        assert sorted(actual_stages) == sorted(expected_stages)

    def test_should_have_main_flow_stages(self):
        """应该包含主流程阶段"""
        assert TimelineStage.INGEST.value == "INGEST"
        assert TimelineStage.DECISION.value == "DECISION"
        assert TimelineStage.COMPLETE.value == "COMPLETE"


class TestInboxStatus:
    """测试 InboxStatus 枚举"""

    def test_should_have_all_required_statuses(self):
        """应包含所有 Inbox 状态（架构 8.7 节）"""
        expected_statuses = ["NEW", "PROCESSING", "PROCESSED", "FAILED"]
        actual_statuses = [status.value for status in InboxStatus]
        assert sorted(actual_statuses) == sorted(expected_statuses)

    def test_should_have_initial_status(self):
        """初始状态应该是 NEW"""
        assert InboxStatus.NEW.value == "NEW"


class TestOutboxStatus:
    """测试 OutboxStatus 枚举"""

    def test_should_have_all_required_statuses(self):
        """应包含所有 Outbox 状态（架构 8.8 节）"""
        expected_statuses = ["NEW", "DISPATCHING", "SENT", "ACKED", "FAILED", "CANCELLED"]
        actual_statuses = [status.value for status in OutboxStatus]
        assert sorted(actual_statuses) == sorted(expected_statuses)

    def test_should_track_dispatch_progression(self):
        """状态应该反映派发进度"""
        assert OutboxStatus.NEW.value == "NEW"
        assert OutboxStatus.DISPATCHING.value == "DISPATCHING"
        assert OutboxStatus.SENT.value == "SENT"
        assert OutboxStatus.ACKED.value == "ACKED"


class TestOutboxDispatchType:
    """测试 OutboxDispatchType 枚举"""

    def test_should_have_all_required_types(self):
        """应包含所有派发类型（架构 8.8 节）"""
        expected_types = ["DEVICE_COMMAND", "EXTERNAL_HTTP", "INTERNAL_SIGNAL"]
        actual_types = [t.value for t in OutboxDispatchType]
        assert sorted(actual_types) == sorted(expected_types)


class TestManualOperationType:
    """测试 ManualOperationType 枚举"""

    def test_should_have_all_required_operations(self):
        """应包含所有人工操作类型（架构 6.7 节）"""
        expected_operations = [
            "RETRY_LAST_COMMAND",
            "MARK_SUCCESS_AND_CONTINUE",
            "MARK_NG_AND_CLOSE",
            "CANCEL_SESSION",
            "CUSTOM_ACTION",
        ]
        actual_operations = [op.value for op in ManualOperationType]
        assert sorted(actual_operations) == sorted(expected_operations)


class TestDecisionType:
    """测试 DecisionType 枚举"""

    def test_should_have_common_decision_types(self):
        """应包含常见决策类型（架构 8.5 节场景）"""
        expected_types = [
            "BARCODE_VALIDATION",
            "BIN_ALLOCATION",
            "ROUTE_SELECTION",
            "RESOURCE_REQUEST",
            "FAILURE_CLASSIFICATION",
        ]
        actual_types = [dt.value for dt in DecisionType]
        for expected in expected_types:
            assert expected in actual_types


class TestEnumValueUniqueness:
    """跨枚举类的唯一性测试"""

    def test_all_enums_should_have_unique_string_values(self):
        """所有枚举类的字符串值应该在各自类内唯一"""
        enums_to_test = [
            InboundKind,
            SessionStatus,
            FailureDomain,
            TimelineStage,
            InboxStatus,
            OutboxStatus,
            OutboxDispatchType,
            ManualOperationType,
            DecisionType,
        ]

        for enum_class in enums_to_test:
            values = [item.value for item in enum_class]
            assert len(values) == len(set(values)), f"{enum_class.__name__} 有重复的枚举值"


class TestEnumStringRepresentation:
    """枚举字符串表示测试"""

    def test_inbound_kind_string_representation(self):
        """InboundKind 应该返回正确的字符串表示"""
        # Python Enum 的 str() 返回 "ClassName.VALUE" 格式
        assert "InboundKind" in str(InboundKind.DEVICE_EVENT)
        assert "DEVICE_EVENT" in str(InboundKind.DEVICE_EVENT)
        # 获取纯值应该使用 .value 属性
        assert InboundKind.DEVICE_EVENT.value == "DEVICE_EVENT"

    def test_session_status_string_representation(self):
        """SessionStatus 应该返回正确的字符串表示"""
        # Python Enum 的 str() 返回 "ClassName.VALUE" 格式
        assert "SessionStatus" in str(SessionStatus.RUNNING)
        assert "RUNNING" in str(SessionStatus.RUNNING)
        # 获取纯值应该使用 .value 属性
        assert SessionStatus.RUNNING.value == "RUNNING"

    def test_failure_domain_string_representation(self):
        """FailureDomain 应该返回正确的字符串表示"""
        # Python Enum 的 str() 返回 "ClassName.VALUE" 格式
        assert "FailureDomain" in str(FailureDomain.HARDWARE)
        assert "HARDWARE" in str(FailureDomain.HARDWARE)
        # 获取纯值应该使用 .value 属性
        assert FailureDomain.HARDWARE.value == "HARDWARE"


class TestEnumBusinessSemantics:
    """枚举业务语义测试"""

    def test_waiting_statuses_should_be_distinct(self):
        """等待状态应该清晰区分"""
        waiting_statuses = [
            SessionStatus.WAITING_DEVICE_RESULT,
            SessionStatus.WAITING_EXTERNAL,
        ]
        assert len(waiting_statuses) == 2
        assert SessionStatus.WAITING_DEVICE_RESULT.value != SessionStatus.WAITING_EXTERNAL.value

    def test_failure_domains_should_cover_all_boundaries(self):
        """故障域应该覆盖所有边界"""
        # 硬件边界
        assert FailureDomain.HARDWARE.value == "HARDWARE"
        # 网络边界
        assert FailureDomain.NETWORK.value == "NETWORK"
        # 外部系统边界
        assert FailureDomain.UPSTREAM.value == "UPSTREAM"
        assert FailureDomain.DOWNSTREAM.value == "DOWNSTREAM"
        # 内部边界
        assert FailureDomain.SOFTWARE.value == "SOFTWARE"
        assert FailureDomain.CONFIG.value == "CONFIG"
        assert FailureDomain.DATA.value == "DATA"

    def test_manual_operations_should_support_recovery(self):
        """人工操作应该支持恢复场景"""
        recovery_ops = [
            ManualOperationType.RETRY_LAST_COMMAND,
            ManualOperationType.MARK_SUCCESS_AND_CONTINUE,
        ]
        assert len(recovery_ops) == 2
