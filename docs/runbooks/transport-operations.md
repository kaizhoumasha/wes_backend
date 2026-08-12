# Transport 运维诊断 Runbook

> 当前状态：Phase 6 基础能力诊断入口。本 Runbook 只读取日志和 PostgreSQL 事实，不执行状态修改、资源释放或重提。

适用对象是 `wes_runtime` schema 中的 Transport 基础对象。查询应使用只读数据库账号，
时间统一按数据库 UTC 解释；不应为了让结果“消失”而直接改表。以下命令在 `psql` 中执行，
连接字符串由环境中的只读凭据提供：

```text
psql "$READ_ONLY_DATABASE_URL"
```

## 先日志，后数据库事实

1. 先按时间窗口定位结构化事件：`transport.task.reconciling`、
   `transport.submit.late_writeback`、`transport.submit.lease_replaced`、
   `transport.submit.batch_completed`、`transport.outcome.publish_failed` 或
   `transport.evidence.enqueue_failed`。
2. 优先记录日志中的 `transport_task_id`、`operation_id`、`reason` 和时间；不记录
   Payload、claim token 或认证信息。`transport.evidence.enqueue_failed` 没有任务身份时，以事件时间窗口
   查找最近的待处理 evidence，不从请求 Payload 猜测 owner。
3. 再查 `transport_tasks`，核对当前状态、`reason_code`、submit 身份、发送开始时间、
   deadline、claim 到期时间和 outcome 版本。
4. 按同一 `transport_task_id` 查 `transport_evidence`、`transport_members`、
   `transport_position_projections` 和 `transport_resource_bindings`，先确认权威 evidence，再判断
   claim、投影、outcome 和绑定是否与之一致。
5. 只有日志和数据库事实能相互解释时才关闭诊断。日志丢失不代表数据库事实丢失；
   Beat 消息过期也不代表任务、evidence 或 outcome 被删除。

## 未知任务

`RECONCILING` 表示当前只能确认本地事实尚未收敛，不表示可以自动换号重发。

```sql
SELECT
    transport_task_id,
    submit_operation_id,
    status,
    reason_code,
    now() AT TIME ZONE 'UTC' - updated_at AS age,
    send_started_at,
    result_deadline_at,
    submit_attempt_count,
    outcome_version,
    published_outcome_version
FROM wes_runtime.transport_tasks
WHERE status = 'RECONCILING'
ORDER BY updated_at ASC, id ASC
LIMIT 100;
```

对每一行再查同任务 evidence：

```sql
\set transport_task_id '从上一查询复制的 transport_task_id'

SELECT
    operation,
    operation_id,
    status,
    conflict_code,
    received_at,
    processed_at,
    now() AT TIME ZONE 'UTC' - received_at AS age
FROM wes_runtime.transport_evidence
WHERE transport_task_id = :'transport_task_id'
ORDER BY received_at ASC, id ASC;
```

先核对 `reason_code` 是 delivery unknown、result timeout、position unknown 还是 evidence conflict，
再等待或提供权威 evidence 给 Transport Service 收敛。不得把 `RECONCILING` 直接改成终态。

## 过期 claim

以下查询同时列出 submit、evidence 和 outcome 三类过期 claim。claim 过期只说明当前执行权已失效，
不能否定旧 worker 已发出的 HTTP，也不能覆盖后到的权威 ACK。

```sql
SELECT
    'SUBMIT' AS claim_kind,
    transport_task_id AS object_id,
    status,
    submit_claim_until AS claim_until,
    now() AT TIME ZONE 'UTC' - submit_claim_until AS overdue_age
FROM wes_runtime.transport_tasks
WHERE submit_claim_until < (now() AT TIME ZONE 'UTC')

UNION ALL

SELECT
    'EVIDENCE' AS claim_kind,
    operation || '/' || operation_id AS object_id,
    status,
    claim_until,
    now() AT TIME ZONE 'UTC' - claim_until AS overdue_age
FROM wes_runtime.transport_evidence
WHERE claim_until < (now() AT TIME ZONE 'UTC')

UNION ALL

SELECT
    'OUTCOME' AS claim_kind,
    transport_task_id AS object_id,
    status,
    outcome_claim_until AS claim_until,
    now() AT TIME ZONE 'UTC' - outcome_claim_until AS overdue_age
FROM wes_runtime.transport_tasks
WHERE outcome_claim_until < (now() AT TIME ZONE 'UTC')
ORDER BY claim_until ASC
LIMIT 100;
```

诊断 submit claim 时必须同时查 `send_started_at`、`submit_operation_id` 和 evidence。
完成收敛只能由下一轮有效 claim、Transport Service 的 fencing 和权威 ACK/evidence 裁决；
不得清空 token 来“解锁”。

## 待处理 evidence

```sql
SELECT
    operation,
    operation_id,
    transport_task_id,
    status,
    now() AT TIME ZONE 'UTC' - received_at AS age,
    received_at,
    claim_until,
    conflict_code
FROM wes_runtime.transport_evidence
WHERE status = 'PENDING'
ORDER BY received_at ASC, id ASC
LIMIT 100;
```

若同时出现 `transport.evidence.enqueue_failed`，先确认 evidence 已持久化，再观察 10 秒 Beat 扫描是否在下一可用周期取得它。
不得删除重复或冲突 evidence；它们是幂等与对账事实。

## 未发布 outcome

```sql
SELECT
    transport_task_id,
    status,
    reason_code,
    outcome_version,
    published_outcome_version,
    now() AT TIME ZONE 'UTC' - updated_at AS age,
    outcome_claim_until
FROM wes_runtime.transport_tasks
WHERE outcome_json IS NOT NULL
  AND outcome_version > published_outcome_version
ORDER BY updated_at ASC, id ASC
LIMIT 100;
```

Phase 6 不安装默认 publisher 或 outcome Beat；只有 Phase 8 的真实业务 owner 才能显式绑定 publisher。
因此本查询的结果是待业务 owner 处理的持久化事实，不是运维人员可以直接将
`published_outcome_version` 追平的授权。

## 未释放绑定

先列出所有 active 绑定及对应任务 age；`PENDING`、`ACCEPTED` 或 `RECONCILING` 可以是正常持有状态，
终态任务仍有 active 绑定才是需要升级的不一致事实。

```sql
SELECT
    binding.resource_type,
    binding.resource_id,
    binding.transport_task_id,
    task.status,
    task.reason_code,
    now() AT TIME ZONE 'UTC' - binding.created_at AS binding_age,
    CASE
        WHEN task.status IN ('REJECTED', 'SUCCEEDED', 'FAILED') THEN 'TERMINAL_BINDING_INCONSISTENT'
        ELSE 'ACTIVE_OWNER'
    END AS diagnosis
FROM wes_runtime.transport_resource_bindings AS binding
JOIN wes_runtime.transport_tasks AS task
  ON task.transport_task_id = binding.transport_task_id
WHERE binding.released_at IS NULL
ORDER BY binding.created_at ASC, binding.id ASC
LIMIT 100;
```

对终态不一致应保留任务、evidence、outcome 和绑定证据并升级。绑定只能由 Transport Service 在权威状态收敛时释放；
不得直接设置 `released_at`。

## 禁止的运维捷径

- 禁止对 Transport 表执行 `UPDATE`、`DELETE`、`TRUNCATE` 或手工修改 claim/outcome 版本。
- 禁止清空 claim token、直接改任务/evidence 状态或为了重试而删除冲突事实。
- 禁止直接修改 `released_at`、删除 active 绑定或把资源指向另一任务。
- 禁止为 delivery unknown、冲突或超时任务生成新 `operation_id` 或新 `client_request_id` 重提。
- 禁止跳过 Transport Service、fencing 和权威 evidence 直接将 UNKNOWN 标记为成功或失败。

如果现有 Service/evidence 路径不能收敛，保留只读证据并升级为代码或合同问题，不在数据库中制造第二条修复路径。
