"""
摄像头 Mock 服务

模拟流水线识别点摄像头设备，用于 E2E 测试。

功能：
- 监听 127.0.0.1:8003
- 接收状态查询请求 GET /api/v1/device/status
- 返回设备在线状态
- 传感器模拟：手动/自动触发物料到达事件
- 上报事件到 WES 回调接口

运行方式：
    python tests/mock/camera_mock_server.py
    或
    uv run python tests/mock/camera_mock_server.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
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

# WES 事件回调地址（使用环境变量，默认 localhost:8001）
WES_EVENT_CALLBACK_URL = os.getenv("WES_EVENT_CALLBACK_URL", "http://localhost:8001/api/v1/callback/event")

# 传感器配置
SENSOR_AUTO_TRIGGER_DEFAULT_INTERVAL = int(os.getenv("SENSOR_AUTO_TRIGGER_DEFAULT_INTERVAL", "10"))
SENSOR_BARCODE_PREFIX = os.getenv("SENSOR_BARCODE_PREFIX", "PKG")


# ============================================
# 数据模型
# ============================================


class SensorTriggerRequest(BaseModel):
    """传感器触发请求"""

    barcode: str | None = None
    location: str = "CONVEYOR-STATION-01"
    simulate_scan: bool = True


class AutoTriggerConfig(BaseModel):
    """自动触发配置"""

    interval_seconds: int = SENSOR_AUTO_TRIGGER_DEFAULT_INTERVAL
    barcode_prefix: str = SENSOR_BARCODE_PREFIX
    location: str = "CONVEYOR-STATION-01"
    max_triggers: int | None = None


class SensorStatusResponse(BaseModel):
    """传感器状态响应"""

    is_auto_triggering: bool
    trigger_count: int
    current_config: dict | None = None


class EventRecord(BaseModel):
    """事件记录"""

    event_id: str
    device_id: str
    event_type: str
    barcode: str | None = None
    location: str | None = None
    timestamp: int
    reported_at: datetime


# Mock 设备信息
DEVICE_INFO = {
    "device_id": "CAMERA-CONVEYOR-01",
    "device_name": "流水线识别点摄像头",
    "device_type": "CAMERA",
    "status": "IDLE",
    "is_online": True,
}


# ============================================
# 传感器模拟器
# ============================================


class SensorSimulator:
    """传感器模拟器

    模拟摄像头传感器检测物料到达并上报事件到 WES
    """

    def __init__(self, device_id: str = "CAMERA-CONVEYOR-01"):
        self.device_id = device_id
        self._counter = 0
        self._trigger_count = 0
        self._events: list[EventRecord] = []
        self._is_auto_triggering = False
        self._auto_trigger_task: asyncio.Task | None = None
        self._auto_trigger_stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    def _generate_barcode(self, prefix: str = SENSOR_BARCODE_PREFIX) -> str:
        """生成条码（格式：PREFIX + 日期 + 序号）"""
        today = datetime.now().strftime("%Y%m%d")
        barcode = f"{prefix}{today}{self._counter:03d}"
        self._counter += 1
        return barcode

    async def _report_event_to_wes(self, event_data: dict) -> dict:
        """上报事件到 WES

        POST /api/v1/callback/event（白皮书 3.2.2）
        """
        try:
            logger.info(f"上报事件到 WES: device_id={event_data['device_id']}, event_type={event_data['event_type']}")

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    WES_EVENT_CALLBACK_URL,
                    json=event_data,
                )
                response.raise_for_status()
                result = response.json()
                logger.info(f"WES 事件上报成功: {result}")
                return result

        except Exception as e:
            logger.error(f"WES 事件上报失败: {e}")
            raise

    async def trigger_material_arrival(
        self,
        barcode: str | None = None,
        location: str = "CONVEYOR-STATION-01",
    ) -> EventRecord:
        """触发物料到达事件

        Args:
            barcode: 条码（可选，自动生成）
            location: 位置

        Returns:
            事件记录
        """
        async with self._lock:
            # 生成条码
            if barcode is None:
                barcode = self._generate_barcode()

            # 生成事件 ID
            event_id = f"EVT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._trigger_count:03d}"

            # 构建事件数据（白皮书 3.2.2）
            event_data = {
                "device_id": self.device_id,
                "event_type": "MATERIAL_ARRIVED",
                "timestamp": int(datetime.now().timestamp() * 1000),
                "data": {
                    "location": location,
                    "barcode": barcode,
                },
            }

            # 上报事件到 WES
            await self._report_event_to_wes(event_data)

            # 记录事件
            event_record = EventRecord(
                event_id=event_id,
                device_id=self.device_id,
                event_type="MATERIAL_ARRIVED",
                barcode=barcode,
                location=location,
                timestamp=event_data["timestamp"],
                reported_at=datetime.now(),
            )
            self._events.append(event_record)
            self._trigger_count += 1

            logger.info(f"物料到达事件已触发: barcode={barcode}, location={location}, event_id={event_id}")

            return event_record

    async def _auto_trigger_loop(
        self,
        interval_seconds: int,
        barcode_prefix: str,
        location: str,
        max_triggers: int | None,
    ):
        """自动触发循环"""
        trigger_count = 0

        while not self._auto_trigger_stop_event.is_set():
            # 检查是否达到最大触发次数
            if max_triggers is not None and trigger_count >= max_triggers:
                logger.info(f"达到最大触发次数: {max_triggers}")
                break

            try:
                # 触发事件
                await self.trigger_material_arrival(
                    barcode=self._generate_barcode(barcode_prefix),
                    location=location,
                )
                trigger_count += 1

            except Exception as e:
                logger.error(f"自动触发失败: {e}")

            # 等待下一次触发或停止信号
            try:
                await asyncio.wait_for(
                    self._auto_trigger_stop_event.wait(),
                    timeout=interval_seconds,
                )
                break  # 收到停止信号
            except TimeoutError:  # noqa: S112
                continue  # 超时，继续下一次触发

        # 自动停止
        await self.stop_auto_trigger()

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
                    location=config.location,
                    max_triggers=config.max_triggers,
                )
            )

            self._is_auto_triggering = True

            logger.info(
                f"自动触发已启动: interval={config.interval_seconds}s, "
                f"prefix={config.barcode_prefix}, location={config.location}"
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
        async with self._lock:
            if not self._is_auto_triggering:
                raise HTTPException(status_code=400, detail="自动触发未运行")

            # 设置停止事件
            self._auto_trigger_stop_event.set()

            # 等待任务结束
            if self._auto_trigger_task:
                self._auto_trigger_task.cancel()
                try:
                    await self._auto_trigger_task
                except asyncio.CancelledError:
                    pass
                self._auto_trigger_task = None

            self._is_auto_triggering = False

            logger.info("自动触发已停止")

            return {
                "status": "stopped",
                "trigger_count": self._trigger_count,
            }

    def get_status(self) -> SensorStatusResponse:
        """获取传感器状态"""
        return SensorStatusResponse(
            is_auto_triggering=self._is_auto_triggering,
            trigger_count=self._trigger_count,
            current_config={
                "wes_callback_url": WES_EVENT_CALLBACK_URL,
                "default_interval": SENSOR_AUTO_TRIGGER_DEFAULT_INTERVAL,
                "barcode_prefix": SENSOR_BARCODE_PREFIX,
            },
        )

    def get_events(self, limit: int = 50) -> list[EventRecord]:
        """获取事件记录"""
        return self._events[-limit:]


# 全局传感器模拟器实例
sensor_simulator = SensorSimulator(device_id=DEVICE_INFO["device_id"])


# ============================================
# FastAPI 应用
# ============================================

app = FastAPI(
    title="摄像头 Mock 服务",
    description="模拟流水线识别点摄像头设备（含传感器模拟）",
    version="2.0.0",
)


@app.get("/api/v1/device/status")
async def get_status():
    """
    设备状态查询接口

    返回摄像头当前状态（白皮书 3.1 节）
    """
    return {
        "device_id": DEVICE_INFO["device_id"],
        "device_name": DEVICE_INFO["device_name"],
        "device_type": DEVICE_INFO["device_type"],
        "status": DEVICE_INFO["status"],
        "is_online": DEVICE_INFO["is_online"],
        "timestamp": int(datetime.now().timestamp() * 1000),
    }


@app.post("/api/v1/sensor/trigger", summary="手动触发传感器")
async def trigger_sensor(request: SensorTriggerRequest) -> EventRecord:
    """
    手动触发传感器检测物料到达

    **请求参数**：
    - barcode: 条码（可选，不提供则自动生成）
    - location: 位置（默认 CONVEYOR-STATION-01）
    - simulate_scan: 是否模拟扫码（默认 true）

    **流程**：
    1. 生成或使用提供的条码
    2. 构建物料到达事件
    3. 上报事件到 WES
    4. 返回事件记录

    **示例**：
    ```bash
    curl -X POST http://localhost:8003/api/v1/sensor/trigger \\
      -H "Content-Type: application/json" \\
      -d '{"barcode": "PKG-TEST-001", "location": "STATION-01"}'
    ```
    """
    return await sensor_simulator.trigger_material_arrival(
        barcode=request.barcode,
        location=request.location,
    )


@app.post("/api/v1/sensor/auto/start", summary="启动自动触发")
async def start_auto_trigger(config: AutoTriggerConfig) -> dict:
    """
    启动传感器自动触发

    **请求参数**：
    - interval_seconds: 触发间隔（秒，默认 10）
    - barcode_prefix: 条码前缀（默认 PKG）
    - location: 位置（默认 CONVEYOR-STATION-01）
    - max_triggers: 最大触发次数（可选，不限制则为 None）

    **流程**：
    1. 创建后台定时任务
    2. 按间隔触发物料到达事件
    3. 达到最大次数后自动停止

    **示例**：
    ```bash
    curl -X POST http://localhost:8003/api/v1/sensor/auto/start \\
      -H "Content-Type: application/json" \\
      -d '{"interval_seconds": 5, "max_triggers": 3}'
    ```
    """
    return await sensor_simulator.start_auto_trigger(config)


@app.post("/api/v1/sensor/auto/stop", summary="停止自动触发")
async def stop_auto_trigger() -> dict:
    """
    停止传感器自动触发

    **示例**：
    ```bash
    curl -X POST http://localhost:8003/api/v1/sensor/auto/stop
    ```
    """
    return await sensor_simulator.stop_auto_trigger()


@app.get("/api/v1/sensor/status", summary="传感器状态", response_model=SensorStatusResponse)
async def get_sensor_status() -> SensorStatusResponse:
    """
    获取传感器状态

    **返回**：
    - is_auto_triggering: 是否正在自动触发
    - trigger_count: 总触发次数
    - current_config: 当前配置

    **示例**：
    ```bash
    curl http://localhost:8003/api/v1/sensor/status
    ```
    """
    return sensor_simulator.get_status()


@app.get("/api/v1/sensor/events", summary="事件记录")
async def get_sensor_events(limit: int = 50) -> list[EventRecord]:
    """
    获取传感器事件记录

    **参数**：
    - limit: 返回记录数量（默认 50）

    **示例**：
    ```bash
    curl http://localhost:8003/api/v1/sensor/events?limit=10
    ```
    """
    return sensor_simulator.get_events(limit)


@app.get("/")
async def root():
    """根路径健康检查"""
    return {
        "service": "摄像头 Mock 服务",
        "version": "2.0.0",
        "status": "running",
        "device_id": DEVICE_INFO["device_id"],
        "sensor": {
            "is_auto_triggering": sensor_simulator._is_auto_triggering,
            "trigger_count": sensor_simulator._trigger_count,
        },
    }


# ============================================
# 服务器类
# ============================================


class CameraMockServer:
    """摄像头 Mock 服务器"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8003):
        self.host = host
        self.port = port
        self._server: Server | None = None
        self.config = Config(app=app, host=host, port=port, log_level="info")

    async def start(self) -> NoReturn:
        """启动服务器（阻塞运行）"""
        logger.info(f"摄像头 Mock 服务启动: http://{self.host}:{self.port}")
        logger.info(f"模拟设备: {DEVICE_INFO}")
        logger.info(f"WES 事件回调地址: {WES_EVENT_CALLBACK_URL}")
        logger.info("传感器 API 端点:")
        logger.info("  - POST /api/v1/sensor/trigger")
        logger.info("  - POST /api/v1/sensor/auto/start")
        logger.info("  - POST /api/v1/sensor/auto/stop")
        logger.info("  - GET  /api/v1/sensor/status")
        logger.info("  - GET  /api/v1/sensor/events")

        self._server = Server(self.config)
        await self._server.serve()

    def run(self):
        """同步运行服务器"""
        asyncio.run(self.start())


# ============================================
# 直接运行时的入口
# ============================================

if __name__ == "__main__":
    server = CameraMockServer()
    server.run()
