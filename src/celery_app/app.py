# ============================================
# Celery 应用实例 - P9 WES Backend
# ============================================
# 用途: Celery 应用配置和初始化
# ============================================

from typing import Any, cast

from celery import Celery  # pyright: ignore[reportMissingTypeStubs]
from celery.signals import (  # pyright: ignore[reportMissingTypeStubs]
    beat_init,
    worker_init,
    worker_process_init,
    worker_process_shutdown,
)

from src.core.conf import settings
from src.core.logger import setup_logger

from . import config
from .async_runtime import celery_async_runtime

# ============================================
# Celery 应用实例
# ============================================

celery_app = Celery(
    "wes_backend",
    broker=settings.CELERY_BROKER,
    backend=settings.CELERY_BACKEND,
    include=[
        "src.celery_app.tasks.core",  # 核心任务
        "src.celery_app.tasks.handling",  # 系统级 Handling 任务
        "src.celery_app.tasks.runtime_inbox",  # RuntimeInbox 主链路任务 (Plan Task 6)
        "src.celery_app.tasks.sys",  # 系统级统一任务
        "src.celery_app.tasks.workline",  # 作业线编排任务
    ],
)

# ============================================
# Worker 启动信号处理器
# ============================================


@worker_init.connect
def on_worker_init(*args: Any, **kwargs: Any) -> None:
    """Worker 主进程只初始化日志，禁止创建可被 fork 继承的异步资源。"""
    setup_logger()  # 初始化 loguru + 抑制 pidbox/kombu/amqp 噪音


@worker_process_init.connect
def on_worker_process_init(*args: Any, **kwargs: Any) -> None:
    """Worker 子进程启动时初始化基础设施（fork 后独立初始化）"""
    # 子进程 fork 后 _initialized 标志被继承为 True，需要重置以重新配置日志
    import src.core.logger as _logger_module

    _logger_module._initialized = False
    setup_logger()
    celery_async_runtime.initialize()


@worker_process_shutdown.connect
def on_worker_process_shutdown(*args: Any, **kwargs: Any) -> None:
    """Worker 子进程退出时按阶段关闭其唯一异步运行时。"""
    celery_async_runtime.shutdown()


@beat_init.connect
def on_beat_init(*args: Any, **kwargs: Any) -> None:
    """Beat 启动时初始化日志"""
    setup_logger()


# ============================================
# Celery 配置
# ============================================

cast("Any", celery_app.conf).update(
    # ================================
    # 任务配置
    # ================================
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.DATETIME_TIMEZONE,
    enable_utc=True,
    # ================================
    # 任务执行配置
    # ================================
    task_always_eager=False,  # 生产环境设为 False
    task_eager_propagates=True,
    task_ignore_result=False,  # 保存结果以便 Flower 显示任务历史
    # ================================
    # 任务路由
    # ================================
    task_routes=config.task_routes,
    # ================================
    # 任务重试配置
    # ================================
    task_acks_late=True,  # 任务执行后才确认 (防止任务丢失)
    worker_prefetch_multiplier=4,  # 预取任务数
    task_max_retries=3,
    task_default_retry_delay=60,  # 重试延迟 (秒)
    # ================================
    # 结果后端配置
    # ================================
    result_expires=3600,  # 结果保留时间 (秒)
    result_compression="gzip",  # 压缩结果
    # ================================
    # Worker 配置
    # ================================
    worker_concurrency=4,  # 并发任务数 (默认: CPU 核心数)
    worker_max_tasks_per_child=1000,  # 每个 Worker 处理的最大任务数
    worker_log_format="[%(asctime)s: %(levelname)s/%(processName)s] %(message)s",
    worker_task_log_format="[%(asctime)s: %(levelname)s/%(processName)s][%(task_name)s(%(task_id)s)] %(message)s",
    worker_hijack_root_logger=False,  # 不劫持根 logger，避免与项目日志配置冲突
    # ================================
    # Beat 调度器配置 (定时任务)
    # ================================
    beat_schedule=cast("dict[str, dict[str, Any]]", config.beat_schedule),
    beat_log_format="[%(asctime)s: %(levelname)s] %(message)s",
    beat_max_loop_interval=5.0,  # Beat 循环间隔 (秒)
    # ================================
    # 安全配置
    # ================================
    broker_connection_retry_on_startup=True,  # 启动时重试连接
    broker_connection_retry=True,  # 自动重试连接
    broker_connection_max_retries=10,  # 最大重试次数
)

# ============================================
# 调试任务
# ============================================


@celery_app.task(name="celery_app.app.debug_task")
def debug_task() -> dict[str, str]:
    """调试任务 - 用于测试 Celery 是否正常工作"""
    print("Celery 任务执行成功!")
    return {"status": "success", "message": "Celery is working!"}


# ============================================
# 导出
# ============================================

__all__ = ["celery_app"]
