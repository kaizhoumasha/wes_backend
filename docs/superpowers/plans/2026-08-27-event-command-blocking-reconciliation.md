# EVENT 命令阻塞因果诊断与对账门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当 `is_debug=true` 的 EVENT 已持久化但同设备仍有未闭合 `DeviceCommand` 时，保留 EVENT、零创建/零下发新命令、持久化阻塞因果，提供可审计的原命令人工闭合与显式 EVENT 重处理入口。

**Architecture:** 入口继续执行 ACK-after-persist；阻塞门禁只位于 evidence worker 的 `EVENT_DEBUG` 派生命令事务内，并复用现有单设备 advisory lock 与未闭合命令唯一约束。新增一张窄的 `DeviceEventCommandBlock` 因果表保存 EVENT 与旧命令的关系；现有 Result Callback 或受限的 `AUTO + IDLE + current_command_code=null` 人工对账只能闭合旧命令，均不自动重放 EVENT。超级用户显式重处理时，在同一设备创建锁下确认没有任何未闭合命令，再把原 evidence 重新置为 `PENDING`；worker 沿原 EVENT 身份创建唯一 `EVENT_DEBUG` 命令。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、SQLModel/SQLAlchemy、PostgreSQL、Alembic、Celery、pytest。

**Spec:** `TODOS.md` 中 `Reliability / EVENT 阻塞因果诊断与对账门禁`；运行时合同同时受 `docs/architecture/device-command-contract.md`、`docs/integration/callback_event_validation_principles.md` 和 `docs/integration/third_party_integration_whitepaper.md` 约束。

## Global Constraints

- 本计划只改变当前唯一由核心 EVENT 直接派生命令的 `is_debug=true → EVENT_DEBUG/MOVE_FORWARD` 路径；普通 EVENT 进入 WorkLine/Fact/Decision 的行为、插件包和供应商私有协议不在本计划内。
- EVENT 接收入口保持先持久化 `InboundEvidence` 再返回 ACK；设备是否忙不得改变 callback HTTP ACK。
- 门禁按 `device_code` 使用现有单设备执行槽，不解释 `data.location` 等设备附录字段，不新增资源解析器或第二套设备所有权模型。
- 检测到 WES 未闭合命令时，不创建失败占位 `DeviceCommand`，不访问 ECS，不排队下发；原 evidence 进入 `RECONCILING`，阻塞关系与 evidence 同事务提交。
- EVENT worker 确实新建 `PENDING EVENT_DEBUG` 命令后，必须在事务提交后立即唤醒现有 `dispatch_device_commands_batch`；不新增 Celery task 或队列。blocker、同身份幂等复用、事务回滚均不唤醒；send_task 失败不回滚已持久事实，现有 10 秒 Beat 仍作为可靠兜底。
- 阻塞关系保存原 `source_event_id`、`device_code`、旧命令数据库 ID、`command_code`、检测时状态、原因和时间；同一 evidence 同时最多一个 `BLOCKED` 关系，但允许重处理后再次形成新的历史记录。
- Result Callback 仍是物理终态的首选权威；人工闭合只允许请求中明确 `block_id` 对应、当前仍为 `BLOCKED` 且 `reconciliation_reason=DELIVERY_UNKNOWN` 的 `RECONCILING` 旧命令。
- 人工闭合前必须实时证明相同设备 `is_online=true`、`mode=AUTO`、`status=IDLE`、`current_command_code=null`，并按该命令冻结 binding 的 `status_max_age_ms` 校验 `updated_at`。未冻结该状态新鲜度策略的 `MANUAL_DEBUG` / `EVENT_DEBUG` 命令不开放人工闭合，只能由匹配 Result Callback 闭合；本切片不把 `command_timeout_ms` 冒充为状态新鲜度，也不新增诊断配置。
- 人工闭合把旧命令终结为 `FAILED / MANUAL_RECONCILIATION_DEVICE_IDLE`，不得伪造 `DEVICE_RESULT`、`result_evidence_id` 或 `SUCCEEDED`。
- 只要已有匹配该 `command_code` 的已接纳 `DEVICE_RESULT` evidence，即使 worker 尚未应用，人工闭合也必须拒绝；第二事务持有 command 行锁后用普通 MVCC 查询重查该 Result，不再申请 Result evidence 行锁。
- Result Callback 或人工闭合均不自动重处理 EVENT；只有超级用户显式调用重处理 API，且旧命令已终态、设备没有其它未闭合命令时，才能将原 evidence 重新置为 `PENDING`。
- 重处理复用原 `source_event_id` 和既有 `EVENT_DEBUG` 唯一身份；不得改写 EVENT payload、digest、Epoch 绑定或创建新事件身份。
- 不新增 blocker 专用 SSE；现有 `device_evidence.updated` 仍可实时提示 evidence 进入 `RECONCILING`，持久化 GET blocker 是完整因果与处理入口的唯一查询真源。现有 Redis 发布失败不得回滚 evidence、block、command 或 audit。
- 人工闭合和显式重处理使用现有 `AuditLogService`，审计写入与状态变化同事务；审计失败必须整体回滚。
- 严格遵守 API → Service → Repository → Database；Repository 只查询/flush，不 commit。
- 不修改现有 `DeviceEvidenceUpdate`；blocker 返回值和持久化查询快照使用窄的内部合同，不扩大共享 SSE 合同签名和无关消费者。
- 新 Alembic revision 必须由 `uv run alembic revision -m "记录 EVENT 命令阻塞因果"` 生成随机 revision，再编辑生成文件；不得预选或手写 revision ID。
- 这是 schema、可靠执行与人工安全操作的高风险切片，执行采用 RED → DEV → GREEN；纯文档步骤只做文档相称检查。
- Commit、Push、PR、Merge、Deploy 和现场物理操作分别授权。下面的 Commit 步骤只有在当轮已有明确 Commit 授权时执行；否则保留未暂存任务快照并记录 checkpoint。

## Frozen File Structure

- Create `src/app/device/models/event_command_block.py`：阻塞因果持久化模型与状态枚举。
- Modify `src/app/device/models/__init__.py`：导出新模型供 Alembic 和应用加载。
- Create `src/app/device/repositories/event_command_block_repository.py`：阻塞记录的 add、查询、行锁和 `REQUEUED` 写回。
- Modify `src/app/device/repositories/__init__.py`：导出新 Repository。
- Create `src/app/device/event_block_contracts.py`：`EventDebugCommandBlocked` 与持久化查询快照；不改变统一 ECS wire。
- Modify `src/core/task_queue_gateway.py`：新增“唤醒现有 DeviceCommand dispatch 扫描”的窄方法，不新增任务、队列或载荷合同。
- Modify `src/app/device/services/device_command_admission.py`：抽取并复用冻结 `status_max_age_ms` 的状态新鲜度校验。
- Modify `src/app/device/services/device_dispatch_service.py`：改为调用共享状态新鲜度校验，保持既有派发语义不变。
- Modify `src/app/device/services/device_command_service.py`：检测未闭合旧命令、返回 blocker，不创建失败占位命令；新增受限人工闭合。
- Modify `src/app/device/services/device_evidence_service.py`：同事务登记 block、标记 evidence、查询和显式重处理。
- Modify `src/app/execution/repositories/inbound_evidence_repository.py`：增加受限的 `RECONCILING → PENDING` 重处理写回。
- Create `src/app/device/v1/reconciliation.py`：三个超级用户 API：查询 blocker、通过 EVENT blocker 人工闭合其指向的旧命令、显式重处理 EVENT。
- Modify `src/app/device/__init__.py`：注册 reconciliation router。
- Modify `migrations/env.py`：注册新模型。
- Create generated Alembic revision：只创建/删除 `wes_biz.device_event_command_blocks` 及精确约束/索引；实际文件名以 Alembic 命令唯一输出为准。
- Modify `docs/architecture/heavy-test-impact.toml` 和 `tests/scripts/test_select_heavy_tests.py`：闭合新生产路径、migration 和 HEAVY owner。
- Modify `tests/runtime/device_command/test_device_command_admission.py`：锁定派发与人工闭合共用的新鲜度边界。
- Modify `docs/architecture/device-command-contract.md`、`docs/integration/callback_event_validation_principles.md`、`docs/integration/third_party_integration_whitepaper.md`：替换现有“设备忙即失败终结”的冲突语义。
- Modify `TODOS.md`：仅在最终实现、Review 和必选验证全部完成后删除已完成事项。

---

### Task 0: 冻结执行基线、风险与合同差异

**Classification:** 只读实施前门禁。

**Files:**
- Inspect: `TODOS.md`
- Inspect: `src/app/device/services/device_command_service.py`
- Inspect: `src/app/device/services/device_evidence_service.py`
- Inspect: `src/app/device/contracts.py`
- Inspect: `src/app/execution/repositories/inbound_evidence_repository.py`
- Inspect: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**
- Consumes: HEAD 中的 TODO、当前 `EVENT_DEBUG` 合同、GitNexus 当前索引。
- Produces: 固定 base/head/scope、生产符号清单、测试 owner、HEAVY owner、无关 dirty 指纹和 HIGH 风险授权记录。

- [ ] **Step 1: 固定 Git 与工作区快照**

Run:

```bash
rtk git status --short --branch
rtk git rev-parse HEAD
rtk git rev-list --left-right --count origin/develop...HEAD
rtk git merge-base HEAD origin/develop
```

Expected: 明确 branch、HEAD、dirty scope、upstream ahead/behind 和 merge-base；任何目标路径重叠 dirty 都先停止，不 stash/reset/覆盖。

- [ ] **Step 2: 刷新并执行一次 GitNexus 影响分析**

对以下现有生产符号做 upstream impact，并缓存结果：

```text
DeviceCommandService.create_event_debug_command_in_session
DeviceCommandService.reconcile_one
DeviceEvidenceService.process_one
InboundEvidenceRepository.mark_reconciling
CeleryTaskQueueGateway._send_task
```

Expected: 记录直接调用者、测试 owner、风险级别；任何 HIGH/CRITICAL 或计划外普通 EVENT/WorkLine 消费者必须先取得范围确认。本计划编写快照中，`create_event_debug_command_in_session` 与 `process_one` 均为 LOW；不得通过修改共享 `DeviceEvidenceUpdate` 引入其已识别的 HIGH 文件级传播。

- [ ] **Step 3: 固定当前矛盾和本计划选择**

记录以下事实：当前 active-command 分支会创建一个 `FAILED / DEVICE_HAS_ACTIVE_COMMAND` 的 `EVENT_DEBUG` 占位命令并把 evidence 标记 `IGNORED`；当前没有产品化的 DeviceCommand 人工闭合 API；文档仍写“设备忙即失败终结”。实施选择是“不创建占位命令 + durable block + 明确人工闭合 + 显式重处理”，不扩大普通 EVENT/Decision 路径。

- [ ] **Step 4: 进入写操作前确认授权边界**

Expected: 用户确认本高风险切片的上述范围后才能进入 Task 1；该确认只授权实施，不自动授权 Commit、Push、PR、Merge、Deploy 或现场 ECS 调用。

---

### Task 1: 收敛 DeviceCommand 与 EVENT_DEBUG 合同

**Classification:** 纯文档合同切片，不走代码式 RED/GREEN。

**Files:**
- Modify: `docs/architecture/device-command-contract.md:84`
- Modify: `docs/integration/callback_event_validation_principles.md:78`
- Modify: `docs/integration/third_party_integration_whitepaper.md:393`

**Interfaces:**
- Consumes: Task 0 冻结的 `EVENT_DEBUG` 范围。
- Produces: Task 2–5 唯一可执行语义；不改变 Command/Status/Result/Event 外部 wire。

- [ ] **Step 1: 改写 active-command 冲突语义**

文档所有权按读者分开，不在三份文档重复内部实现：

```text
device-command-contract.md：状态机、block 持久事实、人工闭合限制、API 错误和审计语义。
callback_event_validation_principles.md：ACK-after-persist、EVENT evidence -> RECONCILING、零创建/零下发和显式重处理边界。
third_party_integration_whitepaper.md：只写供应商可观测事实——EVENT 仍被接纳、新 Command 不会发送、原 command_code 必须原身份对账；不暴露内部表名、锁顺序、审计 args 或 Repository 语义。
```

三份文档的共同对外结论仍是：WES 已有未闭合命令时，EVENT evidence 进入 `RECONCILING`并持久化因果，不创建 `EVENT_DEBUG DeviceCommand`；ECS Status 自身不满足准入时，已创建命令按既有准入失败终结。

Expected: 不再用“设备忙”一个词混合 WES 数据库执行槽占用与 ECS 实时 Status 拒绝。

- [ ] **Step 2: 冻结闭合与重处理语义**

明确：匹配 Result Callback 可以闭合旧命令；人工闭合只能作用于 blocker 指向、原因为 `DELIVERY_UNKNOWN`、且冻结 binding 可提供状态新鲜度合同的业务命令，只产生 `FAILED / MANUAL_RECONCILIATION_DEVICE_IDLE`；诊断命令本切片只允许 Result 闭合。两者都不自动重放；显式重处理复用原 EVENT 身份并再次经过执行槽门禁。

- [ ] **Step 3: 做文档相称验证**

Run:

```bash
rtk git diff --check -- docs/architecture/device-command-contract.md docs/integration/callback_event_validation_principles.md docs/integration/third_party_integration_whitepaper.md
rtk rg -n "设备忙即失败终结|DEVICE_HAS_ACTIVE_COMMAND|MANUAL_RECONCILIATION_DEVICE_IDLE|显式重处理" docs/architecture/device-command-contract.md docs/integration/callback_event_validation_principles.md docs/integration/third_party_integration_whitepaper.md
```

Expected: 无格式错误，三份合同没有相互冲突的旧句；不运行 pytest。

- [ ] **Step 4: 授权后建立合同 checkpoint**

```bash
rtk git add docs/architecture/device-command-contract.md docs/integration/callback_event_validation_principles.md docs/integration/third_party_integration_whitepaper.md
rtk git commit -m "docs(device): 冻结 EVENT 命令阻塞对账语义"
```

Expected: 仅在明确 Commit 授权存在时执行；否则跳过且不 stage。

---

### Task 2: 建立持久化 blocker 的 RED 与 schema owner

**Classification:** 高风险 schema 切片，RED → DEV → GREEN。

**Files:**
- Create: `tests/runtime/device_command/test_event_command_block_repository.py`
- Create: `tests/integration/device_command/test_event_command_block_migration_postgresql.py`
- Create: `src/app/device/models/event_command_block.py`
- Modify: `src/app/device/models/__init__.py`
- Create: `src/app/device/repositories/event_command_block_repository.py`
- Modify: `src/app/device/repositories/__init__.py`
- Modify: `migrations/env.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Modify: `tests/scripts/test_select_heavy_tests.py`
- Create: generated Alembic revision from current head `9624cc34fa93`

**Interfaces:**
- Consumes: `InboundEvidence.id/source_identity/device_code`、`DeviceCommand.id/command_code/status`。
- Produces: `DeviceEventCommandBlockStatus.BLOCKED|REQUEUED`；`DeviceEventCommandBlockRepository.add_block()`、`get_by_id_for_update()`、`get_latest_for_evidence()`、`mark_requeued()`。

- [ ] **Step 1: 先写模型、Repository 与 PostgreSQL schema 失败测试**

用最小 fake session 锁定以下接口和状态完整性：

```python
block = DeviceEventCommandBlock(
    evidence_id=91,
    source_event_id="EVENT:" + "a" * 64,
    device_code="STATION_SCAN10",
    blocking_command_id=41,
    blocking_command_code="CMD-OLD-001",
    blocking_command_status=CommandStatus.RECONCILING,
    blocking_reconciliation_reason="DELIVERY_UNKNOWN",
    reason_code="DEVICE_HAS_ACTIVE_COMMAND",
    status=DeviceEventCommandBlockStatus.BLOCKED,
    blocked_at=datetime(2026, 8, 27),
)
assert block.requeued_at is None
```

Repository 测试必须断言：`get_by_id_for_update()` 锁定 exact block 并可验证 evidence 所有权；可按 evidence 取得 latest block 并区分“从未阻塞”与“已 `REQUEUED`”；`mark_requeued()` 同时设置 `status=REQUEUED` 和 `requeued_at`；Repository 不 commit。

PostgreSQL 测试在实现前就固定表、BIGINT FK、CHECK、partial unique index、latest 索引和 downgrade 合同；FK 样本 ID 显式大于 `2_147_483_647`，防止再次引入 int32 回归。

- [ ] **Step 2: 运行 FAST RED**

Run:

```bash
uv run pytest tests/runtime/device_command/test_event_command_block_repository.py -q
uv run pytest tests/integration/device_command/test_event_command_block_migration_postgresql.py -q
```

Expected: 两者 FAIL，原因是 `event_command_block` 模型/Repository/schema 尚不存在；导入错误或缺表/约束属于本切片有效 RED。PostgreSQL 环境未就绪时不以 skip 代替 RED，应先按专项测试入口准备独占临时库。

- [ ] **Step 3: 实现最小模型和 Repository**

模型只包含以下字段与约束：

```python
class DeviceEventCommandBlockStatus(str, Enum):
    BLOCKED = "BLOCKED"
    REQUEUED = "REQUEUED"

class DeviceEventCommandBlock(EnterpriseMixin, DataTableMixin, table=True):
    evidence_id: int
    source_event_id: str
    device_code: str
    blocking_command_id: int
    blocking_command_code: str
    blocking_command_status: CommandStatus
    blocking_reconciliation_reason: str | None
    reason_code: str
    status: DeviceEventCommandBlockStatus
    blocked_at: datetime
    requeued_at: datetime | None
```

数据库约束必须保证：`BLOCKED` 时 `requeued_at IS NULL`，`REQUEUED` 时 `requeued_at IS NOT NULL`；`reason_code='DEVICE_HAS_ACTIVE_COMMAND'`；`blocking_command_status` 只能是检测时的 `PENDING|DISPATCHING|ACKNOWLEDGED|RECONCILING`；`evidence_id` 只允许一个 `status='BLOCKED'` 的 partial unique index。使用 BIGINT 兼容 FK 指向 `wes_biz.inbound_evidences.id` 与 `wes_biz.device_commands.id`；最新历史查询索引固定为 `(evidence_id, blocked_at, id)`。

- [ ] **Step 4: 先闭合 HEAVY mapping**

在首次运行 selector 前，为本 Task 已创建的 model、Repository、`migrations/env.py`、待生成 migration 和两个测试 owner 增加精确 mapping，并先运行：

```bash
uv run pytest tests/scripts/test_select_heavy_tests.py -q
```

Expected: selector 合同 PASS；新路径不会在后续 `--scope unstaged` 中 fail closed。

- [ ] **Step 5: 由 Alembic 生成并编辑 migration**

Run:

```bash
uv run alembic revision -m "记录 EVENT 命令阻塞因果"
```

Expected: 只产生一个随机 revision 文件，`down_revision = "9624cc34fa93"`。编辑该唯一文件，仅创建 `wes_biz.device_event_command_blocks`、两个 FK、状态/时间/检测命令状态 CHECK、`(evidence_id, blocked_at, id)` 查询索引和 open-block partial unique index；downgrade 只删除本表。生成后立即用实际 migration 路径补全 mapping 并重跑 selector 合同。

- [ ] **Step 6: 运行 PostgreSQL migration GREEN**

集成测试必须在独占临时 PostgreSQL 中验证：从 `9624cc34fa93` 升级到新 revision；大于 `2_147_483_647` 的合法 FK block 可插入；同一 evidence 第二个 `BLOCKED` 被唯一索引拒绝；首条 `REQUEUED` 后可插入新的 `BLOCKED`；非法时间组合、终态 `blocking_command_status` 均被 CHECK 拒绝；downgrade 后表缺席。

Run:

```bash
uv run pytest tests/runtime/device_command/test_event_command_block_repository.py -q
uv run pytest tests/integration/device_command/test_event_command_block_migration_postgresql.py -q
./scripts/run_selected_heavy_local.sh --scope unstaged
```

Expected: FAST PASS；HEAVY manifest 必须包含新 migration owner 且真实 PostgreSQL 测试 PASS，环境 skip 不算通过。

- [ ] **Step 7: 授权后建立 schema checkpoint**

使用 Step 5 输出的唯一 migration 路径，按本 Task `Files` manifest 精确暂存，禁止 glob；随后只在 Commit 授权存在时提交：

```bash
rtk git add tests/runtime/device_command/test_event_command_block_repository.py tests/integration/device_command/test_event_command_block_migration_postgresql.py src/app/device/models/event_command_block.py src/app/device/models/__init__.py src/app/device/repositories/event_command_block_repository.py src/app/device/repositories/__init__.py migrations/env.py docs/architecture/heavy-test-impact.toml tests/scripts/test_select_heavy_tests.py migrations/versions/20260827_0433_71eeea05c864_记录_event_命令阻塞因果.py
rtk git commit -m "feat(device): 持久化 EVENT 命令阻塞因果"
```

---

### Task 3: 在 EVENT worker 阻止新命令并发布因果诊断

**Classification:** 高风险可靠性切片，RED → DEV → GREEN。

**Files:**
- Create: `src/app/device/event_block_contracts.py`
- Modify: `src/app/device/services/device_command_service.py:356`
- Modify: `src/app/device/services/device_evidence_service.py:264`
- Modify: `src/core/task_queue_gateway.py`
- Modify: `tests/runtime/device_command/test_device_command_service.py:571`
- Modify: `tests/runtime/device_command/test_evidence_service.py:415`
- Modify: `tests/core/test_outbox_dispatch_target_gateway.py`

**Interfaces:**
- Consumes: Task 2 Repository；现有 DeviceCommand dispatch batch 任务名与固定 batch limit。
- Produces: `EventDebugCommandReady(created: bool) | EventDebugCommandBlocked`；持久化 blocker 查询事实；`TaskQueueGateway.enqueue_device_commands()` 复用现有 `dispatch_device_commands_batch(limit=100)`。

- [ ] **Step 1: 把当前错误语义改成 RED 断言**

替换现有“active command 创建终态失败占位命令”测试，锁定新结果：

```python
result = await service.create_event_debug_command_in_session(object(), evidence=evidence)
assert isinstance(result, EventDebugCommandBlocked)
assert result.blocking_command_code == "CMD-OLD-001"
assert result.blocking_command_status is CommandStatus.RECONCILING
assert result.blocking_reconciliation_reason == "DELIVERY_UNKNOWN"
assert repository.created == []
```

Evidence service 测试必须断言：evidence 为 `RECONCILING`；block 与 evidence 同事务写入；没有 debug command code；business wake 与 device dispatch wake 均为零；现有通用 `device_evidence.updated` 仍发布 `RECONCILING`，但不新增 blocker 专用 SSE 事件。

另增唤醒失败测试：本次新建 `PENDING EVENT_DEBUG` 时，事务提交后恰好调用一次 `enqueue_device_commands()`；同身份复用返回 `created=False`、blocker、命令非 `PENDING` 或事务回滚都是零次；queue 唤醒异常不改写已提交 command/evidence，Beat schedule 仍保留 10 秒兜底。

- [ ] **Step 2: 运行行为 RED**

Run:

```bash
uv run pytest tests/runtime/device_command/test_device_command_service.py::test_event_debug_command_records_existing_command_without_creating_placeholder tests/runtime/device_command/test_evidence_service.py::test_debug_event_blocked_by_unclosed_command_persists_causality_and_never_wakes_business tests/runtime/device_command/test_evidence_service.py::test_debug_event_new_command_wakes_dispatch_after_commit tests/core/test_outbox_dispatch_target_gateway.py::test_gateway_enqueues_device_command_dispatch_with_fixed_batch_limit -q
```

Expected: FAIL，当前实现仍创建 `FAILED` 占位命令、没有 blocker 持久化合同，也没有事务后 DeviceCommand dispatch wake 端口。

- [ ] **Step 3: 实现 typed blocker 返回值**

`event_block_contracts.py` 定义不可变 dataclass：

```python
@dataclass(frozen=True, slots=True)
class EventDebugCommandReady:
    command_code: str
    status: CommandStatus
    created: bool

@dataclass(frozen=True, slots=True)
class EventDebugCommandBlocked:
    blocking_command_id: int
    blocking_command_code: str
    blocking_command_status: CommandStatus
    blocking_reconciliation_reason: str | None
```

`create_event_debug_command_in_session()` 保持同身份命令优先返回 `EventDebugCommandReady(created=False)`；随后取得设备创建锁并查询未闭合命令。命中时校验旧命令已有数据库 ID，返回 `EventDebugCommandBlocked`，不得构造或 add 新 `DeviceCommand`；未命中时沿现有路径创建唯一命令并返回 `EventDebugCommandReady(created=True, status=PENDING)`。该内部合同不修改共享 `DeviceCommandHandle`。

- [ ] **Step 4: 同事务登记 block 与 evidence 状态**

`DeviceEvidenceService.process_one()` 只在 `event.is_debug` 分支解释两个窄结果：`EventDebugCommandBlocked` 时 add block，同时快照检测时的 command status 与 `reconciliation_reason`，调用现有 `mark_reconciling()`，且不设 dispatch wake；`EventDebugCommandReady(created=True, status=PENDING)` 时记录事务后 dispatch wake 标志。普通 EVENT、DEVICE_RESULT 和其它 ValueError 路径保持现状。

`TaskQueueGateway` 只新增 `enqueue_device_commands()`，`CeleryTaskQueueGateway` 内复用既有任务名 `src.celery_app.tasks.device_command.dispatch_device_commands_batch` 与固定 `limit=100`。evidence 事务退出且成功提交后，先尝试唤醒 dispatch，再沿现有路径发布 best-effort evidence update；唤醒异常只记录日志，不回滚也不重复发送。Beat 仍是丢失唤醒的唯一兜底。

- [ ] **Step 5: 运行聚焦 GREEN**

Run:

```bash
uv run pytest tests/runtime/device_command/test_device_command_service.py tests/runtime/device_command/test_evidence_service.py tests/core/test_outbox_dispatch_target_gateway.py tests/deployment/test_execution_worker_startup.py -q
uv run ruff check src/app/device/event_block_contracts.py src/app/device/services/device_command_service.py src/app/device/services/device_evidence_service.py src/core/task_queue_gateway.py tests/runtime/device_command/test_device_command_service.py tests/runtime/device_command/test_evidence_service.py tests/core/test_outbox_dispatch_target_gateway.py
uv run ruff format --check src/app/device/event_block_contracts.py src/app/device/services/device_command_service.py src/app/device/services/device_evidence_service.py src/core/task_queue_gateway.py tests/runtime/device_command/test_device_command_service.py tests/runtime/device_command/test_evidence_service.py tests/core/test_outbox_dispatch_target_gateway.py
```

Expected: 全部 PASS；新建 `PENDING EVENT_DEBUG` 事务提交后恰好一次 dispatch wake；blocker、同身份复用和事务回滚均为零次；queue 失败隔离且 10 秒 Beat 兜底仍在。现有普通 EVENT wake、EVENT 幂等、Result Callback 和 publisher-failure 测试继续通过。

- [ ] **Step 6: 授权后建立 worker checkpoint**

```bash
rtk git add src/app/device/event_block_contracts.py src/app/device/services/device_command_service.py src/app/device/services/device_evidence_service.py src/core/task_queue_gateway.py tests/runtime/device_command/test_device_command_service.py tests/runtime/device_command/test_evidence_service.py tests/core/test_outbox_dispatch_target_gateway.py
rtk git commit -m "feat(device): 阻止 EVENT 绕过未闭合命令"
```

---

### Task 4: 提供受限的旧命令人工闭合入口

**Classification:** 高风险人工安全操作，RED → DEV → GREEN。

**Files:**
- Modify: `src/app/device/services/device_command_service.py`
- Modify: `src/app/device/services/device_command_admission.py`
- Modify: `src/app/device/services/device_dispatch_service.py`
- Modify: `src/app/device/repositories/event_command_block_repository.py`
- Create: `src/app/device/v1/reconciliation.py`
- Modify: `src/app/device/__init__.py`
- Create: `tests/runtime/device_command/test_manual_reconciliation_service.py`
- Modify: `tests/runtime/device_command/test_device_command_admission.py`
- Create: `tests/api/test_device_reconciliation_api.py`

**Interfaces:**
- Consumes: 请求指定的 `block_id` 及其 evidence；`DeviceCommand.status/reconciliation_reason/device_code/line_run_epoch_id`；冻结 binding 的 Endpoint 与 `status_max_age_ms`；`InboundEvidenceRepository.get_device_result_for_command()`；`EcsAdapter.fetch_status()`；`AuditLogService.create_audit_log()`。
- Produces: `DeviceCommandService.reconcile_delivery_unknown_as_device_idle(source_event_id: str, block_id: int, reason: str, actor_id: int) -> DeviceCommandHandle`；`POST /api/v1/device/evidences/{source_event_id}/blockers/{block_id}/reconcile-device-idle`。

- [ ] **Step 1: 写人工闭合失败测试**

覆盖以下封闭矩阵：

```text
未知 EVENT / block_id 不存在或不属于该 EVENT               -> 404
block_id 已 REQUEUED 或已不是 latest blocker                 -> 409
blocker 指向的 command 不存在                          -> 409
command 不是 RECONCILING 或原因不是 DELIVERY_UNKNOWN     -> 409，零 ECS 调用
请求意图绕过 block_id 闭合其他 RECONCILING command      -> 404/409，零 ECS 调用
诊断命令或冻结 binding / Endpoint / status_max_age_ms 不可解析 -> 409，命令不变
Status identity mismatch/offline/not AUTO/not IDLE            -> 409，命令不变
current_command_code 非 null                                  -> 409，命令不变
Status updated_at 在未来或超过冻结有效期                    -> 409，命令不变
ECS status 查询不可用                                     -> 503，命令不变
探测前已持久化一条已接纳但尚未应用的 DEVICE_RESULT       -> 409，零 ECS 调用
状态探测后 command/block/version/status 漂移或出现 Result       -> 409，命令不变
状态探测后原 block 已 REQUEUED 且出现下一代 BLOCKED      -> 409，不作用于新 block
满足全部证明                                              -> FAILED / MANUAL_RECONCILIATION_DEVICE_IDLE
audit 写失败                                              -> 整个事务回滚
人工闭合后才到达的 Result                              -> 不改写 FAILED，按既有迟到/冲突 evidence 语义保留
```

- [ ] **Step 2: 运行人工闭合 RED**

Run:

```bash
uv run pytest tests/runtime/device_command/test_manual_reconciliation_service.py tests/api/test_device_reconciliation_api.py -q
```

Expected: FAIL，Service 方法与 API route 尚不存在。

- [ ] **Step 3: 实现两阶段只读探测与锁内闭合**

Service 第一个短事务按 `(source_event_id, block_id)` 读取目标 blocker、evidence、其指向命令和已有 Result，并冻结 target block ID 与 command version；只有冻结 `line_run_epoch_id + device_code` binding 可同时提供 Endpoint 和 `status_max_age_ms`。诊断命令无该冻结新鲜度合同，直接 fail closed。事务外调用 ECS `fetch_status()`，复用 `ensure_runtime_admissible(status, expected_device_code, task_type=None)`，并按当次 `observed_at` 与 binding 的 `status_max_age_ms` 执行和 `DeviceDispatchService` 一致的未来/过期计算。

第二个事务使用固定锁顺序：`evidence -> exact target block -> device advisory lock -> blocking command`。要求 exact `block_id` 仍属于该 evidence、仍为 latest 且 `BLOCKED`，command 仍为相同 version 的 `RECONCILING / DELIVERY_UNKNOWN`。持有 command 行锁后调用现有非锁定 `get_device_result_for_command()`最终复核：新 Result ingress 会先等待同一 command 锁，已提交 Result 对普通 MVCC `SELECT` 可见，而不申请 Result evidence 行锁可避免与“result worker 先锁 evidence 再锁 command”形成环路。复核仍为 `None` 后才：

```python
command.failure_code = "MANUAL_RECONCILIATION_DEVICE_IDLE"
command.transition_to(CommandStatus.FAILED)
```

不得清空原 `reconciliation_reason`，不得写 `result_evidence_id`。

- [ ] **Step 4: 同事务写成功审计**

审计必须使用：

```text
model=DeviceCommand
operation=manual_reconcile_device_idle
record_id=<database command id>
source_event_id, block_id, command_code, device_code, previous_status, reconciliation_reason,
observed is_online/mode/status/current_command_code/updated_at, status_max_age_ms,
actor_id, canonical reason
```

不记录 Endpoint、command params、credential 或原 callback payload。

- [ ] **Step 5: 实现超级用户 API facade**

请求体只有 `reason: str`，strip 后长度 `1..500`。Route 以 `(source_event_id, block_id)` 定位不可变的因果目标，只做认证、输入模型、Service 调用和 404/409/503 映射；不得访问 Repository，也不接受任意 `command_code`。成功返回 `command_code`、`status=FAILED`、`failure_code=MANUAL_RECONCILIATION_DEVICE_IDLE`。

- [ ] **Step 6: 运行人工闭合 GREEN**

Run:

```bash
uv run pytest tests/runtime/device_command/test_manual_reconciliation_service.py tests/api/test_device_reconciliation_api.py tests/runtime/device_command/test_device_command_admission.py -q
uv run ruff check src/app/device/services/device_command_admission.py src/app/device/services/device_command_service.py src/app/device/services/device_dispatch_service.py src/app/device/v1/reconciliation.py src/app/device/__init__.py tests/runtime/device_command/test_device_command_admission.py tests/runtime/device_command/test_manual_reconciliation_service.py tests/api/test_device_reconciliation_api.py
```

Expected: PASS；既有 admission 语义未被放宽；后续 PostgreSQL 闭环测试还必须覆盖 result worker 先锁 evidence、人工闭合再锁 command 的真实交错，证明无死锁且已接纳 Result 胜出。

- [ ] **Step 7: 授权后建立人工对账 checkpoint**

```bash
rtk git add src/app/device/services/device_command_admission.py src/app/device/services/device_command_service.py src/app/device/services/device_dispatch_service.py src/app/device/repositories/event_command_block_repository.py src/app/device/v1/reconciliation.py src/app/device/__init__.py tests/runtime/device_command/test_device_command_admission.py tests/runtime/device_command/test_manual_reconciliation_service.py tests/api/test_device_reconciliation_api.py
rtk git commit -m "feat(device): 增加命令空闲人工对账入口"
```

---

### Task 5: 提供持久 blocker 查询与显式 EVENT 重处理

**Classification:** 高风险幂等/因果恢复切片，RED → DEV → GREEN。

**Files:**
- Modify: `src/app/device/services/device_evidence_service.py`
- Modify: `src/app/execution/repositories/inbound_evidence_repository.py`
- Modify: `src/app/device/repositories/event_command_block_repository.py`
- Modify: `src/app/device/v1/reconciliation.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Modify: `tests/scripts/test_select_heavy_tests.py`
- Modify: `tests/runtime/device_command/test_evidence_service.py`
- Modify: `tests/api/test_device_reconciliation_api.py`
- Create: `tests/integration/device_command/test_event_command_blocking_reconciliation_postgresql.py`

**Interfaces:**
- Consumes: Task 2 open block、Task 4 终态 old command、现有设备创建 advisory lock。
- Produces: `DeviceEvidenceService.get_event_command_block(source_event_id: str)`；`reprocess_blocked_event(source_event_id: str, block_id: int, reason: str, actor_id: int)`；GET blocker 与携带 `block_id` 因果令牌的 POST reprocess API。

- [ ] **Step 1: 写显式重处理 RED**

锁定状态矩阵：

```text
未知 EVENT 或从未有 block               -> 404
非 DEVICE_EVENT / is_debug 非 true        -> 409
blocker 仍占设备槽                        -> 409
同设备出现另一条未闭合命令                 -> 409
最新 block 已 REQUEUED                     -> 409，不能重复重放
请求 block_id 不是该 EVENT 的 latest block    -> 409，不能作用于新一代 blocker
全部满足                                  -> block REQUEUED + evidence PENDING
audit 写失败                              -> 两者回滚
worker 再处理                             -> 原 source_event_id 只创建一个 EVENT_DEBUG command
再次遇到新 blocker                        -> 新建下一条 BLOCKED 历史记录
```

- [ ] **Step 2: 运行 reprocess RED**

Run:

```bash
uv run pytest tests/runtime/device_command/test_evidence_service.py tests/api/test_device_reconciliation_api.py -q
```

Expected: 新增重处理场景 FAIL，既有 EVENT/Result 场景仍保持 GREEN。

- [ ] **Step 3: 实现查询快照**

GET `/api/v1/device/evidences/{source_event_id}/blocker` 返回 latest 持久事实，不返回 raw payload：block ID/status、EVENT identity、device、旧命令 code、检测状态、检测时 `reconciliation_reason`、当前状态、原因、blocked/requeued 时间，以及两个嵌入当前 `block_id` 的固定相对路径。`blocking_command_terminal` 由当前 command 状态计算，不把 BLOCKED 误报为命令仍未终态。查询不存在 latest 才是 `404`；latest 为 `REQUEUED` 仍返回历史快照，其操作路径不因新 blocker 出现而改指目标。

- [ ] **Step 4: 实现事务化显式重处理**

Service 先按 `(source_event_id, block_id)` 冻结请求目标：目标不存在或不属于该 EVENT 返回 404，不是 latest 或已 `REQUEUED` 返回 409，只有 target 仍为 latest `BLOCKED` 才继续。随后在一个事务内按固定顺序锁定 evidence、exact target block、设备创建 advisory lock、blocking command，并在锁内再次确认 target block ID 仍为 latest 且 `BLOCKED`。旧请求不得重新查找并作用于后续新建 blocker。查询同设备当前未闭合命令后，只有 blocker 指向命令已终态且没有其它未闭合命令时：

```python
block.status = DeviceEventCommandBlockStatus.REQUEUED
block.requeued_at = now
evidence.apply_status = InboundEvidenceApplyStatus.PENDING
evidence.processed_at = None
```

EVENT 的 `source_identity`、`payload_digest`、`normalized_payload`、`line_run_epoch_id`、`contract_key/version` 全部保持不变。周期性的现有 `process_device_evidence_batch` 自然拾取，不增加新 Celery task、队列或即时外部调用。

- [ ] **Step 5: 同事务写重处理审计并实现 API**

审计 operation 固定为 `reprocess_blocked_device_event`，record ID 使用 evidence 数据库 ID，args 只含 source identity、device、block ID、blocking command code、actor ID 和 canonical reason。API 固定为 `POST /api/v1/device/evidences/{source_event_id}/blockers/{block_id}/reprocess`；成功返回 `202`，只表示 evidence 已重新进入 `PENDING`，不表示命令已创建、ECS 已接纳或物理动作完成。

- [ ] **Step 6: 运行 FAST GREEN**

Run:

```bash
uv run pytest tests/runtime/device_command/test_evidence_service.py tests/api/test_device_reconciliation_api.py -q
```

Expected: PASS；重复重处理、并发状态漂移和审计失败都 fail closed。

- [ ] **Step 7: 在首次因果闭环 HEAVY 前补全 mapping**

先为 Task 3–5 的 worker、Service、API、Repository、`task_queue_gateway.py` 和 PostgreSQL 因果测试增加精确 mapping，运行：

```bash
uv run pytest tests/scripts/test_select_heavy_tests.py -q
uv run scripts/select_heavy_tests.py --scope unstaged
```

Expected: 无未知生产/测试路径，manifest 只包含实际触及边界的 owner。

- [ ] **Step 8: 运行 PostgreSQL 因果闭环 HEAVY**

集成测试使用真实 Repository 和同一临时 PostgreSQL，分别证明：匹配 Result Callback 闭合旧命令后可显式重处理；人工 idle 对账闭合后可显式重处理；已接纳但尚未应用的 Result 一定阻止人工闭合；result worker 先锁 evidence、人工闭合再锁 command 时无死锁且 Result 胜出；人工闭合后的迟到 Result 不改写终态；人工闭合或重处理的 audit 写入失败时业务状态与审计一起回滚；阻塞期间 `EVENT_DEBUG` 命令数为零；重处理后按原 EVENT identity 仅创建一条命令；并发 A 重处理成功、worker 生成新 blocker 后，旧 A 请求携带的 `block_id` 不能作用于新 blocker；旧 blocker 历史不丢失。

Run:

```bash
./scripts/run_selected_heavy_local.sh --scope unstaged
```

Expected: selector 只运行 manifest 中的 DeviceCommand/Evidence/migration owner，全部 PASS；skip 不算证据。

- [ ] **Step 9: 授权后建立因果恢复 checkpoint**

```bash
rtk git add src/app/device/services/device_evidence_service.py src/app/execution/repositories/inbound_evidence_repository.py src/app/device/repositories/event_command_block_repository.py src/app/device/v1/reconciliation.py docs/architecture/heavy-test-impact.toml tests/scripts/test_select_heavy_tests.py tests/runtime/device_command/test_evidence_service.py tests/api/test_device_reconciliation_api.py tests/integration/device_command/test_event_command_blocking_reconciliation_postgresql.py
rtk git commit -m "feat(device): 支持阻塞 EVENT 显式重处理"
```

---

### Task 6: 闭合 HEAVY mapping、最终验证、Review 与 TODO

**Classification:** 最终门禁与文档收口，不新增行为。

**Files:**
- Modify: `docs/architecture/heavy-test-impact.toml`
- Modify: `tests/scripts/test_select_heavy_tests.py`
- Modify: `TODOS.md`
- Verify: all Task 1–5 paths

**Interfaces:**
- Consumes: Task 1–5 最终可执行树。
- Produces: 当前 staged 或明确 unstaged fingerprint 绑定的聚焦、QUALITY、HEAVY、migration 和 Review 证据。

- [ ] **Step 1: 审计已分步建立的精确 HEAVY mapping**

不把 mapping 推迟到本 Task 才首次创建；Task 2 已在第一次 HEAVY 前建立 schema mapping，Task 5 已在第二次 HEAVY 前补全行为 mapping。本步只审计全量新 model/repository/contracts/API、`migrations/env.py`、生成 migration、InboundEvidence requeue 写回是否闭合。权威 HEAVY owner 固定为：

```text
tests/e2e/device_command/test_device_command_production_wiring.py
tests/integration/device_command/test_device_command_constraints.py
tests/integration/device_command/test_event_command_block_migration_postgresql.py
tests/integration/device_command/test_event_command_blocking_reconciliation_postgresql.py
tests/integration/execution/test_decision_processing_postgresql.py
tests/integration/test_authorization_bootstrap_postgresql.py
```

只给实际触及边界的路径选择对应子集；不得用宽泛 glob、臆造 owner 或 `heavy_tests=[]` 消除 fail closed。

- [ ] **Step 2: 验证 selector 合同**

Run:

```bash
uv run pytest tests/scripts/test_select_heavy_tests.py -q
uv run scripts/select_heavy_tests.py --scope unstaged
```

Expected: selector 测试 PASS；manifest 无未知生产/migration 路径，并且只含本计划冻结的 HEAVY owner。

- [ ] **Step 3: 运行最终聚焦 FAST**

Run:

```bash
uv run pytest tests/runtime/device_command/test_event_command_block_repository.py tests/runtime/device_command/test_device_command_service.py tests/runtime/device_command/test_evidence_service.py tests/runtime/device_command/test_manual_reconciliation_service.py tests/runtime/device_command/test_device_command_admission.py tests/api/test_device_reconciliation_api.py tests/core/test_outbox_dispatch_target_gateway.py tests/deployment/test_execution_worker_startup.py -q
```

Expected: PASS，且没有访问真实 HTTP、Redis、Celery 或容器。

- [ ] **Step 4: 运行 migration 与 selector 指定 HEAVY**

Run:

```bash
./scripts/run_selected_heavy_local.sh --scope unstaged
```

Expected: 当前 diff fingerprint 的全部 manifest 测试 PASS；migration 在干净逻辑库从 `9624cc34fa93` 升级到新 head，并完成 downgrade/upgrade 约束验证。

- [ ] **Step 5: 运行一次主 Review**

固定 base/head/scope 后检查：无普通 EVENT/WorkLine 行为漂移；无失败占位命令；block 与 evidence 原子；仅新建 `PENDING EVENT_DEBUG` 在 commit 后唤醒现有 dispatch，其他路径零唤醒且失败隔离；人工闭合没有伪造成功/Result；重处理不换 EVENT identity；现有 evidence SSE 发布失败隔离；审计失败回滚；API 仅超级用户；migration/HEAVY owner 闭合。生产代码或机器合同修复后，由同一 Reviewer 一轮完成旧意见闭环与 fresh full Review。

- [ ] **Step 6: 运行最终 QUALITY 与 diff 检查**

Run:

```bash
./scripts/git-quality-gate.sh --profile quality
rtk git diff --check
rtk git status --short
```

Expected: QUALITY PASS；diff 只含计划文件及已授权实施范围；用户原有工作区状态未被覆盖。

- [ ] **Step 7: 仅在全部证据有效后关闭 active TODO**

从 `TODOS.md` 删除完整的 `EVENT 阻塞因果诊断与对账门禁` 条目，不保留“已完成”占位。只做：

```bash
rtk git diff --check -- TODOS.md
```

Expected: 不因纯文档收尾重跑已有效的 QUALITY、HEAVY 或 migration。

- [ ] **Step 8: 授权后提交最终快照**

只在 Commit 授权存在时，按最终 manifest 精确暂存。这一步即使前面的可选 checkpoint 都被跳过，也不得漏掉 schema、mapping、合同、测试、计划、Runbook 或 TODO：

```bash
rtk git add docs/superpowers/plans/2026-08-27-event-command-blocking-reconciliation.md docs/architecture/device-command-contract.md docs/integration/callback_event_validation_principles.md docs/integration/third_party_integration_whitepaper.md docs/runbooks/device-command-operations.md docs/architecture/heavy-test-impact.toml TODOS.md migrations/env.py migrations/versions/20260827_0433_71eeea05c864_记录_event_命令阻塞因果.py src/app/device/models/event_command_block.py src/app/device/models/__init__.py src/app/device/repositories/event_command_block_repository.py src/app/device/repositories/__init__.py src/app/device/event_block_contracts.py src/app/device/services/device_command_admission.py src/app/device/services/device_command_service.py src/app/device/services/device_dispatch_service.py src/app/device/services/device_evidence_service.py src/app/execution/repositories/inbound_evidence_repository.py src/app/device/v1/reconciliation.py src/app/device/__init__.py src/core/task_queue_gateway.py tests/runtime/device_command/test_event_command_block_repository.py tests/integration/device_command/test_event_command_block_migration_postgresql.py tests/runtime/device_command/test_device_command_admission.py tests/runtime/device_command/test_device_command_service.py tests/runtime/device_command/test_evidence_service.py tests/core/test_outbox_dispatch_target_gateway.py tests/runtime/device_command/test_manual_reconciliation_service.py tests/api/test_device_reconciliation_api.py tests/integration/device_command/test_event_command_blocking_reconciliation_postgresql.py tests/scripts/test_select_heavy_tests.py
```

再按项目规则对该精确 staged snapshot 运行：

```bash
rtk npx gitnexus detect-changes --scope staged --repo "$PWD"
uv run scripts/select_heavy_tests.py --scope staged
```

Expected: 无计划外符号/流程或 HEAVY owner。随后只有在 Commit 授权存在时执行 Conventional Commit；Push、PR、Merge、Deploy 仍分别等待授权。

## Completion Criteria

- Callback EVENT 在设备被旧命令占槽时仍按原合同持久化并 ACK。
- 阻塞事务不创建任何新 `DeviceCommand`，因此 dispatch worker 没有可下发对象。
- 成功新建的 `PENDING EVENT_DEBUG` 命令在提交后立即唤醒既有 dispatch batch；唤醒丢失时仍由 Beat 扫描补偿，不为此创建第二套任务或可靠事实。
- 数据库能从 EVENT identity 直接查询旧 `command_code`、检测状态、当前状态、原因和处理入口。
- 匹配 Result Callback 与人工 idle 对账都只闭合旧命令，不自动重放、不改写原证据。
- 显式重处理在设备锁下复用原 EVENT identity，重复/并发请求最多一次进入 `PENDING`。
- 旧命令仍未终态、出现新 active command、ECS 状态不可信、audit 失败或状态漂移时全部 fail closed。
- 不新增 blocker 专用 SSE；现有 evidence SSE 只提示 `RECONCILING`，Redis 故障不影响持久事实；GET blocker 是完整因果和处理入口的查询真源。
- 普通 EVENT、WorkLine plugin、统一 ECS wire、供应商私有协议、外部现场动作均未扩面。
- 聚焦 FAST、migration、selector manifest HEAVY、QUALITY、Review 和 diff scope 均绑定最终可执行树；未部署或未现场验证时明确报告 `NOT DEPLOYED / NOT ONSITE VERIFIED`。
