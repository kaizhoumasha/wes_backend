
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles

from src.core.path_conf import STATIC_DIR
from src.core.logger import logger

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
    from src.database.db import close_db, init_db
    from src.database.redis_client import close_redis, init_redis

    try:
        logger.info("Initializing application resources...")
        await init_db()
        await init_redis()
        
        logger.info(f"Swagger DOC：http://{settings.APP_HOST}:{settings.APP_PORT}{settings.DOCS_URL}")
        yield
    except Exception as e:
        # exc_info=True 捕获完整堆栈跟踪
        # 开发模式下 loguru 会自动显示详细变量值（diagnose=True）
        logger.error(
            f"FastAPI 初始化失败: {e}",
        )
        raise
    finally:
        await close_db()
        await close_redis()
        logger.info("FastAPI 应用关闭")

def register_middleware(app: FastAPI) -> None:
    """注册中间件"""
    # GZip
    from fastapi.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    
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
            allow_credentials=True,
            allow_methods=['*'],
            allow_headers=['*'],
            expose_headers=settings.CORS_EXPOSE_HEADERS,
        )

def register_app() -> FastAPI:
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
        default_response_class=ORJSONResponse,
        lifespan=register_init,
    )

    # 挂载静态文件目录
    local_static_path = STATIC_DIR
    app.mount("/static", StaticFiles(directory=local_static_path), name="static")

    # 自定义 Swagger UI 路由
    @app.get(settings.DOCS_URL, include_in_schema=False)
    async def custom_swagger_ui_html() -> HTMLResponse:
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
    async def swagger_ui_redirect() -> HTMLResponse:
        from fastapi.openapi.docs import get_swagger_ui_oauth2_redirect_html

        return get_swagger_ui_oauth2_redirect_html()

    register_middleware(app)

    # register_routers(app)

    # register_exception(app)

    # register_websocket(app)

    @app.get("/log_test")
    async def log_test():
        """测试日志链路追踪"""
        logger.info("log_test 日志测试")
        logger.debug("调试信息")
        logger.warning("警告信息")
        logger.error("错误信息")
        return {"status": "healthy", "service": "P9 WES Backend"}

    return app