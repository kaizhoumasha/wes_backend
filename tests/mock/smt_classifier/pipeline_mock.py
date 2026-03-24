"""
SMT 粗分机流水线 Mock 服务

模拟 SMT 粗分机流水线设备，用于 E2E 测试。

功能：
- 监听 127.0.0.1:8005
- 扫码事件触发 POST /api/v1/pipeline/scan
- 检测事件触发 POST /api/v1/pipeline/detect
- 测厚事件触发 POST /api/v1/pipeline/thickness
- 自动触发模式：定时触发完整流程
- 上报事件到 WES 回调接口

运行方式：
    python tests/mock/smt_classifier/pipeline_mock.py
    或
    uv run python tests/mock/smt_classifier/pipeline_mock.py
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
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
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

# WES 事件回调地址（使用环境变量，默认 localhost:8001）
WES_EVENT_CALLBACK_URL = os.getenv("WES_EVENT_CALLBACK_URL", "http://localhost:8001/api/v1/callback/event")

# API 认证配置（设备调用 WES 回调接口时使用）
API_APP_ID = os.getenv("API_APP_ID", "app_Gqnvr3dpjGwlrjtO")
API_APP_SECRET = os.getenv("API_APP_SECRET", "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao")

# 流水线配置
PIPELINE_AUTO_TRIGGER_DEFAULT_INTERVAL = int(os.getenv("PIPELINE_AUTO_TRIGGER_DEFAULT_INTERVAL", "10"))
PIPELINE_BARCODE_PREFIX = os.getenv("PIPELINE_BARCODE_PREFIX", "PKG")


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
        path: 请求路径（如 "/api/v1/callback/event"）

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

# 事件类型枚举
class EventType(str):
    """事件类型"""

    SCAN_OK = "SCAN_OK"
    SCAN_NG = "SCAN_NG"
    DETECT_OK = "DETECT_OK"
    DETECT_NG = "DETECT_NG"
    THICKNESS_OK = "THICKNESS_OK"
    THICKNESS_NG = "THICKNESS_NG"


# 位置 ID 枚举
class LocationID(str):
    """位置 ID"""

    SCAN = "LEFT_STATION_SCAN"
    DETECT = "LEFT_STATION_DETECT"
    THICKNESS = "LEFT_STATION_THICKNESS"


class Dimensions(BaseModel):
    """尺寸信息（长宽高）"""

    length: float = Field(..., description="长度 (mm)")
    width: float = Field(..., description="宽度 (mm)")
    height: float = Field(..., description="高度 (mm)")


class ScanRequest(BaseModel):
    """扫码事件触发请求"""

    barcode: str = Field(..., description="条码")
    result: str = Field(default="OK", description="结果: OK 或 NG")


class DetectRequest(BaseModel):
    """检测事件触发请求"""

    barcode: str = Field(..., description="条码")
    result: str = Field(default="OK", description="结果: OK 或 NG")
    dimensions: Dimensions = Field(..., description="尺寸信息（长宽高）")


class ThicknessRequest(BaseModel):
    """测厚事件触发请求"""

    barcode: str = Field(..., description="条码")
    result: str = Field(default="OK", description="结果: OK 或 NG")
    thickness_mm: float = Field(..., description="厚度 (mm)")


class AutoTriggerConfig(BaseModel):
    """自动触发配置"""

    interval_seconds: int = Field(default=PIPELINE_AUTO_TRIGGER_DEFAULT_INTERVAL, description="触发间隔（秒）")
    barcode_prefix: str = Field(default=PIPELINE_BARCODE_PREFIX, description="条码前缀")
    max_triggers: int | None = Field(default=None, description="最大触发次数（可选）")


class EventRecord(BaseModel):
    """事件记录"""

    event_id: str
    device_code: str
    event_type: str
    barcode: str
    location: str
    result: str
    timestamp: int
    data: dict[str, Any]
    reported_at: datetime


class PipelineStatusResponse(BaseModel):
    """流水线状态响应"""

    is_auto_triggering: bool
    event_count: int
    scan_count: int
    detect_count: int
    thickness_count: int
    current_config: dict[str, Any] | None = None


# Mock 设备信息
DEVICE_INFO = {
    "device_id": "PIPELINE01",
    "device_name": "SMT 粗分机流水线",
    "device_type": "PIPELINE",
    "status": "ONLINE",
    "is_online": True,
}


# ============================================
# 流水线模拟器
# ============================================


class PipelineSimulator:
    """流水线模拟器

    模拟 SMT 粗分机流水线的扫码、检测、测厚流程，并上报事件到 WES
    """

    def __init__(self, device_code: str = "PIPELINE01"):
        self.device_code = device_code
        self._counter = 0
        self._event_count = 0
        self._scan_count = 0
        self._detect_count = 0
        self._thickness_count = 0
        self._events: list[EventRecord] = []
        self._is_auto_triggering = False
        self._auto_trigger_task: asyncio.Task | None = None
        self._auto_trigger_stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def _finalize_auto_trigger_state(self) -> None:
        """在自动触发循环退出后收敛状态，避免任务自我等待。"""
        async with self._lock:
            self._is_auto_triggering = False
            if self._auto_trigger_task is asyncio.current_task():
                self._auto_trigger_task = None

    def _generate_barcode(self, prefix: str = PIPELINE_BARCODE_PREFIX) -> str:
        """生成条码（格式：PREFIX + 日期 + 序号）"""
        today = datetime.now().strftime("%Y%m%d")
        barcode = f"{prefix}{today}{self._counter:03d}"
        self._counter += 1
        return barcode

    def _generate_event_id(self, event_type: str) -> str:
        """生成事件 ID"""
        event_num = self._event_count + 1
        return f"EVT-{event_type}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{event_num:03d}"

    async def _report_event_to_wes(self, event_data: dict) -> dict:
        """上报事件到 WES

        POST /api/v1/callback/event
        """
        try:
            # 构建 API 认证 Header
            from urllib.parse import urlparse

            parsed_url = urlparse(WES_EVENT_CALLBACK_URL)
            auth_headers = build_api_auth_headers("POST", parsed_url.path)

            logger.info(
                f"上报事件到 WES: device_code={event_data['device_code']}, "
                f"event_type={event_data['event_type']}, app_id={API_APP_ID}"
            )

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    WES_EVENT_CALLBACK_URL,
                    json=event_data,
                    headers=auth_headers,
                )
                response.raise_for_status()
                result = response.json()
                logger.info(f"WES 事件上报成功: {result}")
                return result

        except httpx.HTTPStatusError as e:
            # HTTP 错误（401, 403, 500 等）
            status_code = e.response.status_code
            try:
                error_data = e.response.json()
                if "detail" in error_data:
                    error_msg = error_data.get("message", str(error_data.get("detail", str(e))))
                    error_detail = error_data.get("detail")
                else:
                    error_msg = error_data.get("message", str(e))
                    error_detail = None
            except Exception:
                error_msg = str(e)
                error_detail = None

            logger.error(f"WES 事件上报失败 [HTTP {status_code}]: {error_msg}")
            if error_detail:
                logger.error(f"WES 详细错误: {error_detail}")

            raise HTTPException(
                status_code=500,
                detail={
                    "error": "wes_callback_failed",
                    "message": f"WES 回调失败 (HTTP {status_code})",
                    "wes_error": error_msg,
                    "wes_detail": error_detail,
                    "app_id": API_APP_ID,
                    "callback_url": WES_EVENT_CALLBACK_URL,
                },
            ) from e
        except httpx.RequestError as e:
            # 网络错误
            logger.error(f"WES 事件上报失败 [网络错误]: {e}")
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "wes_unreachable",
                    "message": "无法连接到 WES 服务器",
                    "wes_url": WES_EVENT_CALLBACK_URL,
                },
            ) from e
        except Exception as e:
            logger.error(f"WES 事件上报失败: {e}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "internal_error",
                    "message": "事件上报内部错误",
                    "details": str(e),
                },
            ) from e

    async def trigger_scan(
        self,
        barcode: str,
        result: str = "OK",
    ) -> EventRecord:
        """触发扫码事件

        Args:
            barcode: 条码
            result: 结果（OK 或 NG）

        Returns:
            事件记录
        """
        async with self._lock:
            # 确定事件类型
            event_type = EventType.SCAN_OK if result.upper() == "OK" else EventType.SCAN_NG

            # 生成事件 ID
            event_id = self._generate_event_id(event_type)

            # 构建事件数据
            event_data = {
                "device_code": self.device_code,
                "event_type": event_type,
                "timestamp": int(datetime.now().timestamp() * 1000),
                "data": {
                    "barcode": barcode,
                    "location": LocationID.SCAN,
                    "result": result.upper(),
                },
            }

            # 上报事件到 WES
            await self._report_event_to_wes(event_data)

            # 记录事件
            event_record = EventRecord(
                event_id=event_id,
                device_code=self.device_code,
                event_type=event_type,
                barcode=barcode,
                location=LocationID.SCAN,
                result=result.upper(),
                timestamp=event_data["timestamp"],
                data=event_data["data"],
                reported_at=datetime.now(),
            )
            self._events.append(event_record)
            self._event_count += 1
            self._scan_count += 1

            logger.info(f"扫码事件已触发: barcode={barcode}, result={result}, event_id={event_id}")

            return event_record

    async def trigger_detect(
        self,
        barcode: str,
        result: str = "OK",
        dimensions: Dimensions | None = None,
    ) -> EventRecord:
        """触发检测事件

        Args:
            barcode: 条码
            result: 结果（OK 或 NG）
            dimensions: 尺寸信息（长宽高）

        Returns:
            事件记录
        """
        async with self._lock:
            # 确定事件类型
            event_type = EventType.DETECT_OK if result.upper() == "OK" else EventType.DETECT_NG

            # 生成事件 ID
            event_id = self._generate_event_id(event_type)

            # 默认尺寸
            if dimensions is None:
                dimensions = Dimensions(length=100.0, width=50.0, height=15.0)

            # 构建事件数据
            event_data = {
                "device_code": self.device_code,
                "event_type": event_type,
                "timestamp": int(datetime.now().timestamp() * 1000),
                "data": {
                    "barcode": barcode,
                    "location": LocationID.DETECT,
                    "result": result.upper(),
                    "dimensions": {
                        "length": dimensions.length,
                        "width": dimensions.width,
                        "height": dimensions.height,
                    },
                },
            }

            # 上报事件到 WES
            await self._report_event_to_wes(event_data)

            # 记录事件
            event_record = EventRecord(
                event_id=event_id,
                device_code=self.device_code,
                event_type=event_type,
                barcode=barcode,
                location=LocationID.DETECT,
                result=result.upper(),
                timestamp=event_data["timestamp"],
                data=event_data["data"],
                reported_at=datetime.now(),
            )
            self._events.append(event_record)
            self._event_count += 1
            self._detect_count += 1

            logger.info(
                f"检测事件已触发: barcode={barcode}, result={result}, "
                f"dimensions=({dimensions.length}, {dimensions.width}, {dimensions.height}), event_id={event_id}"
            )

            return event_record

    async def trigger_thickness(
        self,
        barcode: str,
        result: str = "OK",
        thickness_mm: float = 15.0,
    ) -> EventRecord:
        """触发测厚事件

        Args:
            barcode: 条码
            result: 结果（OK 或 NG）
            thickness_mm: 厚度（mm）

        Returns:
            事件记录
        """
        async with self._lock:
            # 确定事件类型
            event_type = EventType.THICKNESS_OK if result.upper() == "OK" else EventType.THICKNESS_NG

            # 生成事件 ID
            event_id = self._generate_event_id(event_type)

            # 构建事件数据
            event_data = {
                "device_code": self.device_code,
                "event_type": event_type,
                "timestamp": int(datetime.now().timestamp() * 1000),
                "data": {
                    "barcode": barcode,
                    "location": LocationID.THICKNESS,
                    "result": result.upper(),
                    "thickness_mm": thickness_mm,
                },
            }

            # 上报事件到 WES
            await self._report_event_to_wes(event_data)

            # 记录事件
            event_record = EventRecord(
                event_id=event_id,
                device_code=self.device_code,
                event_type=event_type,
                barcode=barcode,
                location=LocationID.THICKNESS,
                result=result.upper(),
                timestamp=event_data["timestamp"],
                data=event_data["data"],
                reported_at=datetime.now(),
            )
            self._events.append(event_record)
            self._event_count += 1
            self._thickness_count += 1

            logger.info(
                f"测厚事件已触发: barcode={barcode}, result={result}, "
                f"thickness_mm={thickness_mm}, event_id={event_id}"
            )

            return event_record

    async def trigger_full_flow(self, barcode: str) -> list[EventRecord]:
        """触发完整流程：扫码 -> 检测 -> 测厚

        Args:
            barcode: 条码

        Returns:
            事件记录列表
        """
        records = []

        # 1. 扫码
        scan_record = await self.trigger_scan(barcode, result="OK")
        records.append(scan_record)

        # 短暂延时
        await asyncio.sleep(0.5)

        # 2. 检测
        detect_record = await self.trigger_detect(
            barcode,
            result="OK",
            dimensions=Dimensions(length=100.0 + self._counter, width=50.0 + self._counter, height=15.0),
        )
        records.append(detect_record)

        # 短暂延时
        await asyncio.sleep(0.5)

        # 3. 测厚
        thickness_record = await self.trigger_thickness(
            barcode,
            result="OK",
            thickness_mm=15.0 + (self._counter * 0.1),
        )
        records.append(thickness_record)

        logger.info(f"完整流程已触发: barcode={barcode}, events={len(records)}")

        return records

    async def _auto_trigger_loop(
        self,
        interval_seconds: int,
        barcode_prefix: str,
        max_triggers: int | None,
    ):
        """自动触发循环"""
        trigger_count = 0

        try:
            while not self._auto_trigger_stop_event.is_set():
                if max_triggers is not None and trigger_count >= max_triggers:
                    logger.info(f"达到最大触发次数: {max_triggers}")
                    break

                try:
                    barcode = self._generate_barcode(barcode_prefix)
                    await self.trigger_full_flow(barcode)
                    trigger_count += 1

                except Exception as e:
                    logger.error(f"自动触发失败: {e}")

                try:
                    await asyncio.wait_for(
                        self._auto_trigger_stop_event.wait(),
                        timeout=interval_seconds,
                    )
                    break
                except TimeoutError:  # noqa: S112
                    continue
        finally:
            await self._finalize_auto_trigger_state()

    async def start_auto_trigger(self, config: AutoTriggerConfig) -> dict:
        """启动自动触发

        Args:
            config: 自动触发配置

        Returns:
            启动结果
        """
        async with self._lock:
            if self._is_auto_triggering:
                raise HTTPException(status_code=400, detail="自动触发已在运行中")

            # 重置停止事件
            self._auto_trigger_stop_event.clear()

            # 创建异步任务
            self._auto_trigger_task = asyncio.create_task(
                self._auto_trigger_loop(
                    interval_seconds=config.interval_seconds,
                    barcode_prefix=config.barcode_prefix,
                    max_triggers=config.max_triggers,
                )
            )

            self._is_auto_triggering = True

            logger.info(
                f"自动触发已启动: interval={config.interval_seconds}s, "
                f"prefix={config.barcode_prefix}"
            )

            return {
                "status": "started",
                "config": config.model_dump(),
            }

    async def stop_auto_trigger(self) -> dict:
        """停止自动触发

        Returns:
            停止结果
        """
        current_task = asyncio.current_task()
        async with self._lock:
            if not self._is_auto_triggering:
                raise HTTPException(status_code=400, detail="自动触发未运行")

            self._auto_trigger_stop_event.set()
            auto_task = self._auto_trigger_task

            self._is_auto_triggering = False
            self._auto_trigger_task = None

        if auto_task and auto_task is not current_task:
            auto_task.cancel()
            try:
                await auto_task
            except asyncio.CancelledError:
                pass

        logger.info("自动触发已停止")

        return {
            "status": "stopped",
            "event_count": self._event_count,
        }

    def get_status(self) -> PipelineStatusResponse:
        """获取流水线状态"""
        return PipelineStatusResponse(
            is_auto_triggering=self._is_auto_triggering,
            event_count=self._event_count,
            scan_count=self._scan_count,
            detect_count=self._detect_count,
            thickness_count=self._thickness_count,
            current_config={
                "wes_callback_url": WES_EVENT_CALLBACK_URL,
                "default_interval": PIPELINE_AUTO_TRIGGER_DEFAULT_INTERVAL,
                "barcode_prefix": PIPELINE_BARCODE_PREFIX,
            },
        )

    def get_events(self, limit: int = 50) -> list[EventRecord]:
        """获取事件记录"""
        return self._events[-limit:]


# 全局流水线模拟器实例
pipeline_simulator = PipelineSimulator(device_code=DEVICE_INFO["device_id"])


# ============================================
# FastAPI 应用
# ============================================

app = FastAPI(
    title="SMT 粗分机流水线 Mock 服务",
    description="模拟 SMT 粗分机流水线设备（扫码/检测/测厚）",
    version="1.0.0",
)


@app.get("/api/v1/device/status")
async def get_status():
    """
    设备状态查询接口

    返回流水线设备当前状态
    """
    return {
        "device_id": DEVICE_INFO["device_id"],
        "device_name": DEVICE_INFO["device_name"],
        "device_type": DEVICE_INFO["device_type"],
        "status": DEVICE_INFO["status"],
        "is_online": DEVICE_INFO["is_online"],
        "timestamp": int(datetime.now().timestamp() * 1000),
    }


@app.post("/api/v1/pipeline/scan", summary="触发扫码事件")
async def trigger_scan(request: ScanRequest) -> EventRecord:
    """
    手动触发扫码事件

    **请求参数**：
    - barcode: 条码（必填）
    - result: 结果（OK 或 NG，默认 OK）

    **流程**：
    1. 构建扫码事件（SCAN_OK 或 SCAN_NG）
    2. 上报事件到 WES
    3. 返回事件记录

    **示例**：
    ```bash
    curl -X POST http://localhost:8005/api/v1/pipeline/scan \\
      -H "Content-Type: application/json" \\
      -d '{"barcode": "PKG-TEST-001", "result": "OK"}'
    ```
    """
    return await pipeline_simulator.trigger_scan(
        barcode=request.barcode,
        result=request.result,
    )


@app.post("/api/v1/pipeline/detect", summary="触发检测事件")
async def trigger_detect(request: DetectRequest) -> EventRecord:
    """
    手动触发检测事件

    **请求参数**：
    - barcode: 条码（必填）
    - result: 结果（OK 或 NG，默认 OK）
    - dimensions: 尺寸信息（长宽高，必填）

    **流程**：
    1. 构建检测事件（DETECT_OK 或 DETECT_NG）
    2. 上报事件到 WES
    3. 返回事件记录

    **示例**：
    ```bash
    curl -X POST http://localhost:8005/api/v1/pipeline/detect \\
      -H "Content-Type: application/json" \\
      -d '{
        "barcode": "PKG-TEST-001",
        "result": "OK",
        "dimensions": {"length": 100, "width": 50, "height": 15}
      }'
    ```
    """
    return await pipeline_simulator.trigger_detect(
        barcode=request.barcode,
        result=request.result,
        dimensions=request.dimensions,
    )


@app.post("/api/v1/pipeline/thickness", summary="触发测厚事件")
async def trigger_thickness(request: ThicknessRequest) -> EventRecord:
    """
    手动触发测厚事件

    **请求参数**：
    - barcode: 条码（必填）
    - result: 结果（OK 或 NG，默认 OK）
    - thickness_mm: 厚度（mm，必填）

    **流程**：
    1. 构建测厚事件（THICKNESS_OK 或 THICKNESS_NG）
    2. 上报事件到 WES
    3. 返回事件记录

    **示例**：
    ```bash
    curl -X POST http://localhost:8005/api/v1/pipeline/thickness \\
      -H "Content-Type: application/json" \\
      -d '{"barcode": "PKG-TEST-001", "result": "OK", "thickness_mm": 15.5}'
    ```
    """
    return await pipeline_simulator.trigger_thickness(
        barcode=request.barcode,
        result=request.result,
        thickness_mm=request.thickness_mm,
    )


@app.post("/api/v1/pipeline/auto/start", summary="启动自动触发")
async def start_auto_trigger(config: AutoTriggerConfig) -> dict:
    """
    启动自动触发模式

    **请求参数**：
    - interval_seconds: 触发间隔（秒，默认 10）
    - barcode_prefix: 条码前缀（默认 PKG）
    - max_triggers: 最大触发次数（可选，不限制则为 None）

    **流程**：
    1. 创建后台定时任务
    2. 按间隔触发完整流程（扫码 -> 检测 -> 测厚）
    3. 达到最大次数后自动停止

    **示例**：
    ```bash
    curl -X POST http://localhost:8005/api/v1/pipeline/auto/start \\
      -H "Content-Type: application/json" \\
      -d '{"interval_seconds": 5, "max_triggers": 3}'
    ```
    """
    return await pipeline_simulator.start_auto_trigger(config)


@app.post("/api/v1/pipeline/auto/stop", summary="停止自动触发")
async def stop_auto_trigger() -> dict:
    """
    停止自动触发模式

    **示例**：
    ```bash
    curl -X POST http://localhost:8005/api/v1/pipeline/auto/stop
    ```
    """
    return await pipeline_simulator.stop_auto_trigger()


@app.get("/api/v1/pipeline/status", summary="流水线状态", response_model=PipelineStatusResponse)
async def get_pipeline_status() -> PipelineStatusResponse:
    """
    获取流水线状态

    **返回**：
    - is_auto_triggering: 是否正在自动触发
    - event_count: 总事件数
    - scan_count: 扫码事件数
    - detect_count: 检测事件数
    - thickness_count: 测厚事件数
    - current_config: 当前配置

    **示例**：
    ```bash
    curl http://localhost:8005/api/v1/pipeline/status
    ```
    """
    return pipeline_simulator.get_status()


@app.get("/api/v1/pipeline/events", summary="事件记录")
async def get_pipeline_events(limit: int = 50) -> list[EventRecord]:
    """
    获取流水线事件记录

    **参数**：
    - limit: 返回记录数量（默认 50）

    **示例**：
    ```bash
    curl http://localhost:8005/api/v1/pipeline/events?limit=10
    ```
    """
    return pipeline_simulator.get_events(limit)


@app.get("/")
async def root():
    """根路径健康检查"""
    return {
        "service": "SMT 粗分机流水线 Mock 服务",
        "version": "1.0.0",
        "status": "running",
        "device_id": DEVICE_INFO["device_id"],
        "pipeline": {
            "is_auto_triggering": pipeline_simulator._is_auto_triggering,
            "event_count": pipeline_simulator._event_count,
            "scan_count": pipeline_simulator._scan_count,
            "detect_count": pipeline_simulator._detect_count,
            "thickness_count": pipeline_simulator._thickness_count,
        },
    }


# ============================================
# 服务器类
# ============================================


class PipelineMockServer:
    """流水线 Mock 服务器"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8005):
        self.host = host
        self.port = port
        self._server: Server | None = None
        self.config = Config(app=app, host=host, port=port, log_level="info")

    async def start(self) -> None:
        """启动服务器（阻塞运行）"""
        logger.info(f"SMT 粗分机流水线 Mock 服务启动: http://{self.host}:{self.port}")
        logger.info(f"模拟设备: {DEVICE_INFO}")
        logger.info(f"WES 事件回调地址: {WES_EVENT_CALLBACK_URL}")
        logger.info("流水线 API 端点:")
        logger.info("  - GET  /api/v1/device/status")
        logger.info("  - POST /api/v1/pipeline/scan")
        logger.info("  - POST /api/v1/pipeline/detect")
        logger.info("  - POST /api/v1/pipeline/thickness")
        logger.info("  - POST /api/v1/pipeline/auto/start")
        logger.info("  - POST /api/v1/pipeline/auto/stop")
        logger.info("  - GET  /api/v1/pipeline/status")
        logger.info("  - GET  /api/v1/pipeline/events")

        self._server = Server(self.config)
        await self._server.serve()

    def run(self):
        """同步运行服务器"""
        asyncio.run(self.start())


# ============================================
# 直接运行时的入口
# ============================================

if __name__ == "__main__":
    server = PipelineMockServer()
    server.run()
