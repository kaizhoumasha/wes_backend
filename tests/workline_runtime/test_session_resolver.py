"""
SessionResolver 单元测试

测试 Session 归属解析器的各种场景：
- DEVICE_EVENT: 按 device_id + business_key 查找或创建
- COMMAND_RESULT: 按 command_code -> awaiting_device_command_code 恢复 Session
- EXTERNAL_HTTP: 按 trace_id 恢复 Session
- TIMER_TIMEOUT: 按 session_id 恢复 Session
- MANUAL_*: 按 session_id 恢复 Session

设计参考: 设计文档 phase2-orchestrator
"""

import hashlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.models.inbox import InboxKind
from src.app.workline.models.session import RunMode, SessionStatus
from src.utils.timezone import timezone
from src.workline_plugin_registry import WORKLINE_PLUGIN_REGISTRY, WorklinePluginDefinition
from src.workline_runtime.plugin_manifest import (
    DeviceRequirement,
    RackPosition,
    RackPositionCarrierCapability,
    TopologySpec,
    WorklinePluginManifest,
)
from src.workline_runtime.session_resolver import (
    SessionResolveError,
    _resolve_business_key,
)
from tests.workline_runtime.support.runtime_builders import make_devices_by_role, make_inbox, make_workline

pytestmark = pytest.mark.usefixtures("registered_test_workline_plugin")


def _stable_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _plugin_manifest(plugin_key: str) -> WorklinePluginManifest:
    return WorklinePluginManifest(
        plugin_key=plugin_key,
        contract_version="test.v1",
        devices=(DeviceRequirement(role="SCANNER", min_count=0),),
        rack_positions=(
            RackPosition(
                code="ENTRY",
                role="ENTRY",
                station_code="ST-1",
                carrier_capability=RackPositionCarrierCapability(allowed_rack_kinds=("SINGLE_LAYER",)),
            ),
        ),
        topology=TopologySpec(),
    )


def _payload_data(payload_json: dict[str, object]) -> dict[str, object]:
    data = payload_json.get("data")
    return data if isinstance(data, dict) else {}


def _resolve_test_business_key(payload_json: dict[str, object]) -> str | None:
    data = _payload_data(payload_json)
    item_id = data.get("item_id")
    if isinstance(item_id, str) and item_id:
        return _stable_hash(item_id)

    required_without_item_id = ("part_no", "vendor_part_no", "quantity", "production_date", "lot_no")
    if all(isinstance(data.get(field), str) and data.get(field) for field in required_without_item_id):
        evidence = {field: data[field] for field in required_without_item_id}
        return f"incomplete-test-item:{_stable_hash(evidence)}"
    return None


def _resolve_rough_sorter_business_key(payload_json: dict[str, object]) -> str | None:
    data = _payload_data(payload_json)
    pkg_id = data.get("PkgID")
    return pkg_id if isinstance(pkg_id, str) and pkg_id else None


@pytest.fixture(autouse=True)
def registered_runtime_resolver_plugins(registered_test_workline_plugin):
    old_registry = dict(WORKLINE_PLUGIN_REGISTRY)
    WORKLINE_PLUGIN_REGISTRY["test_workline_plugin"] = WorklinePluginDefinition(
        plugin_key="test_workline_plugin",
        plugin_module=__name__,
        plugin_class_name="TestWorklineRuntimePlugin",
    )
    WORKLINE_PLUGIN_REGISTRY["rough_sorter"] = WorklinePluginDefinition(
        plugin_key="rough_sorter",
        plugin_module=__name__,
        plugin_class_name="RoughSorterRuntimePlugin",
    )
    try:
        yield
    finally:
        WORKLINE_PLUGIN_REGISTRY.clear()
        WORKLINE_PLUGIN_REGISTRY.update(old_registry)


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

    async def get_open_session_by_waiting_rack_operation_key(
        self,
        db: object,
        *,
        workline_id: int,
        operation_key: str,
    ) -> object | None:
        self.find_calls.append(("waiting_rack_operation_key", workline_id, operation_key))
        for session in self.sessions.values():
            s = session if isinstance(session, dict) else session.__dict__
            context = s.get("context_json") if isinstance(s.get("context_json"), dict) else {}
            if (
                s.get("workline_id") == workline_id
                and context.get("waiting_rack_operation_key") == operation_key
                and s.get("status") in ["NEW", "RUNNING", "WAITING_DEVICE_RESULT", "WAITING_EXTERNAL", "MANUAL_HOLD"]
            ):
                return session
        return None

    async def get_open_session_by_waiting_handling_operation_key(
        self,
        db: object,
        *,
        workline_id: int,
        operation_key: str,
    ) -> object | None:
        self.find_calls.append(("waiting_handling_operation_key", workline_id, operation_key))
        for session in self.sessions.values():
            s = session if isinstance(session, dict) else session.__dict__
            context = s.get("context_json") if isinstance(s.get("context_json"), dict) else {}
            if (
                s.get("workline_id") == workline_id
                and context.get("waiting_handling_operation_key") == operation_key
                and s.get("status") in ["NEW", "RUNNING", "WAITING_DEVICE_RESULT", "WAITING_EXTERNAL", "MANUAL_HOLD"]
            ):
                return session
        return None

    async def create(self, db: object, data: dict) -> object:
        session_id = self.next_id
        self.next_id += 1
        session = SimpleNamespace(id=session_id, **data)
        self.sessions[session_id] = session
        self.created_sessions.append(session)
        return session

    async def get_open_session_by_awaiting_device_command_code(self, db: object, command_code: str) -> object | None:
        self.find_calls.append(("awaiting_device_command_code", command_code, None))
        for session in self.sessions.values():
            s = session if isinstance(session, dict) else session.__dict__
            if s.get("awaiting_device_command_code") == command_code:
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


class MockRackTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[str, object] = {}
        self.find_calls: list[tuple[str, str]] = []

    async def get_by_dispatch_key(self, db: object, dispatch_key: str) -> object | None:
        self.find_calls.append(("dispatch_key", dispatch_key))
        return self.tasks.get(dispatch_key)


class MockHandlingStepRepository:
    def __init__(self) -> None:
        self.steps: dict[str, object] = {}
        self.find_calls: list[tuple[str, str]] = []

    async def get_by_dispatch_key(self, db: object, dispatch_key: str) -> object | None:
        self.find_calls.append(("dispatch_key", dispatch_key))
        return self.steps.get(dispatch_key)


class MockHandlingOperationRepository:
    def __init__(self) -> None:
        self.operations: dict[str, object] = {}
        self.find_calls: list[tuple[str, str]] = []

    async def get_by_operation_key(self, db: object, operation_key: str) -> object | None:
        self.find_calls.append(("operation_key", operation_key))
        return self.operations.get(operation_key)


class RuntimeBusinessKeyPlugin:
    """仅用于验证 registry helper 调用插件运行时方法。"""

    manifest = _plugin_manifest("runtime_business_key_plugin")

    def resolve_business_key(self, payload_json: dict) -> str | None:
        data = payload_json.get("data")
        if isinstance(data, dict):
            value = data.get("runtime_key")
            return value if isinstance(value, str) and value else None
        return None


class TestWorklineRuntimePlugin:
    """SessionResolver 测试用插件运行时。"""

    manifest = _plugin_manifest("test_workline_plugin")

    def resolve_business_key(self, payload_json: dict[str, object]) -> str | None:
        return _resolve_test_business_key(payload_json)


class RoughSorterRuntimePlugin:
    """粗分机 SessionResolver 测试用插件运行时。"""

    manifest = _plugin_manifest("rough_sorter")

    def resolve_business_key(self, payload_json: dict[str, object]) -> str | None:
        return _resolve_rough_sorter_business_key(payload_json)


def test_legacy_entry_blocker_symbols_removed() -> None:
    import importlib

    session_resolver_module = importlib.import_module("src.workline_runtime.session_resolver")

    legacy_error_name = "WorklineEntryAdmissionBlocked"
    legacy_lock_name = "_lock_workline_entry_admission"
    legacy_lookup_name = "_find_entry_admission_blocker_session"

    assert not hasattr(session_resolver_module, legacy_error_name)
    assert not hasattr(session_resolver_module, legacy_lock_name)
    assert not hasattr(session_resolver_module, legacy_lookup_name)
    assert not hasattr(session_resolver_module, "_lock_workline_entry_admission")
    assert legacy_error_name not in session_resolver_module.__all__


def test_resolve_business_key_uses_registry_plugin_runtime() -> None:
    old_definition = WORKLINE_PLUGIN_REGISTRY.get("runtime_business_key_plugin")
    WORKLINE_PLUGIN_REGISTRY["runtime_business_key_plugin"] = WorklinePluginDefinition(
        plugin_key="runtime_business_key_plugin",
        plugin_module=__name__,
        plugin_class_name="RuntimeBusinessKeyPlugin",
    )
    try:
        key = _resolve_business_key(
            {"business_key": "UPSTREAM-MISMATCH", "data": {"runtime_key": "runtime-key-001"}},
            plugin_key="runtime_business_key_plugin",
        )
    finally:
        if old_definition is None:
            WORKLINE_PLUGIN_REGISTRY.pop("runtime_business_key_plugin", None)
        else:
            WORKLINE_PLUGIN_REGISTRY["runtime_business_key_plugin"] = old_definition

    assert key == "runtime-key-001"


class TestSessionResolver:
    """SessionResolver 测试套件"""

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
        resolver.rack_task_repo = MockRackTaskRepository()
        resolver.handling_step_repo = MockHandlingStepRepository()
        resolver.handling_operation_repo = MockHandlingOperationRepository()
        return resolver

    @pytest.mark.asyncio
    async def test_resolve_device_event_creates_new_session(
        self,
        workline_runtime_mock_db,
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
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")
        workline.contract_version = "wl-2026.04"
        workline.run_mode = "SIMULATION"
        devices_by_role = make_devices_by_role()

        # Act
        with patch(
            "src.workline_runtime.session_resolver.get_plugin_contract_version",
            return_value="registry-legacy",
        ):
            session = await resolver.resolve_or_create(
                db=workline_runtime_mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=devices_by_role,
            )

        # Assert
        assert session is not None
        assert session.workline_id == 1
        assert session.plugin_key == "test_workline_plugin"
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
        workline_runtime_mock_db,
        resolver,
    ):
        """PostgreSQL 下同一 workline/business_key 必须先串行化再查建会话。"""
        workline_runtime_mock_db.get_bind = lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            source_message_id="req-lock",
            payload_json={"barcode": "PKG12345", "business_key": "ORDER_LOCK"},
        )
        workline = make_workline(workline_id=7, plugin_key="test_workline_plugin")

        _ = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        workline_runtime_mock_db.execute.assert_awaited()
        lock_statement, lock_params = workline_runtime_mock_db.execute.await_args_list[0].args
        assert "pg_advisory_xact_lock(hashtext(:lock_key))" in str(lock_statement)
        assert lock_params == {"lock_key": "workline-session:7:ORDER_LOCK"}
        assert len(workline_runtime_mock_db.execute.await_args_list) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_falls_back_to_registry_contract_version_when_workline_missing(
        self, workline_runtime_mock_db, mock_session_repo, resolver
    ):
        """workline.contract_version 缺失时才回退 registry。"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            source_message_id="req-002",
            payload_json={"barcode": "PKG12346", "business_key": "ORDER_003"},
        )
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")
        workline.contract_version = None

        with patch(
            "src.workline_runtime.session_resolver.get_plugin_contract_version",
            return_value="registry-2026.04",
        ):
            session = await resolver.resolve_or_create(
                db=workline_runtime_mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=make_devices_by_role(),
            )

        assert session.contract_version == "registry-2026.04"
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_does_not_use_line_code_as_plugin_key_fallback(
        self,
        workline_runtime_mock_db,
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
            db=workline_runtime_mock_db,
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
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 DEVICE_EVENT 查找已存在的 Session"""
        # Arrange - 预先创建一个 Session
        existing_session = SimpleNamespace(
            id=100,
            session_code="SESSION_100",
            workline_id=1,
            plugin_key="test_workline_plugin",
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
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
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
    async def test_resolve_device_event_reuses_terminal_session_for_same_business_key_without_time_window(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """同一物料终态后重复入口不能靠超过 5 秒绕过归档防线。"""
        existing_session = SimpleNamespace(
            id=101,
            session_code="SESSION_101",
            workline_id=1,
            plugin_key="test_workline_plugin",
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
            db=workline_runtime_mock_db,
            inbox=inbox,
            workline=make_workline(workline_id=1, plugin_key="test_workline_plugin"),
            devices_by_role=make_devices_by_role(),
        )

        assert session.id == 101
        assert session.ingress_count == 2
        assert inbox.session_id == 101
        assert len(mock_session_repo.created_sessions) == 0

    @pytest.mark.asyncio
    async def test_resolve_timer_timeout_finds_by_session_id(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 TIMER_TIMEOUT 按 session_id 恢复 Session"""
        # Arrange - 预先创建一个等待中的 Session
        existing_session = SimpleNamespace(
            id=200,
            session_code="SESSION_200",
            workline_id=1,
            plugin_key="test_workline_plugin",
            business_key="ORDER_002",
            status=SessionStatus.WAITING_DEVICE_RESULT,
            context_json={"step": "waiting_pick"},
        )
        mock_session_repo.sessions[200] = existing_session

        inbox = make_inbox(
            kind=InboxKind.TIMER_TIMEOUT,
            session_id=200,
        )
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
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
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 EXTERNAL_HTTP 按 trace_id 恢复 Session"""
        # Arrange - 预先创建一个等待外部系统的 Session
        existing_session = SimpleNamespace(
            id=300,
            session_code="SESSION_300",
            workline_id=1,
            plugin_key="test_workline_plugin",
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
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
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
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """同一 trace 存在多个外部等待时，应按 dispatch_key 归属到对应 outbox/session。"""
        smt_session = SimpleNamespace(
            id=300,
            session_code="SESSION_SMT_300",
            workline_id=45,
            plugin_key="test_workline_plugin",
            business_key="SMT_REEL_001",
            status=SessionStatus.WAITING_EXTERNAL,
            context_json={},
            trace_id="trace-shared-001",
        )
        fullbox_session = SimpleNamespace(
            id=301,
            session_code="SESSION_FULLBOX_301",
            workline_id=50,
            plugin_key="test_workline_plugin",
            business_key="release-001",
            status=SessionStatus.WAITING_EXTERNAL,
            context_json={},
            trace_id="trace-shared-001",
        )
        mock_session_repo.sessions[300] = smt_session
        mock_session_repo.sessions[301] = fullbox_session
        dispatch_key = "external:rack_exchange:release-001:RACK_OPERATION"
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
                "callback_type": "WMS_RACK_TASK_RESULT",
                "dispatch_key": dispatch_key,
            },
        )

        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=inbox,
            workline=None,
            devices_by_role=make_devices_by_role(),
        )

        assert session.id == 301
        assert inbox.session_id == 301
        assert inbox.workline_id == 50
        assert resolver.outbox_repo.find_calls == [("dispatch_key", dispatch_key)]

    @pytest.mark.asyncio
    async def test_external_http_callback_resolves_session_by_rack_operation_key(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """rack task 回调应按 operation_key 找回等待中的物料 session。"""
        material_session = SimpleNamespace(
            id=300,
            session_code="SESSION_SMT_300",
            workline_id=45,
            plugin_key="test_workline_plugin",
            business_key="SMT_REEL_001",
            status=SessionStatus.WAITING_EXTERNAL,
            context_json={
                "waiting_rack_operation_key": "rack-op:trace-001",
                "rack_operation": {"operation_key": "rack-op:trace-001", "status": "PENDING"},
            },
            trace_id="trace-shared-001",
        )
        mock_session_repo.sessions[300] = material_session
        dispatch_key = "external:test_workline_plugin:trace-001:RACK_OPERATION"
        resolver.outbox_repo.outboxes[dispatch_key] = SimpleNamespace(
            id=77,
            session_id=None,
            workline_id=45,
            dispatch_key=dispatch_key,
        )
        resolver.rack_task_repo.tasks[dispatch_key] = SimpleNamespace(
            id=901,
            dispatch_key=dispatch_key,
            workline_id=45,
            material_session_id=999,
            operation_key="rack-op:trace-001",
        )
        inbox = make_inbox(
            kind=InboxKind.EXTERNAL_HTTP,
            trace_id="trace-shared-001",
            payload_json={
                "callback_type": "WMS_RACK_ARRIVED",
                "dispatch_key": dispatch_key,
            },
        )

        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=inbox,
            workline=None,
            devices_by_role=make_devices_by_role(),
        )

        assert session.id == 300
        assert inbox.session_id == 300
        assert inbox.workline_id == 45
        assert resolver.rack_task_repo.find_calls == [("dispatch_key", dispatch_key)]
        assert ("waiting_rack_operation_key", 45, "rack-op:trace-001") in mock_session_repo.find_calls

    @pytest.mark.asyncio
    async def test_external_http_callback_does_not_resume_session_until_all_operation_tasks_succeeded(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """第一条 sibling task 成功回调只能归属 session，不能提前恢复等待态。"""
        material_session = SimpleNamespace(
            id=300,
            session_code="SESSION_SMT_300",
            workline_id=45,
            plugin_key="test_workline_plugin",
            business_key="SMT_REEL_001",
            status=SessionStatus.WAITING_EXTERNAL,
            current_wait_type="RACK_OPERATION",
            waiting_since=timezone.now_for_db(),
            deadline_at=timezone.now_for_db() + timedelta(minutes=5),
            current_wait_timeout_seconds=300,
            context_json={
                "waiting_rack_operation_key": "rack-op:trace-001",
                "rack_operation": {"operation_key": "rack-op:trace-001", "status": "PENDING"},
            },
            trace_id="trace-shared-001",
        )
        mock_session_repo.sessions[300] = material_session
        dispatch_key = "external:test_workline_plugin:trace-001:RACK_MOVE_OUT"
        resolver.rack_task_repo.tasks[dispatch_key] = SimpleNamespace(
            id=901,
            dispatch_key=dispatch_key,
            workline_id=45,
            operation_key="rack-op:trace-001",
            task_status="SUCCEEDED",
        )
        inbox = make_inbox(
            kind=InboxKind.EXTERNAL_HTTP,
            trace_id="trace-shared-001",
            payload_json={
                "callback_type": "WMS_RACK_TASK_RESULT",
                "dispatch_key": dispatch_key,
                "status": "SUCCEEDED",
            },
        )

        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=inbox,
            workline=None,
            devices_by_role=make_devices_by_role(),
        )

        assert session.id == 300
        assert session.status == SessionStatus.WAITING_EXTERNAL
        assert session.current_wait_type == "RACK_OPERATION"
        assert session.context_json["waiting_rack_operation_key"] == "rack-op:trace-001"

    @pytest.mark.asyncio
    async def test_external_http_callback_resolves_handling_operation_session(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        material_session = SimpleNamespace(
            id=301,
            session_code="SESSION_SMT_301",
            workline_id=45,
            plugin_key="test_workline_plugin",
            business_key="SMT_REEL_001",
            status=SessionStatus.WAITING_EXTERNAL,
            current_wait_type="HANDLING_OPERATION",
            waiting_since=timezone.now_for_db(),
            deadline_at=timezone.now_for_db() + timedelta(minutes=5),
            current_wait_timeout_seconds=300,
            context_json={
                "waiting_handling_operation_key": "bin-operation:trace-001",
                "handling_operation": {"operation_key": "bin-operation:trace-001", "status": "PENDING"},
            },
            trace_id="trace-bin-001",
        )
        mock_session_repo.sessions[301] = material_session
        dispatch_key = "handling:bin-operation:trace-001:move:1"
        resolver.handling_step_repo.steps[dispatch_key] = SimpleNamespace(
            id=701,
            dispatch_key=dispatch_key,
            operation_key="bin-operation:trace-001",
        )
        resolver.handling_operation_repo.operations["bin-operation:trace-001"] = SimpleNamespace(
            id=700,
            operation_key="bin-operation:trace-001",
            workline_id=45,
        )
        inbox = make_inbox(
            kind=InboxKind.EXTERNAL_HTTP,
            trace_id="trace-bin-001",
            payload_json={
                "callback_type": "CTU_BIN_MOVE_COMPLETED",
                "dispatch_key": dispatch_key,
                "status": "SUCCEEDED",
            },
        )

        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=inbox,
            workline=None,
            devices_by_role=make_devices_by_role(),
        )

        assert session.id == 301
        assert inbox.session_id == 301
        assert inbox.workline_id == 45
        assert resolver.handling_step_repo.find_calls == [("dispatch_key", dispatch_key)]
        assert resolver.handling_operation_repo.find_calls == [("operation_key", "bin-operation:trace-001")]
        assert ("waiting_handling_operation_key", 45, "bin-operation:trace-001") in mock_session_repo.find_calls

    @pytest.mark.asyncio
    async def test_resolve_manual_hold_by_session_id(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 MANUAL_HOLD 按 session_id 恢复 Session"""
        # Arrange
        existing_session = SimpleNamespace(
            id=400,
            session_code="SESSION_400",
            workline_id=1,
            plugin_key="test_workline_plugin",
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
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
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
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 TIMER_TIMEOUT 在 Session 不存在时抛出异常"""
        inbox = make_inbox(
            kind=InboxKind.TIMER_TIMEOUT,
            session_id=999,  # 不存在的 Session
        )
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")
        devices_by_role = make_devices_by_role()

        # Act & Assert
        with pytest.raises(ValueError, match="Session not found: 999"):
            await resolver.resolve_or_create(
                db=workline_runtime_mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=devices_by_role,
            )

    @pytest.mark.asyncio
    async def test_resolve_external_http_raises_when_trace_id_missing(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 EXTERNAL_HTTP 在 trace_id 缺失时抛出异常"""
        inbox = make_inbox(
            kind=InboxKind.EXTERNAL_HTTP,
            trace_id=None,  # 缺失 trace_id
        )
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")
        devices_by_role = make_devices_by_role()

        # Act & Assert
        with pytest.raises(ValueError, match="trace_id is required for EXTERNAL_HTTP"):
            await resolver.resolve_or_create(
                db=workline_runtime_mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=devices_by_role,
            )

    @pytest.mark.asyncio
    async def test_resolve_device_event_without_business_key_creates_session(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 DEVICE_EVENT 无 business_key 时创建新 Session"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            payload_json={"data": {"barcode": "PKG12345"}},  # 无 business_key，barcode 在 data 内
        )
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")
        devices_by_role = make_devices_by_role()

        # Act
        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
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

    def test_resolve_business_key_uses_plugin_runtime_resolver_for_test_item(self):
        """测试插件业务键由插件运行时 resolver 解析。"""
        payload = {
            "data": {
                "part_no": "PART-001",
                "vendor_part_no": "VENDOR-PART-001",
                "quantity": "7387",
                "lot_no": "LOTABC123",
                "production_date": "20260409",
                "item_id": "ITEM-001",
            }
        }

        key1 = _resolve_business_key(payload, plugin_key="test_workline_plugin")
        key2 = _resolve_business_key(payload, plugin_key="test_workline_plugin")

        expected_hash = _stable_hash("ITEM-001")

        assert key1 == expected_hash
        assert key2 == expected_hash

    def test_resolve_business_key_prefers_plugin_manifest_key_over_upstream_business_key(self):
        """插件解析器命中业务键时，不应再信任外部透传 business_key。"""
        payload = {
            "business_key": "UPSTREAM-MISMATCH",
            "data": {
                "part_no": "PART-001",
                "vendor_part_no": "VENDOR-PART-001",
                "quantity": "7387",
                "lot_no": "LOTABC123",
                "production_date": "20260409",
                "item_id": "ITEM-001",
            },
        }

        key = _resolve_business_key(payload, plugin_key="test_workline_plugin")

        import hashlib
        import json

        expected_hash = hashlib.sha256(json.dumps("ITEM-001", ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

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
            _resolve_business_key(payload, plugin_key="test_workline_plugin")

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
            _resolve_business_key(payload, plugin_key="test_workline_plugin")

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
        workline_runtime_mock_db,
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
                db=workline_runtime_mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=make_devices_by_role(),
            )

        assert len(mock_session_repo.created_sessions) == 0

    @pytest.mark.asyncio
    async def test_resolve_device_event_rejects_estop_as_normal_session_event(
        self,
        workline_runtime_mock_db,
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
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")

        with pytest.raises(
            SessionResolveError,
            match="Unable to resolve stable business_key from payload",
        ):
            await resolver.resolve_or_create(
                db=workline_runtime_mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=make_devices_by_role(),
            )

        assert len(mock_session_repo.created_sessions) == 0

    @pytest.mark.asyncio
    async def test_resolve_device_event_uses_event_scope_key_for_material_arrived(
        self,
        workline_runtime_mock_db,
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
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")

        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
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
        workline_runtime_mock_db,
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
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")

        with pytest.raises(
            SessionResolveError,
            match="Unable to resolve stable business_key from payload",
        ):
            await resolver.resolve_or_create(
                db=workline_runtime_mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=make_devices_by_role(),
            )

        assert len(mock_session_repo.created_sessions) == 0

    @pytest.mark.asyncio
    async def test_resolve_device_event_uses_test_plugin_item_fields_as_business_key(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试 DEVICE_EVENT 使用测试插件字段生成稳定 business_key。"""
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            payload_json={
                "data": {
                    "part_no": "PART-001",
                    "vendor_part_no": "VENDOR-PART-001",
                    "quantity": "7387",
                    "lot_no": "LOTABC123",
                    "production_date": "20260409",
                    "item_id": "ITEM-001",
                }
            },
        )
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")

        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        # item_id 生成 16 位稳定业务键
        import hashlib
        import json

        expected_hash = hashlib.sha256(json.dumps("ITEM-001", ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

        assert session.business_key == expected_hash
        assert session.barcode is None
        assert ("business_key", 1, expected_hash) in mock_session_repo.find_calls

    @pytest.mark.asyncio
    async def test_resolve_device_event_uses_rough_sorter_data_pkg_id_as_business_key(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """粗分机 DEVICE_EVENT 应通过 payload.data.PkgID 派生并复用同一 Session。"""

        payload = {
            "device_code": "RS-SCAN-01",
            "event_type": "SCAN_COMPLETED",
            "data": {
                "HHPN": "HH-001",
                "MfrPN": "MFR-001",
                "Qty": "1500",
                "DateCode": "260528",
                "LotCode": "LOT-A",
                "PkgID": "PKG-ROUGH-001",
            },
        }
        expected_key = "PKG-ROUGH-001"
        workline = make_workline(workline_id=1, plugin_key="rough_sorter")

        first_session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=make_inbox(kind=InboxKind.DEVICE_EVENT, device_id=1, payload_json=payload),
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )
        second_session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=make_inbox(kind=InboxKind.DEVICE_EVENT, device_id=1, payload_json=payload),
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        assert first_session.business_key == expected_key
        assert second_session.id == first_session.id
        assert second_session.ingress_count == 2
        assert ("business_key", 1, expected_key) in mock_session_repo.find_calls

    @pytest.mark.asyncio
    async def test_resolve_device_event_uses_incomplete_scan_key_when_test_item_id_missing(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试插件扫码缺 item_id 时仍需建会话，让插件生成 NG 分流指令。"""

        payload = {
            "device_code": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "canonical_event_type": "SCAN_COMPLETED",
            "timestamp": 1777338994000,
            "data": {
                "location": "ARM01",
                "part_no": "PART-001",
                "vendor_part_no": "VENDOR-PART-001",
                "quantity": "7387",
                "production_date": "122625",
                "lot_no": "8904936031",
            },
        }
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            source_message_id="req-incomplete-scan",
            payload_json=payload,
        )
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")

        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=inbox,
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        assert session.business_key.startswith("incomplete-test-item:")
        assert session.business_key == _resolve_business_key(payload, plugin_key="test_workline_plugin")
        assert ("business_key", 1, session.business_key) in mock_session_repo.find_calls
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_reuses_incomplete_scan_session_across_timestamps(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """缺 item_id 的同一份扫码证据不能因为跨秒重复上报而拆成多个 NG 周期。"""
        payload = {
            "device_code": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "canonical_event_type": "SCAN_COMPLETED",
            "timestamp": 1777338994000,
            "data": {
                "location": "ARM01",
                "part_no": "PART-001",
                "vendor_part_no": "VENDOR-PART-001",
                "quantity": "7387",
                "production_date": "122625",
                "lot_no": "8904936031",
            },
        }
        later_payload = {**payload, "timestamp": 1777338999000}
        assert _resolve_business_key(payload, plugin_key="test_workline_plugin") == _resolve_business_key(
            later_payload,
            plugin_key="test_workline_plugin",
        )

        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")
        first_session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=make_inbox(kind=InboxKind.DEVICE_EVENT, device_id=1, payload_json=payload),
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )
        second_session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=make_inbox(kind=InboxKind.DEVICE_EVENT, device_id=1, payload_json=later_payload),
            workline=workline,
            devices_by_role=make_devices_by_role(),
        )

        assert second_session.id == first_session.id
        assert second_session.ingress_count == 2
        assert len(mock_session_repo.created_sessions) == 1

    @pytest.mark.asyncio
    async def test_resolve_device_event_rejects_test_plugin_item_without_plugin_key(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """plugin_key 缺失时不再由通用 resolver 解析测试插件专属字段。"""
        import hashlib
        import json

        expected_hash = hashlib.sha256(json.dumps("ITEM-001", ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        _ = await mock_session_repo.create(
            workline_runtime_mock_db,
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
                    "part_no": "PART-001",
                    "vendor_part_no": "VENDOR-PART-001",
                    "quantity": "7387",
                    "lot_no": "LOTABC123",
                    "production_date": "20260409",
                    "item_id": "ITEM-001",
                }
            },
        )
        workline = make_workline(workline_id=1, plugin_key=None)

        with pytest.raises(
            SessionResolveError,
            match="Unable to resolve stable business_key from payload",
        ):
            await resolver.resolve_or_create(
                db=workline_runtime_mock_db,
                inbox=inbox,
                workline=workline,
                devices_by_role=make_devices_by_role(),
            )

        assert len(mock_session_repo.created_sessions) == 1
        assert ("business_key", 1, expected_hash) not in mock_session_repo.find_calls

    @pytest.mark.asyncio
    async def test_resolve_device_event_test_plugin_item_key_reuses_existing_session(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        """测试相同的测试插件数据会命中同一 business_key。"""
        import hashlib
        import json

        expected_hash = hashlib.sha256(json.dumps("ITEM-001", ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        _ = await mock_session_repo.create(
            workline_runtime_mock_db,
            {
                "session_code": "SES_EXISTING",
                "workline_id": 1,
                "plugin_key": "test_workline_plugin",
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
                    "part_no": "PART-001",
                    "vendor_part_no": "VENDOR-PART-001",
                    "quantity": "7387",
                    "lot_no": "LOTABC123",
                    "production_date": "20260409",
                    "item_id": "ITEM-001",
                }
            },
        )
        workline = make_workline(workline_id=1, plugin_key="test_workline_plugin")

        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
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
    async def test_resolve_command_result_finds_session_by_awaiting_device_command_code(
        self,
        workline_runtime_mock_db,
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
            awaiting_device_command_code="CMD-001",
            trace_id="trace-301",
            status=SessionStatus.WAITING_DEVICE_RESULT,
        )
        mock_session_repo.sessions[401] = existing_session

        inbox = make_inbox(
            kind=InboxKind.COMMAND_RESULT,
            payload_json={"command_code": "CMD-001"},
        )

        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
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

    @pytest.mark.asyncio
    async def test_resolve_command_result_does_not_fallback_to_trace_id(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        command = SimpleNamespace(
            id=302,
            command_code="CMD-NOT-AWAITED",
            device_id=11,
            workline_id=1,
            trace_id="trace-shared-command",
        )
        resolver.command_repo.commands["CMD-NOT-AWAITED"] = command

        trace_matched_session = SimpleNamespace(
            id=402,
            awaiting_device_command_code="CMD-OTHER",
            trace_id="trace-shared-command",
            status=SessionStatus.WAITING_DEVICE_RESULT,
        )
        mock_session_repo.sessions[402] = trace_matched_session

        inbox = make_inbox(
            kind=InboxKind.COMMAND_RESULT,
            payload_json={"command_code": "CMD-NOT-AWAITED"},
        )

        with pytest.raises(ValueError, match="Session not found for command_code: CMD-NOT-AWAITED"):
            await resolver.resolve_or_create(
                db=workline_runtime_mock_db,
                inbox=inbox,
                workline=make_workline(),
                devices_by_role=make_devices_by_role(),
            )

        assert ("awaiting_device_command_code", "CMD-NOT-AWAITED", None) in mock_session_repo.find_calls
        assert ("trace_id", 0, "trace-shared-command") not in mock_session_repo.find_calls
        assert inbox.command_id == 302
        assert inbox.trace_id == "trace-shared-command"
        assert getattr(inbox, "session_id", None) is None

    @pytest.mark.asyncio
    async def test_device_event_creates_new_session_when_other_business_key_open(
        self,
        workline_runtime_mock_db,
        mock_session_repo,
        resolver,
    ):
        blocker = SimpleNamespace(
            id=501,
            workline_id=1,
            business_key="busy-material",
            status=SessionStatus.WAITING_DEVICE_RESULT,
        )
        mock_session_repo.sessions[501] = blocker
        inbox = make_inbox(
            kind=InboxKind.DEVICE_EVENT,
            device_id=1,
            source_message_id="req-next-material",
            payload_json={
                "data": {
                    "part_no": "PART-NEXT",
                    "vendor_part_no": "VENDOR-NEXT",
                    "quantity": "1",
                    "lot_no": "LOT-NEXT",
                    "production_date": "20260604",
                    "item_id": "ITEM-NEXT",
                }
            },
        )

        session = await resolver.resolve_or_create(
            db=workline_runtime_mock_db,
            inbox=inbox,
            workline=make_workline(workline_id=1, plugin_key="test_workline_plugin"),
            devices_by_role=make_devices_by_role(),
        )

        assert session.id != blocker.id
        assert session.workline_id == 1
        assert session.business_key
        assert session.business_key != blocker.business_key
        assert session.status == SessionStatus.NEW
        assert len(mock_session_repo.created_sessions) == 1
        assert ("entry_blocker", 1, session.business_key) not in mock_session_repo.find_calls
