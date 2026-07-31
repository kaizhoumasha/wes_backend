# T5 G4.5b1 domain authority review fix 报告

## 状态

DONE_WITH_CONCERNS

## Review 问题关闭

- P0：domain caller 不再通过 `SimpleNamespace`/ORM-like ctx 自报 producer、correlation 或 workline。
  production resolver 只接收当前事务和 correlation key，经 Repository 使用 `FOR UPDATE` 锁定
  `ExecutionCorrelation → SmtInboundHandoffDemand → WorkLine`，并校验 session、owner、release、
  trace、workline ID/code 与 correlation anchor。
- P1：既有 `RuntimeIntentLog.binding_snapshot_json` 现在冻结 producer、business owner、workline 和
  correlation 四项身份；`correlation_id` 继续写入既有 FK。domain MATCH 同时精确比较
  `correlation_id + binding_snapshot_json`，任一漂移进入稳定 idempotency conflict/reconciliation。
- plugin authority、handler、outbox、dispatcher、scanner、`SmtInboundHandoffService`、
  `FullBoxExchangeService`、Celery、Handling 与 Rack 逻辑均未修改。

## TDD 与验证

- RED：review 测试首次运行 `17 failed / 9 passed`，准确暴露 resolver 注入、派生 producer 和 MATCH
  identity reconciliation 三项缺口。
- GREEN：resolver/domain admission/repository 定向测试 `37 passed`。
- production resolver line/branch coverage `100%`。
- 真实 PostgreSQL effect 文件 `5 passed`；其中 domain 场景实证三张权限事实行锁、producer 冒充拒绝、
  精确 replay，以及同 payload 不同 correlation/owner/workline conflict。
- plugin 组合 `81 passed, 4 failed`；四项仍为已登记的旧 fake fixture 缺少
  `outbox_dispatch_targets`，未在本修复越界处理。
- test topology `6 passed`，默认收集 `5122 tests`；完整 quality profile、Ruff format/check 与
  `git diff --check` 通过。

## Concern

- T5 final blocker 仍包含上述四个旧 fixture 合同失败；本 review fix 没有扩大到 write-back fixture。

## GitNexus

- 修复前 impact：`claim_or_match` MEDIUM；`prepare_and_claim` HIGH；`RuntimeIntentLog` CRITICAL。
  Changes Requested 已明确授权收紧这些身份边界，编码前已向用户报告 blast radius。
- 提交前 staged detect：`14 files / 36 symbols / 0 affected processes / risk low`，变更范围与 resolver、
  ledger reconciliation、测试及文档预期一致。
