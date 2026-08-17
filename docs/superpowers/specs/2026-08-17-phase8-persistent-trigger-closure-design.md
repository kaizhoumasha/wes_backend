---
title: Phase 8 粗分执行持久触发闭环设计
status: Approved
created_at: 2026-08-17
updated_at: 2026-08-17
scope: 粗分插件本地暂缓、Transport NEW_IN 结果、WMS 业务 WAIT 与事件驱动唤醒
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
WMS 业务 `WAIT` 到期。本文只修订实现 Phase 8 已批准业务闭环所必需的运行机制，不新增正式阶段、通用工作流、插件私有状态或
未来业务能力。

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
- 不新增通用 wakeup 表、Effect ledger、工作流状态机或 `plugin_state` JSON。

## 3. 方案裁决

### 3.1 采用：业务 `DeferExecution` + 既有可靠对象

新增一个最窄 SDK Decision：`DeferExecution`。插件在业务条件尚未满足但没有异常或物理事实冲突时返回该 Decision，例如目标设备
仍有未关闭命令、换架 release gate 尚未释放。`DeferExecution` 只能单独返回，不能与设备命令、WMS 请求、Transport 请求、完成或
对账 Decision 混合。

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
evidence 未发布并把 execution 置为 `HOLD`。重复 defer 是幂等的，不得累加失败次数或创建新的持久对象。

现有 `decision_attempt_count` 收敛为“真实失败次数”：claim 本身不递增；只有 handler、Fact 构建或 Decision 应用发生异常时才递增并
按现有上限进入退避或 `RECONCILING`。进程在 claim 后崩溃不算业务失败，lease 到期后可重新领取。

### 4.3 唤醒策略

相关事实成功提交后立即发送一次“扫描可领取 evidence”的无载荷 Celery 任务：

- Device callback 基础处理完成；
- WMS 结果或后续 confirmation 创建完成；
- Transport material evidence 创建完成；
- Task 8 启动装配确认有未发布 evidence。

即时消息只负责唤醒数据库扫描，不携带 Fact、Decision 或业务快照。消息丢失时，现有 10 秒 Beat 扫描负责恢复；不得为此把整个系统
改成 1 秒高频轮询。

## 5. Transport 结果桥

### 5.1 基础能力

`InboundEvidence` 增加通用 `TRANSPORT_RESULT` kind 和冻结的 `transport_task_id`，`FactBuilder` 可据此构建 SDK
`TransportResultReadyFact`。这是执行基础能力，不包含 `NEW_IN`、`OLD_OUT`、换架或粗分角色判断。

稳定 source identity 使用 `transport_task_id + outcome_version`。同一身份相同摘要返回 duplicate；同一身份不同摘要进入现有冲突
路径。migration 直接扩展最终 schema 和约束，不迁移开发数据，不提供兼容字段或可用 downgrade。

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

## 7. 错误与隔离

- `DeferExecution` 与其它 Decision 混合、身份不一致或缺少 execution 时 fail closed，不按等待处理。
- Transport binding、task identity、outcome version 或 payload digest 冲突时进入 `RECONCILING`，不得创建目标 Cell 请求。
- `NEW_IN` 的 rack、最终位置或到达面不匹配由粗分插件进入对账；基础 Transport 只报告已验证实际结果。
- business-wait planner 返回非法 operation identity、deadline 或 request payload 时，必须先保留已接收的响应 evidence，再把原
  confirmation 置为 `RECONCILING`；不得创建 follow-up，也不得把确定的 `WAIT` 当成原 operation 的技术重试。
- 即时 Celery 唤醒失败不回滚已提交业务事实；Beat 扫描最终恢复。

## 8. 测试与验收所有权

- SDK/架构测试：`DeferExecution` 不可变、封闭、无基础设施依赖。
- execution FAST：defer 不发布、不写 digest、不消耗失败次数、重复 defer 幂等，正常恢复后只应用一次 Decision。
- WMS Adapter FAST：业务 `WAIT` 产生新 operation identity 和延迟 confirmation；技术重试继续复用原 identity。
- Transport/execution FAST：只验证通用 `TRANSPORT_RESULT` evidence 身份与 Fact 构建，不使用粗分业务判断证明基础能力。
- rough_sorter 插件测试：设备忙/release gate 返回 `DeferExecution`，`NEW_IN`/`OLD_OUT` 业务分流和 mismatch 对账。
- PostgreSQL HEAVY：原 confirmation 完成与 follow-up 创建原子性、Transport duplicate/冲突、claim/defer/恢复事务与索引约束。
- deployment/worker：持久化后即时唤醒、消息丢失后的 Beat 兜底、Web/Celery 使用同一静态插件绑定。

纯文档设计提交不新增测试。代码实施继续逐行为切片执行 TDD，最终门禁不能用插件测试替代核心能力，也不能用核心测试替代粗分业务
或现场验收。

## 9. 实施边界与退出条件

实现分为 Task 8 内两个独立提交切片，不与其它 Task 混合：

1. 核心持久触发切片：SDK `DeferExecution`、通用 `TRANSPORT_RESULT`、WMS follow-up 端口、Fact defer 语义、migration 和直接
   FAST/HEAVY owner。
2. 部署装配切片：rough_sorter handler/factory、Transport publisher、WMS business-wait planner、持久化后 Celery 唤醒、workspace
   和镜像装配。

只有代码、migration、最终 Review、QUALITY、selector 选中的 HEAVY 和干净 PostgreSQL 迁移链全部通过后，本文
`implementation_alignment` 才能改为 `ALIGNED`。这仍不代表供应商一致性或现场业务验收完成。
