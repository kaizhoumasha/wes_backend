"""
测试数据生成器 - 为插件验证生成测试数据

支持生成：
- 各种场景的扫码事件
- 不同检测结果的检测事件
- 错误场景数据
"""

import random
from dataclasses import dataclass
from enum import Enum
from typing import Any


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
    """扫码事件数据"""

    device_code: str = "SCANNER01"
    event_type: str = "SCAN_COMPLETED"
    barcode: str = "ABC123"
    location_id: str = "LOC01"
    scan_result: str = "OK"

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "device_code": self.device_code,
            "event_type": self.event_type,
            "barcode": self.barcode,
            "location": self.location_id,
            "scan_result": self.scan_result,
        }


@dataclass
class InspectionEventData:
    """检测事件数据"""

    device_code: str = "INSPECTOR01"
    event_type: str = "INSPECTION_COMPLETED"
    barcode: str = "ABC123"
    inspection_result: str = "OK"
    reel_diameter: float = 210.5

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "device_code": self.device_code,
            "event_type": self.event_type,
            "barcode": self.barcode,
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
        生成条码

        Args:
            valid: 是否生成有效条码

        Returns:
            条码字符串
        """
        if valid:
            # 有效条码：3-10位字母数字
            length = random.randint(3, 10)
            return "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=length))
        else:
            # 无效条码：包含特殊字符或太短
            invalid_type = random.choice(["too_short", "special_chars", "empty"])
            match invalid_type:
                case "too_short":
                    return "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=2))
                case "special_chars":
                    return f"ABC{''.join(random.choices('!@#$%^&*()', k=3))}123"
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
                    barcode=self.generate_barcode(valid=True),
                    scan_result="OK",
                )
            case TestDataScenario.VALID_BARCODE_NG:
                return ScanEventData(
                    barcode=self.generate_barcode(valid=True),
                    scan_result="NG",
                )
            case TestDataScenario.BARCODE_TOO_SHORT:
                return ScanEventData(
                    barcode=self.generate_barcode(valid=False)[:2],
                    scan_result="OK",
                )
            case TestDataScenario.BARCODE_SPECIAL_CHARS:
                return ScanEventData(
                    barcode=f"ABC{random.choice('!@#$%^&*()')}123",
                    scan_result="OK",
                )
            case TestDataScenario.BARCODE_EMPTY:
                return ScanEventData(
                    barcode="",
                    scan_result="OK",
                )
            case TestDataScenario.RANDOM_MIXED:
                is_valid = random.random() < 0.8
                return ScanEventData(
                    barcode=self.generate_barcode(valid=is_valid),
                    scan_result="OK" if random.random() < 0.9 else "NG",
                )
            case _:
                return ScanEventData()

    def generate_inspection_event(
        self,
        barcode: str,
        ok_ratio: float = 0.9,
    ) -> InspectionEventData:
        """
        生成检测事件数据

        Args:
            barcode: 条码
            ok_ratio: OK结果比例

        Returns:
            检测事件数据
        """
        return InspectionEventData(
            barcode=barcode,
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

    @staticmethod
    def scenario_s005_barcode_special_chars() -> ScanEventData:
        """S005: 条码特殊字符"""
        return TestDataGenerator().generate_scan_event(
            TestDataScenario.BARCODE_SPECIAL_CHARS
        )


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
            # 有效条码
            barcode = generator.generate_barcode(valid=True)
            scan_result = "OK" if random.random() < ok_ratio else "NG"
        else:
            # 无效条码
            barcode = generator.generate_barcode(valid=False)
            scan_result = "OK"

        events.append(
            ScanEventData(
                barcode=barcode,
                scan_result=scan_result,
            ).to_dict()
        )

    return events


# ========== 导出 ==========


__all__ = [
    "TestDataGenerator",
    "TestDataScenario",
    "ScanEventData",
    "InspectionEventData",
    "CommandResultData",
    "ScenarioPresets",
    "generate_batch_scan_events",
]
