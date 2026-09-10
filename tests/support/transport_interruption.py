"""只在独占测试 worker 中启用的退出屏障，生产代码不包含故障入口。"""

import os
from pathlib import Path


def install_before_http_crash() -> None:
    from src.app.wms_adapter.transport_adapter import WmsTransportAdapter

    marker = Path(os.environ["TRANSPORT_TEST_CRASH_BEFORE_HTTP"])

    async def exit_before_http(self, **kwargs):
        # Service 的 claim/send_started 事务已提交；未发生 HTTP 也必须保持交付歧义。
        marker.write_text(kwargs["operation_id"])  # noqa: ASYNC240 - 强制退出前同步落测试屏障，不能在此切换协程。
        os._exit(17)

    WmsTransportAdapter.submit = exit_before_http
