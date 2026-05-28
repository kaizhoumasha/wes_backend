"""Mock 服务模块

用于 E2E 测试的模拟设备服务

使用方式:
    # 直接运行
    python tests/mock/camera_mock_server.py

    # uvicorn 运行 (Docker 内)
    uvicorn camera_mock_server:app --host 0.0.0.0 --port 8003
    uvicorn robot_arm_mock_server:app --host 0.0.0.0 --port 8004

模块:
    - camera_mock_server: 摄像头 Mock 服务 (端口 8003)
    - robot_arm_mock_server: 机械臂 Mock 服务 (端口 8004)
"""

from tests.mock.camera_mock_server import CameraMockServer
from tests.mock.camera_mock_server import app as camera_app
from tests.mock.robot_arm_mock_server import RobotArmMockServer
from tests.mock.robot_arm_mock_server import app as robot_arm_app

__all__ = [
    "CameraMockServer",
    "RobotArmMockServer",
    "camera_app",
    "robot_arm_app",
]
