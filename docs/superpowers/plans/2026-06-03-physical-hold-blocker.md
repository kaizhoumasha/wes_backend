<!-- /autoplan restore point: /Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/develop-autoplan-restore-20260603-202227.md -->
# ECS Admission Resource Wait Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 WorkLine 设备命令只在 ECS 实时状态确认 `AUTO/IDLE/current_command_id=None` 时下发；设备忙或状态预检暂不可用时，队首 outbox 进入可观测资源等待，并保持同设备严格串行。

**Architecture:** 硬件/ECS 是物理防呆和指令接纳的权威来源，WES 不建立位置码 physical blocker 或影子物理占位模型。WES 在 outbox dispatcher 中维护设备级 FIFO、资源等待元数据、ECS admission probe 和 TTL 升级；本地 `DeviceStatus` 只用于诊断和业务追踪，不作为放行事实。

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy async, Alembic, pytest, uv, GitNexus.

---

## Scope Check

本计划只处理设备命令下发前的 ECS admission gate 和 outbox 资源等待状态机：

- 覆盖 `DEVICE_BUSY`: ECS 实时状态非 `AUTO/IDLE/current_command_id=None` 时，当前 outbox park 为 `BLOCKED_RESOURCE`，不消耗普通 retry budget。
- 覆盖 `DEVICE_STATUS_PRECHECK_WAIT`: ECS `/device/status` timeout、非 200、JSON 解析失败或响应结构不可识别时，当前 outbox park 为通信/状态等待，并在 TTL/check-count 超限后升级 runtime diagnostic/reconciliation。
- 覆盖 `BLOCKED_RESOURCE` 队首重新探测：同设备队首 blocked outbox 必须能被 dispatcher 重新选中做 ECS probe；IDLE 时领取为 `DISPATCHING` 并进入现有 POST 流程，非 IDLE 时保持 blocked 并更新诊断。
- 覆盖最小等待诊断字段：等待起点、最近检查时间、检查次数、状态摘要 JSON。
- 覆盖 runtime/trace 查询展示 blocked reason、等待时长、检查次数和诊断摘要。

明确不做：

- 不新增 `PhysicalBlockerService`。
- 不新增 `PHYSICAL_POSITION_BLOCKED`。
- 不新增 `blocked_location_code` / `blocked_owner_session_id`。
- 不从 `MANUAL_HOLD + context_json.phase` 推导物理占位。
- 不新增 `PositionClaim` / `ResourceLock` / 多缓冲库存表。
- 不改变 WMS/ECS mock catalog 方案。
- 不改变 API -> Service -> Repository -> Database 分层。

## File Structure

- Modify: `src/app/sys/models/outbox.py`
  - 给 `SystemOutbox` 增加设备级资源等待诊断字段。
  - 增加支持 blocked 队首扫描和诊断查询的索引。
- Create: `migrations/versions/<generated>_add_system_outbox_resource_wait_metadata.py`
  - 通过 Alembic generator 生成 revision，再编辑列和索引。
- Modify: `src/app/sys/repositories/outbox_repository.py`
  - 增加统一资源等待写入、观测更新、队首 blocked 选择、blocked -> dispatching 领取方法。
  - 保证同设备 FIFO 不跳过队首 blocked outbox。
  - 调整资源等待恢复语义，避免盲目清零真实普通 retry 历史。
- Modify: `src/app/workline/services/device_command_gateway.py`
  - 将 ECS status precheck 从 `bool` 结果升级为可区分 ready、device busy、status wait 的结果/异常契约。
  - 保留 POST 前单设备实时 ECS status 检查。
- Modify: `src/app/workline/services/outbox_dispatch_service.py`
  - 在 batch dispatch 开始时处理 blocked 队首 ECS probe。
  - 将 `DEVICE_BUSY` 和 `DEVICE_STATUS_PRECHECK_WAIT` 写入统一资源等待元数据。
  - 移除或降级本地 `DeviceStatus.IDLE` 自动 release blocked outbox 的语义。
  - TTL/check-count 超限时记录 runtime diagnostic/reconciliation。
- Modify: `src/app/workline/models/runtime.py`
  - 给 trace outbox item 和 runtime device projection 增加可选等待诊断字段。
- Modify: `src/app/workline/services/runtime_query_service.py`
  - 汇总 active blocked outbox 的等待状态，计算等待秒数。
- Modify: `src/app/workline/services/trace_response_builder.py`
  - Trace outbox evidence 暴露新增阻塞诊断字段。
- Test: `tests/workline_runtime/test_outbox_repository.py`
  - 覆盖资源等待字段、队首 blocked 选择、claim、防跳过、retry history。
- Test: `tests/workline_runtime/test_device_command_gateway.py`
  - 覆盖 ECS ready/busy/status unavailable 预检契约。
- Test: `tests/workline_runtime/test_outbox_dispatch_service.py`
  - 覆盖 blocked 队首探测、IDLE 派发、BUSY 保持等待、status wait TTL 升级、本地投影不放行。
- Test: `tests/workline_runtime/test_runtime_query_service.py`
  - 覆盖 runtime device blocked projection。
- Test: `tests/workline_runtime/test_trace_query_service.py`
  - 覆盖 trace outbox blocked diagnostics。

## Business Contract

### 权威来源

ECS `/device/status` 是设备命令接纳的唯一权威来源。只有以下条件全部成立，WES 才能向设备 POST 命令：

- `mode == "AUTO"`
- `status == "IDLE"` 或 ECS 响应中等价设备状态字段为 `IDLE`
- `current_command_id is None`

WES 本地 `DeviceStatus.IDLE`、callback projection、session status 只用于诊断和业务追踪，不得单独让 blocked outbox 回到可派发状态。

### Outbox 状态机

```text
NEW
  -> dispatcher claims -> DISPATCHING
  -> ECS precheck READY -> POST command -> SENT / ACK flow
  -> ECS precheck BUSY -> BLOCKED_RESOURCE(reason=DEVICE_BUSY)
  -> ECS precheck unavailable -> BLOCKED_RESOURCE(reason=DEVICE_STATUS_PRECHECK_WAIT)

BLOCKED_RESOURCE at device FIFO head
  -> probe ECS READY -> atomically claim DISPATCHING -> POST command
  -> probe ECS BUSY -> stay BLOCKED_RESOURCE, update check metadata
  -> probe unavailable under TTL -> stay BLOCKED_RESOURCE, update check metadata
  -> probe unavailable over TTL -> runtime diagnostic/reconciliation, keep FIFO safe
```

### 资源等待原因

| Reason | Meaning | Release / Next Step |
| --- | --- | --- |
| `DEVICE_BUSY` | ECS status reachable but target device is not admissible | Next dispatcher probe retries ECS status; IDLE then dispatches |
| `DEVICE_STATUS_PRECHECK_WAIT` | ECS status endpoint unavailable, non-200, bad JSON, or invalid shape | Probe until TTL/check-count, then escalate diagnostic/reconciliation |

No `PHYSICAL_POSITION_BLOCKED` reason exists in v1.

### 诊断字段

Add only these fields to `SystemOutbox`:

- `blocked_at: datetime | None`
- `last_blocked_check_at: datetime | None`
- `blocked_check_count: int`
- `blocked_detail_json: dict[str, Any]`

`blocked_detail_json` must be a small whitelisted summary. It may include:

- `device_code`
- `status_url`
- `observed_mode`
- `observed_status`
- `observed_current_command_id`
- `http_status`
- `error_kind`
- `error_message`
- `last_probe_result`
- `ttl_seconds`
- `max_check_count`

Do not store raw vendor response bodies or large payloads in `blocked_detail_json`.

### TTL / Escalation Defaults

Use explicit constants in `outbox_dispatch_service.py` or a nearby service-local constants block:

- `RESOURCE_WAIT_PROBE_MIN_INTERVAL_SECONDS = 2`
- `DEVICE_STATUS_PRECHECK_MAX_WAIT_SECONDS = 120`
- `DEVICE_STATUS_PRECHECK_MAX_CHECK_COUNT = 30`

If either max wait or max check count is exceeded for `DEVICE_STATUS_PRECHECK_WAIT`, record a visible diagnostic/reconciliation event and keep the outbox blocked. Do not mark it ordinary `FAILED` just to let later same-device outboxes bypass it.

## Task 1: 建立最小资源等待元数据模型和迁移

**Files:**
- Modify: `src/app/sys/models/outbox.py`
- Modify: `src/app/sys/repositories/outbox_repository.py`
- Create: `migrations/versions/<generated>_add_system_outbox_resource_wait_metadata.py`
- Test: `tests/workline_runtime/test_outbox_repository.py`

- [ ] **Step 1: GitNexus impact**

Run:

```bash
npx gitnexus impact --target SystemOutbox --direction upstream
npx gitnexus impact --target SystemOutboxRepository --direction upstream
```

Expected: 记录 direct callers、affected processes、risk level。若 HIGH/CRITICAL，先向用户汇报风险再继续。

- [ ] **Step 2: 写失败测试：资源等待字段写入**

在 `tests/workline_runtime/test_outbox_repository.py` 增加测试 `test_block_for_resource_wait_writes_minimal_diagnostics`，断言：

- `mark_as_blocked_by_device_busy()` 或新统一方法写入 `blocked_at`。
- 首次 park 写入 `last_blocked_check_at`。
- 首次 park 写入 `blocked_check_count == 1`。
- `blocked_detail_json` 包含 `device_code` 和 `last_probe_result`。
- 不存在 `blocked_location_code` 或 `blocked_owner_session_id` 断言。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_repository.py::test_block_for_resource_wait_writes_minimal_diagnostics -q
```

Expected: FAIL，当前模型缺少新增字段或 repository 未写入字段。

- [ ] **Step 3: 写失败测试：重复 probe 更新观测但保留起点**

新增测试 `test_block_for_resource_wait_preserves_blocked_at_and_increments_checks`，断言：

- 同一 outbox 第二次进入资源等待时 `blocked_at` 不变。
- `last_blocked_check_at` 更新。
- `blocked_check_count` 从 1 递增到 2。
- `blocked_detail_json.last_probe_result` 被最新探测摘要覆盖。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_repository.py::test_block_for_resource_wait_preserves_blocked_at_and_increments_checks -q
```

Expected: FAIL。

- [ ] **Step 4: 生成 Alembic revision**

Run:

```bash
uv run alembic revision -m "add system outbox resource wait metadata"
```

Expected: `migrations/versions/` 下生成一个新 revision 文件。不要手写 revision ID。

- [ ] **Step 5: 编辑模型和迁移**

在 `SystemOutboxBase` 增加字段：

- `blocked_at`
- `last_blocked_check_at`
- `blocked_check_count`
- `blocked_detail_json`

迁移增加同名列。索引策略：

- 保留或调整现有 `ix_system_outbox_blocked_release`，继续覆盖 `blocked_reason`、`blocked_device_id`、`blocked_workline_id`。
- 增加适合 blocked 队首探测的组合索引，至少覆盖 `status`、`dispatch_type`、`blocked_reason`、`blocked_device_id`、`target_code`、`created_at`。
- 不增加位置码或 owner session 相关索引。

时间写入继续使用 `timezone.now_for_db()`，不要对 naive datetime 直接 `.timestamp()`。

- [ ] **Step 6: 实现 repository 写入 helper**

在 `SystemOutboxRepository` 中增加或改造资源等待 helper，接口语义固定为：

- 输入：`outbox_id`、`reason`、`blocked_device_id`、`blocked_workline_id`、`last_error`、`detail`。
- 只允许 active outbox 进入 blocked。
- 资源等待不增加普通 `attempt_count`。
- `blocked_at` 首次写入后不覆盖。
- `_clear_block()` 清空新增诊断字段。

- [ ] **Step 7: 运行本任务测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_repository.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交本任务**

Run:

```bash
git add src/app/sys/models/outbox.py src/app/sys/repositories/outbox_repository.py migrations/versions tests/workline_runtime/test_outbox_repository.py
git commit -m "feat(workline): 记录 outbox 设备资源等待诊断"
```

Expected: commit 成功；如果执行策略要求统一提交，可暂不提交但必须保留清晰 diff。

## Task 2: Repository 支持 blocked 队首探测与原子领取

**Files:**
- Modify: `src/app/sys/repositories/outbox_repository.py`
- Test: `tests/workline_runtime/test_outbox_repository.py`

- [ ] **Step 1: GitNexus impact**

Run:

```bash
npx gitnexus impact --target get_pending_messages --direction upstream
npx gitnexus impact --target mark_as_dispatching --direction upstream
npx gitnexus impact --target release_blocked_by_device --direction upstream
```

Expected: 记录风险。若 HIGH/CRITICAL，先汇报再继续。

- [ ] **Step 2: 写失败测试：blocked 队首可被选中**

新增测试 `test_get_probeable_blocked_device_heads_returns_oldest_blocked_per_device`，构造：

- 同设备较早 outbox: `BLOCKED_RESOURCE/DEVICE_BUSY`
- 同设备较晚 outbox: `NEW`
- 另一设备 outbox: `NEW`

断言：

- 新 repository 方法返回较早 blocked outbox。
- 同设备较晚 `NEW` 不被返回为可派发消息。
- 另一设备 `NEW` 仍可由现有 `get_pending_messages()` 返回。
- 同一物理设备在一个 outbox 有 `device_id`、另一个只写 `target_code` 时，仍按同设备处理，不允许后者越过前者。
- 传入 `operation_domains=("WORKLINE", "RACK")` 时，不返回其它 operation domain 的 blocked outbox。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_repository.py::test_get_probeable_blocked_device_heads_returns_oldest_blocked_per_device -q
```

Expected: FAIL，方法尚不存在。

- [ ] **Step 3: 写失败测试：blocked -> DISPATCHING 原子领取**

新增测试 `test_claim_blocked_resource_wait_for_dispatch_requires_same_status_and_reason`，断言：

- 只有仍处于 `BLOCKED_RESOURCE` 且 reason 匹配的 outbox 可被 claim 为 `DISPATCHING`。
- claim 后设置 `next_retry_at` 为 dispatch lease 截止时间。
- claim 后清理 `blocked_reason`、`blocked_device_id`、`blocked_workline_id` 和新增诊断字段。
- claim 不重置 `attempt_count`。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_repository.py::test_claim_blocked_resource_wait_for_dispatch_requires_same_status_and_reason -q
```

Expected: FAIL。

- [ ] **Step 4: 实现队首 blocked 查询**

新增 repository 方法，命名建议：

- `get_probeable_blocked_device_heads(db, limit: int = 50, min_probe_interval_seconds: int = 2, operation_domains: Sequence[str] | None = None, exclude_operation_domains: Sequence[str] | None = None) -> list[SystemOutbox]`

选择规则：

- `status == BLOCKED_RESOURCE`
- `dispatch_type == DEVICE_COMMAND`
- `blocked_reason in ("DEVICE_BUSY", "DEVICE_STATUS_PRECHECK_WAIT")`
- `last_blocked_check_at is null` 或距离当前时间超过最小 probe interval
- `operation_domains` / `exclude_operation_domains` 语义必须与现有 `get_pending_messages()` 一致，避免 Workline dispatcher 探测或领取其它调度域的 blocked outbox。
- 每个物理设备只返回队首 blocked outbox。物理设备匹配沿用现有 FIFO 规则：优先 `device_id`，无 `device_id` 时用 `target_code`。
- 不返回被更早同设备 active outbox 挡住的 blocked outbox。
- 实现前先建立 `device_id <-> device_code` 解析表；不能只用 `device_id=device_id OR target_code=target_code` 两个孤立条件，否则会漏掉“较早 outbox 有 `device_id`，较晚 outbox 只有 `target_code`”的混合写入场景。

- [ ] **Step 5: 实现 blocked claim**

新增 repository 方法，命名建议：

- `claim_blocked_resource_wait_for_dispatch(db, outbox_id: int, expected_reason: str) -> SystemOutbox | None`

行为：

- `SELECT ... FOR UPDATE` 锁定 outbox。
- fencing 条件不满足时返回 `None`。
- 成功时状态改为 `DISPATCHING`，设置 dispatch lease。
- 清理资源等待字段，但保留 `attempt_count`。

- [ ] **Step 6: 运行 repository 测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_repository.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交本任务**

Run:

```bash
git add src/app/sys/repositories/outbox_repository.py tests/workline_runtime/test_outbox_repository.py
git commit -m "feat(workline): 支持资源等待队首重新探测"
```

Expected: commit 成功，或等待统一提交。

## Task 3: 调整 ECS status precheck 契约

**Files:**
- Modify: `src/app/workline/services/device_command_gateway.py`
- Test: `tests/workline_runtime/test_device_command_gateway.py`

- [ ] **Step 1: GitNexus impact**

Run:

```bash
npx gitnexus impact --target _ensure_realtime_device_status_ready --direction upstream
```

Expected: 记录影响范围。若 HIGH/CRITICAL，先汇报再继续。

- [ ] **Step 2: 写失败测试：ECS ready**

在 `tests/workline_runtime/test_device_command_gateway.py` 增加或更新测试，命名建议 `test_realtime_status_ready_allows_command_dispatch`，断言：

- ECS status response 为 `mode=AUTO,status=IDLE,current_command_id=None` 时，precheck 通过。
- gateway 随后可以进入 POST 分支。

Run:

```bash
uv run pytest tests/workline_runtime/test_device_command_gateway.py::test_realtime_status_ready_allows_command_dispatch -q
```

Expected: PASS 或按新契约调整后 PASS。

- [ ] **Step 3: 写失败测试：ECS busy**

新增测试 `test_realtime_status_busy_raises_device_busy_governance_error`，断言：

- `mode != AUTO`、`status != IDLE`、或 `current_command_id is not None` 时抛出 governance error。
- error code 是 `DEVICE_BUSY`。
- error detail 包含 device code、observed mode/status/current_command_id 摘要。

Run:

```bash
uv run pytest tests/workline_runtime/test_device_command_gateway.py::test_realtime_status_busy_raises_device_busy_governance_error -q
```

Expected: 当前 busy 分支可能部分通过；缺 detail 时 FAIL。

- [ ] **Step 4: 写失败测试：ECS status 不可用**

新增测试 `test_realtime_status_unavailable_raises_precheck_wait_error`，覆盖：

- httpx timeout/connection error
- HTTP non-200
- JSON parse error
- JSON shape 无法提取设备状态

断言 error code 是 `DEVICE_STATUS_PRECHECK_WAIT`，并且不会返回普通 `False`。

Run:

```bash
uv run pytest tests/workline_runtime/test_device_command_gateway.py::test_realtime_status_unavailable_raises_precheck_wait_error -q
```

Expected: FAIL，当前代码返回 `False`。

- [ ] **Step 5: 实现 precheck 契约**

实现约定：

- 保留 `_DeviceCommandGovernanceError(code="DEVICE_BUSY")` 作为设备忙资源等待信号。
- 新增或复用 governance error，code 为 `DEVICE_STATUS_PRECHECK_WAIT`，表示 status admission 事实暂不可得。
- 同一 `current_command_id == command_code` 的自阻塞保护保持现有语义，不重复 POST。
- 不把 status unavailable 混成 `DEVICE_STATUS_PRECHECK_FAILED` 普通 dispatch failure。

- [ ] **Step 6: 运行 gateway 测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_device_command_gateway.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交本任务**

Run:

```bash
git add src/app/workline/services/device_command_gateway.py tests/workline_runtime/test_device_command_gateway.py
git commit -m "fix(workline): 区分设备忙与状态预检等待"
```

Expected: commit 成功，或等待统一提交。

## Task 4: Dispatch 接入资源等待 park 和 blocked 队首探测

**Files:**
- Modify: `src/app/workline/services/outbox_dispatch_service.py`
- Test: `tests/workline_runtime/test_outbox_dispatch_service.py`

- [ ] **Step 1: GitNexus impact**

Run:

```bash
npx gitnexus impact --target OutboxDispatchService --direction upstream
npx gitnexus impact --target _block_outbox_for_device_busy --direction upstream
```

Expected: 记录影响范围。若 HIGH/CRITICAL，先汇报再继续。

- [ ] **Step 2: 写失败测试：NEW outbox 遇到 DEVICE_BUSY park**

新增测试 `test_dispatch_parks_device_busy_without_retry_exhaustion`，断言：

- ECS status 返回 busy。
- 不调用 command POST。
- outbox 进入 `BLOCKED_RESOURCE`。
- `blocked_reason == "DEVICE_BUSY"`。
- `attempt_count` 不增加到普通 retry failure。
- dispatch attempt response 是 `{"result": "blocked_resource", "reason": "DEVICE_BUSY"}` 或等价结构。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_dispatch_parks_device_busy_without_retry_exhaustion -q
```

Expected: FAIL 或当前只覆盖部分行为。

- [ ] **Step 3: 写失败测试：NEW outbox status unavailable park**

新增测试 `test_dispatch_parks_status_precheck_wait_without_retry_exhaustion`，断言：

- ECS status timeout/non-200/bad JSON 时不调用 POST。
- outbox 进入 `BLOCKED_RESOURCE`。
- `blocked_reason == "DEVICE_STATUS_PRECHECK_WAIT"`。
- detail 包含 `error_kind` 和 `device_code`。
- 不调用 `mark_as_failed()`。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_dispatch_parks_status_precheck_wait_without_retry_exhaustion -q
```

Expected: FAIL，当前路径会走普通失败。

- [ ] **Step 4: 写失败测试：blocked 队首 ECS IDLE 后派发**

新增测试 `test_dispatch_probes_blocked_head_and_posts_when_ecs_idle`，断言：

- repository 返回一个 `BLOCKED_RESOURCE/DEVICE_BUSY` 队首 outbox。
- ECS status ready。
- dispatcher 调用 `claim_blocked_resource_wait_for_dispatch()`。
- command POST 被调用一次。
- 同设备较晚 outbox 没有被越过。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_dispatch_probes_blocked_head_and_posts_when_ecs_idle -q
```

Expected: FAIL。

- [ ] **Step 5: 写失败测试：blocked 队首 ECS BUSY 继续等待**

新增测试 `test_dispatch_keeps_blocked_head_when_ecs_still_busy`，断言：

- repository 返回 blocked 队首。
- ECS status busy。
- 不调用 command POST。
- 不 claim 为 `DISPATCHING`。
- 调用资源等待观测更新，`blocked_check_count` 增加。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_dispatch_keeps_blocked_head_when_ecs_still_busy -q
```

Expected: FAIL。

- [ ] **Step 6: 写失败测试：blocked 队首探测遵守 operation domain**

新增测试 `test_dispatch_blocked_head_probe_respects_workline_operation_domains`，断言：

- 存在 `operation_domain="OTHER"` 的 `BLOCKED_RESOURCE/DEVICE_BUSY` 设备命令时，Workline dispatcher 不 probe、不 claim、不 POST。
- 存在 `operation_domain="WORKLINE"` 或 `"RACK"` 的 eligible blocked 队首时，Workline dispatcher 才进入 ECS probe。
- 调用 repository blocked-head 查询时传入 `operation_domains=("WORKLINE", "RACK")`，与现有 `get_pending_messages()` 过滤边界一致。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_dispatch_blocked_head_probe_respects_workline_operation_domains -q
```

Expected: FAIL。

- [ ] **Step 7: 实现 dispatch 资源等待处理**

实现顺序：

1. batch dispatch 开始时先 probe eligible blocked heads，并传入 `operation_domains=("WORKLINE", "RACK")`。
2. 对每个 blocked head 执行 ECS status precheck。
3. ready 时调用 repository claim 方法，再复用现有 command POST 流程。
4. busy/status wait 时调用资源等待 helper 更新 detail，不 POST。
5. blocked probe 之后再调用现有 `get_pending_messages()` 处理普通 `NEW` outbox。

保持现有 safety guard、attempt finalize、trace logging 和 transaction-before-side-effect 约束。

- [ ] **Step 8: 移除本地投影 release 语义**

调整 `_repair_orphaned_device_busy_dispatches()`、operation callback 后 `release_blocked_by_device()` 等路径：

- 本地 `DeviceStatus.IDLE` 不能直接 release `BLOCKED_RESOURCE` 为 `NEW`。
- 如需保留 orphan/self-block 修复，只能用于“同一 command 已被 ECS 接受但 WES 未确认”的幂等修复；普通 release 必须通过下发前 ECS probe。
- 更新现有测试中对 `release_blocked_by_device()` 的断言，改为断言不因本地投影直接放行。

- [ ] **Step 9: 运行 dispatch 测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py tests/workline_runtime/test_device_command_gateway.py tests/workline_runtime/test_outbox_repository.py -q
```

Expected: PASS。

- [ ] **Step 10: 提交本任务**

Run:

```bash
git add src/app/workline/services/outbox_dispatch_service.py tests/workline_runtime/test_outbox_dispatch_service.py
git commit -m "feat(workline): 派发前探测资源等待队首"
```

Expected: commit 成功，或等待统一提交。

## Task 5: `DEVICE_STATUS_PRECHECK_WAIT` TTL 升级

**Files:**
- Modify: `src/app/workline/services/outbox_dispatch_service.py`
- Test: `tests/workline_runtime/test_outbox_dispatch_service.py`
- Optional Modify: `src/app/workline/services/runtime_reconciliation_service.py`

- [ ] **Step 1: GitNexus impact**

Run:

```bash
npx gitnexus impact --target OutboxDispatchService --direction upstream
npx gitnexus impact --target RuntimeReconciliationService --direction upstream
```

If `RuntimeReconciliationService` symbol name differs, first run:

```bash
rg -n "class .*Reconciliation|create_for_resource|RuntimeHold|diagnostic" src/app/workline/services src/app/workline/repositories
```

Expected: 记录影响范围。若 HIGH/CRITICAL，先汇报再继续。

- [ ] **Step 2: 写失败测试：TTL 内继续等待**

新增测试 `test_status_precheck_wait_under_ttl_stays_blocked`，断言：

- blocked reason 为 `DEVICE_STATUS_PRECHECK_WAIT`。
- `blocked_at` 距当前时间未超过 max wait。
- `blocked_check_count` 未超过 max count。
- ECS status 仍不可用时，outbox 保持 `BLOCKED_RESOURCE`。
- 只更新 `last_blocked_check_at`、`blocked_check_count`、detail。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_status_precheck_wait_under_ttl_stays_blocked -q
```

Expected: FAIL。

- [ ] **Step 3: 写失败测试：TTL 超限升级诊断**

新增测试 `test_status_precheck_wait_over_ttl_escalates_runtime_diagnostic`，断言：

- max wait 或 max check count 任一超限。
- dispatcher 记录可见 diagnostic/reconciliation 事件。
- 对同一个 outbox、同一个 timeout reason，重复 dispatch 只复用同一个 active diagnostic 或按稳定 key 幂等，不每轮新增一条诊断。
- outbox 仍保持 `BLOCKED_RESOURCE`，不标记普通 `FAILED`。
- detail 写入 `last_probe_result="escalated"` 或等价摘要。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_status_precheck_wait_over_ttl_escalates_runtime_diagnostic -q
```

Expected: FAIL。

- [ ] **Step 4: 实现 TTL 判断**

实现规则：

- max wait 使用 `blocked_at` 与 `timezone.now_for_db()` 比较，不调用 naive `.timestamp()`。
- max check count 使用 `blocked_check_count`。
- 超限后调用现有 `_record_diagnostic()`，diagnostic code 使用现有 `OUTBOX_DISPATCH_FAILED` 或项目内更精确的 runtime diagnostic code；不要新增未接入查询链路的孤立错误枚举。
- 调用诊断记录时必须传入稳定 trace/entity/request 语义，确保 `WorklineDiagnosticService.build_diagnostic_key()` 能对同一 outbox 的同一类长期 status wait 幂等。
- `blocked_detail_json` 在第一次升级后写入 `escalated_at`、`diagnostic_key` 或等价稳定摘要；后续 probe 只更新 `last_blocked_check_at`、`blocked_check_count` 和 latest observation，不重复创建 active diagnostic。
- 如果现有 runtime reconciliation 创建服务已有可复用入口，可创建 source reason 为 `DEVICE_STATUS_PRECHECK_WAIT_TIMEOUT` 的 hold；如果没有，先记录 persisted diagnostic，不在本计划内发明新 hold 生命周期。

- [ ] **Step 5: 运行 TTL 测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交本任务**

Run:

```bash
git add src/app/workline/services/outbox_dispatch_service.py tests/workline_runtime/test_outbox_dispatch_service.py
git commit -m "fix(workline): 升级长期设备状态预检等待"
```

Expected: commit 成功，或等待统一提交。

## Task 6: Runtime 和 Trace 暴露等待诊断

**Files:**
- Modify: `src/app/workline/models/runtime.py`
- Modify: `src/app/workline/services/runtime_query_service.py`
- Modify: `src/app/workline/services/trace_response_builder.py`
- Test: `tests/workline_runtime/test_runtime_query_service.py`
- Test: `tests/workline_runtime/test_trace_query_service.py`

- [ ] **Step 1: GitNexus impact**

Run:

```bash
npx gitnexus impact --target RuntimeQueryService --direction upstream
npx gitnexus impact --target build_trace_response --direction upstream
```

Expected: 若 HIGH/CRITICAL，先汇报。

- [ ] **Step 2: 写失败测试：Trace outbox 展示诊断**

在 `tests/workline_runtime/test_trace_query_service.py` 新增测试 `test_trace_outbox_includes_resource_wait_diagnostics`，断言 blocked outbox trace item 包含：

- `blocked_at`
- `last_blocked_check_at`
- `blocked_wait_seconds`
- `blocked_check_count`
- `blocked_detail_json`

且不包含：

- `blocked_location_code`
- `blocked_owner_session_id`

Run:

```bash
uv run pytest tests/workline_runtime/test_trace_query_service.py::test_trace_outbox_includes_resource_wait_diagnostics -q
```

Expected: FAIL，response model 缺字段。

- [ ] **Step 3: 写失败测试：Runtime device projection 展示等待摘要**

在 `tests/workline_runtime/test_runtime_query_service.py` 新增测试 `test_runtime_device_projection_includes_resource_wait_summary`，断言：

- 设备存在 active blocked outbox 时，device detail 或 workline detail 的对应 projection 包含 blocked count、reason、wait seconds、check count。
- wait seconds 使用 aware-safe/naive-safe 计算，不抛 datetime 错误。
- detail JSON 只输出白名单摘要。

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_query_service.py::test_runtime_device_projection_includes_resource_wait_summary -q
```

Expected: FAIL。

- [ ] **Step 4: 增加 response model 字段**

在 `TraceOutboxItem` 和 runtime device projection model 上增加可选字段：

- `blocked_at`
- `last_blocked_check_at`
- `blocked_wait_seconds`
- `blocked_check_count`
- `blocked_detail_json`

所有字段可选，保持旧响应兼容。

- [ ] **Step 5: 实现 projection**

实现规则：

- Trace builder 从 outbox 直接读取新增字段。
- Runtime query 按设备聚合 active blocked outbox，优先展示队首 blocked reason。
- `blocked_wait_seconds` 用 `timezone.now_utc()` 或项目已有安全 helper 计算，不对 naive datetime 直接 `.timestamp()`。
- `blocked_detail_json` 输出白名单 key，避免泄露完整 ECS 响应或超大 payload。

- [ ] **Step 6: 运行 runtime/trace 测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_query_service.py tests/workline_runtime/test_trace_query_service.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交本任务**

Run:

```bash
git add src/app/workline/models/runtime.py src/app/workline/services/runtime_query_service.py src/app/workline/services/trace_response_builder.py tests/workline_runtime/test_runtime_query_service.py tests/workline_runtime/test_trace_query_service.py
git commit -m "feat(workline): 暴露设备资源等待诊断"
```

Expected: commit 成功，或等待统一提交。

## Task 7: 粗分机 ECS admission 回归

**Files:**
- Test: `tests/workline_runtime/test_outbox_dispatch_service.py`
- Optional Test: `tests/integration/workline_plugins/test_rough_sorter_plugin_events.py` or nearest existing rough sorter integration test
- Optional Modify: `tests/mock/ecs_mock_server.py`

- [ ] **Step 1: 写 dispatch 级回归测试**

新增测试 `test_rough_sorter_conveyor_command_waits_for_ecs_idle`，用以下业务事实建模：

- input arm 已 callback WES。
- WES 准备发送 conveyor `MOVE_FORWARD`。
- conveyor ECS status 非 IDLE。

断言：

- WES 不 POST `MOVE_FORWARD`。
- outbox 为 `BLOCKED_RESOURCE/DEVICE_BUSY`。
- 后续同 conveyor 命令不越过队首。
- 当 conveyor ECS status 变为 IDLE，队首 outbox 被 probe 并 POST。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_rough_sorter_conveyor_command_waits_for_ecs_idle -q
```

Expected: PASS after Task 4。

- [ ] **Step 2: 写输出机械臂 admission 回归测试**

新增测试 `test_rough_sorter_output_arm_command_waits_for_ecs_idle`，用以下业务事实建模：

- conveyor 已 callback WES。
- WES 准备发送 output arm 指令。
- output arm ECS status 非 IDLE。

断言同上：不 POST、blocked、同设备不跳过、ECS IDLE 后派发。

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_rough_sorter_output_arm_command_waits_for_ecs_idle -q
```

Expected: PASS after Task 4。

- [ ] **Step 3: 如 mock 缺少 status 控制能力，补 ECS mock 测试支撑**

只有当现有 mock 无法表达设备 status 序列时，才修改 `tests/mock/ecs_mock_server.py`：

- 支持测试内配置设备 status 为 BUSY/IDLE。
- 保持现有 catalog 和 callback mock 行为不变。
- 补 `tests/mock/test_ecs_mock_server.py` 覆盖 status response。

Run:

```bash
uv run pytest tests/mock/test_ecs_mock_server.py -q
```

Expected: PASS。

- [ ] **Step 4: 提交本任务**

Run:

```bash
git add tests/workline_runtime/test_outbox_dispatch_service.py tests/mock/ecs_mock_server.py tests/mock/test_ecs_mock_server.py
git commit -m "test(workline): 回归粗分机设备接纳闸门"
```

Expected: commit 成功，或等待统一提交。

## Task 8: 质量门禁与提交前检测

**Files:**
- All modified files

- [ ] **Step 1: 运行相关测试集**

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_repository.py tests/workline_runtime/test_device_command_gateway.py tests/workline_runtime/test_outbox_dispatch_service.py tests/workline_runtime/test_runtime_query_service.py tests/workline_runtime/test_trace_query_service.py tests/mock/test_ecs_mock_server.py -q
```

Expected: PASS。

- [ ] **Step 2: 运行 workline runtime 扩展测试**

Run:

```bash
uv run pytest tests/workline_runtime/ -q
```

Expected: PASS。

- [ ] **Step 3: 运行格式和 lint**

Run:

```bash
uv run ruff format src/app/sys/models/outbox.py src/app/sys/repositories/outbox_repository.py src/app/workline/services/device_command_gateway.py src/app/workline/services/outbox_dispatch_service.py src/app/workline/models/runtime.py src/app/workline/services/runtime_query_service.py src/app/workline/services/trace_response_builder.py tests/workline_runtime tests/mock
uv run ruff check src/app/sys/models/outbox.py src/app/sys/repositories/outbox_repository.py src/app/workline/services/device_command_gateway.py src/app/workline/services/outbox_dispatch_service.py src/app/workline/models/runtime.py src/app/workline/services/runtime_query_service.py src/app/workline/services/trace_response_builder.py tests/workline_runtime tests/mock
```

Expected: PASS。

- [ ] **Step 4: 运行架构违规检查**

Run:

```bash
grep -r "from sqlalchemy import select" src/app/*/v1/ || true
grep -r "db.execute(" src/app/*/v1/ || true
```

Expected: 本次变更不引入 API 直接查库。

- [ ] **Step 5: GitNexus detect changes**

Run:

```bash
npx gitnexus detect-changes
```

Expected: 变更范围仅包含 system outbox resource wait、workline dispatch/device gateway、runtime diagnostics、必要测试与迁移。

- [ ] **Step 6: 最终提交**

如果前面没有逐任务提交，统一提交：

```bash
git add src/app/sys/models/outbox.py src/app/sys/repositories/outbox_repository.py src/app/workline/services/device_command_gateway.py src/app/workline/services/outbox_dispatch_service.py src/app/workline/models/runtime.py src/app/workline/services/runtime_query_service.py src/app/workline/services/trace_response_builder.py migrations/versions tests/workline_runtime tests/mock docs/superpowers/plans/2026-06-03-physical-hold-blocker.md
git commit -m "feat(workline): 增加 ECS 设备接纳资源等待"
```

Expected: commit 成功。

## Acceptance Criteria

- 设备命令 POST 前一定查询 ECS `/device/status`。
- ECS `AUTO/IDLE/current_command_id=None` 时才下发命令。
- ECS status 可达但非 IDLE 时，outbox 进入或保持 `BLOCKED_RESOURCE/DEVICE_BUSY`。
- ECS status 不可用时，outbox 进入或保持 `BLOCKED_RESOURCE/DEVICE_STATUS_PRECHECK_WAIT`。
- `DEVICE_BUSY` 和 `DEVICE_STATUS_PRECHECK_WAIT` 不消耗普通 retry budget，不因等待设备接纳而进入普通 dispatch failed。
- 队首 `BLOCKED_RESOURCE` outbox 会被 dispatcher 重新探测；同设备后续 outbox 不会越过它。
- Workline dispatcher 只探测 `WORKLINE` / `RACK` operation domain 的 blocked 队首，不越权领取其它调度域的 outbox。
- `device_id` 与 `target_code` 混合写入时仍按同一物理设备 FIFO 串行，不会因 identity 表达不同而跳队。
- 本地 WES `DeviceStatus.IDLE` 不会单独 release blocked outbox。
- `DEVICE_STATUS_PRECHECK_WAIT` 超过 TTL/check-count 后有幂等可见 diagnostic/reconciliation，且仍保持 FIFO 安全。
- Runtime/Trace 查询可以看到 blocked reason、等待时长、检查次数和诊断 detail 摘要。
- 计划和实现中不存在 `PhysicalBlockerService`、`PHYSICAL_POSITION_BLOCKED`、`blocked_location_code`、`blocked_owner_session_id`。

## Manual Docker Smoke

在所有应用、MOCK 和基础设施都运行于 Docker 容器的环境中执行：

- 启动服务：

```bash
docker-compose up -d
```

- 应用迁移：

```bash
./scripts/migrate.sh upgrade
```

- 复现 conveyor busy：

```bash
# 通过现有 mock 或测试辅助把 rough sorter conveyor ECS status 设为 BUSY
# 投入一条会触发 MOVE_FORWARD 的物料
```

Expected:

- WES 不向 conveyor POST `MOVE_FORWARD`。
- 目标 outbox 为 `BLOCKED_RESOURCE/DEVICE_BUSY`。
- Runtime/Trace 能看到等待原因和检查次数。

- 恢复 conveyor idle：

```bash
# 将同一 conveyor ECS status 改为 AUTO/IDLE/current_command_id=None
# 触发下一轮 outbox dispatch
```

Expected:

- 队首 blocked outbox 被重新探测。
- WES POST 原 `MOVE_FORWARD` 命令。
- 后续同设备命令仍按创建顺序串行。

- 复现 status unavailable：

```bash
# 让 ECS status endpoint timeout 或返回非 200
# 投入一条目标设备命令
```

Expected:

- outbox 为 `BLOCKED_RESOURCE/DEVICE_STATUS_PRECHECK_WAIT`。
- 超过 TTL/check-count 后产生可见 diagnostic/reconciliation。
- 不出现同设备后续 outbox 越过队首的行为。

## Self-Review

- Spec coverage: 计划覆盖 ECS admission gate、设备忙资源等待、status precheck wait、blocked 队首重新探测、TTL 升级、runtime/trace 诊断和粗分机场景回归。
- Placeholder scan: 本文没有 `TBD`、`TODO`、`implement later` 或“写适当测试”式占位；每个任务都有具体文件、测试名、命令和预期结果。
- Type consistency: 资源等待字段统一为 `blocked_at`、`last_blocked_check_at`、`blocked_check_count`、`blocked_detail_json`；资源等待 reason 只使用 `DEVICE_BUSY`、`DEVICE_STATUS_PRECHECK_WAIT`。

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/autoplan` | Scope & strategy | 1 | REVISED | 旧结论要求显式物理占位证据；用户澄清硬件/ECS IDLE 是接纳权威，正文已改为 ECS admission gate |
| Codex Review | `/autoplan` | Independent 2nd opinion | 1 | SUPERSEDED | 旧 outside voice 围绕 `physical_occupancy`，不再作为 v1 主线 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | CLEAR | 复审发现 3 个执行细节缺口，均已写回计划正文：混合设备身份 FIFO、TTL 诊断幂等、blocked probe 调度域过滤 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | Backend-only scope |
| DX Review | `/autoplan` | Developer experience gaps | 1 | REVISED | 实现者契约已从位置 blocker 改为 ECS admission gate |

### Summary

本轮复审结论：计划方向正确，复杂度来自必要的安全闭环，不建议降范围。复审补了三个实现时容易遗漏的约束：

1. 同一物理设备在一个 outbox 写 `device_id`、另一个只写 `target_code` 时，队首 blocked 查询仍必须按同设备 FIFO 串行。
2. `DEVICE_STATUS_PRECHECK_WAIT` TTL 超限后的 diagnostic/reconciliation 必须幂等或节流，不能每轮 dispatch 新增一条 active diagnostic。
3. Workline dispatcher 的 blocked-head probe 必须继承 `operation_domains=("WORKLINE", "RACK")` 边界，不能探测或领取其它调度域的 blocked outbox。

### Review Findings Addressed

1. `[P1] (confidence: 9/10) src/app/sys/repositories/outbox_repository.py:56-62` — 现有 FIFO 同设备规则依赖 `device_id` 或 `target_code`，新 blocked-head 查询若不显式处理 mixed identity，会让后续 outbox 跳过队首等待。正文 Task 2 已补测试和实现规则。
2. `[P2] (confidence: 8/10) src/app/workline/services/diagnostic_service.py:52-59` — 诊断幂等依赖 stable diagnostic key；TTL 超限路径若不传稳定 trace/entity/request，可能重复刷诊断。正文 Task 5 已补幂等约束。
3. `[P2] (confidence: 8/10) src/app/workline/services/outbox_dispatch_service.py:284` — Workline dispatcher 现有 pending 查询限定 `WORKLINE/RACK` 域；新增 blocked-head 查询若没有相同过滤，会越过 `SystemOutboxEngine` 的跨域调度边界。正文 Task 2/Task 4 已补签名、过滤和测试。

### Coverage Diagram

```text
CODE PATHS                                            USER FLOWS
[+] SystemOutboxRepository                            [+] Rough sorter command sequence
  ├── [GAP] blocked head by device_id                 ├── [GAP] [→E2E] Conveyor BUSY then IDLE
  ├── [GAP] blocked head by target_code               └── [GAP] [→E2E] Output arm BUSY then IDLE
  ├── [GAP] mixed device_id/target_code FIFO
  ├── [GAP] operation-domain filtered blocked probe
  └── [GAP] claim keeps retry history

[+] DeviceCommandGateway                              [+] Status outage recovery
  ├── [GAP] ECS READY -> allow POST                    ├── [GAP] timeout/non-200/bad JSON parks outbox
  ├── [GAP] ECS BUSY -> DEVICE_BUSY                    └── [GAP] repeated TTL breach is diagnostic-idempotent
  └── [GAP] status unavailable -> PRECHECK_WAIT

[+] OutboxDispatchService
  ├── [GAP] NEW outbox park without retry burn
  ├── [GAP] blocked head IDLE -> claim and POST
  ├── [GAP] blocked head BUSY -> stay blocked
  ├── [GAP] local WES IDLE does not release
  └── [GAP] TTL escalation keeps FIFO safe
```

### NOT In Scope

- WES 位置码占位模型、`PositionClaim` / `ResourceLock` 表：长期可能需要，但 v1 不建影子物理模型。
- `MANUAL_HOLD + context_json.phase` 推导物理占用：不再作为资源阻塞来源。
- 前端运营看板和告警 UI：本轮只要求后端诊断字段和 runtime diagnostic 可见。
- worker 吞吐 benchmark 和连接池调优：已有 TODO 跟踪，本轮只保持 probe 节流和单设备串行安全。

### What Already Exists

- `SystemOutboxStatus.BLOCKED_RESOURCE` 已存在，并已被 `get_pending_messages()` 作为同设备 FIFO active blocker 使用。
- `blocked_device_id`、`blocked_workline_id`、`blocked_reason` 已存在，可承载设备级资源等待。
- `_ensure_realtime_device_status_ready()` 已实现 ECS 单设备实时 status GET 和 `AUTO/IDLE/current_command_id` 判断，应改造错误语义而不是重写协议。
- `WorklineDiagnosticService.record_event()` 已有 diagnostic key 幂等能力，可复用于 TTL 超限诊断去重。

### Failure Modes

| Failure Mode | Test? | Handling Required | User-visible Result |
| --- | --- | --- | --- |
| mixed `device_id`/`target_code` identity | Required | resolve device identity before head selection | 后续同设备物料不跳过队首 |
| ECS status BUSY | Required | keep `BLOCKED_RESOURCE`, update diagnostics | 队首等待，后续同设备串行等待 |
| ECS status unavailable | Required | `DEVICE_STATUS_PRECHECK_WAIT`, TTL escalation | runtime diagnostic/reconciliation 可见 |
| repeated TTL breach | Required | stable diagnostic key or detail marker | 不刷屏，不丢失排障入口 |
| WES local projection stale IDLE | Required | do not release without ECS probe | 避免误派发到未接纳设备 |
| cross-domain blocked outbox | Required | apply same operation domain filter as pending query | Workline 不越权领取其它调度域 |

### Worktree Parallelization

| Step | Modules touched | Depends on |
|------|----------------|------------|
| Model/migration metadata | `src/app/sys/models`, `migrations` | — |
| Repository state machine | `src/app/sys/repositories` | metadata |
| Gateway error semantics | `src/app/workline/services` | metadata |
| Dispatcher blocked-head probing | `src/app/workline/services` | repository + gateway |
| Runtime diagnostics/tests | `src/app/workline`, `tests/workline_runtime` | dispatcher |

Parallel lanes:

- Lane A: model/migration -> repository state machine.
- Lane B: gateway error semantics, can run in parallel after metadata names are agreed.
- Lane C: dispatcher integration, waits for A + B.
- Lane D: tests should be written TDD with each lane, then full `uv run pytest tests/workline_runtime/` after merge.

### Implementation Tasks

- [ ] **T1 (P1, human: ~1h / CC: ~10min)** — repository — Add mixed `device_id` / `target_code` FIFO test and identity resolution rule.
  - Surfaced by: Architecture review — `get_pending_messages()` already has subtle physical-device matching; new blocked-head query must not weaken it.
  - Files: `src/app/sys/repositories/outbox_repository.py`, `tests/workline_runtime/test_outbox_repository.py`.
  - Verify: `uv run pytest tests/workline_runtime/test_outbox_repository.py -q`.
- [ ] **T2 (P2, human: ~1h / CC: ~10min)** — diagnostics — Make status-wait TTL escalation diagnostic-idempotent.
  - Surfaced by: Code quality/performance review — repeated worker loops can flood diagnostics if key inputs are unstable.
  - Files: `src/app/workline/services/outbox_dispatch_service.py`, `tests/workline_runtime/test_outbox_dispatch_service.py`.
  - Verify: `uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_status_precheck_wait_over_ttl_escalates_runtime_diagnostic -q`.
- [ ] **T3 (P2, human: ~30min / CC: ~5min)** — dispatcher — Preserve operation domain boundaries during blocked-head probe.
  - Surfaced by: Architecture review — Workline dispatcher already filters pending messages to `WORKLINE/RACK`; blocked probe must use the same boundary.
  - Files: `src/app/sys/repositories/outbox_repository.py`, `src/app/workline/services/outbox_dispatch_service.py`, `tests/workline_runtime/test_outbox_dispatch_service.py`.
  - Verify: `uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py::test_dispatch_blocked_head_probe_respects_workline_operation_domains -q`.

### Artifacts

- Test plan artifact: `/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/kaizhou-develop-eng-review-test-plan-20260604-011553.md`
- Tasks JSONL: `/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/tasks-eng-review-20260604-011553.jsonl`

- **UNRESOLVED:** 0.
- **VERDICT:** ENG CLEARED — plan is ready for implementation with TDD and GitNexus impact checks.
