# T5 G4.5b1 WMS EFFECT runtime domain authority 实施报告

## 状态

DONE_WITH_CONCERNS

## 实现

- `SystemCapabilityIntentService` 保留既有
  `WORKLINE_PLUGIN + PLUGIN_DECLARED_CAPABILITY` admission，并新增互斥的
  `RUNTIME_DOMAIN_SERVICE + DOMAIN_CAPABILITY_ALLOWLIST` 分支。
- runtime 静态 allowlist 只包含
  `SMT_INBOUND_HANDOFF → wms.fulfillment.full_box_exchange@v1`；caller 的 ctx/payload 不能扩展该映射。
- domain ctx 仅携带数据库事务与 correlation key；production resolver 以 `FOR UPDATE` 锁定
  `ExecutionCorrelation → SmtInboundHandoffDemand → WorkLine`，从持久化 owner/release/workline
  事实派生 producer 与完整 authority，caller ORM-like 对象不再作为权限依据。
- domain claim 的 execution session/work-item 与 plugin/binding identity 全部为 NULL；correlation 保持
  既有 FK，producer、business owner、workline 与 correlation anchor 冻结进入
  `binding_snapshot_json`。MATCH 时精确比较 correlation 与完整快照，身份漂移进入既有
  conflict/reconciliation。
- domain 幂等键固定为
  `system-capability:<capability>@<version>:domain:<producer>:<operation_key>`，超长键沿用既有有界摘要；
  不会生成 `session:None:work-item:None`。
- `RuntimeIntentLog.execution_session_id` 改为 nullable，原 execution session FK 保留；generator 创建
  revision `f557c7b749b1`，upgrade/downgrade 只切换该列 nullability。
- 没有新增 intent 表、outbox、dispatcher、facade 或兼容路径；未修改 scanner、
  `SmtInboundHandoffService`、`FullBoxExchangeService`、Celery、Handling 或 Rack。

## TDD

- RED 1：domain authority 与 nullable model 合同首次运行 `14 failed`；成功用例被既有 plugin lock 拒绝，
  所有 domain 拒绝矩阵尚无独立错误合同，模型列仍为 NOT NULL。
- GREEN 1：双 authority 最小实现后同文件 `14 passed`。
- RED 2：迁移合同因 generator revision 尚不存在而 `1 failed`。
- GREEN 2：使用 Alembic revision generator 创建 revision 并只编辑 nullability 后，authority/model/migration
  组合 `15 passed`。
- 后续补齐空 producer、correlation 冒充 plugin session 与长 operation key 分支；domain 新增分支均被覆盖。
- Review RED：持久化 resolver 与 domain MATCH identity reconciliation 尚不存在时 `17 failed / 9 passed`。
- Review GREEN：resolver、domain admission 与 repository MATCH 修复后定向 authority/repository
  `37 passed`。

## 验证

- authority/model/migration + 既有 plugin effect authority 覆盖：
  `71 passed`；`RuntimeIntentLog` line coverage `100%`，
  `SystemCapabilityIntentService` 总体 line/branch coverage `88%`，本检查点新增 domain 分支全覆盖。
- 真实 PostgreSQL cold-start：
  `tests/integration/workline_capabilities/test_system_capability_effect_postgresql.py` → `5 passed`。
  覆盖 nullable FK 仍存在、correlation/demand/workline 三行锁、首次 claim、精确 MATCH、
  producer 冒充拒绝、同 key/异 hash conflict、同 payload 不同 owner/workline/correlation conflict，
  以及 plugin/binding ledger identity 为空。
- repository/model/plugin 扩展组合：`105 passed, 4 failed`。四项失败均为 progress ledger 已登记的既有
  business-reject fake fixture 缺少 `outbox_dispatch_targets`，不在 G4.5b1 允许修改范围。
- test topology `6 passed`；默认 collection `5110 tests`。
- 完整 `./scripts/git-quality-gate.sh --profile quality` 通过：Ruff、Bandit、361 项 runtime contract、
  11 项 process naming、import-linter 与 architecture guardrail 均通过。
- 目标文件 Ruff format/check 与 `git diff --check` 通过。
- Review fix 后 production resolver line/branch coverage `100%`，topology `6 passed`，默认收集
  `5122 tests`，完整 quality profile 再次通过。

## GitNexus

- 编码前 impact：`_validate_execution_identity`、`_final_idempotency_key` 与 `prepare_and_claim` 均为 LOW；
  `RuntimeIntentLog` 为 CRITICAL，按 brief 的明确授权继续，并用模型、plugin 回归和 PostgreSQL FK/claim
  测试约束影响面。
- 提交前 CLI staged detect：`9 files / 42 symbols / 0 affected processes / risk low`。MCP 进程因
  LadybugDB storage 40/42 不兼容无法读取当前 worktree 索引，刷新索引后使用同版本 CLI 完成等价 detect。
- Review fix staged detect：`14 files / 36 symbols / 0 affected processes / risk low`。

## Concern

- `tests/workline_runtime/extensions/test_plugin_attempt_effect_authority.py` 仍有 4 个既有 fixture 失败：
  fake writeback result 缺 `outbox_dispatch_targets`。该项已在 T5 final blocker ledger 登记，必须在 T5
  最终门禁前按正式 `RuntimeInboxWriteBackResult` 合同修复；本检查点没有越界修改。
