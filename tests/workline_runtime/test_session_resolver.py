"""
SessionResolver 单元测试

测试 Session 归属解析器的各种场景：
- DEVICE_EVENT: 按 device_id + business_key 查找或创建
- EXTERNAL_HTTP: 按 trace_id 恢复 Session
- TIMER_TIMEOUT: 按 session_id 恢复 Session
- MANUAL_*: 按 session_id 恢复 Session

设计参考: 设计文档 phase2-orchestrator
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.models.inbox import InboxKind
from src.app.workline.models.session import RunMode, SessionStatus
from src.utils.timezone import timezone
from src.workline_runtime.session_resolver import SessionResolveError, _resolve_business_key


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

    async def get_by_trace_id(
        self,
        db: object,
        trace_id: str,
    ) -> object | None:
        self.find_calls.append(("trace_id", 0, trace_id))
        for session in self.sessions.values():
            s = session if isinstance(session, dict) else session.__dict__
            if s.get("trace_id") == trace_id:
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


class MockOutboxRepository:
    def __init__(self) -> None:
        self.outboxes: dict[str, object] = {}
        self.find_calls: list[tuple[str, str]] = []

    async def get_by_dispatch_key(self, db: object, dispatch_key: str) -> object | None:
        self.find_calls.append(("dispatch_key", dispatch_key))
        return self.outboxes.get(dispatch_key)


def make_inbox(
    kind: InboxKind,
    device_id: int | None = None,
    command_id: int | None = None,
    session_id: int | None = None,
    trace_id: str | None = None,
    source_message_id: str | None = None,
    payload_json: dict | None = None,
) -> MagicMock:
    """创建模拟 Inbox"""
    inbox = MagicMock()
    inbox.kind = kind
    inbox.device_id = device_id
    inbox.command_id = command_id
    inbox.session_id = session_id
    inbox.trace_id = trace_id
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
    workline.run_mode = "AUTO"
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
        resolver.outbox_repo = MockOutboxRepository()
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
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")
        workline.contract_version = "wl-2026.04"
        workline.run_mode = "SIMULATION"
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
        assert session.plugin_key == "smt_classifier"
        assert session.business_key == "ORDER_001"
        assert session.run_mode == RunMode.SIMULATION
        assert session.status == SessionStatus.NEW
        assert session.ingress_count == 1
        assert session.last_request_id == "req-001"
        assert session.last_ingress_at is not None
        assert isinstance(session.trace_id, str)
        assert session.trace_id.startswith("trace_")
        assert inbox.trace_id == session.trace_id
        assert session.contract_version == "wl-2026.04"
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_locks_business_key_before_lookup(
        self,
        mock_db,
        resolver,
    ):
        """PostgreSQL 下同一 workline/business_key 必须先串行化再查建会话。"""
        mock_db.get_bind = lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            source_message_id="req-lock",
            payload_json={"barcode": "PKG12345", "business_key": "ORDER_LOCK"},
        )
        workline = make_workline(workline_id=7, plugin_key="smt_classifier")

        _ = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        mock_db.execute.assert_awaited()
        lock_statement, lock_params = mock_db.execute.await_args_list[0].args
        assert "pg_advisory_xact_lock(hashtext(:lock_key))" in str(lock_statement)
        assert lock_params == {"lock_key": "workline-session:7:ORDER_LOCK"}

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
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")
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
            plugin_key="smt_classifier",
            business_key="ORDER_001",
            status=SessionStatus.RUNNING,
            ingress_count=1,
            last_request_id="req-old",
            last_ingress_at=None,
            trace_id="trace-main-001",
            context_json={},
        )
        mock_session_repo.sessions[100] = existing_session

        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            trace_id="trace_temp_001",
            source_message_id="req-new",
            payload_json={"barcode": "PKG12345", "business_key": "ORDER_001"},
        )
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")
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
        assert "trace_id" not in pending_ingress
        assert inbox.session_id == 100
        assert inbox.trace_id == "trace-main-001"
        assert inbox.trace_id == "trace-main-001"
        assert session.trace_id == "trace-main-001"
        assert session.trace_id == "trace-main-001"
        assert len(mock_session_repo.created_sessions) == 0
        # 验证调用了 business_key 查找
        assert ("business_key", 1, "ORDER_001") in mock_session_repo.find_calls

    @pytest.mark.asyncio
    async def test_resolve_device_event_same_trace_different_workline_creates_independent_session(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """同一 trace 下的第二插件入口事件不能复用其他作业线的 open session。"""
        smt_session = SimpleNamespace(
            id=100,
            session_code="SESSION_SMT_100",
            workline_id=45,
            plugin_key="smt_classifier",
            business_key="SMT_REEL_001",
            status=SessionStatus.WAITING_EXTERNAL,
            ingress_count=1,
            last_request_id="req-smt",
            last_ingress_at=None,
            trace_id="trace-shared-001",
            context_json={},
        )
        mock_session_repo.sessions[100] = smt_session
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=44,
            trace_id="trace-shared-001",
            source_message_id="release-001",
            payload_json={
                "event_type": "SINGLE_LAYER_RACK_RELEASED",
                "data": {
                    "rack_release_id": "release-001",
                    "single_layer_rack_id": "RACK-001",
                },
            },
        )

        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=make_workline(workline_id=50, plugin_key="smt_full_box_exchange"),
            devices_by_role=make_devices_by_role(),
        )

        assert session.id != smt_session.id
        assert session.workline_id == 50
        assert session.plugin_key == "smt_full_box_exchange"
        assert session.business_key == "release-001"
        assert session.trace_id == "trace-shared-001"
        assert inbox.session_id is None
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_reuses_terminal_session_for_same_business_key_without_time_window(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """同一物料终态后重复入口不能靠超过 5 秒绕过归档防线。"""
        existing_session = SimpleNamespace(
            id=101,
            session_code="SESSION_101",
            workline_id=1,
            plugin_key="smt_classifier",
            business_key="ORDER_001",
            status=SessionStatus.COMPLETED,
            ended_at=timezone.now_for_db() - timedelta(minutes=3),
            ingress_count=1,
            last_request_id="req-old",
            last_ingress_at=None,
            trace_id="trace-main-101",
            context_json={},
        )
        mock_session_repo.sessions[101] = existing_session
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            source_message_id="req-late-duplicate",
            payload_json={"business_key": "ORDER_001"},
        )

        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=make_workline(workline_id=1, plugin_key="smt_classifier"),
            devices_by_role=make_devices_by_role(),
        )

        assert session.id == 101
        assert session.ingress_count == 2
        assert inbox.session_id == 101
        assert len(mock_session_repo.created_sessions) == 0

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
            plugin_key="smt_classifier",
            business_key="ORDER_002",
            status=SessionStatus.WAITING_DEVICE_RESULT,
            context_json={"step": "waiting_pick"},
        )
        mock_session_repo.sessions[200] = existing_session

        inbox = make_inbox(
            kind=InboxKind.TIMER_TIMEOUT,
            session_id=200,
        )
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")
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
    async def test_resolve_external_http_by_trace_id(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 EXTERNAL_HTTP 按 trace_id 恢复 Session"""
        # Arrange - 预先创建一个等待外部系统的 Session
        existing_session = SimpleNamespace(
            id=300,
            session_code="SESSION_300",
            workline_id=1,
            plugin_key="smt_classifier",
            business_key="ORDER_003",
            status=SessionStatus.WAITING_EXTERNAL,
            context_json={"wms_order": "WO123"},
            trace_id="trace_12345",
        )
        mock_session_repo.sessions[300] = existing_session

        inbox = make_inbox(
            kind=InboxKind.EXTERNAL_HTTP,
            trace_id="trace_12345",
            payload_json={"wms_response": "success"},
        )
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")
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
        assert session.trace_id == "trace_12345"
        assert inbox.session_id == 300
        assert inbox.workline_id == 1
        assert len(mock_session_repo.created_sessions) == 0

    @pytest.mark.asyncio
    async def test_resolve_external_http_prefers_dispatch_key_when_trace_has_multiple_sessions(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """同一 trace 存在多个外部等待时，应按 dispatch_key 归属到对应 outbox/session。"""
        smt_session = SimpleNamespace(
            id=300,
            session_code="SESSION_SMT_300",
            workline_id=45,
            plugin_key="smt_classifier",
            business_key="SMT_REEL_001",
            status=SessionStatus.WAITING_EXTERNAL,
            context_json={},
            trace_id="trace-shared-001",
        )
        fullbox_session = SimpleNamespace(
            id=301,
            session_code="SESSION_FULLBOX_301",
            workline_id=50,
            plugin_key="smt_full_box_exchange",
            business_key="release-001",
            status=SessionStatus.WAITING_EXTERNAL,
            context_json={},
            trace_id="trace-shared-001",
        )
        mock_session_repo.sessions[300] = smt_session
        mock_session_repo.sessions[301] = fullbox_session
        dispatch_key = "external:smt_full_box_exchange:release-001:FULL_BIN_EXCHANGE"
        resolver.outbox_repo.outboxes[dispatch_key] = SimpleNamespace(
            id=77,
            session_id=301,
            workline_id=50,
            dispatch_key=dispatch_key,
        )
        inbox = make_inbox(
            kind=InboxKind.EXTERNAL_HTTP,
            trace_id="trace-shared-001",
            payload_json={
                "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
                "dispatch_key": dispatch_key,
            },
        )

        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=None,
            devices_by_role=make_devices_by_role(),
        )

        assert session.id == 301
        assert inbox.session_id == 301
        assert inbox.workline_id == 50
        assert resolver.outbox_repo.find_calls == [("dispatch_key", dispatch_key)]

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
            plugin_key="smt_classifier",
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
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")
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
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")
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
    async def test_resolve_external_http_raises_when_trace_id_missing(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 EXTERNAL_HTTP 在 trace_id 缺失时抛出异常"""
        inbox = make_inbox(
            kind=InboxKind.EXTERNAL_HTTP,
            trace_id=None,  # 缺失 trace_id
        )
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")
        devices_by_role = make_devices_by_role()

        # Act & Assert
        with pytest.raises(ValueError, match="trace_id is required for EXTERNAL_HTTP"):
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
            payload_json={"data": {"barcode": "PKG12345"}},  # 无 business_key，barcode 在 data 内
        )
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")
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

    def test_resolve_business_key_prefers_data_barcode(self):
        """data.barcode 是稳定业务标识时，应直接作为 business_key。"""
        payload = {"data": {"barcode": "PKG12345"}}

        key = _resolve_business_key(payload, plugin_key=None)

        assert key == "PKG12345"

    def test_resolve_business_key_uses_plugin_manifest_resolver_for_smt_six_in_one(self):
        """SMT Six-In-One 业务键由插件 manifest resolver 解析。"""
        payload = {
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "LotCode": "LOTABC123",
                "DateCode": "20260409",
                "PkgID": "SVYU00125TP4LCR02_2",
            }
        }

        key1 = _resolve_business_key(payload, plugin_key="smt_classifier")
        key2 = _resolve_business_key(payload, plugin_key="smt_classifier")

        import hashlib
        import json

        expected_hash = hashlib.sha256(
            json.dumps("SVYU00125TP4LCR02_2", ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

        assert key1 == expected_hash
        assert key2 == expected_hash

    def test_resolve_business_key_prefers_plugin_manifest_key_over_upstream_business_key(self):
        """插件解析器命中业务键时，不应再信任外部透传 business_key。"""
        payload = {
            "business_key": "UPSTREAM-MISMATCH",
            "data": {
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "LotCode": "LOTABC123",
                "DateCode": "20260409",
                "PkgID": "SVYU00125TP4LCR02_2",
            },
        }

        key = _resolve_business_key(payload, plugin_key="smt_classifier")

        import hashlib
        import json

        expected_hash = hashlib.sha256(
            json.dumps("SVYU00125TP4LCR02_2", ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

        assert key == expected_hash

    def test_resolve_business_key_rejects_estop_as_normal_session_event(self):
        """急停是平台保留安全事件，不应进入普通 Session 归属。"""
        payload = {
            "device_code": "ARM01",
            "event_type": "ESTOP_PRESSED",
            "data": None,
        }

        with pytest.raises(
            SessionResolveError,
            match="Unable to resolve stable business_key from payload",
        ):
            _resolve_business_key(payload, plugin_key="smt_classifier")

    def test_resolve_business_key_uses_event_id_for_material_arrived(self):
        """无业务条码的传感器事件应优先使用 event_id 形成稳定归属键。"""
        payload = {
            "device_code": "PIPELINE01",
            "event_type": "MATERIAL_ARRIVED",
            "data": {
                "event_id": "VENDOR-EVT-123",
                "location": "STATION_INPUT1",
            },
        }

        key = _resolve_business_key(payload, plugin_key=None)

        assert key == "event:MATERIAL_ARRIVED:PIPELINE01:VENDOR-EVT-123"

    def test_resolve_business_key_rejects_material_arrived_without_event_identity(self):
        """MATERIAL_ARRIVED 缺少事件实例标识时，不应退化成 location 级业务键。"""
        payload = {
            "device_code": "PIPELINE01",
            "event_type": "MATERIAL_ARRIVED",
            "data": {
                "location": "STATION_INPUT1",
            },
        }

        with pytest.raises(
            SessionResolveError,
            match="Unable to resolve stable business_key from payload",
        ):
            _resolve_business_key(payload, plugin_key="smt_classifier")

    def test_resolve_business_key_raises_when_stable_identity_missing(self):
        """缺少稳定业务标识时，不应伪造随机 business_key。"""
        payload = {"data": {"location": "STATION_INPUT1"}}

        with pytest.raises(
            SessionResolveError,
            match="Unable to resolve stable business_key from payload",
        ):
            _resolve_business_key(payload, plugin_key=None)

    @pytest.mark.asyncio
    async def test_resolve_device_event_raises_when_stable_business_key_missing(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """DEVICE_EVENT 无法得到稳定业务标识时，应显式失败而不是随机建单。"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            payload_json={"data": {"location": "STATION_INPUT1"}},
        )
        workline = make_workline(workline_id=1, plugin_key=None)

        with pytest.raises(
            SessionResolveError,
            match="Unable to resolve stable business_key from payload",
        ):
            await resolver.resolve_or_create(
                db=mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=make_devices_by_role(),
            )

        assert len(mock_session_repo.created_sessions) == 0

    @pytest.mark.asyncio
    async def test_resolve_device_event_rejects_estop_as_normal_session_event(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """急停事件应在 SessionResolver 之前由安全事件入口短路处理。"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            payload_json={
                "device_code": "ARM01",
                "event_type": "ESTOP_PRESSED",
                "data": None,
            },
        )
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")

        with pytest.raises(
            SessionResolveError,
            match="Unable to resolve stable business_key from payload",
        ):
            await resolver.resolve_or_create(
                db=mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=make_devices_by_role(),
            )

        assert len(mock_session_repo.created_sessions) == 0

    @pytest.mark.asyncio
    async def test_resolve_device_event_uses_event_scope_key_for_material_arrived(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """MATERIAL_ARRIVED 这类无业务条码事件必须携带事件实例标识。"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=2,
            payload_json={
                "device_code": "PIPELINE01",
                "event_type": "MATERIAL_ARRIVED",
                "data": {
                    "event_id": "PIPELINE-EVT-001",
                    "location": "STATION_INPUT1",
                },
            },
        )
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")

        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        assert session.business_key == "event:MATERIAL_ARRIVED:PIPELINE01:PIPELINE-EVT-001"
        assert session.workline_id == 1
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_rejects_material_arrived_without_event_identity(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """MATERIAL_ARRIVED 缺少 event_id/vendor_event_id 时应显式失败。"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=2,
            payload_json={
                "device_code": "PIPELINE01",
                "event_type": "MATERIAL_ARRIVED",
                "data": {
                    "location": "STATION_INPUT1",
                },
            },
        )
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")

        with pytest.raises(
            SessionResolveError,
            match="Unable to resolve stable business_key from payload",
        ):
            await resolver.resolve_or_create(
                db=mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=make_devices_by_role(),
            )

        assert len(mock_session_repo.created_sessions) == 0

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
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")

        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        # PkgID 生成 16 位稳定业务键
        import hashlib
        import json

        expected_hash = hashlib.sha256(
            json.dumps("SVYU00125TP4LCR02_2", ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

        assert session.business_key == expected_hash
        assert session.barcode == "SVYU00125TP4LCR02_2"
        assert ("business_key", 1, expected_hash) in mock_session_repo.find_calls

    @pytest.mark.asyncio
    async def test_resolve_device_event_uses_incomplete_scan_key_when_smt_pkg_id_missing(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """SMT 扫码缺 PkgID 时仍需建会话，让插件生成 NG 分流指令。"""

        payload = {
            "device_code": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "canonical_event_type": "SCAN_COMPLETED",
            "timestamp": 1777338994000,
            "data": {
                "location": "ARM01",
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
            },
        }
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            source_message_id="req-incomplete-scan",
            payload_json=payload,
        )
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")

        session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        assert session.business_key.startswith("incomplete-scan:")
        assert session.business_key == _resolve_business_key(payload, plugin_key="smt_classifier")
        assert ("business_key", 1, session.business_key) in mock_session_repo.find_calls
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_reuses_incomplete_scan_session_across_timestamps(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """缺 PkgID 的同一份扫码证据不能因为跨秒重复上报而拆成多个 NG 周期。"""
        payload = {
            "device_code": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "canonical_event_type": "SCAN_COMPLETED",
            "timestamp": 1777338994000,
            "data": {
                "location": "ARM01",
                "HHPN": "620100L00-011-G",
                "MfrPN": "CC0402JRNPO9BN220",
                "Qty": "7387",
                "DateCode": "122625",
                "LotCode": "8904936031",
            },
        }
        later_payload = {**payload, "timestamp": 1777338999000}
        assert _resolve_business_key(payload, plugin_key="smt_classifier") == _resolve_business_key(
            later_payload,
            plugin_key="smt_classifier",
        )

        workline = make_workline(workline_id=1, plugin_key="smt_classifier")
        first_session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=make_inbox(kind=InboxKind.DEVICE_EVENT, device_id=1, payload_json=payload),
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )
        second_session = await resolver.resolve_or_create(
            db=mock_db,
            inbox=make_inbox(kind=InboxKind.DEVICE_EVENT, device_id=1, payload_json=later_payload),
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        assert second_session.id == first_session.id
        assert second_session.ingress_count == 2
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_rejects_six_in_one_without_plugin_key(
        self,
        mock_db,
        mock_session_repo,
        resolver,
    ):
        """plugin_key 缺失时不再由通用 resolver 解析 SMT Six-In-One。"""
        import hashlib
        import json

        expected_hash = hashlib.sha256(
            json.dumps("SVYU00125TP4LCR02_2", ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        _ = await mock_session_repo.create(
            mock_db,
            {
                "session_code": "SES_CANONICAL",
                "workline_id": 1,
                "plugin_key": None,
                "business_key": expected_hash,
                "status": SessionStatus.NEW,
                "ingress_count": 1,
                "last_request_id": "req-existing-none-plugin",
                "last_ingress_at": None,
                "trace_id": "trace-canonical-none-plugin",
                "context_json": {},
                "started_at": None,
            },
        )

        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            source_message_id="req-none-plugin",
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
        workline = make_workline(workline_id=1, plugin_key=None)

        with pytest.raises(
            SessionResolveError,
            match="Unable to resolve stable business_key from payload",
        ):
            await resolver.resolve_or_create(
                db=mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=make_devices_by_role(),
            )

        assert len(mock_session_repo.created_sessions) == 1
        assert ("business_key", 1, expected_hash) not in mock_session_repo.find_calls

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

        expected_hash = hashlib.sha256(
            json.dumps("SVYU00125TP4LCR02_2", ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        _ = await mock_session_repo.create(
            mock_db,
            {
                "session_code": "SES_EXISTING",
                "workline_id": 1,
                "plugin_key": "smt_classifier",
                "business_key": expected_hash,
                "status": SessionStatus.NEW,
                "ingress_count": 1,
                "last_request_id": "req-existing",
                "last_ingress_at": None,
                "trace_id": "trace_existing",
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
        workline = make_workline(workline_id=1, plugin_key="smt_classifier")

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
            trace_id="trace-301",
        )
        resolver.command_repo.commands["CMD-001"] = command

        existing_session = SimpleNamespace(
            id=401,
            awaiting_command_id=301,
            trace_id="trace-301",
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
        assert inbox.trace_id == "trace-301"
