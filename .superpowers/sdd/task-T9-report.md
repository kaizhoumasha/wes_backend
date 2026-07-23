# T9 实施报告：`confirm_inbound` typed EFFECT 硬切换

## 结论

`confirm_inbound` 已硬切换为唯一 `wms.inventory.confirm_inbound@v1` typed WMS EFFECT。粗分机
material-flow 现在只产生 `RuntimeIntent.system_capability`；operation-owned gateway/adapter 冻结 canonical payload、
Provider profile、target、credential reference 与 dispatch identity，orchestration Service 在调用方事务内复用
`RuntimeIntentLogRepository.add_proposed_pair` 写入唯一
`RuntimeIntentLog(PROPOSED) + SystemOutbox(NEW)`。提交后的 claim/send/attempt/reducer/reconciliation/callback 全部沿用
T8a–T8g，没有新增 dispatcher、ledger、sender、retry 或事务所有权。

旧 `WmsInventoryTransactionPort.confirm_inbound` method、`ConfirmInboundRequest/Response`、typed client method、
`WMS_SYNC_CONFIRM_INBOUND_PATH`、旧 endpoint/profile binding、EXTERNAL_REQUEST/string consumer、旧 mock route 与测试引用
已删除，不保留 alias、delegate、fallback 或双运行。

## 最小设计

brainstorming 比较了三种方案：

- A：operation-owned `OUTBOX_ASYNC SystemCapabilityDefinition`，material-flow 只构造 typed intent，handler 通过冻结 adapter
  与既有 T8 双账本写入口落库。
- B：通用动态 WMS handler，以 operation 字符串选择 endpoint/model。
- C：保留 legacy typed client，由新 capability 兼容委托。

采用 A。B 会把已删除的字符串路由重新引入通用协调器；C 会形成双入口和兼容期，均违反未发布系统的硬切要求。
最终依赖边界与现有 DeviceCommand EFFECT 模式一致：capability adapter 只构造 frozen outbox，Repository 写入位于
`ConfirmInboundEffectPreparationService`；handler 不直接 import Repository、不 commit、不执行 I/O。

## 合同与消费路径

- Definition：`EFFECT + OUTBOX_ASYNC`，输入 `ConfirmInboundOperationRequest`，输出仅表示 durable acceptance 的
  `ConfirmInboundDispatchAccepted`，typed admission 要求本地物理事实已记录并冻结 `fact_version`。
- 稳定 identity：capability `wms.inventory.confirm_inbound@v1`；operation/idempotency business key 使用 `inbound_key`；
  dispatch key 不包含瞬时 `request_id`，同一业务对象重放保持不变。
- 冻结出站：gateway 生成 canonical bytes/hash，并冻结
  `wms.2026-07-06.material-flow.production`、`WMS_INBOUND_CONFIRM`、HMAC credential reference 与 endpoint snapshot。
- 粗分机：runtime plan 改为 typed `SYSTEM_CAPABILITY`，preview 只暴露 stable operation identity；插件 generated
  definition 显式授权该 capability。
- callback：`ConfirmInboundOperationResult` 只由 adapter 映射为 T8d `COMPLETED/REJECTED` reducer event，不直接改
  ledger/case。

## 遗留归零

T1 inventory 中归属 T9 的 14 行已全部删除：
`NBWMS-002/008/011/026/037/040/043/051/057/091/093/097/114/117`。inventory guard 现在区分“仍待迁移
identity”与“已完成 identity”：ADR 保留完整决策历史，活动 CSV 禁止重新登记已完成的 T9 identity。

全仓审计排除任务设计/历史 ADR 及守卫自身字面量后，以下旧路径均为零输出：

- `WmsInventoryTransactionPort.confirm_inbound`
- `ConfirmInboundRequest` / `ConfirmInboundResponse`
- `WMS_SYNC_CONFIRM_INBOUND_PATH`
- `/api/wms/inbound/confirm` / `/inbound/confirm`
- `port_method ... confirm_inbound` / `effect_contracts ... confirm_inbound`
- 上述 14 个 inventory entry id

## TDD 与回归

- 合同 RED：新增 T9 contract 首轮 `5 failed`，原因是 definition/handler/effect contract/adapter/callback 尚不存在；
  最小实现后 `5 passed`。
- consumer RED：粗分机仍输出 legacy EXTERNAL_REQUEST，`3 failed`；切为 typed SYSTEM_CAPABILITY 后 `3 passed`。
- deletion RED：旧 source marker 15 个、活动 Port reference 12 个、inventory T9 行 14 个，守卫 `3 failed`；
  硬删后守卫与 inventory/T8 ledger fixture 合计 `22 passed`。
- material-flow/T8 定向：最终 T9 contract + generic EFFECT service `56 passed`；T9/consumer/preview/effect-applier/
  inventory focused 集合此前 `50 passed`。
- WMS/contracts/readiness/material-flow mock 扩大回归：`271 passed`。
- runtime extension/plugin binding/effect service 扩大回归：`199 passed`。
- 生成索引 `--check`：workline plugin count 1，system capability count 5；两个 digest 均稳定。
- 测试拓扑：`6 passed`；显式默认快速收集：`3791 tests collected`。

## PostgreSQL integration / resilience

本机 Docker PostgreSQL 使用安全随机临时数据库并在退出时清理，最终集合 `6 passed`：

- 同一 `inbound_key`/payload 重放命中既有 claim，只保留一条 RuntimeIntentLog 与一条 SystemOutbox，handler 不再次执行。
- endpoint rotation 不改写已冻结 outbox；只有新业务 identity 的新 intent 冻结新 endpoint。
- T9 frozen outbox 在 `AFTER_SEND` 崩溃后 sender 总调用数保持 1；lease fence 将 outbox/attempt 收敛为 `UNKNOWN`，
  intent 收敛为 `RECONCILING` 并打开 case，重启 worker 不盲发。
- 完整 T8g crash matrix、unexpected sender exception、lease-loss race 同组通过。
- T8d PostgreSQL reducer/callback duplicate/out-of-order/reconciliation constraint 同组通过，terminal evidence 不被晚到事件
  反转。

## 质量与影响分析

完整 `./scripts/git-quality-gate.sh --profile quality` 最终通过：

- 1031 个文件格式检查、Ruff、Bandit（0 issues）；
- runtime readiness/production closure；
- 348 项 runtime contract guardrails；
- 11 项 process naming；
- import-linter；
- architecture enforced 0 violations；
- 测试拓扑 6 项。

首轮 quality 精确发现 capability adapter 直接 import Repository；未添加 allowlist，而是按现有 EFFECT 模式把事务写入
下沉到 orchestration Service。修正后架构守卫、单元与 PostgreSQL 集合均重新通过。

写前 GitNexus CLI 影响分析无 HIGH/CRITICAL。主要风险结果：`SorterInboundRuntimeService` LOW（7 个上游影响）；
`RuntimeCapabilityPlan` LOW（7 个）；`WmsTypedPortService` LOW（18 个）；inventory scanner `_discover_references`
MEDIUM（6 个直接测试调用）；其余被修改生产/测试 symbol 均为 LOW。MCP 因本地 LadybugDB storage version
42/40 不兼容不可用，已按 CLI 规范刷新当前 worktree branch 索引后完成 impact。

提交明确排除用户维护中的 `AGENTS.md`、`CLAUDE.md`，且未实施 T10、T11、其它 inventory operation、Jenkins 或
GitLab。

最终 staged GitNexus detect 为 `46 files / 54 symbols / 0 affected processes / LOW`；文件与 symbol 均落在预期的
T9 EFFECT、material-flow consumer、legacy deletion、inventory/docs 和测试范围。
