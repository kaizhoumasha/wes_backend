# DeviceCommand 运维诊断 Runbook

> 本 Runbook 以配置、日志、受限对账 API 和 PostgreSQL 持久事实为准；不得直接改表、伪造 CALLBACK、释放设备槽位或换 `command_code` 重放。

适用对象位于 `wes_biz` schema：`device_commands`、`device_status_observations`、`inbound_evidences`、
`inbound_evidence_conflicts`、`device_event_command_blocks`、`work_lines`。供应商私有协议、PLC 互锁、现场机械安全和
业务 Decision 不属于本 Runbook；发现这类问题应分别交给 ECS/PLC、供应商一致性、Phase 8 `rough_sorter` 或 Phase 12/13 插件 owner。

## 启动配置与 worker

API 和 Celery 子进程启动时必须同时取得以下环境配置，缺失或格式错误会失败关闭：

| 配置 | 约束 |
| --- | --- |
| `ECS_CONNECT_TIMEOUT_SECONDS` | 大于 0 |
| `ECS_READ_TIMEOUT_SECONDS` | 大于 0 |
| `DEVICE_COMMAND_QUEUE` | 固定为 `device-command` |

`Device.endpoint_base_url` 不是进程启动配置，静态主数据允许为空。参与业务运行的必需角色 Device 必须在公开 START 前配置有效的
局域网 HTTP origin；START 校验并保存 WorkLine 当前设备合同；命令创建时冻结必要值，派发读取原命令的合同。

`device-command` worker 必须消费三个固定任务：

- `src.celery_app.tasks.device_command.dispatch_device_commands_batch`
- `src.celery_app.tasks.device_command.process_device_evidence_batch`
- `src.celery_app.tasks.device_command.reconcile_device_commands_batch`

Beat 只发送固定上限 100 的数据库扫描任务，不携带命令或 evidence 快照。零设备绑定是合法安装态；它不表示已完成供应商或现场验收。

## 诊断顺序

1. 记录 `command_code`、`device_code`、`source_identity`、`trace_id` 和时间窗口；不得记录完整 Payload、凭据或 claim token。
2. 查 `device_commands`，确认命令状态、deadline、claim、失败码和对账原因。
3. 查同一命令的状态观察与 evidence；ACK 只表示接纳，只有匹配的 RESULT evidence 可以形成物理终态。
4. 查原命令的 WorkLine、执行关联和设备合同，确认当前证据匹配原命令。
5. 只有数据库事实与 ECS/现场事实一致时才关闭问题。不得根据“worker 已执行”推测设备已完成。

`MANUAL_DEBUG` 不关联业务 WorkLine 绑定或执行对象；应改为核对命令冻结的 Endpoint、审计原因、创建人和两次
实时 Status 准入。超级用户 SSE 只展示连接期间的 best-effort callback 尝试与 evidence 更新，不提供历史回放，也不能替代数据库事实。

以下示例在只读 `psql` 会话执行：

```text
psql "$READ_ONLY_DATABASE_URL"
```

## 未闭合命令与设备槽位

同一 `device_code` 可以保存多条 `PENDING` 正式业务命令，但同时至多一条命令进入 `DISPATCHING`。
`ACKNOWLEDGED` 和 `RECONCILING` 仍保留物理设备围栏，后续 `PENDING` 命令不得越过；只有匹配的权威终态闭合原命令后才能继续领取。

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

- `PENDING`：业务命令按创建顺序等待领取，并核对所属 WorkLine 准入、原设备合同和下一次准入时间；`MANUAL_DEBUG` 核对冻结
  Endpoint 与审计字段；不要直接触发 HTTP。
- `DISPATCHING` 且 claim 过期：delivery 可能未知，只能交给对账扫描，不能换 identity 重发。
- `ACKNOWLEDGED` 且 deadline 过期：等待匹配 CALLBACK 或权威现场证据；ACK 不能当成功。
- `RECONCILING`：保留设备槽位，核对 `reconciliation_reason`；`RESULT_BEFORE_DISPATCH` 表示命令尚未下发就收到 RESULT，
  必须核对 ECS/WES 时序和现场事实，不得重报 callback、重新下发或直接改为失败/成功。

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

业务命令只有新鲜的 `AUTO + IDLE`、无活动设备命令，且命令所属 WorkLine 准入有效且合同匹配 时才可发送。`MANUAL_DEBUG` 不使用业务
binding 或状态新鲜度合同，但创建前和发送前都必须满足身份匹配、在线、`AUTO + IDLE`、无活动命令，且 `task_type` 位于非空
`supported_commands`。供应商状态字段转换错误应在 ECS/网关修复，不得在 WES 增加供应商别名或 fallback。

## Evidence、重复与冲突

```sql
SELECT
    source_identity,
    kind,
    command_code,
    device_code,
    contract_key,
    contract_version,
    workline_id,
    apply_status,
    normalized_payload ->> 'observation' AS observation,
    normalized_payload ->> 'reason_code' AS observation_reason_code,
    normalized_payload ->> 'observed_at' AS observed_at,
    received_at,
    processed_at
FROM wes_biz.inbound_evidences
WHERE command_code = :'command_code'
ORDER BY received_at ASC, id ASC;
```

`PENDING` 表示已持久化待应用；`APPLIED` 表示已按当前权威边界处理；`IGNORED` 表示不推进对象；`RECONCILING` 表示证据存在但
无法安全闭合。重复 `source_identity` 应复用首次接收结果；同一 identity 对应不同摘要会写入冲突表：

`DEVICE_OBSERVATION` 是 WES 内部诊断事实：`NOT_ACCEPTED` 表示请求未被 ECS 接纳，`RESULT_UNKNOWN` 表示接纳或物理结果仍未知。
它的 `APPLIED` 只表示观察已完成本地处理，不表示命令成功或物理完成；结果未知时必须保留原命令身份与设备围栏，等待匹配的权威终态。

```sql
SELECT
    source_identity,
    first_evidence_id,
    reason_code,
    received_at
FROM wes_biz.inbound_evidence_conflicts
WHERE source_identity = :'source_identity'
ORDER BY received_at ASC, id ASC;
```

冲突 evidence 只用于审计和人工判定，不得覆盖首次证据，也不得推进业务对象。

## EVENT 命令阻塞与恢复

`is_debug=true` 的 `DEVICE_EVENT` 创建新的 `PENDING EVENT_DEBUG` 命令并提交后，会立即唤醒既有 dispatch batch；唤醒失败只影响时延，Beat 每
10 秒扫描仍是补偿路径。重复事件、已存在命令、非 `PENDING` 结果、事务回滚或被旧命令占槽的事件均不得唤醒下发。

若设备已有 `PENDING`、`DISPATCHING`、`ACKNOWLEDGED` 或 `RECONCILING` 命令，该调试 EVENT 保持
`RECONCILING`，且不创建失败占位命令；阻塞因果记录在 `device_event_command_blocks`。超级用户应先调用：

```text
GET /api/v1/device/evidences/{source_event_id}/blocker
```

响应中的 `block_id` 和两个固定操作路径是不可变因果令牌。只有原命令为 `RECONCILING / DELIVERY_UNKNOWN`、原命令保存的执行合同可提供
Endpoint 与状态新鲜度合同、实时 ECS Status 证明同一设备 `AUTO + IDLE`，且没有已持久化 Result 时，才可按响应路径调用
`reconcile-device-idle`。该操作将原命令闭合为 `FAILED / MANUAL_RECONCILIATION_DEVICE_IDLE`，不伪造 Result 或物理成功。

原命令终态且设备没有其它未闭合命令后，可按响应路径调用 `reprocess`。`202` 只表示原 evidence 以相同
`source_event_id`、payload、digest、WorkLine 与合同身份重新进入 `PENDING`；它不表示新命令已创建、ECS 已接纳或物理动作完成。
不得手工改写 blocker/evidence，也不得绕过 `block_id` 重放。

## WorkLine 准入与命令关联

```sql
SELECT
    c.command_code,
    c.workline_id,
    c.device_code,
    c.contract_key,
    c.contract_version,
    w.line_code,
    w.is_active,
    w.version,
    w.plugin_key,
    w.plugin_version
FROM wes_biz.device_commands AS c
LEFT JOIN wes_biz.work_lines AS w ON w.id = c.workline_id
WHERE c.command_code = :'command_code';
```

命令可靠重试以原命令保存的目标和执行合同为准；WorkLine 当前配置不冒充历史命令配置。未闭合命令、待应用 Evidence、
Transport、WMS 义务或有效占用存在时，系统拒绝停用和插件切换。现场人员完成停料及物理清线、系统检查通过后才能修改配置。
违反准入的事件保存拒绝证据并报错，不将原业务交给新插件，也不新增清线确认记录。

## 升级与回归检查

```bash
uv run alembic upgrade head
uv run pytest tests/runtime/device_command tests/contracts/device tests/api/test_device_ecs_callbacks.py -q
uv run pytest tests/deployment/test_device_command_startup.py -q
```

真实闭环验收必须另外提供隔离 PostgreSQL、Redis 与 `RUN_WORKLINE_INTEGRATION=1`，并实际运行
`tests/e2e/device_command/test_device_command_production_wiring.py`。环境缺失导致的 skip 不算通过。
