# Phase 9 Minimum Execution Foundation 实施计划

status: Implementation candidate verified in worktree; final staged exit gate and develop integration pending
decision_date: 2026-08-28
owner: WES 基础执行能力
depends_on: Gate A 开发流程基线、Gate B 运输接入诊断、Phase 8 backend RC
blocks: Phase 10 Execution Lock、Phase 11 Schema 基线

## 1. 目标

只交付 Phase 10 删除旧平台前必须存在的最小执行内核和当前生产 successor：

- `BinExecution`；
- RACK/BIN 共用的唯一当前态 `PositionProjection` schema；
- WorkLine unfinished-work target aggregate；
- `ESTOP_PRESSED` final router 与 incident-owned drain；
- E03/E07 `WmsConfirmation` barrier；
- 最小 WMS target configuration；
- 当前 operation consumer 的 `RETAIN / SWITCH / DELETE → NONE` 清单；
- 删除无人使用的同步 OpenTelemetry HTTP backend，同时保留内部 registry、signal 和 bridge。

Phase 9 按三个连续验收切片实施：

```text
Slice A：BinExecution + PositionProjection + migration + Transport authority/consumer
   │
   ▼
Slice B：unfinished aggregate + FIFO/START gate + ESTOP + WmsConfirmation
   │
   ▼
Slice C：WMS target config + OpenTelemetry 删除 + operation inventory/Phase 10 handoff
```

前一切片的聚焦验收未通过，不开始后一切片；三个切片共享 execution/transport schema、Composition 和 HEAVY mapping，
不得拆成并行写 worktree。

## 2. What already exists

| 当前事实 | Phase 9 处置 |
| --- | --- |
| `MaterialExecution` | 保留物料执行语义，增加不可变 FIFO admission identity；不得用它顶替 `BinExecution` |
| `TransportTask`、member、Evidence 与资源 binding | 原样复用可靠 Transport aggregate；增加本地 execution authority，不重建 Transport |
| `TransportPositionProjection` | 一次性替换为唯一 `PositionProjection`；Transport 通过注入 port 成为消费者 |
| `RuntimeInbox` unfinished 查询 | 切换到读取最终可靠对象的一次 SQL snapshot，不建立第二套 workload 表或缓存 |
| `WorkLineSafetyService.handle_estop()` | 保留为安全事务 owner，由 Device `InboundEvidence` application boundary 的 final router 调用 |
| `WmsConfirmation` | 拆分 lifecycle service 与 dispatcher，承接 E03/E07 双义务及 execution barrier |
| WMS Profile、typed Adapter/Service、Phase 2 HTTP factory | 复用为单一 target runtime，不新增 registry、credential lane 或 raw client |
| `RuntimeOpenTelemetryHttpExporter` | 删除 exporter、配置、lifespan wiring 和专属测试；保留内部 registry、signal 和 bridge |
| `LineRunEpoch` fence、PostgreSQL advisory lock 先例 | 复用现有 fence 与 `hashtextextended(..., 0)` 约定，不引入新锁服务 |
| HEAVY selector | 继续以 `docs/architecture/heavy-test-impact.toml` 为唯一 mapping 真源 |

## 3. 冻结领域合同

### 3.1 `BinExecution` 与 `PositionProjection`

- `BinExecution` 只保留 `ACTIVE / CLOSED` 最小生命周期及当前已有 producer 能证明的关闭语义；Phase 9 不加入
  `NG_MANUAL_TAKEOVER_CONFIRMED`、NG provenance 或其它尚无批准 producer 的状态。
- 同一 `bin_id` 由 partial unique index 保证最多一个 ACTIVE execution；创建路径先获取
  `bin-execution\x1f{bin_id}` transaction advisory lock，再在同一事务中复核活动记录。
- RACK 与 BIN 共用一个 `PositionProjection` schema，以 `(object_type, object_id)` 唯一标识对象；各自生命周期仍由
  `LineRunEpoch`、`BinExecution` 和可靠 Transport Evidence 管辖，不能把两类对象合并成同一生命周期。
- projection 是 current-only：BinExecution 关闭时删除其 BIN projection，Epoch 关闭时删除该 Epoch 剩余 projection；
  历史位置继续由 Transport Evidence/member 保存。
- projection 只保存封闭 normalized current state；raw payload 留在 Evidence/member，不增加 occupancy 唯一约束。
- projection 更新与触发它的 Transport terminal Evidence 在同一数据库事务提交，不能先形成终态、后异步补投影。

并发锁序固定为：

```text
Epoch lifecycle fence
  → BinExecution row（BIN only）
  → position-projection\x1f{object_type}\x1f{object_id} advisory lock
  → PositionProjection row/upsert
  → recheck Epoch/BinExecution ACTIVE
```

BinExecution 关闭使用相同前缀锁序后删除 BIN projection；Epoch 关闭取得 exclusive epoch fence 后删除剩余 projection。
所有 advisory identity 由一个共享 key builder 生成，再交给 PostgreSQL `hashtextextended(..., 0)`；禁止 Python `hash()`。

### 3.2 Transport execution authority

发往 WMS 的 `TransportCaller` 与本地 `TransportExecutionAuthority` 分离。后者只由内部 execution path 创建，包含
`workline_id`、`line_run_epoch_id` 和可选 `bin_execution_id`，创建 TransportTask 时持久化，并满足全有或全无的数据库约束。

结果回写只能读取任务已冻结的 authority，禁止事后查询“当前 Epoch”。debug Transport 始终没有 execution authority：它可以执行设备联调
并保留 TransportTask、member 和 Evidence，但不能写核心 `PositionProjection`。

### 3.3 Phase 9 admission/return FIFO 与 unfinished-work snapshot

Phase 9 范围内的 WorkLine 入站 admission 与 Bin RETURN 分别按 `(workline_id, line_run_epoch_id)` 严格 FIFO，不允许后到对象跳过
被 retry、unknown、reconciling 或人工对账阻塞的队头。该规则不把全部物料运动定义为 FIFO；顶端可达的 `BIN_CELL` 堆叠按出库
PickingTask 合同执行 LIFO，不属于本阶段的业务实现范围：

```text
InboundEvidence (received_at, id)
  → MaterialExecution admission (admission_received_at, admission_evidence_id)
  → E03 confirm_inbound
  → E07 notify_pkg_binding
  → BIN RETURN order (positioned_at, id)
```

- `MaterialExecution` 持久化不可变 `admission_received_at`、`admission_evidence_id`，并为活动队头建立 partial keyset index。
- 同一 execution 必须先闭合 E03，才能进入 E07；前序 timeout、拒绝、delivery unknown 或 reconciliation 会阻断后序。
- 不同 WorkLine 可以并行，同一 WorkLine/Epoch 不得通过扩大 worker concurrency 绕过 FIFO。
- unfinished gate 使用一条 SQL snapshot，由带索引的 `EXISTS` 生成各 owner 精确布尔值和确定性 sample；sample 只用于诊断，
  不能参与是否放行，也不计算无用 exact counts、不引入缓存。
- snapshot 覆盖活动 `LineRunEpoch`、`MaterialExecution`、`BinExecution`、`DeviceCommand`、`TransportTask`、
  `InboundEvidence` 和 `WmsConfirmation`。`InboundEvidence` 沿用发布静默门禁谓词：`PENDING`、可 claim 的
  `APPLIED + published_at IS NULL` 和 `RECONCILING` 阻断；`IGNORED`、已发布 `APPLIED` 与未绑定诊断结果按现有规则处理。

ESTOP clear 后，新 START 必须在同一 WorkLine/Epoch fence 下读取该 snapshot。任一旧 Epoch owner 未闭合时拒绝 START，
旧 Epoch 保持原 identity；不得迁移、重放或把未完成义务降为诊断记录。只有完成可靠对账和物理闭合后，START 才能关闭旧 Epoch 并开启新 Epoch。

### 3.4 ESTOP final router

- final router 位于 Device `InboundEvidence` application boundary，可靠保存事件后，在同一 session 调用
  `WorkLineSafetyService.handle_estop()`；WorkLine safety 仍是唯一安全 owner。
- 主事务只做 O(1) barrier-first 变更：建立/复用 active `WorklineSafetyIncident`，把 WorkLine/Epoch 置为禁止继续 admission，
  并登记 incident-owned post-commit drain。
- drain 以有界 keyset/set-based 批次收敛未下发工作；失败由同一 incident 重试。已发送、ACK 未知或正在搬运的命令保持原 identity，
  进入 reconciliation，不盲取消、不重发、不改写结果。
- 同一切片删除新 admission 对 `RuntimeHold` 的依赖；active safety incident 是唯一 ESTOP blocker。clear 只关闭 incident，
  不自动恢复旧编排，必须等待新的 START 且受 3.3 的旧 Epoch unfinished gate 约束。

### 3.5 E03/E07 `WmsConfirmation` barrier

- `WmsConfirmationLifecycleService` 管创建、幂等、拒绝、歧义、重试资格和 execution barrier；Dispatcher 只负责 lease/HTTP/结果回写。
- E03/E07 以 `material_execution_id` 为互斥身份：先锁 `MaterialExecution`，再按固定 operation 顺序锁 confirmations；
  禁止 generic Barrier、RuntimeIntent、Hold、ReconciliationCase 或旧 Provider status lane。
- `WmsConfirmation` 只保留必要 execution FK/index；FIFO identity 属于 `MaterialExecution`，不得复制到 confirmation。
- E03 `wms.inventory.confirm_inbound@v1` 与 E07 `wms.fulfillment.notify_pkg_binding@v1` 由
  `src/app/wms_adapter/` 下两个静态 typed successor 直接拥有；在 `WMS_BASE_URL` 下固定使用
  `/inventory/confirm-inbound` 与 `/fulfillment/pkg-bindings`，不再经 Provider profile、generated capability index 或 operation registry 查找。
- execution-side request resolver 只从已持久化的 `MaterialExecution`、Evidence 和已完成前序 confirmation 重建严格 DTO；
  Dispatcher 注入 E03/E07 typed adapter，API/worker Composition 共用同一 target runtime。旧 operation definition 只有在这两个
  successor 的 endpoint、identity、payload/response、幂等和结果回写合同通过后才能删除，不保留 alias 或双路径。

### 3.6 WMS target runtime 与 OpenTelemetry

- 当前现场合同固定为 `network_trust_mode=isolated_lan + auth=NONE`。API、worker、Beat 使用相同冻结 profile；
  非隔离网络或 auth/config digest 漂移必须 fail closed。跨安全域或新认证协议另立 WMS 合同，不进入 Phase 9。
- 单一 WMS target runtime 拥有 shared async client、显式连接池和跨 Transport/WmsConfirmation 的并发 limiter；
  等待 permit 和 HTTP I/O 期间不得持有数据库事务或 row lock。
- Dispatcher 使用有界 lease-safe claim window 和 bounded concurrency；每个结果以独立短事务回写。并行只能发生在不违反 3.3 FIFO 的对象之间。
- 删除 `RuntimeOpenTelemetryHttpExporter`、其配置、lifespan wiring 和专属测试；内部 OTel registry、signals、bridge 保持不变。

## 4. 开发与测试策略

本阶段跨模型、事务、状态机、Composition 和 migration，分类为大型/高风险，运行时代码采用 RED → DEV → GREEN；
计划、清单和纯文档不走代码式 TDD。

- 数据库存储统一使用 `timezone.now_for_db()`；API 时间使用 `timezone.now_utc().isoformat()`；时间戳使用
  `timezone.now_utc().timestamp()`，禁止 naive datetime `.timestamp()`。
- 同一行为只有一个主要测试 owner；fixture/helper 的机械传播完成后再运行领域集，不能以放宽断言掩盖合同或环境问题。
- PostgreSQL 约束、锁序、事务和 migration 使用独占临时 PostgreSQL 与干净逻辑库验证。
- HEAVY mapping 先随生产模块/migration/资产变更更新，再由 selector 生成 manifest；未知影响 fail closed，禁止用 `heavy_tests=[]` 猜测。
- 每个切片只运行聚焦测试；最终 staged snapshot 再运行 QUALITY、selector 选中的 HEAVY 和 migration 验证。
- 不以 Phase 12 插件、旧 Runtime 测试、Mock 绿灯或 `rough_sorter` 业务测试证明本阶段生产不变量。

## 5. 实施任务

### Task 0：冻结执行清单与影响范围（所有切片前置）

1. 从最新 `develop` 建立实施分支，记录 HEAD、dirty 指纹、数据库和验证环境。
2. 对 `TransportPositionProjection`、unfinished workload owner、ESTOP route、`WmsConfirmationService`、WMS runtime factory
   和 `RuntimeOpenTelemetryHttpExporter` 批量执行 GitNexus upstream impact；HIGH/CRITICAL 超出本计划清单时暂停确认。
3. 枚举生产调用点、直接/间接测试、fixture/helper、migration metadata、Composition、Celery、部署配置和 HEAVY mapping。
4. 形成 `RETAIN / SWITCH / DELETE → NONE / UNRESOLVED` 机器清单；`UNRESOLVED` 非零时停止。

退出证据：首个生产补丁前冻结完整调用点、测试 owner、migration/HEAVY owner 和无关 dirty 指纹。

### Slice A / Task 1：建立 `BinExecution`、`PositionProjection` 与 Transport authority

RED 锁定 3.1/3.2 的状态、唯一性、锁序、current-only 生命周期、debug 隔离、同事务 Evidence/projection 和 authority 不可晚绑定。

DEV：

1. 在 execution 分层新增 `BinExecution`、`PositionProjection` 的 Model/Repository/Service 与共享 advisory key builder；
2. Transport 通过注入的 projection port 在 terminal Evidence 事务中更新核心投影；
3. 增加并持久化 `TransportExecutionAuthority`，debug path 固定为无 authority；
4. 一次性删除 `TransportPositionProjection`，同步迁移调用者、导出、fixture、metadata 和 migration。

GREEN 测试 owner：

- execution FAST：BinExecution 生命周期、authority、projection lifecycle 与锁序；
- transport FAST：同事务 terminal Evidence、debug 无核心投影、旧符号 absence；
- PostgreSQL integration：partial unique、并发 create/update/close、authority constraints；
- migration：干净 base→head schema、metadata 一致性和旧表 absence；
- Transport HEAVY/E2E：真实 worker 回调到 Evidence 与 projection 同步收敛。

### Slice B / Task 2：切换 unfinished snapshot、FIFO 与 START gate

RED 锁定 3.3 的 exact booleans、确定性 sample、所有 owner 状态谓词、严格 FIFO 和旧 Epoch START blocker。

DEV：在现有 WorkLine Repository/Service 边界用一条 SQL snapshot 替换 RuntimeInbox 查询；增加
`MaterialExecution` admission identity/index；不得创建 workload 表、缓存、兼容 fallback 或跨 Epoch reassignment。

GREEN：WorkLine START/STOP/deactivate、execution、Device、Transport、InboundEvidence、WMS confirmation 的聚焦 FAST；
独占 PostgreSQL 上验证并发 snapshot、FIFO 队头和索引查询；旧 RuntimeInbox owner 测试仅在 successor 通过后删除。

### Slice B / Task 3：闭合 `ESTOP_PRESSED` final router

RED 锁定 final routing、active incident blocker、barrier-first 事务、incident-owned drain、retry/unknown reconciliation、clear 与新 START 行为。

DEV：在 Device Evidence application boundary 建立唯一 final route；删除 RuntimeHold admission 依赖和只记 Evidence 的旧分支；
drain 使用有界 keyset/set-based 批次，不在 ESTOP 主事务逐条扫描全部任务。

GREEN：Device Evidence FAST、WorkLine Safety FAST、PostgreSQL incident/drain 并发测试、API/Composition 测试和真实 Celery worker E2E；
扫描双 ESTOP route、旧 Runtime side effect 和无 owner drain 零残留。

### Slice B / Task 4：闭合 E03/E07 `WmsConfirmation` barrier

RED 锁定创建、幂等、互斥、固定锁序、响应冲突、拒绝、重试资格、歧义、FIFO 队头和 execution 推进。

DEV：拆分 lifecycle 与 dispatcher，补齐必要 Epoch 关联和 `material_execution_id` 约束/index；HTTP dispatch 遵守 shared client、
lease window、bounded concurrency 和无数据库长事务规则；在 `src/app/wms_adapter/` 建立 E03/E07 静态 DTO、request resolver、
typed adapter 与 API/worker Composition，固定两个 target path，直接替换旧 operation definition/generated capability 查找。

GREEN：`WmsConfirmationLifecycleService` FAST、dispatcher FAST、execution decision、WMS Adapter contract、PostgreSQL 事务/锁竞争，
以及真实 Celery worker 下 E03→E07 严格顺序与 retry/unknown 阻断；逐项证明 endpoint、identity、payload/response、幂等结果回写、
shared-client lifecycle 和旧 registry absence。

### Slice C / Task 5：建立最小 WMS target runtime 并删除同步 OTel HTTP backend

1. 冻结 `WMS_BASE_URL`、Transport submit path、E03 `/inventory/confirm-inbound`、E07 `/fulfillment/pkg-bindings`、
   `isolated_lan + NONE`、pool/limiter、lease/deadline 和当前 typed consumers。
2. 复用唯一 WMS Client/Phase 2 HTTP factory；目标配置不读取旧 Provider effect/query lane，不建立第二个 client lifecycle。
3. 删除同步 OTel HTTP exporter、配置、lifespan wiring 和专属测试，保留内部 registry、signals、bridge。
4. 更新 API/worker/Beat/Compose/Jenkins 的 config digest、readiness 和部署 attestation，不保留双配置。

验证：WMS Adapter、HTTP boundary、shared-client child/cross-child/shutdown、Composition、部署配置、真实 worker；
OTel 删除以生产引用/配置/测试 absence 和 retained internal signals 测试为 owner，HEAVY mapping 为 `[]` 仅在明确评审后登记。

### Slice C / Task 6：关闭 operation consumer 与 Phase 10 handoff

1. 生成 machine-readable operation inventory；Transport submit、粗分确认、E03 confirm_inbound、E07 notify_pkg_binding 和其它已交付能力
   必须指向具体 typed composition owner。
2. `manual_bin_processing`、自动上架和自动拣货等没有当前 consumer 的 operation 裁决为 `DELETE → NONE`。
3. 用稳定 absence contract 和 typed Composition 测试锁定 inventory；更新 Phase 10 cleanup matrix/successor 清单。
4. 证明 `UNRESOLVED=0`，冻结 Phase 10 target-only candidate；不新增 Phase 9 registry。

## 6. Migration 与失败恢复

`PositionProjection` 迁移只在停写窗口执行：

1. 停止 API、worker、Beat 写入，记录数据库快照以及当前镜像、profile 和 config digest；
2. 在同一数据库快照证明无 active WorkLine/Epoch，且没有未闭合 `TransportTask`、`DeviceCommand`、`WmsConfirmation`；
3. 预检通过后执行事务 migration，删除旧表并创建 target schema，不 backfill、不双写、不保留 alias/downgrade；
4. 在进程重启前完成 schema/metadata/migration contract 和关键查询验证；新 START/Evidence 只重建新的 current projection；
5. 提交前失败由事务回滚，验证旧 schema 后才能恢复旧镜像；提交后验证失败保持停机，选择“数据库快照＋旧镜像恢复”
   或受控 forward-fix，禁止在未知 schema 上启动任一写进程。

迁移 preflight 不通过即阻断 Phase 9 部署，不能通过删除业务事实或伪造 closed 状态绕过。

## 7. Failure modes

| 路径 | 生产失败方式 | 测试 | 处理与可见性 |
| --- | --- | --- | --- |
| BinExecution create | 并发创建两个 ACTIVE bin owner | PostgreSQL partial unique + advisory lock race | 冲突显式失败，不产生双 owner |
| projection update/close | terminal callback 与 Bin/Epoch close 交错 | PostgreSQL 固定锁序与 ACTIVE recheck | stale writer 被拒绝，不复活已删除 projection |
| migration | 旧环境仍有活动执行或 DDL/验证失败 | preflight + 干净 base→head + schema contract | fail closed；按第 6 节回滚、恢复或 forward-fix |
| unfinished START gate | 漏掉某 owner 或 sample 被误作 gate | owner 状态矩阵 + exact EXISTS + SQL snapshot | START 返回明确 blocker，sample 仅诊断 |
| FIFO dispatch | retry/unknown 队头被后续物料跳过 | PostgreSQL queue + real worker E2E | 队头保持阻断并进入 reconciliation |
| ESTOP | 主事务成功但 drain worker 失败 | incident retry/lease + real worker E2E | active incident 保持 blocker，告警可追踪，不静默恢复 |
| E03/E07 | 双 worker 乱序或重复写回 | lock-order/lease/idempotency integration | E03 未闭合时 E07 不可 dispatch，冲突显式化 |
| WMS HTTP | timeout、lease 过期或 pool 饱和 | dispatcher timeout/late-result/shared limiter | 短事务回写 unknown/retry；不持有 DB 等 HTTP |
| runtime config | API/worker/Beat profile 或安全域漂移 | digest/readiness/deployment attestation | 进程 fail closed，不以部分 ready 对外服务 |
| debug Transport | debug callback 意外写核心 projection | authority absence contract + E2E | 无 authority 时跳过核心 projection，只保留诊断事实 |

所有列出的失败方式均有计划测试和明确处理；当前没有“无测试、无处理且静默”的 critical gap。

## 8. NOT in scope

- `manual_bin_processing`、人工任务、PDA/WMS 人工业务 wire：由 Phase 12 批准的插件合同交付。
- `NG_MANUAL_TAKEOVER_CONFIRMED` 与 NG provenance：Phase 9 没有批准 producer，不提前建状态或字段。
- RETURN_BUFFER 业务、自动上架、自动拣货：仅保留 FIFO/稳定查询基础，不实现后续业务。
- 新 WMS 认证协议或跨安全域部署：当前真实合同是 `isolated_lan + NONE`，变化时另立合同。
- 同步 OpenTelemetry HTTP exporter 替代品：当前 backend 无批准 consumer，直接删除，不造新 exporter。
- 旧 projection backfill、双表、alias、downgrade 或兼容 wrapper：系统未发布，target schema 直接替换。
- Phase 10 生产删除、Phase 11 空库基线、Phase 12/13 schema：本阶段只生成 handoff，不提前实施。
- 供应商/现场、物理运动和业务验收：代码、测试、Mock、部署 readiness 不能替代这些验收。

## 9. Worktree parallelization strategy

| Step | Modules touched | Depends on |
| --- | --- | --- |
| Slice A | `src/app/execution/`、`src/app/transport/`、models/migrations/tests | Task 0 |
| Slice B | `src/app/execution/`、`src/app/workline/`、`src/app/device/`、WMS confirmation/tests | Slice A |
| Slice C | WMS integration、Transport Composition、Celery/deployment、operation inventory/tests | Slice B |

Lane A：Task 0 → Slice A → Slice B → Slice C（顺序执行，共享 schema、execution/transport Composition 和测试资产）。

实现采用一个 owner/worktree；只读调用点枚举和互不写共享状态的聚焦测试可以批量或并行运行。不存在安全的并行写 lane。

## 10. Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [x] **T1 (P1, human: ~2d / CC: ~3h)** — execution schema — 建立 BinExecution、PositionProjection、锁序与迁移
  - Surfaced by: Architecture/Code Quality/Outside Voice — 单一 current projection、活动 bin 唯一性和 close race
  - Files: `src/app/execution/`、models/migrations、execution/integration tests
  - Verify: execution FAST、PostgreSQL concurrency、干净 base→head migration、旧 projection absence
- [x] **T2 (P1, human: ~1.5d / CC: ~2h)** — Transport — 接入 execution authority 与事务内 projection port
  - Surfaced by: Architecture/Test/Outside Voice — 禁止 late current-Epoch lookup 与 debug 投影污染
  - Files: `src/app/transport/`、execution/transport Composition、Transport tests/HEAVY
  - Verify: Transport FAST、debug absence、terminal Evidence/projection transaction、真实 worker E2E
- [x] **T3 (P1, human: ~2d / CC: ~3h)** — WorkLine admission — 切换 exact unfinished snapshot、FIFO 与 START gate
  - Surfaced by: Architecture/Performance/Outside Voice — 一次 SQL、严格 FIFO、旧 Epoch 不可跳过
  - Files: `src/app/workline/`、`src/app/execution/`、WorkLine/integration tests
  - Verify: owner 状态矩阵、FIFO queue、并发 START、索引查询和旧 Runtime owner absence
- [x] **T4 (P1, human: ~2d / CC: ~3h)** — Safety — 建立 ESTOP final router 与 incident-owned drain
  - Surfaced by: Architecture/Code Quality/Test — barrier-first safety transaction 与 bounded retry drain
  - Files: `src/app/device/`、`src/app/workline/`、Device/Safety/Celery tests
  - Verify: Device/Safety FAST、PostgreSQL incident tests、真实 worker E2E、旧 side-effect absence
- [x] **T5 (P1, human: ~2d / CC: ~3h)** — WMS confirmation — 闭合 E03/E07 lifecycle、FIFO 和 dispatcher
  - Surfaced by: Architecture/Code Quality/Test/Performance — execution mutex、固定锁序、lease-safe HTTP
  - Files: `src/app/execution/`、`src/app/wms_adapter/`、confirmation/worker tests
  - Verify: lifecycle/dispatcher FAST、PostgreSQL race、两个静态 endpoint/DTO/Composition、旧 registry absence、真实 Celery E03→E07
- [x] **T6 (P1, human: ~1d / CC: ~90min)** — runtime/deployment — 收敛 WMS target runtime 并删除同步 OTel backend
  - Surfaced by: Architecture/Test/Performance/Outside Voice — shared client owner、配置一致性和无人使用 exporter
  - Files: WMS integration/Transport Composition、Celery/deployment config/tests
  - Verify: shared-client lifecycle、runtime config/readiness、deployment attestation、OTel absence/retained signals
- [x] **T7 (P2, human: ~4h / CC: ~45min)** — Phase 10 handoff — 生成 operation inventory 与 successor 清单
  - Surfaced by: Architecture/Test — 当前 consumer 必须 typed，`UNRESOLVED=0`
  - Files: operation catalog/Composition tests、Phase 10 cleanup matrix
  - Verify: machine inventory、stable absence contract、typed composition、旧 operation residual scan

## 11. 最终验证

最终快照必须同时满足：

1. Slice A、B、C 的聚焦 FAST、PostgreSQL integration、真实 worker E2E 和 migration evidence 全部绑定当前可执行树；
2. `BinExecution`、`PositionProjection`、unfinished snapshot、ESTOP、E03/E07、WMS runtime 各有唯一 production/test owner；
3. 严格 FIFO、未知结果阻断、旧 Epoch START gate 和 debug 无核心 projection 已被机器测试锁定；
4. `manual_bin_processing`、NG manual takeover、RETURN_BUFFER、人工/自动业务 schema 和 operation 未提前进入生产代码或 migration；
5. 旧 projection、RuntimeInbox admission、双 ESTOP route、旧 Provider consumer 和同步 OTel HTTP backend 零残留；
6. staged snapshot 上 QUALITY 通过，HEAVY selector manifest 已闭合且所有必选 HEAVY 通过；migration 在干净逻辑库通过；
7. GitNexus staged change scope、唯一主 Review 与反馈闭环完成，Phase 10 inventory `UNRESOLVED=0`；
8. 未把代码、Mock、镜像、部署 readiness、callback receipt 或历史绿灯描述成物理/供应商/业务验收。

满足以上条件后，才能把 Phase 9 标记为完成并开启 Phase 10 Execution Lock。

### 11.1 2026-08-29 实施候选证据

- 基线：`develop@a78e8d66eb36c7fdc361a71b310706cdd3fc3fb7`；实施分支
  `codex/phase9-minimum-execution-foundation`，当前变更未暂存、未提交、未合入 `develop`。
- 当前未暂存可执行树通过 `./scripts/git-quality-gate.sh --profile quality`：`4077 passed, 5 skipped`；skip 为既有
  FAST 套件边界，不作为 PostgreSQL/worker 证据。
- 当前未暂存可执行树通过 `./scripts/run_selected_heavy_local.sh --scope unstaged`：selector 选择的 PostgreSQL、Redis、
  migration 与真实 Celery worker owner 共 `267 passed`，无 skip。
- migration chain 从空库升级到单一 head `dd35f04b258f`；operation inventory 共 36 项，
  `RETAIN=7 / SWITCH=2 / DELETE → NONE=27 / UNRESOLVED=0`。
- T1–T7 的实现候选和测试 owner 已闭合；但第 6、7 条要求的 staged snapshot、GitNexus staged scope，以及 Commit/Review
  后的最终快照尚未形成。因此本节不是 Phase 9 完成声明，Phase 10 仍保持 `GATED`。
- 未执行部署、供应商联调、现场物理运动或业务验收；仓内绿灯不能替代这些证据。

## 12. Review completion summary

- Step 0: Scope Challenge — scope reduced to three sequential minimum-foundation slices
- Architecture Review: 9 issues found and folded
- Code Quality Review: 8 issues found and folded；NG manual close decision superseded by minimum Phase 9 lifecycle
- Test Review: ownership diagram produced, 8 gaps identified and folded
- Performance Review: 7 issues found and folded
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 1 candidate proposed, 0 added
- Failure modes: 0 critical gaps after approved mitigations
- Outside voice: Claude fallback ran after Codex CLI timeout；8 项先前决策与本轮 E03/E07 typed successor 决策均已折叠
- Parallelization: 1 write lane, 0 parallel / 3 sequential slices
- Lake Score: 40/40 complete recommendations accepted；1 speculative TODO correctly skipped

## 13. Retrospective learning

近期 `#179` 已做过 Phase 9 规划基线闭合，`#180` 校准过双仓规划状态，`#181` 闭合 Device EVENT 因果，`#183` 又新增
Transport 联调诊断链。当前计划因此不再依赖旧 Phase 9 假设，而以已交付的 Device/Transport reliable aggregates 为 successor；
最需要防止的回归是再次引入 Runtime owner、把 debug/Mock 当生产 authority，或把计划审批误写成现场业务验收。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 本轮未运行 |
| Codex Review | `/codex review` | Independent 2nd opinion | 2 | CLEAR（Claude fallback） | 20 findings；8 项独立决策及其余既有决策映射全部闭合 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 4 | CLEAR（APPROVED） | 累计 32 issues；本次 develop 基线复核 0 new，0 critical gaps，0 unresolved |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端计划，不需要 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 本轮未运行 |

**VERDICT:** APPROVED — Phase 9 可按本计划进入 Execution Lock；生产实施尚未开始，本次批准不授权实施、Push、PR、Merge 或 Deploy。

NO UNRESOLVED DECISIONS
