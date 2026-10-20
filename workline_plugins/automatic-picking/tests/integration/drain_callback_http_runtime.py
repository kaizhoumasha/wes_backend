"""只在离线验收子进程挂载真实 WMS 回调路由，不加载生产插件组合根。"""

import inspect
from contextlib import asynccontextmanager

from drain_wait_worker_runtime import record
from fastapi import Depends, FastAPI

from src.app.transport.composition import build_transport_runtime
from src.app.wms_adapter.inbound_auth import WmsInboundAuthPolicy
from src.app.wms_adapter.v1.events import receive_wms_event, router
from src.core.conf import settings
from src.database import db
from src.utils.background_tasks import inject_background_tasks


@asynccontextmanager
async def lifespan(app):
    await db.init_db()
    runtime = None
    try:
        runtime = await build_transport_runtime(
            wms_base_url=settings.WMS_BASE_URL,
            transport_submit_path=settings.TRANSPORT_SUBMIT_PATH,
            session_factory=db.AsyncSessionLocal,
        )
        app.state.transport_runtime = runtime
        app.state.wms_inbound_auth_policy = WmsInboundAuthPolicy()
        record("http_ready", route_source=inspect.getfile(receive_wms_event))
        yield
    finally:
        if runtime is not None:
            await runtime.client.aclose()
        await db.close_db()


app = FastAPI(lifespan=lifespan, dependencies=[Depends(inject_background_tasks)])
app.include_router(router, prefix="/api/v1/wms")
