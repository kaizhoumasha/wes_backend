"""Mock 服务模块

用于 E2E 测试的模拟外部系统服务

使用方式:
    # 直接运行
    python tests/mock/ecs_mock_server.py

    # uvicorn 运行 (Docker 内)
    uvicorn ecs_mock_server:app --host 0.0.0.0 --port 8010

模块:
    - ecs_mock_server: ECS Mock 服务 (端口 8010)
    - wms_mock_server: WMS Mock 服务 (端口 8011)
"""

from tests.mock.ecs_mock_server import EcsMockServer
from tests.mock.ecs_mock_server import app as ecs_app
from tests.mock.wms_mock_server import app as wms_app

__all__ = [
    "EcsMockServer",
    "ecs_app",
    "wms_app",
]
