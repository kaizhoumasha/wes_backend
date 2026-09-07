"""执行能力的共享策略值；修改后重启相关 API、worker 和 Beat。"""

from datetime import timedelta

WMS_CONFIRMATION_BATCH_LIMIT = 100
# 仅覆盖请求持久化与接收响应，不包含 WMS 后台业务处理。
WMS_CONFIRMATION_DISPATCH_WINDOW = timedelta(seconds=30)
