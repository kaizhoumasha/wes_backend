"""SMT 粗分机工作线 Mock 服务模块。

用于 SMT 粗分机工作线 E2E 测试的模拟设备服务。

包含服务:
- Pipeline Mock (PIPELINE01): 皮带线模拟，端口 8005
- Arm Mock (ARM01): 进料机械臂模拟，端口 8006
- Arm Mock (ARM02): 出料机械臂模拟，端口 8007

运行方式:
    # 运行所有 Mock 服务
    python tests/mock/smt_classifier/run_all.py

    # 单独运行进料机械臂
    DEVICE_ID=ARM01 python tests/mock/smt_classifier/arm_mock.py

    # 单独运行出料机械臂
    DEVICE_ID=ARM02 python tests/mock/smt_classifier/arm_mock.py
"""

from __future__ import annotations

from typing import Any

__all__ = ["ArmSimulator", "SmtArmMockServer"]


def __getattr__(name: str) -> Any:
    if name in {"ArmSimulator", "SmtArmMockServer"}:
        from .arm_mock import ArmSimulator, SmtArmMockServer

        exports = {
            "ArmSimulator": ArmSimulator,
            "SmtArmMockServer": SmtArmMockServer,
        }
        return exports[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
