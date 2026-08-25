"""
机械臂 Mock 服务

模拟搬运机械臂设备，用于 E2E 测试。

功能：
- 监听 127.0.0.1:8004
- 接收指令请求 POST /api/v1/device/command
- 返回 ACK 确认
- 支持指令取消 POST /api/v1/device/cancel
- 模拟执行后自动回调结果到 WES
- 调试接口：手动/自动执行指令，查看执行历史

运行方式：
    python tests/mock/robot_arm_mock_server.py
    或
    uv run python tests/mock/robot_arm_mock_server.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from uvicorn import Config, Server

# 添加项目根目录到 sys.path，使直接运行时能找到 src 模块
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# WES 回调地址（使用环境变量，默认 8001）
WES_CALLBACK_URL = os.getenv("WES_CALLBACK_URL", "http://host.docker.internal:8001/api/v1/callback/result")

# API 认证配置（设备调用 WES 回调接口时使用）
API_APP_ID = os.getenv("API_APP_ID", "app_Gqnvr3dpjGwlrjtO")
API_APP_SECRET = os.getenv("API_APP_SECRET", "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao")

# API_APP_ID = os.getenv("API_APP_ID", "app_sj1RniTg5ls5qCMG")
# API_APP_SECRET = os.getenv("API_APP_SECRET", "sec_MebzUjXBJ2iscKi9lM8X7s7gGN9OM1nCs6xg-uJ8Cgk")

# 机械臂配置
ROBOT_AUTO_EXECUTE_DEFAULT_INTERVAL = int(os.getenv("ROBOT_AUTO_EXECUTE_DEFAULT_INTERVAL", "5"))
ROBOT_BARCODE_PREFIX = os.getenv("ROBOT_BARCODE_PREFIX", "PKG")


# ============================================
# API 认证工具
# ============================================


def calculate_signature(app_secret: str, app_id: str, timestamp: str, method: str, path: str) -> str:
    """计算 API 签名

    签名字符串格式: {app_id}{timestamp}{method}{path}

    Args:
        app_secret: 应用密钥
        app_id: 应用 ID
        timestamp: 时间戳（秒）
        method: HTTP 方法（大写）
        path: 请求路径

    Returns:
        签名字符串（小写十六进制）
    """
    sign_string = f"{app_id}{timestamp}{method}{path}"
    return hmac.new(app_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).hexdigest()


def build_api_auth_headers(method: str, path: str) -> dict:
    """构建 API 认证所需的 HTTP Header

    Args:
        method: HTTP 方法（如 "POST"）
        path: 请求路径（如 "/api/v1/callback/result"）

    Returns:
        包含认证信息的 Header 字典
    """
    timestamp = str(int(time.time()))
    signature = calculate_signature(API_APP_SECRET, API_APP_ID, timestamp, method, path)

    return {
        "X-App-ID": API_APP_ID,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }


# ============================================
# 数据模型
# ============================================


class DeviceCommandAck(BaseModel):
    """设备 ACK 响应（白皮书格式）"""

    code: int
    message: str
    trace_id: str | None = None


class DeviceCommandPayload(BaseModel):
    """WES 下发的指令 Payload（白皮书格式）"""

    command_code: str
    task_type: str
    priority: int
    timeout: int
    params: dict
    timestamp: int


class CancelRequest(BaseModel):
    """取消指令请求"""

    command_code: str


# ============================================
# 调试接口数据模型
# ============================================


class ManualExecuteRequest(BaseModel):
    """手动执行指令请求"""

    task_type: str = "PICK_AND_PLACE"
    source_loc: str = "CONVEYOR-STATION-01"
    target_loc: str = "SHELF-A-01"
    barcode: str | None = None
    simulate_failure: bool = False
    execution_time: float = 2.0


class AutoExecuteConfig(BaseModel):
    """自动执行配置"""

    interval_seconds: int = ROBOT_AUTO_EXECUTE_DEFAULT_INTERVAL
    source_location: str = "CONVEYOR-STATION-01"
    target_locations: list[str] = ["SHELF-A-01", "SHELF-B-01"]
    barcode_prefix: str = ROBOT_BARCODE_PREFIX
    max_executions: int | None = None


class ExecutionRecord(BaseModel):
    """执行记录"""

    execution_id: str
    command_code: str
    task_type: str
    source_loc: str
    target_loc: str
    barcode: str | None
    result: str  # SUCCESS/FAILED
    error_detail: dict | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int


class RobotStatusResponse(BaseModel):
    """机械臂状态响应"""

    is_auto_executing: bool
    execution_count: int
    success_count: int
    failure_count: int
    current_command: dict | None = None
    current_config: dict | None = None


# Mock 设备信息
DEVICE_INFO = {
    "device_code": "ROBOT-ARM-01",
    "device_name": "搬运机械臂",
    "device_type": "ROBOTIC_ARM",
    "status": "IDLE",
    "is_online": True,
}


# ============================================
# 机械臂模拟器
# ============================================


class RobotSimulator:
    """机械臂模拟器

    模拟机械臂执行搬运指令并回调结果到 WES
    """

    def __init__(self, device_code: str = "ROBOT-ARM-01"):
        self.device_code = device_code
        self._counter = 0
        self._execution_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._executions: list[ExecutionRecord] = []
        self._is_auto_executing = False
        self._auto_task: asyncio.Task | None = None
        self._auto_stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    def _generate_command_code(self) -> str:
        """生成指令编码"""
        self._counter += 1
        return f"CMD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._counter:03d}"

    def _generate_barcode(self, prefix: str = ROBOT_BARCODE_PREFIX) -> str:
        """生成条码（格式：PREFIX + 日期 + 序号）"""
        today = datetime.now().strftime("%Y%m%d")
        return f"{prefix}{today}{self._counter:03d}"

    async def _callback_to_wes(
        self,
        command_code: str,
        result: str,
        source_loc: str | None = None,
        target_loc: str | None = None,
        barcode: str | None = None,
        error_detail: dict | None = None,
    ) -> dict:
        """回调执行结果到 WES

        POST /api/v1/callback/result（白皮书 3.2.1）
        """
        try:
            # 构建回调数据
            callback_data = {
                "command_code": command_code,
                "device_code": self.device_code,
                "result": result,
                "finish_time": int(datetime.now().timestamp() * 1000),
            }

            if result == "SUCCESS":
                callback_data["data"] = {
                    "actual_source": source_loc,
                    "actual_target": target_loc,
                }
                if barcode:
                    callback_data["data"]["barcode"] = barcode
            else:
                callback_data["error_detail"] = error_detail or {
                    "code": "EXECUTION_ERROR",
                    "msg": "执行失败",
                }

            # 构建 API 认证 Header
            # 从 WES_CALLBACK_URL 中提取路径
            from urllib.parse import urlparse

            parsed_url = urlparse(WES_CALLBACK_URL)
            auth_headers = build_api_auth_headers("POST", parsed_url.path)

            logger.info(f"回调结果到 WES: command_code={command_code}, result={result}, app_id={API_APP_ID}")

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    WES_CALLBACK_URL,
                    json=callback_data,
                    headers=auth_headers,
                )
                response.raise_for_status()
                result_data = response.json()
                logger.info(f"WES 回调成功: {result_data}")
                return result_data

        except httpx.HTTPStatusError as e:
            # HTTP 错误（401, 403, 500 等）
            status_code = e.response.status_code
            try:
                error_data = e.response.json()
                error_msg = error_data.get("message", str(e))
            except Exception:
                error_msg = str(e)

            logger.error(f"WES 回调失败 [HTTP {status_code}]: command_code={command_code}, error={error_msg}")
            # 回调失败不影响执行记录，只记录错误
            return {
                "callback_success": False,
                "error": f"HTTP {status_code}",
                "wes_error": error_msg,
            }

        except httpx.RequestError as e:
            # 网络错误
            logger.error(f"WES 回调失败 [网络错误]: command_code={command_code}, error={e}")
            return {
                "callback_success": False,
                "error": "wes_unreachable",
                "wes_url": WES_CALLBACK_URL,
            }

        except Exception as e:
            logger.error(f"WES 回调失败: {e}")
            return {
                "callback_success": False,
                "error": "internal_error",
                "details": str(e),
            }

    async def execute_command(
        self,
        task_type: str = "PICK_AND_PLACE",
        source_loc: str = "CONVEYOR-STATION-01",
        target_loc: str = "SHELF-A-01",
        barcode: str | None = None,
        simulate_failure: bool = False,
        execution_time: float = 2.0,
    ) -> ExecutionRecord:
        """执行搬运指令

        Args:
            task_type: 任务类型
            source_loc: 源位置
            target_loc: 目标位置
            barcode: 条码（可选，自动生成）
            simulate_failure: 是否模拟失败
            execution_time: 执行时间（秒）

        Returns:
            执行记录
        """
        async with self._lock:
            # 生成 command_code
            command_code = self._generate_command_code()

            # 生成条码（如果未提供）
            if barcode is None and not simulate_failure:
                barcode = self._generate_barcode()

            # 记录开始时间
            started_at = datetime.now()

            logger.info(
                f"开始执行指令: command_code={command_code}, task_type={task_type}, "
                f"source={source_loc}, target={target_loc}, barcode={barcode}"
            )

            # 模拟执行延时
            await asyncio.sleep(execution_time)

            # 确定执行结果
            result = "FAILED" if simulate_failure else "SUCCESS"
            error_detail = {"code": "SIMULATED_FAILURE", "msg": "模拟失败"} if simulate_failure else None

            # 回调到 WES
            try:
                await self._callback_to_wes(
                    command_code=command_code,
                    result=result,
                    source_loc=source_loc,
                    target_loc=target_loc,
                    barcode=barcode,
                    error_detail=error_detail,
                )
            except Exception as e:
                logger.error(f"回调失败: {e}")
                # 回调失败不影响执行记录

            # 记录结束时间
            finished_at = datetime.now()
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)

            # 创建执行记录
            execution_id = f"EXEC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._execution_count:03d}"
            record = ExecutionRecord(
                execution_id=execution_id,
                command_code=command_code,
                task_type=task_type,
                source_loc=source_loc,
                target_loc=target_loc,
                barcode=barcode,
                result=result,
                error_detail=error_detail,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )

            self._executions.append(record)
            self._execution_count += 1
            if result == "SUCCESS":
                self._success_count += 1
            else:
                self._failure_count += 1

            logger.info(f"指令执行完成: command_code={command_code}, result={result}, duration={duration_ms}ms")

            return record

    async def _auto_execute_loop(
        self,
        interval_seconds: int,
        source_location: str,
        target_locations: list[str],
        barcode_prefix: str,
        max_executions: int | None,
    ):
        """自动执行循环"""
        execution_count = 0
        target_index = 0

        while not self._auto_stop_event.is_set():
            # 检查是否达到最大执行次数
            if max_executions is not None and execution_count >= max_executions:
                logger.info(f"达到最大执行次数: {max_executions}")
                break

            try:
                # 轮询目标位置
                target_loc = target_locations[target_index % len(target_locations)]
                target_index += 1

                # 执行指令
                await self.execute_command(
                    task_type="PICK_AND_PLACE",
                    source_loc=source_location,
                    target_loc=target_loc,
                    barcode=self._generate_barcode(barcode_prefix),
                )
                execution_count += 1

            except Exception as e:
                logger.error(f"自动执行失败: {e}")

            # 等待下一次执行或停止信号
            try:
                await asyncio.wait_for(
                    self._auto_stop_event.wait(),
                    timeout=interval_seconds,
                )
                break  # 收到停止信号
            except TimeoutError:  # noqa: S112
                continue  # 超时，继续下一次执行

        # 自动停止
        await self.stop_auto_execution()

    async def start_auto_execution(self, config: AutoExecuteConfig) -> dict:
        """启动自动执行

        Args:
            config: 自动执行配置

        Returns:
            启动结果
        """
        async with self._lock:
            if self._is_auto_executing:
                raise HTTPException(status_code=400, detail="自动执行已在运行中")

            # 重置停止事件
            self._auto_stop_event.clear()

            # 创建异步任务
            self._auto_task = asyncio.create_task(
                self._auto_execute_loop(
                    interval_seconds=config.interval_seconds,
                    source_location=config.source_location,
                    target_locations=config.target_locations,
                    barcode_prefix=config.barcode_prefix,
                    max_executions=config.max_executions,
                )
            )

            self._is_auto_executing = True

            logger.info(
                f"自动执行已启动: interval={config.interval_seconds}s, "
                f"source={config.source_location}, targets={config.target_locations}"
            )

            return {
                "status": "started",
                "config": config.model_dump(),
            }

    async def stop_auto_execution(self) -> dict:
        """停止自动执行

        Returns:
            停止结果
        """
        async with self._lock:
            if not self._is_auto_executing:
                raise HTTPException(status_code=400, detail="自动执行未运行")

            # 设置停止事件
            self._auto_stop_event.set()

            # 等待任务结束
            if self._auto_task:
                self._auto_task.cancel()
                try:
                    await self._auto_task
                except asyncio.CancelledError:
                    pass
                self._auto_task = None

            self._is_auto_executing = False

            logger.info("自动执行已停止")

            return {
                "status": "stopped",
                "execution_count": self._execution_count,
            }

    def get_status(self, current_command: dict | None = None) -> RobotStatusResponse:
        """获取机械臂状态"""
        return RobotStatusResponse(
            is_auto_executing=self._is_auto_executing,
            execution_count=self._execution_count,
            success_count=self._success_count,
            failure_count=self._failure_count,
            current_command=current_command,
            current_config={
                "wes_callback_url": WES_CALLBACK_URL,
                "default_interval": ROBOT_AUTO_EXECUTE_DEFAULT_INTERVAL,
                "barcode_prefix": ROBOT_BARCODE_PREFIX,
            },
        )

    def get_executions(self, limit: int = 50) -> list[ExecutionRecord]:
        """获取执行记录"""
        return self._executions[-limit:]

    async def execute_wes_command(self, payload: DeviceCommandPayload) -> ExecutionRecord:
        """执行 WES 下发的指令

        与 execute_command() 类似，但接收 DeviceCommandPayload 格式

        Args:
            payload: WES 下发的指令 Payload

        Returns:
            执行记录
        """
        params = payload.params or {}
        task_type = payload.task_type
        source_loc = params.get("source_loc", "CONVEYOR-STATION-01")
        target_loc = params.get("target_loc", "SHELF-A-01")
        barcode = params.get("barcode")

        async with self._lock:
            # 记录开始时间
            started_at = datetime.now()

            logger.info(
                f"开始执行 WES 指令: command_code={payload.command_code}, task_type={task_type}, params={params}"
            )

            # 模拟执行时间（2秒）
            execution_time = 2.0
            await asyncio.sleep(execution_time)

            # 执行成功（WES 下发的指令默认成功）
            result = "SUCCESS"

            # 回调到 WES
            try:
                # 构建回调数据
                callback_data = {
                    "command_code": payload.command_code,
                    "device_code": self.device_code,
                    "result": result,
                    "finish_time": int(datetime.now().timestamp() * 1000),
                    "data": {
                        "actual_source": source_loc,
                        "actual_target": target_loc,
                    },
                }
                if barcode:
                    callback_data["data"]["barcode"] = barcode

                await self._callback_to_wes(
                    command_code=payload.command_code,
                    result=result,
                    source_loc=source_loc,
                    target_loc=target_loc,
                    barcode=barcode,
                )
            except Exception as e:
                logger.error(f"回调失败: {e}")

            # 记录结束时间
            finished_at = datetime.now()
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)

            # 创建执行记录
            execution_id = f"EXEC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._execution_count:03d}"
            record = ExecutionRecord(
                execution_id=execution_id,
                command_code=payload.command_code,
                task_type=task_type,
                source_loc=source_loc,
                target_loc=target_loc,
                barcode=barcode,
                result=result,
                error_detail=None,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )

            self._executions.append(record)
            self._execution_count += 1
            self._success_count += 1

            logger.info(
                f"WES 指令执行完成: command_code={payload.command_code}, result={result}, duration={duration_ms}ms"
            )

            return record


# 全局模拟器实例
robot_simulator = RobotSimulator(device_code=DEVICE_INFO["device_code"])


# ============================================
# FastAPI 应用
# ============================================

app = FastAPI(
    title="机械臂 Mock 服务",
    description="模拟搬运机械臂设备（含调试接口）",
    version="2.0.0",
)

# 存储当前执行的指令（用于 WES 下发的指令跟踪）
current_command: dict | None = None


@app.post("/api/v1/device/command")
async def receive_command(payload: DeviceCommandPayload) -> DeviceCommandAck:
    """
    接收 WES 下发的指令（白皮书 3.1 节）

    流程：
    1. 验证指令格式
    2. 更新设备状态为 RUNNING
    3. 立即返回 ACK
    4. 异步执行指令（模拟执行时间）
    5. 执行完成后回调结果到 WES
    """
    global current_command

    logger.info(f"收到指令: {payload.command_code}, task_type={payload.task_type}")

    # 验证设备状态
    if DEVICE_INFO["status"] == "RUNNING":
        logger.warning(f"设备忙，拒绝指令: {payload.command_code}")
        raise HTTPException(status_code=503, detail="Device Busy")

    # 更新设备状态
    DEVICE_INFO["status"] = "RUNNING"
    current_command = payload.model_dump()

    # 生成追踪 ID
    trace_id = f"ROBOT-LOG-{payload.command_code.split('-')[-1]}"

    # 立即返回 ACK
    ack = DeviceCommandAck(code=200, message="ACK", trace_id=trace_id)
    logger.info(f"指令已接受: {payload.command_code}, trace_id={trace_id}")

    # 异步执行指令（使用 RobotSimulator，以便记录执行历史）
    asyncio.create_task(_execute_wes_command_with_cleanup(payload))  # noqa: RUF006

    return ack


async def _execute_wes_command_with_cleanup(payload: DeviceCommandPayload):
    """执行 WES 指令并清理设备状态"""
    global current_command
    try:
        await robot_simulator.execute_wes_command(payload)
    finally:
        # 释放设备状态
        DEVICE_INFO["status"] = "IDLE"
        current_command = None


@app.post("/api/v1/device/cancel")
async def cancel_command(request: CancelRequest) -> DeviceCommandAck:
    """
    取消正在执行的指令（白皮书 3.1 节）
    """
    global current_command

    logger.info(f"收到取消指令请求: {request.command_code}")

    if current_command is None:
        logger.warning("没有正在执行的指令")
        raise HTTPException(status_code=404, detail="No Active Command")

    if current_command["command_code"] != request.command_code:
        logger.warning(f"指令编码不匹配: {request.command_code}")
        raise HTTPException(status_code=404, detail="Command Not Found")

    # 模拟取消成功
    DEVICE_INFO["status"] = "IDLE"
    current_command = None

    logger.info(f"指令已取消: {request.command_code}")

    return DeviceCommandAck(code=200, message="Cancelled")


@app.get("/api/v1/device/status")
async def get_status():
    """
    设备状态查询接口（白皮书 3.1 节）
    """
    return {
        "device_code": DEVICE_INFO["device_code"],
        "device_name": DEVICE_INFO["device_name"],
        "device_type": DEVICE_INFO["device_type"],
        "status": DEVICE_INFO["status"],
        "is_online": DEVICE_INFO["is_online"],
        "current_command_code": current_command["command_code"] if current_command else None,
        "timestamp": int(datetime.now().timestamp() * 1000),
    }


# ============================================
# 调试接口
# ============================================


@app.post("/api/v1/robot/execute", summary="手动执行指令")
async def execute_robot_command(request: ManualExecuteRequest) -> ExecutionRecord:
    """
    手动执行搬运指令（调试接口）

    **请求参数**：
    - task_type: 任务类型（默认 PICK_AND_PLACE）
    - source_loc: 源位置（默认 CONVEYOR-STATION-01）
    - target_loc: 目标位置（默认 SHELF-A-01）
    - barcode: 条码（可选，不提供则自动生成）
    - simulate_failure: 是否模拟失败（默认 false）
    - execution_time: 执行时间（秒，默认 2.0）

    **流程**：
    1. 生成指令 ID
    2. 模拟执行延时
    3. 回调结果到 WES
    4. 返回执行记录

    **示例**：
    ```bash
    curl -X POST http://localhost:8004/api/v1/robot/execute \\
      -H "Content-Type: application/json" \\
      -d '{
        "source_loc": "CONVEYOR-STATION-01",
        "target_loc": "SHELF-A-01",
        "barcode": "PKG-TEST-001"
      }'
    ```
    """
    return await robot_simulator.execute_command(
        task_type=request.task_type,
        source_loc=request.source_loc,
        target_loc=request.target_loc,
        barcode=request.barcode,
        simulate_failure=request.simulate_failure,
        execution_time=request.execution_time,
    )


@app.post("/api/v1/robot/auto/start", summary="启动自动执行")
async def start_auto_execute(config: AutoExecuteConfig) -> dict:
    """
    启动机械臂自动执行（调试接口）

    **请求参数**：
    - interval_seconds: 执行间隔（秒，默认 5）
    - source_location: 源位置（默认 CONVEYOR-STATION-01）
    - target_locations: 目标位置列表（默认 ["SHELF-A-01", "SHELF-B-01"]）
    - barcode_prefix: 条码前缀（默认 PKG）
    - max_executions: 最大执行次数（可选，不限制则为 None）

    **流程**：
    1. 创建后台定时任务
    2. 按间隔执行搬运指令
    3. 轮询目标位置
    4. 达到最大次数后自动停止

    **示例**：
    ```bash
    curl -X POST http://localhost:8004/api/v1/robot/auto/start \\
      -H "Content-Type: application/json" \\
      -d '{
        "interval_seconds": 5,
        "max_executions": 3
      }'
    ```
    """
    return await robot_simulator.start_auto_execution(config)


@app.post("/api/v1/robot/auto/stop", summary="停止自动执行")
async def stop_auto_execute() -> dict:
    """
    停止机械臂自动执行（调试接口）

    **示例**：
    ```bash
    curl -X POST http://localhost:8004/api/v1/robot/auto/stop
    ```
    """
    return await robot_simulator.stop_auto_execution()


@app.get("/api/v1/robot/status", summary="机械臂状态", response_model=RobotStatusResponse)
async def get_robot_status() -> RobotStatusResponse:
    """
    获取机械臂执行状态（调试接口）

    **返回**：
    - is_auto_executing: 是否正在自动执行
    - execution_count: 总执行次数
    - success_count: 成功次数
    - failure_count: 失败次数
    - current_command: 当前执行的 WES 指令
    - current_config: 当前配置

    **示例**：
    ```bash
    curl http://localhost:8004/api/v1/robot/status
    ```
    """
    return robot_simulator.get_status(current_command)


@app.get("/api/v1/robot/executions", summary="执行记录")
async def get_robot_executions(limit: int = 50) -> list[ExecutionRecord]:
    """
    获取机械臂执行记录（调试接口）

    **参数**：
    - limit: 返回记录数量（默认 50）

    **示例**：
    ```bash
    curl http://localhost:8004/api/v1/robot/executions?limit=10
    ```
    """
    return robot_simulator.get_executions(limit)


@app.get("/")
async def root():
    """根路径健康检查"""
    return {
        "service": "机械臂 Mock 服务",
        "version": "2.0.0",
        "status": "running",
        "device_code": DEVICE_INFO["device_code"],
        "robot": {
            "is_auto_executing": robot_simulator._is_auto_executing,
            "execution_count": robot_simulator._execution_count,
            "success_count": robot_simulator._success_count,
            "failure_count": robot_simulator._failure_count,
        },
        "current_command": current_command,
    }


# ============================================
# 服务器类
# ============================================


class RobotArmMockServer:
    """机械臂 Mock 服务器"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8004):
        self.host = host
        self.port = port
        self._server: Server | None = None
        self.config = Config(app=app, host=host, port=port, log_level="info")

    async def start(self) -> None:
        """启动服务器（阻塞运行）"""
        logger.info(f"机械臂 Mock 服务启动: http://{self.host}:{self.port}")
        logger.info(f"模拟设备: {DEVICE_INFO}")
        logger.info(f"WES 回调地址: {WES_CALLBACK_URL}")
        logger.info("设备接口:")
        logger.info("  - POST /api/v1/device/command")
        logger.info("  - POST /api/v1/device/cancel")
        logger.info("  - GET  /api/v1/device/status")
        logger.info("调试接口:")
        logger.info("  - POST /api/v1/robot/execute")
        logger.info("  - POST /api/v1/robot/auto/start")
        logger.info("  - POST /api/v1/robot/auto/stop")
        logger.info("  - GET  /api/v1/robot/status")
        logger.info("  - GET  /api/v1/robot/executions")

        self._server = Server(self.config)
        await self._server.serve()

    def run(self):
        """同步运行服务器"""
        asyncio.run(self.start())


# ============================================
# 直接运行时的入口
# ============================================

if __name__ == "__main__":
    server = RobotArmMockServer()
    server.run()
