"""
设备模拟器 - 用于插件验证测试

模拟以下设备行为：
- 扫码器（Scanner）：发送扫码事件
- 检测传感器（Inspector）：发送检测结果
- 机械臂（Arm）：执行抓取放置命令
- 流水线（Conveyor）：执行传输命令
- NG缓存位（NG Buffer）：接收NG产品
"""

import asyncio
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx


class SimulationScenario(str, Enum):
    """模拟场景"""

    NORMAL_OK = "normal_ok"  # 正常流程：全部OK
    NORMAL_NG = "normal_ng"  # 正常流程：扫码NG
    INSPECTION_NG = "inspection_ng"  # 检测NG
    BARCODE_INVALID = "barcode_invalid"  # 条码无效
    PICK_FAILED = "pick_failed"  # 抓取失败
    CONVEYOR_FAILED = "conveyor_failed"  # 传输失败
    TIMEOUT = "timeout"  # 超时
    RANDOM = "random"  # 随机场景


@dataclass
class SimulationConfig:
    """模拟配置"""

    scenario: SimulationScenario = SimulationScenario.NORMAL_OK
    barcode_ok_ratio: float = 0.8  # 扫码OK比例（仅 RANDOM 场景）
    inspection_ok_ratio: float = 0.9  # 检测OK比例（仅 RANDOM 场景）
    pick_failure_ratio: float = 0.05  # 抓取失败比例（仅 RANDOM 场景）
    timeout_simulation: bool = False  # 是否模拟超时


class DeviceSimulator:
    """设备模拟器"""

    def __init__(
        self,
        base_url: str = "http://localhost:8010",
        api_key: str | None = None,
        config: SimulationConfig | None = None,
    ):
        """
        初始化设备模拟器

        Args:
            base_url: ECS Mock 基础URL
            api_key: API密钥（如需要）
            config: 模拟配置
        """
        self.base_url = base_url
        self.api_key = api_key
        self.config = config or SimulationConfig()

        self.http_client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else None,
            timeout=30.0,
        )

    async def close(self):
        """关闭 HTTP 客户端"""
        await self.http_client.aclose()

    # ========== 扫码器模拟 ==========

    async def send_scan_event(
        self,
        workline_id: int,
        barcode: str | None = None,
        location: str = "ARM01",
    ) -> dict[str, Any]:
        """
        发送扫码事件

        Args:
            workline_id: WorkLine ID (保留参数，仅用于兼容性)
            barcode: 条码（None 则自动生成）
            location: 位置ID

        Returns:
            API 响应
        """
        if barcode is None:
            barcode = self._generate_barcode()

        payload = {
            "device_code": "RS-INPUT-ARM-01",  # 进料机械臂（扫码设备）
            "event_type": "SCAN_COMPLETED",
            "timestamp": int(time.time() * 1000),
            "data": {
                "location": location,
                # 使用完整的 SixInOne 字段（对齐硬件约定）
                "LotCode": barcode,  # 批次码
                "DateCode": "20260409",  # 日期码
                "Qty": "100",  # 数量
                "ProductNo": "PN001",  # 产品PN码
                "MfrPN": "MFR002",  # 制造商PN码
                "PONumber": "PO2026040901",  # 订单码
            },
        }

        # 通过 ECS Mock 的手动事件入口上报，再由 Mock 负责签名回调 WES。
        response = await self.http_client.post(
            "/api/v1/mock/event",
            json=payload,
        )

        return response.json()

    def _generate_barcode(self) -> str:
        """生成随机条码"""
        return f"{''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=10))}"

    def _get_scan_result(self, barcode: str) -> str:
        """获取扫码结果（OK/NG）"""
        match self.config.scenario:
            case SimulationScenario.NORMAL_OK:
                return "OK"
            case SimulationScenario.NORMAL_NG:
                return "NG"
            case SimulationScenario.BARCODE_INVALID:
                return "OK"  # 条码无效由 barcode 长度决定
            case SimulationScenario.RANDOM:
                return "OK" if random.random() < self.config.barcode_ok_ratio else "NG"
            case _:
                return "OK"

    # ========== 检测传感器模拟 ==========

    async def send_inspection_event(
        self,
        workline_id: int,
        barcode: str,
        inspection_result: str | None = None,
    ) -> dict[str, Any]:
        """
        发送检测完成事件

        Args:
            workline_id: WorkLine ID (保留参数，仅用于兼容性)
            barcode: 条码
            inspection_result: 检测结果（None 则自动生成）

        Returns:
            API 响应
        """
        if inspection_result is None:
            inspection_result = self._get_inspection_result()

        payload = {
            "device_code": "RS-INPUT-ARM-01",  # 进料机械臂（扫码+检测）
            "event_type": "SCAN_COMPLETED",  # 检测模拟仍复用扫码完成入口事件
            "timestamp": int(time.time() * 1000),
            "data": {
                "location": "ARM01",  # 检测位置
                "LotCode": barcode,
                "DateCode": "20260409",
                "Qty": "100",
                "ProductNo": "PN001",
                "MfrPN": "MFR002",
                "PONumber": "PO2026040901",
                "inspection_result": inspection_result,
                "reel_diameter": round(random.uniform(200, 220), 2),
            },
        }

        response = await self.http_client.post("/api/v1/mock/event", json=payload)

        return response.json()

    def _get_inspection_result(self) -> str:
        """获取检测结果（OK/NG）"""
        match self.config.scenario:
            case SimulationScenario.INSPECTION_NG:
                return "NG"
            case SimulationScenario.RANDOM:
                return "OK" if random.random() < self.config.inspection_ok_ratio else "NG"
            case _:
                return "OK"

    # ========== 命令结果回调模拟 ==========

    async def send_command_result(
        self,
        workline_id: int,
        command_code: str,
        result: str,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        """
        发送命令结果

        Args:
            workline_id: WorkLine ID (保留参数，仅用于兼容性)
            command_code: 命令代码 (PICK_AND_PUT, MOVE_FORWARD, OUTPUT, PICK_NG)
            result: 结果（SUCCESS/FAILED）
            error_code: 错误代码（如果失败）

        Returns:
            API 响应
        """
        device_code = self._get_device_code(command_code)
        scenario = "fail" if result == "FAILED" else "success"
        await self.http_client.post(f"/api/v1/mock/devices/{device_code}/scenario", json={"scenario": scenario})

        payload = {
            "device_code": device_code,
            "command_code": command_code,
            "task_type": command_code,
            "params": {},
            "timestamp": int(time.time() * 1000),
        }
        if error_code:
            payload["params"]["error_code"] = error_code

        response = await self.http_client.post("/api/v1/device/command", json=payload)

        return response.json()

    def _get_device_code(self, command_type: str) -> str:
        """根据命令类型获取设备代码（与 WorkLine 绑定的设备一致）"""
        device_map = {
            "PICK_AND_PUT": "RS-INPUT-ARM-01",  # 进料机械臂
            "MOVE_FORWARD": "RS-CONVEYOR-01",  # 粗分机流水线
            "OUTPUT": "RS-OUTPUT-ARM-01",  # 出料机械臂
            "PUT_TO_BIN": "RS-OUTPUT-ARM-01",  # 出料机械臂
            "PICK_NG": "RS-INPUT-ARM-01",  # NG 处理使用进料机械臂
        }
        return device_map.get(command_type, "UNKNOWN")

    # ========== 完整流程模拟 ==========

    async def simulate_full_workflow(
        self,
        workline_id: int,
        scenario: SimulationScenario = SimulationScenario.NORMAL_OK,
    ) -> dict[str, Any]:
        """
        模拟完整工作流程

        Args:
            workline_id: WorkLine ID
            scenario: 模拟场景

        Returns:
            工作流程结果
        """
        self.config.scenario = scenario
        results = []

        try:
            # 1. 发送扫码事件
            if scenario == SimulationScenario.BARCODE_INVALID:
                barcode = "X"  # 无效条码（太短）
            else:
                barcode = self._generate_barcode()

            scan_result = await self.send_scan_event(workline_id, barcode)
            results.append({"step": "scan", "result": scan_result})

            if scenario == SimulationScenario.BARCODE_INVALID:
                return {"scenario": scenario, "results": results, "status": "failed"}

            # 2. 模拟命令执行（机械臂抓取）
            if scenario == SimulationScenario.PICK_FAILED:
                # 发送抓取失败结果
                await asyncio.sleep(0.5)
                await self.send_command_result(
                    workline_id,
                    "PICK_AND_PUT",
                    "FAILED",
                    "ARM_ERROR",
                )
                return {"scenario": scenario, "results": results, "status": "failed"}

            # 发送抓取成功结果
            await asyncio.sleep(0.5)
            await self.send_command_result(workline_id, "PICK_AND_PUT", "SUCCESS")
            results.append({"step": "pick", "result": "success"})

            # 3. 发送检测事件
            if scenario == SimulationScenario.INSPECTION_NG:
                inspection_result = "NG"
            else:
                inspection_result = self._get_inspection_result()

            inspection_result = await self.send_inspection_event(workline_id, barcode, inspection_result)
            results.append({"step": "inspection", "result": inspection_result})

            # 4. 模拟命令执行（流水线或NG缓存）
            await asyncio.sleep(0.5)

            if scenario == SimulationScenario.INSPECTION_NG:
                # NG流程
                await self.send_command_result(workline_id, "PICK_NG", "SUCCESS")
                results.append({"step": "ng_pick", "result": "success"})
            else:
                # OK流程
                if scenario == SimulationScenario.CONVEYOR_FAILED:
                    await self.send_command_result(
                        workline_id,
                        "MOVE_FORWARD",
                        "FAILED",
                        "CONVEYOR_ERROR",
                    )
                    return {"scenario": scenario, "results": results, "status": "failed"}

                await self.send_command_result(workline_id, "MOVE_FORWARD", "SUCCESS")
                results.append({"step": "conveyor", "result": "success"})

                # 最终出料
                await asyncio.sleep(0.5)
                await self.send_command_result(workline_id, "OUTPUT", "SUCCESS")
                results.append({"step": "output", "result": "success"})

            return {"scenario": scenario, "results": results, "status": "completed"}

        except Exception as e:
            return {"scenario": scenario, "results": results, "status": "error", "error": str(e)}

    # ========== 批量测试模拟 ==========

    async def run_batch_test(
        self,
        traditional_workline_id: int,
        simplified_workline_id: int,
        scenario: SimulationScenario,
        count: int = 10,
    ) -> dict[str, Any]:
        """
        批量运行测试场景

        Args:
            traditional_workline_id: 传统插件 WorkLine ID
            simplified_workline_id: 简化插件 WorkLine ID
            scenario: 测试场景
            count: 测试次数

        Returns:
            对比结果
        """
        traditional_results = []
        simplified_results = []

        for i in range(count):
            print(f"Running test {i + 1}/{count}...")

            # 并行运行两个插件
            traditional_task = self.simulate_full_workflow(traditional_workline_id, scenario)
            simplified_task = self.simulate_full_workflow(simplified_workline_id, scenario)

            trad_result, simp_result = await asyncio.gather(traditional_task, simplified_task)

            traditional_results.append(trad_result)
            simplified_results.append(simp_result)

            # 等待一段时间，避免过快
            await asyncio.sleep(1.0)

        return {
            "scenario": scenario,
            "count": count,
            "traditional": traditional_results,
            "simplified": simplified_results,
        }


# ========== 便捷函数 ==========


async def create_simulator(
    base_url: str = "http://localhost:8010",
    api_key: str | None = None,
) -> DeviceSimulator:
    """创建设备模拟器"""
    return DeviceSimulator(base_url=base_url, api_key=api_key)


__all__ = [
    "DeviceSimulator",
    "SimulationConfig",
    "SimulationScenario",
    "create_simulator",
]
