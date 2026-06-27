"""
TimelineGenerator 单元测试

测试 Phase 2 时间线记录生成器：
- 从 Session 提取关联信息
- 自动填充时间戳
- 支持多种阶段类型
- 处理 payload 数据

设计参考:
- 设计文档: phase2-orchestrator design doc
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.app.workline.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.utils.timezone import timezone
from src.workline_runtime.timeline_generator import TimelineGenerator


class TestTimelineGenerator:
    """TimelineGenerator 测试"""

    @pytest.fixture
    def generator(self):
        """创建 TimelineGenerator 实例"""
        return TimelineGenerator()

    @pytest.fixture
    def timeline_session(self):
        """创建模拟的 WorklineSession"""
        from tests.workline_runtime.support.runtime_builders import make_mock_session

        return make_mock_session(id=1001, workline_id=2001, trace_id="trace-abc-123")

    def test_generate_creates_timeline_with_session_info(self, generator, timeline_session):
        """测试生成 Timeline 记录包含 Session 信息"""
        timeline = generator.generate(
            session=timeline_session,
            stage=TimelineStage.INGEST,
            action_type=TimelineActionType.SESSION_CREATED,
        )

        assert isinstance(timeline, WorklineTimeline)
        assert timeline.session_id == 1001
        assert timeline.workline_id == 2001
        assert timeline.trace_id == "trace-abc-123"

    def test_generate_includes_from_and_to_status(self, generator, timeline_session):
        """测试生成 Timeline 记录包含状态转换信息"""
        timeline = generator.generate(
            session=timeline_session,
            stage=TimelineStage.ROUTE,
            action_type=TimelineActionType.STATUS_CHANGED,
            from_status="NEW",
            to_status="RUNNING",
        )

        assert timeline.from_status == "NEW"
        assert timeline.to_status == "RUNNING"

    def test_generate_sets_occurred_at(self, generator, timeline_session):
        """测试生成 Timeline 记录自动设置发生时间"""
        before = timezone.now_for_db()
        timeline = generator.generate(
            session=timeline_session,
            stage=TimelineStage.INGEST,
            action_type=TimelineActionType.SESSION_STARTED,
        )
        after = timezone.now_for_db()

        assert timeline.occurred_at is not None
        assert before <= timeline.occurred_at <= after

    def test_generate_with_payload(self, generator, timeline_session):
        """测试生成 Timeline 记录包含 payload 数据"""
        payload = {
            "device_code": "DEV-001",
            "command_id": 12345,
            "params": {"speed": 100, "direction": "forward"},
        }

        timeline = generator.generate(
            session=timeline_session,
            stage=TimelineStage.DISPATCH_PREPARE,
            action_type=TimelineActionType.COMMAND_SENT,
            payload=payload,
        )

        assert timeline.payload_json == payload

    def test_generate_with_different_stages(self, generator, timeline_session):
        """测试生成不同阶段的 Timeline 记录"""
        test_cases = [
            (TimelineStage.INGEST, TimelineActionType.EVENT_RECEIVED),
            (TimelineStage.ROUTE, TimelineActionType.DECISION_MADE),
            (TimelineStage.DECISION, TimelineActionType.DECISION_MADE),
            (TimelineStage.DISPATCH_PREPARE, TimelineActionType.COMMAND_SENT),
            (TimelineStage.WAITING, TimelineActionType.WAIT_STARTED),
            (TimelineStage.CALLBACK, TimelineActionType.COMMAND_ACKED),
            (TimelineStage.COMPLETE, TimelineActionType.SESSION_COMPLETED),
            (TimelineStage.FAIL, TimelineActionType.SESSION_FAILED),
        ]

        for stage, action_type in test_cases:
            timeline = generator.generate(
                session=timeline_session,
                stage=stage,
                action_type=action_type,
            )
            assert timeline.stage == stage
            assert timeline.action_type == action_type

    def test_generate_sets_default_actor_type(self, generator, timeline_session):
        """测试生成 Timeline 记录设置默认参与者类型为编排器"""
        timeline = generator.generate(
            session=timeline_session,
            stage=TimelineStage.ROUTE,
            action_type=TimelineActionType.DECISION_MADE,
        )

        assert timeline.actor_type == TimelineActorType.ORCHESTRATOR

    def test_generate_sets_default_status(self, generator, timeline_session):
        """测试生成 Timeline 记录设置默认状态为成功"""
        timeline = generator.generate(
            session=timeline_session,
            stage=TimelineStage.ROUTE,
            action_type=TimelineActionType.DECISION_MADE,
        )

        assert timeline.status == TimelineStatus.SUCCESS

    def test_generate_seq_no_defaults_to_zero_for_atomic_writer(self, generator, timeline_session):
        """测试生成的 Timeline 记录 seq_no 默认为 0（由 AtomicWriter 从序列获取并替换）"""
        timeline = generator.generate(
            session=timeline_session,
            stage=TimelineStage.INGEST,
            action_type=TimelineActionType.SESSION_CREATED,
        )

        # seq_no 由 AtomicWriter 从数据库序列获取并替换，这里默认为 0
        assert timeline.seq_no == 0

    def test_generate_with_optional_fields(self, generator, timeline_session):
        """测试生成 Timeline 记录包含可选字段"""
        timeline = generator.generate(
            session=timeline_session,
            stage=TimelineStage.DISPATCH_PREPARE,
            action_type=TimelineActionType.COMMAND_SENT,
            payload={"command": "move"},
            from_status="RUNNING",
            to_status="WAITING_DEVICE_RESULT",
        )

        assert timeline.payload_json == {"command": "move"}
        assert timeline.from_status == "RUNNING"
        assert timeline.to_status == "WAITING_DEVICE_RESULT"

    def test_generate_handles_none_trace_id(self, generator):
        """测试处理 trace_id 为 None 的情况"""
        session = MagicMock()
        session.id = 1001
        session.workline_id = 2001
        session.trace_id = None

        timeline = generator.generate(
            session=session,
            stage=TimelineStage.INGEST,
            action_type=TimelineActionType.SESSION_CREATED,
        )

        assert timeline.session_id == 1001
        assert timeline.workline_id == 2001
        assert timeline.trace_id is None

    def test_generate_with_none_payload(self, generator, timeline_session):
        """测试处理 payload 为 None 的情况"""
        timeline = generator.generate(
            session=timeline_session,
            stage=TimelineStage.INGEST,
            action_type=TimelineActionType.SESSION_STARTED,
            payload=None,
        )

        assert timeline.payload_json is None
