"""
SMT 粗分机机械臂 Mock 服务

支持左右两条标准 SMT 粗分线:
- ARM01 / ARM03: 进料机械臂，负责扫码、检测/测厚、输入侧 PICK_AND_PUT、NG 放置
- ARM02 / ARM04: 出料机械臂，负责从流水线出料位搬运到 BIN

正式接口:
- POST /api/v1/device/command
- GET  /api/v1/device/status
- POST /api/v1/device/cancel

调试接口:
- /debug/*
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict, cast

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from uvicorn import Config, Server

project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.workline_plugins.smt_classifier.contract import (
    INSPECTION_SIZE_NG_REASON,
    INSPECTION_THICKNESS_NG_REASON,
)
from src.workline_runtime.contracts import DeviceErrorCode
from tests.mock.smt_classifier.mock_support import (
    WES_EVENT_CALLBACK_URL,
    WES_RESULT_CALLBACK_URL,
    CancelRequest,
    DeviceCommandAck,
    DeviceCommandPayload,
    DeviceLocation,
    DeviceStatusResponse,
    JsonDict,
    current_millis,
    post_signed_json,
    register_mock_exception_handlers,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DEVICE_CODE = os.getenv("DEVICE_CODE") or os.getenv("DEVICE_ID", "ARM01")
EXECUTION_TIME = float(os.getenv("EXECUTION_TIME", "2.0"))
AUTO_EXECUTE_INTERVAL = int(os.getenv("AUTO_EXECUTE_INTERVAL", "5"))
BARCODE_PREFIX = os.getenv("BARCODE_PREFIX", "SMT-PKG")


class DeviceLocations(TypedDict):
    source: list[str]
    target: list[str]


class DeviceConfig(TypedDict):
    device_code: str
    device_name: str
    device_type: str
    device_role: Literal["INPUT_ARM", "OUTPUT_ARM"]
    port: int
    description: str
    task_types: list[str]
    locations: dict[str, list[DeviceLocation]]
    source_types: list[str]
    target_types: list[str]
    default_source_type: str
    default_target_type: str


class DeviceRuntimeStatus(TypedDict):
    device_code: str
    status: Literal["IDLE", "RUNNING", "ERROR", "OFFLINE"]
    is_online: bool
    error_code: str
    current_command_code: str | None


DEVICE_CONFIGS: dict[str, DeviceConfig] = {
    "ARM01": {
        "device_code": "ARM01",
        "device_name": "左侧进料机械臂",
        "device_type": "ROBOTIC_ARM",
        "device_role": "INPUT_ARM",
        "port": 8006,
        "description": "负责左侧扫码、检测/测厚、输入侧搬运与 NG 放置",
        "task_types": ["PICK_AND_PUT", "PICK_NG", "MEASUREMENT_REEL"],
        "locations": {
            "INPUT_PLATFORM": [DeviceLocation(location_id="LEFT_STATION_INPUT", location_type="INPUT_PLATFORM")],
            "PIPELINE_PLATFORM": [
                DeviceLocation(
                    location_id="LEFT_STATION_PIPELINE_INPUT",
                    location_type="PIPELINE_PLATFORM",
                )
            ],
            "NG_PLATFORM": [DeviceLocation(location_id="LEFT_STATION_NG", location_type="NG_PLATFORM")],
        },
        "source_types": ["INPUT_PLATFORM", "PIPELINE_PLATFORM"],
        "target_types": ["PIPELINE_PLATFORM", "NG_PLATFORM"],
        "default_source_type": "INPUT_PLATFORM",
        "default_target_type": "PIPELINE_PLATFORM",
    },
    "ARM02": {
        "device_code": "ARM02",
        "device_name": "左侧出料机械臂",
        "device_type": "ROBOTIC_ARM",
        "device_role": "OUTPUT_ARM",
        "port": 8007,
        "description": "负责从左侧流水线出料位搬运到料箱",
        "task_types": ["PICK_AND_PUT", "OUTPUT"],
        "locations": {
            "PIPELINE_PLATFORM": [
                DeviceLocation(
                    location_id="LEFT_STATION_PIPELINE_OUTPUT",
                    location_type="PIPELINE_PLATFORM",
                )
            ],
            "BIN": [
                DeviceLocation(
                    location_id="LEFT_STATION_OUTPUT",
                    location_type="BIN",
                    rack_id="LEFT_RACK_001",
                    bin_id="LEFT_BIN_104",
                    bin_type="三格箱",
                    bin_cell_location="1",
                    reel_layer="15",
                    reel_thickness="20",
                    reel_diameter="15inch",
                    reel_totalthickness="300",
                )
            ],
        },
        "source_types": ["PIPELINE_PLATFORM"],
        "target_types": ["BIN"],
        "default_source_type": "PIPELINE_PLATFORM",
        "default_target_type": "BIN",
    },
    "ARM03": {
        "device_code": "ARM03",
        "device_name": "右侧进料机械臂",
        "device_type": "ROBOTIC_ARM",
        "device_role": "INPUT_ARM",
        "port": 8006,
        "description": "负责右侧扫码、检测/测厚、输入侧搬运与 NG 放置",
        "task_types": ["PICK_AND_PUT", "PICK_NG", "MEASUREMENT_REEL"],
        "locations": {
            "INPUT_PLATFORM": [DeviceLocation(location_id="RIGHT_STATION_INPUT", location_type="INPUT_PLATFORM")],
            "PIPELINE_PLATFORM": [
                DeviceLocation(
                    location_id="RIGHT_STATION_PIPELINE_INPUT",
                    location_type="PIPELINE_PLATFORM",
                )
            ],
            "NG_PLATFORM": [DeviceLocation(location_id="RIGHT_STATION_NG", location_type="NG_PLATFORM")],
        },
        "source_types": ["INPUT_PLATFORM", "PIPELINE_PLATFORM"],
        "target_types": ["PIPELINE_PLATFORM", "NG_PLATFORM"],
        "default_source_type": "INPUT_PLATFORM",
        "default_target_type": "PIPELINE_PLATFORM",
    },
    "ARM04": {
        "device_code": "ARM04",
        "device_name": "右侧出料机械臂",
        "device_type": "ROBOTIC_ARM",
        "device_role": "OUTPUT_ARM",
        "port": 8007,
        "description": "负责从右侧流水线出料位搬运到料箱",
        "task_types": ["PICK_AND_PUT", "OUTPUT"],
        "locations": {
            "PIPELINE_PLATFORM": [
                DeviceLocation(
                    location_id="RIGHT_STATION_PIPELINE_OUTPUT",
                    location_type="PIPELINE_PLATFORM",
                )
            ],
            "BIN": [
                DeviceLocation(
                    location_id="RIGHT_STATION_OUTPUT",
                    location_type="BIN",
                    rack_id="RIGHT_RACK_001",
                    bin_id="BIN_204",
                    bin_type="三格箱",
                    bin_cell_location="1",
                    reel_layer="15",
                    reel_thickness="20",
                    reel_diameter="15inch",
                    reel_totalthickness="300",
                )
            ],
        },
        "source_types": ["PIPELINE_PLATFORM"],
        "target_types": ["BIN"],
        "default_source_type": "PIPELINE_PLATFORM",
        "default_target_type": "BIN",
    },
}

if DEVICE_CODE not in DEVICE_CONFIGS:
    raise ValueError(f"无效的设备编码: {DEVICE_CODE}，支持的设备: {list(DEVICE_CONFIGS)}")

CURRENT_DEVICE = DEVICE_CONFIGS[DEVICE_CODE]
DEVICE_PORT = int(os.getenv("DEVICE_PORT", str(CURRENT_DEVICE["port"])))
HOSTED_DEVICE_CONFIGS: dict[str, DeviceConfig] = {
    code: config for code, config in DEVICE_CONFIGS.items() if config["port"] == DEVICE_PORT
}
if not HOSTED_DEVICE_CONFIGS:
    raise ValueError(f"端口 {DEVICE_PORT} 未绑定任何机械臂设备")

DEFAULT_DEVICE_CODE = DEVICE_CODE if DEVICE_CODE in HOSTED_DEVICE_CONFIGS else next(iter(HOSTED_DEVICE_CONFIGS))
DEFAULT_DEVICE = HOSTED_DEVICE_CONFIGS[DEFAULT_DEVICE_CODE]


class ExecutionRecord(BaseModel):
    execution_id: str
    command_code: str
    task_type: str
    source: JsonDict
    target: JsonDict
    result: str
    message: str
    reported_event_type: str | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int


class DeviceStatusDetail(BaseModel):
    device_code: str
    status: str
    is_online: bool
    error_code: str
    current_command_code: str | None = None
    is_auto_executing: bool
    execution_count: int
    success_count: int
    failure_count: int


class ArmRootResponse(BaseModel):
    service: str
    version: str
    status: Literal["running"]
    device_code: str
    device_name: str
    description: str
    task_types: list[str]
    source_types: list[str]
    target_types: list[str]
    device_status: DeviceStatusDetail
    wes_result_callback_url: str
    wes_event_callback_url: str


class ManualExecuteRequest(BaseModel):
    """手动执行命令请求（调试接口）

    默认值根据设备角色自动选择：
    - ARM01 (进料臂): INPUT_PLATFORM → PIPELINE_PLATFORM
    - ARM02 (出料臂): PIPELINE_PLATFORM → BIN

    示例（最小化请求）:
        {}  # 使用全部默认值，执行默认路径

    示例（仅指定条码）:
        {"barcode": "TEST-001"}

    示例（NG 流程）:
        {"target_type": "NG_PLATFORM"}  # ARM01 放置到 NG 位
    """

    task_type: str | None = None  # 默认使用设备第一个支持的任务类型
    source_type: str | None = None  # 默认使用设备 default_source_type
    target_type: str | None = None  # 默认使用设备 default_target_type
    source_location_id: str | None = None  # 精确指定源位置 ID
    target_location_id: str | None = None  # 精确指定目标位置 ID
    command_code: str | None = None  # 默认自动生成
    barcode: str | None = None  # 默认自动生成
    simulate_failure: bool = False
    execution_time: float = 0.5  # 缩短默认执行时间，方便调试
    reason: str | None = None
    report_result: bool = False  # 默认不上报结果，避免干扰测试

    model_config = {
        "json_schema_extra": {
            "examples": [
                {},  # 最小化：使用全部默认值
                {"barcode": "TEST-001"},  # 仅指定条码
                {"task_type": "PICK_NG", "target_type": "NG_PLATFORM"},  # NG 流程
                {"simulate_failure": True, "execution_time": 0.1},  # 模拟失败
            ]
        }
    }


class AutoExecuteConfig(BaseModel):
    interval_seconds: int = AUTO_EXECUTE_INTERVAL
    source_type: str | None = None
    target_type: str | None = None
    barcode_prefix: str = BARCODE_PREFIX
    max_executions: int | None = None


class AutoExecuteResolvedConfig(BaseModel):
    interval_seconds: int
    source_type: str
    target_type: str
    barcode_prefix: str
    max_executions: int | None = None


class AutoExecuteStartResponse(BaseModel):
    status: Literal["started"]
    device_code: str
    config: AutoExecuteResolvedConfig


class AutoExecuteStopResponse(BaseModel):
    status: Literal["stopped"]
    device_code: str
    execution_count: int


class ScanCompletedDebugRequest(BaseModel):
    """扫码完成事件模拟（调试接口）

    用于模拟扫码枪完成扫码后上报事件到 WES。

    示例（最小化）:
        {"barcode": "TEST-001"}

    示例（NG 结果）:
        {"barcode": "TEST-NG-001", "result": "NG"}
    """

    barcode: str
    result: Literal["OK", "NG"] = "OK"
    location_id: str = "STATION_INPUT1"  # 默认进料平台位置

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"barcode": "TEST-001"},
                {"barcode": "TEST-NG-001", "result": "NG"},
                {
                    "barcode": "CUSTOM-001",
                    "result": "OK",
                    "location_id": "STATION_INPUT1",
                },
            ]
        }
    }


class InspectionCompletedDebugRequest(BaseModel):
    """检测完成事件模拟（调试接口）

    用于模拟检测设备完成检测后上报事件到 WES。
    仅 ARM01（进料臂）支持此接口。

    示例（最小化，OK 结果）:
        {}  # 使用全部默认值

    示例（NG 结果）:
        {"result": "NG"}
    """

    result: Literal["OK", "NG"] = "OK"
    location_id: str = "STATION_PIPELINE1_INPUT1"  # 默认流水线进料位
    barcode: str | None = None  # 默认自动生成
    reel_diameter: str = "15inch"
    reel_thickness: str = "20"
    dimensions: JsonDict = Field(
        default_factory=lambda: {"length": 100.0, "width": 50.0, "height": 15.0},
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {},  # 最小化：OK 结果
                {"result": "NG"},  # NG 结果
                {"result": "OK", "barcode": "TEST-001", "reel_diameter": "13inch"},
            ]
        }
    }


DEVICE_STATUS_BY_CODE: dict[str, DeviceRuntimeStatus] = {
    code: {
        "device_code": config["device_code"],
        "status": "IDLE",
        "is_online": True,
        "error_code": "NONE",
        "current_command_code": None,
    }
    for code, config in HOSTED_DEVICE_CONFIGS.items()
}


class ArmSimulator:
    """Mock 机械臂模拟器"""

    MAX_EXECUTION_RECORDS = 1000  # 最多保留1000条执行记录

    def __init__(self, device_config: DeviceConfig):
        self.device_config = device_config
        self.device_code = device_config["device_code"]
        self.device_name = device_config["device_name"]
        self.runtime_status = DEVICE_STATUS_BY_CODE[self.device_code]
        self._counter = 0
        self._execution_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._executions: deque[ExecutionRecord] = deque(maxlen=self.MAX_EXECUTION_RECORDS)
        self._is_auto_executing = False
        self._auto_task: asyncio.Task[None] | None = None
        self._auto_stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def _finalize_auto_execution_state(self) -> None:
        current_task = asyncio.current_task()
        async with self._lock:
            self._is_auto_executing = False
            if current_task is not None and self._auto_task is current_task:
                self._auto_task = None

    def _generate_command_code(self) -> str:
        self._counter += 1
        return f"CMD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._counter:03d}"

    def _generate_barcode_seed(self, prefix: str = BARCODE_PREFIX) -> str:
        today = datetime.now().strftime("%Y%m%d")
        return f"{prefix}{today}{self._counter:03d}"

    def _validate_task_type(self, task_type: str) -> bool:
        return task_type in self.device_config["task_types"]

    def _find_location_by_id(self, location_id: str) -> DeviceLocation | None:
        for locations in self.device_config["locations"].values():
            for location in locations:
                if location.location_id == location_id:
                    return location
                # 出料位实际常按 bin_id 回传，mock 需要兼容将其解析回 BIN 位置定义。
                if location.location_type == "BIN" and location.bin_id == location_id:
                    return location
        return None

    def _resolve_dynamic_bin_location(self, location_id: str) -> DeviceLocation | None:
        if not location_id.startswith("BIN_"):
            return None

        bin_locations = self.device_config["locations"].get("BIN", [])
        if not bin_locations:
            return None

        template = bin_locations[0]
        return template.model_copy(update={"bin_id": location_id})

    def _resolve_location(
        self,
        *,
        location_type: str | None,
        location_id: str | None,
        default_type: str,
        allowed_types: list[str],
        field_name: str,
    ) -> DeviceLocation:
        if location_id:
            location = self._find_location_by_id(location_id)
            if location is None and "BIN" in allowed_types:
                location = self._resolve_dynamic_bin_location(location_id)
            if location is None:
                raise HTTPException(status_code=400, detail=f"无效的{field_name}位置: {location_id}")
            if location.location_type not in allowed_types:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name}位置类型不支持: {location.location_type}，支持: {allowed_types}",
                )
            if location_type and location.location_type != location_type:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name}位置类型与位置编码不匹配: {location_id} != {location_type}",
                )
            return location

        resolved_type = location_type or default_type
        if resolved_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的{field_name}位置类型: {resolved_type}，支持: {allowed_types}",
            )

        locations = self.device_config["locations"].get(resolved_type, [])
        if not locations:
            raise HTTPException(
                status_code=400,
                detail=f"设备未配置{field_name}位置类型: {resolved_type}",
            )
        return locations[0]

    def _resolve_command_locations_from_params(self, params: JsonDict) -> tuple[DeviceLocation, DeviceLocation]:
        source_payload = cast("JsonDict", params.get("source")) if isinstance(params.get("source"), dict) else {}
        target_payload = cast("JsonDict", params.get("target")) if isinstance(params.get("target"), dict) else {}
        source = self._resolve_location(
            location_type=cast(
                "str | None",
                source_payload.get("location_type") or params.get("source_type"),
            ),
            location_id=cast(
                "str | None",
                source_payload.get("location_id") or params.get("source_loc"),
            ),
            default_type=self.device_config["default_source_type"],
            allowed_types=self.device_config["source_types"],
            field_name="源",
        )
        target = self._resolve_location(
            location_type=cast(
                "str | None",
                target_payload.get("location_type") or params.get("target_type"),
            ),
            location_id=cast(
                "str | None",
                target_payload.get("location_id") or params.get("target_loc"),
            ),
            default_type=self.device_config["default_target_type"],
            allowed_types=self.device_config["target_types"],
            field_name="目标",
        )
        return source, target

    def _build_barcode_fields(self, barcode_seed: str | None) -> JsonDict:
        """
        构建 SixInOne 条码字段（对齐硬件约定）

        Args:
            barcode_seed: 条码种子（用于生成 LotCode）

        Returns:
            SixInOne 字段字典
        """
        base = barcode_seed or self._generate_barcode_seed()
        return {
            "PkgID": base,  # 流水号（业务主键，对齐 SixInOne.PkgID）
            "LotCode": base,  # 批次码
            "DateCode": "20260409",  # 日期码
            "Qty": "100",  # 数量
            "HHPN": "PN001",  # 产品PN码
            "MfrPN": "MFR002",  # 制造商PN码
        }

    def _build_result_data(
        self,
        *,
        task_type: str,
        source: DeviceLocation,
        target: DeviceLocation,
        barcode_seed: str | None,
        pkg_id: str | None,
        move_result: str,
        inspection_ng_reason: str | None = None,
    ) -> JsonDict:
        if task_type == "MEASUREMENT_REEL":
            data: JsonDict = {
                "PkgID": pkg_id or barcode_seed or "",
                "reel_diameter": 15.0,
                "reel_thickness": 20.0,
                "inspection_result": "NG" if inspection_ng_reason else "OK",
            }
            if inspection_ng_reason:
                reason_messages = {
                    INSPECTION_SIZE_NG_REASON: "料盘尺寸检测 NG",
                    INSPECTION_THICKNESS_NG_REASON: "料盘厚度检测 NG",
                }
                data["reason_code"] = inspection_ng_reason
                data["reason_message"] = reason_messages.get(inspection_ng_reason, "检测结果 NG")
            return data

        data: JsonDict = {
            "actual_qty": 1,
            "location": target.location_id,
            "pick_and_put_result": move_result,
        }
        if self.device_config["device_role"] == "INPUT_ARM" and target.location_type == "PIPELINE_PLATFORM":
            data.update(self._build_barcode_fields(barcode_seed))
            data["reel_diameter"] = "15inch"
            data["reel_thickness"] = "20"
        if target.location_type == "BIN":
            data.update(
                {
                    "rack_id": target.rack_id,
                    "bin_id": target.bin_id,
                    "bin_type": target.bin_type,
                    "bin_cell_location": target.bin_cell_location,
                }
            )
        return data

    async def _callback_result_to_wes(
        self,
        *,
        command_code: str,
        result: str,
        data: JsonDict | None,
        error_detail: JsonDict | None,
    ) -> JsonDict:
        payload: JsonDict = {
            "command_code": command_code,
            "device_code": self.device_code,
            "result": result,
            "finish_time": current_millis(),
        }
        if data is not None:
            payload["data"] = data
        if error_detail is not None:
            payload["error_detail"] = error_detail

        # ========== 业务流程日志：回调结果到 WES ==========
        logger.info(
            f"\n{'=' * 60}\n"
            f"[{self.device_name}] 回调结果到 WES\n"
            f"{'=' * 60}\n"
            f"  命令编号: {command_code}\n"
            f"  设备编号: {self.device_code}\n"
            f"  执行结果: {result}\n"
            f"  错误码: {error_detail.get('error_code', 'N/A') if error_detail else 'N/A'}\n"
            f"  错误信息: {error_detail.get('error_message', 'N/A') if error_detail else 'N/A'}\n"
            f"  回调地址: {WES_RESULT_CALLBACK_URL}\n"
            f"{'=' * 60}"
        )

        return await post_signed_json(WES_RESULT_CALLBACK_URL, payload)

    async def _post_event_to_wes(self, event_type: str, data: JsonDict | None) -> JsonDict:
        payload: JsonDict = {
            "device_code": self.device_code,
            "event_type": event_type,
            "timestamp": current_millis(),
            "data": data or {},
        }

        # ========== 业务流程日志：上报事件到 WES ==========
        logger.info(
            f"\n{'=' * 60}\n"
            f"[{self.device_name}] 上报事件到 WES\n"
            f"{'=' * 60}\n"
            f"  设备编号: {self.device_code}\n"
            f"  事件类型: {event_type}\n"
            f"  事件数据: {data}\n"
            f"  回调地址: {WES_EVENT_CALLBACK_URL}\n"
            f"{'=' * 60}"
        )

        return await post_signed_json(WES_EVENT_CALLBACK_URL, payload)

    async def execute_command(
        self,
        task_type: str | None = None,
        source_type: str | None = None,
        target_type: str | None = None,
        source_location_id: str | None = None,
        target_location_id: str | None = None,
        target_bin_type: str | None = None,
        barcode: str | None = None,
        simulate_failure: bool = False,
        execution_time: float = EXECUTION_TIME,
        command_code: str | None = None,
        reason: str | None = None,
        error_code: str | None = None,
        pkg_id: str | None = None,
        inspection_ng_reason: str | None = None,
        report_result: bool = True,
    ) -> ExecutionRecord:
        async with self._lock:
            resolved_task_type = task_type or self.device_config["task_types"][0]
            if not self._validate_task_type(resolved_task_type):
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的任务类型: {resolved_task_type}，支持的任务类型: {self.device_config['task_types']}",
                )

            source = self._resolve_location(
                location_type=source_type,
                location_id=source_location_id,
                default_type=self.device_config["default_source_type"],
                allowed_types=self.device_config["source_types"],
                field_name="源",
            )
            target = self._resolve_location(
                location_type=target_type,
                location_id=target_location_id,
                default_type=self.device_config["default_target_type"],
                allowed_types=self.device_config["target_types"],
                field_name="目标",
            )
            if target.location_type == "BIN":
                target = target.model_copy(
                    update={
                        "bin_id": target_location_id or target.bin_id,
                        "bin_type": target_bin_type or target.bin_type,
                    }
                )
            resolved_command_code = command_code or self._generate_command_code()

            # 详细日志：开始执行
            logger.info(
                f"[{self.device_name}] 开始执行: {resolved_command_code}\n"
                f"  任务类型: {resolved_task_type}\n"
                f"  源位置: {source.location_id} ({source.location_type})\n"
                f"  目标位置: {target.location_id} ({target.location_type})\n"
                f"  执行时间: {execution_time}s\n"
                f"  条码: {barcode or '无'}"
            )

            started_at = datetime.now()
            self.runtime_status["status"] = "RUNNING"
            self.runtime_status["current_command_code"] = resolved_command_code

            # 模拟执行过程
            logger.info(f"[{self.device_name}] 执行中...")
            await asyncio.sleep(execution_time)

            # 错误码映射（硬件约定）
            error_messages = {
                DeviceErrorCode.SCAN_FAILED.value: "扫码异常",
                DeviceErrorCode.PICK_AND_PUT_FAILED.value: "搬运失败",
                DeviceErrorCode.BIN_FULL.value: "料箱已满",
                DeviceErrorCode.DEVICE_UNKNOWN_ERROR.value: "未知错误",
            }

            # 确定错误码和结果
            if error_code:
                # 使用指定的错误码
                result = "FAILED"
                error_message = error_messages.get(error_code, reason or "未知错误")
                error_detail = {
                    "error_code": error_code,
                    "error_message": error_message,
                }
                move_result = "PUT_FAILED"
                logger.warning(f"[{self.device_name}] 模拟错误: 错误码={error_code}, 错误信息={error_message}")
            elif simulate_failure:
                # 默认失败
                result = "FAILED"
                error_detail = {
                    "error_code": DeviceErrorCode.PICK_AND_PUT_FAILED.value,
                    "error_message": reason or "搬运失败",
                }
                move_result = "PUT_FAILED"
            else:
                # 成功
                result = "SUCCESS"
                error_detail = {
                    "error_code": DeviceErrorCode.NONE.value,
                    "error_message": "",
                }
                move_result = "PUT_FINISHED"

            # 详细日志：执行完成
            logger.info(
                f"[{self.device_name}] 执行完成: {resolved_command_code}\n"
                f"  结果: {result}\n"
                f"  错误码: {error_detail['error_code']}\n"
                f"  耗时: {execution_time}s"
            )

            callback_data = self._build_result_data(
                task_type=resolved_task_type,
                source=source,
                target=target,
                barcode_seed=barcode,
                pkg_id=pkg_id,
                move_result=move_result,
                inspection_ng_reason=inspection_ng_reason,
            )
            if report_result:
                try:
                    await self._callback_result_to_wes(
                        command_code=resolved_command_code,
                        result=result,
                        data=callback_data,
                        error_detail=None if result == "SUCCESS" else error_detail,
                    )
                except Exception as exc:
                    logger.error(f"WES 结果回调失败: {exc}")

            finished_at = datetime.now()
            duration_ms = int((finished_at - started_at).total_seconds() * 1000)

            # 更新设备状态
            self.runtime_status["status"] = "IDLE"
            self.runtime_status["current_command_code"] = None
            if result == "SUCCESS":
                self._success_count += 1
            else:
                self._failure_count += 1
                self.runtime_status["error_code"] = error_detail["error_code"]

            execution_id = f"EXEC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self._execution_count:03d}"
            record = ExecutionRecord(
                execution_id=execution_id,
                command_code=resolved_command_code,
                task_type=resolved_task_type,
                source=source.to_payload(),
                target=target.to_payload(),
                result=result,
                message="任务执行成功" if result == "SUCCESS" else (reason or "任务执行失败"),
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
            self._executions.append(record)
            self._execution_count += 1
            return record

    @staticmethod
    def _inspection_ng_reason_from_token(token: str | None) -> str | None:
        normalized = (token or "").upper()
        if "SIZENG" in normalized or "SIZE_NG" in normalized:
            return INSPECTION_SIZE_NG_REASON
        if "THICKNESSNG" in normalized or "THICKNESS_NG" in normalized:
            return INSPECTION_THICKNESS_NG_REASON
        return None

    async def execute_wes_command(self, payload: DeviceCommandPayload) -> ExecutionRecord:
        params = payload.params or {}
        source, target = self._resolve_command_locations_from_params(params)
        if target.location_type == "BIN":
            target = target.model_copy(
                update={
                    "bin_id": cast("str | None", params.get("target_loc")) or target.bin_id,
                    "bin_type": cast("str | None", params.get("bin_type")) or target.bin_type,
                }
            )

        barcode = cast("str | None", params.get("barcode"))
        pkg_id = cast("str | None", params.get("pkg_id"))
        inspection_ng_reason = None
        if payload.task_type == "MEASUREMENT_REEL":
            inspection_ng_reason = self._inspection_ng_reason_from_token(pkg_id or barcode)
            if inspection_ng_reason:
                logger.info(f"[{self.device_name}] 智能业务 NG 模拟: {inspection_ng_reason}")

        # 从 params 中提取错误码（优先级高于智能模拟）
        error_code = params.get("error_code")
        if error_code:
            logger.info(f"[{self.device_name}] 模拟错误码: {error_code}")

        return await self.execute_command(
            task_type=payload.task_type,
            source_type=source.location_type,
            target_type=target.location_type,
            source_location_id=source.location_id,
            target_location_id=cast("str | None", params.get("target_loc")) or target.location_id,
            target_bin_type=cast("str | None", params.get("bin_type")),
            barcode=barcode,
            simulate_failure=bool(params.get("simulate_failure", False)),
            execution_time=float(params.get("execution_time", EXECUTION_TIME)),
            command_code=payload.command_code,
            reason=cast("str | None", params.get("reason")),
            error_code=cast("str | None", error_code),
            pkg_id=pkg_id,
            inspection_ng_reason=inspection_ng_reason,
            report_result=True,
        )

    async def emit_scan_completed(self, request: ScanCompletedDebugRequest) -> ExecutionRecord:
        if self.device_config["device_role"] != "INPUT_ARM":
            raise HTTPException(status_code=400, detail="当前设备不支持扫码事件模拟")
        barcodes = self._build_barcode_fields(request.barcode)
        event_data = {
            "location": request.location_id,
            "result": request.result,
            **barcodes,
        }
        await self._post_event_to_wes("SCAN_COMPLETED", event_data)
        now = datetime.now()
        record = ExecutionRecord(
            execution_id=f"EVT-SCAN-{current_millis()}",
            command_code="-",
            task_type="SCAN_COMPLETED",
            source={
                "location_id": request.location_id,
                "location_type": "INPUT_PLATFORM",
            },
            target={},
            result=request.result,
            message="扫码事件已上报",
            reported_event_type="SCAN_COMPLETED",
            started_at=now,
            finished_at=now,
            duration_ms=0,
        )
        self._executions.append(record)
        return record

    async def emit_inspection_completed(self, request: InspectionCompletedDebugRequest) -> ExecutionRecord:
        if self.device_config["device_role"] != "INPUT_ARM":
            raise HTTPException(status_code=400, detail="当前设备不支持检测事件模拟")
        barcode_seed = request.barcode or self._generate_barcode_seed()
        event_data = {
            "location": request.location_id,
            "result": request.result,
            "inspection_result": request.result,
            "dimensions": request.dimensions,
            "reel_diameter": request.reel_diameter,
            "reel_thickness": request.reel_thickness,
            **self._build_barcode_fields(barcode_seed),
        }
        # 使用 INSPECTION_COMPLETED 事件类型（smt_classifier 插件期望）
        await self._post_event_to_wes("INSPECTION_COMPLETED", event_data)
        now = datetime.now()
        record = ExecutionRecord(
            execution_id=f"EVT-INSPECT-{current_millis()}",
            command_code="-",
            task_type="INSPECTION_COMPLETED",
            source={
                "location_id": request.location_id,
                "location_type": "PIPELINE_PLATFORM",
            },
            target={},
            result=request.result,
            message="检测事件已上报",
            reported_event_type="INSPECTION_COMPLETED",
            started_at=now,
            finished_at=now,
            duration_ms=0,
        )
        self._executions.append(record)
        return record

    async def _auto_execute_loop(
        self,
        *,
        interval_seconds: int,
        source_type: str,
        target_type: str,
        barcode_prefix: str,
        max_executions: int | None,
    ) -> None:
        executed = 0
        try:
            while not self._auto_stop_event.is_set():
                if max_executions is not None and executed >= max_executions:
                    break
                barcode = self._generate_barcode_seed(barcode_prefix)
                try:
                    await self.execute_command(
                        source_type=source_type,
                        target_type=target_type,
                        barcode=barcode,
                        report_result=False,
                    )
                    executed += 1
                except Exception as exc:
                    logger.error(f"自动执行失败: {exc}")
                try:
                    await asyncio.wait_for(self._auto_stop_event.wait(), timeout=interval_seconds)
                    break
                except TimeoutError:  # noqa: S112
                    continue
        finally:
            await self._finalize_auto_execution_state()

    async def start_auto_execution(self, config: AutoExecuteConfig) -> AutoExecuteStartResponse:
        async with self._lock:
            if self._is_auto_executing:
                raise HTTPException(status_code=400, detail="自动执行已在运行中")
            source_type = config.source_type or self.device_config["default_source_type"]
            target_type = config.target_type or self.device_config["default_target_type"]
            self._auto_stop_event.clear()
            self._auto_task = asyncio.create_task(
                self._auto_execute_loop(
                    interval_seconds=config.interval_seconds,
                    source_type=source_type,
                    target_type=target_type,
                    barcode_prefix=config.barcode_prefix,
                    max_executions=config.max_executions,
                )
            )
            self._is_auto_executing = True
            return AutoExecuteStartResponse(
                status="started",
                device_code=self.device_code,
                config=AutoExecuteResolvedConfig(
                    interval_seconds=config.interval_seconds,
                    source_type=source_type,
                    target_type=target_type,
                    barcode_prefix=config.barcode_prefix,
                    max_executions=config.max_executions,
                ),
            )

    async def stop_auto_execution(self) -> AutoExecuteStopResponse:
        current_task = asyncio.current_task()
        async with self._lock:
            if not self._is_auto_executing:
                return AutoExecuteStopResponse(
                    status="stopped",
                    device_code=self.device_code,
                    execution_count=self._execution_count,
                )
            self._auto_stop_event.set()
            auto_task = self._auto_task
            self._is_auto_executing = False
            self._auto_task = None
        if auto_task and auto_task is not current_task:
            auto_task.cancel()
            try:
                await auto_task
            except asyncio.CancelledError:
                pass
        return AutoExecuteStopResponse(
            status="stopped",
            device_code=self.device_code,
            execution_count=self._execution_count,
        )

    def get_status(self, current_command: JsonDict | None = None) -> DeviceStatusResponse:
        return DeviceStatusResponse(
            device_code=self.device_code,
            status=self.runtime_status["status"],
            current_command_code=current_command["command_code"] if current_command else None,
            error_code=self.runtime_status["error_code"],
            timestamp=current_millis(),
        )

    def get_executions(self, limit: int = 50) -> list[ExecutionRecord]:
        if limit <= 0:
            return []
        return list(self._executions)[-limit:]


ARM_SIMULATORS: dict[str, ArmSimulator] = {
    code: ArmSimulator(device_config=config) for code, config in HOSTED_DEVICE_CONFIGS.items()
}
CURRENT_COMMANDS: dict[str, JsonDict | None] = dict.fromkeys(HOSTED_DEVICE_CONFIGS, None)
CURRENT_COMMAND_TASKS: dict[str, asyncio.Task[None] | None] = dict.fromkeys(HOSTED_DEVICE_CONFIGS, None)


def _set_current_command(device_code: str, value: JsonDict | None) -> None:
    global current_command
    CURRENT_COMMANDS[device_code] = value
    if device_code == DEFAULT_DEVICE_CODE:
        current_command = value


def _set_current_command_task(device_code: str, value: asyncio.Task[None] | None) -> None:
    global current_command_task
    CURRENT_COMMAND_TASKS[device_code] = value
    if device_code == DEFAULT_DEVICE_CODE:
        current_command_task = value


def _resolve_hosted_device_code(device_code: str | None) -> str:
    resolved_device_code = device_code or DEFAULT_DEVICE_CODE
    if resolved_device_code not in HOSTED_DEVICE_CONFIGS:
        raise HTTPException(
            status_code=404,
            detail=f"设备 {resolved_device_code} 不由当前服务实例托管，支持设备: {sorted(HOSTED_DEVICE_CONFIGS)}",
        )
    return resolved_device_code


def _find_device_code_by_command(command_code: str) -> str | None:
    for device_code, command in CURRENT_COMMANDS.items():
        if command is not None and command.get("command_code") == command_code:
            return device_code
    return None


arm_simulator = ARM_SIMULATORS[DEFAULT_DEVICE_CODE]
DEVICE_STATUS = DEVICE_STATUS_BY_CODE[DEFAULT_DEVICE_CODE]
current_command: JsonDict | None = None
current_command_task: asyncio.Task[None] | None = None

app = FastAPI(
    title=f"SMT 粗分机机械臂 Mock 服务 - {', '.join(HOSTED_DEVICE_CONFIGS)}",
    description=f"模拟机械臂设备: {', '.join(HOSTED_DEVICE_CONFIGS)}",
    version="2.0.0",
)
register_mock_exception_handlers(app, logger, service_name="SMT_ARM_MOCK")


@app.get("/api/v1/device/status", response_model=DeviceStatusResponse)
async def get_device_status(device_code: str | None = None) -> DeviceStatusResponse:
    resolved_device_code = _resolve_hosted_device_code(device_code)
    simulator = ARM_SIMULATORS[resolved_device_code]
    return simulator.get_status(current_command=CURRENT_COMMANDS[resolved_device_code])


@app.post("/api/v1/device/command", response_model=DeviceCommandAck)
async def receive_command(payload: DeviceCommandPayload) -> DeviceCommandAck:
    resolved_device_code = _resolve_hosted_device_code(payload.device_code)
    device_config = HOSTED_DEVICE_CONFIGS[resolved_device_code]
    simulator = ARM_SIMULATORS[resolved_device_code]
    device_status = DEVICE_STATUS_BY_CODE[resolved_device_code]

    # ========== 业务流程日志：收到命令 ==========
    logger.info(
        f"\n{'=' * 60}\n"
        f"[{device_config['device_name']}] 收到 WES 命令\n"
        f"{'=' * 60}\n"
        f"  设备编号: {resolved_device_code}\n"
        f"  命令编号: {payload.command_code}\n"
        f"  任务类型: {payload.task_type}\n"
        f"  优先级: {payload.priority}\n"
        f"  超时: {payload.timeout}ms\n"
        f"  参数: {payload.params}\n"
        f"{'=' * 60}"
    )

    if device_status["status"] == "RUNNING":
        logger.warning(f"[{device_config['device_name']}] 设备忙，拒绝命令")
        raise HTTPException(status_code=503, detail="Device Busy")
    if not simulator._validate_task_type(payload.task_type):
        logger.error(f"[{device_config['device_name']}] 不支持的任务类型: {payload.task_type}")
        raise HTTPException(
            status_code=400,
            detail=f"不支持的任务类型: {payload.task_type}，支持的任务类型: {device_config['task_types']}",
        )

    device_status["status"] = "RUNNING"
    device_status["error_code"] = "NONE"
    device_status["current_command_code"] = payload.command_code
    _set_current_command(resolved_device_code, cast("JsonDict", payload.model_dump()))
    trace_id = f"{resolved_device_code}-LOG-{payload.command_code.split('-')[-1]}"

    # ========== 业务流程日志：开始执行 ==========
    logger.info(f"[{device_config['device_name']}] 开始异步执行命令...")

    task = asyncio.create_task(_execute_wes_command_with_cleanup(resolved_device_code, payload))
    _set_current_command_task(resolved_device_code, task)
    return DeviceCommandAck(code=200, message="Accepted", trace_id=trace_id)


async def _execute_wes_command_with_cleanup(device_code: str, payload: DeviceCommandPayload) -> None:
    device_config = HOSTED_DEVICE_CONFIGS[device_code]
    simulator = ARM_SIMULATORS[device_code]
    device_status = DEVICE_STATUS_BY_CODE[device_code]
    try:
        await simulator.execute_wes_command(payload)
    except asyncio.CancelledError:
        logger.info(f"[{device_config['device_name']}] 指令已中断: {payload.command_code}")
        raise
    finally:
        device_status["status"] = "IDLE"
        device_status["current_command_code"] = None
        _set_current_command(device_code, None)
        _set_current_command_task(device_code, None)


@app.post("/api/v1/device/cancel", response_model=DeviceCommandAck)
async def cancel_command(request: CancelRequest) -> DeviceCommandAck:
    resolved_device_code = _find_device_code_by_command(request.command_code)
    if resolved_device_code is None:
        raise HTTPException(status_code=404, detail="No Active Command")
    device_config = HOSTED_DEVICE_CONFIGS[resolved_device_code]
    device_status = DEVICE_STATUS_BY_CODE[resolved_device_code]
    command = CURRENT_COMMANDS[resolved_device_code]
    logger.info(f"[{device_config['device_name']}] 收到取消指令请求: {request.command_code}")
    if command is None:
        raise HTTPException(status_code=404, detail="No Active Command")
    task = CURRENT_COMMAND_TASKS[resolved_device_code]
    if task is None or task.done():
        device_status["status"] = "IDLE"
        device_status["current_command_code"] = None
        _set_current_command(resolved_device_code, None)
        _set_current_command_task(resolved_device_code, None)
        raise HTTPException(status_code=409, detail="Command Already Finished")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return DeviceCommandAck(code=200, message="Cancelled")


@app.post(
    "/debug/execute",
    response_model=ExecutionRecord,
    summary="手动执行命令",
    description="执行机械臂命令。不指定参数时使用设备默认值：ARM01 默认从 INPUT_PLATFORM 搬到 PIPELINE_PLATFORM。",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "minimal": {"summary": "最小化请求", "value": {}},
                        "with_barcode": {
                            "summary": "指定条码",
                            "value": {"barcode": "TEST-001"},
                        },
                        "ng_flow": {
                            "summary": "NG 流程",
                            "value": {
                                "task_type": "PICK_NG",
                                "target_type": "NG_PLATFORM",
                            },
                        },
                        "failure": {
                            "summary": "模拟失败",
                            "value": {"simulate_failure": True, "execution_time": 0.1},
                        },
                    }
                }
            }
        }
    },
)
async def execute_arm_command(request: ManualExecuteRequest) -> ExecutionRecord:
    return await arm_simulator.execute_command(
        task_type=request.task_type,
        source_type=request.source_type,
        target_type=request.target_type,
        source_location_id=request.source_location_id,
        target_location_id=request.target_location_id,
        barcode=request.barcode,
        simulate_failure=request.simulate_failure,
        execution_time=request.execution_time,
        command_code=request.command_code,
        reason=request.reason,
        report_result=request.report_result,
    )


@app.post(
    "/debug/scan-completed",
    response_model=ExecutionRecord,
    summary="模拟扫码完成事件",
    description="触发扫码完成事件，会上报 SCAN_COMPLETED 到 WES。",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "ok": {"summary": "OK 结果", "value": {"barcode": "LOTABC123"}},
                        "ng": {
                            "summary": "NG 结果",
                            "value": {"barcode": "LOTSIZENG", "result": "NG"},
                        },
                    }
                }
            }
        }
    },
)
async def debug_scan_completed(request: ScanCompletedDebugRequest) -> ExecutionRecord:
    return await arm_simulator.emit_scan_completed(request)


@app.post(
    "/debug/inspection-completed",
    response_model=ExecutionRecord,
    summary="模拟检测完成事件",
    description="触发检测完成事件，会上报 INSPECTION_COMPLETED 到 WES。仅 ARM01 支持。",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "ok": {"summary": "OK 结果", "value": {}},
                        "ng": {"summary": "NG 结果", "value": {"result": "NG"}},
                    }
                }
            }
        }
    },
)
async def debug_inspection_completed(
    request: InspectionCompletedDebugRequest,
) -> ExecutionRecord:
    return await arm_simulator.emit_inspection_completed(request)


@app.post("/debug/auto/start", response_model=AutoExecuteStartResponse)
async def start_auto_execute(config: AutoExecuteConfig) -> AutoExecuteStartResponse:
    return await arm_simulator.start_auto_execution(config)


@app.post("/debug/auto/stop", response_model=AutoExecuteStopResponse)
async def stop_auto_execute() -> AutoExecuteStopResponse:
    return await arm_simulator.stop_auto_execution()


@app.get("/debug/executions", response_model=list[ExecutionRecord])
async def get_arm_executions(limit: int = 50) -> list[ExecutionRecord]:
    return arm_simulator.get_executions(limit)


@app.get("/", response_model=ArmRootResponse)
async def root() -> ArmRootResponse:
    return ArmRootResponse(
        service=f"SMT 粗分机机械臂 Mock 服务 - {CURRENT_DEVICE['device_name']}",
        version="2.0.0",
        status="running",
        device_code=CURRENT_DEVICE["device_code"],
        device_name=CURRENT_DEVICE["device_name"],
        description=CURRENT_DEVICE["description"],
        task_types=CURRENT_DEVICE["task_types"],
        source_types=CURRENT_DEVICE["source_types"],
        target_types=CURRENT_DEVICE["target_types"],
        device_status=DeviceStatusDetail(
            device_code=CURRENT_DEVICE["device_code"],
            status=DEVICE_STATUS["status"],
            is_online=DEVICE_STATUS["is_online"],
            error_code=DEVICE_STATUS["error_code"],
            current_command_code=current_command["command_code"] if current_command else None,
            is_auto_executing=arm_simulator._is_auto_executing,
            execution_count=arm_simulator._execution_count,
            success_count=arm_simulator._success_count,
            failure_count=arm_simulator._failure_count,
        ),
        wes_result_callback_url=WES_RESULT_CALLBACK_URL,
        wes_event_callback_url=WES_EVENT_CALLBACK_URL,
    )


class SmtArmMockServer:
    def __init__(
        self,
        device_code: str = DEVICE_CODE,
        host: str = "127.0.0.1",
        port: int | None = None,
    ):
        self.device_code = device_code
        self.host = host
        self.port = port or DEVICE_CONFIGS[device_code]["port"]
        self._server: Server | None = None
        self.config = Config(app=app, host=host, port=self.port, log_level="info")

    async def start(self) -> None:
        device_config = DEVICE_CONFIGS[self.device_code]
        logger.info(f"SMT 粗分机机械臂 Mock 服务启动: http://{self.host}:{self.port}")
        logger.info(f"设备编码: {self.device_code}")
        logger.info(f"设备名称: {device_config['device_name']}")
        logger.info(f"设备描述: {device_config['description']}")
        logger.info(f"结果回调地址: {WES_RESULT_CALLBACK_URL}")
        logger.info(f"事件回调地址: {WES_EVENT_CALLBACK_URL}")
        logger.info("正式接口:")
        logger.info("  - GET  /api/v1/device/status")
        logger.info("  - POST /api/v1/device/command")
        logger.info("  - POST /api/v1/device/cancel")
        logger.info("调试接口:")
        logger.info("  - POST /debug/execute")
        logger.info("  - POST /debug/scan-completed")
        logger.info("  - POST /debug/inspection-completed")
        logger.info("  - POST /debug/auto/start")
        logger.info("  - POST /debug/auto/stop")
        logger.info("  - GET  /debug/executions")
        self._server = Server(self.config)
        await self._server.serve()

    def run(self) -> None:
        asyncio.run(self.start())


if __name__ == "__main__":
    server = SmtArmMockServer()
    server.run()
