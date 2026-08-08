# WES Phase 4 AGV/CTU Transport 基础能力实施计划

> **For agentic workers:** 实施时使用 `superpowers:subagent-driven-development` 或
> `superpowers:executing-plans`，按 Task 顺序执行；代码行为遵循 TDD。

**Goal:** 在不接入当前生产路径的前提下，暗构建 AGV/CTU Transport 的最小可靠履约闭环：持久化不可变搬运请求、
经 WMS 转发提交 RCS/AGV/CTU、可靠接收异步结果，并按权威结果更新本地位置投影。

**Architecture:** 新增窄领域包 `src/app/transport/`，只拥有 `TransportTask`、冻结成员、TransportResult evidence、
本地位置投影和两个窄服务。外部访问复用 Phase 3 `WmsClient`，WMS wire 翻译继续位于 `src/app/wms_adapter/`。
Phase 4 不建设通用执行内核、DeviceCommand、统一设备 Adapter、设备状态、设备 CALLBACK、插件 SDK、Decision 引擎或
WorkLine 运行期能力。Phase 4 只暗装配，Phase 5 才接入生产 Composition Root 并删除 Transport 旧 owner。

**Tech Stack:** Python 3.13、FastAPI、Pydantic 2、SQLModel/SQLAlchemy 2、PostgreSQL 17、Alembic、Phase 3
`WmsClient`、Pytest 9、Ruff、Bandit、GitNexus。

**Status:** `BLOCKED_AT_TASK_0`。Transport submit/result wire 仍为 `ReviewRequired`；合同批准前不得开始代码实施。

## 1. 全局约束

- 系统未发布；不保留旧 API、旧表、旧字段、旧数据、alias、shim、双写、双读或迁移兼容逻辑。
- Phase 4 只建设 AGV/CTU Transport 基础能力，不建设任何 ECS 相关能力。
- WMS 拥有搬运对象、来源、目标、优先级和业务授权；WES 只执行冻结事实并维护本地履约状态与位置投影。
- WES 不选择车辆、路径、交通策略、RCS Endpoint 或设备内部动作，不直连 AGV、CTU 或 ECS。
- 同步 ACK 只表示 WMS 接纳请求；只有匹配的权威 `TransportResult` 可以推进物理终态和位置投影。
- `TransportTask`、结果 evidence 和位置投影的写入 owner 必须唯一；Repository 只执行 SQL/flush，不自行 commit。
- 外部 HTTP 永远不进入数据库事务；发送事实必须在 HTTP 返回后使用新事务保存。
- Phase 4 不注册新 API、Celery task、beat、worker hook 或 Adapter 到当前生产 Composition Root。
- 核心 Transport、WMS Adapter、业务插件和 WMS/RCS 联调测试各自拥有唯一范围，不得相互代测。
- 代码行为按 TDD 实施；本计划是纯文档变更，不新增或修改测试代码，只做文档一致性检查。
- 修改已有函数、类或方法前运行 GitNexus upstream impact；HIGH/CRITICAL 必须暂停并取得用户确认。提交前运行
  GitNexus detect changes。

### 1.1 能力归属

| 层级 | Phase 4 是否建设 | 内容 | 验收 owner |
| --- | --- | --- | --- |
| Transport 核心 | 是 | `TransportTask`、冻结成员、claim/fencing、提交事实、异步结果、对账状态 | Transport FAST/PostgreSQL integration |
| WMS Transport Adapter | 是 | submit/result 固定 wire、DTO、ACK/错误映射、单次发送 | WMS Adapter contract tests |
| Transport 结果证据 | 是 | `event_id` 幂等、摘要冲突、ACK-after-persist、处理终态 | WMS ingress/Transport integration |
| 本地位置投影 | 是 | 货架/料箱最终位置、工作面、unknown、最后权威 evidence | Transport integration |
| 通用执行平台 | 否 | Epoch、Material/Bin Execution、插件 SDK、Decision、通用 EvidenceProcessor | 独立后续计划 |
| ECS/Device | 否 | DeviceCommand、设备状态、统一设备 Adapter、CALLBACK、供应商合同 | 独立 Device/ECS 计划 |
| PickingTask/WorkLine 业务 | 否 | 准入、来源/目标决定、Cell/NG、完成顺序、业务 Fact | 业务计划和插件包 |
| RCS/AGV/CTU 内部实现 | 否 | 车辆、路径、交通、充电、设备动作和供应商协议 | WMS/RCS/供应商验收 |

判定红线：若一个对象必须依赖 PickingTask operation、ECS 设备合同、WorkLine 插件或具体业务完成顺序才能解释，
它就不属于 Phase 4。不得通过“通用事件”“通用命令”“通用执行对象”等名称把未来能力重新包装进本阶段。

## 2. Task 0 实施入口门禁

### Task 0: 冻结 Transport 合同与交接范围

**Review:**

- `docs/contracts/transport-fulfillment-contract.md`
- `docs/contracts/wms-northbound-interaction-contract.md`
- `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- `src/app/wms_adapter/client.py`
- Phase 5 Transport 旧 owner、result callback 和生产装配 owner

**Produces:** 批准后的 submit/result wire、入站身份矩阵、Payload 上限、实施基线 SHA，以及仅限 Transport 的
consumer/successor/NONE 清单。

- [ ] WMS/WES 双方批准固定 path、operation、请求联合类型、六态、ACK、异步结果、错误闭集、幂等和 deadline。
- [ ] 确认当前产品只经 WMS 转发 RCS/AGV/CTU；Phase 4 不直连 AGV、CTU、RCS 或 ECS。
- [ ] 确认 `ROTATE` 与原子 `EXCHANGE` 是否真实获批；未批准则从首版合同、枚举和测试计划全部删除。
- [ ] 冻结结果身份：`data.event_id` 在单次部署范围唯一，`request_id` 只做 HTTP 关联。
- [ ] 冻结 submit/result Payload 和批次成员上限，不在实现阶段凭经验猜测。
- [ ] 冻结同一 Rack/Bin 禁止重叠非终态 Transport 的双方责任、WES 唯一活动绑定和冲突返回；若 WMS 不接受该约束则
  必须先提供可比较的对象级权威序号，不能用 `request_version` 猜测结果新旧。
- [ ] 冻结 submit claim lease、`WmsClient` 最大总耗时和写回事务预算的硬关系；不以 lease 到期推断外部未接纳。
- [ ] 确认 `/api/v1/wms/events` 唯一路由 owner 和静态 operation 分发表；Phase 4 只交付 Transport handler。
- [ ] 使用 GitNexus query/context 和直接源码检查定位 Transport 旧 Effect/Outbox、result callback 和 Composition Root owner。
- [ ] 为每个旧 owner/旧测试记录 Phase 5 successor 或 `NONE`；Device/ECS 和 PickingTask owner 不进入本清单。
- [ ] 保存 `git status --short`、分支、HEAD、`origin/develop` 和合同批准证据。

**Exit:** Transport 合同状态变为 `Approved`，所有字段、边界和 successor/NONE 无未决项。

## 3. 目标结构

### 3.1 生产代码

```text
src/app/transport/
├── __init__.py
├── contracts.py
├── composition.py
├── models/
│   ├── __init__.py
│   ├── transport_task.py
│   ├── result_evidence.py
│   └── position_projection.py
├── repositories/
│   ├── __init__.py
│   ├── transport_task_repository.py
│   └── transport_result_repository.py
└── services/
    ├── __init__.py
    ├── transport_task_service.py
    └── transport_result_service.py

src/app/wms_adapter/
├── transport_wire.py
├── transport_adapter.py
└── transport_event_handler.py
```

`TransportTaskRepository` 拥有任务、冻结成员和位置投影的聚合写入；`TransportResultRepository` 只拥有结果 evidence 的
幂等绑定、领取和处理终态。首版不为只有一个调用方的 SQL 再增加 generic repository、UnitOfWork 或 query service。

### 3.2 测试所有权

```text
tests/runtime/transport/                 # 生命周期、reducer、位置投影、可靠性
tests/contracts/wms_adapter/             # Transport submit/result wire 与 Adapter
tests/integration/transport/             # PostgreSQL claim、唯一约束、事务和暗闭环
tests/integration/wms_adapter/            # TransportResult ingress 持久化后 ACK
```

不得加入 PickingTask、五层货架补给、目标架换面流程、料箱投放/回收等业务 happy path。测试可以使用无业务含义的
Rack/Bin fixture 验证公共合同，但不得用真实工作线流程证明 Transport 核心。

### 3.3 最小处理流水线

```text
已批准的业务事实（Phase 4 外）
        │
        ▼
TransportTaskService.create() ── COMMIT
        │
        ▼ claim_due(limit, lease)
TransportTaskService.begin_submit() ── COMMIT
        │
        ▼
WmsTransportAdapter.submit() ── 单次 HTTP，事务外
        │
        ▼
TransportTaskService.record_submit_result() ── 新事务

WMS TransportResult ingress
        │ parse stable identity + normalize within contract limit
        ▼
TransportEventHandler.handle()
        │ static operation owner，非 FastAPI route
        ▼
TransportResultService.bind() ── COMMIT ──► ACK / DUPLICATE / CONFLICT
        │
        ▼ claim_pending(limit, lease)
TransportResultService.apply()
        └── ONE DB TRANSACTION
            ├── 校验 task/version/action/object/frozen members
            ├── 推进 TransportTask 终态或 RECONCILING
            ├── bulk 更新最终位置/工作面/unknown
            └── 标记 result evidence PROCESSED / REJECTED
```

首版只有 TransportResult evidence，不定义 `DEVICE_EVENT`、`DEVICE_RESULT`、普通 WMS Event kind 或动态 operation registry。
Phase 4 只交付接收 `transport.task.resulted@v1` 的 operation-scoped `TransportEventHandler`，不拥有或注册共享
`POST /api/v1/wms/events` FastAPI route。Phase 5 由唯一 WMS event route 使用静态 `match` 分发到该 Handler。

## 4. 实施任务

### Task 1: 建立 Transport 模型与数据库约束

**Files:**

- Create: `src/app/transport/models/transport_task.py`
- Create: `src/app/transport/models/result_evidence.py`
- Create: `src/app/transport/models/position_projection.py`
- Create: `src/app/transport/models/__init__.py`
- Create: `src/app/transport/__init__.py`
- Modify: `migrations/env.py`
- Create via Alembic generator: one revision with message `add transport fulfillment core`
- Test: `tests/runtime/transport/test_model_contracts.py`
- Test: `tests/integration/transport/test_transport_schema.py`

**Produces:**

- `TransportTask` 六态：`PENDING / ACCEPTED / REJECTED / SUCCEEDED / FAILED / RECONCILING`。
- `reconciliation_cause` 闭集：`SUBMIT_DELIVERY_UNKNOWN / RESULT_DEADLINE_EXCEEDED / RESULT_CONFLICT /
  POSITION_UNKNOWN`；只允许 `SUBMIT_DELIVERY_UNKNOWN` 重新进入 submit claim。
- `TransportTaskMember`：只保存 Bin 批次冻结成员和最终成员事实，不建立成员级第二套生命周期。
- `TransportResultEvidence`：部署级唯一 `event_id`、规范化摘要、原始 Payload、首次 ACK、处理状态、claim/fencing。
- `TransportPositionProjection`：对象类型、对象 ID、最终位置、工作面、`position_unknown` 和最后权威 evidence。
- 不可变提交快照：请求类型、请求版本、WMS `authority_refs`、action、来源、目标、对象/成员和 Payload 摘要。

- [ ] 先写失败测试锁定枚举、非空字段、唯一约束、外键方向、不可变身份和禁止字段。
- [ ] 明确断言不存在 Epoch、Material/Bin Execution、DeviceCommand、设备投影、插件/Decision 字段。
- [ ] 覆盖 `(transport_task_id, request_version)` 唯一、`event_id` 部署级唯一、同身份同摘要可重提、异摘要冲突。
- [ ] 覆盖 Rack 与 BinBatch 联合约束、冻结成员无重复、空成员拒绝和批准后的成员上限。
- [ ] Task 0 必须冻结同一 Rack/Bin 是否允许重叠 Transport；首版推荐 WMS 禁止重叠，WES 使用数据库唯一活动绑定失败关闭。
- [ ] Rack 任务和 Bin 成员分别建立可原子释放的 active binding；同一对象只能属于一个非终态 TransportTask，终态事务内释放。
- [ ] 为真实查询建立最小索引，不加入未来状态、设备、WorkLine 或供应商字段。
- [ ] 先运行 `uv run alembic check`，再用 Alembic generator 生成 revision；不得手写 revision ID。
- [ ] 在隔离 PostgreSQL 空库执行 upgrade，验证 schema、约束和索引。
- [ ] 提交：`feat(transport): 建立运输履约模型`

### Task 2: 实现 TransportTask Repository、claim 与可靠提交

**Files:**

- Create: `src/app/transport/contracts.py`
- Create: `src/app/transport/repositories/transport_task_repository.py`
- Create: `src/app/transport/repositories/__init__.py`
- Create: `src/app/transport/services/transport_task_service.py`
- Create: `src/app/transport/services/__init__.py`
- Test: `tests/runtime/transport/test_transport_task_lifecycle.py`
- Test: `tests/integration/transport/test_transport_task_claiming.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

```text
TransportPort.submit(RackTransportRequest | BinBatchTransportRequest) -> TransportSubmitResult
TransportTaskService.create(request) -> TransportTask
TransportTaskService.claim_due(owner, limit, lease_seconds, now) -> Sequence[TransportTask]
TransportTaskService.begin_submit(task_id, claim_token) -> ImmutableTransportRequest
TransportTaskService.record_submit_result(task_id, claim_token, result) -> TransportTask
TransportTaskService.mark_result_deadline_exceeded(task_id, observed_at) -> TransportTask
```

- [ ] 写失败测试覆盖创建、不可变版本、ACK 映射、拒绝、冲突、delivery unknown、deadline 和六态迁移。
- [ ] 覆盖四种 `reconciliation_cause`；`claim_due()` 只选择安全的 `PENDING` 和
  `RECONCILING + SUBMIT_DELIVERY_UNKNOWN`，deadline/conflict/position-unknown 永不重新 submit。
- [ ] 覆盖 `limit <= 0`、`limit > 100`、稳定顺序、空队列、lease 回收、旧 token 写回拒绝和并发 worker 不重复领取。
- [ ] 使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 实现小批量 claim；不自研队列、不创建通用 claim service。
- [ ] `begin_submit()` 在发送前提交 claim/attempt 事实；HTTP 在事务外执行；结果使用新事务保存。
- [ ] Task 0 冻结 `lease_seconds > WmsClient 最大总耗时 + 结果写回事务预算`；活动 attempt 未结束前不得仅因 lease 到期并发重发。
- [ ] 增加阻塞 HTTP 超过旧 lease 阈值的 PostgreSQL 并发测试；使用硬超时上限解决，不引入 heartbeat。
- [ ] 已取得 `RECEIVED/DUPLICATE` 后不得再次提交；只允许合同批准的同身份、同版本、同 Payload 收敛。
- [ ] `RECONCILING` 不自动变成失败，不换身份重提，不猜测现场状态。
- [ ] Transport Port 不持久化、不重试、不读取 WMS/RCS 内部状态。
- [ ] 若保留 `ROTATE`，`create()` 必须在同一事务锁定当前 Rack 位置投影，当前面缺失/unknown 或等于 `target_face` 时拒绝；
  若合同不批准该准入 owner，则从首版删除 `ROTATE`。
- [ ] 为新增生产路径补精确 HEAVY mapping，不用空列表掩盖 PostgreSQL 并发影响。
- [ ] 提交：`feat(transport): 实现可靠运输提交`

### Task 3: 实现 WMS Transport Adapter

**Files:**

- Create: `src/app/wms_adapter/transport_wire.py`
- Create: `src/app/wms_adapter/transport_adapter.py`
- Modify: `src/app/wms_adapter/__init__.py`
- Test: `tests/contracts/wms_adapter/test_transport_adapter.py`

**Produces:** `WmsTransportAdapter(WmsClient)`，固定 `POST /api/v1/wes/transport-requests`、
`transport.task.submit@v1`、闭集 DTO 和 `TransportSubmitResult` 映射。

- [ ] 写失败测试覆盖 Rack/Bin 联合 DTO、公共信封、ACK、重复、拒绝、冲突、BUSY/UNAVAILABLE 和 delivery unknown。
- [ ] 锁定 `ROTATE/EXCHANGE` 合同门禁；未批准时相关枚举、字段和测试全部缺席。
- [ ] 实现一次 `WmsClient.post()` 调用和严格 Pydantic 解码；不得访问数据库或解释 `TransportTask` 状态。
- [ ] 验证 Adapter 无 status query、cancel、retry/backoff、动态 registry、直接 RCS/AGV/CTU/ECS Client。
- [ ] 运行 WMS Adapter 合同测试、Phase 3 Client 回归、Ruff 和 Import Linter。
- [ ] 提交：`feat(wms-adapter): 实现 Transport 提交合同`

### Task 4: 实现 TransportResult 入站、唯一 reducer 与位置投影

**Files:**

- Create: `src/app/transport/repositories/transport_result_repository.py`
- Create: `src/app/transport/services/transport_result_service.py`
- Modify: `src/app/transport/repositories/__init__.py`
- Modify: `src/app/transport/services/__init__.py`
- Create: `src/app/wms_adapter/transport_event_handler.py`
- Modify: `src/app/wms_adapter/__init__.py`
- Test: `tests/contracts/wms_adapter/test_transport_event_handler.py`
- Test: `tests/runtime/transport/test_transport_result_service.py`
- Test: `tests/integration/transport/test_transport_result_transaction.py`
- Test: `tests/integration/wms_adapter/test_transport_event_handler.py`

**Interfaces:**

```text
TransportEventHandler.handle(operation=transport.task.resulted@v1, envelope)
TransportResultService.bind(input) -> TransportResultAck
TransportResultService.claim_pending(owner, limit, lease_seconds, now) -> Sequence[TransportResultEvidence]
TransportResultService.apply(evidence_id, claim_token) -> TransportResultOutcome
```

- [ ] 写失败测试覆盖 DTO、`event_id` 唯一、ACK-after-persist、重复、冲突、绑定失败不 ACK 和 Payload 上限。
- [ ] 覆盖未知任务、版本/action/对象/成员不匹配、缺少成员、矛盾终态、迟到结果和终态后重复结果。
- [ ] 覆盖 Rack 最终位置/工作面、Bin 全量成员、部分失败、position unknown、批准后的 ROTATE/EXCHANGE 原子性规则。
- [ ] Handler 只解析固定 operation/DTO 并调用 service；不得拥有 FastAPI route、直接访问数据库、执行 reducer 或建立 registry。
- [ ] `apply()` 首先 `SELECT ... FOR UPDATE` 锁定 TransportTask 并在锁内重检终态；随后按稳定顺序锁定成员/投影，
  在一个事务内推进任务、bulk 更新投影并标记 evidence；任一步失败整体回滚。
- [ ] 增加两个不同 `event_id` 并发提交相同终态和矛盾终态的 PostgreSQL 测试；后到者只能幂等留痕或进入冲突对账，
  不得覆盖已接受终态。
- [ ] 批次一次 bulk 读取冻结成员并批量写入最终事实，禁止循环内逐成员查询或 commit。
- [ ] 只有匹配的权威结果可以推进终态；ACK、callback hint、普通 WMS Event 和人工口述均不能终结任务。
- [ ] 提交：`feat(transport): 接收并应用运输结果`

### Task 5: 暗 Composition、Phase 5 交接与最终验收

**Files:**

- Create: `src/app/transport/composition.py`
- Modify: `src/app/transport/__init__.py`
- Modify: `docs/architecture/file_index.md`
- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Test: `tests/architecture/test_transport_boundaries.py`
- Test: `tests/integration/transport/test_dark_transport_loop.py`

**Produces:** 一个只能显式构造、不会自动注册的 Transport 装配函数，以及 Phase 5 Transport consumer/successor/NONE 清单。

- [ ] 写失败架构测试：Transport 核心零 `httpx`、零旧 Runtime/Intent/Effect/SystemOutbox、零 Device/ECS import；
  WMS Adapter 不依赖 Repository；ingress 不直接执行 SQL。
- [ ] 写失败暗装配测试：构造服务不会注册 route、Celery task、beat、全局 singleton 或生产 observer。
- [ ] 实现显式构造函数，只接收已创建的 `WmsClient`、repositories 和 session factory；不读取全局容器。
- [ ] 使用真实 PostgreSQL 完成暗闭环：Task → claim → fake TransportPort ACK → Result bind/ACK → apply → projection。
- [ ] 冻结 Phase 5 对 Transport 旧 Effect/Outbox、result callback 和 Composition Root 的唯一 successor/NONE。
- [ ] Phase 5 清单必须精确到文件/符号冻结三个生产 owner：submit dispatcher、result evidence processor、
  result-deadline sweeper；Phase 4 只交付可调用的窄入口和暗测试，不注册调度任务。
- [ ] Phase 5 唯一 WMS event route 必须静态分发 `transport.task.resulted@v1` 到 `TransportEventHandler`；
  不新增同 method/path 的第二条 FastAPI route，也不截断普通 WMS event。
- [ ] 明确 DeviceCommand、统一设备 Adapter、设备 CALLBACK 和 ECS 旧 owner 不属于本阶段及本清单。
- [ ] 运行 GitNexus detect changes，确认新代码未进入旧生产执行流。
- [ ] 提交：`feat(transport): 完成 Phase 4 暗装配与交接`

## 5. Phase 5 Transport successor/NONE 矩阵

Phase 4 不修改下列旧资产，Task 0/5 只重新验证并冻结处置：

| 旧资产 | Phase 5 successor | Phase 4 目标测试 |
| --- | --- | --- |
| WMS Transport Effect/status/Outbox 分支 | `TransportTaskService` + `TransportPort` | `tests/runtime/transport/test_transport_task_lifecycle.py` |
| Transport callback hint/status 分支 | `NONE` | 无承接测试；Phase 5 删除对应 route/payload/OpenAPI/tests |
| Transport result callback | 共享 WMS event route → `TransportEventHandler` + `TransportResultService` | handler contract + result transaction |
| Transport 位置写回旧分支 | `TransportTaskRepository` 聚合投影写入 | `tests/integration/transport/test_transport_result_transaction.py` |
| Transport 生产 Composition Root | `transport/composition.py` 显式目标装配 | `tests/integration/transport/test_dark_transport_loop.py` |
| Transport submit dispatcher | Phase 5 静态调度 owner → `TransportTaskService` | lifecycle + claiming integration |
| Transport result processor | Phase 5 静态调度 owner → `TransportResultService.claim_pending/apply` | result concurrency integration |
| Transport deadline sweeper | Phase 5 静态调度 owner → `mark_result_deadline_exceeded` | deadline lifecycle test |

旧测试删除必须遵守“先建立 successor，再删除旧 owner”。只证明旧 Effect、旧 schema、旧 callback hint 或旧 Outbox 枚举且
没有最终 Transport 语义的测试标记 `NONE`，不得为了保留测试而增加兼容层。

## 6. 验证命令与通过标准

### 6.1 每个任务

| Task | FAST | PostgreSQL integration |
| --- | --- | --- |
| 1 | `uv run pytest tests/runtime/transport/test_model_contracts.py -q` | `uv run pytest tests/integration/transport/test_transport_schema.py -q` |
| 2 | `uv run pytest tests/runtime/transport/test_transport_task_lifecycle.py -q` | `uv run pytest tests/integration/transport/test_transport_task_claiming.py -q` |
| 3 | `uv run pytest tests/contracts/wms_adapter/test_transport_adapter.py tests/contracts/wms_adapter/test_client.py -q` | selector 显式结果 |
| 4 | `uv run pytest tests/contracts/wms_adapter/test_transport_event_handler.py tests/runtime/transport/test_transport_result_service.py -q` | `uv run pytest tests/integration/transport/test_transport_result_transaction.py tests/integration/wms_adapter/test_transport_event_handler.py -q` |
| 5 | `uv run pytest tests/architecture/test_transport_boundaries.py -q` | `uv run pytest tests/integration/transport/test_dark_transport_loop.py -q` |

每个任务追加：

```bash
uv run ruff format --check .
uv run ruff check .
git diff --check
```

修改测试拓扑或生产候选路径时追加：

```bash
uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
uv run pytest tests/scripts -q
uv run scripts/select_heavy_tests.py --scope unstaged
```

纯文档评审阶段不运行 pytest；以上命令只约束后续代码实施。

### 6.2 Phase 4 退出

1. Transport submit/result 合同已批准，path、DTO、错误、幂等、deadline 和 fixture 无未决项。
2. 空 PostgreSQL 完整 migration upgrade 成功。
3. `TransportTask` 只有 submit + async result；零 status query、cancel、callback hint 和动态 registry。
4. `(transport_task_id, request_version)`、结果 `event_id` 唯一、claim/fencing 和单事务结果应用通过 PostgreSQL 并发测试。
5. 同一 Rack/Bin 最多属于一个非终态 TransportTask；晚到旧任务结果不能覆盖新任务位置。
6. WMS Adapter 每次调用最多执行一次 `WmsClient.post()`，不持久化、不重试、不解释任务状态。
7. TransportResult 必须 ACK-after-persist；并发结果锁定任务，只有匹配权威结果可以推进任务与位置投影。
8. 批次成员有界并使用 bulk I/O，无逐成员 N+1 查询或循环 commit。
9. Transport 核心零 `httpx`、ECS、DeviceCommand、设备合同、PickingTask 和旧 Runtime/Effect import。
10. 新 route、Celery task、Adapter 和 worker 未注册到当前生产 Composition Root。
11. Phase 5 三个后台 owner、共享 WMS route 静态分发和 successor/NONE 清单无未决项。
12. GitNexus detect changes 无意外生产调用链。

## 7. 停止条件

出现以下任一情况立即停止：

- Transport submit/result wire 未批准或字段仍可能变化；
- 需要直连 RCS、AGV、CTU 或 ECS 才能证明能力；
- 需要 DeviceCommand、设备状态、统一设备 Adapter 或设备 CALLBACK；
- 需要从 PickingTask、WorkLine 插件或旧 Runtime/Effect 测试推断 Transport 业务语义；
- 新代码必须接入生产 Composition Root 才能验收；
- Transport 核心需要读取供应商私有 Payload、车辆、路径或设备内部状态；
- Alembic autogenerate 包含未授权旧表修改；
- GitNexus impact 为 HIGH/CRITICAL，或 detect changes 显示意外生产调用链；
- HEAVY selector fail closed 且没有获批映射。

## 8. What already exists

| 现有能力 | 本计划如何处理 | 边界 |
| --- | --- | --- |
| Phase 2 `src/core/outbound_http/` | 由 `WmsClient` 间接复用 | 不拥有 Transport 状态、业务重试或投影 |
| Phase 3 `src/app/wms_adapter/WmsClient` | 直接复用 HTTP/JSON 薄封装 | 不拥有 Transport 生命周期 |
| `docs/contracts/transport-fulfillment-contract.md` | 作为唯一 wire 评审基线 | `Approved` 前阻塞实施 |
| 现有 WMS Transport Effect/Outbox | 只作为 Phase 5 删除范围证据 | 不复用模型、service、状态或测试 oracle |
| 现有 RuntimeInbox/SystemOutbox | 只作为旧 owner 搜索证据 | 新实现不 import、不写入、不兼容 |
| SQLModel、AsyncSession、BaseRepository 基础设施 | 按项目分层复用 | 不再包 generic UnitOfWork/Service Locator |
| `tests/README.md` 与 HEAVY selector | 直接复用测试治理 | Transport、Adapter、业务和联调继续隔离 |

## 9. NOT in scope

- 所有 ECS/Device 能力：DeviceCommand、设备状态、统一设备 Adapter、设备 Event/CALLBACK、供应商合同和设备准入。
- 通用执行平台：LineRunEpoch、Material/Bin Execution、插件 SDK、Decision、EvidenceProcessor、通用 workflow/queue。
- PickingTask Event/Decision/Fact、任务完成顺序、NG、Cell、货架换面策略、料箱投放/回收规则等业务能力。
- WES 直连 RCS/AGV/CTU，以及车辆、路径、交通、充电、设备内部动作和供应商私有协议。
- Transport status query、进度订阅、轮询、取消、暂停、恢复、改派、换车或自动补偿。
- Phase 5 生产切换、旧 owner/旧表/旧字段/旧测试删除。
- UI、运营看板、供应商 Runbook、设备联调和 Redis 审计。
- 旧数据迁移、兼容 schema、alias、shim、fallback 或双轨。
- `docs/hardware/` 厂商原始资料；保留且不作为核心架构真源。
- 公网级零信任、复杂签名和凭据平台；系统运行于纯局域网。

## 10. 测试覆盖图

此图表达后续实施必须具备的测试，不声称尚未实现的代码已经通过。

```text
CODE PATHS                                             SYSTEM FLOWS
[+] TransportTask                                      [+] 提交搬运请求
  ├── [PLANNED ★★★] 不可变请求与版本                    ├── [PLANNED ★★★] 先持久化再发送
  ├── [PLANNED ★★★] 六态合法迁移                        ├── [PLANNED ★★★] ACK/拒绝/冲突
  ├── [PLANNED ★★★] 四种 reconciliation cause          ├── [PLANNED ★★★] 只有 delivery unknown 可重提
  ├── [PLANNED ★★★] 单对象活动绑定                      ├── [PLANNED ★★★] 重叠 Rack/Bin 失败关闭
  ├── [PLANNED ★★★] claim/lease/fencing                └── [PLANNED ★★★] delivery unknown → RECONCILING
  └── [PLANNED ★★★] deadline 不伪造终态

[+] WMS Transport Adapter                              [+] 经 WMS 转发 RCS/AGV/CTU
  ├── [PLANNED ★★★] 固定 path/operation/DTO             ├── [PLANNED ★★★] 单次 HTTP
  ├── [PLANNED ★★★] Rack/Bin 联合请求                   └── [PLANNED ★★★] 无直连设备系统
  └── [PLANNED ★★★] 严格 ACK/错误映射

[+] TransportEventHandler                              [+] WMS 异步结果
  ├── [PLANNED ★★★] 共享 route 静态 operation 分发      ├── [PLANNED ★★★] 不注册第二条同路径 route
  ├── [PLANNED ★★★] event_id + digest                  ├── [PLANNED ★★★] 持久化后 ACK
  ├── [PLANNED ★★★] duplicate/conflict                 ├── [PLANNED ★★★] 同身份重提收敛
  └── [PLANNED ★★★] Payload 上限                        └── [PLANNED ★★★] 冲突进入对账

[+] TransportResultService                             [+] 应用权威终态 [→INTEGRATION]
  ├── [PLANNED ★★★] 锁 task 后重检终态                  ├── [PLANNED ★★★] 并发结果不覆盖
  ├── [PLANNED ★★★] task/version/action/object 校验     ├── [PLANNED ★★★] Task + projection + evidence 单事务
  ├── [PLANNED ★★★] 完整冻结成员校验                    ├── [PLANNED ★★★] 部分失败/unknown
  ├── [PLANNED ★★★] bulk 位置更新                       └── [PLANNED ★★★] 崩溃回滚后幂等重领
  └── [PLANNED ★★★] 重复/矛盾/迟到结果

COVERAGE TARGET: 计划内分支 100%
QUALITY TARGET: 全部 ★★★（成功 + 边界 + 错误）
INTEGRATION: PostgreSQL claim、唯一约束、ACK-after-persist、结果应用事务、暗闭环
EVAL / 浏览器 E2E: 不适用
```

## 11. 生产失败模式

| 路径 | 真实失败 | 测试 | 处理与可见性 |
| --- | --- | --- | --- |
| Transport claim | 两个 worker 领取同一任务 | PostgreSQL concurrency | `SKIP LOCKED` + token；旧 owner 写回被拒绝并记录 |
| submit | 请求可能送达但响应丢失 | lifecycle/adapter | 同身份同 Payload 收敛；进入 `RECONCILING`，不换身份 |
| submit | WMS 明确拒绝或冲突 | lifecycle/adapter | `REJECTED` 或 `RECONCILING`，保留稳定错误事实 |
| result handler | bind commit 后进程崩溃 | handler integration | WMS 重提得到原 ACK；不生成第二条 evidence |
| result handler | 同 `event_id` 不同 Payload | contract/integration | 返回冲突并告警，不覆盖首次事实 |
| result reducer | 两个不同 event 并发终结同一任务 | PostgreSQL concurrency | 锁任务并重检终态；后到者不得覆盖 |
| object order | 旧任务结果晚于新任务 | overlap/result integration | 单对象活动绑定阻止重叠；投影不倒退 |
| reducer | 结果成员缺失或多出 | result service | 失败关闭并进入对账，不部分终结任务 |
| reducer | 投影写入后 evidence 标记失败 | transaction test | 整体回滚，重领后只产生同一结果 |
| projection | 外部报告位置未知 | result service | 显式 `position_unknown`，不猜测仍在来源位置 |
| batch | 大批次形成 N+1 | query-shape integration | 合同上限 + bulk I/O，超限发送前拒绝 |

无“没有测试、没有错误处理且静默失败”的已知路径。

## 12. 并行实施策略

| Step | Modules touched | Depends on |
| --- | --- | --- |
| Task 0 合同门禁 | `docs/contracts/`, `docs/superpowers/` | — |
| Task 1 模型与 schema | `src/app/transport/models/`, `migrations/` | Task 0 |
| Task 2 生命周期 | `src/app/transport/repositories/`, `services/` | Task 1 |
| Task 3 WMS Adapter | `src/app/wms_adapter/` | Task 0 |
| Task 4 结果闭环 | `src/app/transport/`, `src/app/wms_adapter/` | Task 2 + Task 3 |
| Task 5 暗装配 | `src/app/transport/`, `docs/architecture/` | Task 4 |

```text
Task 0
  ├── Lane A: Task 1 → Task 2
  └── Lane B: Task 3
            │
            ▼
          Task 4 → Task 5
```

Task 0 关闭后，Lane A 与 Lane B 可以并行 worktree 实施；Task 4 等待两条 lane 合并。Lane A/B 不共享生产目录，
但都可能修改测试治理配置时必须指定唯一 owner，避免 `heavy-test-impact.toml` 冲突。

## 13. Implementation Tasks

- [ ] **T1 (P1, human: ~4h / CC: ~30min)** — Transport contract — 关闭 submit/result wire 门禁
  - Surfaced by: Scope Challenge — Phase 4 只保留 AGV/CTU Transport，必须先冻结唯一外部合同
  - Files: `docs/contracts/transport-fulfillment-contract.md`
  - Verify: 合同状态 `Approved`，path/DTO/错误/幂等/deadline/fixture 无未决项
- [ ] **T2 (P1, human: ~1d / CC: ~2h)** — Transport persistence — 建立任务、结果证据和位置投影
  - Surfaced by: Architecture Review — Transport 闭环只保留三个持久化 owner
  - Files: `src/app/transport/models/`, `migrations/versions/`
  - Verify: model FAST + PostgreSQL schema integration
- [ ] **T3 (P1, human: ~2d / CC: ~4h)** — Transport lifecycle — 实现可靠提交与六态 reducer
  - Surfaced by: Architecture/Performance Review — claim、delivery unknown、deadline 和 bulk I/O
  - Files: `src/app/transport/repositories/`, `src/app/transport/services/`
  - Verify: lifecycle FAST + PostgreSQL concurrency
- [ ] **T4 (P1, human: ~1d / CC: ~2h)** — WMS Adapter — 实现固定 Transport submit wire
  - Surfaced by: Architecture Review — 复用 WmsClient，禁止直连 RCS/AGV/CTU/ECS
  - Files: `src/app/wms_adapter/transport_wire.py`, `transport_adapter.py`
  - Verify: WMS Adapter contract + Phase 3 regression
- [ ] **T5 (P1, human: ~2d / CC: ~4h)** — Transport result — 实现 ACK-after-persist 与原子结果应用
  - Surfaced by: Test Review — 结果身份、冲突、成员完整性、事务和投影全部需要覆盖
  - Files: `src/app/transport/`, `src/app/wms_adapter/transport_event_handler.py`
  - Verify: result contract + PostgreSQL transaction integration
- [ ] **T6 (P2, human: ~4h / CC: ~45min)** — Dark composition — 完成交接与边界验收
  - Surfaced by: Code Quality Review — Phase 4 不得注册到生产路径
  - Files: `src/app/transport/composition.py`, `docs/architecture/file_index.md`
  - Verify: architecture guardrail + dark loop + GitNexus detect changes

## 14. GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | CLEAR | 7 项 Transport 可靠性缺口全部按最小修正写回 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | CLEAR | 11 项已写回；范围收敛为 AGV/CTU Transport，ECS/Device 与通用执行平台全部移出 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端基础能力，不适用 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**VERDICT:** ENG CLEARED — Phase 4 只建设 AGV/CTU Transport 基础能力；Task 0 合同批准后方可实施。

**CODEX:** 对账原因、并发结果、对象重叠、后台 owner、共享路由、lease/timeout 和 ROTATE 准入已闭合。

NO UNRESOLVED DECISIONS
