import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.core.logger import logger
from src.core.openapi import generate_route_operation_id
from src.core.path_conf import STATIC_DIR
from src.utils.background_tasks import inject_background_tasks

from .core.conf import settings


def register_logger() -> None:
    """
    注册系统日志管理模块
    """
    from .core.logger import setup_logger

    setup_logger()


@asynccontextmanager
async def register_init(_app: FastAPI) -> AsyncIterator[None]:
    """注册初始化"""
    from src.core.task_queue_gateway import task_queue_gateway
    from src.database import db as db_module
    from src.database.db import close_db, init_db
    from src.database.redis_client import close_redis, init_redis

    transport_runtime = None
    device_command_runtime = None
    rough_sorter_runtime = None
    wms_data_lane_runtime = None
    wms_effect_preparation_runtime = None
    primary_error: BaseException | None = None
    try:
        logger.info("Initializing application resources...")
        from src.app.runtime.orchestration.repositories.northbound_operations_repository import (
            northbound_operations_repository,
        )
        from src.app.runtime.system_capabilities.wms.provider_catalog import validate_wms_transport_configuration
        from src.app.transport.composition import validate_transport_runtime_profile
        from src.app.wms_adapter import WmsInboundAuthPolicy

        # 先清空前一轮 lifecycle 可能遗留的策略；profile 编译失败时必须 fail closed。
        _app.state.wms_inbound_auth_policy = None
        _app.state.transport_runtime = None
        _app.state.device_evidence_service = None
        _app.state.rough_sorter_runtime = None
        _app.state.workline_start_service = None
        _app.state.task_queue_gateway = task_queue_gateway
        _app.state.wms_inbound_event_handler = None
        startup = validate_wms_transport_configuration(settings_source=settings)
        validate_transport_runtime_profile(startup)
        wms_inbound_auth_policy = WmsInboundAuthPolicy.from_compiled_profile(startup.compiled_profile)
        _app.state.wms_inbound_auth_policy = wms_inbound_auth_policy
        northbound_operations_repository.bind_provider_catalog(startup.catalog)
        await init_db()
        if db_module.AsyncSessionLocal is None:
            raise RuntimeError("Database session factory is unavailable after initialization")

        from src.app.wms_adapter.inbound_event_handler import InboundEventEvidenceRecorder, InboundEventHandler

        _app.state.wms_inbound_event_handler = InboundEventHandler(
            InboundEventEvidenceRecorder(
                db_module.AsyncSessionLocal,
                task_queue_gateway=task_queue_gateway,
            )
        )

        from src.app.transport.composition import build_transport_runtime

        transport_runtime = await build_transport_runtime(
            startup=startup,
            session_factory=db_module.AsyncSessionLocal,
        )
        _app.state.transport_runtime = transport_runtime

        from src.app.device.composition import (
            build_device_command_runtime,
            resolve_device_command_runtime_config,
        )

        device_config = resolve_device_command_runtime_config()
        device_command_runtime = build_device_command_runtime(
            session_factory=db_module.AsyncSessionLocal,
            timeout_seconds=device_config.timeout_seconds,
            task_queue_gateway=task_queue_gateway,
        )
        _app.state.device_evidence_service = device_command_runtime.evidence_service
        from deployment.rough_sorter_composition import build_rough_sorter_runtime, build_rough_sorter_start_service

        rough_sorter_runtime = build_rough_sorter_runtime(
            session_factory=db_module.AsyncSessionLocal,
            transport_runtime=transport_runtime,
            device_command_service=device_command_runtime.command_service,
        )
        _app.state.rough_sorter_runtime = rough_sorter_runtime
        _app.state.workline_start_service = build_rough_sorter_start_service()
        await init_redis()

        from src.app.wms_integration.effect_preparation_runtime import (
            bind_wms_effect_preparation_runtime,
            build_wms_effect_preparation_runtime,
        )
        from src.app.wms_integration.query_runtime import (
            bind_wms_data_lane_query_runtime,
            build_wms_data_lane_query_runtime,
        )

        # 一个进程/事件循环/data lane 只持有一个长期 client；attempt/page 仅借用。
        wms_data_lane_runtime = build_wms_data_lane_query_runtime(startup, settings_source=settings)
        bind_wms_data_lane_query_runtime(wms_data_lane_runtime)
        _app.state.wms_data_lane_query_runtime = wms_data_lane_runtime

        effect_preparation_candidate = build_wms_effect_preparation_runtime(
            catalog=startup.catalog,
            admission_enabled=settings.WMS_EFFECT_ADMISSION_ENABLED,
        )
        bind_wms_effect_preparation_runtime(effect_preparation_candidate)
        wms_effect_preparation_runtime = effect_preparation_candidate
        _app.state.wms_effect_preparation_runtime = wms_effect_preparation_runtime

        # 初始化系统健康状态缓存（乐观初始化，后续由 health_check 任务纠正）
        from src.core.health import system_health

        system_health.update(db_ok=True, redis_ok=True, celery_ok=True)

        from src.app.runtime.orchestration.observability import configure_runtime_open_telemetry_backend

        if configure_runtime_open_telemetry_backend(service_name=settings.PROJECT_NAME, environment=settings.APP_ENV):
            logger.info("Runtime OpenTelemetry backend configured")

        logger.info(f"Swagger DOCS: http://{settings.APP_HOST}:{settings.APP_PORT}{settings.DOCS_URL}")
        yield
    except BaseException as exc:
        primary_error = exc
        if isinstance(exc, Exception):
            # exc_info=True 捕获完整堆栈跟踪
            # 开发模式下 loguru 会自动显示详细变量值（diagnose=True）
            logger.error(
                f"FastAPI 初始化失败: {exc}",
            )
        raise
    finally:
        from src.app.runtime.orchestration.observability import runtime_observability_registry

        _app.state.wms_inbound_auth_policy = None
        _app.state.transport_runtime = None
        _app.state.device_evidence_service = None
        _app.state.rough_sorter_runtime = None
        _app.state.workline_start_service = None
        _app.state.task_queue_gateway = None
        _app.state.wms_inbound_event_handler = None
        cleanup_errors: list[BaseException] = []
        try:
            await asyncio.to_thread(runtime_observability_registry.close)
        except BaseException as exc:
            cleanup_errors.append(exc)
            logger.warning(f"FastAPI observability 清理失败（继续）: type={type(exc).__name__}, error={exc!r}")
        if wms_data_lane_runtime is not None:
            from src.app.wms_integration.query_runtime import close_bound_wms_data_lane_query_runtime

            try:
                await close_bound_wms_data_lane_query_runtime()
            except BaseException as exc:
                cleanup_errors.append(exc)
                logger.warning(f"FastAPI WMS data lane 清理失败（继续）: type={type(exc).__name__}, error={exc!r}")
        if wms_effect_preparation_runtime is not None:
            from src.app.wms_integration.effect_preparation_runtime import close_wms_effect_preparation_runtime

            try:
                await close_wms_effect_preparation_runtime(wms_effect_preparation_runtime)
            except BaseException as exc:
                cleanup_errors.append(exc)
                logger.warning(
                    f"FastAPI WMS effect preparation 清理失败（继续）: type={type(exc).__name__}, error={exc!r}"
                )
        if transport_runtime is not None:
            try:
                await transport_runtime.aclose()
            except BaseException as exc:
                cleanup_errors.append(exc)
                logger.warning(f"FastAPI Transport 清理失败（继续）: type={type(exc).__name__}, error={exc!r}")
        if device_command_runtime is not None:
            try:
                await device_command_runtime.aclose()
            except BaseException as exc:
                cleanup_errors.append(exc)
                logger.warning(f"FastAPI DeviceCommand 清理失败（继续）: type={type(exc).__name__}, error={exc!r}")
        try:
            await close_db()
        except BaseException as exc:
            cleanup_errors.append(exc)
            logger.warning(f"FastAPI database 清理失败（继续）: type={type(exc).__name__}, error={exc!r}")
        try:
            await close_redis()
        except BaseException as exc:
            cleanup_errors.append(exc)
            logger.warning(f"FastAPI Redis 清理失败（继续）: type={type(exc).__name__}, error={exc!r}")
        logger.info("FastAPI 应用关闭")
        if primary_error is None and cleanup_errors:
            raise cleanup_errors[0]


def register_middleware(app: FastAPI) -> None:
    """注册中间件"""
    # GZip
    from fastapi.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # 方案A: 注册请求限流中间件（最大500并发）
    from .middleware.rate_limit import RateLimitMiddleware

    app.add_middleware(RateLimitMiddleware, max_concurrent=200)

    # 注册性能监控中间件
    from .middleware.performance import PerformanceMiddleware

    app.add_middleware(PerformanceMiddleware, slow_request_threshold=1000)

    # 注册请求日志中间件
    # RequestLogMiddleware 内部使用 request_cycle_context 自主管理上下文
    # 确保 request_id 在整个请求周期内可用
    from .middleware.request_log import RequestLogMiddleware

    app.add_middleware(RequestLogMiddleware)

    # CORS
    # https://github.com/fastapi-practices/fastapi_best_architecture/pull/789/changes
    # https://github.com/open-telemetry/opentelemetry-python-contrib/issues/4031
    from starlette.middleware.cors import CORSMiddleware

    if settings.MIDDLEWARE_CORS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ALLOWED_ORIGINS,
            allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=settings.CORS_EXPOSE_HEADERS,
        )


def register_routers(app: FastAPI) -> None:
    """注册路由"""
    from src.app.admin import router_v1 as admin_router
    from src.app.api_auth import router_v1 as api_auth_router
    from src.app.auth import router_v1 as auth_router
    from src.app.callback import router_v1 as callback_router
    from src.app.device import router_v1 as device_router
    from src.app.material import router_v1 as material_router
    from src.app.resource import router_v1 as resource_router
    from src.app.sys import router_v1 as sys_router
    from src.app.wms_adapter import router_v1 as wms_adapter_router
    from src.app.workline import router_v1 as workline_router

    app.include_router(auth_router, prefix=settings.API_PATH)
    app.include_router(admin_router, prefix=settings.API_PATH)
    app.include_router(sys_router, prefix=settings.API_PATH)
    app.include_router(workline_router, prefix=settings.API_PATH)
    app.include_router(device_router, prefix=settings.API_PATH)
    app.include_router(resource_router, prefix=settings.API_PATH)
    app.include_router(material_router, prefix=settings.API_PATH)
    app.include_router(api_auth_router, prefix=settings.API_PATH)
    app.include_router(callback_router, prefix=settings.API_PATH)
    app.include_router(wms_adapter_router, prefix=settings.API_PATH)


def register_exception(app: FastAPI) -> None:
    """注册全局异常处理器"""
    from .core.error_handlers import register_exception_handlers

    register_exception_handlers(app)


def register_health_route(app: FastAPI) -> None:
    """注册公共健康检查路由。"""
    from src.core.health import system_health

    def _basic_health_payload() -> dict[str, str]:
        return {
            "status": "ok",
            "service": settings.PROJECT_NAME,
            "version": settings.VERSION,
        }

    @app.get("/health", include_in_schema=False)
    async def health_check() -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        """
        基础活性检查（无状态、零 I/O、无鉴权）。

        用于 Docker / 负载均衡 / 外部探针快速确认 API 进程是否存活，
        不依赖数据库、Redis、Celery 或进程内健康缓存状态。
        """
        return JSONResponse(status_code=200, content=_basic_health_payload())

    @app.get("/ready", include_in_schema=False)
    async def readiness_check() -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        """
        就绪检查（基于进程内健康缓存）。

        由 Celery health_check 任务异步更新缓存，适合运维观察和详细排障。
        """
        is_stale = system_health.is_stale
        is_ready = system_health.is_ready

        if is_stale:
            status = "stale"
            status_code = 200
        elif is_ready:
            status = "healthy"
            status_code = 200
        else:
            status = "unhealthy"
            status_code = 503

        return JSONResponse(
            status_code=status_code,
            content={
                "status": status,
                "ready": is_ready,
                "stale": is_stale,
                "components": {
                    "database": system_health.db_ok,
                    "redis": system_health.redis_ok,
                    "celery": system_health.celery_ok,
                },
                "version": settings.VERSION,
            },
        )


def create_app() -> FastAPI:
    from fastapi.openapi.docs import get_swagger_ui_html

    static_path = "/static"
    swagger_js_url = f"{static_path}/swagger-ui/swagger-ui-bundle.js"
    swagger_css_url = f"{static_path}/swagger-ui/swagger-ui.css"

    register_logger()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description=settings.DESCRIPTION,
        docs_url=None,  # 设置为 None，使用自定义路由
        redoc_url=settings.REDOC_URL,
        openapi_url=settings.OPENAPI_URL,
        # 移除 default_response_class=ORJSONResponse
        # FastAPI 0.135+ 推荐直接返回 Pydantic 模型，由 Pydantic 在 Rust 层面序列化
        # 性能优于 ORJSONResponse
        lifespan=register_init,
        generate_unique_id_function=generate_route_operation_id,
        # 全局依赖：自动为所有路由注入 BackgroundTasks 到上下文
        # 使得 Repository 层可以透明地使用后台任务功能
        dependencies=[Depends(inject_background_tasks)],
    )

    # 挂载静态文件目录
    local_static_path = STATIC_DIR
    app.mount("/static", StaticFiles(directory=local_static_path), name="static")

    register_health_route(app)

    # 自定义 Swagger UI 路由
    @app.get(settings.DOCS_URL, include_in_schema=False)
    async def custom_swagger_ui_html() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        openapi_url = app.openapi_url
        if openapi_url is None:
            openapi_url = settings.OPENAPI_URL

        return get_swagger_ui_html(
            openapi_url=openapi_url,
            title=f"{app.title} - Swagger UI",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
            swagger_js_url=swagger_js_url,
            swagger_css_url=swagger_css_url,
            swagger_ui_parameters={
                "docExpansion": "none",
                "defaultModelsExpandDepth": 0,
                "persistAuthorization": True,
                "displayRequestDuration": True,
                "filter": True,
                "tryItOutEnabled": True,
                "syntaxHighlight.theme": "monokai",
            },
        )

    # 添加 OAuth2 重定向路由
    @app.get(f"{settings.DOCS_URL}/oauth2-redirect", include_in_schema=False)
    async def swagger_ui_redirect() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        from fastapi.openapi.docs import get_swagger_ui_oauth2_redirect_html

        return get_swagger_ui_oauth2_redirect_html()

    register_middleware(app)

    # 注册路由
    register_routers(app)

    register_exception(app)

    return app
