# T6 实施报告：QUERY shadow comparison 与 readiness 删除门禁

## 状态与范围

T6 已实现。交付覆盖 durable QUERY evidence expected、bounded 纯 comparison evaluator、现有 Celery task
queue producer/consumer、按月分区的引用式 comparison store、不可变 readiness report/approval、连续窗口重置、
SLO 双 p99 和分区生命周期。

本任务未执行 T7 首个 rough-sorter QUERY 迁移，也未实现 T8+ EFFECT/outbox。T6 通用模块不导入
rough-sorter、RuntimeIntent 或 SystemOutbox。

## 权威与异步边界

- `QueryEvidence.shadow_expected` 在既有 Stage 3 timeline evidence 事务内持久化，包含 eligibility、old/new
  policy/contract、normalization/evaluator versions、确定性 `comparison_key`、profile/operation、hash 与 evidence
  reference。
- expected 的 input/output hash 与时间必须和同一 evidence 完全相等；eligible expected 与 comparison draft 在
  `AttemptWriteSet` 中必须一一对应，禁止 orphan 或旁路 comparison。
- 数据库 commit 成功后才调用现有 `TaskQueueGateway` enqueue；commit 失败绝不 enqueue。enqueue 失败仅告警，
  不反转已提交生产动作，readiness 由 expected/stored gap 立即失效。
- consumer 只接受固定字段的引用式任务；comparison 不复制 request/response/authority snapshot，只保存
  evidence/replay reference、hash、版本、决策摘要、受控 diff 和两类 latency。
- consumer 在 insert 前用 `to_regclass` 检查目标月分区；缺失分区显式失败并由 Celery 重试，不存在
  default partition。

## 纯 evaluator 与 readiness

- evaluator 只比较已归一化的冻结 `action/reason/error_class`，无 I/O、数据库、队列或 effect 能力；限制
  decision canonical bytes、diff 字段数与 policy duration budget。
- task consumer 会重新验证 decision、difference classification 与 diff 一致性，拒绝伪造 MATCH 或 payload
  字段走私。
- readiness 从 durable timeline evidence 派生 expected 集合，再和 comparison rows 对账；缺口、重复、expected
  mismatch、evaluator error 和任一 version 变化都会清空旧窗口，仅计算最后一个连续有效后缀。
- 默认门槛为连续 7 天、1,000 eligible samples、零未解释差异；policy shadow p99 与 production QUERY
  end-to-end p99 独立计算和判定，禁止互相推导。
- readiness report 使用内容寻址 SHA-256 report ID，加载/审批前重算；数据库 trigger 禁止 report 与 approval
  UPDATE/DELETE。删除门禁只接受同一 READY report ID 的显式 GO approval。

## 分区生命周期

- Alembic revision `8db8cbba582c` 创建 `wes_runtime.query_shadow_comparisons` RANGE parent、迁移时当前月及
  未来 3 个月分区、三组受控索引、readiness report/approval 表与 immutable triggers。
- Beat 每小时运行 maintainer；事务级 advisory try-lock 保证并发维护单写，始终预建 current+3。
- 只 drop upper bound 已早于 90 天 cutoff 的整月分区；online drop 在 `SET LOCAL lock_timeout = '5s'`
  下执行，失败由 task retry，不做逐行大批删除。

## TDD 记录

1. RED：13 项目标测试因 shadow/readiness、分区、queue contract 和 migration 不存在而全部失败；最小实现后
   12 项 GREEN，保留 migration RED。
2. 使用 Alembic generator 创建 revision 并编辑分区/immutable DDL 后，目标组合 16 项 GREEN。
3. RED：hash/time 旁路与重复 comparison 可被接受；增加 evidence binding 和 duplicate invalidation 后 GREEN。
4. RED：eligible evidence/comparison orphan 可通过 write-set；加入一一对应边界后 GREEN。
5. RED：不可变 report 内容可在保持 report ID 时被替换；加入 digest 重算后 GREEN。
6. RED：任务可携带额外 payload 字段、classification 可伪造；加入 fixed task schema 与 consumer 重算后 GREEN。
7. crash/outage 矩阵覆盖 commit 前失败不 enqueue、commit 后 enqueue、enqueue outage 不反转主路径、缺分区
   consumer failure、expected/stored gap、evaluator error、version change 与重复 store。

## GitNexus 影响分析

- `AttemptWriteSet` 为 HIGH：21 个直接依赖、70 个三层影响符号；最小变更是向后兼容的默认空
  `shadow_comparisons` 与 bounded 一一对应校验。
- `QueryEvidence` 为 MEDIUM：6 个直接依赖、48 个三层影响符号；新增 optional expected，不改变非 shadow
  evidence payload（`None` 不序列化）。
- `RuntimeInboxWriteBackService` 为 MEDIUM：11 个直接依赖；仅新增 commit 后 queue hint，失败不会改写主事务。
- `commit_plugin_attempt` 本身为 LOW、0 图谱调用者。HIGH/MEDIUM 扩散已用完整 workline runtime、evidence/
  replay、三阶段 processor、Celery deployment contract 和 quality profile 回归。
- 提交前 `gitnexus detect-changes --scope staged` 检出 20 个文件、400 个符号、2 条受影响 execution flow，
  综合风险为 MEDIUM；两条 flow 均以 `commit_plugin_attempt` 为变更节点，已纳入上述三阶段与 runtime 回归。

## 验证

- T6 target/database：`19 passed`。
- T6 architecture + test topology：`9 passed`。
- workline runtime、system-capability contracts、runtime 三阶段、Celery deployment、T6 database/architecture：
  `881 passed`，仅 2 条既有 aiosqlite datetime deprecation warning。
- 默认测试收集：`3571 tests collected`。
- Alembic：新 head `8db8cbba582c`；revision 与新生产模块 `py_compile` 通过。
- 全仓 `ruff format --check .`：980 files already formatted；`ruff check .` 与 `git diff --check` 通过。
- `./scripts/git-quality-gate.sh --profile quality`：通过；Bandit 0 issue、348 runtime contracts、11 process
  naming、import-linter、architecture guardrails 与 test topology 全部通过。

## Concern

- 已新增并尝试运行最小真实 PostgreSQL heavy test，覆盖跨月并发 insert、缺分区、immutable trigger、90 天
  drop 和 lock timeout；当前环境未配置 `INTEGRATION_DATABASE_URL`，heavy harness 在任何建库/DDL 前以
  `HeavyHarnessError: missing_url` 拒绝，因此这 1 项需要在显式安全的本地 PostgreSQL test/admin URL 下补跑。
- offline Alembic 全链 SQL 生成被既有早期 migration 的 runtime inspector 阻塞；本次 revision 本身已通过
  Python 编译、文本合同测试和 Alembic head 校验，真实 DDL 仍以上述 PostgreSQL heavy test 为最终补验项。
