---
title: Phase 8 粗分执行持久触发闭环设计
status: Approved
created_at: 2026-08-17
updated_at: 2026-08-17
scope: 粗分插件本地暂缓、Transport NEW_IN 结果、WMS 业务 WAIT、单对象人工核验恢复与事件驱动唤醒
system_stage: pre_release
migration_strategy: direct_replacement
implementation_alignment: ALIGNMENT_REQUIRED
related:
  - docs/contracts/wms-rough-sorter-inbound-integration-requirements.md
  - docs/contracts/device-annexes/rough-sorter-device-contract.md
  - docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md
  - docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
---

# Phase 8 粗分执行持久触发闭环设计

## 1. 文档定位

本文补齐 Phase 8 实施中暴露的四个持久触发缺口：设备转闲、换架 release gate 释放、`NEW_IN` Transport 结果到达以及
WMS 业务 `WAIT` 到期；同时把已有批量 reconciliation 收缩为单 `MaterialExecution` 的人工核验恢复证据。本文只修订实现
Phase 8 已批准业务闭环所必需的运行机制，不新增正式阶段、通用工作流、插件私有状态或未来业务能力。

本文是获批设计真源，当前实现状态为 `ALIGNMENT_REQUIRED`。这表示设计已经批准，但生产代码、migration、Task 8 装配和分层验收
尚未全部完成；不得据此宣称真实 ECS、WMS、RCS 或现场业务闭环已经通过。

系统尚未发布，目标实现直接替换当前不闭环行为，不保留旧 Decision 语义、别名、双路径、兼容 wrapper 或旧数据迁移逻辑。
`docs/hardware/` 继续作为厂商原始输入只读保留，不参与本次文档清理。

## 2. 问题与不变量

### 2.1 当前缺口

1. 插件返回普通 `Wait` 后，当前 evidence 会被标记为已发布；设备稍后转闲或 release gate 释放时，没有持久对象可重新产生业务
   Decision。
2. Transport 终态只收敛在 Transport 域，尚无通用 `TransportResultReadyFact` 承接路径；`NEW_IN` 成功无法可靠触发重新申请
   Cell。
3. WMS 返回的业务 `WAIT` 是一次确定响应，原 `operation_id` 必须完成；到期后的重新判断必须使用新的 `operation_id`，不能复用
   原请求做技术重试。
4. 只依靠 10 秒 Beat 会给连续设备作业引入不必要延迟；只依靠瞬时 Celery 消息又无法承受消息丢失。

### 2.2 必须保持的不变量

- 插件拥有业务判断；基础层不得根据粗分角色、位置或 WMS 结果猜测下一业务动作。
- `TransportTask` 与 `DeviceCommand` 继续是平行可靠对象，不能互相替代。
- ECS ACK、设备 callback、Transport outcome 和 WMS 响应必须先持久化，再唤醒后续处理。
- 一个物理结果只建立一个稳定 evidence 身份；重复消息不得创建第二个业务动作。
- 失败重试次数只统计真实处理失败；等待设备或业务条件不属于失败。
- `OLD_OUT` 只隔离旧架，不进入当前料盘的 material Decision lane；`NEW_IN` 不等待 `OLD_OUT` 终态。
- WMS 拥有业务判断，ECS/现场拥有物理事实，WES 只保存恢复所需的过程证据和执行投影；WES 不建立人工对账单。
- 所有 `RECONCILING` 恢复必须验证造成当前冻结的 evidence，迟到决定不得解除新的冻结。
- 进程重启不得自动恢复旧 Epoch 的物理编排；人工清线并关闭旧 Epoch 后才能创建新 Epoch。
- 不新增通用 wakeup 表、Effect ledger、工作流状态机或 `plugin_state` JSON。

## 3. 方案裁决

### 3.1 采用：业务 `DeferExecution` + 既有可靠对象

新增一个最窄 SDK Decision：`DeferExecution`。插件在业务条件尚未满足但没有异常或物理事实冲突时返回该 Decision，例如目标设备
仍有未关闭命令、换架 release gate 尚未释放。`DeferExecution` 只能单独返回，不能与设备命令、WMS 请求、Transport 请求、完成或
人工恢复 Decision 混合。它只允许用于已经唯一关联一个 `MaterialExecution` 的 evidence；一个 evidence 不得以一次 defer 阻塞
多个 execution。

核心只实现通用暂缓语义：

- 原 `InboundEvidence` 保持 `APPLIED` 且 `published_at = NULL`；
- 不写入 `decision_digest`，不增加失败次数；
- 清理当前 claim，允许后续事件重新领取；
- `MaterialExecution` 进入 `HOLD`；
- 条件满足后，同一 evidence 重新构建 Fact，产生正常 Decision 并一次性写入 digest、应用及发布。

业务条件仍由插件根据 typed Fact 和只读快照判断。`PluginFactFactory` 只组装已验证快照，不抛业务 defer 异常，不承担设备忙、
release gate 或 WMS 结果的业务解释。

### 3.2 拒绝：由 `PluginFactFactory` 抛 defer 异常

该方案代码较少，但会把“是否等待”的业务判断放入部署适配器，使插件不再是业务 Decision 的唯一 owner。它还会让相同业务条件在
factory 和 handler 中形成两套判断，因此不采用。

### 3.3 拒绝：新增通用 wakeup/Effect 表

当前只有四个已知触发缺口，现有 `InboundEvidence`、`WmsConfirmation`、Transport outcome 和 Celery 兜底扫描足以可靠承接。
新增通用调度账本会重复现有 claim、幂等和状态能力，并为尚不存在的业务提前设计，因此不采用。

## 4. 本地暂缓与事件唤醒

### 4.1 `DeferExecution` 合同

`DeferExecution` 只包含：

- `material_execution_id`
- `fact_id`
- 稳定非空 `reason_code`

它不包含设备角色、轮询时间、回调函数、任意 context 或下一动作。插件不得用它表示物理结果未知、身份冲突或确定失败；这些情况仍
返回 `PauseForReconciliation`。

### 4.2 核心处理规则

Fact processor 在 handler 返回后、生成 Decision digest 前识别单一 `DeferExecution`。处理器在一个短事务中清理 claim、保持
evidence 未发布、把 execution 置为 `HOLD`，并把既有 `decision_next_attempt_at` 更新为当前数据库时间。该字段在 defer 路径只表示
“最近一次检查时间”，不表示业务轮询期限。重复 defer 是幂等的，不得累加失败次数或创建新的持久对象。

现有 `decision_attempt_count` 收敛为“真实失败次数”：claim 本身不递增；只有 handler、Fact 构建或 Decision 应用发生异常时才递增并
按现有上限进入退避或 `RECONCILING`。进程在 claim 后崩溃不算业务失败，lease 到期后可重新领取。

领取顺序固定为：`decision_next_attempt_at IS NULL` 的新 evidence 优先，其余按最久未检查的
`decision_next_attempt_at, received_at, id` 轮转。这样不增加 defer 订阅表，也不会让长期 HOLD 的队首对象饿死新 evidence。

### 4.3 唤醒策略

相关事实成功提交后，由拥有该事务的应用服务通过既有 `TaskQueueGateway` 立即发送一次无载荷提示；提示失败不回滚已提交事实。
唤醒目标必须按可靠对象职责拆分：

- Device callback、WMS_RESULT evidence 或 Transport material evidence 提交后，唤醒 execution Fact 扫描；
- 普通新 `WmsConfirmation` 提交且已可派发时，唤醒 WMS confirmation dispatcher；
- business `WAIT` follow-up 尚未到 `next_attempt_at` 时不发送无意义即时任务，由 dispatcher 的 10 秒 Beat 到期领取；
- dispatcher 收到确定响应并提交 WMS_RESULT evidence 后，再唤醒 execution Fact 扫描。

即时消息只负责唤醒对应数据库扫描，不携带 Fact、Decision、confirmation identity 或业务快照。消息丢失时，各自的 10 秒 Beat
扫描负责恢复；不得使用 Celery ETA/countdown、增加调度表或把整个系统改成 1 秒高频轮询。

### 4.4 重启门禁

execution worker 启动时只做 fail-closed 校验：数据库存在遗留 `ACTIVE` Epoch 时拒绝启动业务扫描，不自动改写 Epoch 状态，也不扫描
该 Epoch 的未发布 evidence。操作员必须先完成现场清线并通过 Web/API 关闭旧 Epoch，再启动 worker；新 Epoch 激活事务提交后才发送
execution 扫描提示。evidence 领取查询只允许当前 `ACTIVE` Epoch，`CLOSED` Epoch 的历史 evidence 永久保留但不得再次进入 Decision
lane。该门禁直接落实总架构第 9.3 节，不新增 `RECOVERY_REQUIRED` 状态或 boot ledger。

## 5. Transport 结果桥

### 5.1 基础能力

`InboundEvidence` 增加通用 `TRANSPORT_RESULT` kind 和冻结的 `transport_task_id`，`FactBuilder` 可据此构建 SDK
`TransportResultReadyFact`。这是执行基础能力，不包含 `NEW_IN`、`OLD_OUT`、换架或粗分角色判断。

稳定 source identity 使用 `transport_task_id + outcome_version`。同一身份相同摘要返回 duplicate；同一身份不同摘要进入现有冲突
路径。migration 直接扩展最终 schema 和约束，不迁移开发数据，不提供兼容字段或可用 downgrade。

`UNKNOWN` 可以被同一 `transport_task_id` 的更高确定版本单调收敛，但只有当前 `RECONCILING` 确实由该任务较低版本 UNKNOWN evidence
造成时，才允许后续 `SUCCEEDED | FAILED` 解除冻结。无关 Transport 结果只保存 evidence，不得恢复 execution。旧 Epoch 的迟到结果
同样只保存，不恢复物理编排。

### 5.2 粗分装配边界

Task 8 部署适配器通过 `RackReplacementTransportBinding.client_request_id` 查找业务绑定：

- `NEW_IN`：把 Transport outcome 映射为 `TRANSPORT_RESULT` evidence，并关联原 source evidence 的 execution/Epoch；
- `OLD_OUT`：可靠结果只留在 Transport 域，不创建 material evidence；
- 无粗分换架绑定的 TransportTask：不进入粗分 material lane。

Transport publisher 在 evidence 提交成功后才返回。若进程在 evidence 提交后、Transport 标记 published 前崩溃，后续重发通过稳定
source identity 得到 duplicate，不会创建第二个业务 Decision。

## 6. WMS 业务 `WAIT`

WMS `WAIT` 与 HTTP 未发送、投递未知、`BUSY` 或 `UNAVAILABLE` 的技术重试严格分离：

- 原 `WmsConfirmation` 按确定响应进入 `COMPLETED`，并关联原 WMS_RESULT evidence；
- WMS ACL 的 typed business-wait planner 根据已验证 `WAIT + retry_after_ms` 生成后续请求；
- 后续请求使用新的 `operation_id`、新的时间戳和同一 material execution/业务 operation；
- 新 confirmation 初始保持 `PENDING`，使用现有 `next_attempt_at` 表示不得早于何时派发；
- 原 confirmation 与后续 confirmation 在同一数据库事务中完成/创建，避免“原请求已完成但后续请求丢失”。

执行基础层只消费 planner 返回的 typed follow-up，不硬编码 `WAIT`、粗分 operation 或请求 DTO。现有
`WmsConfirmation.next_attempt_at` 已足够，不新增 parent 列或调度表；原/后续请求可通过 execution、operation、operation_id 和
response evidence 完整审计。

现有 `WmsConfirmationService.dispatch_batch()` 是派发 owner。Task 8 只补一个固定批量、无业务载荷的
`dispatch_wms_confirmations_batch` Celery 入口、`wms-fulfillment` 队列路由和 10 秒 Beat；不得让 execution Fact scanner 顺便调用
WMS，也不得为每个 follow-up 建立 Celery ETA/countdown。

## 7. 单对象人工核验恢复与错误隔离

### 7.1 人工核验恢复不是 WES 业务单据

WES 不建立人工对账单、恢复任务、恢复队列或 `RecoveryCase` 聚合。物理结果未知或事实冲突时，WES 只把单个
`MaterialExecution` 置为 `RECONCILING` 并冻结最小资源；操作员按作业流程核验现场，WMS 根据业务主账和现场核验结果发送
`inbound.execution.recovery_decided@v1`。

每条恢复消息只允许一个 execution，严格数据为：

- `recovery_id`
- `material_execution_id`
- `material_trace_id`
- `reconciling_evidence_id`
- `decision = CONTINUE | ABORT`
- `authoritative_position`：`CONTINUE` 时必填，`ABORT` 时可空
- `reason_code`

WES 在同一事务中解析公开 `reconciling_evidence_id`，校验其对应 `InboundEvidence.id` 等于 execution 当前
`last_transition_evidence_id`，且 execution 仍为 `RECONCILING`；不匹配返回冲突并保持冻结。`CONTINUE` 只按本次权威位置恢复，
`ABORT` 停止业务推进但不删除现场实物。相同 operation identity 和相同 payload 为 duplicate；同身份异 payload 为 conflict。

### 7.2 其它错误与隔离

- `DeferExecution` 与其它 Decision 混合、身份不一致或缺少 execution 时 fail closed，不按等待处理。
- Transport binding、task identity、outcome version 或 payload digest 冲突时进入 `RECONCILING`，不得创建目标 Cell 请求。
- `NEW_IN` 的 rack、最终位置或到达面不匹配由粗分插件进入人工核验恢复；基础 Transport 只报告已验证实际结果。
- business-wait planner 返回非法 operation identity、deadline 或 request payload 时，必须先保留已接收的响应 evidence，再把原
  confirmation 置为 `RECONCILING`；不得创建 follow-up，也不得把确定的 `WAIT` 当成原 operation 的技术重试。
- 即时 Celery 唤醒失败不回滚已提交业务事实；Beat 扫描最终恢复。

## 8. 测试与验收所有权

- SDK/架构 FAST：`DeferExecution` 不可变、封闭、不可与其它 Decision 混合且无基础设施依赖。
- execution FAST：defer 不发布、不写 digest、不消耗失败次数，写入公平重检时间；真实异常才累加失败；恢复后 Decision 只应用一次。
- WMS Adapter FAST：业务 `WAIT` 产生新 operation identity 和延迟 confirmation；技术重试继续复用原 identity；恢复 wire 只接受单
  execution 和完整 `reconciling_evidence_id`。
- Transport/execution FAST：只验证通用 `TRANSPORT_RESULT` evidence 身份、版本和 Fact 构建，不使用粗分业务判断证明基础能力。
- rough_sorter 插件 FAST：设备忙/release gate 返回 `DeferExecution`；`NEW_IN`/`OLD_OUT` 分流；UNKNOWN、后续确定版本和 mismatch
  的业务 Decision。
- PostgreSQL HEAVY：超过 batch limit 的 defer 公平轮转；原 confirmation 完成与 follow-up 创建原子性；Transport duplicate/冲突；
  同任务 UNKNOWN 单调收敛；无关结果和 stale recovery evidence 不得解除冻结；`CLOSED` Epoch evidence 不可领取。
- deployment/worker：遗留 `ACTIVE` Epoch 启动失败；WMS dispatcher task/Beat/队列路由；事实提交后即时唤醒；消息丢失后的 Beat
  兜底；Web/Celery 使用同一静态插件绑定。

纯文档设计提交不新增测试。代码实施继续逐行为切片执行 TDD，最终门禁不能用插件测试替代核心能力，也不能用核心测试替代粗分业务
或现场验收。

## 9. 实施边界与退出条件

实现分为 Task 8 内三个有依赖的提交切片，不新增正式阶段，也不与其它 Task 混合：

1. defer/attempt 基础语义：SDK `DeferExecution`、单 execution 限制、失败计数、队列公平、重启 Epoch 领取门禁及直接
   FAST/PostgreSQL owner。
2. Transport/WMS 持久生产者：通用 `TRANSPORT_RESULT`、单对象 recovery wire 与因果围栏、WMS business-wait planner、独立 WMS
   confirmation dispatcher、migration 及对应 FAST/PostgreSQL owner。
3. post-commit 唤醒与静态部署：rough_sorter handler/factory、Transport publisher、各事务 owner 的 `TaskQueueGateway` 调用、Celery
   task/Beat/route、workspace 和镜像装配及 deployment owner。

三个切片共享 execution 和 deployment 装配，必须按 1 → 2 → 3 顺序实施；不创建并行 worktree。

只有代码、migration、最终 Review、QUALITY、selector 选中的 HEAVY 和干净 PostgreSQL 迁移链全部通过后，本文
`implementation_alignment` 才能改为 `ALIGNED`。这仍不代表供应商一致性或现场业务验收完成。

## 10. 数据流

```text
本地条件未满足
  → 插件返回 DeferExecution
  → evidence 保持未发布 + execution HOLD + 公平重检游标
  → Device/Transport/WMS 事实提交后发无载荷提示
  → execution scanner 重建 Fact
  → 插件产生最终 Decision

WMS business WAIT
  → 原 confirmation COMPLETED + 同事务创建新 PENDING confirmation
  → next_attempt_at 到期
  → WMS confirmation dispatcher 调用 WMS
  → WMS_RESULT evidence 提交
  → execution scanner → 插件 Decision

物理未知或冲突
  → execution RECONCILING + 记录 causal evidence
  → 现场核验 + WMS 业务裁决
  → 单对象 recovery evidence
  → causal fence 匹配后 CONTINUE/ABORT
```

## 11. 已有能力复用

- `InboundEvidence` 的 claim、lease、digest、失败退避与 `decision_next_attempt_at`：直接收敛语义，不重建调度账本。
- `WmsConfirmation`、`claim_eligible()` 与 `dispatch_batch()`：补运行时入口和 business WAIT follow-up，不新建 WMS outbox。
- Transport `outcome_version` 与可靠 Publisher：只增加 material evidence bridge，不改变 Transport 领域状态机。
- `TaskQueueGateway` 与 10 秒 Beat：增加两个明确的无载荷扫描入口，不增加全局事务 hook。
- `LineRunEpoch ACTIVE/CLOSED`：通过启动 fail-closed 和领取过滤实现重启围栏，不新增状态。
- `MaterialExecution.last_transition_evidence_id`：直接作为恢复 CAS 围栏，不新增 `RecoveryCase`。

## 12. NOT in scope

- 自动重启恢复、自动物理命令重放或旧 Epoch 续跑：违反总架构的人工清线边界。
- WES 人工对账单、恢复工单、批量恢复或 UI：WMS 拥有业务判断，WES 只保存单对象过程证据。
- defer 条件订阅表、通用 wakeup/effect ledger、Celery ETA/countdown：现有可靠对象和 Beat 已足够。
- 高频轮询、吞吐压测、HA、多供应商和现场节拍优化：不是 Phase 8 最小业务闭环的退出条件。
- 供应商一致性、真实 ECS/WMS/RCS 联调和业务验收：仍由 Task 9 独立证明。

## 13. 失败模式与验收可见性

| 路径 | 现实失败模式 | 自动化所有者 | 处理与可见性 |
| --- | --- | --- | --- |
| defer/re-evaluate | 长期 defer 反复占据队首 | PostgreSQL HEAVY | 公平游标轮转；execution 保持 `HOLD`，日志保留 reason |
| post-commit wake | DB 已提交但 Celery 提示丢失 | deployment/worker | 业务提交不回滚；10 秒 Beat 恢复，enqueue failure 记录结构化日志 |
| WMS business WAIT | follow-up 已创建但未再次派发 | WMS Adapter FAST + deployment | 独立 dispatcher 到期领取；deadline/派发失败进入既有持久状态 |
| Transport UNKNOWN | 迟到或无关版本错误解除冻结 | PostgreSQL HEAVY + plugin FAST | 同 task/version 因果围栏；不匹配只保存 evidence 并保持冻结 |
| 人工核验恢复 | 旧恢复决定晚于新冲突到达 | WMS Adapter FAST + PostgreSQL HEAVY | `reconciling_evidence_id` CAS 失败返回 conflict，操作员可重新核验 |
| worker restart | 旧 Epoch evidence 自动重放物理动作 | deployment + PostgreSQL HEAVY | 遗留 `ACTIVE` Epoch 启动失败，必须人工清线并关闭 |

上述失败模式均有计划内测试和 fail-closed 处理；本轮没有“无测试、无处理且静默”的剩余 critical gap。

## 14. 实施任务

- [ ] **T1（P0，人工约 1 天 / AI 辅助约 2–4 小时）— execution — 建立重启 Epoch 门禁**
  - 来源：总架构禁止重启后自动恢复物理编排。
  - 文件：`src/app/execution/`、`src/app/workline/`、`src/celery_app/`、对应 deployment/PostgreSQL tests。
  - 验证：遗留 `ACTIVE` Epoch 启动失败，`CLOSED` Epoch evidence 不可领取，新 Epoch 激活后可扫描。
- [ ] **T2（P1，人工约 0.5–1 天 / AI 辅助约 2–3 小时）— execution/SDK — 实现 defer、真实失败计数与公平轮转**
  - 来源：普通 `Wait` 会发布 evidence，且长期 defer 会造成队首阻塞。
  - 文件：`packages/wes_plugin_sdk/`、`src/app/execution/`、execution FAST/PostgreSQL tests。
  - 验证：defer 不写 digest/不计失败，真实异常有界退避，超过 batch limit 仍公平。
- [ ] **T3（P0，人工约 1–2 天 / AI 辅助约 3–5 小时）— WMS Adapter/execution/plugin — 直接替换为单对象恢复合同**
  - 来源：批量 reconciliation 把业务恢复聚合泄漏到基础 Fact processor，且缺少因果围栏。
  - 文件：`src/app/wms_adapter/`、execution binding model/repository、`workline_plugins/rough_sorter/`、migration、合同/FAST/HEAVY tests。
  - 验证：旧 operation/binding absence；单 execution wire；stale evidence conflict；CONTINUE/ABORT 幂等。
- [ ] **T4（P1，人工约 1 天 / AI 辅助约 2–4 小时）— Transport/execution/plugin — 建立 TransportResult material bridge**
  - 来源：`NEW_IN` 终态当前只留在 Transport 域，且 UNKNOWN 后续确定版本缺少恢复围栏。
  - 文件：`src/app/transport/`、`src/app/execution/`、deployment adapter、plugin、FAST/PostgreSQL tests。
  - 验证：task/version duplicate/conflict；只映射 `NEW_IN`；同任务单调收敛；无关结果不恢复。
- [ ] **T5（P1，人工约 0.5–1 天 / AI 辅助约 2–3 小时）— WMS confirmation — 闭合 business WAIT 派发**
  - 来源：follow-up 可持久化但当前没有运行时 dispatcher task/Beat/route。
  - 文件：`src/app/execution/services/wms_confirmation_service.py`、`src/celery_app/`、`src/core/task_queue_gateway.py`、WMS/deployment tests。
  - 验证：原完成与 follow-up 创建原子；未到期不派发；到期派发；响应提交后唤醒 execution。
- [ ] **T6（P1，人工约 1 天 / AI 辅助约 2–4 小时）— deployment — 完成 post-commit 唤醒与静态镜像装配**
  - 来源：可靠事实提交后需要低延迟提示，但提示不能成为恢复权威。
  - 文件：事务 owning services、`src/core/task_queue_gateway.py`、`deployment/`、`Dockerfile`、deployment tests。
  - 验证：只在 commit 后发无载荷任务；enqueue 失败不回滚；Beat 恢复；Web/Celery 绑定和镜像一致。

## 15. 实施顺序与 TODO 裁决

所有任务共享 execution 模型、migration 或 Composition Root，采用顺序实施：`T1 → T2 → T3 → T4 → T5 → T6`。没有安全的并行
worktree 切分，不为形式并行增加合并成本。

本轮发现均属于 Phase 8 正确性和安全退出门禁，已经纳入当前 Task 8；没有值得写入 `TODOS.md` 的延期项。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 12 issues, 0 critical gaps; all accepted decisions folded |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**VERDICT:** ENG CLEARED — ready to implement under `implementation_alignment: ALIGNMENT_REQUIRED`；不代表代码、供应商或现场验收完成。

NO UNRESOLVED DECISIONS
