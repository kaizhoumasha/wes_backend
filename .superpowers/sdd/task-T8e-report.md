# T8e 实施报告：SystemOutbox lease/fencing 与公平桶调度

## 结论

T8e 已完成。`SystemOutbox` 现在直接持久化不可变、低基数且可索引的
`provider_profile_identity + operation_identity` 调度身份，并以 owner token + 有限 expiry 表示当前派发 lease。
generic 与 workline 两个 dispatcher 复用同一公平 claim 核心；每个 Provider profile + operation 桶独立核算
并发、速率、batch、retry 与 lease 预算，达到上限时消息留在 durable backlog。所有 outbox 与 attempt worker
写回均校验 owner 和有效期，lease 丢失的旧 worker 不能提交状态。

本任务没有从 JSON/snapshot/correlation 推导调度身份，没有修改 T8b canonical bytes、T8c typed transport、T8d
reducer/case 语义，也没有实现 T8f credential/target snapshot、T8g crash matrix、Jenkins/GitLab 或业务数据迁移。

## 最小设计与边界

brainstorming 选择“显式调度列 + PostgreSQL 桶事务锁 + 单行 SKIP LOCKED + 双 owner fencing”的最小方案：

- `DispatchBucketKey` 只含持久化的 provider profile 与 operation identity；worker SQL 不读取 payload 或 snapshot。
- `DispatchPolicyRegistry` 为每桶提供 concurrency、rate window、batch、retry、lease 与 profile/bucket pause；默认策略
  有界，调用方可注入冻结 registry，不引入动态 DSL。
- `FairDispatchScheduler` 对活跃桶轮转；单桶 PostgreSQL transaction advisory lock 串行化跨 worker 预算核算，
  桶内由 Repository 使用 `FOR UPDATE ... SKIP LOCKED` 领取。
- backlog/oldest age 只统计当前 dispatcher 可消费的域；active lease 与 recent attempt 按完整
  provider+operation 桶全局核算，防止同一桶横跨 generic/workline 路由域后分别吃满额度。
- generic/workline 均在任何 I/O 前为整批 claim 创建同 owner/expiry 的 attempt，并与 claim 一次提交；释放 bucket
  lock 时，其他 worker 已能看到完整并发和速率占用。
- 资源等待路径保留既有 ECS probe 与 WorkLine safety 语义，但领取后同样持有有限 owner lease，所有 blocked、失败、
  SENT 与 attempt 写回都受 fencing。

## PostgreSQL claim、lease 与 fencing

- `SystemOutbox` 新增必填 `provider_profile_identity`、`operation_identity`，以及 nullable
  `lease_owner_token/lease_expires_at`；CHECK 保证 DISPATCHING 必须同时具备 owner/expiry，非 DISPATCHING 不保留
  有效 expiry。owner token 作为审计痕迹保留。
- `WorklineDispatchAttempt` 复用 outbox owner token，并冻结相同 `lease_expires_at`；新 owner 创建 attempt 时取消旧
  DISPATCHING attempt，旧 token 或过期 token 的 finalize 抛出 `OUTBOX_LEASE_LOST`。
- 非 HTTP 过期 lease 可被新 owner 原子领取；EXTERNAL_HTTP 过期 lease 按 T8c 收口 `UNKNOWN`，绝不自动重发。
- worker 驱动的 outbox `SENT/FAILED/RETRY_WAIT/UNKNOWN`、资源等待及 attempt 终态写回都要求
  `status=DISPATCHING + matching owner + unexpired lease`；evidence 隔离恢复同样要求有效期，不能覆盖过期 lease、
  scheduler 已写入的 UNKNOWN 证据或新 owner。
- claim 候选使用 `MATERIALIZED` CTE 固定本事务唯一候选。真实 PostgreSQL RED 曾证明普通
  `UPDATE ... id IN (SELECT ... LIMIT 1 FOR UPDATE SKIP LOCKED)` 在自更新扫描中会重求值并更新多行；物化 CTE
  消除了该陷阱，合同测试锁定 `AS MATERIALIZED`。
- device command 的物理设备 FIFO 直接进入原子候选 SQL，没有领取后再丢弃的旁路。

## 调度身份与生产写入口

- canonical WMS profile 使用 `wms.2026-07-06.material-flow.production` 与 typed operation contract identity。
- legacy handling/rack transport 使用 `wms.legacy-transport.production` 与各自 typed operation identity。
- device command 使用 `ecs.device-command.v1 + device.command`。
- plugin runtime 使用 `workline.plugin-runtime.v1 + workline.external-http.v1` 固定低基数桶；`target_code` 在
  author-time 必须通过冻结 `EndpointRegistry`，仅作为受控路由 code 持久化，不能参与桶 identity。
- `SystemOutboxUpdate` 排除 identity/lease；model update hook 与 Repository `update` 同时拒绝绕过专用状态方法修改
  dispatch key、调度 identity、冻结 bytes/hash 或 lease。

## 背压与可观测性

- 公平轮转确保每轮先访问所有可用桶，再在桶配额内领取下一条；受限、暂停或锁竞争桶不消耗其它桶额度。
- 并发与速率预算在全局 bucket advisory lock 内计算；batch 与调用 limit 共同限制预取，retry budget 进入 claim
  条件和失败终态判断。
- claim metrics/log 输出 backlog、active lease、oldest queue age、rate-limited buckets、paused buckets、bucket lock
  contention、UNKNOWN 与 `lease_loss_count`。lease loss 汇总 HTTP 过期隔离和非 HTTP 可夺取的过期 lease。
- 额度耗尽时不改写剩余行；真实 PostgreSQL 用例确认 active lease 达限后 backlog 仍持久存在。

## Migration

- 通过 Alembic generator 创建 revision `2c1407a3606e`，父 revision 为 T8d 的 `c325aab03400`。
- migration 只新增调度/lease 列、CHECK 与组合索引；没有 UPDATE、INSERT、backfill 或兼容映射。
- 系统尚未发布，按既定目标合同不迁移旧业务数据；存在旧 outbox/attempt 行的环境应清理重建，而不是从 JSON
  猜测身份。
- Docker PostgreSQL 已完成 `downgrade c325aab03400 → upgrade head`，最终
  `2c1407a3606e (head)`。

## TDD 与验证结果

- schema/identity、repository、scheduler、attempt fencing 与两个 dispatcher 按 RED → GREEN 实施。
- 扩大定向回归：`276 passed`；仅有 3 条既有 `datetime.utcnow()` deprecation warning。
- 真实 Docker PostgreSQL：`21 passed`，覆盖 SKIP LOCKED 双 session、单行 claim、outbox/attempt lease steal
  fencing、公平桶、durable backlog、跨 dispatcher 全桶并发预算、canonical bytes/attempt evidence 与 migration
  inventory。
- 测试拓扑守卫：`6 passed`；复审新增合同后显式 collect-only：`3760 tests collected`。
- 定向 Ruff format/check 与 `git diff --check` 通过。
- `./scripts/git-quality-gate.sh --profile quality` 通过：Ruff、Bandit、348 项 runtime contract guardrails、
  import-linter、architecture guardrails 与测试拓扑全部通过。

## 影响分析与提交边界

写前 GitNexus impact：`DispatchEnvelope`、`SystemOutbox` 及相关 schema 为 HIGH（约 142 个上游、13 条流程）；
`DeviceCommandService.prepare_runtime_effect` 为 HIGH（9 个上游、3 个直接调用）；`WorklineDispatchAttempt`、
`SystemOutboxRepository` 为 MEDIUM；dispatcher、engine、终态方法与新增 scheduler helper 多为 LOW/UNKNOWN。所有 HIGH
风险均在编辑前报告并按本任务授权继续；没有 CRITICAL。

最终 staged `gitnexus_detect_changes` 覆盖 42 个文件、154 个已索引 symbols 与 16 条 affected processes，汇总风险为
CRITICAL。传播面来自 generic/workline 共享 dispatch、设备命令以及 handling/rack 生产写入口同时收紧到新调度
schema/owner lease；staged 文件清单未出现范围外文件。276 项定向回归、21 项真实 PostgreSQL、348 项 runtime
contract guardrails、architecture/import 门禁与完整 quality profile 已覆盖该高传播面。

提交范围只包含 T8e 计划、实现、generator migration、测试与本报告，明确排除用户维护的 `AGENTS.md`、
`CLAUDE.md`。

## P1 复审整改

复审后的四组缺口已按同一 lease/evidence 合同收敛：

- `mark_evidence_persistence_unknown` 查询与内存态二次校验同时要求 `EXTERNAL_HTTP + DISPATCHING + matching owner +
  lease_expires_at > now`。过期同 token、已收口 UNKNOWN 与异主恢复均返回 `None`，不清理现有 unknown evidence，
  也不降级到普通失败路径；有效 owner 的独立恢复仍可收口 UNKNOWN。
- plugin external decision 在创建 outbox 前调用 `EndpointRegistry.resolve`，未注册的自由 `target_code` author-time
  fail closed；三个已注册 WMS endpoint 与任意请求数量始终共享固定的 plugin runtime bucket，不能绕过 concurrency、
  rate 或 pause。
- callback `DISPATCHING → SENT`、按 WorkLine/session 的 `DISPATCHING → CANCELLED`，以及共享 block/park
  `DISPATCHING → RETRY_WAIT` helper 均清理 `lease_expires_at`，满足数据库 lease-shape CHECK；最近一次
  `lease_owner_token` 继续保留为审计证据。
- 过期 EXTERNAL_HTTP lease 不再停在 outbox-only metric。Repository 在行锁内返回不可变 fence 快照，
  `ExternalHttpLeaseLossService` 在同一 savepoint 将 outbox 与匹配 attempt 收口 UNKNOWN，并复用
  `EffectTransportBridge` 写入 `TRANSPORT_AMBIGUOUS + RECONCILIATION_OPENED`。绑定的 `RuntimeIntentLog` 最终进入
  `RECONCILING`，对应 `ReconciliationCase` 为 OPEN；任一 attempt/reducer/bridge 步骤失败都会回滚整个 closure
  savepoint，禁止出现 outbox 已 UNKNOWN 而语义证据缺失。

复审写前 GitNexus impact 无 HIGH/CRITICAL：状态方法、plugin outbox builder、fence 与共享 retry helper 为 LOW；
`FairDispatchScheduler` 和 `WorklineDispatchAttemptRepository` 类为 MEDIUM。新增 RED 精确复现三类越 fence 恢复、
高基数 bucket/未授权 target、lease-shape flush 与 lease-loss 证据断链；GREEN 后相关快速回归 `143 passed`，
Docker PostgreSQL `13 passed`（其中本轮新增/扩展文件 `7 passed`），覆盖真实 CHECK、attempt、intent/case 与
savepoint 回滚。完整 `quality` profile 再次通过：Ruff、Bandit、348 项 runtime contract guardrails、
import-linter、architecture guardrails 与测试拓扑均无新增违规。刷新 worktree 索引后的 staged GitNexus detect 为
`12 files / 54 symbols / 0 affected processes / LOW`；内置 MCP 因 LadybugDB 存储版本不兼容不可读，使用同一
worktree 与 staged scope 的 GitNexus CLI 完成等价检测。

### Sandbox callback/ACK lease-shape 补充

后续复审发现 `operation_service` 的 sandbox 同步收口 helper 在 `DISPATCHING → SENT` 后未清理
`lease_expires_at`，真实 callback/ACK 提交会违反 T8e 新增的数据库 CHECK。全文件审计确认 sandbox external
callback 与 device ACK 两条路径都汇合到该 helper，且没有其它离开 `DISPATCHING` 的状态转换旁路。helper 现于
合法状态转换后清理 expiry，并继续保留 owner token 作为审计证据。

写前 GitNexus impact 为 LOW：2 个直接调用方、2 条 affected processes、3 个 upstream symbols。两条真实 service
调用定向回归携带有效 lease，RED 均精确失败于 expiry 未清，修复后转绿；另以真实 PostgreSQL 对持久化
`DISPATCHING` outbox 执行同一 sandbox transition 并再次 flush，确认 `SENT + expiry=NULL` 满足 CHECK。相关快速
回归 `40 passed`，该 PostgreSQL 文件 `8 passed`，测试拓扑 `6 passed`，显式收集 `3761 tests collected`；完整
`quality` profile 通过 Ruff、Bandit、348 项 runtime contract guardrails、import-linter 与 architecture guardrails。
刷新索引后的 staged GitNexus detect 为 `5 files / 4 symbols / 0 affected processes / LOW`，变更边界仅包含共享
sandbox transition、两条真实调用回归、PostgreSQL CHECK 回归与本报告。
