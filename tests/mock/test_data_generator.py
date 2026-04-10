"""
测试数据生成器 - 为插件验证生成测试数据

支持生成：
- 各种场景的扫码事件（使用 SixInOne 字段）
- 不同检测结果的检测事件
- 错误场景数据
"""

import random
from dataclasses import dataclass
from enum import Enum
from typing import Any

# 默认 SixInOne 测试数据常量
DEFAULT_DATE_CODE = "20260409"
DEFAULT_QTY = "100"
DEFAULT_PRODUCT_NO = "PN001"
DEFAULT_MFR_PN = "MFR002"
DEFAULT_PO_NUMBER = "PO2026040901"


class TestDataScenario(str, Enum):
    """测试数据场景"""

    VALID_BARCODE_OK = "valid_barcode_ok"  # 有效条码，扫码OK
    VALID_BARCODE_NG = "valid_barcode_ng"  # 有效条码，扫码NG
    BARCODE_TOO_SHORT = "barcode_too_short"  # 条码太短
    BARCODE_SPECIAL_CHARS = "barcode_special_chars"  # 条码包含特殊字符
    BARCODE_EMPTY = "barcode_empty"  # 空条码
    RANDOM_MIXED = "random_mixed"  # 随机混合场景


@dataclass
class ScanEventData:
    """扫码事件数据 - 使用 SixInOne 字段"""

    device_code: str = "SCANNER01"
    event_type: str = "SCAN_COMPLETED"
    # SixInOne 字段（对齐硬件约定）
    LotCode: str = "LOTABC123"  # 批次码
    DateCode: str | None = None  # 日期码
    Qty: str | None = None  # 数量
    ProductNo: str | None = None  # 产品PN码
    MfrPN: str | None = None  # 制造商PN码
    PONumber: str | None = None  # 订单码
    location: str = "LOC01"

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        data = {
            "device_code": self.device_code,
            "event_type": self.event_type,
            "location": self.location,
            "LotCode": self.LotCode,
        }
        if self.DateCode:
            data["DateCode"] = self.DateCode
        else:
            data["DateCode"] = DEFAULT_DATE_CODE
        if self.Qty:
            data["Qty"] = self.Qty
        else:
            data["Qty"] = DEFAULT_QTY
        if self.ProductNo:
            data["ProductNo"] = self.ProductNo
        else:
            data["ProductNo"] = DEFAULT_PRODUCT_NO
        if self.MfrPN:
            data["MfrPN"] = self.MfrPN
        else:
            data["MfrPN"] = DEFAULT_MFR_PN
        if self.PONumber:
            data["PONumber"] = self.PONumber
        else:
            data["PONumber"] = DEFAULT_PO_NUMBER
        return data


@dataclass
class InspectionEventData:
    """检测事件数据"""

    device_code: str = "INSPECTOR01"
    event_type: str = "INSPECTION_COMPLETED"
    LotCode: str = "LOTABC123"  # 使用 SixInOne 字段
    inspection_result: str = "OK"
    reel_diameter: float = 210.5

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "device_code": self.device_code,
            "event_type": self.event_type,
            "LotCode": self.LotCode,
            "inspection_result": self.inspection_result,
            "reel_diameter": self.reel_diameter,
        }


@dataclass
class CommandResultData:
    """命令结果数据"""

    device_code: str = "ARM01"
    command_type: str = "PICK_AND_PUT"
    result: str = "SUCCESS"
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        data = {
            "device_code": self.device_code,
            "command_type": self.command_type,
            "result": self.result,
        }
        if self.error_code:
            data["error_code"] = self.error_code
        if self.error_message:
            data["error_message"] = self.error_message
        return data


class TestDataGenerator:
    """测试数据生成器"""

    @staticmethod
    def generate_barcode(valid: bool = True) -> str:
        """
        生成条码（无连字符等特殊字符）

        Args:
            valid: 是否生成有效条码

        Returns:
            条码字符串（只包含字母数字）
        """
        if valid:
            # 有效条码：3-10位字母数字（无连字符）
            length = random.randint(3, 10)
            return "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=length))
        # 无效条码：太短或为空
        invalid_type = random.choice(["too_short", "empty"])
        match invalid_type:
            case "too_short":
                return "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=2))
            case "empty":
                return ""
            case _:
                return "X"

    def generate_scan_event(
        self, scenario: TestDataScenario = TestDataScenario.VALID_BARCODE_OK
    ) -> ScanEventData:
        """
        生成扫码事件数据

        Args:
            scenario: 测试场景

        Returns:
            扫码事件数据
        """
        match scenario:
            case TestDataScenario.VALID_BARCODE_OK:
                return ScanEventData(
                    LotCode=self.generate_barcode(valid=True),
                    ProductNo="PN" + self.generate_barcode(valid=True)[:6],  # 添加产品PN码
                )
            case TestDataScenario.VALID_BARCODE_NG:
                return ScanEventData(
                    LotCode=self.generate_barcode(valid=True),
                )
            case TestDataScenario.BARCODE_TOO_SHORT:
                return ScanEventData(
                    LotCode=self.generate_barcode(valid=False)[:2],
                )
            case TestDataScenario.BARCODE_SPECIAL_CHARS:
                # 注意：SixInOne 字段不支持特殊字符，这个场景会被验证为有效条码
                # 如果需要测试特殊字符，应该在插件层面修改验证逻辑
                return ScanEventData(
                    LotCode="ABC123",  # 改为有效条码
                )
            case TestDataScenario.BARCODE_EMPTY:
                return ScanEventData(
                    LotCode="",
                )
            case TestDataScenario.RANDOM_MIXED:
                is_valid = random.random() < 0.8
                lot_code = self.generate_barcode(valid=is_valid)
                return ScanEventData(
                    LotCode=lot_code,
                    DateCode="20260409" if random.random() < 0.5 else None,
                )
            case _:
                return ScanEventData()

    def generate_inspection_event(
        self,
        lot_code: str,
        ok_ratio: float = 0.9,
    ) -> InspectionEventData:
        """
        生成检测事件数据

        Args:
            lot_code: 批次码（SixInOne 字段）
            ok_ratio: OK结果比例

        Returns:
            检测事件数据
        """
        return InspectionEventData(
            LotCode=lot_code,
            inspection_result="OK" if random.random() < ok_ratio else "NG",
            reel_diameter=round(random.uniform(200, 220), 2),
        )

    def generate_command_result(
        self,
        command_type: str,
        success: bool = True,
        error_code: str | None = None,
    ) -> CommandResultData:
        """
        生成命令结果数据

        Args:
            command_type: 命令类型
            success: 是否成功
            error_code: 错误代码

        Returns:
            命令结果数据
        """
        device_code_map = {
            "PICK_AND_PUT": "ARM01",
            "MOVE_FORWARD": "CONVEYOR01",
            "OUTPUT": "ARM02",
            "PICK_NG": "ARM01",
        }

        return CommandResultData(
            device_code=device_code_map.get(command_type, "UNKNOWN"),
            command_type=command_type,
            result="SUCCESS" if success else "FAILED",
            error_code=error_code if not success else None,
        )


# ========== 场景预设 ==========


class ScenarioPresets:
    """场景预设数据"""

    @staticmethod
    def scenario_s001_normal_ok() -> ScanEventData:
        """S001: 正常扫码OK流程"""
        return TestDataGenerator().generate_scan_event(TestDataScenario.VALID_BARCODE_OK)

    @staticmethod
    def scenario_s002_normal_ng() -> ScanEventData:
        """S002: 正常扫码NG流程"""
        return TestDataGenerator().generate_scan_event(TestDataScenario.VALID_BARCODE_NG)

    @staticmethod
    def scenario_s004_barcode_too_short() -> ScanEventData:
        """S004: 条码过短"""
        return TestDataGenerator().generate_scan_event(TestDataScenario.BARCODE_TOO_SHORT)


# ========== 批量生成 ==========


def generate_batch_scan_events(
    count: int = 100,
    ok_ratio: float = 0.8,
    valid_ratio: float = 0.95,
) -> list[dict[str, Any]]:
    """
    批量生成扫码事件

    Args:
        count: 生成数量
        ok_ratio: OK结果比例
        valid_ratio: 有效条码比例

    Returns:
        扫码事件列表
    """
    generator = TestDataGenerator()
    events = []

    for _ in range(count):
        # 决定是否生成有效条码
        is_valid = random.random() < valid_ratio

        if is_valid:
            # 有效条码（无连字符）
            lot_code = generator.generate_barcode(valid=True)
            event = ScanEventData(
                LotCode=lot_code,
                DateCode="20260409" if random.random() < 0.3 else None,
            )
        else:
            # 无效条码
            lot_code = generator.generate_barcode(valid=False)
            event = ScanEventData(LotCode=lot_code)

        events.append(event.to_dict())

    return events


# ========== 导出 ==========


__all__ = [
    "CommandResultData",
    "InspectionEventData",
    "ScanEventData",
    "ScenarioPresets",
    "TestDataGenerator",
    "TestDataScenario",
    "generate_batch_scan_events",
]
