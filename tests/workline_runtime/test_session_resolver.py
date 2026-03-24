"""
SessionResolver 单元测试

测试 Session 归属解析器的各种场景：
- DEVICE_EVENT: 按 device_id + business_key 查找或创建
- EXTERNAL_HTTP: 按 correlation_id 恢复 Session
- TIMER_TIMEOUT: 按 session_id 恢复 Session
- MANUAL_*: 按 session_id 恢复 Session

设计参考: 设计文档 phase2-orchestrator
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.models.inbox import InboxKind
from src.app.workline.models.session import SessionStatus


class MockSessionRepository:
    """模拟 Session Repository"""

    def __init__(self) -> None:
        self.sessions: dict[int, object] = {}
        self.next_id = 1
        self.created_sessions: list[object] = []
        self.find_calls: list[tuple[str, int, str | None]] = []

    async def get_by_id(self, db: object, session_id: int) -> object | None:
        return self.sessions.get(session_id)

    async def get_open_session_by_business_key(
        self,
        db: object,
        workline_id: int,
        business_key: str,
    ) -> object | None:
        self.find_calls.append(("business_key", workline_id, business_key))
        # 模拟查找逻辑
        for session in self.sessions.values():
            s = session if isinstance(session, dict) else session.__dict__
            if (
                s.get("workline_id") == workline_id
                and s.get("business_key") == business_key
                and s.get("status") in ["NEW", "RUNNING", "WAITING_DEVICE_RESULT", "WAITING_EXTERNAL", "MANUAL_HOLD"]
            ):
                return session
        return None

    async def get_by_session_code(
        self,
        db: object,
        session_code: str,
    ) -> object | None:
        self.find_calls.append(("session_code", 0, session_code))
        for session in self.sessions.values():
            s = session if isinstance(session, dict) else session.__dict__
            if s.get("session_code") == session_code:
                return session
        return None

    async def get_by_correlation_id(
        self,
        db: object,
        correlation_id: str,
    ) -> object | None:
        self.find_calls.append(("correlation_id", 0, correlation_id))
        for session in self.sessions.values():
            s = session if isinstance(session, dict) else session.__dict__
            if s.get("correlation_id") == correlation_id:
                return session
        return None

    async def create(self, db: object, data: dict) -> object:
        session_id = self.next_id
        self.next_id += 1
        session = SimpleNamespace(id=session_id, **data)
        self.sessions[session_id] = session
        self.created_sessions.append(session)
        return session


def make_inbox(
    kind: InboxKind,
    device_id: int | None = None,
    command_id: int | None = None,
    session_id: int | None = None,
    correlation_id: str | None = None,
    payload_json: dict | None = None,
) -> MagicMock:
    """创建模拟 Inbox"""
    inbox = MagicMock()
    inbox.kind = kind
    inbox.device_id = device_id
    inbox.command_id = command_id
    inbox.session_id = session_id
    inbox.correlation_id = correlation_id
    inbox.payload_json = payload_json or {}
    return inbox


def make_workline(
    workline_id: int = 1,
    plugin_key: str = "test_plugin",
) -> MagicMock:
    """创建模拟 WorkLine"""
    workline = MagicMock()
    workline.id = workline_id
    workline.plugin_key = plugin_key
    return workline


def make_devices_by_role() -> dict[str, list]:
    """创建模拟设备映射"""
    return {
        "SCANNER": [MagicMock(id=1, device_code="SCANNER_01")],
        "CONVEYOR": [MagicMock(id=2, device_code="CONVEYOR_01")],
    }


class TestSessionResolver:
    """SessionResolver 测试套件"""

    @pytest.fixture
    def mock_db(self):
        """创建模拟数据库会话"""
        return AsyncMock()

    @pytest.fixture
    def mock_session_repo(self):
        """创建模拟 Session Repository"""
        return MockSessionRepository()

    @pytest.fixture
    def resolver(self, mock_session_repo):
        """创建 SessionResolver 实例"""
        from src.workline_runtime.session_resolver import SessionResolver

        resolver = SessionResolver()
        resolver.session_repo = mock_session_repo
        return resolver

    @pytest.mark.asyncio
    async def test_resolve_device_event_creates_new_session(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 DEVICE_EVENT 创建新 Session"""
        # Arrange
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            payload_json={"barcode": "PKG12345", "business_key": "ORDER_001"},
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
        )

        # Assert
        assert session is not None
        assert session.workline_id == 1
        assert session.plugin_key == "smt_coarse"
        assert session.business_key == "ORDER_001"
        assert session.status == SessionStatus.NEW
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_finds_existing_session(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 DEVICE_EVENT 查找已存在的 Session"""
        # Arrange - 预先创建一个 Session
        existing_session = SimpleNamespace(
            id=100,
            session_code="SESSION_100",
            workline_id=1,
            plugin_key="smt_coarse",
            business_key="ORDER_001",
            status=SessionStatus.RUNNING,
            context_json={},
        )
        mock_session_repo.sessions[100] = existing_session

        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            payload_json={"barcode": "PKG12345", "business_key": "ORDER_001"},
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
        )

        # Assert - 应该返回已存在的 Session，不创建新的
        assert session.id == 100
        assert session.business_key == "ORDER_001"
        assert len(mock_session_repo.created_sessions) == 0
        # 验证调用了 business_key 查找
        assert ("business_key", 1, "ORDER_001") in mock_session_repo.find_calls

    @pytest.mark.asyncio
    async def test_resolve_timer_timeout_finds_by_session_id(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 TIMER_TIMEOUT 按 session_id 恢复 Session"""
        # Arrange - 预先创建一个等待中的 Session
        existing_session = SimpleNamespace(
            id=200,
            session_code="SESSION_200",
            workline_id=1,
            plugin_key="smt_coarse",
            business_key="ORDER_002",
            status=SessionStatus.WAITING_DEVICE_RESULT,
            context_json={"step": "waiting_pick"},
        )
        mock_session_repo.sessions[200] = existing_session

        inbox = make_inbox(
            kind=InboxKind.TIMER_TIMEOUT,
            session_id=200,
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
        )

        # Assert
        assert session.id == 200
        assert session.status == SessionStatus.WAITING_DEVICE_RESULT
        assert len(mock_session_repo.created_sessions) == 0

    @pytest.mark.asyncio
    async def test_resolve_external_http_by_correlation_id(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 EXTERNAL_HTTP 按 correlation_id 恢复 Session"""
        # Arrange - 预先创建一个等待外部系统的 Session
        existing_session = SimpleNamespace(
            id=300,
            session_code="SESSION_300",
            workline_id=1,
            plugin_key="smt_coarse",
            business_key="ORDER_003",
            status=SessionStatus.WAITING_EXTERNAL,
            context_json={"wms_order": "WO123"},
            correlation_id="CORR_12345",
        )
        mock_session_repo.sessions[300] = existing_session

        inbox = make_inbox(
            kind=InboxKind.EXTERNAL_HTTP,
            correlation_id="CORR_12345",
            payload_json={"wms_response": "success"},
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
        )

        # Assert
        assert session.id == 300
        assert session.status == SessionStatus.WAITING_EXTERNAL
        assert session.correlation_id == "CORR_12345"
        assert len(mock_session_repo.created_sessions) == 0

    @pytest.mark.asyncio
    async def test_resolve_manual_hold_by_session_id(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 MANUAL_HOLD 按 session_id 恢复 Session"""
        # Arrange
        existing_session = SimpleNamespace(
            id=400,
            session_code="SESSION_400",
            workline_id=1,
            plugin_key="smt_coarse",
            business_key="ORDER_004",
            status=SessionStatus.MANUAL_HOLD,
            context_json={"manual_reason": "quality_check"},
        )
        mock_session_repo.sessions[400] = existing_session

        inbox = make_inbox(
            kind=InboxKind.MANUAL_RESUME,
            session_id=400,
            payload_json={"operator": "user001"},
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
        )

        # Assert
        assert session.id == 400
        assert session.status == SessionStatus.MANUAL_HOLD
        assert len(mock_session_repo.created_sessions) == 0

    @pytest.mark.asyncio
    async def test_resolve_timer_timeout_raises_when_session_not_found(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 TIMER_TIMEOUT 在 Session 不存在时抛出异常"""
        inbox = make_inbox(
            kind=InboxKind.TIMER_TIMEOUT,
            session_id=999,  # 不存在的 Session
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")
        devices_by_role = make_devices_by_role()

        # Act & Assert
        with pytest.raises(ValueError, match="Session not found: 999"):
            await resolver.resolve_or_create(
                db=mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=devices_by_role,
            )

    @pytest.mark.asyncio
    async def test_resolve_external_http_raises_when_correlation_id_missing(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 EXTERNAL_HTTP 在 correlation_id 缺失时抛出异常"""
        inbox = make_inbox(
            kind=InboxKind.EXTERNAL_HTTP,
            correlation_id=None,  # 缺失 correlation_id
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")
        devices_by_role = make_devices_by_role()

        # Act & Assert
        with pytest.raises(ValueError, match="correlation_id is required for EXTERNAL_HTTP"):
            await resolver.resolve_or_create(
                db=mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=devices_by_role,
            )

    @pytest.mark.asyncio
    async def test_resolve_device_event_without_business_key_creates_session(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 DEVICE_EVENT 无 business_key 时创建新 Session"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            payload_json={"barcode": "PKG12345"},  # 无 business_key
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=devices_by_role,
        )

        # Assert - 应该创建新 Session
        assert session is not None
        assert session.workline_id == 1
        # business_key 应该生成一个唯一的值
        assert session.business_key is not None
        assert len(mock_session_repo.created_sessions) == 1
