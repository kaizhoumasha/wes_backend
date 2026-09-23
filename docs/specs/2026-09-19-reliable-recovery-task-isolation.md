# SPEC: WES 可靠恢复、业务任务隔离与 RCS/ECS 物理事实边界

## 1. 目标与范围

联调中出现了四类连锁故障：KT16 被 `RUNTIME_RESET` 停止；`RECONCILING` 只能人工恢复；`NO_BATCH`、取消货架和 completion 判定互相阻塞；历史 drain binding 异常打断整个 prepare 批次，导致 `AC5A908C3AE33400487AB78A8DEEC5043` 长期 `QUEUED`。新线程还出现 `510042` 的 `RACK_ROTATE` 已 `ACCEPTED` 但无终态回调。

本 SPEC 覆盖 WMS confirmation、TransportTask、DeviceCommand、工作线运行状态和手工拣选插件。系统未发布，不保留兼容层，不迁移旧数据；开发/测试数据可以清理后按新模型重建。

## 2. 所有权边界

### 2.1 基础能力

`src/app/execution`、`src/app/transport`、`src/app/device` 和 Celery 基础任务只负责：稳定身份、payload digest 和幂等；claim/lease、超时、退避和可靠发送；callback/Evidence 持久化与冲突检测；原身份迟到结果闭合；`DATA_CONFLICT` 记录和批次异常隔离。

基础能力不得导入 `workline_plugins`，可独立部署、运行和测试。

### 2.2 业务能力

`workline_plugins/manual-picking` 只负责创建 inbound/return/completion/drain intent、解释 WMS 结果和定义 `NO_BATCH`、取消货架、task 完成顺序。插件只依赖基础能力的 typed port，不读取原始 JSON，不实现 HTTP、claim、重试或 Transport 状态机。

### 2.3 物理事实

WES 不拥有跨业务生命周期的物理资源所有权，不依据 `position_unknown`、历史 Transport 或旧 task binding 阻止无关新 task；但 WES 仍保留同一执行 identity、同一货架面未闭合动作和发送 lease 的可靠顺序闸门，防止自身重复提交。RCS/ECS 负责物理冲突、重复指令控制、设备准入和最终位置事实。WES 只保存请求身份、因果关联和外部结果。

`picking_task_id` 只表示业务归属，不表示物理资源所有权，也不能通过创建空壳 task 绕过状态。

### 2.1 术语与 durable boundary glossary

本 SPEC、Service contract、structured log、metrics、event name 和测试统一使用以下限定词；保留现有数据库 `InboundEvidence.apply_status = APPLIED` 字面量，不为术语清晰度新增 enum/status 或 migration：

| 术语 | 唯一含义 | 不代表 |
|---|---|---|
| **Evidence processed / `EVIDENCE_APPLIED`** | Evidence 已被 Tx1 processor 消费，并按该 Evidence contract 写入对应处理状态 | 不代表 Transport fact 已 durable convergence，也不代表 projection 已应用 |
| **Transport fact converged / `FACT_COMMITTED`** | 权威 Transport/business fact 已完成验证、收口并跨 Tx1 durable commit boundary | 不代表 Tx2 projection mutation 已完成 |
| **Projection applied / `PROJECTION_APPLIED`** | Tx2 已实际成功反映对应 projection effect；Service typed outcome 为 `APPLIED` | 不代表 Evidence 首次处理或 physical submit 成功 |
| **Projection already applied** | 同一 effect/source/version 已在 projection 中，replay 为幂等 NOOP（`ALREADY_APPLIED`） | 不代表重新执行了 mutation write |
| **Projection superseded** | 权威 causal comparator 已证明 current projection 来自更新 source（`SUPERSEDED`） | 不代表“无法比较”或普通 ownership mismatch |
| **Projection stale suppressed** | incoming fact 已失去当前 execution mutation 权限，安全抑制（`STALE_SUPPRESSED`） | 不代表 causal ordering 不可证明 |

硬不变量：`Evidence processing state must never be used as a projection-convergence predicate.` `Evidence APPLIED != Projection APPLIED`，且 `Evidence APPLIED does not imply projection convergence`。candidate discovery 必须继续从 durable authoritative fact、projection provenance/effect 和 current authority 推导；不能因为 `InboundEvidence.apply_status == APPLIED` 就排除 candidate。新代码和 contract 禁止无 domain 的 `is_applied`、`mark_applied()`、`processing_done`、`completed` 等模糊命名；优先使用 `evidence_processed`、`mark_evidence_applied`、`transport_fact_converged`、`projection_outcome`、`apply_projection`。`FACT_COMMITTED` 只用于 durable transaction boundary 的概念、日志和 metrics，不塞入 TransportTask lifecycle enum。

## 3. 统一可靠状态机

### 3.1 WMS confirmation

```text
PENDING → DISPATCHING → COMPLETED
                    └→ RECONCILING → PENDING
```

`PENDING` 可领取；`DISPATCHING` 持有有效 lease；`RECONCILING` 超时或响应冲突，保留原身份并等待恢复；`COMPLETED` 有权威 WMS Evidence 和 typed result，不再发送。`mark_reconciling()` 不得关闭重试资格；`requeue_reconciling()` 不再作为唯一恢复入口。

### 3.2 TransportTask 与 DeviceCommand

两者沿用各自现有终态字面量，补齐发送前领取、ACK 失败/超时的指数退避重发、ACK 成功后的结果等待和迟到终态单调闭合。ACK 是发送层的继续下发门槛：ACK 未成功只表示本次发送未被接纳，允许沿用原身份重发；ACK 成功后停止下发，后续只等待 ECS/RCS 结果或回调，不再重发同一物理任务。显式业务 `REJECTED/FAILED` 仍是确定失败，不通过重试掩盖无效任务。超时只记录当前尝试未知并安排自动恢复，不是业务终态，也不要求人工对账才能继续：

```text
PENDING → DISPATCHING → ACKNOWLEDGED/ACCEPTED → SUCCEEDED 或 FAILED/REJECTED
       └→ ACK 未确认 / SUBMIT_DELIVERY_UNKNOWN → PENDING（是否 resend 由 provider capability 决定）
ACKNOWLEDGED/ACCEPTED 的结果等待超时 → RECONCILING / TRANSPORT_DELIVERY_UNKNOWN（只等结果，不重新下发）
```

`ACCEPTED` 无结果只代表对端已接纳，不能被 WES 当成业务成功或失败，但也禁止再次下发同一物理任务。RCS/ECS 的 `client_request_id`/`command_code` 是对端幂等键；WES 每次只允许一个有效 lease，ACK/回调必须匹配原 identity 和 payload digest。

Transport 的 physical side effect 必须遵守单向 phase boundary：`PRE-ACK physical submission domain → authoritative ACK success → POST-ACK result/projection recovery domain`。`PENDING` 是唯一允许进入 physical submit 的 Transport 状态；`send_started_at` 只证明发生过一次 submit attempt，不等于 ACK success。真正的 ACK-success fact 是对端 authoritative accepted/ACK response 与 WES 原 identity/digest 校验共同形成的单调事实；一旦成立，原 TransportTask 永久关闭 physical submission path。

因此禁止任何 production transition `ACCEPTED → PENDING`、`RECONCILING → PENDING` 或其它 post-ACK/final state → `PENDING`。`ACCEPTED`、`RECONCILING / TRANSPORT_DELIVERY_UNKNOWN` 和已有 ACK-success Evidence 只能进入 result/evidence convergence、outcome publication、projection replay；它们不得增加 `submit_attempt_count` 或调用 ECS/RCS。若业务要再次执行物理动作，必须创建新的 `client_request_id`/Transport identity。`TRANSPORT_DELIVERY_UNKNOWN` 只表示 ACK 已成功后的 delivery/result unknown；没有 authoritative ACK-success 的对象进入该 reason/state 属于 invariant violation。

#### 3.3 pre-ACK ambiguous submission 与 provider capability

pre-ACK submit timeout/connection loss 的事实 reason 固定为 `SUBMIT_DELIVERY_UNKNOWN`（或现有同义、语义明确的 reason），保持 `status=PENDING`、原 immutable `client_request_id` 和实际 `submit_attempt_count`；不得伪造 `ACCEPTED`、post-ACK `RECONCILING` 或 `TRANSPORT_DELIVERY_UNKNOWN`。`SUBMIT_DELIVERY_UNKNOWN` 描述事实，不描述下一步动作。物理 submit eligibility 只满足必要条件，不自动推出可 resend：`PENDING`、无 authoritative ACK-success、due/backoff/claim、当前 reason/retry policy 允许，并且 provider capability 明确允许安全恢复。

当前代码盘点显示 provider contract 尚未证明同 identity 自动 resend 安全：`TransportProviderPort` 只有 `submit()`，没有 `query_by_client_request_id()` 或 idempotency capability；`WmsTransportAdapter` 能校验冻结 request/ACK identity，但未在代码中承诺 provider duplicate submit 的语义；`EcsAdapter` 只有 command submit 与 device status 查询，现有 status API 也不是按 `client_request_id` 查询。故不能仅通过 `status=PENDING` 或配置 `idempotent=True` 推断安全重试。实现前必须从实际 WMS/RCS/ECS provider 合同确认 duplicate submit 是复用已有 command、忽略 duplicate、创建第二条 command 还是 undefined，并按 provider 分别冻结以下 capability：`SAFE_SAME_IDENTITY_RESUBMIT`、`QUERY_BEFORE_RESUBMIT`、`NO_SAFE_AUTOMATIC_RECOVERY`。

若 provider 支持可靠 identity query，优先执行 `SUBMIT_DELIVERY_UNKNOWN → query same identity → found 则收口；明确 not accepted 且 contract 允许时才 resend`；query 本身不增加 submit attempt。`NO_SAFE_AUTOMATIC_RECOVERY` 保持 pre-ACK ambiguous、`next_submit_at=NULL`，由 dispatcher 的 reason/capability predicate 排除，metrics/logs/alerting 暴露，不新增 attention/case 表。scheduled retry 前必须重新检查 authoritative ACK absence；late ACK 一旦可见，立即关闭同一 TransportTask 的 physical submit path。数据库锁不能宣称跨外部调用 exactly-once，安全性来自 stable identity 与 provider contract。

## 4. 数据模型（直接替换，不做兼容）

在 `wes_biz.wms_confirmations`、`wes_biz.transport_tasks`、`wes_biz.device_commands` 使用同一套 lifecycle/recovery contract。下表是本期逐对象 capability mapping；禁止把逻辑 contract 机械复制成三张表的同名字段。

| Recovery capability | WMS Confirmation | TransportTask | DeviceCommand |
|---|---|---|---|
| business/physical attempt count | `existing`: `attempt_count`，只计 WMS dispatch attempt | `existing`: `submit_attempt_count`，只计 physical submit attempt | `existing`: `attempt_count`，只计 device dispatch attempt |
| due/backoff time | `existing`: `next_attempt_at`，`NULL`=当前没有已安排的 WMS dispatch；首次领取由 `attempt_count=0` 的显式初始谓词允许 | `existing`: `next_submit_at`，`NULL`=当前没有已安排的 physical submit；首次 submit 由 `submit_attempt_count=0` 的显式初始谓词允许，`SUBMIT_DELIVERY_UNKNOWN + NO_SAFE_AUTOMATIC_RECOVERY` 不得因 NULL 被领取 | `existing`: `next_attempt_at`，`NULL`=当前没有已安排的 command dispatch；首次 command dispatch 由 `attempt_count=0` 的显式初始谓词允许 |
| claim/lease | `existing`: `claim_token` + `claim_expires_at`，成对为 NULL=未领取 | `existing`: `submit_claim_token` + `submit_claim_until`；`outcome_claim_token` + `outcome_claim_until` 仅用于 outcome publication；各自成对为 NULL=未领取 | `existing`: `claim_token` + `claim_expires_at`，成对为 NULL=未领取 |
| current attempt/result deadline | `existing`: `deadline_at`，当前 WMS delivery window，非永久截止 | `existing`: `result_deadline_at`，ACK/ACCEPTED 后结果等待窗口；`send_started_at` 表示可能已发出 | `existing`: `deadline_at`，当前 command/ACK window |
| retry eligibility | `existing`: `retry_eligible`；`false` 不得进入 WMS dispatch scan | `derived`: 由 `status`、`next_submit_at`、`submit_attempt_count`、claim lease 和 ACK 语义推导；不新增统一布尔字段 | `derived`: 由 `status`、`next_attempt_at`、`deadline_at`、ACK 状态和 claim lease 推导；不新增统一布尔字段 |
| recovery reason | `derived/existing`: 由现有 response/status 与 Service 记录；只有明确查询需要时才评估新增字段 | `existing`: `reason_code` | `existing`: `reconciliation_reason` / `failure_code` |
| outcome publication cursor | `N/A` | `existing`: `outcome_version` / `published_outcome_version`，只在单 TransportTask comparison domain 内表达 business outcome publication | `existing`: `outcome_published_at`；不表达 projection replay |
| projection replay lifecycle | `N/A` | `derived`: Transport fact + `PositionProjection.source_transport_task_id` / `source_operation_id` 推导 deterministic projection outcomes；明确 transient failure 通过 `PositionProjectionRetryableError` 表达；本期不新增字段 | `N/A` |
| new persisted field | `none currently proven` | `none currently proven` | `none currently proven` |

`No persisted recovery field without a concrete writer, reader and correctness requirement.` 当前 mapping 中没有 `new` 字段，因此本期 migration 只允许收录实际实现阶段证明必要的字段/索引/约束；若出现 `new`，必须在变更说明中同时写明 field/type/nullability/default、writer、reader、依赖的 due/claim/replay 查询、索引/约束、backfill 行为及现有事实无法等价表达的原因。projection replay 默认不新增 recovery lifecycle 字段；若 D5 的 provenance 验证最终证明 atomic fencing 必须同行保存最小 version/token，只能增加 fencing metadata，不得复制业务事实或建立 projection history。

nullable recovery 字段的 NULL 语义必须固定：`next_attempt_at`/`next_submit_at IS NULL` 统一表示“当前没有已安排的 retry/dispatch 时间”；首次 dispatch 必须由 `attempt_count=0`/`submit_attempt_count=0` 的显式初始谓词允许，不能仅凭 NULL。`SUBMIT_DELIVERY_UNKNOWN` 且 provider 为 `NO_SAFE_AUTOMATIC_RECOVERY` 时保持 `PENDING` ambiguous fact、`next_submit_at=NULL`，并由 reason/capability predicate 排除，避免每个 tick 重复领取。claim token 与 expiry 必须同时为 NULL 表示未领取；`claim_expires_at IS NULL` 不得被解释为永久有效 lease；attempt count 初始值 0 表示尚未实际领取并尝试发送。dispatcher scan failure、lock contention、projection replay、query-before-resubmit 都不得错误增加 physical/business submit attempt。索引必须由实际 due/claim query 反推，并在 PostgreSQL 验证 query plan 与 bounded batch 行为，不按逻辑字段模板机械建索引。

`deadline_at` / `result_deadline_at` 是当前尝试的等待窗口，不是永久截止点；每次重新进入允许发送的 `DISPATCHING` 前按领域语义重算，ACK 成功后永久关闭 physical-send retry path。

默认参数：`base_delay=1s`、`multiplier=2`、`max_delay=300s`、`jitter=±20%`；具体对象只补充实际需要的字段。普通 timeout、`RECONCILING` 和 retry 次数变化都不是终态，也不要求人工对账。长时间未收敛通过 metrics、logs 和 alerting 暴露；identity conflict、权威事实冲突和 invariant violation 只记录错误日志与告警并保留原始事实。本期不新增 attention/case 表；只有未来明确出现“人工领取 → 调查 → 处理 → 关闭 → 审计”的独立异常工单需求时再评估。

## 5. 公共 dispatcher 协议

数据库是 recovery truth。Beat 只作为 discovery clock 周期性触发 due dispatcher；dispatcher 扫描并有界领取 due records，不通过 Celery task 自递归 retry 构造可靠性。进程或 broker 重启后，仍由数据库中的 `next_retry_at`、attempt/retry count 和 lease 事实继续恢复。所有基础 dispatcher 使用同一事务协议，但 queue、batch size、timeout 和 concurrency 可按对象保持现有差异：

1. Physical submit dispatcher 使用明确且唯一的谓词领取 `PENDING`：不存在 authoritative ACK-success fact，满足 due/backoff/claim 条件，且 pre-ACK retry policy 允许；不得使用 `PENDING | RECONCILING` 等宽泛状态集合。`ACCEPTED`、`RECONCILING / TRANSPORT_DELIVERY_UNKNOWN` 和 ACK-success Evidence 只由 result/evidence/projection recovery 入口处理。
2. 仅当 `claim_expires_at is null or claim_expires_at < now()` 时可领取。
3. 写入新的 `claim_token`、`claim_expires_at=now()+60s`、`last_dispatch_at=now()`、`status=DISPATCHING`；只有实际领取并尝试发送/执行时才增加对应对象的 attempt/retry count。
4. 提交事务后调用外部系统，禁止持有数据库锁做 HTTP/RCS/ECS 调用。
5. 回写时必须匹配 `claim_token`；lease 过期的旧 worker 不得覆盖新状态。
6. ACK 未成功或 submit 结果不明确时，Transport 保持 `PENDING` 并记录 `SUBMIT_DELIVERY_UNKNOWN`；只有 provider contract 明确允许同 identity 安全恢复时，下一次 dispatcher tick 才能在重新验证 no-ACK predicate 后按原 identity/idempotency contract 领取。ACK 已成功的 `ACCEPTED/ACKNOWLEDGED` 结果等待超时转为 `RECONCILING / TRANSPORT_DELIVERY_UNKNOWN` 只记录未知并等待结果，不重新领取发送；`submit_attempt_count` 在 ACK 成功后冻结。
7. 迟到 callback 只按原 identity 写入 Evidence；response/payload 冲突写 `DATA_CONFLICT`，不覆盖第一次事实。ACK 后的 immutable `client_request_id` 仍可用于 callback resolution、query/reconciliation 和 fact convergence，但不得重新用于 physical submit。

Physical submit candidate query 与 Service adapter call 前都必须再次验证 no-ACK/physical-submit eligibility。以下 poison state 必须 zero ECS/RCS call、zero submit-attempt increment、不静默修回 `PENDING`，只记录 structured error/metric/alert 并转入事实恢复：`PENDING` 但已有 ACK-success Evidence；`RECONCILING` 但 submit metadata due；没有 ACK-success 却标记 `TRANSPORT_DELIVERY_UNKNOWN`；或其它状态与 ACK fact 矛盾。pre-ACK ambiguous submission 仍可按同一 immutable `client_request_id` 和既有 ECS/RCS 幂等提交 contract 决定是否重试；`send_started_at != NULL` 本身不能阻止该判断。

基础任务入口：

| 对象 | 发送/恢复服务 | 超时与回调 |
|---|---|---|
| WMS | `WmsConfirmationService` / `src/celery_app/tasks/wms_confirmation.py` | `record_delivery_unknown()` / `complete()` |
| Transport | `TransportService` / `src/celery_app/tasks/transport.py` | Transport deadline worker / outcome processor |
| DeviceCommand | `DeviceDispatchService` / `src/celery_app/tasks/device_command.py` | Device deadline worker / `DeviceEvidenceService` |

三个对象各自保存事实，不抽象成业务插件可见的万能 `call` 接口。

### 5.1 运行契约

| Dispatcher | Queue | Beat interval | expires | batch limit | worker/concurrency | claim/lease | failure semantics |
|---|---|---:|---:|---:|---|---|---|
| WMS confirmation | `celery` | 沿用 `celery_app/config.py` 当前值 10s | 沿用当前值 10s | `WMS_CONFIRMATION_BATCH_LIMIT` | 沿用现有 `celery` worker ownership/concurrency | DB `SKIP LOCKED` + confirmation claim lease | scan 失败记 scan failure，不增加业务 attempt；下一 tick 重扫 |
| Transport | `wms-fulfillment` | 沿用当前 submit/reconcile 周期 30s | 沿用当前值 30s | 沿用当前 limit 100 | 沿用现有 fulfillment worker ownership/concurrency | DB `SKIP LOCKED` + transport submit/outcome lease | scan 失败记 scan failure，不增加业务 attempt；已领取发送失败才更新 Transport retry 字段 |
| DeviceCommand | `device-command` | 沿用当前 dispatch/reconcile 周期 10s/30s | 沿用当前值 10s/30s | 沿用当前 limit 100 | 沿用现有 device-command worker ownership/concurrency | DB `SKIP LOCKED` + command claim lease | scan 失败记 scan failure，不增加 command attempt；已领取发送失败才更新 command retry 字段 |

`expires` 只表示 dispatcher scan task 的调度时效，不等同于 ACK timeout、业务 timeout 或 result/reconciliation timeout。过期 scan 可以丢弃，由下一 Beat tick 重新扫描数据库。不得新增 recovery 专用 scheduler、retry producer 或调度实体；没有证据证明现有周期不适合 recovery 时，不调整现有运行参数。

dispatcher failure 与业务对象执行 failure 必须分开记录。每类 dispatcher 至少暴露 `scan_success_empty`、`scan_failure`、`claimed_records`、`processed_records`、`ack_attempt`、`ack_success`、`ack_failure`、`due_backlog` 和 `oldest_due_age`；长时间未收敛继续通过 metrics、logs 和 alerting 暴露，不新增 attention/case 模型。batch 必须有界，多 worker 领取依赖现有 lease 与 `SKIP LOCKED` 避免重复处理，并防止 recovery backlog 长时间占用 worker、饿死正常任务。

### 5.2 projection-gap candidate discovery

projection replay 固定为两阶段：`set-based candidate discovery → per-candidate PositionProjectionService guard/apply`。candidate query 只负责低成本发现“可能存在 gap”的有限 immutable identity/provenance；最终 `APPLIED`、`ALREADY_APPLIED`、`SUPERSEDED`、`STALE_SUPPRESSED` 仍由 single mutation authority 决定。`False positives are acceptable; false negatives are not.` 查询不得把 ownership/provenance/causal-order/fencing 规则复制进 SQL，也不得 bulk update `PositionProjection`。

候选优先从已形成可投影 final fact 且 provenance 有效的 `TransportMember`/`TransportTask` 集合出发，而非遍历全部历史 processed Evidence。候选 SQL 的语义固定为：选取 `TransportMember.last_operation_id IS NOT NULL`、对应 TransportTask 已产生 outcome/final fact、并且 current `PositionProjection` 不存在或其 `(source_transport_task_id, source_operation_id)` 不等于 incoming `(transport_task_id, last_operation_id)` 的行；只返回 `transport_task_id`、`object_type`、`object_id`、`last_operation_id`、`updated_at` 等最小 provenance，不 eager-load ORM graph，不把 scan 时的 current execution snapshot 传入 Tx2 信任。示意查询：

```sql
WITH active_execution AS (
    SELECT DISTINCT b.client_request_id,
           b.workline_id, b.picking_task_id, b.step, b.resource_fence_id
    FROM wes_biz.transport_decision_bindings AS b
    JOIN wes_biz.picking_tasks AS pt
      ON pt.id = b.picking_task_id
    WHERE pt.status IN ('PREPARING', 'EXECUTING')
)
SELECT m.transport_task_id, m.object_type, m.object_id,
       m.last_operation_id, m.updated_at, m.id
FROM wes_biz.transport_members AS m
JOIN wes_biz.transport_tasks AS t
  ON t.transport_task_id = m.transport_task_id
JOIN active_execution AS ae
  ON ae.client_request_id = t.client_request_id
LEFT JOIN wes_biz.position_projections AS p
  ON p.object_type = m.object_type AND p.object_id = m.object_id
WHERE m.last_operation_id IS NOT NULL
  AND (m.status IN ('SUCCEEDED', 'FAILED') OR m.position_unknown IS TRUE)
  AND t.outcome_version > 0
  AND (
       p.id IS NULL
       OR p.source_transport_task_id IS DISTINCT FROM m.transport_task_id
       OR p.source_operation_id IS DISTINCT FROM m.last_operation_id
  )
ORDER BY m.updated_at ASC, m.transport_task_id ASC,
         m.object_type ASC, m.object_id ASC, m.id ASC
LIMIT :batch_limit
```

生产实现将上述 picking 分支与独立 drain 分支做 `UNION ALL`，再在 union 外按 `updated_at ASC, authority_kind ASC, transport_task_id ASC, object_type ASC, object_id ASC, member_id ASC` 稳定排序并应用 batch limit。drain 分支的 driving CTE 等价复用现有 `ReturnBufferDrainResultReader.history()` 的 current record 语义：按既有 `(operation, operation_id)` UUIDv7 顺序读取当前 WorkLine 的已完成、Evidence 已发布的 drain Confirmation，并通过 `DrainRepository.current()` 的 READY/WAIT 与 departure closure 规则在 Tx2 最终确认；SQL 只用 workline、drain operation/operation_id、response Evidence、`picking_task_id IS NULL`、drain step 和 resource fence 做 coarse join，不在 SQL 重写 drain 业务状态机。两分支均只返回最小 immutable identity，并带低基数 `authority_kind`。

Transport projection 至少有两个 effect domain，candidate discovery 必须分别定义 eligibility 后再 `UNION ALL`：

- **post-ACK invalidation branch**：driving fact 是已经持久化、可验证的 authoritative ACK-success（包括 poison `PENDING + ACK-success Evidence`），effect phase 为 `ACK_INVALIDATION`，目标是把仍由该 Transport/member 明确持有的旧确定位置置为 `position_unknown`；不得仅凭 `status IN (ACCEPTED, RECONCILING)` 猜 ACK。
- **final-result branch**：driving fact 是已完成的 Transport/member final fact，effect phase 为 `FINAL_RESULT`，目标是应用最终位置或最终未知结果。

两类 candidate 都输出最小 immutable Transport/member identity、operation provenance、authority identity 和 effect phase，最终进入 `PositionProjectionService`；不得 bulk update 或把 invalidation 伪装成 `ApplyPosition(None)`。概念因果顺序固定为 `known old position → ACK_INVALIDATION/UNKNOWN → FINAL_RESULT`。若 final result P2 已应用，late invalidation replay 必须为 `SUPERSEDED`、zero write；若 projection 仍由该旧 Transport/source 明确持有且没有 newer source 接管，允许 provenance-fenced cleanup invalidation，不要求原 PickingTask 仍 active，但必须重新验证 ACK fact、expected old provenance、execution/resource fence、causal phase 和 atomic Tx2 boundary。current projection 不得被用来反推谁是 current execution；它只用于验证 expected-old provenance 与检测 newer source。

当前 `invalidate_transport_member()` 会写入 `source_transport_task_id/source_operation_id`，可作为 invalidation applied provenance；但历史 `_invalidate_other_task_positions()` 曾只写 `position_unknown=True`，没有完整 provenance，必须迁移到 mutation authority。若盘点仍证明现有 source fields 无法区分“invalidation 未执行”和“已执行”，不得假装 recovery 闭环，按 D5/D22 只评估最小 effect/fencing metadata，不建立 invalidation retry lifecycle。

上述 `IS DISTINCT FROM` 只负责 NULL-safe candidate discovery，不承担 mutation correctness。candidate eligibility 必须先要求 Transport/member 已形成可投影 final fact，`last_operation_id`、final outcome/provenance 等 projection-relevant 必要字段存在；进入 eligible 集合后，才比较 current projection provenance。必须区分 projection row 完全不存在与 row 存在但 source transport/operation 为 NULL 或不完整；后者仍须进入 candidate。即使 schema 当前声明 provenance `NOT NULL`，LEFT JOIN 未命中、历史/迁移中间态和 legacy rows 仍要求 fail-safe NULL 语义。Tx2 继续由 `PositionProjectionService` 重新执行 ownership、provenance、causal-order、idempotency 和 CAS guard，最终可返回 `APPLIED`、`STALE_SUPPRESSED` 或 `SUPERSEDED`。

PostgreSQL regression 必须用真实 candidate SQL 覆盖：projection missing；两个 provenance 都 NULL；transport source match/operation source NULL；transport source NULL/operation source match；provenance 完全一致不入 candidate；以及 active-execution coarse filter 已判定 stale 的历史 Task A 即使 provenance 不同或 NULL 也不属于 actionable gap。对本次 recovery SQL 做小范围 NULL-semantics audit，检查 `<>`、nullable `=`、`NOT IN`、LEFT JOIN 后 WHERE、nullable provenance/timestamp comparison，防止同类三值逻辑产生永久 false negative；不扩展为全仓 SQL lint。原则为：`False positives may be bounded and resolved by the mutation authority; actionable false negatives must not become permanent.`

每个候选进入独立 Tx2 后，必须重新读取 authoritative Transport fact、Binding 和 current execution，并执行 ownership、provenance、idempotency、causal supersession 与 atomic fencing/CAS；candidate query 的结果不具备 mutation 权限。允许 bounded duplicate discovery 由 `ALREADY_APPLIED`/CAS 收敛，不机械新增 claim/lease；若采用 `FOR UPDATE SKIP LOCKED`，必须只锁定短时 candidate rows，不能在外部逻辑或长批次 processing 期间持有 Transport row locks。scan 必须有 batch limit、稳定 order 和唯一 tie-breaker，按 oldest `updated_at` 优先，不能使用永久漏掉老 gap 的硬时间窗口。

Tx2 的 retry boundary 固定为“下一次 candidate discovery tick”，不是单 candidate 内部 retry loop：明确 transient failure 时 rollback 当前 Tx2、释放全部 row locks、本 tick 停止该 candidate；下一 tick 从 authoritative facts 重新发现并完整 replay。candidate scan 只返回 bounded immutable IDs；每个 candidate 使用独立 transaction，不能把整批包成一个长事务。单对象 `RETRYABLE_FAILURE` 不得 rollback 已完成的其它 candidate，也不得阻塞本 batch 后续 candidate：T1 retryable 时仍继续处理 T2 `APPLIED`、T3 `STALE_SUPPRESSED`、T4 `ALREADY_APPLIED`。scan/query failure、per-object Tx2 retryable failure、dispatcher/worker process failure 分别记录和计数，不把 scan failure 计入业务 attempt，也不因单对象失败把整轮 scan 标记为失败。

单次 worker invocation 可按 immutable candidate identity 做 in-memory dedupe，但它只是性能优化；跨 worker、进程和 tick 的正确性继续依赖 authoritative DB facts、idempotent/fenced Tx2。`RETRYABLE_FAILURE` 仍是 actionable gap，继续计入 `projection_gap_count` 与 `oldest_projection_gap_age`；`APPLIED`、`ALREADY_APPLIED`、`SUPERSEDED`、`STALE_SUPPRESSED` 等 deterministic completion 不再计入。观测至少区分 `projection_retryable_failure_total`、scan failure、actionable backlog、oldest age，并只使用 authority kind/低基数 failure reason 作为 metrics label，高基数 identity 仅进入 structured log。告警优先看 oldest actionable gap 持续增长与 retryable failure 的组合，而非瞬时 failure counter。

Observability 必须带 domain-qualified name：Evidence 层使用 `evidence_processed_total`，Tx1 使用 `transport_fact_converged_total` / `transport_fact_commit_failure_total`，Tx2 使用 `projection_mutation_total{outcome="applied|already_applied|superseded|stale_suppressed"}`；禁止新增裸 `applied_total`、`success_total` 或让读者依赖隐含上下文。ACK invalidation branch 同样遵守：`ACK Evidence APPLIED + ACK fact FACT_COMMITTED` 仍可能存在 invalidation projection unapplied，不能把 Evidence 状态当作 ACK 相关处理全部完成的谓词。

实现前必须盘点 ORM/DB/Celery 自动 retry，禁止 `driver retry + Service retry + Celery retry + Beat next tick` 叠加；第一版只允许一次明确 transient classification 后交给下一 dispatcher tick。若底层 transaction wrapper 必须有限重试，必须冻结次数与责任边界，不能再由 Tx2 Service 或 Celery 递归重试。commit outcome unknown 的 transient failure 允许下一 tick replay：未 commit 时得到 `APPLIED`，已 commit 但响应丢失时得到 `ALREADY_APPLIED`，两者都不得重复副作用。

actionable gap 的 coarse filter 只能使用 authoritative active execution / Binding / resource ownership 事实，且分为两个明确分支再 `UNION ALL`：

- **picking-authority candidates**：当前最小来源是 `PickingTaskRepository.get_active_for_workline()` 的 `PREPARING`/`EXECUTING` task，加上 `TransportDecisionBinding` 的 `workline_id + picking_task_id + step + resource_fence_id` task-scoped identity。
- **taskless drain-authority candidates**：复用 `ReturnBufferDrainResultReader.history()` 的 checkpoint/已发布 Evidence 语义、`DrainRepository.current()`/`transport()` 对 READY/WAIT 与 rack departure closure 的判定，以及 drain Binding 的 `workline_id + correlation_id + step + resource_fence_id + picking_task_id IS NULL` identity。不得由 recovery 自己发明“按 created_at 取最新 drain”规则，也不把 `picking_task_id IS NULL` 当成 stale/no-owner。

两分支各自只输出最小 immutable candidate identity，再进入同一 `PositionProjectionService`；不得把两套 authority 规则塞进巨大 `OR`，也不新增 execution/recovery 表。不得从 `PositionProjection` 反推谁是 current。scan 时 A active、Tx2 时切换 B 仍由 Service 返回 `STALE_SUPPRESSED`；scan 已看到 B 时 A 不进入 actionable candidate，两者都正确。`No actionable projection gap may become permanently undiscoverable.` task transition 短窗口漏候选允许由后续 tick 重新发现。

`SUPERSEDED` 可在现有 provenance 能以低成本、无歧义证明时作为 coarse filter 排除；否则保留 bounded false positive，由 mutation authority 返回 `SUPERSEDED`，不把完整 causal-order 规则复制进 SQL。`projection_gap_count` 与 `oldest_projection_gap_age` 只统计仍可能具有 current mutation authority 的 actionable candidates；`stale_suppressed_total`、`superseded_total` 是 mutation outcome counters，历史 suppression 不得继续抬高 actionable backlog/oldest age。只有 profiling 证明 authoritative facts 无法可靠排除 terminal stale 且重复扫描成本不可接受时，才重新评估最小 fencing/suppression metadata，不新增 suppression status、retry lifecycle 或 attention entity。

索引从上述最终 SQL 反推，不预先机械新增。实现阶段必须在代表性 PostgreSQL 数据量上运行 `EXPLAIN (ANALYZE, BUFFERS)`，记录 scan type、join cardinality、sort、rows removed by filter 和 bounded batch 实际扫描量；只有计划显示真实瓶颈时，才新增最小 partial/index/constraint，并同步更新 schema mapping 与 migration。metrics 至少区分 `projection_gap_candidates_discovered`、`projection_gap_applied`、`projection_gap_already_applied`、`projection_gap_superseded`、`projection_gap_stale_suppressed`、`projection_gap_oldest_age`，用 candidate→deterministic outcome 比例判断 query 是否过宽；不新增持久化 recovery state。replay backlog 继续服从 bounded processing、queue isolation 和正常 Transport/outcome workload 不被饿死的运行契约。

### 5.3 candidate query plan gate

candidate SQL、stable ordering 和 batch limit 必须先冻结，再决定索引；`LIMIT 100` 不等于 bounded database work。交付门禁必须在本机 Docker PostgreSQL synthetic dataset 快速迭代，并在联调 PostgreSQL 复核：数据集要包含大量已收敛历史 final facts、大量被 active-execution coarse filter 排除的 stale facts、一部分 superseded facts 和少量 actionable gaps；装载后先对相关表 `ANALYZE`。至少运行 steady-state（gap=0/极少）与 recovery-backlog（大量 actionable gaps）两种 workload。

每种 workload 都保存 `EXPLAIN (ANALYZE, BUFFERS)`，检查 actual/estimated rows、actual time、loops、rows removed by filter、shared hit/read buffers、join strategy、sort method/top-N sort、pathological nested loop，以及 batch result 与实际扫描量比例；不能只因出现 Index Scan 就通过，也不能因 Seq Scan 就机械加索引。严重 cardinality misestimate 先判断 statistics/data-correlation，再判断索引。只有存在 before plan、明确 bottleneck、proposed index 与 after plan，且能解释 scan/filter/sort/buffer 改善时，recovery-specific index 才能进入 migration；否则保持现有 schema。不预建 future index，不为 Index Only Scan 在 INCLUDE 中复制大量业务/provenance 字段，也不设置跨环境绝对 wall-clock CI 门槛，优先冻结结构性 bounded-work 证据并记录 runtime 参考值。

## 6. task 隔离与 `position_unknown`

`TransportDecisionBinding` 的查询 scope 必须按用途分开，不能把“查得到历史事实”当成“可以修改当前执行态”：

- **immutable identity query**：callback、outcome 和 reconciliation 可按不可变 `client_request_id` / `transport_task_id` 找到原 Binding、TransportTask 和 Evidence，收口其所属历史事实；PickingTask 已结束或当前任务已切换，不影响原 Transport 事实最终闭合。
- **task-scoped query**：当前业务判定必须使用 `workline_id + picking_task_id + step`，只读取当前任务的业务 binding、Decision 和未闭业务面。
- **runtime mutation guard**：任何修改当前工作位、货架位置、resource ownership、runtime status 或其它 current projection 的动作，都必须重新校验 callback 所属 execution identity 与当前 active execution context；除上述业务 scope 外，还要复用现有最小可靠 resource fence / ownership identity（如有），禁止只用 `rack_id`、`workline_id` 等部分 identity。

核心原则：`Identity scope controls fact convergence; execution scope controls runtime mutation.` 历史 callback 即使按 identity 找到旧 Binding，也没有获得当前 runtime projection 的写权限。

现有晚到 RCS callback 风险路径必须在实现中显式收口：`TransportOutcomeService.publish()` 先按 `client_request_id` 找到历史 Binding，随后 `_apply_result_evidence()` → `_apply_member_position_projection()` 可能调用 `PositionProjectionService.apply_transport_result()`，而 `apply_transport_result()` 当前在 source task 不同且未允许 replacement 时仍会把当前 projection 改为 `position_unknown`；`_invalidate_other_task_positions()` 也可能直接把其它 task 的当前 projection 标记为 unknown。该 read-after-resolve → current-runtime-mutation 路径必须改为先执行 current execution ownership/invariant guard，再允许 projection mutation。

对于当前 runtime 已 stale 的 callback：仍更新原 TransportTask/Binding 对应的最终事实，不把 callback 当作失败或丢弃；但禁止修改当前 task、rack 或 workstation runtime projection，并记录 `stale_runtime_mutation_blocked` metric 和 structured log，达到阈值时告警。identity conflict、execution ownership conflict 或 invariant violation 均 fail closed：保留原始 callback 与 Transport 最终事实，禁止 runtime mutation，通过现有 logs/metrics/alerting 暴露，不新增 attention/case 实体。

callback/outcome 的处理必须明确分成三个阶段，并建立事实与当前投影之间真正的 durable boundary：

```text
Tx1: Evidence / identity validation
       └─合法→ Fact convergence → COMMIT（历史事实 durable）
                               └─非法→保留原始 Evidence，事实与 runtime 均不修改
Tx2: reload current execution → ownership/invariant guard
       ├─仍属于当前 execution → conditional runtime projection mutation → COMMIT
       └─已 stale / guard conflict → 不写 projection，记录 stale_runtime_mutation_blocked
```

identity conflict、payload conflict、互斥终态冲突等表示事实本身不可信：不得进入 fact convergence，也不得修改 runtime，只保留原始 Evidence 并通过 logs/metrics/alerting 暴露。合法但已 stale 的 callback 必须先完成原 TransportTask/Binding/Evidence 的事实收口；`stale_runtime_mutation_blocked` 是确定性的副作用抑制，不是 retryable failure，不能为了改变当前 projection 重试已经收口的历史 callback。

实现优先采用两个事务段：Tx1 完成 validation + fact convergence 后提交；Tx2 重新读取当前 execution，执行 ownership/invariant guard，并在同一并发保护边界内有条件写 projection 后提交。savepoint 不能替代 durable commit；如果实现必须复用其它 UoW，必须证明 Tx2 的 guard/error 不会使 Tx1 已提交事实被外层 rollback。row lock 或 atomic UPDATE/compare-and-set 只有在 execution transition 也竞争同一 authoritative fencing root 时才算有效 fencing；CAS affected row 为 0 不能直接等同 stale，必须 reload 后分类为确定性 outcome 或 invariant violation。

核心原则：`Validate evidence → durably converge fact → conditionally mutate current projection.`

### 6.2 Tx2 replay 与 ACK 发送边界

当前 `TransportRepository.claim_pending_evidence()` 只扫描 `TransportEvidence.status = 'PENDING'`；`process_pending_evidence()` 成功后把 Evidence 标记为 `APPLIED` 并写 `processed_at`。因此，若 Tx1 已提交而 worker 在 Tx2 前崩溃，单纯重跑现有 pending processor **不能**发现 projection 尚未应用，当前闭环是不完整的。实现必须在现有 Transport outcome dispatcher/processor 中增加有界的 derived recovery scan：从已收口的 TransportTask/TransportMember final fact、`last_operation_id`/outcome provenance 与 `PositionProjection.source_transport_task_id`（及现有 operation provenance）推导“事实已收口但 projection 尚未同一 outcome 应用”的候选；只有确实无法从这些事实判定时，才补充最小 marker，不新增 projection retry status、attention 表或独立 scheduler/queue。

该 replay 只执行 Tx2 projection application，绝不重新进入 physical submission。系统不变量为：`Post-ACK recovery must never invoke physical submission.` ACK 成功永久关闭物理发送重试路径；Tx2 临时失败不得回滚 Transport fact、不得把 Transport 置回 `RECONCILING`、不得再次发送 ECS/RCS。

ACK 后 projection invalidation 是独立于 final-result projection 的可靠副作用：`ACK fact committed / invalidation Tx2 unapplied` 必须由上述 invalidation candidate branch 重新发现。invalidation 与 final result 都通过 `PositionProjectionService`，但 typed intent 分别为概念上的 `InvalidateTransportPosition` 与 `ApplyTransportFinalPosition`，都携带 immutable Transport/member/operation provenance 和 execution authority identity。invalidation replay 不改变 Transport physical lifecycle，不改回 `PENDING`、不增加 `submit_attempt_count`、不设置 `next_submit_at`、不调用 ECS/RCS，也不影响已经 durable 的 ACK/final Transport fact。

invalidation cleanup authority 不是“当前 task mismatch 就一律 stale”：若 newer Task/Drain/newer source 已接管 projection，返回 `SUPERSEDED` 或 `STALE_SUPPRESSED` 且 zero write；若 projection 仍明确是 expected old source、ACK fact 权威存在且没有 newer source，允许 provenance-fenced cleanup invalidation。该例外仍必须执行 execution/resource fencing、effect-phase causal comparator 与 atomic Tx2 guard，不能无条件把当前 projection clear/unknown。

每次 replay 都重新加载当前 execution 并执行 ownership/invariant guard，不能复用 callback 首次处理时缓存的 context。Service 正常返回的 projection outcomes 为 `APPLIED`（已成功或 provenance 已一致的幂等 NOOP）、`ALREADY_APPLIED`、`SUPERSEDED`、`STALE_SUPPRESSED`；明确 transient 技术故障抛出 `PositionProjectionRetryableError`，只重试 Tx2；`PositionProjectionInvariantViolation` fail closed。`STALE_SUPPRESSED` 不是 failure/pending retry；只写 structured log/metric，必要时告警。

projection guard 还必须检查同一 execution 内的 outcome 顺序、step/transport sequence 与现有 projection provenance；ownership match 只是必要条件，不足以允许较老 outcome 覆盖较新 outcome。guard 与 write 必须在同一 row-lock 或 compare-and-set 边界内执行。重复 callback、Tx1 commit response 丢失和 worker crash 后 replay 都必须落入 `APPLIED`/幂等 NOOP 或明确的 `STALE_SUPPRESSED`，不得产生重复 runtime 副作用。

### 6.3 outcome consumer 边界

`outcome_version` 只表示 Transport business fact 的版本推进，`published_outcome_version` 只表示该 business outcome 是否已发布；两者都不表示 runtime projection 已应用。Tx1 提交后允许独立驱动 `publish_pending_outcomes()`，不得等待 Tx2 `APPLIED`。outcome publish 失败只重试 outcome publish；projection apply 失败只重试 Tx2；任一失败都不得回滚 Tx1，也不得重新触发 ACK 已成功的 ECS/RCS physical submission。

manual-picking 及其它 outcome consumer 必须基于 task-scoped Transport fact、Binding、Evidence 和 outcome identity 做业务判定，不得读取当前 workstation/rack projection 来重新解释历史 outcome。当前代码审查已确认 `InstalledPluginTransportOutcomePublisher` → `ManualPickingTransportOutcomePublisher` 是历史 outcome 发布链，而 `scan_flow`、`rack_readiness`、`completion_repository` 读取 `PositionProjection` 的路径属于当前 task/当前 operation 判定；实现时必须保持这两类路径隔离，禁止出现 `historical outcome → read current projection → business decision`。历史 Task A 即使当前已切换到 Task B，且 Tx2 返回 `STALE_SUPPRESSED`，A 的合法 outcome 仍必须发布并收口自身业务事实；projection 的 eventual consistency window 不得改变 A 的解释。

### 6.4 ownership、provenance 与 causal ordering 的边界

三者不得混用：

1. **execution ownership**：判断 incoming outcome 是否仍有权修改当前 runtime，至少重新验证 `workline_id + picking_task_id + step` 及现有 resource fence/ownership identity。
2. **projection provenance**：判断当前 projection 最后由哪个 `TransportTask` / `operation_id` 产生，优先使用 `PositionProjection.source_transport_task_id`、`source_operation_id` 回溯 TransportTask、Binding、Evidence/outcome。
3. **causal ordering**：判断 incoming outcome 相对 current projection source 是更新、更旧还是同一个。不得把 provenance identity 直接当作顺序，也不得把 ownership match 当作顺序证明。

当前 comparison domain 必须冻结：`TransportTask.outcome_version` 只在单个 TransportTask 内递增，用于该 task 的 business outcome publication，不可跨 TransportTask 比较；`TransportDecisionBinding.step` 是业务语义标签，不保证单调；`source_operation_id` 是 Evidence/operation identity，不表达先后；`created_at`、callback arrival time 只能作诊断/排序辅助，不能单独证明业务因果顺序。跨 task 的 ordering 必须回溯现有 task-scoped plan/step、Evidence/outcome provenance 和已确认的 resource/execution fence；如果无法可靠判断，必须返回 `INCOMPARABLE` 并 fail closed，不得覆盖 current projection。

Service/domain contract 需要显式返回一个不持久化的 causal relation：`SAME`、`BEFORE`、`AFTER`、`INCOMPARABLE`。`SAME` 才能映射 `ALREADY_APPLIED`；`BEFORE` 只有在权威事实证明 current source 更新时才能映射 `SUPERSEDED`；`AFTER` 才允许继续 guarded apply；`INCOMPARABLE` 不能伪装成 `SUPERSEDED` 或 `STALE_SUPPRESSED`，必须 zero-write、fail closed，并按 `PositionProjectionInvariantViolation` 记录 structured error/metric/alert（未知技术异常仍按原始异常传播）。ownership、idempotency、causal ordering 是三个独立判断：ownership mismatch 才是 `STALE_SUPPRESSED`，same source/version 才是 `ALREADY_APPLIED`，causal relation 才能产生 `SUPERSEDED`。

本次 domain 盘点暂不能证明同一 active `PickingTask` 内不同 `TransportTask` 修改同一 `(object_type, object_id)` 时的完整 `BEFORE/AFTER` 关系：`PickingTaskPlanMember.plan_revision` 说明 WMS 计划代际，但不记录每个 Transport 的执行序列；Binding 的 `step` 和 `source_evidence_id` 目前分别是业务 scope 与 evidence identity；同一 rack 可依次产生 `IN`、`ROTATE`、`OUT` 等不同 Transport，但没有一个跨 TransportTask 的权威 generation/token 可直接比较。`TransportMember.last_operation_id` 能细化 member provenance，却不提供跨任务因果顺序。因此 D22 的 A 只能在实现阶段证明一个具体 domain comparator 后成立，不能以当前的时间戳比较或 step 排名提前冻结。

drain domain 的可比较范围更窄：仅在 `ReturnBufferDrainResultReader.history()` 已明确提供的同一 drain operation history/checkpoint 内，才可使用既有 operation history 顺序；不得将 UUIDv7 或该顺序推广到 picking Transport。不同 drain operation、不同 picking TransportTask、或 multi-member source 无权威关系时，relation 必须为 `INCOMPARABLE`。比较粒度至少包含 projection identity、TransportTask、member object、`last_operation_id` 和同一 task 内可比较的 `outcome_version`；不得只用 `transport_task_id` 假定所有 member/source 演化相同。

如果实现阶段证明某个合法且高频的 mutation domain（特别是同一 PickingTask 内同一 projection 的 T1/T2）无法从上述事实可靠得到 `SAME/BEFORE/AFTER`，则该 domain 的 A 不通过，必须单独升级为最小 B：由真正的 plan/execution authority 生成并保护 `source_generation` 或 `source_fencing_token`，只在 `PositionProjection` 同行保存 atomic comparison 所需的最小 metadata。该 token 不能由 `created_at`、callback time、数据库自增 id 或 arrival 顺序伪造；不得建立 projection history、retry lifecycle 或新 execution 表。未完成该证明或最小 metadata 设计前，该 domain 不得宣称支持 `SUPERSEDED`。

Tx2 conditional apply 至少返回 `ALREADY_APPLIED`（同一 Transport/outcome provenance 已在 projection）、`SUPERSEDED`（incoming 已被因果更新 outcome 取代）、`STALE_SUPPRESSED`（ownership 或 invariant 不再允许写）、`APPLIED` 或 `RETRYABLE_FAILURE`。不新增这些结果的持久化 enum。硬不变量为：`An older or superseded outcome must never overwrite a projection produced by a causally newer outcome.` 只有在现有 source identity 无法唯一回溯、无法判断幂等应用、无法表达因果顺序、atomic fencing 缺少必要 token/version，或实际 profiling 证明跨表查询不可接受时，才允许增加最小 fencing metadata；不得复制完整 task/step/outcome history或建立 projection history。

#### 6.4.1 共同 fencing root 与 authority transition lock protocol

本次盘点确认：`WorkLine` 行是当前唯一同时被 picking 与 taskless drain 运行路径反复锁定、且能表达工作线边界的现有候选 fencing root；但现有代码尚未让所有会改变 current execution authority 的路径一致竞争该 root，因此不能把当前零散的 `FOR UPDATE` 直接宣称为 fencing。必须先完成以下 transition inventory 和迁移，再宣称 D21 通过：

| authority transition / path | 当前事实与锁证据 | D21 要求 |
|---|---|---|
| picking prepare / plan activation / scan-handoff / archive / deactivate | `PickingTaskPrepareCoordinator`、`PickingTaskPlanActivationService`、manual-picking scan、archive/configuration service 已在 WorkLine 行上工作；随后读取或写入 task/binding | 固定为 `WorkLine -> PickingTask -> Binding/resource-fence -> PositionProjection` |
| picking plan delta 将 Task 置为 `EXECUTING` | `PickingTaskPlanDeltaService.record()` 当前主要锁 task 行，未统一先锁 WorkLine | 迁移到 WorkLine root 先行；Task 状态和 plan/binding 变化必须与 Tx2 竞争同一 root |
| issued / queue change / cancel | issued/cancel 当前使用 task identity、dispatch/task 行锁；queue change 主要是 queued task 顺序事实 | 只有会改变 current authority 的分支纳入 root protocol；queued-only 顺序更新可保持 task lock，但不得与 active authority transition 形成反向锁序 |
| drain start / WAIT→READY / rack in/rotate/out / drain switch | `ReturnBufferDrainResultReader.history()`、`DrainRepository.current()/transport()` 以 WorkLine-scoped Confirmation/Evidence、drain operation/correlation、taskless Binding 和 rack fence 判定；owner/flow 已使用 WorkLine lock | drain authority transition 必须先锁 WorkLine，再锁 Confirmation/Evidence、Binding/resource-fence；不得按“最新 created_at”另造 current 规则 |
| Transport outcome / projection replay | 当前 transport task、Binding、projection 各有 identity/advisory/row lock，不能单独证明 current authority 未切换 | Tx2 必须先锁 WorkLine root，再按统一顺序加载 authority facts 和 projection；所有 production projection mutation 归 `PositionProjectionService` |

因此本 SPEC 冻结核心 invariant：`Execution authority transition and projection mutation must serialize on the same authoritative fencing resource.` 当前实现阶段优先采用现有 `WorkLine` 行作为 root，不新增 execution 表或 lock service；如果某条 authority transition 不能安全映射到 WorkLine，必须在实现前证明一个现有、生命周期稳定且由竞争方共同锁定的更小 root，否则该路径不得声明已完成 recovery fencing。`resource_fence_id` 只有在确认其 identity 不被复用、authority transition 会同步切换/保护它后，才可作为 ABA fence；不得从字段名称推断 fencing 语义。

统一 Tx2 lock order 为：`authority fencing root (WorkLine) -> required PickingTask 或 drain Confirmation/Evidence -> Binding/resource-fence facts -> target PositionProjection`。所有 projection mutation path 必须遵守同一顺序；禁止任何 production path 先锁 `PositionProjection` 再锁 authority facts。Tx2 持锁期间只做 reload、验证、guarded write/NOOP 和 commit，不调用 ECS/RCS/WMS、publish event 或其它外部 I/O。

Projection row 不存在时，`FOR UPDATE` 不会锁住不存在的行。Tx2 必须在已持有 authority root 的前提下依赖现有 projection identity unique constraint 尝试插入；发生并发 unique conflict 时重新读取 projection，并重新执行 ownership、provenance、idempotency、causal-order 分类。unique conflict 本身不能直接映射为 `ALREADY_APPLIED` 或 retryable failure。atomic UPDATE/CAS affected rows 为 0 也只表示并发状态变化，必须 reload 后分别分类为 `STALE_SUPPRESSED`、`ALREADY_APPLIED`、`SUPERSEDED` 或 `PositionProjectionInvariantViolation`。

PostgreSQL concurrency 验收必须使用至少两个独立 connection/session，覆盖 picking 和 taskless drain 各一组：A 持有 root 时 B 的 authority transition 等待，或 B 先完成切换后 A reload 得到 `STALE_SUPPRESSED`；任何 interleaving 都不得出现 B 已成为 authoritative current 后 A 仍提交 current projection mutation。另需覆盖两个 worker 同时发现 projection missing 时只有一条 projection，loser reload 后得到正确 deterministic outcome，且不触发 physical Transport retry。D21 的通过标准不是出现 `FOR UPDATE`，而是：`Once a newer execution becomes authoritative, an older execution can no longer commit a current projection mutation.`

### 6.5 `PositionProjection` single mutation authority

`PositionProjectionService` 是 current `PositionProjection` 的 single mutation authority，但不是所有领域业务逻辑的 God Service。Transport、outcome replay、manual-picking、scan/handoff 等调用方只能提交要应用的 immutable fact/intent（TransportTask、operation、最小 authority identity、目标位置等）；所有 set/update position、invalidate、`position_unknown`、clear/reset、source transport/operation provenance 及其它改变 current execution projection 语义的写入，必须通过该 Service 的受保护 mutation API。API 可以按领域动作拆成 `apply_fact` / `apply_transport_result`、`invalidate_from_fact` 等多个入口，但必须共享同一 safety kernel：current execution ownership、provenance validation、causal-order/superseded、idempotency/already-applied、atomic fencing/CAS 与 mutation commit boundary。

Repository 只是 `PositionProjectionService` 的 persistence mechanism，不是其它业务 Service 可直接调用的公共 mutation API；read-only query 可按职责保留。现有生产代码盘点必须覆盖 `TransportService._apply_member_position_projection()`、`_invalidate_other_task_positions()`、`PositionProjectionService.apply_transport_result()`、`invalidate_transport_member()`、manual-picking scan/handoff、所有 `PositionProjection` 字段赋值、Repository update/save 与直接 SQL update。当前残留扫描已确认 manual-picking `scan_flow` 存在直接字段赋值（`position_json`/`position_unknown`/`arrival_face`），必须迁移；`TransportDebugPositionProjection` 属于独立可丢弃诊断投影，不属于本 invariant。

Service contract 正常返回只表达 `APPLIED`、`ALREADY_APPLIED`、`SUPERSEDED`、`STALE_SUPPRESSED` 四类 deterministic outcome；`PositionProjectionRetryableError` 只表示明确 transient infrastructure failure，`PositionProjectionInvariantViolation` 只表示权威事实/contract contradiction。只有 retryable exception 进入 Tx2 recovery；其它确定性 NOOP/抑制不重试，invariant exception fail closed 并通过既有 logs/metrics/alerting 暴露。ownership/provenance/order check 与最终 write 必须在同一 row-lock 或 compare-and-set 边界内。

架构 guardrail 只保护一个窄而硬的 invariant：`Production runtime PositionProjection mutations must not bypass PositionProjectionService as the single mutation authority.` 第一版只扫描真实 production runtime 的 `src/` 与 `workline_plugins/` 相关路径，tests、migration、bootstrap/scripts 不纳入扫描范围；不实现通用 Python call-graph/data-flow 分析器，也不要求识别所有 alias/dynamic dispatch/raw SQL 变体。规则以明确的 `PositionProjection` model/table/mutation primitive 为目标，不按 `*PositionProjection*` 模糊匹配，因此 `TransportDebugPositionProjection` 不触发。

允许 mutation primitive 出现在极小的具体文件 allowlist：`src/app/execution/services/position_projection_service.py`（authority）和 `src/app/execution/repositories/position_projection_repository.py`（persistence mechanism），每项必须写明理由。扫描至少阻断：`PositionProjection` 关键字段直接赋值、production runtime 直接构造/覆盖 current projection、已知 repository mutation API 越权调用、ORM/Core 对 `PositionProjection` 的直接 UPDATE，以及明确 position projection table 的直接 SQL update（小范围文本 fallback）。`manual-picking/scan_flow.py` 当前直接字段 mutation 是基准验收案例：迁移前扫描失败、迁移后通过、人工重新加入直接 mutation 时必须失败。Architecture test 只防明显 bypass，不替代 runtime ownership/provenance/causal-order/idempotency/atomic-fencing correctness；新增 allowlist 本身属于架构变更，必须 review。

mutation API 的输入必须是最小、immutable、fact-oriented 的 typed contract，而不是 `dict[str, Any]`、大量 optional 参数或通用 command framework。优先组合现有 `TransportExecutionAuthority`、`TransportOutcome`/`TransportMemberOutcome`、`RackPosition`/其它已有 value object 与 manual-picking 已有 execution context；不复制第二套 projection identity model。execution authority 使用最小 typed union/value objects（概念上 `PickingExecutionAuthority` 与 `DrainExecutionAuthority`），而不是把 `picking_task_id` 设计成所有 mutation 的必填字段。contract 可携带 `transport_task_id`、`operation_id`、`workline_id`、可选的 `picking_task_id`、`step`、必要 resource fence identity、immutable drain correlation/operation identity、target position，以及明确区分的 `InvalidateTransportPosition` 或 `ApplyTransportFinalPosition` intent；不得把 invalidation 伪装成 `ApplyPosition(None)`。调用方不得携带并让 Service 信任 `is_current_execution`、`ownership_valid`、`is_newer`、`already_applied` 等 guard 结论。incoming execution context 只表示 originating/expected identity；首次 apply 和 replay 都必须在自己的 Tx2 transaction 中按 immutable IDs 重新读取 Transport/Binding/current execution authority，并在实际 write 时重新执行 fencing guard。禁止“外层 typed、内层 `action + payload dict`”的伪 typed contract；只定义当前实际需要的两个 effect intent。

正常 guard/mutation 判定只返回一个最小封闭 typed outcome `PositionProjectionMutationOutcome`：`APPLIED`、`ALREADY_APPLIED`、`SUPERSEDED`、`STALE_SUPPRESSED`。这四种都是 mutation authority 已成功完成判断的 deterministic completion，不是异常，也不进入 retry：`STALE_SUPPRESSED` 表示 incoming fact 已失去当前 execution 权限，`SUPERSEDED` 表示已有因果更新 projection，`ALREADY_APPLIED` 表示幂等 replay 已经完成。DB connection、lock timeout、serialization failure、worker/transaction interruption 等基础设施问题不建成普通 enum，而作为最小 retryable projection mutation exception，由 Tx2 recovery 单独重试。immutable identity conflict、authoritative fact contradiction、contract violation 等作为 invariant/contract exception fail closed，统一记录 structured error/metric/alert，不自动 retry。`incoming Task A != current Task B` 是正常 `STALE_SUPPRESSED`；同一 immutable identity 矛盾解析出 Task A/Task B 才是 invariant violation。typed outcome 属于 execution/position-projection contract，不反向成为 Transport outcome status；mutation authority 统一记录四类 outcome metrics，调用方只按“normal return / retryable exception / invariant exception”三路契约处理，任何路径都不得重新打开 ACK 已成功的 physical submission。

execution/position-projection contract 只定义两个最小专用异常：`PositionProjectionRetryableError` 与 `PositionProjectionInvariantViolation`，不复用 Transport domain exception，也不建立复杂继承体系。前者仅包装明确的 transient DB connection、lock timeout、deadlock victim、serialization failure 或其它已分类且“从 authoritative state 完整 replay Tx2 有合理机会安全成功”的故障，必须 `raise ... from exc` 保留 cause；禁止 `except Exception` 兜底包装，programming error、`TypeError`/`AttributeError`、未知 `ValueError`、schema/SQL programming error、NOT NULL/FK/constraint violation及未分类异常保持原始传播。后者只表示权威事实/immutable identity 自相矛盾或 typed contract 与权威事实不一致，例如同一 immutable Transport identity 解析到两个 execution owner。正常 ownership mismatch、superseded 和 duplicate replay 仍分别返回 `STALE_SUPPRESSED`、`SUPERSEDED`、`ALREADY_APPLIED`。

现有 `PositionProjectionAuthorityError` 的 raise 路径必须逐个重分类，而非机械改名：unsupported `object_type` 属于 contract/programming error；inactive/missing WorkLine 或 authority 与权威事实不一致属于 `PositionProjectionInvariantViolation`；底层明确 transient transaction failure 才包装为 `PositionProjectionRetryableError`；未知异常不转换。当前未发现上层 catch；迁移时需补充窄捕获：normal outcome=done，retryable error=只 retry Tx2，invariant violation=fail closed+observability，其它异常正常 error propagation。

| 消费者 | 新语义 |
|---|---|
| position projection / Transport outcome / audit / monitor | 只记录和展示事实 |
| `BatchRepository.occupied_source_rack_ids()`、`fenced_source_rack_ids()` | 删除作为物理提交门禁的查询，只保留 task 业务 binding 查询 |
| `rack_ready` | 只检查当前 task 的业务 Decision 和最新权威 Transport result |
| `scan_flow` | 只按当前 operation/result/evidence 推进，不扫描历史物理占用 |
| `completion_repository` | 只检查当前 task 的未闭业务面、料箱和可靠决定 |

RCS/ECS 明确 `REJECTED/FAILED` 时，WES 保存该终态并交给业务插件解释，不改写为成功。

## 7. 异常隔离与手工拣选

### 7.1 `DATA_CONFLICT`

`DrainRepository`、Transport outcome processor 和 prepare coordinator 对 identity/evidence/owner mismatch 必须在单条记录 savepoint 内记录错误日志/指标，回滚该条业务副作用，然后继续同批下一个 workline/task。冲突和 invariant violation 保留原始事实，不新增 attention/case 实体；普通 timeout 保留原记录并自动恢复，不要求人工对账。不得让异常冒泡终止整个 Celery 批次。

### 7.2 业务规则

- 取消来源货架可以先发起 `return_batch`。
- `NO_BATCH` 是确定终态，不重试，继续 CTU02/CTU03。
- 已取消来源货架不阻塞 `completion_confirm`。
- 当前 task 无待执行料箱、无执行中料箱、无未闭业务货架面和无未闭可靠决定时，请求 `outbound.picking_task.completion_confirm@v1`。
- 无 active PickingTask 且有 READY 退料箱时创建 drain。
- drain 超时继续用原 identity 自动恢复。

### 7.3 RUNTIME_RESET

`wes_biz.workline_runtime_status_projections` 保存 `source`、`stopped_reason`、`stopped_at`、`resumed_at` 和现有 `evidence_json`。`evidence_json` 只作为恢复动作的 evidence/diagnostic context，由 Service 统一写入稳定 key：`resume_evidence_id`、`resume_trigger`、`driver_tick_id`、`observed_at`；它不是新的权威事实，不作为状态机判断是否允许从 `STOPPED` 恢复到 `READY` 的输入，不复制原始事实内容，也不建立 FK、索引或独立查询能力。是否允许恢复必须基于原始事实对象和既有 invariant 判断。调用方不得自行扩展同一语义的不同 key；未来只有当恢复证据成为业务查询条件、状态机输入或需要数据库级约束时，才将其提升为正式字段并通过 migration 演进。`RUNTIME_RESET` 不是业务 `STOPPED`。恢复 Service 将状态设为 `READY` 后，在 `celery` 队列投递一次 `activate_picking_task_plans_batch`；workline advisory lock 保证幂等，失败记录错误日志/指标，下一 tick 重试。

## 8. 生产异常验收样本

| 样本 | 复现 | 通过判据 |
|---|---|---|
| 510042 | 创建 `RACK_ROTATE`，保存 ACK 成功的 `ACCEPTED`，不写终态 Evidence，推进到 deadline | 原 Transport 进入结果未知/`RECONCILING`，记录重试原因但不再次下发；迟到原 identity 结果仍可闭合，且其它 task/prepare 行数不减少 |
| AC5A... | 插入 drain binding 与 READY Evidence 不匹配 | 记录 `DATA_CONFLICT` 日志/指标；该 task 不创建伪造 prepare；其它 task 的 prepare confirmation 正常生成 |
| KT16 | `A000001905`、510050、510055 按 `NO_BATCH` 路径运行 | CTU03、completion_confirm、drain 依次有权威记录，取消货架不阻塞完成 |
| 迟到回调 | ACK 成功后发送第一次 identity 的迟到终态 | 原记录闭合；不创建第二个业务结果；冲突响应保留原始事实并触发错误告警 |
| 历史 callback + 当前复用货架 | 旧 execution 的 RCS callback 在同一 `rack_id` 被新 execution 复用后到达 | 旧 Transport/Binding 事实闭合；当前 rack/workstation projection、ownership 和 runtime status 不变；记录 `stale_runtime_mutation_blocked` |
| Tx1/Tx2 边界 | 合法旧 callback 完成事实收口后，Tx2 ownership guard 失败 | Transport 不回到 `RECONCILING`；Tx1 已提交事实保持闭合；当前 projection 不变 |

### 8.1 2026-09-21 现场会话验收评审

本节记录以下现场调试会话的只读验收结论：

- `codex://threads/01a0c430-46e4-7e50-99bb-efd0283bf8f8`
- `codex://threads/01a0c469-0cea-7a50-b11b-89b86a14d733`
- `codex://threads/01a0bb25-9343-7071-b2d4-952448696d99`
- `codex://threads/01a0ba2f-3d6a-7de1-a4ba-3720e4f51719`
- `codex://threads/01a0bb76-1fc2-7ab3-8155-c4509077384e`

这些会话和 `wes_integration` 历史数据只作为现场故障样本与运行观察，不能替代绑定当前代码快照的确定性验收。期间发生过人工删除历史 Binding、drain operation/Evidence/conflict，手工触发 Celery task 和多轮热更新；因此不能把后续收敛外推为原路径自动恢复，也不能作为 PostgreSQL fixture isolation、crash replay 或 provider contract 的证明。

现场确认的事实：

- `NO_BATCH` 后继续 CTU03、取消来源架不阻塞 completion 曾在热修后观察到，但未覆盖当前快照的完整自动恢复矩阵。
- 自动 drain 曾创建并被 WMS/ECS 接纳；也观察到所有 rack face 耗尽后旧 drain 卡住、active-task source rack 与 drain authority 竞争、return batch 误归属等反例。
- 多个 Transport/DeviceCommand 到达 `ACCEPTED` 后长期没有权威终态；这证明 ACK 不能替代物理完成，不证明 provider 支持 same-identity resend 或 identity query。
- 历史 task Binding 曾实际占用当前 FIVE_RACK 容量窗口。当前实现已增加 active-task/taskless-drain 过滤及对应插件回归，但现场重验仍待执行。
- WMS 同 identity response drift 会进入 `RECONCILING` 并保留冲突事实；现场曾通过人工删除旧事实继续，不能计为自动恢复通过。

现场验收结论：`FIELD FAILURE EVIDENCE CAPTURED — NOT ONSITE VERIFIED`。AC8、AC9、AC38 只有部分运行佐证；AC4、AC6、AC34、AC36 存在历史反例；AC39 没有真实 provider 合同证据。

### 8.2 当前实现快照

截至 2026-09-21 的当前 staged snapshot：

- QUALITY：`4192 passed, 5 skipped`；skip 为需要 live WES/API credentials 或已构建 production image 的非默认本地验收。
- staged HEAVY：`495 passed, 0 failed, 0 errors, 0 skipped`，JUnit 为 `reports/heavy-local-wes-heavy-local-50825.xml`；selector 当前为 57 个 owner。
- checkpoint 后 T6 durable metrics 增量快照：QUALITY `4216 passed, 5 skipped`；staged selector 16 个 owner；HEAVY `108 passed, 0 skipped`，JUnit 为 `reports/heavy-local-wes-heavy-local-83873.xml`。
- worker lifecycle 已看到 prefork child ready、confirmation `task.body.start/task.body.done` 和 result backend 返回；失败运行会把 worker metadata/log 归档到 `reports/heavy-worker/<run_id>/`。
- manual-picking 在 fresh PostgreSQL/Redis 与真实 prefork worker 下：`401 passed, 0 skipped`；覆盖 historical task 不再冻结当前 source window、task 与 drain authority 原子互斥、drain reservation 阻止新 prepare、未闭合 return-batch 保留原 identity，以及 departure wiring。

上述绿灯证明当前所选测试 owner 通过，不代表 AC1-AC42 已全部有唯一 owner，也不替代真实 provider、现场终态和尚未实现的 drain projection-gap/metrics 验收。

T2 当前冻结的 authority lock order 清单为：`WorkLine exclusive root -> PickingTask 或 drain Confirmation/Evidence current fact -> sorted object/resource fence -> Binding/decision identity -> PositionProjection row`。Tx2 持锁期间只允许 reload、guard、mutation、commit；禁止外部调用。当前已修复 device-position 与 manual-picking ScanFlow 的反序路径；仍未闭合 historical Drain A/current Drain B 的 typed authority 识别和全部 transition 清单。

T4 SSH 只读验收（`CANTAISYS@100.94.216.118`）确认：真实 WMS Swagger 只有匿名 `POST /api/v1/wes/transport-requests`，没有 duplicate/payload-drift/query contract；两个 ECS 的 status endpoint 对已存在和不存在 command code 均返回空成功形状，缺少 NOT_FOUND/retention/state schema；`wes_integration` 的 199 条 TransportTask 与 142 条 DeviceCommand 均没有 `attempt_count > 1` 样本。结论保持 `NO_SAFE_AUTOMATIC_RECOVERY`，不得开放 automatic resend 或 query-before-resubmit。

## 9. Acceptance Criteria 与测试映射

| AC | 通过条件 | Owner |
|---|---|---|
| AC1 | WMS confirmation 按原 identity 自动恢复；ECS/RCS 指令仅在 ACK 未成功时按原 identity 指数退避重发，ACK 成功后不再下发，digest 不变 | 基础 FAST + integration |
| AC2 | `SKIP LOCKED` 下同一记录只有一个有效 lease，过期 lease 可重领 | 基础 integration |
| AC3 | 发送层 deadline 每次按当前尝试重算并支持退避；ACK 成功后的结果等待超时不重新下发，超过告警阈值仍继续记录和等待权威结果 | 基础 FAST |
| AC4 | 510042 场景不影响其它 task/prepare | Transport integration |
| AC5 | 单条 DATA_CONFLICT 不打断 prepare 批次，原始事实保留且日志/指标/告警可观测 | 基础 integration |
| AC6 | 历史 task binding 不进入当前 task 查询 | Binding FAST |
| AC7 | `position_unknown` 不再作为物理提交门禁，RCS reject 仍原样保存 | Transport/plugin integration |
| AC8 | `NO_BATCH` 后继续 CTU03，取消货架不阻塞 completion | manual-picking FAST |
| AC9 | 无 active task + READY 料箱自动 drain，timeout 自动原身份恢复 | manual-picking integration |
| AC10 | RUNTIME_RESET 恢复后产生一次幂等 driver tick | workline integration |
| AC11 | 晚到旧 Transport/RCS callback 按 immutable identity 收口原 Binding/Transport 事实，但在当前 active execution 已切换或 resource ownership 不匹配时，不修改当前 projection，并产生 `stale_runtime_mutation_blocked` | Transport + position projection integration |
| AC12 | 同一货架被历史 execution 与当前 execution 重用时，不能仅凭 `rack_id`/`workline_id` 通过 runtime guard；identity conflict、ownership conflict 和 invariant violation 均保留原始事实并 fail closed | Transport/Binding FAST + integration |
| AC13 | callback 依次经过 evidence validation、fact convergence、runtime mutation；Tx1 durable commit 后 Tx2 guard 失败不回滚历史事实 | Transport integration |
| AC14 | identity/payload/互斥终态冲突不进入 fact convergence，原始 Evidence 保留且 fact/projection 均不被错误修改 | Transport/Binding FAST + integration |
| AC15 | ownership transition 与 projection mutation 竞争同一 authoritative fencing root；双 session 并发时旧 callback 不能通过 TOCTOU 覆盖新 execution，CAS/unique miss 经 reload 后分类而不是统一映射 stale | Position projection integration |
| AC16 | 同一合法 callback 重复到达时 fact convergence 幂等，projection 不产生重复副作用 | Transport outcome integration |
| AC17 | Evidence 已在 Tx1 后标记 `APPLIED`、Tx2 未完成时，derived recovery scan 能从 Transport fact + projection provenance 重新发现候选；只重试 Tx2，不重发物理指令 | Transport integration |
| AC18 | ACK 成功后的任何 recovery/replay 路径都不会调用 physical submission，也不会把 Transport 回退为 `RECONCILING` | Transport/Celery integration |
| AC19 | projection application 正常返回 deterministic `APPLIED/ALREADY_APPLIED/SUPERSEDED/STALE_SUPPRESSED`；明确 transient 才抛 `PositionProjectionRetryableError` 并只 retry Tx2；retry 重新加载 current execution，stale/superseded 不再重试 | Position projection integration |
| AC20 | 同一 execution 内较老 outcome 不能覆盖较新 outcome；重复 callback、commit response 丢失和 worker crash replay 均为幂等 NOOP 或安全抑制 | Transport outcome integration |
| AC21 | Tx1 已提交、Tx2 未完成时，`publish_pending_outcomes()` 仍能发布历史 outcome；Task B projection 不被 Task A 污染，插件不读取 current projection 解释 Task A | Transport/plugin integration |
| AC22 | outcome publish retry 与 projection replay 可分别成功；任一失败不回滚 Tx1、不回退 Transport、不调用 physical submission | Celery/integration |
| AC23 | ABA：Task A/Rack R → Task B/Rack X → Task C/Rack R，A callback 晚到必须 `STALE_SUPPRESSED`，不能因 rack/workline 再次相同而修改 C | Position projection integration |
| AC24 | 同一 execution 乱序：T1→P1、T2→P2，T2 先应用、T1 后到时最终 projection 保持 P2；旧 outcome 不覆盖因果更新 source | Position projection integration |
| AC25 | 所有 current `PositionProjection` mutation 均经过 `PositionProjectionService` safety kernel；直接字段赋值、Repository mutation、SQL update 的生产残留扫描失败 | Architecture/static guardrail + projection integration |
| AC26 | mutation authority 对四类 deterministic outcome 正常返回；retryable infrastructure exception 只重试 Tx2，invariant/contract exception fail closed 且不自动 retry | Position projection FAST |
| AC27 | 真实 PostgreSQL 中 Tx1 已提交、Evidence 已 processed/APPLIED、ACK 成功且 `outcome_version` 已推进但 projection lagging；fresh recovery entry point 能发现 gap 并只执行 Tx2 | Transport PostgreSQL integration/resilience |
| AC28 | Tx2 commit 后 worker interruption replay 为 `ALREADY_APPLIED`；Task A→B 切换 replay 为 `STALE_SUPPRESSED`；较新 T2 后 replay 老 T1 为 `SUPERSEDED`；均无重复 projection/physical side effect | Position projection PostgreSQL integration |
| AC29 | post-ACK recovery 使用 fail-fast ECS/RCS adapter，任何 physical submission 调用均失败；`publish_pending_outcomes()` 可在 projection lagging 时独立发布历史 outcome | Transport/Celery integration |
| AC30 | 独立 QUALITY architecture test 在限定 production runtime corpus 中阻断绕过 `PositionProjectionService` 的 mutation，并输出 repo-relative path、line、primitive/type、短 source context；allowlist 可审计且无残留 | `tests/architecture/test_position_projection_mutation_authority.py` |
| AC31 | Task A historical outcome 在 A projection lagging/STALE、Task B current projection 冲突时，仍仅凭 A task-scoped facts 收口；B projection/runtime 不变，current-task projection 读取不参与历史判定 | manual-picking plugin FAST/integration |
| AC32 | Task A final fact + A active 时 candidate 可发现；A→B switch 后 A 不再统计为 actionable gap；scan 看到 A、apply 前切换 B 返回 `STALE_SUPPRESSED`；后续 tick 可发现 B 的真实 actionable gap | Transport PostgreSQL integration/performance |
| AC33 | stale historical fact 不持续增加 `projection_gap_count`/`oldest_projection_gap_age`；`stale_suppressed_total`/`superseded_total` 单独计数，candidate batch 有稳定排序且无 starvation | Transport performance/metrics |
| AC34 | taskless drain current authority + projection missing/NULL provenance 可被 drain 分支发现；historical Drain A/current Drain B 被排除；scan/apply race 返回 `STALE_SUPPRESSED`；Drain ABA（C1→C2→C3）不覆盖 C3 | Drain/position PostgreSQL integration |
| AC35 | picking 与 drain 同时存在 backlog 时，`UNION ALL` candidate scan 在代表性数据上无永久 starvation；`authority_kind=picking|drain` 低基数 metrics 可区分 candidate/outcome/backlog | Transport performance/metrics |
| AC36 | 所有会改变 current authority 的 picking/drain transition 与 Tx2 竞争同一 WorkLine fencing root，并遵守 `WorkLine -> authority facts -> Binding/resource-fence -> PositionProjection` 锁序；双 session 并发切换与 projection-missing insert 只能收敛为正确 deterministic outcome，旧 execution 不能在新 execution 成为 authoritative current 后提交 projection | Position projection PostgreSQL concurrency/integration |
| AC37 | causal comparator 明确区分 `SAME/BEFORE/AFTER/INCOMPARABLE`；同源版本、同 task 版本升降、domain 内权威跨 task 顺序和不可比较场景分别得到唯一结果；`INCOMPARABLE` zero-write + `PositionProjectionInvariantViolation`，不伪装为 `SUPERSEDED/STALE_SUPPRESSED`；同一 PickingTask 的 T1/T2 若无法证明顺序则升级最小 fencing metadata 设计 | Position projection FAST + PostgreSQL ordering integration |
| AC38 | `PENDING` 是唯一 physical submit 状态；ACK-success 后任何同一 Transport identity 都不能再次调用 ECS/RCS、增加 submit attempt 或回到 PENDING；pre-ACK ambiguous submission 仍按原 identity/idempotency contract 处理，poison state fail closed | Transport FAST + PostgreSQL/Celery integration |
| AC39 | pre-ACK `SUBMIT_DELIVERY_UNKNOWN` 与 post-ACK `TRANSPORT_DELIVERY_UNKNOWN` 分离；provider capability 未证明 safe same-identity resend 时不自动重发；query-before-resubmit 不增加 attempt；late ACK 在真正 adapter call 前可关闭 resend；`NO_SAFE_AUTOMATIC_RECOVERY` 保持 ambiguous + observability | Transport provider-contract FAST/integration |
| AC40 | 单 candidate Tx2 transient failure 只延迟该 candidate 到下一 tick；独立事务释放锁并继续处理本 batch 其它 candidate；scan/object/worker failure 分开计数；oldest retryable candidates 不会永久饿死更年轻 actionable gaps；未 commit/已 commit response unknown replay 分别收敛为 `APPLIED`/`ALREADY_APPLIED` | Projection replay PostgreSQL/Celery integration/performance |
| AC41 | ACK-success invalidation branch 与 final-result branch 分离；invalidation provenance 可区分未应用/已应用；`ACK_INVALIDATION BEFORE FINAL_RESULT`，late invalidation 对已应用 final P2 返回 `SUPERSEDED` zero-write；旧 source cleanup 只在 expected provenance 且无 newer source 时允许；ACK poison state 仍可安全发现 invalidation gap 且 zero physical submit | Position projection PostgreSQL crash/replay + FAST contract |
| AC42 | Evidence `apply_status=APPLIED`、Transport/ACK 或 final fact 已 `FACT_COMMITTED`、projection effect 未应用时，candidate 仍可发现；ACK invalidation 与 final-result 两个 branch 各有真实 regression；日志/metrics 能区分 Evidence processing、fact convergence 和 projection mutation | Transport/position PostgreSQL integration + QUALITY contract |
基础能力测试放 `tests/` 对应核心域；手工拣选行为放 `workline_plugins/manual-picking/tests/`；真实 Celery/数据库放 integration；同步更新 `docs/architecture/heavy-test-impact.toml`。纯文档修改不新增测试。

Tx1/Tx2 durable boundary 必须至少有少量真实 PostgreSQL integration/resilience tests，不能由 mock/service test 替代。测试必须让 Tx1 真正 `COMMIT`，丢弃原 session/UoW，再由 fresh session/UoW 进入真实 production recovery entry point 执行独立 Tx2；fixture 不得用外层永不提交事务、flush 或 savepoint 吞掉 Tx1 的可见性。第一版可在 Tx1 commit 后的明确 fault-injection point 模拟 worker interruption，不要求 `kill -9` Celery worker，但 recovery 前必须用新 session 直接验证 `FACT COMMITTED / PROJECTION LAGGING`：Transport fact 已收口、ACK 成功保持、Evidence 已是 Tx1 应有状态、`outcome_version` 已推进、current projection 尚未应用。

D21 的 PostgreSQL concurrency tests 必须额外验证真实 fencing，而不是只 mock `FOR UPDATE`：使用至少两个独立 connection/session，分别覆盖 picking 与 taskless drain。测试要证明 WorkLine root 被 Tx2 或 authority transition 持有时另一方会等待；若 authority transition 先提交，Tx2 reload 后只能得到 `STALE_SUPPRESSED` 或其它正确 deterministic classification；不得出现新 execution 已 authoritative 但旧 execution 仍提交 current projection。另覆盖两个 worker 同时发现 projection missing 的唯一键 insert 竞争：最终一条 projection，loser reload 后分类，且不触发 physical submit。测试 fixture 必须按 `WorkLine -> authority facts -> Binding/resource-fence -> PositionProjection` 锁序构造，并验证旧 production 路径不存在相反的 `projection -> authority` 顺序。

D23 的 Transport regression 必须把 submit candidate query 和 Service adapter 二次 guard 都覆盖：`PENDING + no ACK + due` 可以 submit；pre-ACK timeout/no-ACK 依既有 idempotency contract 判定；`ACCEPTED`、`RECONCILING`、`RECONCILING + next_submit_at due`、ACK-success Evidence、ACK 后 projection gap 均 zero physical submit；`PENDING + ACK-success Evidence`、无 ACK 却 `TRANSPORT_DELIVERY_UNKNOWN` 等 poison state fail closed、zero attempt increment；ACK 后 late callback 仍可事实收口；ACK 后 result overdue 转 `RECONCILING` 但 `submit_attempt_count` 不变。通过标准为：`Once authoritative ACK success exists for a TransportTask, no production path can cause that same TransportTask to invoke physical submission again.`

D24 的 provider-contract tests 必须按真实 adapter capability 分组：首次 submit ACK 后进入 post-ACK fence；pre-ACK timeout + `SAFE_SAME_IDENTITY_RESUBMIT` 使用同一 `client_request_id` 重试且 attempt 加一；timeout + identity query 找到已有 command 时只收口、不 resend、attempt 不增加；`NO_SAFE_AUTOMATIC_RECOVERY` zero resend、保持 `PENDING/SUBMIT_DELIVERY_UNKNOWN` 并暴露 observability；scheduled retry 前 late ACK 到达时 zero resend；pre-ACK ambiguous 不得标成 `TRANSPORT_DELIVERY_UNKNOWN`；ACK 后不得保留任何有效 auto-submit eligibility。测试不得用配置名替代 provider 合同证据；没有真实 duplicate/query 语义证明的 adapter 只能走 query-only 或 fail-closed policy。

D25 的 replay tests 必须证明 retry 发生在下一次 candidate discovery tick：同一 candidate 的 Tx2 transient failure 后 rollback/释放锁，本 tick 不再重试该 candidate，但继续处理同批其它 candidate；scan failure、object failure、worker failure 分别可观测。构造 oldest candidates 持续 retryable、同时存在大量年轻 actionable gap 的 backlog，验证 bounded batch、per-candidate continuation、queue isolation 不产生永久 starvation。另覆盖 Tx2 commit outcome unknown：实际未 commit 的 replay 得到 `APPLIED`，已 commit 但响应丢失的 replay 得到 `ALREADY_APPLIED`，均无重复副作用；不得叠加 driver/Service/Celery/Beat 多层 retry。

D26 的 PostgreSQL/crash tests 必须覆盖独立 invalidation branch：ACK fact commit 后在 invalidation Tx2 前中断、无 final result 时下一次 recovery 能发现并安全 invalidate 旧 projection；final P2 先应用后 replay invalidation 得到 `SUPERSEDED`、P2 保持；Task A ACK/invalidation lag 后 Task B/newer authority 接管时 A 不改变 B；invalidation 已 commit 但 worker 未收到结果时 replay 得到 `ALREADY_APPLIED`；`PENDING + ACK-success Evidence` zero physical submit 但仍能由 ACK fact 进入 invalidation recovery。测试还必须证明现有 source fields 足以区分 invalidation applied provenance，否则阻止宣称 D26 闭环并升级最小 metadata 设计。

D27 的真实 candidate-discovery regression 必须分别构造：`Evidence.apply_status=APPLIED + FACT_COMMITTED + final-result projection lagging`，以及 `Evidence.apply_status=APPLIED + ACK FACT_COMMITTED + invalidation projection lagging`；两者都必须仍能从 authoritative facts/provenance 进入对应 branch。测试和 review 只接受 domain-qualified `evidence_processed`、`transport_fact_converged`、`projection_outcome` 术语，保留数据库现有 `APPLIED` 字面量，不通过改 enum 消除歧义。

gap recovery 测试必须从真实 processor/dispatcher 的 derived gap-discovery 入口进入，即使 Evidence 已 `processed/APPLIED`，也要由 Transport fact + projection provenance 重新发现 Tx2 gap；直接调用 `PositionProjectionService.apply(...)` 不算 recovery 证据。post-ACK replay 的 physical submit adapter 使用 fail-fast sentinel，任何 ECS/RCS submission 调用都立即失败，以直接验证 `Post-ACK recovery must never invoke physical submission.` PostgreSQL 层只验证 commit visibility、独立事务、gap discovery、幂等、ownership/fencing、CAS/locking；mutation result classification 用 FAST unit tests，Celery queue/Beat routing 用少量 topology/E2E tests，不把所有职责堆进单个 integration test。Tx1 已提交、Tx2 lagging 时，`publish_pending_outcomes()` 必须仍可基于 Transport fact 独立发布历史 business outcome。

`tests/architecture/test_position_projection_mutation_authority.py` 是 single mutation authority 的独立 QUALITY/PR-fast owner：只扫描明确的 `src/` 与 `workline_plugins/` production runtime paths，不启动 PostgreSQL/Celery、不访问网络、不扫描 tests、migration、bootstrap/scripts，也不匹配 `TransportDebugPositionProjection`。测试失败必须列出 repo-relative path、line、mutation primitive/type 和最短 source context。allowlist 只能使用具体文件路径及理由；路径不存在、已迁移文件残留或目录 wildcard 均使测试失败。若实现复杂度可控，额外区分 Repository ORM/direct SQL primitive 只允许在 `position_projection_repository.py`，Repository mutation API 的 production 调用只允许来自 `position_projection_service.py`，不实现通用 call graph。

该 architecture test 同时包含 detector synthetic unit cases（直接字段赋值、Repository mutation、ORM update）和 repository-wide corpus check：`manual-picking/scan_flow.py` direct mutation 是迁移前可检出、迁移后 corpus 通过、detector 规则仍可检出的基准样本。QUALITY 负责 architecture guardrail、typed contract、result/exception classification；PostgreSQL integration/HEAVY 负责 Tx1/Tx2 durable boundary、crash/replay、CAS/concurrency；E2E/topology 负责 Beat/queue/worker wiring。不新增 architecture-testing framework、registry 或 policy engine。

mutation authority 另设小型 FAST truth-table，使用参数化 fake/stub persistence port，只模拟 authoritative fact lookup、current execution lookup、current provenance lookup、guarded mutation success/no-op/已知 transient failure；不模拟 PostgreSQL isolation、row lock、deadlock 或真实 CAS 竞争。矩阵至少覆盖：valid+ownership match→`APPLIED`；same provenance→`ALREADY_APPLIED`；causally newer projection→`SUPERSEDED`；historical A/current B→`STALE_SUPPRESSED`；allowlisted transient DB/lock/serialization failure→`PositionProjectionRetryableError`；authoritative identity/fact contradiction→`PositionProjectionInvariantViolation`；programming/unknown exception原样向上；普通 constraint/schema error 默认不包装。必须成对覆盖“权威 fact 属于 A、current 为 B”与“权威说 T1 属于 A、typed request 声称 B”，“incoming source=current source”与“incoming source 因果更旧”。

safety-kernel 判定顺序固定为：identity/contract validation → current ownership → idempotency/already-applied → causal supersession → atomic guarded apply。每个 case 同时断言 side effect：`APPLIED` 恰好一次 guarded mutation；`ALREADY_APPLIED`、`SUPERSEDED`、`STALE_SUPPRESSED`、`PositionProjectionInvariantViolation` 均零 mutation write，防止先写后判定的假通过。retryable exception 采用显式 allowlist 参数化；新增可转换 transient 类型必须同时增加 contract/test case，禁止 `except Exception` 或宽泛 DB exception 作为 retry 入口。FAST 不持有 ECS/RCS submit capability；projection authority 与 physical command submission 结构隔离。FAST 只证明 classification/contract/zero-or-one side effect，D12 PostgreSQL tests 证明真实 commit/locking/CAS/crash-replay。

D22 另增加 causal comparator truth-table：同一 source/version→`SAME`；同一 TransportTask 较低/较高 `outcome_version`→`BEFORE`/`AFTER`；同一 drain history contract 内有权威关系→对应 `BEFORE`/`AFTER`；跨 task 且无权威关系→`INCOMPARABLE`。每个 case 断言 ownership、idempotency、causal ordering 分开计算；`INCOMPARABLE` 必须 zero-write、抛出 `PositionProjectionInvariantViolation` 并记录 observability。PostgreSQL 乱序测试覆盖同一 execution 的 T1→P1、T2→P2，T2 先 commit 后 T1 reload 得到 `SUPERSEDED` 且 projection 保持 P2，并覆盖两个 worker 并发 T1/T2 时最终状态与 interleaving 无关。实现前必须盘点 PickingTask plan revision、Binding/step/resource-fence、drain history 和 multi-member source；只有证明每个可能竞争同一 projection 的 source pair 可比较后，才能冻结该 domain 的 A，否则按最小 fencing metadata 设计单独升级，不用时间戳猜顺序。

manual-picking/plugin 另增加 fact-driven outcome consumer 回归测试，冻结边界：`Historical business outcomes must be interpreted from their own task-scoped facts; current runtime projection must not be used to reinterpret another task's historical outcome.` 这不是“插件禁止读取 projection”：`scan_flow`、`rack_readiness`、workstation admission 等回答 current runtime question 时仍可读取 current `PositionProjection`；但 historical Task A/outcome 问题必须使用 Task A 的 Transport fact、Binding、Evidence、outcome。

核心 adversarial 场景为：Task A/Rack A fact 已完成，A 的 Tx2 projection 尚未应用或 `STALE_SUPPRESSED`；execution 已切换到 Task B/Rack B；current projection 明确属于 B，并故意设置为与 A 所需条件冲突（不同 rack、`position_unknown=true` 或 readiness 相反）；消费 A outcome 后，A 必须仅凭自身 task-scoped facts 正确收口，不能被 B projection 阻塞、推进或重新解释，B projection/runtime state 必须完全不变。还要覆盖 A projection 缺失/尚未同步但 A fact 完整收口的情形；若某业务语义确实需要 current runtime，必须在合同中标明那是 current-task decision，不得隐式混入 historical outcome consumption。

实现前逐点分类 `scan_flow`、`rack_readiness`、`completion_repository`、picking task/step advancement、resource/rack admission 的 projection 读取：current runtime question 保留，historical task/outcome question 改为 task-scoped fact。特别检查 completion/step advancement 是否通过当前 rack/workstation projection 判定 Task A；若是，必须迁移。测试不仅断言 A 最终状态，还要断言 historical consumer 未调用不该调用的 current-projection query，或通过冲突 projection 证明该 query 不参与历史判定。该 D15 测试不重复 D12 crash/resilience，不启动真实 Celery/RCS，优先放在 manual-picking plugin FAST/integration boundary owner。

## 10. 依赖与实施顺序

```text
#1 基础模型与状态机
 ├─> #2 claim/lease dispatcher
 ├─> #3 WMS / Transport / DeviceCommand 接入
 └─> #4 DATA_CONFLICT 与批次隔离
       └─> #5 task 隔离与 position_unknown 消费者收敛
             └─> #6 手工拣选 completion/drain
                   └─> #7 KT16 联调验收
```

基础能力先完成并独立测试；业务插件最后接入，禁止用插件测试证明基础 dispatcher 正确。

## 11. 文件与交付边界

| File | Change |
|---|---|
| `src/app/execution/services/wms_confirmation_service.py` | 状态机、claim/lease、deadline 重算 |
| `src/app/execution/models/wms_confirmation.py` | retry/lease 字段 |
| `src/app/transport/models.py` | Transport retry/lease 字段 |
| `src/app/device/models/command.py` | Device retry/lease 字段 |
| `src/celery_app/tasks/wms_confirmation.py` | due dispatcher |
| `src/celery_app/tasks/transport.py` | due dispatcher |
| `src/celery_app/tasks/device_command.py` | due dispatcher |
| `src/app/execution/repositories/transport_decision_binding_repository.py` | task-scoped 查询 |
| `src/app/execution/services/position_projection_service.py` | current execution ownership/invariant guard；禁止 stale callback 改写当前 projection |
| `src/app/transport/contracts.py` | 复用/组合现有 immutable Transport identity、authority、outcome/value objects，不复制 projection identity |
| `src/app/transport/service.py` | immutable fact convergence 与 runtime mutation 分离；stale callback 阻断指标/日志 |
| `src/app/transport/repository.py` | 基于 Transport fact/provenance 与 current projection 的有界 derived recovery scan；不只扫描 PENDING Evidence |
| `src/app/transport/contracts.py` / `src/app/wms_adapter/transport_adapter.py` | 冻结 provider submit/ACK 与 same-identity recovery capability；未有合同证据不得默认自动 resend |
| `src/app/device/contracts.py` / `src/app/device/ecs_adapter.py` / `src/app/device/services/device_dispatch_service.py` | 盘点 ECS duplicate submit/identity query 真实语义；区分 pre-ACK ambiguous 与 post-ACK reconciliation |
| `src/app/workline/repositories/workline_repository.py` | 复用 WorkLine 行作为 picking/drain 共同 fencing root；冻结统一锁序与短事务边界 |
| `src/app/wms_integration/outbound_picking/services/picking_task_plan_delta.py` | Task→EXECUTING transition 先竞争 WorkLine root，再锁 task/plan facts |
| `src/app/wms_integration/outbound_picking/services/picking_task_issued.py` / `picking_task_cancel.py` | 盘点 issued/cancel 的 task/advisory lock 与 WorkLine root 顺序，消除 authority transition 的反向锁序 |
| `src/app/wms_integration/return_buffer_drain/owner.py` / `src/app/wms_integration/return_buffer_drain/service.py` | drain Confirmation/Evidence authority transition 复用 WorkLine root，不新增 drain execution entity |
| `workline_plugins/manual-picking/src/manual_picking/application/drain_repository.py` / `drain_flow.py` | 复用 history/current/transport 的 drain authority facts 与 resource-fence，遵守共同锁序 |
| `workline_plugins/manual-picking/src/manual_picking/application/transport_outcome.py` | outcome identity resolution 后仅调用受 guard 保护的 runtime mutation |
| `src/app/workline/plugin_routing.py` | outcome publication 只依赖 task-scoped fact，不以 current projection 作为前置条件 |
| `workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py` | 当前 operation 判定与历史 outcome consumer 隔离 |
| `workline_plugins/manual-picking/src/manual_picking/application/rack_readiness.py` | 当前 rack readiness 仅服务当前 task，不重新解释历史 outcome |
| `workline_plugins/manual-picking/src/manual_picking/application/completion_repository.py` | completion 判定使用当前 task facts，禁止读取历史 callback 的 current projection 解释 |
| `src/app/wms_integration/outbound_picking/services/picking_task_prepare_batch.py` | 单条异常隔离 |
| `workline_plugins/manual-picking/src/manual_picking/application/batch_repository.py` | 移除物理资源门禁 |
| `workline_plugins/manual-picking/src/manual_picking/application/drain_repository.py` | 错误日志/指标与单条隔离 |
| `workline_plugins/manual-picking/src/manual_picking/application/completion_repository.py` | 当前 task 完成判定 |
| `workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py` | 迁移直接 PositionProjection mutation 到 single mutation authority |
| `tests/architecture/test_position_projection_mutation_authority.py` | production projection write 残留扫描与白名单 guardrail |
| `src/app/workline/services/workline_configuration_service.py` | reset/resume 语义 |
| `docs/architecture/device-command-contract.md` | 顶层超时合同 |
| `docs/architecture/SRS.md` | WES/RCS/ECS 所有权合同 |
| `docs/architecture/heavy-test-impact.toml` | 运行时模块 HEAVY mapping |
| `alembic/versions/<new_revision>.py` | 仅当 mapping 出现经证明必要的 `new` 字段、索引或约束时生成 migration；当前不预设统一 recovery 字段，也不新增 attention 表 |

不得新增兼容 wrapper、旧字段双写、旧数据迁移脚本或平行业务路径。schema 变更必须提供 migration，并在干净临时库和联调数据库分别验证 upgrade、真实查询、worker 启动及回滚边界；不得把清库重建当作联调部署替代。修改生产符号前先做 GitNexus upstream impact；提交前执行 staged detect-changes、对应 FAST、QUALITY 和 selector 选中的 HEAVY。

## 12. 回滚

未发布系统不做业务旧数据迁移，但 schema 仍通过受控 migration 部署；代码回滚不自动回滚已写入的 recovery 字段，发布前必须明确兼容窗口和数据库回滚边界。开发库可清理重建，联调库不得以清库替代 migration。联调中如需暂停恢复，只停止 due-record 领取，不删除请求、Evidence、Transport、DeviceCommand 或 Binding；已发送且 ACK 成功的请求继续等待原 callback，ACK 未成功的请求恢复后由 lease 过期和 `next_retry_at` 重新领取。

## 13. Out of Scope

- 修改 WMS、RCS、ECS 内部控制逻辑或供应商协议。
- WES 实现物理资源调度、占用判断或重复搬运保护。
- 创建空壳 PickingTask 绕过历史状态。
- 前端监控页面；本期提供 Service/API 可查询数据和告警。
- `wes_biz.reconciliation_attentions` 或其它独立人工异常工单表；当前只使用事实表、metrics、logs 和 alerting。
- 旧版本兼容、旧数据迁移和兼容别名。

## 历史评审阶段边界（已过期）

原评审阶段不直接实现代码、migration、provider 合同变更、Celery/Beat wiring、PostgreSQL query-plan 调优或联调部署；当前已进入实施与验证阶段，不再以该段作为完成状态。仍保留的范围限制是：除本 SPEC 已冻结的 recovery contract 外，不新增 attention/case 实体、projection retry 状态机、通用 command framework 或第二套 execution identity model。

## What already exists

- 事实链已有 `InboundEvidence`、TransportTask/member、TransportDecisionBinding、WMS Confirmation/Evidence、`PositionProjection.source_transport_task_id/source_operation_id` 及现有 Celery queue/Beat 拓扑。
- `PositionProjectionService`、PositionProjection Repository、WorkLine row lock、drain history/current/transport 查询、Transport outcome publication 和 manual-picking current-runtime 查询均是现有实现入口；本 SPEC 只要求收敛其权限边界与 recovery discovery，不复制第二套持久化事实。
- 现有数据库 `InboundEvidence.apply_status = APPLIED`、Transport lifecycle 字面量和既有 attempt/lease 字段继续保留；新增字段、索引与约束只有在实现阶段以 mapping、真实 query plan 或 atomic fencing 证据证明不可推导时才允许进入 migration。

## Implementation Tasks

当前状态汇总：

| Task | 状态 | 已有证据 | 剩余闭合条件 |
|---|---|---|---|
| T1 | PARTIAL | mutation authority guardrail、生产残留扫描、GitNexus impact 与 QUALITY | 补齐 Evidence/FACT/projection 日志与 metrics 术语审计 |
| T2 | PARTIAL | prepare、plan activation、plan delta、cancel、deactivate/full drain、drain owner、ScanFlow、ACK writeback、position/result callback、final/ACK replay 遵守 root-first；Confirmation pre-dispatch/response-save 均为 `WorkLine -> PickingTask/drain fact -> Confirmation`；historical Drain A/current Drain B 三路径 zero-write；PostgreSQL lock-order/concurrency/missing-row/evidence tests 通过 | 补 AC36 的 picking/drain authority switch 胜出后旧 Tx2 只能 deterministic stale/no-write 的双会话 production 证明，并冻结静态 transition owner 清单 |
| T3 | VERIFIED FOR TRANSPORT-DERIVED PAIRS | causal token/provenance migration、FAST comparator `34 passed`、隔离 PostgreSQL domain ordering `1 passed`，覆盖 drain/BIN/multi-member；不可比 zero-write/fail-closed | 若新增非 Transport-derived authority domain，必须先冻结其 provenance/order contract |
| T4 | BLOCKED-EXTERNAL | phase fence、typed capability、默认 `NO_SAFE_AUTOMATIC_RECOVERY` | 真实 ECS/RCS/WMS duplicate-submit 与 identity-query 合同或供应商实验 |
| T5 | VERIFIED | picking/taskless-drain `FINAL_RESULT` / `ACK_INVALIDATION` 独立 bounded branch、Tx2 current-drain revalidation、Drain ABA、混合 backlog stable ordering、zero-submit 与 production SQL EXPLAIN | 新 authority domain 必须扩展 typed Evidence/Confirmation join，不得解析插件 step/correlation |
| T6 | VERIFIED | FAST、QUALITY、migration、Tx1/Tx2、production query-plan、manual-picking PostgreSQL/Celery `401 passed`、per-candidate continuation、commit-unknown、worker interruption、oldest-first tests；六个低基数字段均有 production 更新源并保存于 Redis Hash，Redis 不可用时降级为进程内 snapshot | 新 outcome/branch 必须同步扩展 Redis metrics 和 fault-injection owner |
| T7 | PARTIAL | selector mapping、QUALITY 与 staged HEAVY 当前快照通过 | AC1-AC42 唯一测试 owner 清单、外部阻塞解除、现场重验 |

- [ ] **T1 — PARTIAL (P1, human: ~1 day / CC: ~20 min)** — 事实与调用点清单 — 完成 `TransportService`、`PositionProjectionService`、`invalidate_transport_member()`、`_invalidate_other_task_positions()`、manual-picking scan/handoff、所有 PositionProjection Repository/SQL mutation 的 upstream impact 和残留扫描；确认 Evidence processing、`FACT_COMMITTED`、projection outcome 的日志/metrics 命名边界。
  - Surfaced by: Code Quality / D27 terminology audit and D7 single mutation authority
  - Files: `src/app/transport/service.py`, `src/app/execution/services/position_projection_service.py`, `workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py`, `tests/architecture/test_position_projection_mutation_authority.py`
  - Verify: GitNexus upstream impact + production runtime residual scan + QUALITY architecture test
- [ ] **T2 — PARTIAL (P1, human: ~2 days / CC: ~30 min)** — 统一 authority fencing — 盘点所有 picking/drain authority transition，证明其与 Tx2 竞争同一 WorkLine 或 domain-specific fencing root，并统一 `authority root → Binding/resource-fence → PositionProjection` 锁序；未证明的路径不得宣称 D21 correctness。
  - Surfaced by: Architecture / D21 fencing-root review
  - Files: `src/app/workline/repositories/workline_repository.py`, `src/app/wms_integration/outbound_picking/services/`, `src/app/wms_integration/return_buffer_drain/`, `workline_plugins/manual-picking/src/manual_picking/application/drain_flow.py`
  - Verify: two-session PostgreSQL authority-switch/projection-mutation concurrency tests, including missing-row insert race
- [x] **T3 — VERIFIED FOR TRANSPORT-DERIVED PAIRS** — 证明 causal comparator domain — 已逐对象验证当前 Transport-derived projection identity 的 picking、drain、BIN 与 multi-member source pair；合法但不可比较的 source fail closed 且 zero-write。新增非 Transport-derived authority domain 时必须重新冻结 ordering contract。
  - Surfaced by: Architecture / D22 causal-order review
  - Files: `src/app/execution/services/position_projection_service.py`, `src/app/transport/contracts.py`, `workline_plugins/manual-picking/src/manual_picking/application/transport_outcome.py`
  - Verify: FAST comparator truth-table plus PostgreSQL same-execution out-of-order replay
- [ ] **T4 — BLOCKED-EXTERNAL (P1, human: ~1 day / CC: ~15 min)** — provider capability与Transport phase predicate — 从真实 ECS/RCS/WMS adapter/provider 合同确认 duplicate submit 与 identity query 语义；实现 `PENDING`/`SUBMIT_DELIVERY_UNKNOWN` 与 post-ACK `TRANSPORT_DELIVERY_UNKNOWN` 分离，并在 candidate query 与 adapter call 前双重关闭 ACK-success resend path。
  - Surfaced by: Architecture / D23–D24 ACK fence and provider capability review
  - Files: `src/app/transport/service.py`, `src/app/wms_adapter/transport_adapter.py`, `src/app/device/ecs_adapter.py`, `src/app/device/services/device_dispatch_service.py`
  - Verify: provider-contract FAST/integration matrix with fail-fast physical-submit sentinel
- [x] **T5 — VERIFIED** — 独立 projection branches — `ACK_INVALIDATION` 和 `FINAL_RESULT` 使用独立 bounded set-based discovery、独立 Tx2 replay 与 current-authority/ABA guard，并进入统一 `PositionProjectionService`；replay 不触发 physical submit。
  - Surfaced by: Architecture / D26–D27 projection-gap recovery review
  - Files: `src/app/transport/repository.py`, `src/app/execution/services/position_projection_service.py`, `src/app/transport/service.py`, `tests/integration/`
  - Verify: fresh-session Tx1/Tx2 crash-replay tests; Evidence `APPLIED` with both final-result and ACK-invalidation gaps remains discoverable
- [x] **T6 — VERIFIED** — 测试、query-plan 与 recovery observability 门禁 — FAST/QUALITY、真实 PostgreSQL Tx1/Tx2 crash/replay/candidate SQL、production `EXPLAIN (ANALYZE, BUFFERS)`、per-candidate continuation、commit-outcome-unknown、oldest-first 和 Redis-backed 六项低基数 metrics 已闭合。
  - Surfaced by: Test / Performance review and D12, D14, D15, D16, D19, D25 acceptance criteria
  - Files: `tests/architecture/`, `tests/integration/`, `workline_plugins/manual-picking/tests/`, `docs/architecture/heavy-test-impact.toml`
  - Verify: `uv run pytest <focused tests> -q`, PostgreSQL plan artifacts, selector-driven HEAVY and migration validation
- [ ] **T7 — PARTIAL (P2, human: ~30 min / CC: ~5 min)** — 闭合交付验证 — 更新 HEAVY mapping，运行 doc/spec validator、聚焦 FAST/QUALITY、selector 选中的 HEAVY 与 migration 验证；确认 AC1–AC42 映射到唯一测试 owner，再进入实现 review。
  - Surfaced by: Review completion gate
  - Files: `docs/architecture/heavy-test-impact.toml`, `docs/specs/2026-09-19-reliable-recovery-task-isolation.md`
  - Verify: `git diff --check`, focused QUALITY, staged HEAVY selector and migration checks

## 14. Effort Estimate

基础模型与状态机 1.5 天；dispatcher 2 天；三类可靠对象接入 2 天；migration/批次隔离 1 天；task/position 消费者收敛 1.5 天；手工拣选 completion/drain 1 天；测试和联调 2 天，总计约 11 天。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---:|---|---|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run in this phase | no CEO gate requested |
| Outside Review | prior `codex-plan-review` / Claude Code | Independent second opinion | 1 | completed | findings incorporated through D27 |
| Eng Review | `/plan-eng-review` + 2026-09-21 field acceptance review | Architecture, data flow, failure modes and tests | 2 | implementation partially verified | T3/T5/T6 verified；T1/T2/T7 partial；T4 external-blocked |
| Design Review | — | UI/UX gaps | 0 | skipped | backend SPEC only |
| DX Review | — | Developer experience gaps | 0 | skipped | not in scope |

- **OUTSIDE COVERAGE:** Claude Code, plan-review phase, completed in the prior review pass; its findings were incorporated into D21–D27. No new external CLI pass was run after the documentation-only terminology edit.
- **VERDICT:** IMPLEMENTATION PARTIALLY VERIFIED — current selected gates are green, but the SPEC is not complete and is not onsite verified.
- **UNRESOLVED DECISIONS:**
  - WorkLine remains the fencing-root candidate; every picking/drain authority transition must acquire the same root and lock order before D21 can be marked complete.
  - Same-projection source pairs without authoritative `SAME/BEFORE/AFTER` ordering must remain `INCOMPARABLE` and fail closed until domain evidence or minimal metadata exists.
  - Provider duplicate-submit/query semantics and invalidation-applied provenance remain implementation evidence gates, not assumptions.
