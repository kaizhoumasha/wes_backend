# DeviceCommand 运维诊断 Runbook

> 当前状态：Phase 7 核心基础能力诊断入口。本 Runbook 只读取配置、日志和 PostgreSQL 事实；不直接改表、伪造 CALLBACK、释放设备槽位或换 `command_code` 重放。

适用对象位于 `wes_biz` schema：`device_commands`、`device_status_observations`、`device_evidences`、
`device_evidence_conflicts`、`line_run_epochs` 和 `line_run_epoch_device_bindings`。供应商私有协议、PLC 互锁、现场机械安全和
业务 Decision 不属于本 Runbook；发现这类问题应分别交给 ECS/PLC、供应商一致性或 Phase 8/9 插件 owner。

## 启动配置与 worker

API 和 Celery 子进程启动时必须同时取得以下配置，缺失或格式错误会失败关闭：

| 配置 | 约束 |
| --- | --- |
| `ECS_BASE_URL` | 局域网 HTTP origin；不得含凭据、路径、query 或 fragment |
| `ECS_CONNECT_TIMEOUT_SECONDS` | 大于 0 |
| `ECS_READ_TIMEOUT_SECONDS` | 大于 0 |
| `DEVICE_COMMAND_QUEUE` | 固定为 `device-command` |

`device-command` worker 必须消费三个固定任务：

- `src.celery_app.tasks.device_command.dispatch_device_commands_batch`
- `src.celery_app.tasks.device_command.process_device_evidence_batch`
- `src.celery_app.tasks.device_command.reconcile_device_commands_batch`

Beat 只发送固定上限 100 的数据库扫描任务，不携带命令或 evidence 快照。零设备绑定是合法安装态；它不表示已完成供应商或现场验收。

## 诊断顺序

1. 记录 `command_code`、`device_code`、`source_event_id`、`trace_id` 和时间窗口；不得记录完整 Payload、凭据或 claim token。
2. 查 `device_commands`，确认命令状态、deadline、claim、失败码和对账原因。
3. 查同一命令的状态观察与 evidence；ACK 只表示接纳，只有匹配的 RESULT evidence 可以形成物理终态。
4. 查命令冻结的 `LineRunEpoch` 和设备合同绑定，确认当前证据没有跨 Epoch 或合同版本。
5. 只有数据库事实与 ECS/现场事实一致时才关闭问题。不得根据“worker 已执行”推测设备已完成。

以下示例在只读 `psql` 会话执行：

```text
psql "$READ_ONLY_DATABASE_URL"
```

## 未闭合命令与设备槽位

`PENDING`、`DISPATCHING`、`ACKNOWLEDGED` 和 `RECONCILING` 都占用设备槽位。同一 `device_code` 不得出现第二条未闭合命令。

```sql
SELECT
    command_code,
    device_code,
    status,
    execution_ref_type,
    execution_ref_id,
    deadline_at,
    claim_expires_at,
    failure_code,
    reconciliation_reason,
    now() AT TIME ZONE 'UTC' - updated_at AS age
FROM wes_biz.device_commands
WHERE status IN ('PENDING', 'DISPATCHING', 'ACKNOWLEDGED', 'RECONCILING')
ORDER BY updated_at ASC, id ASC
LIMIT 100;
```

处理原则：

- `PENDING`：核对活动 Epoch、设备绑定和下一次准入时间；不要直接触发 HTTP。
- `DISPATCHING` 且 claim 过期：delivery 可能未知，只能交给对账扫描，不能换 identity 重发。
- `ACKNOWLEDGED` 且 deadline 过期：等待匹配 CALLBACK 或权威现场证据；ACK 不能当成功。
- `RECONCILING`：保留设备槽位，核对 `reconciliation_reason`；不得直接改为失败或成功。

## 状态准入失败

每次实际派发准入都会保存不可变状态观察：

```sql
\set command_code '待诊断的 command_code'

SELECT
    device_code,
    contract_key,
    contract_version,
    mode,
    status,
    current_command_code,
    device_timestamp,
    received_at
FROM wes_biz.device_status_observations
WHERE command_code = :'command_code'
ORDER BY received_at DESC, id DESC;
```

只有新鲜的 `AUTO + IDLE`、无活动设备命令，且合同身份匹配活动 Epoch 时才可发送。供应商状态字段转换错误应在 ECS/网关修复，
不得在 WES 增加供应商别名或 fallback。

## Evidence、重复与冲突

```sql
SELECT
    source_event_id,
    kind,
    command_code,
    device_code,
    contract_key,
    contract_version,
    line_run_epoch_id,
    apply_status,
    received_at,
    processed_at
FROM wes_biz.device_evidences
WHERE command_code = :'command_code'
ORDER BY received_at ASC, id ASC;
```

`PENDING` 表示已持久化待应用；`APPLIED` 表示已按当前权威边界处理；`IGNORED` 表示不推进对象；`RECONCILING` 表示证据存在但
无法安全闭合。重复 `source_event_id` 应复用首次接收结果；同一 identity 对应不同摘要会写入冲突表：

```sql
SELECT
    source_event_id,
    first_evidence_id,
    reason_code,
    received_at
FROM wes_biz.device_evidence_conflicts
WHERE source_event_id = :'source_event_id'
ORDER BY received_at ASC, id ASC;
```

冲突 evidence 只用于审计和人工判定，不得覆盖首次证据，也不得推进业务对象。

## Epoch fencing

```sql
SELECT
    e.epoch_code,
    e.status AS epoch_status,
    e.started_at,
    e.closed_at,
    b.device_code,
    b.contract_key,
    b.contract_version,
    b.status_max_age_ms,
    b.command_timeout_ms
FROM wes_biz.line_run_epochs AS e
JOIN wes_biz.line_run_epoch_device_bindings AS b
  ON b.line_run_epoch_id = e.id
WHERE e.id = (
    SELECT line_run_epoch_id
    FROM wes_biz.device_commands
    WHERE command_code = :'command_code'
);
```

旧 Epoch 或合同不匹配 evidence 不得绑定当前运行代际。若真实现场合同已经变化，应先关闭旧 Epoch 并按获批配置建立新 Epoch；
不能原地改绑定或让核心猜测供应商版本。

## 升级与回归检查

```bash
uv run alembic upgrade head
uv run pytest tests/runtime/device_command tests/contracts/device tests/api/test_device_ecs_callbacks.py -q
uv run pytest tests/deployment/test_device_command_startup.py -q
```

真实闭环验收必须另外提供隔离 PostgreSQL、Redis 与 `RUN_WORKLINE_INTEGRATION=1`，并实际运行
`tests/e2e/device_command/test_device_command_production_wiring.py`。环境缺失导致的 skip 不算通过。
