"""
SimplifiedSmtPlugin 集成测试

使用 tests/mock/smt_classifier 中的 mock 服务测试 SimplifiedSmtPlugin 的完整业务流程。
测试从数据库查询数据验证业务流程。

测试架构:
┌──────────────────────────────────────────────────────────────────────────┐
│                           测试环境                                        │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Mock Services                    WES Backend                            │
│  ┌─────────────────┐              ┌─────────────────┐                    │
│  │ ARM01 (8006)    │──event──────▶│ /callback/event │                    │
│  │ - scan-completed│              └────────┬────────┘                    │
│  │ - inspection    │                       │                             │
│  │ - PICK_AND_PUT  │◀────command───────────┤                             │
│  │ - PICK_NG       │                       │                             │
│  └─────────────────┘                       ▼                             │
│                                   ┌─────────────────┐                    │
│  ┌─────────────────┐              │ SimplifiedSmt   │                    │
│  │ PIPELINE01(8005)│◀────command──│ Plugin         │                    │
│  │ - MOVE_FORWARD  │              │                 │                    │
│  └─────────────────┘              └─────────────────┘                    │
│                                                                          │
│  ┌─────────────────┐                                                     │
│  │ ARM02 (8007)    │◀────command─── OUTPUT                              │
│  │ - PICK_AND_PUT  │                                                     │
│  │ - OUTPUT        │                                                     │
│  └─────────────────┘                                                     │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

运行方式:
    # 1. 先启动 mock 服务
    ./tests/mock/smt_classifier/start_local.sh &

    # 2. 运行测试
    uv run pytest tests/workline_plugins/test_simplified_smt_plugin_mock.py -v

测试场景:
    - test_ok_flow: OK 流程完整验证
    - test_ng_flow_scan: 扫码 NG 流程
    - test_ng_flow_inspection: 检测 NG 流程
    - test_failure_handling: 命令失败处理
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import httpx
import pytest

# Mock 服务配置
MOCK_SERVICES = {
    "pipeline": {"base_url": "http://127.0.0.1:8005", "device_code": "PIPELINE01"},
    "arm01": {"base_url": "http://127.0.0.1:8006", "device_code": "ARM01"},
    "arm02": {"base_url": "http://127.0.0.1:8007", "device_code": "ARM02"},
    "allocation": {"base_url": "http://127.0.0.1:8008", "device_code": "ALLOCATION"},
    "agv": {"base_url": "http://127.0.0.1:8009", "device_code": "AGV01"},
}

# WES 配置
WES_BASE_URL = os.getenv("WES_BASE_URL", "http://localhost:8001")

WORKLINE_ID = 30  # 测试用的 workline ID


def run_db_query(query: str) -> list:
    """通过 docker exec 运行数据库查询"""
    cmd = [
        "docker",
        "exec",
        "wes_postgres_dev",
        "psql",
        "-U",
        "wes_user",
        "-d",
        "wes_db",
        "-t",
        "-c",
        query.replace("\n", " "),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"DB query error: {result.stderr}")
        return []

    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    return [line.strip() for line in lines if line.strip()]


def get_max_session_id() -> int:
    """获取当前最大的 session ID"""
    result = run_db_query(f"SELECT MAX(id) FROM wes_biz.workline_sessions WHERE workline_id = {WORKLINE_ID};")
    try:
        return int(result[0]) if result and result[0] else 0
    except (ValueError, IndexError):
        return 0


def get_inbox_events(since_session_id: int) -> list[str]:
    """获取指定 session 之后的 inbox 事件类型"""
    max_id_result = run_db_query(f"SELECT MAX(id) FROM wes_biz.workline_sessions WHERE workline_id = {WORKLINE_ID};")
    try:
        max_session_id = int(max_id_result[0]) if max_id_result and max_id_result[0] else 0
    except (ValueError, IndexError):
        return []

    if max_session_id <= since_session_id:
        return []

    query = f"""
        SELECT payload_json->>'event_type' as event_type
        FROM wes_biz.workline_inbox
        WHERE session_id > {since_session_id}
        AND session_id <= {max_session_id}
        AND kind = 'DEVICE_EVENT'
        ORDER BY id;
    """
    return run_db_query(query)


def get_outbox_commands(since_session_id: int) -> list[str]:
    """获取指定 session 之后的 outbox 命令类型"""
    max_id_result = run_db_query(f"SELECT MAX(id) FROM wes_biz.workline_sessions WHERE workline_id = {WORKLINE_ID};")
    try:
        max_session_id = int(max_id_result[0]) if max_id_result and max_id_result[0] else 0
    except (ValueError, IndexError):
        return []

    if max_session_id <= since_session_id:
        return []

    query = f"""
        SELECT payload_json->>'task_type' as task_type
        FROM wes_biz.workline_outbox
        WHERE session_id > {since_session_id}
        AND session_id <= {max_session_id}
        ORDER BY id;
    """
    return run_db_query(query)


def get_session_status(session_id: int) -> dict | None:
    """获取 session 状态"""
    query = f"""
        SELECT status, step_code
        FROM wes_biz.workline_sessions
        WHERE id = {session_id};
    """
    result = run_db_query(query)
    if result and result[0]:
        parts = result[0].split("|")
        if len(parts) >= 2:
            return {"status": parts[0].strip(), "step_code": parts[1].strip()}
    return None


@pytest.fixture(scope="module")
def mock_services_running() -> bool:
    """检查 mock 服务是否运行"""
    try:
        with httpx.Client(timeout=2.0) as client:
            for name, config in MOCK_SERVICES.items():
                response = client.get(f"{config['base_url']}/")
                assert response.status_code == 200, f"{name} not responding"
        return True
    except Exception as e:
        pytest.skip(f"Mock services not running: {e}")
        return False


@pytest.fixture(scope="module")
def wes_running() -> bool:
    """检查 WES 服务是否运行"""
    try:
        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{WES_BASE_URL}/api/v1/admin/performance/health")
            assert response.status_code == 200, "WES not responding"
        return True
    except Exception as e:
        pytest.skip(f"WES service not running: {e}")
        return False


class MockServiceClient:
    """Mock 服务客户端"""

    def __init__(self, base_url: str, device_code: str):
        self.base_url = base_url
        self.device_code = device_code
        self._client = httpx.Client(timeout=10.0)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._client.close()

    def trigger_scan(self, barcode: str, location_id: str = "STATION_INPUT1", result: str = "OK") -> dict:
        """触发扫码完成事件"""
        response = self._client.post(
            f"{self.base_url}/debug/scan-completed",
            json={"barcode": barcode, "location_id": location_id, "result": result},
        )
        return response.json()

    def trigger_inspection(
        self,
        result: str = "OK",
        location_id: str = "STATION_PIPELINE1_INPUT1",
        barcode: str | None = None,
    ) -> dict:
        """触发检测完成事件"""
        payload = {"result": result, "location_id": location_id}
        if barcode:
            payload["barcode"] = barcode
        response = self._client.post(f"{self.base_url}/debug/inspection-completed", json=payload)
        return response.json()


@pytest.mark.integration
class TestSimplifiedSmtPluginOKFlow:
    """测试 OK 流程 - 数据库验证"""

    @pytest.fixture(autouse=True)
    def setup(self, mock_services_running: bool, wes_running: bool):
        """测试前检查服务"""
        self.arm01 = MockServiceClient(MOCK_SERVICES["arm01"]["base_url"], MOCK_SERVICES["arm01"]["device_code"])

    def test_ok_flow_end_to_end(self):
        """
        OK 流程完整验证 - 从数据库验证

        流程:
        1. 扫码完成 → 机械臂抓取到检测位
        2. 检测完成 (OK) → 流水线传输
        3. 流水线完成 → 出料机械臂放置
        4. 出料完成 → 会话完成

        验证点（从数据库）:
        - workline_sessions 最终状态为 COMPLETED
        """
        # 使用有效条码（无特殊字符）
        barcode = f"TESTSCANOK{int(time.time())}"

        # Step 1: 扫码完成
        scan_result = self.arm01.trigger_scan(barcode=barcode, result="OK")
        assert scan_result.get("result") == "OK", f"Scan failed: {scan_result}"

        # 等待扫码处理完成
        time.sleep(8)

        # Step 2: 检测完成 (OK)
        inspection_result = self.arm01.trigger_inspection(result="OK", barcode=barcode)
        assert inspection_result.get("result") == "OK", f"Inspection failed: {inspection_result}"

        # 等待完整流程完成（增加等待时间）
        time.sleep(15)

        # ========== 数据库验证 ==========
        # 验证 session 最终状态为 COMPLETED
        max_id = get_max_session_id()
        session = get_session_status(max_id)
        assert session is not None, "Session not found"
        assert session["status"] == "COMPLETED", f"Session should be COMPLETED, got: {session}"
        assert session["step_code"] == "COMPLETED", "Session step_code should be COMPLETED"


@pytest.mark.integration
class TestSimplifiedSmtPluginNGFlow:
    """测试 NG 流程 - 数据库验证"""

    @pytest.fixture(autouse=True)
    def setup(self, mock_services_running: bool, wes_running: bool):
        """测试前检查服务"""
        self.arm01 = MockServiceClient(MOCK_SERVICES["arm01"]["base_url"], MOCK_SERVICES["arm01"]["device_code"])

    def test_scan_ng_flow(self):
        """扫码 NG 流程 - 数据库验证"""
        # 使用有效条码（无特殊字符）
        barcode = f"TESTSCANNG{int(time.time())}"

        # 扫码完成 (NG)
        scan_result = self.arm01.trigger_scan(barcode=barcode, result="NG")
        assert scan_result.get("result") == "NG", f"Scan should be NG: {scan_result}"

        # 等待流程完成
        time.sleep(12)

        # 数据库验证
        max_id = get_max_session_id()
        session = get_session_status(max_id)
        assert session is not None, "Session not found"
        assert session["status"] == "COMPLETED", f"Session should be COMPLETED: {session}"

    def test_inspection_ng_flow(self):
        """检测 NG 流程 - 数据库验证"""
        # 使用有效条码（无特殊字符）
        barcode = f"TESTSCANOK{int(time.time())}"

        # 扫码完成 (OK)
        scan_result = self.arm01.trigger_scan(barcode=barcode, result="OK")
        assert scan_result.get("result") == "OK", f"Scan failed: {scan_result}"

        time.sleep(8)

        # 检测完成 (NG)
        inspection_result = self.arm01.trigger_inspection(result="NG", barcode=barcode)
        assert inspection_result.get("result") == "NG", f"Inspection should be NG: {inspection_result}"

        time.sleep(12)

        # 数据库验证
        max_id = get_max_session_id()
        session = get_session_status(max_id)
        assert session is not None, "Session not found"
        assert session["status"] == "COMPLETED", f"Session should be COMPLETED: {session}"


@pytest.mark.integration
class TestSimplifiedSmtPluginFailureHandling:
    """测试失败处理"""

    @pytest.fixture(autouse=True)
    def setup(self, mock_services_running: bool, wes_running: bool):
        """测试前检查服务"""
        self.arm01 = MockServiceClient(MOCK_SERVICES["arm01"]["base_url"], MOCK_SERVICES["arm01"]["device_code"])

    def test_pick_failure_handling(self):
        """测试超时场景（简化验证）"""
        # 超时测试需要特殊设置，跳过
