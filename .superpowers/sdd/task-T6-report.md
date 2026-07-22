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
  evidence reference、hash、版本、决策摘要、受控 diff 和两类 latency。
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

## 评审修复追加（2026-07-22）

### 零兼容合同收口

- `QueryEvidence.shadow_expected` 与 `AttemptWriteSet.shadow_comparisons` 均改为无默认值的必填字段；所有生产与
  测试构造点显式传入实际值或空值，不保留 alias、兼容默认值或 `exclude_none` 分支。
- evidence payload 会稳定序列化 `shadow_expected: null`，缺字段输入则直接由 Pydantic 拒绝。
- shadow/readiness 的所有嵌套任务模型统一使用 `extra="forbid"`，版本、决策、限制、expected、comparison 与
  readiness 模型均拒绝未知字段，consumer 在数据库访问前阻断字段走私。

### 冲突语义与动态分区

- comparison 新增持久化状态 `STORED` / `CONFLICT`。同一 `(observed_at, comparison_key)` 只有所有持久化语义
  字段完全一致时才视为幂等重复；decision、diff、hash、版本、耗时或 reference 任一不同，都会通过单条条件
  upsert 原子地、不可逆地标记为 `CONFLICT`。
- readiness 遇到 conflict 会产生 `COMPARISON_CONFLICT`，清空此前连续窗口并返回 `INVALID`；并发与重复输入
  的单元/真实 PostgreSQL 用例覆盖了完全一致幂等、决策差异、diff 差异和 hash 差异。
- Alembic DDL 在执行时以数据库 UTC 当前月动态预建 current+3 分区，不再硬编码 2026 年月份；仍不创建 default
  partition，Beat maintainer 继续负责后续滚动预建与保留期清理。
- 本次修复仍保持 reference-only payload，没有引入 T7/T8 范围或生产查询切换能力。

### 评审修复 TDD 与影响分析

- 先新增 7 项针对性断言并确认全部按预期 RED：两个必填字段、嵌套 extra 禁止、任务走私、原子冲突、冲突
  readiness 失效和动态分区；完成最小实现后同一组合 7 项 GREEN。
- GitNexus 复核：`AttemptWriteSet` 为 HIGH（22 个直接、71 个总影响符号），`bound_attempt_write_set` 为
  CRITICAL（13 个直接、22 个总影响符号、2 条 `commit_plugin_attempt` flow）；`QueryEvidence` 与主要
  shadow/readiness 模型为 MEDIUM。风险扩散已由 runtime orchestration、workline plugin、system-capability
  contract 和三阶段 processor 回归覆盖。

### 评审修复验证

- 定向 contract/runtime/database/architecture：`36 passed`。
- workline extensions、runtime orchestration、plugin、system-capability contract 与类型边界：`957 passed`，
  仅 5 条既有 deprecation warning。
- 超长文件收口后的三阶段 processor：`114 passed`；测试拓扑：`6 passed`；默认收集：`3578 tests collected`。
- Alembic head 为 `8db8cbba582c`；revision 与新增生产模块 `py_compile` 通过；全仓 `ruff format --check .`、
  `ruff check .`、`git diff --check` 均通过。
- `./scripts/git-quality-gate.sh --profile quality` 完整通过，包括 Bandit 0 issue、348 runtime contracts、
  process naming、import-linter、architecture guardrails 与 test topology。
- 提交前 GitNexus CLI `detect-changes --scope staged` 检出 25 个文件、124 个符号、0 条受影响 execution flow，
  综合风险为 LOW；MCP 因本地 LadybugDB 存储版本不匹配不可用，检测使用本工作树刚刷新的同一索引完成。
- 真实 PostgreSQL 评审用例共 2 项，当前环境因未配置 `INTEGRATION_DATABASE_URL` 均在任何建库/DDL 前以
  `HeavyHarnessError: missing_url` 停止；因此动态分区、并发 upsert 与 trigger 的真实 PostgreSQL 行为仍未验证，
  不能以单元测试或 SQL 文本合同替代该结论。

## 最终评审修复追加（2026-07-22）

### 统一 observed 时间轴

- expected 查询不再使用重新持久化 Timeline 的 `occurred_at`；Repository 在 PostgreSQL 查询和 Python
  防御过滤中都使用 `shadow_expected.observed_at`，并按该字段排序。comparison 分区/查询与 readiness window
  因此共享同一 UTC 时间轴。
- 新增月末 observed、次月 Timeline commit 的单元与真实 PostgreSQL 回归：7 月窗口只读取 7 月 expected/
  comparison，排除 8 月样本，避免样本被忽略或错误生成 READY 报告。

### eligibility 前的版本重置

- readiness evaluator 在检查 `shadow_eligible` 之前检测并记录版本集合变化。`A eligible → B ineligible →
  A eligible` 会跨两次版本边界清空连续窗口并记录 `VERSION_CHANGED`，最终窗口只包含最后一个 A 样本，不能借用
  第一个 A 样本达到 READY。

### recorded replay 与 shadow 一一对应

- recorded replay 仍恢复并持久化历史 QUERY evidence 供审计，但在生成 replay write-set 时确定性清除每条
  evidence 的 `shadow_expected`；不重跑 policy、不生成 comparison，也不重新执行历史 EFFECT。
- 因 replay write-set 不再声明新的 eligible expected，强制的一一对应校验不会把合法 replay 降级为
  `PLUGIN_WRITE_SET_LIMIT_EXCEEDED` Hold。新增回归同时验证 replay 的 bounded write-set 保持合法。
- 选择“重放不生成新 expected”的最小语义后，`replay_ref` 没有生产写入场景，已从 SQLModel、Alembic DDL 与
  architecture contract 中删除，不保留无效兼容列。

### TDD、影响分析与验证

- RED：新增 4 项回归后确认全部按预期失败，分别暴露 ineligible version 被跳过、Timeline `occurred_at`
  跨月错轴、recorded replay 保留历史 expected 导致 write-set Hold，以及无生产者的 `replay_ref` 列。
  最小实现后相同组合 `4 passed`。
- GitNexus：`list_expected` LOW；`build_query_shadow_readiness_report` MEDIUM（7 个直接调用点）；
  `QueryShadowComparison` LOW（BaseMixin 动态边界使结果为 lower-bound）；`_write_set_from_recorded_replay` HIGH
  （8 个直接、32 个三层影响点）。HIGH 风险按持续授权继续，并由完整 runtime/replay 回归覆盖。
- T6 target/contracts/database/architecture/topology：`195 passed`；runtime orchestration、workline plugin、
  system-capability contract 与类型边界：`957 passed`，仅 5 条既有 deprecation warning；相关组合另为
  `192 passed`。默认收集为 `3582 tests collected`。
- 全仓 `ruff format --check .`（981 files）、`ruff check .`、`git diff --check`、生产模块/migration `py_compile`
  与 Alembic head `8db8cbba582c` 均通过；`./scripts/git-quality-gate.sh --profile quality` 完整通过，包括
  Bandit 0 issue、348 runtime contracts、11 process naming、import-linter、enforced architecture guardrails 与
  test topology。
- 提交前 GitNexus CLI `detect-changes --scope staged` 检出本任务 12 个文件、9 个图谱符号、0 条受影响 execution
  flow，综合风险为 LOW；`AGENTS.md` / `CLAUDE.md` 的既有未暂存改动未进入本提交。
- 真实 PostgreSQL 文件现有 3 项用例（含新增月末/次月 commit 回归）均已执行，但当前环境未配置
  `INTEGRATION_DATABASE_URL`，全部在任何建库/DDL 前以 `HeavyHarnessError: missing_url` 停止；真实 PostgreSQL
  行为仍明确标记为未验证。
- 额外运行全量 `tests/architecture` 时，发现 `d3575958` 基线已存在的 cleanup matrix 漂移：T4 的 6 个
  material-flow 测试条目未进入 CSV。该问题与 T6 diff 无关，本任务未改动该矩阵；T6 定向 architecture 与
  quality profile 的 enforced architecture guardrail 均通过。
