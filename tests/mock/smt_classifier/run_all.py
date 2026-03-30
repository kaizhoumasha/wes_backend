"""
SMT 粗分机工作线 Mock 服务启动脚本

同时启动:
- Pipeline Mock (PIPELINE01, port 8005)
- Arm Mock (ARM01, port 8006)
- Arm Mock (ARM02, port 8007)
- Allocation Mock (port 8008)
- AGV Mock (AGV01, port 8009)

运行方式:
    python tests/mock/smt_classifier/run_all.py

环境变量:
    WES_EVENT_CALLBACK_URL: WES 事件回调地址 (默认 http://localhost:8001/api/v1/callback/event)
    WES_RESULT_CALLBACK_URL: WES 结果回调地址 (默认 http://localhost:8001/api/v1/callback/result)
    API_APP_ID: API 应用 ID
    API_APP_SECRET: API 应用密钥
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from uvicorn import Config, Server

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(processName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 全局进程列表
_processes: list[multiprocessing.Process] = []

# Mock 服务配置
MOCK_SERVICES = [
    {
        "name": "Pipeline Mock (PIPELINE01)",
        "module": "tests.mock.smt_classifier.pipeline_mock",
        "app_attr": "app",
        "host": "127.0.0.1",
        "port": 8005,
        "device_code": "PIPELINE01",
    },
    {
        "name": "Arm Mock (ARM01)",
        "module": "tests.mock.smt_classifier.arm_mock",
        "app_attr": "app",
        "host": "127.0.0.1",
        "port": 8006,
        "device_code": "ARM01",
    },
    {
        "name": "Arm Mock (ARM02)",
        "module": "tests.mock.smt_classifier.arm_mock",
        "app_attr": "app",
        "host": "127.0.0.1",
        "port": 8007,
        "device_code": "ARM02",
    },
    {
        "name": "Allocation Mock",
        "module": "tests.mock.smt_classifier.allocation_mock",
        "app_attr": "app",
        "host": "127.0.0.1",
        "port": 8008,
        "device_code": "ALLOCATION",
    },
    {
        "name": "AGV Mock (AGV01)",
        "module": "tests.mock.smt_classifier.agv_mock",
        "app_attr": "app",
        "host": "127.0.0.1",
        "port": 8009,
        "device_code": "AGV01",
    },
]


def run_server(
    module_name: str,
    app_attr: str,
    host: str,
    port: int,
    device_code: str,
    log_level: str = "info",
    env_vars: dict[str, str] | None = None,
) -> None:
    """在子进程中运行 uvicorn 服务器

    Args:
        module_name: 模块名（如 tests.mock.smt_classifier.pipeline_mock）
        app_attr: FastAPI 应用属性名（如 app）
        host: 监听地址
        port: 监听端口
        device_code: 设备编码（用于环境变量传递）
        log_level: 日志级别
        env_vars: 要设置的环境变量字典
    """
    # 首先设置从父进程传递的环境变量（spawn 方式需要）
    if env_vars:
        for key, value in env_vars.items():
            os.environ[key] = value

    # 设置子进程环境变量
    os.environ["DEVICE_CODE"] = device_code

    # 动态导入模块
    import importlib

    module = importlib.import_module(module_name)
    app = getattr(module, app_attr)

    # 创建 uvicorn 配置
    config = Config(
        app=app,
        host=host,
        port=port,
        log_level=log_level,
        access_log=True,
    )

    # 创建并运行服务器
    server = Server(config)

    # 设置信号处理（子进程）
    def handle_signal(signum: int, frame: Any) -> None:
        logger.info(f"收到信号 {signum}，准备关闭服务 {device_code}")
        server.should_exit = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info(f"启动服务 {device_code} -> http://{host}:{port}")
    server.run()


def start_all_services() -> None:
    """启动所有 Mock 服务"""
    logger.info("=" * 60)
    logger.info("SMT 粗分机工作线 Mock 服务启动器")
    logger.info("=" * 60)
    logger.info(f"WES 事件回调地址: {os.getenv('WES_EVENT_CALLBACK_URL', 'http://localhost:8001/api/v1/callback/event')}")
    logger.info(f"WES 结果回调地址: {os.getenv('WES_RESULT_CALLBACK_URL', 'http://localhost:8001/api/v1/callback/result')}")
    logger.info(
        f"WES 外部回调地址: {os.getenv('WES_EXTERNAL_CALLBACK_URL', 'http://localhost:8001/api/v1/callback/external')}"
    )
    logger.info(
        f"库位分配正式接口: {os.getenv('SMT_CLASSIFIER_BIN_ALLOCATION_URL', 'http://127.0.0.1:8008/api/v1/bin-allocation/allocate')}"
    )
    logger.info(
        f"AGV 正式接口: {os.getenv('SMT_CLASSIFIER_AGV_DISPATCH_URL', 'http://127.0.0.1:8009/api/v1/device/command')}"
    )
    logger.info(f"API App ID: {os.getenv('API_APP_ID', '未设置')}")
    logger.info("-" * 60)

    for service in MOCK_SERVICES:
        process = multiprocessing.Process(
            target=run_server,
            args=(
                service["module"],
                service["app_attr"],
                service["host"],
                service["port"],
                service["device_code"],
            ),
            name=service["name"],
        )
        process.start()
        _processes.append(process)
        logger.info(f"已启动进程: {service['name']} (PID: {process.pid})")

    logger.info("-" * 60)
    logger.info(f"所有服务已启动，共 {len(_processes)} 个")
    logger.info("按 Ctrl+C 优雅退出")
    logger.info("=" * 60)


def stop_all_services() -> None:
    """停止所有 Mock 服务"""
    global _processes

    logger.info("=" * 60)
    logger.info("正在停止所有服务...")
    logger.info("-" * 60)

    for process in _processes:
        if process.is_alive():
            logger.info(f"发送 SIGTERM 到进程 {process.name} (PID: {process.pid})")
            process.terminate()

    # 等待进程结束
    for process in _processes:
        process.join(timeout=5)
        if process.is_alive():
            logger.warning(f"进程 {process.name} 未响应，强制终止")
            process.kill()
            process.join()
        else:
            logger.info(f"进程 {process.name} 已停止")

    _processes = []
    logger.info("-" * 60)
    logger.info("所有服务已停止")
    logger.info("=" * 60)


def signal_handler(signum: int, frame: Any) -> None:
    """信号处理器（主进程）"""
    logger.info(f"\n收到信号 {signum}，准备关闭所有服务...")
    stop_all_services()
    sys.exit(0)


def main() -> None:
    """主函数"""
    # 设置信号处理（主进程）
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动所有服务
    start_all_services()

    # 等待所有进程结束
    try:
        # 监控进程状态
        while True:
            # 检查是否有进程意外退出
            for process in _processes:
                if not process.is_alive():
                    if process.exitcode != 0:
                        logger.error(
                            f"进程 {process.name} 意外退出，退出码: {process.exitcode}"
                        )
                    else:
                        logger.info(f"进程 {process.name} 正常退出")

            # 检查是否所有进程都已退出
            if all(not p.is_alive() for p in _processes):
                logger.info("所有服务已退出")
                break

            time.sleep(1)
    except KeyboardInterrupt:
        # Ctrl+C 已被信号处理器捕获
        pass

    logger.info("Mock 服务启动器已退出")


if __name__ == "__main__":
    # 设置 multiprocessing 启动方式（macOS 需要）
    multiprocessing.set_start_method("spawn", force=True)
    main()
