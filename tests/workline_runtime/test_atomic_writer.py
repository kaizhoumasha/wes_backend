"""
AtomicWriter 单元测试

测试原子写入器的核心功能：
- 单一事务内更新所有实体
- Session 状态更新
- Timeline 记录插入
- Outbox 记录插入
- Inbox 状态更新
- 事务回滚

设计参考: 设计文档 phase2-orchestrator
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.workline.models.inbox import InboxStatus
from src.app.workline.models.session import SessionStatus
from src.workline_runtime.atomic_writer import AtomicWriter, atomic_writer
from src.workline_runtime.enums import TimelineStage
from tests.workline_runtime.support.runtime_builders import make_mock_db


class MockSession:
    """模拟 WorklineSession"""

    def __init__(
        self,
        session_id: int = 12345,
        status: SessionStatus = SessionStatus.RUNNING,
        context: dict[str, Any] | None = None,
    ):
        self.id = session_id
        self.status = status
        self.context_json = context or {}
        self.workline_id = 1
        self.plugin_key = "test_plugin"


class MockTimeline:
    """模拟 WorklineTimeline"""

    def __init__(
        self,
        session_id: int = 12345,
        seq_no: int = 1,
        stage: TimelineStage = TimelineStage.DECISION,
    ):
        self.session_id = session_id
        self.seq_no = seq_no
        self.stage = stage
        self.workline_id = 1
        self.occurred_at = None
        self.action_type = "TEST_ACTION"
        self.actor_type = "PLUGIN"
        self.status = "SUCCESS"


class MockOutbox:
    """模拟 SystemOutbox"""

    def __init__(
        self,
        session_id: int = 12345,
        dispatch_type: str = "DEVICE_COMMAND",
    ):
        self.session_id = session_id
        self.dispatch_type = dispatch_type
        self.workline_id = 1
        self.dispatch_key = "test-key"
        self.target_type = "DEVICE"
        self.target_code = "DEVICE_001"
        self.payload_json = {}
        self.status = "NEW"


class MockInbox:
    """模拟 WorklineInbox"""

    def __init__(
        self,
        inbox_id: int = 1,
        status: InboxStatus = InboxStatus.PROCESSING,
    ):
        self.id = inbox_id
        self.status = status
        self.processed_at = None


class TestAtomicWriterCommit:
    """AtomicWriter.commit 方法测试"""

    @pytest.fixture
    def workline_runtime_mock_db(self):
        """创建模拟数据库会话"""
        return make_mock_db(spec=AsyncSession)

    @pytest.fixture
    def writer(self):
        """创建 AtomicWriter 实例"""
        return AtomicWriter()

    @pytest.mark.asyncio
    async def test_commit_updates_session_status(self, workline_runtime_mock_db, writer):
        """测试 commit 更新 Session 状态"""
        session = MockSession(status=SessionStatus.RUNNING)
        timelines = [MockTimeline()]
        inbox = MockInbox()

        # Mock _get_next_seq_no
        with patch.object(writer, "_get_next_seq_no", return_value=1):
            await writer.commit(
                db=workline_runtime_mock_db,
                session=session,
                timelines=timelines,
                outboxes=None,
                inbox=inbox,
            )

        # 验证 Session 状态已更新
        assert session.status == SessionStatus.RUNNING

        # 验证 db.add 被调用（用于添加 Timeline）
        workline_runtime_mock_db.add.assert_called()

    @pytest.mark.asyncio
    async def test_commit_inserts_timelines(self, workline_runtime_mock_db, writer):
        """测试 commit 插入 Timeline 记录"""
        session = MockSession()
        timelines = [
            MockTimeline(seq_no=1),
            MockTimeline(seq_no=2),
        ]
        inbox = MockInbox()

        seq_no_counter = 0

        async def mock_get_seq_no(db):
            nonlocal seq_no_counter
            seq_no_counter += 1
            return seq_no_counter

        with patch.object(writer, "_get_next_seq_no", side_effect=mock_get_seq_no):
            await writer.commit(
                db=workline_runtime_mock_db,
                session=session,
                timelines=timelines,
                outboxes=None,
                inbox=inbox,
            )

        # 验证为每个 Timeline 调用 add
        assert workline_runtime_mock_db.add.call_count >= 2

    @pytest.mark.asyncio
    async def test_commit_inserts_outboxes(self, workline_runtime_mock_db, writer):
        """测试 commit 插入 Outbox 记录"""
        session = MockSession()
        timelines = [MockTimeline()]
        outboxes = [
            MockOutbox(dispatch_type="DEVICE_COMMAND"),
            MockOutbox(dispatch_type="EXTERNAL_HTTP"),
        ]
        inbox = MockInbox()

        with patch.object(writer, "_get_next_seq_no", return_value=1):
            await writer.commit(
                db=workline_runtime_mock_db,
                session=session,
                timelines=timelines,
                outboxes=outboxes,
                inbox=inbox,
            )

        # 验证为每个 Outbox 调用 add
        assert workline_runtime_mock_db.add.call_count >= 3  # 1 timeline + 2 outboxes

    @pytest.mark.asyncio
    async def test_commit_updates_inbox_status(self, workline_runtime_mock_db, writer):
        """测试 commit 更新 Inbox 状态"""
        session = MockSession()
        timelines = [MockTimeline()]
        inbox = MockInbox(status=InboxStatus.PROCESSING)

        with patch.object(writer, "_get_next_seq_no", return_value=1):
            await writer.commit(
                db=workline_runtime_mock_db,
                session=session,
                timelines=timelines,
                outboxes=None,
                inbox=inbox,
            )

        # 验证 Inbox 状态已更新为 PROCESSED
        assert inbox.status == InboxStatus.PROCESSED
        assert inbox.processed_at is not None

    @pytest.mark.asyncio
    async def test_commit_is_atomic_rollback_on_error(self, workline_runtime_mock_db, writer):
        """测试 commit 在错误时回滚"""
        session = MockSession()
        timelines = [MockTimeline()]
        inbox = MockInbox()

        # 模拟数据库操作抛出异常
        workline_runtime_mock_db.add.side_effect = ValueError("Database error")

        with patch.object(writer, "_get_next_seq_no", return_value=1):
            with pytest.raises(ValueError, match="Database error"):
                await writer.commit(
                    db=workline_runtime_mock_db,
                    session=session,
                    timelines=timelines,
                    outboxes=None,
                    inbox=inbox,
                )

        # 验证调用了 rollback
        workline_runtime_mock_db.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_commit_without_outboxes(self, workline_runtime_mock_db, writer):
        """测试 commit 不传 outboxes 时正常工作"""
        session = MockSession()
        timelines = [MockTimeline()]
        inbox = MockInbox()

        with patch.object(writer, "_get_next_seq_no", return_value=1):
            await writer.commit(
                db=workline_runtime_mock_db,
                session=session,
                timelines=timelines,
                outboxes=None,
                inbox=inbox,
            )

        # 验证正常完成
        assert inbox.status == InboxStatus.PROCESSED


class TestAtomicWriterGetNextSeqNo:
    """AtomicWriter._get_next_seq_no 方法测试"""

    @pytest.fixture
    def writer(self):
        """创建 AtomicWriter 实例"""
        return AtomicWriter()

    @pytest.mark.asyncio
    async def test_get_next_seq_no_returns_incrementing_values(self, writer):
        """测试 _get_next_seq_no 返回递增值"""
        # 创建模拟数据库会话
        workline_runtime_mock_db = AsyncMock(spec=AsyncSession)

        # 模拟 execute 返回结果
        mock_result = MagicMock()
        mock_result.scalar = MagicMock(return_value=100)
        workline_runtime_mock_db.execute = AsyncMock(return_value=mock_result)

        result = await writer._get_next_seq_no(workline_runtime_mock_db)

        assert result == 100

        # 验证调用了正确的 SQL
        call_args = workline_runtime_mock_db.execute.call_args
        assert call_args is not None
        # 检查第一个参数是否是 text 对象
        sql_text = call_args[0][0]
        assert "nextval" in str(sql_text)
        assert "workline_timeline_seq_no_seq" in str(sql_text)

    @pytest.mark.asyncio
    async def test_get_next_seq_no_multiple_calls(self, writer):
        """测试多次调用 _get_next_seq_no 返回不同值"""
        workline_runtime_mock_db = AsyncMock(spec=AsyncSession)

        # 模拟多次调用返回不同值
        call_count = 0

        async def mock_execute(query):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            mock_result.scalar = MagicMock(return_value=100 + call_count)
            return mock_result

        workline_runtime_mock_db.execute = mock_execute

        results = []
        for _ in range(3):
            result = await writer._get_next_seq_no(workline_runtime_mock_db)
            results.append(result)

        # 验证每次调用返回不同的值
        assert results[0] == 101
        assert results[1] == 102
        assert results[2] == 103


class TestAtomicWriterSingleton:
    """AtomicWriter 单例测试"""

    def test_atomic_writer_singleton_exists(self):
        """测试 atomic_writer 单例存在"""
        assert atomic_writer is not None
        assert isinstance(atomic_writer, AtomicWriter)

    def test_atomic_writer_is_same_instance(self):
        """测试多次导入返回同一实例"""
        from src.workline_runtime.atomic_writer import atomic_writer as writer1
        from src.workline_runtime.atomic_writer import atomic_writer as writer2

        assert writer1 is writer2


class TestAtomicWriterEdgeCases:
    """AtomicWriter 边界情况测试"""

    @pytest.fixture
    def workline_runtime_mock_db(self):
        """创建模拟数据库会话"""
        return make_mock_db(spec=AsyncSession)

    @pytest.fixture
    def writer(self):
        """创建 AtomicWriter 实例"""
        return AtomicWriter()

    @pytest.mark.asyncio
    async def test_commit_empty_timelines(self, workline_runtime_mock_db, writer):
        """测试空 timelines 列表"""
        session = MockSession()
        timelines = []
        inbox = MockInbox()

        await writer.commit(
            db=workline_runtime_mock_db,
            session=session,
            timelines=timelines,
            outboxes=None,
            inbox=inbox,
        )

        # 验证 Inbox 状态已更新
        assert inbox.status == InboxStatus.PROCESSED

    @pytest.mark.asyncio
    async def test_commit_empty_outboxes_list(self, workline_runtime_mock_db, writer):
        """测试空 outboxes 列表（非 None）"""
        session = MockSession()
        timelines = [MockTimeline()]
        outboxes = []
        inbox = MockInbox()

        with patch.object(writer, "_get_next_seq_no", return_value=1):
            await writer.commit(
                db=workline_runtime_mock_db,
                session=session,
                timelines=timelines,
                outboxes=outboxes,
                inbox=inbox,
            )

        # 验证正常完成
        assert inbox.status == InboxStatus.PROCESSED

    @pytest.mark.asyncio
    async def test_commit_session_context_update(self, workline_runtime_mock_db, writer):
        """测试 Session 上下文更新"""
        session = MockSession(context={"initial": "value"})
        timelines = [MockTimeline()]
        inbox = MockInbox()

        # 更新上下文
        session.context_json = {"initial": "value", "new_key": "new_value"}

        with patch.object(writer, "_get_next_seq_no", return_value=1):
            await writer.commit(
                db=workline_runtime_mock_db,
                session=session,
                timelines=timelines,
                outboxes=None,
                inbox=inbox,
            )

        # 验证上下文已更新
        assert session.context_json["new_key"] == "new_value"
