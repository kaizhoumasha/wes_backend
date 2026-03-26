"""
SMT 粗分机机械臂 Mock 服务

模拟 SMT 粗分机工作线的进料/出料机械臂设备，用于 E2E 测试。

功能：
- 支持两个设备实例（通过环境变量 DEVICE_ID 控制）
- ARM01: 进料机械臂 - 从串杆抓取放到流水线入口 (端口 8006)
- ARM02: 出料机械臂 - 从流水线出口抓取放入料箱 (端口 8007)
- 监听 WES 指令 POST /api/v1/device/command
- 返回 ACK 确认
- 支持指令取消 POST /api/v1/device/cancel
- 模拟执行后自动回调结果到 WES
- 调试接口：手动/自动执行指令，查看执行历史

运行方式：
    # 运行 ARM01 (进料机械臂)
    DEVICE_ID=ARM01 python tests/mock/smt_classifier/arm_mock.py

    # 运行 ARM02 (出料机械臂)
    DEVICE_ID=ARM02 python tests/mock/smt_classifier/arm_mock.py
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
from typing import Any, Literal, NoReturn, TypedDict, cast

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from uvicorn import Config, Server

# 添加项目根目录到 sys.path，使直接运行时能找到 src 模块
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# ============================================
# 设备配置
# ============================================

# 设备 ID（通过环境变量控制，优先使用 DEVICE_CODE，兼容 run_all.py）
DEVICE_ID = os.getenv("DEVICE_CODE") or os.getenv("DEVICE_ID", "ARM01")


class DeviceLocations(TypedDict):
    source: list[str]
    target: list[str]


class DeviceConfig(TypedDict):
    device_id: str
    device_name: str
    device_type: str
    port: int
    description: str
    task_types: list[str]
    locations: DeviceLocations
    default_source: str
    default_target: str


class DeviceRuntimeStatus(TypedDict):
    device_id: str
    device_name: str
    device_type: str
    status: Literal["IDLE", "RUNNING"]
    is_online: bool


# 设备配置映射
DEVICE_CONFIGS: dict[str, DeviceConfig] = {
    "ARM01": {
        "device_id": "ARM01",
        "device_name": "进料机械臂",
        "device_type": "ROBOTIC_ARM",
        "port": 8006,
        "description": "从串杆抓取放到流水线入口",
        # 支持的任务类型
        "task_types": ["PICK_FROM_POLE", "PLACE_TO_CONVEYOR"],
        # 位置映射
        "locations": {
            "source": ["POLE_A", "POLE_B", "POLE_C"],  # 串杆位置
            "target": ["CONVEYOR_IN"],  # 流水线入口
        },
        # 默认位置
        "default_source": "POLE_A",
        "default_target": "CONVEYOR_IN",
    },
    "ARM02": {
        "device_id": "ARM02",
        "device_name": "出料机械臂",
        "device_type": "ROBOTIC_ARM",
        "port": 8007,
        "description": "从流水线出口抓取放入料箱",
        # 支持的任务类型
        "task_types": ["PICK_FROM_CONVEYOR", "PLACE_TO_BIN"],
        # 位置映射
        "locations": {
            "source": ["CONVEYOR_OUT"],  # 流水线出口
            "target": ["BIN_OK", "BIN_NG"],  # 料箱位置
        },
        # 默认位置
        "default_source": "CONVEYOR_OUT",
        "default_target": "BIN_OK",
    },
}

# 当前设备配置
if DEVICE_ID not in DEVICE_CONFIGS:
    raise ValueError(f"无效的设备 ID: {DEVICE_ID}，支持的设备: {list(DEVICE_CONFIGS.keys())}")

CURRENT_DEVICE: DeviceConfig = DEVICE_CONFIGS[DEVICE_ID]
DEVICE_PORT = int(os.getenv("DEVICE_PORT", CURRENT_DEVICE["port"]))

# WES 回调地址
WES_CALLBACK_URL = os.getenv("WES_CALLBACK_URL", "http://localhost:8001/api/v1/callback/result")

# API 认证配置
API_APP_ID = os.getenv("API_APP_ID", "app_Gqnvr3dpjGwlrjtO")
API_APP_SECRET = os.getenv("API_APP_SECRET", "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao")

# 模拟执行时间（秒）
EXECUTION_TIME = float(os.getenv("EXECUTION_TIME", "2.0"))

# 自动执行配置
AUTO_EXECUTE_INTERVAL = int(os.getenv("AUTO_EXECUTE_INTERVAL", "5"))
BARCODE_PREFIX = os.getenv("BARCODE_PREFIX", "SMT-PKG")


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


def build_api_auth_headers(method: str, path: str) -> dict[str, str]:
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
    params: dict[str, Any]
    timestamp: int


class CancelRequest(BaseModel):
    """取消指令请求"""

    command_code: str


# ============================================
# 调试接口数据模型
# ============================================


class ManualExecuteRequest(BaseModel):
    """手动执行指令请求"""

    task_type: str | None = None  # 若不指定，使用设备默认任务类型
    source_loc: str | None = None  # 若不指定，使用设备默认源位置
    target_loc: str | None = None  # 若不指定，使用设备默认目标位置
    barcode: str | None = None
    simulate_failure: bool = False
    execution_time: float = EXECUTION_TIME


class AutoExecuteConfig(BaseModel):
    """自动执行配置"""

    interval_seconds: int = AUTO_EXECUTE_INTERVAL
    source_location: str | None = None  # 若不指定，使用设备默认源位置
    target_locations: list[str] | None = None  # 若不指定，使用设备默认目标位置列表
    barcode_prefix: str = BARCODE_PREFIX
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
    error_detail: JsonDict | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int


class DeviceStatusResponse(BaseModel):
    """设备状态响应"""

    device_id: str
    device_name: str
    device_type: str
    status: str
    is_online: bool
    current_command_code: str | None = None
    is_auto_executing: bool
    execution_count: int
    success_count: int
    failure_count: int
    timestamp: int


class AutoExecuteResolvedConfig(BaseModel):
    interval_seconds: int
    source_location: str
    target_locations: list[str]
    barcode_prefix: str
    max_executions: int | None = None


class AutoExecuteStartResponse(BaseModel):
    status: Literal["started"]
    device_id: str
    config: AutoExecuteResolvedConfig


class AutoExecuteStopResponse(BaseModel):
    status: Literal["stopped"]
    device_id: str
    execution_count: int


class ArmRootDeviceStatus(BaseModel):
    status: str
    is_online: bool
    current_command_code: str | None = None
    is_auto_executing: bool
    execution_count: int
    success_count: int
    failure_count: int


class ArmRootResponse(BaseModel):
    service: str
    version: str
    status: Literal["running"]
    device_id: str
    device_name: str
    description: str
    task_types: list[str]
    locations: DeviceLocations
    device_status: ArmRootDeviceStatus
    wes_callback_url: str


# Mock 设备状态
DEVICE_STATUS: DeviceRuntimeStatus = {
    "device_id": CURRENT_DEVICE["device_id"],
    "device_name": CURRENT_DEVICE["device_name"],
    "device_type": CURRENT_DEVICE["device_type"],
    "status": "IDLE",
    "is_online": True,
}


# ============================================
# 机械臂模拟器
# ============================================


class ArmSimulator:
    """机械臂模拟器

    模拟 SMT 粗分机机械臂执行指令并回调结果到 WES
    """

    def __init__(self, device_config: DeviceConfig):
        self.device_config = device_config
        self.device_id = device_config["device_id"]
        self.device_name = device_config["device_name"]
        self._counter = 0
        self._execution_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._executions: list[ExecutionRecord] = []
        self._is_auto_executing = False
        self._auto_task: asyncio.Task[None] | None = None
        self._auto_stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def _finalize_auto_execution_state(self) -> None:
        """在自动执行循环退出后收敛状态，避免任务自我等待。"""
        current_task = asyncio.current_task()
        async with self._lock:
            self._is_auto_executing = False
            if current_task is not None and self._auto_task is current_task:
                self._auto_task = None

    def _generate_command_code(self) -> str:
        """生成指令编码"""
        self._counter += 1
        return f"CMD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._counter:03d}"

    def _generate_barcode(self, prefix: str = BARCODE_PREFIX) -> str:
        """生成条码（格式：PREFIX + 日期 + 序号）"""
        today = datetime.now().strftime("%Y%m%d")
        return f"{prefix}{today}{self._counter:03d}"

    def _validate_task_type(self, task_type: str) -> bool:
        """验证任务类型是否支持"""
        return task_type in self.device_config["task_types"]

    def _validate_location(self, location: str, loc_type: str) -> bool:
        """验证位置是否有效

        Args:
            location: 位置 ID
            loc_type: "source" 或 "target"
        """
        valid_locations = self.device_config["locations"].get(loc_type, [])
        return location in valid_locations

    async def _callback_to_wes(
        self,
        command_code: str,
        result: str,
        source_loc: str | None = None,
        target_loc: str | None = None,
        barcode: str | None = None,
        error_detail: JsonDict | None = None,
    ) -> JsonDict:
        """回调执行结果到 WES

        POST /api/v1/callback/result（白皮书 3.2.1）
        """
        try:
            # 构建回调数据
            callback_data: JsonDict = {
                "command_code": command_code,
                "device_code": self.device_id,
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
            from urllib.parse import urlparse

            parsed_url = urlparse(WES_CALLBACK_URL)
            auth_headers = build_api_auth_headers("POST", parsed_url.path)

            logger.info(f"回调结果到 WES: device={self.device_id}, command_code={command_code}, result={result}")

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    WES_CALLBACK_URL,
                    json=callback_data,
                    headers=auth_headers,
                )
                _ = response.raise_for_status()
                result_data = cast("JsonDict", response.json())
                logger.info(f"WES 回调成功: {result_data}")
                return result_data

        except httpx.HTTPStatusError as e:
            # HTTP 错误
            status_code = e.response.status_code
            try:
                error_data = cast("JsonDict", e.response.json())
                error_msg = error_data.get("message", str(e))
            except Exception:
                error_msg = str(e)

            logger.error(f"WES 回调失败 [HTTP {status_code}]: command_code={command_code}, error={error_msg}")
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
        task_type: str | None = None,
        source_loc: str | None = None,
        target_loc: str | None = None,
        barcode: str | None = None,
        simulate_failure: bool = False,
        execution_time: float = EXECUTION_TIME,
    ) -> ExecutionRecord:
        """执行机械臂指令

        Args:
            task_type: 任务类型（若不指定，使用设备默认任务类型）
            source_loc: 源位置（若不指定，使用设备默认源位置）
            target_loc: 目标位置（若不指定，使用设备默认目标位置）
            barcode: 条码（可选，自动生成）
            simulate_failure: 是否模拟失败
            execution_time: 执行时间（秒）

        Returns:
            执行记录
        """
        async with self._lock:
            resolved_task_type = task_type or self.device_config["task_types"][0]
            resolved_source_loc = source_loc or self.device_config["default_source"]
            resolved_target_loc = target_loc or self.device_config["default_target"]

            # 验证任务类型
            if not self._validate_task_type(resolved_task_type):
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的任务类型: {resolved_task_type}，支持的任务类型: {self.device_config['task_types']}",
                )
            if not self._validate_location(resolved_source_loc, "source"):
                raise HTTPException(status_code=400, detail=f"无效的源位置: {resolved_source_loc}")
            if not self._validate_location(resolved_target_loc, "target"):
                raise HTTPException(status_code=400, detail=f"无效的目标位置: {resolved_target_loc}")

            # 生成 command_code
            command_code = self._generate_command_code()

            # 生成条码（如果未提供）
            if barcode is None and not simulate_failure:
                barcode = self._generate_barcode()

            # 记录开始时间
            started_at = datetime.now()

            logger.info(
                f"[{self.device_name}] 开始执行指令: command_code={command_code}, task_type={task_type}, "
                f"source={resolved_source_loc}, target={resolved_target_loc}, barcode={barcode}"
            )

            # 模拟执行延时
            await asyncio.sleep(execution_time)

            # 确定执行结果
            result = "FAILED" if simulate_failure else "SUCCESS"
            error_detail = {"code": "SIMULATED_FAILURE", "msg": "模拟失败"} if simulate_failure else None

            # 回调到 WES
            try:
                _ = await self._callback_to_wes(
                    command_code=command_code,
                    result=result,
                    source_loc=resolved_source_loc,
                    target_loc=resolved_target_loc,
                    barcode=barcode,
                    error_detail=error_detail,
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
                command_code=command_code,
                task_type=resolved_task_type,
                source_loc=resolved_source_loc,
                target_loc=resolved_target_loc,
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

            logger.info(
                f"[{self.device_name}] 指令执行完成: command_code={command_code}, result={result}, duration={duration_ms}ms"
            )

            return record

    async def _auto_execute_loop(
        self,
        interval_seconds: int,
        source_location: str,
        target_locations: list[str],
        barcode_prefix: str,
        max_executions: int | None,
    ) -> None:
        """自动执行循环"""
        execution_count = 0
        target_index = 0

        try:
            while not self._auto_stop_event.is_set():
                if max_executions is not None and execution_count >= max_executions:
                    logger.info(f"[{self.device_name}] 达到最大执行次数: {max_executions}")
                    break

                try:
                    target_loc = target_locations[target_index % len(target_locations)]
                    target_index += 1

                    _ = await self.execute_command(
                        task_type=self.device_config["task_types"][0],
                        source_loc=source_location,
                        target_loc=target_loc,
                        barcode=self._generate_barcode(barcode_prefix),
                    )
                    execution_count += 1

                except Exception as e:
                    logger.error(f"[{self.device_name}] 自动执行失败: {e}")

                try:
                    _ = await asyncio.wait_for(
                        self._auto_stop_event.wait(),
                        timeout=interval_seconds,
                    )
                    break
                except TimeoutError:  # noqa: S112
                    continue
        finally:
            await self._finalize_auto_execution_state()

    async def start_auto_execution(self, config: AutoExecuteConfig) -> AutoExecuteStartResponse:
        """启动自动执行

        Args:
            config: 自动执行配置

        Returns:
            启动结果
        """
        async with self._lock:
            if self._is_auto_executing:
                raise HTTPException(status_code=400, detail="自动执行已在运行中")

            # 使用设备默认值
            source_location = config.source_location or self.device_config["default_source"]
            target_locations = config.target_locations or self.device_config["locations"]["target"]

            # 重置停止事件
            self._auto_stop_event.clear()

            # 创建异步任务
            self._auto_task = asyncio.create_task(
                self._auto_execute_loop(
                    interval_seconds=config.interval_seconds,
                    source_location=source_location,
                    target_locations=target_locations,
                    barcode_prefix=config.barcode_prefix,
                    max_executions=config.max_executions,
                )
            )

            self._is_auto_executing = True

            logger.info(
                f"[{self.device_name}] 自动执行已启动: interval={config.interval_seconds}s, "
                f"source={source_location}, targets={target_locations}"
            )

            return AutoExecuteStartResponse(
                status="started",
                device_id=self.device_id,
                config=AutoExecuteResolvedConfig(
                    interval_seconds=config.interval_seconds,
                    source_location=source_location,
                    target_locations=target_locations,
                    barcode_prefix=config.barcode_prefix,
                    max_executions=config.max_executions,
                ),
            )

    async def stop_auto_execution(self) -> AutoExecuteStopResponse:
        """停止自动执行

        Returns:
            停止结果
        """
        current_task = asyncio.current_task()
        async with self._lock:
            if not self._is_auto_executing:
                raise HTTPException(status_code=400, detail="自动执行未运行")

            self._auto_stop_event.set()
            auto_task = self._auto_task

            self._is_auto_executing = False
            self._auto_task = None

        if auto_task and auto_task is not current_task:
            _ = auto_task.cancel()
            try:
                _ = await auto_task
            except asyncio.CancelledError:
                pass

        logger.info(f"[{self.device_name}] 自动执行已停止")

        return AutoExecuteStopResponse(
            status="stopped",
            device_id=self.device_id,
            execution_count=self._execution_count,
        )

    def get_status(self, current_command: JsonDict | None = None) -> DeviceStatusResponse:
        """获取设备状态"""
        return DeviceStatusResponse(
            device_id=self.device_id,
            device_name=self.device_name,
            device_type=self.device_config["device_type"],
            status=DEVICE_STATUS["status"],
            is_online=DEVICE_STATUS["is_online"],
            current_command_code=current_command["command_code"] if current_command else None,
            is_auto_executing=self._is_auto_executing,
            execution_count=self._execution_count,
            success_count=self._success_count,
            failure_count=self._failure_count,
            timestamp=int(datetime.now().timestamp() * 1000),
        )

    def get_executions(self, limit: int = 50) -> list[ExecutionRecord]:
        """获取执行记录"""
        return self._executions[-limit:]

    async def execute_wes_command(self, payload: DeviceCommandPayload) -> ExecutionRecord:
        """执行 WES 下发的指令

        Args:
            payload: WES 下发的指令 Payload

        Returns:
            执行记录
        """
        params = payload.params or {}
        task_type = payload.task_type
        source_loc = params.get("source_loc", self.device_config["default_source"])
        target_loc = params.get("target_loc", self.device_config["default_target"])
        barcode = params.get("barcode")

        # 验证任务类型
        if not self._validate_task_type(task_type):
            raise HTTPException(
                status_code=400,
                detail=f"不支持的任务类型: {task_type}，支持的任务类型: {self.device_config['task_types']}",
            )
        if not self._validate_location(source_loc, "source"):
            raise HTTPException(status_code=400, detail=f"无效的源位置: {source_loc}")
        if not self._validate_location(target_loc, "target"):
            raise HTTPException(status_code=400, detail=f"无效的目标位置: {target_loc}")

        async with self._lock:
            # 记录开始时间
            started_at = datetime.now()

            logger.info(
                f"[{self.device_name}] 开始执行 WES 指令: command_code={payload.command_code}, "
                f"task_type={task_type}, params={params}"
            )

            # 模拟执行时间
            await asyncio.sleep(EXECUTION_TIME)

            # 执行成功（WES 下发的指令默认成功）
            result = "SUCCESS"

            # 回调到 WES
            try:
                _ = await self._callback_to_wes(
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
                f"[{self.device_name}] WES 指令执行完成: command_code={payload.command_code}, "
                f"result={result}, duration={duration_ms}ms"
            )

            return record


# 全局模拟器实例
arm_simulator = ArmSimulator(device_config=CURRENT_DEVICE)

# 存储当前执行的指令（用于 WES 下发的指令跟踪）
current_command: JsonDict | None = None
current_command_task: asyncio.Task[None] | None = None


# ============================================
# FastAPI 应用
# ============================================

app = FastAPI(
    title=f"SMT 粗分机机械臂 Mock 服务 - {CURRENT_DEVICE['device_name']}",
    description=f"模拟 {CURRENT_DEVICE['device_name']}（{DEVICE_ID}）设备",
    version="1.0.0",
)


@app.get("/api/v1/device/status", response_model=DeviceStatusResponse)
async def get_device_status() -> DeviceStatusResponse:
    """
    设备状态查询接口（白皮书 3.1 节）

    返回设备在线状态和执行统计
    """
    return arm_simulator.get_status(current_command=current_command)


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
    global current_command, current_command_task

    logger.info(f"[{CURRENT_DEVICE['device_name']}] 收到指令: {payload.command_code}, task_type={payload.task_type}")

    # 验证设备状态
    if DEVICE_STATUS["status"] == "RUNNING":
        logger.warning(f"[{CURRENT_DEVICE['device_name']}] 设备忙，拒绝指令: {payload.command_code}")
        raise HTTPException(status_code=503, detail="Device Busy")

    # 验证任务类型
    if not arm_simulator._validate_task_type(payload.task_type):
        logger.warning(
            f"[{CURRENT_DEVICE['device_name']}] 不支持的任务类型: {payload.task_type}, "
            f"支持的任务类型: {CURRENT_DEVICE['task_types']}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"不支持的任务类型: {payload.task_type}，支持的任务类型: {CURRENT_DEVICE['task_types']}",
        )

    # 更新设备状态
    DEVICE_STATUS["status"] = "RUNNING"
    current_command = cast("JsonDict", payload.model_dump())

    # 生成追踪 ID
    trace_id = f"ARM-LOG-{payload.command_code.split('-')[-1]}"

    # 立即返回 ACK
    ack = DeviceCommandAck(code=200, message="Accepted", trace_id=trace_id)
    logger.info(f"[{CURRENT_DEVICE['device_name']}] 指令已接受: {payload.command_code}, trace_id={trace_id}")

    # 异步执行指令
    current_command_task = asyncio.create_task(_execute_wes_command_with_cleanup(payload))

    return ack


async def _execute_wes_command_with_cleanup(payload: DeviceCommandPayload) -> None:
    """执行 WES 指令并清理设备状态"""
    global current_command, current_command_task
    try:
        _ = await arm_simulator.execute_wes_command(payload)
    except asyncio.CancelledError:
        logger.info(f"[{CURRENT_DEVICE['device_name']}] 指令已中断: {payload.command_code}")
        raise
    finally:
        DEVICE_STATUS["status"] = "IDLE"
        current_command = None
        current_command_task = None


@app.post("/api/v1/device/cancel")
async def cancel_command(request: CancelRequest) -> DeviceCommandAck:
    """
    取消正在执行的指令（白皮书 3.1 节）
    """
    global current_command, current_command_task

    logger.info(f"[{CURRENT_DEVICE['device_name']}] 收到取消指令请求: {request.command_code}")

    if current_command is None:
        logger.warning(f"[{CURRENT_DEVICE['device_name']}] 没有正在执行的指令")
        raise HTTPException(status_code=404, detail="No Active Command")

    if current_command["command_code"] != request.command_code:
        logger.warning(f"[{CURRENT_DEVICE['device_name']}] 指令编码不匹配: {request.command_code}")
        raise HTTPException(status_code=404, detail="Command Not Found")

    task: asyncio.Task[None] | None = current_command_task
    if task is None:
        DEVICE_STATUS["status"] = "IDLE"
        current_command = None
        current_command_task = None
        raise HTTPException(status_code=409, detail="Command Already Finished")

    if task.done():
        DEVICE_STATUS["status"] = "IDLE"
        current_command = None
        current_command_task = None
        raise HTTPException(status_code=409, detail="Command Already Finished")

    _ = task.cancel()
    try:
        _ = await task
    except asyncio.CancelledError:
        pass

    logger.info(f"[{CURRENT_DEVICE['device_name']}] 指令已取消: {request.command_code}")

    return DeviceCommandAck(code=200, message="Cancelled")


# ============================================
# 调试接口
# ============================================


@app.post("/api/v1/arm/execute", summary="手动执行指令")
async def execute_arm_command(request: ManualExecuteRequest) -> ExecutionRecord:
    """
    手动执行机械臂指令（调试接口）

    **请求参数**：
    - task_type: 任务类型（可选，默认使用设备支持的任务类型）
    - source_loc: 源位置（可选，默认使用设备默认源位置）
    - target_loc: 目标位置（可选，默认使用设备默认目标位置）
    - barcode: 条码（可选，不提供则自动生成）
    - simulate_failure: 是否模拟失败（默认 false）
    - execution_time: 执行时间（秒，默认 2.0）

    **ARM01 支持的任务类型**：
    - PICK_FROM_POLE: 从串杆抓取
    - PLACE_TO_CONVEYOR: 放到流水线入口

    **ARM02 支持的任务类型**：
    - PICK_FROM_CONVEYOR: 从流水线出口抓取
    - PLACE_TO_BIN: 放入料箱

    **示例**：
    ```bash
    curl -X POST http://localhost:8006/api/v1/arm/execute \\
      -H "Content-Type: application/json" \\
      -d '{
        "task_type": "PICK_FROM_POLE",
        "source_loc": "POLE_A",
        "target_loc": "CONVEYOR_IN",
        "barcode": "SMT-PKG-001"
      }'
    ```
    """
    return await arm_simulator.execute_command(
        task_type=request.task_type,
        source_loc=request.source_loc,
        target_loc=request.target_loc,
        barcode=request.barcode,
        simulate_failure=request.simulate_failure,
        execution_time=request.execution_time,
    )


@app.post("/api/v1/arm/auto/start", summary="启动自动执行", response_model=AutoExecuteStartResponse)
async def start_auto_execute(config: AutoExecuteConfig) -> AutoExecuteStartResponse:
    """
    启动机械臂自动执行（调试接口）

    **请求参数**：
    - interval_seconds: 执行间隔（秒，默认 5）
    - source_location: 源位置（可选，默认使用设备默认源位置）
    - target_locations: 目标位置列表（可选，默认使用设备默认目标位置列表）
    - barcode_prefix: 条码前缀（默认 SMT-PKG）
    - max_executions: 最大执行次数（可选，不限制则为 None）

    **流程**：
    1. 创建后台定时任务
    2. 按间隔执行机械臂指令
    3. 轮询目标位置
    4. 达到最大次数后自动停止

    **示例**：
    ```bash
    curl -X POST http://localhost:8006/api/v1/arm/auto/start \\
      -H "Content-Type: application/json" \\
      -d '{
        "interval_seconds": 5,
        "max_executions": 3
      }'
    ```
    """
    return await arm_simulator.start_auto_execution(config)


@app.post("/api/v1/arm/auto/stop", summary="停止自动执行", response_model=AutoExecuteStopResponse)
async def stop_auto_execute() -> AutoExecuteStopResponse:
    """
    停止机械臂自动执行（调试接口）

    **示例**：
    ```bash
    curl -X POST http://localhost:8006/api/v1/arm/auto/stop
    ```
    """
    return await arm_simulator.stop_auto_execution()


@app.get("/api/v1/arm/executions", summary="执行记录")
async def get_arm_executions(limit: int = 50) -> list[ExecutionRecord]:
    """
    获取机械臂执行记录（调试接口）

    **参数**：
    - limit: 返回记录数量（默认 50）

    **示例**：
    ```bash
    curl http://localhost:8006/api/v1/arm/executions?limit=10
    ```
    """
    return arm_simulator.get_executions(limit)


@app.get("/", response_model=ArmRootResponse)
async def root() -> ArmRootResponse:
    """根路径健康检查"""
    return ArmRootResponse(
        service=f"SMT 粗分机机械臂 Mock 服务 - {CURRENT_DEVICE['device_name']}",
        version="1.0.0",
        status="running",
        device_id=CURRENT_DEVICE["device_id"],
        device_name=CURRENT_DEVICE["device_name"],
        description=CURRENT_DEVICE["description"],
        task_types=CURRENT_DEVICE["task_types"],
        locations=CURRENT_DEVICE["locations"],
        device_status=ArmRootDeviceStatus(
            status=DEVICE_STATUS["status"],
            is_online=DEVICE_STATUS["is_online"],
            current_command_code=current_command["command_code"] if current_command else None,
            is_auto_executing=arm_simulator._is_auto_executing,
            execution_count=arm_simulator._execution_count,
            success_count=arm_simulator._success_count,
            failure_count=arm_simulator._failure_count,
        ),
        wes_callback_url=WES_CALLBACK_URL,
    )


# ============================================
# 服务器类
# ============================================


class SmtArmMockServer:
    """SMT 粗分机机械臂 Mock 服务器"""

    def __init__(self, device_id: str = DEVICE_ID, host: str = "127.0.0.1", port: int | None = None):
        self.device_id = device_id
        self.host = host
        self.port = port or DEVICE_CONFIGS[device_id]["port"]
        self._server: Server | None = None
        self.config = Config(app=app, host=host, port=self.port, log_level="info")

    async def start(self) -> None:
        """启动服务器（阻塞运行）"""
        device_config = DEVICE_CONFIGS[self.device_id]
        logger.info(f"SMT 粗分机机械臂 Mock 服务启动: http://{self.host}:{self.port}")
        logger.info(f"设备 ID: {self.device_id}")
        logger.info(f"设备名称: {device_config['device_name']}")
        logger.info(f"设备描述: {device_config['description']}")
        logger.info(f"支持的任务类型: {device_config['task_types']}")
        logger.info(f"源位置: {device_config['locations']['source']}")
        logger.info(f"目标位置: {device_config['locations']['target']}")
        logger.info(f"WES 回调地址: {WES_CALLBACK_URL}")
        logger.info("设备接口:")
        logger.info("  - GET  /api/v1/device/status")
        logger.info("  - POST /api/v1/device/command")
        logger.info("  - POST /api/v1/device/cancel")
        logger.info("调试接口:")
        logger.info("  - POST /api/v1/arm/execute")
        logger.info("  - POST /api/v1/arm/auto/start")
        logger.info("  - POST /api/v1/arm/auto/stop")
        logger.info("  - GET  /api/v1/arm/executions")

        self._server = Server(self.config)
        await self._server.serve()

    def run(self):
        """同步运行服务器"""
        asyncio.run(self.start())


# ============================================
# 直接运行时的入口
# ============================================

if __name__ == "__main__":
    server = SmtArmMockServer(device_id=DEVICE_ID, port=DEVICE_PORT)
    server.run()
