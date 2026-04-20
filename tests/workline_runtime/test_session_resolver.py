"""
SessionResolver 单元测试

测试 Session 归属解析器的各种场景：
- DEVICE_EVENT: 按 device_id + business_key 查找或创建
- EXTERNAL_HTTP: 按 correlation_id 恢复 Session
- TIMER_TIMEOUT: 按 session_id 恢复 Session
- MANUAL_*: 按 session_id 恢复 Session

设计参考: 设计文档 phase2-orchestrator
"""

from datetime import datetime
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

    async def get_latest_session_by_business_key(
        self,
        db: object,
        workline_id: int,
        business_key: str,
    ) -> object | None:
        """查找最新的会话（无论状态）"""
        matching = []
        for session in self.sessions.values():
            s = session if isinstance(session, dict) else session.__dict__
            if s.get("workline_id") == workline_id and s.get("business_key") == business_key:
                matching.append(session)
        if matching:
            # 按 id 降序，返回最新的
            matching.sort(key=lambda x: x.id if hasattr(x, "id") else 0, reverse=True)
            return matching[0]
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

    async def get_open_session_by_awaiting_command_id(self, db: object, command_id: int) -> object | None:
        self.find_calls.append(("awaiting_command_id", command_id, None))
        for session in self.sessions.values():
            s = session if isinstance(session, dict) else session.__dict__
            if s.get("awaiting_command_id") == command_id:
                return session
        return None


class MockCommandRepository:
    def __init__(self) -> None:
        self.commands: dict[str, object] = {}

    async def get_by_command_code(self, db: object, command_code: str) -> object | None:
        return self.commands.get(command_code)


def make_inbox(
    kind: InboxKind,
    device_id: int | None = None,
    command_id: int | None = None,
    session_id: int | None = None,
    correlation_id: str | None = None,
    source_message_id: str | None = None,
    payload_json: dict | None = None,
) -> MagicMock:
    """创建模拟 Inbox"""
    inbox = MagicMock()
    inbox.kind = kind
    inbox.device_id = device_id
    inbox.command_id = command_id
    inbox.session_id = session_id
    inbox.correlation_id = correlation_id
    inbox.source_message_id = source_message_id
    inbox.payload_json = payload_json or {}
    return inbox


def make_workline(
    workline_id: int = 1,
    plugin_key: str | None = "test_plugin",
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
        resolver.command_repo = MockCommandRepository()
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
            source_message_id="req-001",
            payload_json={"barcode": "PKG12345", "business_key": "ORDER_001"},
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")
        workline.contract_version = "wl-2026.04"
        devices_by_role = make_devices_by_role()

        # Act
        with patch(
            "src.workline_runtime.session_resolver.get_plugin_contract_version",
            return_value="registry-legacy",
        ):
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
        assert session.ingress_count == 1
        assert session.last_request_id == "req-001"
        assert session.last_ingress_at is not None
        assert isinstance(session.correlation_id, str)
        assert session.correlation_id.startswith("corr_")
        assert session.contract_version == "wl-2026.04"
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_falls_back_to_registry_contract_version_when_workline_missing(
        self, mock_db, mock_session_repo, resolver
    ):
        """workline.contract_version 缺失时才回退 registry。"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            source_message_id="req-002",
            payload_json={"barcode": "PKG12346", "business_key": "ORDER_003"},
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")
        workline.contract_version = None

        with patch(
            "src.workline_runtime.session_resolver.get_plugin_contract_version",
            return_value="registry-2026.04",
        ):
            session = await resolver.resolve_or_create(
                db=mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=make_devices_by_role(),
            )

        assert session.contract_version == "registry-2026.04"
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_does_not_use_line_code_as_plugin_key_fallback(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """plugin_key 缺失时不应回退为业务 line_code。"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            payload_json={"barcode": "PKG12345", "business_key": "ORDER_002"},
        )
        workline = make_workline(workline_id=1, plugin_key=None)
        workline.line_code = "WL-001"

        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        assert session.plugin_key is None
        assert session.workline_id == 1
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
            ingress_count=1,
            last_request_id="req-old",
            last_ingress_at=None,
            correlation_id="corr-main-001",
            context_json={},
        )
        mock_session_repo.sessions[100] = existing_session

        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            correlation_id="corr_temp_001",
            source_message_id="req-new",
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
        assert session.ingress_count == 2
        assert session.last_request_id == "req-new"
        assert isinstance(session.last_ingress_at, datetime)
        pending_ingress = getattr(session, "_pending_session_ingress_metadata", None)
        assert pending_ingress is not None
        assert pending_ingress["ingress_count"] == 2
        assert pending_ingress["last_request_id"] == "req-new"
        assert pending_ingress["last_ingress_at"] == session.last_ingress_at
        assert inbox.session_id == 100
        assert inbox.correlation_id == "corr-main-001"
        assert session.correlation_id == "corr-main-001"
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
        assert inbox.session_id == 300
        assert inbox.workline_id == 1
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

    @pytest.mark.asyncio
    async def test_resolve_device_event_uses_new_six_in_one_fields_as_business_key(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 DEVICE_EVENT 使用新 Six-In-One 字段生成稳定 business_key。"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            payload_json={
                "data": {
                    "HHPN": "620100L00-011-G",
                    "MfrPN": "CC0402JRNPO9BN220",
                    "Qty": "7387",
                    "LotCode": "LOTABC123",
                    "DateCode": "20260409",
                    "PkgID": "SVYU00125TP4LCR02_2",
                }
            },
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")

        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        # Six-In-One 组合生成 16 位 hash
        import hashlib
        import json

        fields = [
            "620100L00-011-G",
            "CC0402JRNPO9BN220",
            "7387",
            "20260409",
            "LOTABC123",
            "SVYU00125TP4LCR02_2",
        ]
        json_str = json.dumps(fields, ensure_ascii=False)
        expected_hash = hashlib.sha256(json_str.encode("utf-8")).hexdigest()[:16]

        assert session.business_key == expected_hash
        assert ("business_key", 1, expected_hash) in mock_session_repo.find_calls

    @pytest.mark.asyncio
    async def test_resolve_device_event_new_six_in_one_key_reuses_existing_session(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试相同的新 Six-In-One 数据会命中同一 business_key。"""
        import hashlib
        import json

        fields = [
            "620100L00-011-G",
            "CC0402JRNPO9BN220",
            "7387",
            "20260409",
            "LOTABC123",
            "SVYU00125TP4LCR02_2",
        ]
        json_str = json.dumps(fields, ensure_ascii=False)
        expected_hash = hashlib.sha256(json_str.encode("utf-8")).hexdigest()[:16]
        _ = await mock_session_repo.create(
            mock_db,
            {
                "session_code": "SES_EXISTING",
                "workline_id": 1,
                "plugin_key": "smt_coarse",
                "business_key": expected_hash,
                "status": SessionStatus.NEW,
                "ingress_count": 1,
                "last_request_id": "req-existing",
                "last_ingress_at": None,
                "correlation_id": "corr_existing",
                "context_json": {},
                "started_at": None,
            },
        )

        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            source_message_id="req-latest",
            payload_json={
                "data": {
                    "HHPN": "620100L00-011-G",
                    "MfrPN": "CC0402JRNPO9BN220",
                    "Qty": "7387",
                    "LotCode": "LOTABC123",
                    "DateCode": "20260409",
                    "PkgID": "SVYU00125TP4LCR02_2",
                }
            },
        )
        workline = make_workline(workline_id=1, plugin_key="smt_coarse")

        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        assert session.business_key == expected_hash
        assert session.ingress_count == 2
        assert session.last_request_id == "req-latest"
        assert isinstance(session.last_ingress_at, datetime)
        pending_ingress = getattr(session, "_pending_session_ingress_metadata", None)
        assert pending_ingress is not None
        assert pending_ingress["ingress_count"] == 2
        assert pending_ingress["last_request_id"] == "req-latest"
        assert pending_ingress["last_ingress_at"] == session.last_ingress_at
        assert len(mock_session_repo.created_sessions) == 1
        assert ("business_key", 1, expected_hash) in mock_session_repo.find_calls

    @pytest.mark.asyncio
    async def test_resolve_command_result_finds_session_by_awaiting_command_id(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        command = SimpleNamespace(
            id=301,
            command_code="CMD-001",
            device_id=11,
            workline_id=1,
            correlation_id="corr-301",
        )
        resolver.command_repo.commands["CMD-001"] = command

        existing_session = SimpleNamespace(
            id=401,
            awaiting_command_id=301,
            correlation_id="corr-301",
            status=SessionStatus.WAITING_DEVICE_RESULT,
        )
        mock_session_repo.sessions[401] = existing_session

        inbox = make_inbox(
            kind=InboxKind.COMMAND_RESULT,
            payload_json={"command_code": "CMD-001"},
        )

        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=make_workline(),
            devices_by_role=make_devices_by_role(),
        )

        assert session.id == 401
        assert inbox.command_id == 301
        assert inbox.device_id == 11
        assert inbox.workline_id == 1
        assert inbox.session_id == 401
        assert inbox.correlation_id == "corr-301"
