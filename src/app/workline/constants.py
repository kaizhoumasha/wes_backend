"""Workline 业务常量。"""

import os

# 外部 HTTP 回调进入 Inbox 时使用的消息类型常量。
# 用途：避免 callback / runtime 层散落字符串，保持和 InboxKind.EXTERNAL_HTTP 的业务语义一致。
EXTERNAL_HTTP_INBOX_KIND = "EXTERNAL_HTTP"

# RuntimeIntent 中用于表示“需要发起外部 HTTP 请求”的决策类型。
# 用途：write-back / orchestration 层识别该 effect，并转交外部系统调用链路处理。
EXTERNAL_HTTP_DECISION_TYPE = "EXTERNAL_HTTP_REQUEST"

# 设备命令的默认优先级。
# 用途：当插件或编排结果没有显式指定优先级时，为下发到 Outbox 的设备命令提供稳定默认值。
DEFAULT_COMMAND_PRIORITY = 5

# 设备命令默认超时时间，单位：毫秒。
# 用途：当插件或编排结果没有显式指定 timeout 时，作为设备侧执行超时和后续对账的默认窗口。
DEFAULT_COMMAND_TIMEOUT_MS = 300000

# 单条 Inbox 消息处理超时时间，单位：秒。
# 用途：限制 Orchestrator / write-back / reconciliation 单消息处理耗时；超过后标记失败并继续下一条。
# 约束：stale reclaim 的下限必须大于该值，避免仍在正常处理窗口内的消息被其他 worker 抢占。
INBOX_PROCESS_TIMEOUT_SECONDS = 60

# Inbox PROCESSING stale 判定的安全余量，单位：秒。
# 用途：给处理超时、Redis bucket 锁过期、调度抖动和数据库提交留出缓冲，避免崩溃恢复路径产生假失败。
INBOX_PROCESSING_STALE_MARGIN_SECONDS = 30

# Inbox 批处理默认并发度。
# 用途：控制单次 process_inbox_batch 内允许并行处理的 bucket 数；默认 1 保持旧串行语义。
# 配置：可通过 WORKLINE_INBOX_BATCH_PARALLELISM 覆盖；生产开启 2-4 前必须跑 benchmark gate。
WORKLINE_INBOX_BATCH_PARALLELISM = int(os.getenv("WORKLINE_INBOX_BATCH_PARALLELISM", "1"))

# Inbox 批处理允许的最大并发度。
# 用途：对环境变量和 Celery task kwarg 做上限保护，避免 DB pool、Redis 锁和设备冲突域被配置放大。
WORKLINE_INBOX_BATCH_MAX_PARALLELISM = 4

# Redis Inbox bucket 锁 TTL，单位：秒。
# 用途：跨 worker 串行化同一 session/device/workline bucket；TTL 覆盖一个最大并发 wave 的处理窗口。
# 约束：WORKLINE_INBOX_PROCESSING_STALE_SECONDS 必须大于等于该 TTL 加安全余量，
# 避免 worker 崩溃后 stale reclaim 早于旧 Redis 锁过期，
# 导致 reclaim worker 拿锁失败并误消耗重试次数。
INBOX_BUCKET_LOCK_TTL_SECONDS = INBOX_PROCESS_TIMEOUT_SECONDS * (WORKLINE_INBOX_BATCH_MAX_PARALLELISM + 1) + 60

# Inbox PROCESSING stale 回收阈值，单位：秒。
# 用途：允许 worker 崩溃后，其他 worker 重新 claim 长时间停留在 PROCESSING 的消息。
# 配置：可通过 WORKLINE_INBOX_PROCESSING_STALE_SECONDS 提高；代码会钳住最小安全值。
# 约束：不得低于 Redis bucket 锁 TTL + 安全余量，否则崩溃恢复时可能先 reclaim、后拿锁失败。
WORKLINE_INBOX_PROCESSING_STALE_SECONDS = max(
    int(os.getenv("WORKLINE_INBOX_PROCESSING_STALE_SECONDS", "300")),
    INBOX_BUCKET_LOCK_TTL_SECONDS + INBOX_PROCESSING_STALE_MARGIN_SECONDS,
)

__all__ = [
    "DEFAULT_COMMAND_PRIORITY",
    "DEFAULT_COMMAND_TIMEOUT_MS",
    "EXTERNAL_HTTP_DECISION_TYPE",
    "EXTERNAL_HTTP_INBOX_KIND",
    "INBOX_BUCKET_LOCK_TTL_SECONDS",
    "INBOX_PROCESSING_STALE_MARGIN_SECONDS",
    "INBOX_PROCESS_TIMEOUT_SECONDS",
    "WORKLINE_INBOX_BATCH_MAX_PARALLELISM",
    "WORKLINE_INBOX_BATCH_PARALLELISM",
    "WORKLINE_INBOX_PROCESSING_STALE_SECONDS",
]
