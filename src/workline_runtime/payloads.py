"""
插件共享Payload定义

提供常用的Pydantic Payload模型，供所有插件复用。
避免重复定义，确保类型安全。

使用示例：
    from src.workline_runtime.payloads import ScanEventPayload

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, event: ScanEventPayload):
        # event.barcode 自动可用，类型安全
        ...
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

# ==================== Six-In-One 公共模型 ====================


class SixInOne(BaseModel):
    """六合一码数据 - 硬件商约定

    约定来源：SMT 粗分机接口调用说明书 v2.0 (2026-03-21)

    不变量：
    - 至少一个字段非空（硬件商保证）
    - 字段值不含连字符、空格等特殊字符（扫描仪固件限制，isalnum()）
    - LotCode 是主批次码，其他字段是辅助追溯信息

    Usage:
        # 获取条码字段列表
        fields = SixInOne.barcode_fields()
    """

    # 条码字段列表
    BARCODE_FIELDS: ClassVar[tuple[str, ...]] = ("LotCode", "DateCode", "Qty", "ProductNo", "MfrPN", "PONumber")

    @classmethod
    def barcode_fields(cls) -> tuple[str, ...]:
        """获取条码字段列表"""
        return cls.BARCODE_FIELDS

    LotCode: str | None = Field(default=None, description="批次码")
    DateCode: str | None = Field(default=None, description="日期码")
    Qty: str | None = Field(default=None, description="数量")
    ProductNo: str | None = Field(default=None, description="产品PN码")
    MfrPN: str | None = Field(default=None, description="制造商PN码")
    PONumber: str | None = Field(default=None, description="订单码")


# ==================== 基础Payload ====================


class DeviceEventPayload(BaseModel):
    """设备事件Payload基类"""

    device_code: str
    event_type: str


class CommandResultPayload(BaseModel):
    """命令结果Payload基类"""

    command_code: str
    result: str  # "SUCCESS" or "FAILED"
    error_code: str | None = None
    error_message: str | None = None


# ==================== 扫码相关Payload ====================


class ScanEventPayload(DeviceEventPayload):
    """扫码完成事件Payload

    Attributes:
        device_code: 扫码设备编码
        event_type: 事件类型（应为 "SCAN_COMPLETED"）
        barcode: 条码内容
        location_id: 扫码位置（支持字段别名 "location"）
        scan_time: 扫码时间（可选）
    """

    barcode: str
    location_id: str = Field(alias="location")
    scan_time: str | None = None


# ==================== 检测相关Payload ====================


class InspectionEventPayload(DeviceEventPayload):
    """检测完成事件Payload

    Attributes:
        device_code: 检测设备编码
        event_type: 事件类型（如 "INSPECTION_COMPLETED"）
        inspection_result: 检测结果（"OK" 或 "NG"）
        reel_diameter: 卷径（可选）
        reel_thickness: 卷厚（可选）
    """

    inspection_result: str  # "OK" or "NG"
    reel_diameter: float | None = None
    reel_thickness: float | None = None


# ==================== 机械臂相关Payload ====================


class PickPlaceResultPayload(CommandResultPayload):
    """抓取放置命令结果Payload

    Attributes:
        command_code: 命令编码
        result: 执行结果（"SUCCESS" 或 "FAILED"）
        error_code: 错误码（可选）
        actual_location: 实际放置位置（可选）
    """

    actual_location: str | None = None
    placed_barcode: str | None = None


# ==================== 流水线相关Payload ====================


class ConveyorMoveResultPayload(CommandResultPayload):
    """流水线移动结果Payload

    Attributes:
        command_code: 命令编码
        result: 执行结果（"SUCCESS" 或 "FAILED"）
        final_position: 最终位置（可选）
    """

    final_position: str | None = None


# ==================== AGV相关Payload ====================


class AGVTaskResultPayload(CommandResultPayload):
    """AGV任务结果Payload

    Attributes:
        command_code: 任务编码
        result: 执行结果（"SUCCESS" 或 "FAILED"）
        task_id: AGV任务ID
        bin_id: 料箱ID（可选）
    """

    task_id: str
    bin_id: str | None = None
    rack_id: str | None = None


# ==================== 外部系统相关Payload ====================


class ExternalHTTPPayload(BaseModel):
    """外部HTTP回调Payload基类

    Attributes:
        callback_type: 回调类型
        request_id: 请求ID
        data: 回调数据（字典）
    """

    callback_type: str
    request_id: str
    data: dict[str, object] = Field(default_factory=dict)


class MESInspectionResultPayload(ExternalHTTPPayload):
    """MES检测系统回调Payload

    Attributes:
        callback_type: 回调类型（应为 "MES_INSPECTION_RESULT"）
        request_id: 请求ID
        inspection_result: 检测结果（"OK" 或 "NG"）
        reel_diameter: 卷径（可选）
        reel_thickness: 卷厚（可选）
    """

    inspection_result: str
    reel_diameter: float | None = None
    reel_thickness: float | None = None


class WCSTaskResultPayload(ExternalHTTPPayload):
    """WCS任务系统回调Payload

    Attributes:
        callback_type: 回调类型（应为 "WCS_TASK_RESULT"）
        request_id: 请求ID
        task_result: 任务结果（"COMPLETED" 或 "FAILED"）
        bin_id: 分配的料箱ID（可选）
    """

    task_result: str
    bin_id: str | None = None


# ==================== 人工操作相关Payload ====================


class ManualOperationPayload(BaseModel):
    """人工操作Payload

    Attributes:
        operation_type: 操作类型（"HOLD", "RESUME", "CANCEL"）
        operator_id: 操作员ID
        reason: 原因（可选）
    """

    operation_type: str  # "HOLD", "RESUME", "CANCEL"
    operator_id: str
    reason: str | None = None


# ==================== 超时相关Payload ====================


class TimeoutPayload(BaseModel):
    """超时事件Payload

    Attributes:
        session_id: 会话ID
        timeout_type: 超时类型
        waited_seconds: 等待秒数
    """

    session_id: int
    timeout_type: str
    waited_seconds: int


__all__ = [
    "AGVTaskResultPayload",
    "CommandResultPayload",
    "ConveyorMoveResultPayload",
    "DeviceEventPayload",
    "ExternalHTTPPayload",
    "InspectionEventPayload",
    "MESInspectionResultPayload",
    "ManualOperationPayload",
    "PickPlaceResultPayload",
    "ScanEventPayload",
    "TimeoutPayload",
    "WCSTaskResultPayload",
]
