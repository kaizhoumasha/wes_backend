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

各 Mock 镜像是独立部署单元，因此包入口不能预加载其它服务及其运行时依赖。
调用方应显式导入所需的子模块，例如 ``from tests.mock import wms_mock_server``。
"""
