
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles

from .core.conf import settings

def register_logger() -> None:
    """
    注册系统日志管理模块
    """
    # setup_logging()
    # set_customize_logfile()

@asynccontextmanager
async def register_init(_app: FastAPI) -> AsyncIterator[None]:
    """注册初始化"""
    try:
        yield
    except Exception as e:
        raise e
    finally:
        return

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
    print(f"{settings.PROJECT_NAME} v{settings.VERSION}")
    # 挂载静态文件目录
    local_static_path = str(Path(__file__).parent / "static")
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

    # register_middleware(app)

    # register_routers(app)

    # register_exception(app)

    # register_websocket(app)

    return app