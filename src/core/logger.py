"""
Loguru 日志配置模块

提供统一的日志配置，集成 FastAPI 和标准 logging
自动从请求上下文中注入 request_id，实现链路追踪
"""

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from loguru import logger as _logger

from .conf import settings
from .path_conf import BasePath

if TYPE_CHECKING:
    from loguru import Record
else:

    class Record(TypedDict):
        extra: dict[Any, Any]


DEBUG_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>[{extra[request_id]}]</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)
DEFAULT_CONSOLE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | [{extra[request_id]}] | {message}"
FILE_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | [{extra[request_id]}] | {name}:{function}:{line} | {message}"
)
DEBUG_LOGGER_LEVELS = {
    "uvicorn": logging.INFO,
    "uvicorn.access": logging.WARNING,
    "uvicorn.error": logging.ERROR,
    "sqlalchemy": logging.WARNING,
    "sqlalchemy.engine": logging.INFO,
    "sqlalchemy.pool": logging.WARNING,
    "httpx": logging.INFO,
}
DEFAULT_LOGGER_LEVELS = {
    "uvicorn": logging.INFO,
    "uvicorn.access": logging.WARNING,
    "uvicorn.error": logging.ERROR,
    "sqlalchemy": logging.WARNING,
    "sqlalchemy.pool": logging.WARNING,
    "httpx": logging.WARNING,
}

# 日志目录
LOG_DIR = Path(BasePath) / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 初始化标志，防止重复配置
_initialized = False


class InterceptHandler(logging.Handler):
    """
    拦截标准 logging 消息并转发到 loguru

    这使得所有使用标准 logging 的库（如 uvicorn、sqlalchemy）
    的日志都能通过 loguru 处理
    """

    def emit(self, record: logging.LogRecord) -> None:
        """转发日志记录到 loguru"""
        try:
            level = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 查找调用者的帧位置
        frame, depth = logging.currentframe(), 0
        while frame and depth < 10:
            filename = frame.f_code.co_filename
            is_logging = filename == logging.__file__
            is_frozen = "importlib" in filename and "_bootstrap" in filename
            if depth > 0 and not (is_logging or is_frozen):
                break
            frame = frame.f_back
            depth += 1

        # 使用 opt(depth=depth) 正确显示调用位置
        _logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def add_request_id_filter(record: Record) -> bool:
    """
    日志过滤器：自动从上下文注入 request_id

    这个函数会在每条日志输出时自动调用，
    从 starlette-context 中获取当前请求的 request_id

    如果上下文不存在（非请求场景），使用 "SYSTEM" 作为默认值
    """
    if "request_id" not in record["extra"]:
        try:
            from .context import RequestContext

            record["extra"]["request_id"] = RequestContext.get_request_id()
        except (ImportError, RuntimeError):
            # context 模块不可用或不在请求上下文中
            record["extra"]["request_id"] = "SYSTEM"
    return True


def _configure_standard_loggers(is_debug: bool) -> None:
    logger_levels = DEBUG_LOGGER_LEVELS if is_debug else DEFAULT_LOGGER_LEVELS
    for logger_name, logger_level in logger_levels.items():
        logging.getLogger(logger_name).setLevel(logger_level)


def setup_logger() -> None:
    """
    配置 loguru 日志系统

    功能：
    1. 移除默认 handler
    2. 添加标准 logging 拦截器
    3. 配置控制台输出（彩色/简单格式）
    4. 配置文件输出（轮转、压缩、序列化）
    5. 自动注入 request_id 实现链路追踪

    注意：使用内部标志防止重复初始化
    """
    global _initialized

    if _initialized:
        return

    # 移除 loguru 默认的 stderr handler
    _logger.remove()

    is_debug = settings.APP_DEBUG
    log_level = "DEBUG" if is_debug else settings.LOG_LEVEL
    rotation_size = settings.LOG_ROTATION_SIZE
    rotation_day = settings.LOG_RETENTION_DAYS
    log_compression = settings.LOG_COMPRESSION

    if is_debug:
        _ = _logger.add(
            sys.stderr,
            format=DEBUG_CONSOLE_FORMAT,
            level=log_level,
            colorize=True,
            backtrace=True,
            diagnose=True,
            filter=add_request_id_filter,
        )
    else:
        _ = _logger.add(
            sys.stderr,
            format=DEFAULT_CONSOLE_FORMAT,
            level=log_level,
            colorize=False,
            filter=add_request_id_filter,
        )

    _ = _logger.add(
        LOG_DIR / "app.log",
        format=FILE_LOG_FORMAT,
        level=log_level,
        rotation=rotation_size,
        retention=rotation_day,
        compression=log_compression,
        encoding="utf-8",
        enqueue=False,  # 禁用多进程队列，避免 Python 3.13 + macOS 信号量问题
        backtrace=True,
        diagnose=is_debug,
        filter=add_request_id_filter,
    )

    _ = _logger.add(
        LOG_DIR / "error.log",
        format=FILE_LOG_FORMAT,
        level="ERROR",
        rotation=rotation_size,
        retention=rotation_day,
        compression=log_compression,
        encoding="utf-8",
        enqueue=False,  # 禁用多进程队列，避免 Python 3.13 + macOS 信号量问题
        backtrace=True,
        diagnose=True,
        filter=add_request_id_filter,
    )

    if not is_debug and settings.LOG_JSON_OUTPUT:
        _ = _logger.add(
            LOG_DIR / "structured.json",
            serialize=True,
            level=log_level,
            rotation=rotation_size,
            retention=rotation_day,
            compression=log_compression,
            enqueue=False,  # 禁用多进程队列，避免 Python 3.13 + macOS 信号量问题
            filter=add_request_id_filter,
        )

    logging.basicConfig(
        handlers=[InterceptHandler()],
        level=0,
        force=True,
    )

    _configure_standard_loggers(is_debug)

    _initialized = True

    # 输出系统信息
    # 注意：此时上下文不存在，filter 会自动使用 "SYSTEM"
    _logger.info(f"项目路径: {BasePath}")
    _logger.info(f"日志系统已初始化 - 调试模式: {is_debug}")


# 导出 logger 供外部使用
# 这个 logger 会自动从上下文获取 request_id
logger = _logger


__all__ = ["InterceptHandler", "logger", "setup_logger"]
