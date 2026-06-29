"""标准设备错误码定义。"""

from enum import StrEnum


class DeviceErrorCode(StrEnum):
    """设备执行结果统一错误码。

    约束：
    - 插件业务逻辑只消费这些标准语义码，不消费供应商数字码
    - 供应商原始码若需要保留，只能存在于原始日志/证据层
    - ``NONE`` 是设备 ``error_code`` 字段的成功态哨兵值
    """

    NONE = "NONE"

    SCAN_CODE_INVALID = "SCAN_CODE_INVALID"
    SCAN_CODE_INCOMPLETE = "SCAN_CODE_INCOMPLETE"
    SCAN_FAILED = "SCAN_FAILED"

    PICK_FAILED = "PICK_FAILED"
    PLACE_FAILED = "PLACE_FAILED"
    PICK_AND_PUT_FAILED = "PICK_AND_PUT_FAILED"
    MOVE_FAILED = "MOVE_FAILED"

    TARGET_BLOCKED = "TARGET_BLOCKED"
    BIN_FULL = "BIN_FULL"
    DEVICE_BUSY = "DEVICE_BUSY"
    DEVICE_NOT_READY = "DEVICE_NOT_READY"
    DEVICE_FAULT = "DEVICE_FAULT"
    DEVICE_UNKNOWN_ERROR = "DEVICE_UNKNOWN_ERROR"


__all__ = ["DeviceErrorCode"]
