# `outbound.picking_task.prepare@v1` 可靠准备设计

status: Approved
approved_at: 2026-09-04
scope: Phase 12 `MANUAL` PickingTask 单工作线领取与可靠准备暗构建；不包含生产调度、计划增量或物理执行

## 1. 目标

WES 为一条已经存在活动 `LineRunEpoch` 的人工 Picking WorkLine 提供一次原子准备能力：从当前可领取的
`MANUAL` PickingTask 中选择 WMS 优先序最小的一项，冻结 WorkLine 与 Epoch，把任务迁移到 `PREPARING`，并在同一事务中
创建 `outbound.picking_task.prepare@v1` 的可靠 `WmsConfirmation`。

本期采用暗构建：实现和测试完整，但不注册 Celery task、Beat、生产 dispatcher 路由、WorkLine START/STOP blocker 或其它生产入口。
只有真实 `manual_bin_processing` START、静态 Composition、`plan_delta` owner 与未完成 PickingTask 阻塞器同时就绪时，才允许原子激活。

`PREPARE_ACCEPTED` 只表示 WMS 已接收资源计算请求，不表示已经产生可执行计划，也不创建 TransportTask、DeviceCommand、来源明细或
物理执行对象。

## 2. 已批准决策

- 不创建 `PickingWorklineAvailability`、租约表或候选队列表；工作线可用性每次从现有权威事实实时推导。
- 暗构建入口为 `prepare_next_for_workline(workline_id)`，一次只处理一条 WorkLine 和一张任务。
- 任务候选先过滤 `QUEUED + MANUAL + not_before 已满足`，再按 `dispatch_sequence ASC, id ASC` 选择；未来时间任务不阻塞后续合资格任务。
- 复用 Epoch lifecycle lock、活动 Epoch 行锁和 PickingTask 行锁，不增加全局 allocator lock。
- `PickingTask` 拥有业务领取状态及 WorkLine/Epoch 绑定；`WmsConfirmation` 拥有请求、claim、deadline、重试和响应证据。
- `WmsConfirmation` 一次收敛为 `MaterialExecution | BinExecution | PickingTask` 三个显式 owner 且恰好一个非空，不使用弱类型多态 owner。
- `InboundEvidence` 不增加 `picking_task_id`；通过 `PickingTask → WmsConfirmation → response_evidence_id` 追溯响应证据。
- outbound picking 的 DTO、解析与响应解释位于 `src/app/wms_adapter/outbound_picking/`；业务事务位于
  `src/app/wms_integration/outbound_picking/`。
- 不向已退役的 Session、Runtime、Status Projection 或 `WorklineRuntimeStatusProjection` 写入任何状态。

## 3. 明确不在范围

- Celery task、Beat schedule、生产 worker hook、生产 adapter router 和 Composition 注册
- `manual_bin_processing` WorkLine START/STOP、设备/位置静态组成和未完成 PickingTask blocker
- `outbound.picking_task.plan_delta@v1` 与后续执行
- `outbound.picking_task.queue_changed@v1`
- PickingTask 完成、WorkLine 物理清场和下一任务释放
- `AUTO` PickingTask 自动领取
- 人工解除 prepare 对账
- 前端页面、真实 WMS 联调、现场业务验收和部署
- 新建第二套 retry、outbox、evidence、confirmation 或 callback 基础设施
- 既有平铺 Inbound/Transport Adapter 迁移；该事项继续由 `TODOS.md` 单独跟踪

## 4. 权威边界

- WMS 拥有 PickingTask 业务身份、`task_type`、`dispatch_sequence`、`not_before` 与资源计划。
- WES 拥有本地 WorkLine/Epoch 准入、任务领取、请求冻结和可靠派发义务。
- ECS/PLC 的设备、位置和物理结果只作为 WES 当前准入事实；HTTP ACK 不替代物理事实。
- Adapter 只负责 operation wire 与响应解释，不读取 WorkLine、Epoch 或 PickingTask。
- `wms_integration/outbound_picking` 只依赖 WorkLine、Execution、Device 等基础 owner；基础层不得反向导入 PickingTask。

## 5. Wire 合同

WES 调用：

```http
POST {{WMS_BASE_URL}}/api/v1/wes/decisions
Content-Type: application/json
```

请求：

```json
{
  "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804da",
  "operation": "outbound.picking_task.prepare@v1",
  "timestamp": 1786060810000,
  "data": {
    "task_id": "PICK-20260811-001",
    "workline_code": "SORTING-LINE-01"
  }
}
```

唯一成功响应为 `202 / PREPARE_ACCEPTED + data={}`。相同 `operation_id` 与相同正文重放时，WMS 返回第一次的完整成功响应，不能改为
`DUPLICATE`。prepare 复用既有 `WMS_CONFIRMATION_DISPATCH_WINDOW=30s`：该窗口只要求 WMS 持久化请求并返回接收结果，不包含后台
资源计算时间。响应未知或 `UNAVAILABLE` 时，WES 在窗口内只重试冻结的原 `operation_id` 和原正文；到期仍无确定响应则冻结对账。

请求与响应遵守共享严格 JSON 合同：UTF-8、唯一 key、禁止未知字段、禁止非法 `null`、UUIDv7 operation ID、Unix 毫秒时间戳和
`256 KiB` 原始 Body 上限。发送前保存 canonical JSON 请求与 SHA-256 digest；重试不得从业务表重新构造正文。

## 6. 数据模型

### 6.1 PickingTask

在统一 `PickingTask` 上增加：

- `workline_id: int | None`，FK 到 `wes_biz.work_lines.id`
- `line_run_epoch_id: int | None`，FK 到 `wes_biz.line_run_epochs.id`

数据库约束：

- `QUEUED` 时两个绑定字段都为空。
- `PREPARING | EXECUTING | EXECUTION_COMPLETED` 时两个绑定字段都存在。
- 同一 WorkLine 最多存在一个 `PREPARING | EXECUTING` PickingTask，使用 PostgreSQL/SQLite 部分唯一索引兜底。
- 候选领取使用 `status='QUEUED'` 的部分索引 `(task_type, dispatch_sequence, id)`；`not_before_ms` 是随当前时间变化的资格过滤，
  不放入索引排序前缀。

`PREPARING` 表示 WES 已冻结任务、WorkLine、Epoch 和准备请求；请求可能待发送、等待重试或已经被 WMS 接纳。是否收到明确
`PREPARE_ACCEPTED` 由关联 `WmsConfirmation.status` 判断。PickingTask 不保存第二套 operation、payload、attempt 或 evidence 字段。

### 6.2 WmsConfirmation

`material_execution_id` 改为可空，并增加可空 `bin_execution_id` 与 `picking_task_id` 显式外键。数据库 CHECK 要求三个 owner
恰好一个非空。

新增 `(picking_task_id, operation)` 唯一约束，确保同一任务不能产生第二个 prepare operation；既有
`(operation, operation_id)` 唯一身份保持不变。现有 MaterialExecution confirmation 的 operation identity、payload、状态和证据不重写。

共享生命周期不识别插件或人工业务字段，也不得导入 `wms_integration.outbound_picking`。execution 层声明最小的 PickingTask
owner port，具体实现位于 outbound_picking 业务模块，并由业务 composition 显式注入。派发后的 owner 应用采用显式静态分支：

- MaterialExecution：保持现有 evidence 与 fact wake-up 行为。
- PickingTask prepare：从任务读取冻结 Epoch，保存 WMS_RESULT evidence，完成或冻结 confirmation，不唤醒 MaterialExecution processor。
- BinExecution：本期只落最终已批准的 owner schema；具体 operation 应用由后续人工 Bin 切片实现。

本期暗构建不进行生产 composition；未注入 PickingTask owner port 时，`WmsConfirmationService` 不得处理 PickingTask owner。
后续激活必须将该 port、生产 START composition 与调度入口作为同一个原子切片接入。

### 6.3 InboundEvidence

不增加 PickingTask 外键。prepare 响应以 `operation + operation_id` 作为 Evidence 唯一身份，并由
`WmsConfirmation.response_evidence_id` 指向。PickingTask 级响应 evidence 的 `line_run_epoch_id` 与
`material_execution_id` 必须保持为空；执行归属通过 `PickingTask → WmsConfirmation → response_evidence_id` 追溯，Epoch 由
PickingTask 的冻结绑定提供。这样该 evidence 不会进入只处理 MaterialExecution 事实的 Decision 扫描链路。

## 7. 工作线准入

调用者传入的 WorkLine 必须同时满足：

- WorkLine 存在、静态启用、类型为人工线且运行模式允许自动业务执行。
- 存在 `ACTIVE LineRunEpoch`，且 `plugin_key=manual_bin_processing`、`flow_mode=MANUAL_BIN_PROCESSING`。
- 没有 active `WorklineSafetyIncident`。
- Epoch 冻结设备的最新权威状态满足合同新鲜度、AUTO、IDLE、无当前命令。
- Epoch 冻结位置没有当前执行对象或未知位置事实。
- 没有未闭合 DeviceCommand、TransportTask、WMS confirmation 或其它现场义务。
- 没有绑定该 WorkLine 的 `PREPARING | EXECUTING` PickingTask。

准入只读取当前权威 owner，不落库 READY、`available_since`、候选列表或租约。任何事实缺失、过期、冲突或无法确定都按不可领取处理。

## 8. 原子领取流程

```text
prepare_next_for_workline(workline_id)
  → 读取候选 ACTIVE Epoch
  → 获取该 Epoch 的 lifecycle advisory lock
  → 锁定 WorkLine 与 ACTIVE Epoch，并确认身份未变化
  → 读取并锁定当前最小 dispatch_sequence 的合资格 MANUAL PickingTask
  → 在同一事务中重新检查全部 WorkLine/Epoch/设备/位置/未完成义务
  → 生成 UUIDv7 operation_id 与 UTC Unix 毫秒 timestamp
  → PickingTask: QUEUED → PREPARING，冻结 workline_id + line_run_epoch_id
  → 创建 owner=PickingTask 的 WmsConfirmation(PENDING)，冻结正文、digest 与 created_at + 30s deadline
  → 提交事务
  → 提交后通过既有 TaskQueueGateway 请求派发
```

任务查询使用 `FOR UPDATE SKIP LOCKED` 与 `LIMIT 1`。未找到合资格任务或 WorkLine 不可用时返回明确的 no-op 结果，不产生数据库写入。
锁事务内禁止 HTTP、broker publish 或其它外部 I/O。即时 enqueue 失败不丢失 confirmation，既有可靠扫描器仍可重试，但本期不为 prepare
增加新的生产扫描入口。

## 9. 派发与结果收敛

prepare adapter 复用共享 `WmsClient`、严格 JSON 与传输事实，但拥有自己的 request/response parser。暗构建测试直接注入该 adapter；
本期不修改平铺 `inbound_wire.py`、`inbound_adapter.py`，也不把 prepare 接入 rough sorter 的生产 adapter。

| 结果 | WmsConfirmation | PickingTask / WorkLine | 动作 |
| --- | --- | --- | --- |
| `202 / PREPARE_ACCEPTED` 且 `data={}` | `COMPLETED` | 保持 `PREPARING` 与原绑定 | 保存 WMS_RESULT evidence，等待 plan delta |
| `503 / UNAVAILABLE` | `PENDING` | 保持冻结 | 原 identity、正文和 digest 重试 |
| NOT_SENT 或 DELIVERY_UNKNOWN | `PENDING` | 保持冻结 | 原 identity、正文和 digest 重试 |
| `409 / CONFLICT` | `RECONCILING` | 保持冻结 | 保存响应 evidence，等待人工核对 |
| `422 / REJECTED` | `RECONCILING` | 保持冻结 | 禁止修改正文自动重发 |
| 非法响应、ID 不匹配、错误 code | `RECONCILING` | 保持冻结 | fail closed |
| 超过 30 秒派发 deadline | `RECONCILING` | 保持冻结 | 不回队、不换线、不创建第二次 prepare |

## 10. 测试所有权

- `tests/contracts/wms_adapter/outbound_picking/`：prepare 请求/响应 DTO、路径、HTTP 状态、严格 JSON 与响应分类。
- `tests/runtime/execution/`：WmsConfirmation 三 owner CHECK 所对应的生命周期行为、PickingTask owner 响应分支，以及既有
  MaterialExecution 回归。
- `tests/integration/wms_adapter/outbound_picking/`：PostgreSQL schema、单线领取、排序、原子回滚、并发单领取、confirmation 与 evidence。
- 不新增 Celery、Beat、生产 Composition、浏览器、插件 E2E 或真实 WMS 测试；这些能力未在本期激活。
- migration、生产模块和测试资产必须更新 `docs/architecture/heavy-test-impact.toml` 的精确映射。

代码行为按 TDD 推进。最终实现证据包括聚焦 FAST、独占干净 PostgreSQL base→head migration、相关集成测试、测试拓扑、QUALITY、
staged selector 与其选择的 HEAVY。纯文档修改不编写 pytest。

## 11. 性能与并发

- 每次只处理一条 WorkLine 和一张任务，不扫描或批量锁定全库。
- 候选任务查询依赖 `status/task_type/not_before/dispatch_sequence/id` 的有界索引与 `LIMIT 1`。
- 不增加缓存。WorkLine 数量和准入事实规模很小，正确性优先于缓存派生状态。
- 所有并发写入由数据库约束兜底；应用层预检查不替代唯一约束与事务锁。

## 12. 生产激活硬门禁

以下内容必须在后续同一个原子切片完成，禁止只打开其中一项：

1. 真实 `manual_bin_processing` WorkLine START plan 与静态 Composition。
2. `plan_delta` 的严格接收、持久化和插件 owner。
3. `PREPARING | EXECUTING` PickingTask 对 WorkLine STOP/Epoch 切换的静态业务 blocker；基础 WorkLine 层不得导入 PickingTask。
4. prepare adapter 的唯一静态 production route。
5. Celery task、Beat schedule、queue route、worker runtime 与真实 worker 验证。
6. 生产失败、恢复、停止和重启场景的集成与插件测试。

## 13. 验收标准

1. 合资格人工 WorkLine 的一次调用只领取最高优先级的当前可领取 MANUAL PickingTask。
2. 领取、WorkLine/Epoch 绑定和 WmsConfirmation 创建原子完成；失败时全部回滚。
3. 两个 PostgreSQL 并发事务最多成功领取一张任务并只创建一个 prepare confirmation。
4. 未来 `not_before`、AUTO、非活动/不匹配 Epoch、设备非就绪、位置占用、安全事件或未闭合义务均不产生领取。
5. `PREPARE_ACCEPTED` 只完成 confirmation，PickingTask 保持 `PREPARING`。
6. 可安全重试结果只使用冻结的原 identity 与正文；冲突、非法响应和 deadline 到期进入 `RECONCILING`。
7. 三 owner CHECK 和 PickingTask 绑定约束由 PostgreSQL 测试证明，既有 MaterialExecution confirmation 行为不变。
8. `InboundEvidence` 不增加重复 PickingTask 关联。
9. 没有 Celery/Beat/生产 Composition 注册，没有旧 runtime/status 写入，也没有第二套 outbox。

## 14. 评审结论

Claude 独立只读 Review 与本评审一致认为本期不得提前注册 Beat；Claude 建议把 prepare 可靠发送字段放入 PickingTask，本评审依据
现有 `WmsConfirmation` 职责与“不新增第二套 outbox”合同拒绝该建议，最终选择三 owner 的中立 `WmsConfirmation`。
