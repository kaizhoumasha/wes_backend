# WORKLINE ECS_TEST 现场验收 Runbook

> 本 Runbook 以 PostgreSQL 持久事实、ECS 回调记录和现场物理观察为准；不得直接改表、伪造 CALLBACK、改写命令状态或
> 换 `command_code` 重放。规范依据：`docs/superpowers/specs/2026-09-24-workline-ecs-test-mode-design.md`；网络接口
> 依据：`docs/integration/third_party_integration_whitepaper.md`。

适用对象位于 `wes_biz` schema：`work_lines`、`device_commands`、`inbound_evidences`、`devices`。
与设备命令的通用诊断（未闭合命令、状态观察、Evidence 冲突）请先看
[device-command-operations.md](device-command-operations.md)；本文件只覆盖 `ECS_TEST` 特有的启停、
规则配置和验收记录。

## 1. 前置条件：设备附录证据缺口

`ECS_TEST` 只批准了统一网络传输接口（四个固定路径），流水线/粗分机说明书只用于选动作，不是验收依据。现场启用前必须
取得设备附录对以下三点的**书面确认**，缺一项就不能报告"现场连续运行验收通过"（可以继续本地/Mock 验证）：

| 待确认项 | 为什么必须确认 | 记录位置 |
| --- | --- | --- |
| 同一来源背靠背两次相同扫码，是否产生不同的规范化事件身份 | 统一 wire 没有独立事件 ID，身份由报文内容+毫秒 timestamp 派生；如果两次真实扫码恰好落在同一毫秒且内容相同，会被误判成一次重报，漏发一条命令 | 本节表格 + 现场验收记录第 6 节 |
| 同一事件重报，身份字段是否保持完全一致 | 精确重报要求返回原命令，不能生成第二个 `command_code` | 同上 |
| 目标设备的 `task_type`、`params` 二级字段及 Result 字段含义 | 白皮书不规定 `data`/`params` 二级字段，需按设备合同附录 | 同上 |

```text
待确认状态记录表（现场启用前填写）：

设备        | 事件身份唯一性确认 | 重报稳定性确认 | task_type/params 确认 | 供应商回执日期
------------|-------------------|---------------|----------------------|---------------
流水线      | [ ] 已确认 [ ] 待确认 | [ ] 已确认 [ ] 待确认 | [ ] 已确认 [ ] 待确认 | ____
粗分机      | [ ] 已确认 [ ] 待确认 | [ ] 已确认 [ ] 待确认 | [ ] 已确认 [ ] 待确认 | ____
```

取得证据的方法：让 ECS 在同一来源设备上背靠背发送两次内容相同的扫码，抓取两次 `POST /api/v1/callback/event` 的完整
报文（含 `timestamp` 到毫秒），比对 `source_event_id` 派生结果是否不同；再原样重发其中一次，确认 WES 只返回一次
`200 ACK` 且不新增命令（第 5 节给出具体查询）。

## 2. 启动配置示例

以下是**讨论示例，非设备合同**——`params` 的实际字段和目标设备能力仍以获批附录为准。

```json
{
  "run_mode": "ECS_TEST",
  "runtime_config_json": {
    "ecs_test_rules": [
      {
        "source_device_code": "STATION_SCAN1",
        "target_device_code": "STATION_SCAN1",
        "task_type": "MOVE_FORWARD",
        "params": {"source": {"location_id": "STATION_SCAN1", "location_type": "SCAN_PLATFORM"}}
      },
      {
        "source_device_code": "STATION_SCAN2",
        "target_device_code": "SORTER-01",
        "task_type": "PICK_AND_PUT",
        "params": {"source": {"location_id": "STATION_SCAN2"}, "target": {"location_id": "SORT-BIN-01"}}
      }
    ]
  }
}
```

第二条规则展示"来源与目标不同"和"粗分机固定 `source`/`target` 都在 `params` 内"两种约束。`source_device_code` 在
同一条 WorkLine 上不能重复；`task_type`/`params` 一旦 START 成功即被冻结，Event 的 `data` 不会覆盖它们。

## 3. 启用前：关闭旧业务义务

`ECS_TEST` 复用 WorkLine 通用停用检查。启用前先确认目标线处于停用状态，且没有未闭合的历史业务：

```sql
\set workline_id '待启用的 workline id'

SELECT count(*) FILTER (WHERE status IN ('PENDING','DISPATCHING','ACKNOWLEDGED','RECONCILING')) AS unclosed_commands,
       count(*) FILTER (WHERE apply_status IN ('PENDING','RECONCILING')) AS unclosed_evidence
FROM wes_biz.device_commands
FULL OUTER JOIN wes_biz.inbound_evidences USING (workline_id)
WHERE workline_id = :'workline_id';
```

两个计数都必须为 0；不为 0 时按 [device-command-operations.md](device-command-operations.md) 的"未闭合命令"一节处理，
不能靠切换 `run_mode` 绕过。WorkLine 保留停用前的插件草稿（`plugin_key` 不变），只有 `plugin_version`/`flow_mode` 在
`ECS_TEST` 启动时被清空；这是预期行为，不代表插件配置丢失，切回业务模式时正常 START 会重新冻结插件版本。

## 4. 连续真实事件观察

验收要求 ECS 现场持续产生真实事件，WES 不主动发送或重开物理动作。观察窗口至少覆盖：

- 单一来源、单一目标的基本链路（源→命令→ACK→Result）
- 多个来源指向同一目标（见第 7 节，核对 ECS 侧的排队/拒绝行为，不是 WES 侧限流）
- 现场设备的真实断线重连、命令超时场景（对应 `TIMED_OUT`/`RECONCILING`，见第 6 节）

**重复事件注入方法**：用同一来源设备，在观察窗口内人工触发一次已发生过的物理动作（例如同一个包裹再次经过同一
扫码点），或直接用抓包工具原样重放一次历史请求体（含毫秒 `timestamp`）。两种方式都应验证：WES 返回相同的
`200 ACK`，不新增 `DeviceCommand` 记录。

## 5. Event → Command → Result 关联查询

验收的核心可追溯链路是 `InboundEvidence.source_identity → DeviceCommand.execution_ref_id → command_code → Result Evidence`：

```sql
\set workline_id '待观察的 workline id'
\set window_start '观察窗口起始时间戳，如 2026-09-24 08:00:00'

SELECT
    e.source_identity AS event_identity,
    e.received_at AS event_received_at,
    e.apply_status AS event_apply_status,
    c.command_code,
    c.execution_ref_id,
    c.status AS command_status,
    c.created_at AS command_created_at,
    r.source_identity AS result_identity,
    r.apply_status AS result_apply_status,
    r.received_at AS result_received_at
FROM wes_biz.inbound_evidences AS e
LEFT JOIN wes_biz.device_commands AS c
    ON c.workline_id = e.workline_id
    AND c.execution_ref_type = 'ECS_TEST'
    AND c.execution_ref_id = e.source_identity
LEFT JOIN wes_biz.inbound_evidences AS r
    ON r.command_code = c.command_code
WHERE e.workline_id = :'workline_id'
    AND e.kind = 'DEVICE_EVENT'
    AND e.received_at >= :'window_start'
ORDER BY e.received_at ASC, e.id ASC;
```

核对要点：

- 每个新的、匹配规则的 `event_identity` 恰好关联一个 `command_code`；同一 `event_identity` 出现多行只能是"一次事件、
  一个命令、多条 Result 重报"，不能是"一次事件、多个命令"。
- 没有匹配规则的合法事件（`command_code` 为空）应有 `event_apply_status = IGNORED`，且没有进入业务 FactProcessor
  的迹象（不产生 `material_execution_id`、不产生 WMS Confirmation，见第 7 节的零新增核对）。
- `execution_ref_type = 'ECS_TEST'` 是内部标记，不出现在任何 ECS 网络 wire 上；只用于本查询和数据库诊断。

## 6. 失败/超时/对账分类

```sql
SELECT command_code, status, reconciliation_reason, failure_code, deadline_at, updated_at
FROM wes_biz.device_commands
WHERE workline_id = :'workline_id' AND execution_ref_type = 'ECS_TEST'
    AND status IN ('TIMED_OUT', 'RECONCILING', 'FAILED')
ORDER BY updated_at DESC;
```

区分两种"未知结果"，不要混为一谈：

- **`TIMED_OUT`**：命令在提交给 ECS 之前就到期（从未被 ECS 接纳），是明确的本地终态，不代表设备执行过。
- **`RECONCILING`**：命令已经提交给 ECS，之后结果未知（ACK 之后没等到 Result，或派发租约过期）。这类命令仍在等待
  迟到 Result，匹配到达时会按原 `command_code` 收敛为 `SUCCEEDED`/`FAILED`；不得因为"现在看起来设备空闲"就人工
  改写。

## 7. 多来源指向同一目标

多个来源可以配置指向同一目标设备；WES 不建立设备级占槽，由 ECS 自己判断容量、排队和物理互斥。验收时需要在目标
设备侧记录 ECS 的真实反应，这是**设备侧事实**，与上面的 WES 数据库事实是两回事：

```text
目标设备并发观察记录表：

时间 | 来源事件 A | 来源事件 B | 目标设备  | ECS ACK A | ECS ACK B | 排队/拒绝行为 | 最终 Result A | 最终 Result B
-----|-----------|-----------|----------|-----------|-----------|--------------|---------------|---------------
     |           |           |          |           |           |              |               |
```

若 ECS 不支持预期的并发接纳，应调整测试规则或事件触发频率，而不是让 WES 依据推测的设备容量隐式丢弃事件。

## 8. WMS 与插件业务零新增核对

`ECS_TEST` 运行期间，以下计数在观察窗口内必须为零：

```sql
SELECT
    (SELECT count(*) FROM wes_biz.picking_tasks WHERE workline_id = :'workline_id'
        AND created_at >= :'window_start') AS new_picking_tasks,
    (SELECT count(*) FROM wes_biz.wms_confirmations WHERE workline_id = :'workline_id'
        AND created_at >= :'window_start') AS new_wms_confirmations,
    (SELECT count(*) FROM wes_biz.material_executions WHERE workline_id = :'workline_id'
        AND created_at >= :'window_start') AS new_material_executions;
```

三个计数应均为 0。若不为 0，先确认这些记录是否属于窗口开始前已存在、尚未闭合的旧业务（旧身份应保持原有幂等语义，
不算违规）；确实是窗口内新建的，则是回归，需要停止验收并升级给开发团队，不要继续采集"现场观察"数据掩盖问题。

## 9. 公共回调合同：`is_debug=true` 冲突响应

活动 `ECS_TEST` 来源显式发送 `is_debug=true` 时的实际请求/响应示例（字段以已批准白皮书为准，非讨论示例）：

请求：

```json
POST /api/v1/callback/event
{
  "device_code": "STATION_SCAN1",
  "event_type": "SCAN_COMPLETED",
  "timestamp": 1790290000000,
  "is_debug": true,
  "data": {}
}
```

响应（`STATION_SCAN1` 是当前活动 `ECS_TEST` 线某条规则的 `source_device_code`）：

```json
{
  "code": 409,
  "message": "ECS_TEST_SOURCE_EXPLICIT_DEBUG"
}
```

省略 `is_debug` 或显式 `false` 的正常测试事件不受影响；这条例外只影响被声明为"来源"的设备，同线的目标设备仍走
原有 `EVENT_DEBUG` 语义。

## 10. 完整验收结论的成立条件

只有以下五点同时成立，才能报告"目标完整验收通过"；缺任何一点，只能报告对应层级已验证，不能外推到完整验收：

1. 第 1 节的设备附录证据已经全部取得书面确认（不是"本地测试通过所以应该没问题"）。
2. 观察窗口内所有新事件在第 5 节的关联查询里可区分、重报无重复物理动作。
3. 第 8 节三个计数在整个观察窗口内保持为零。
4. 观察窗口内所有命令的原身份 Result 都已按第 6 节的分类闭合（没有长期停留在 `RECONCILING`/`TIMED_OUT` 且未跟进的记录）。
5. 现场设备行为已被记录（尤其第 7 节的并发观察表），不是只有 WES 数据库记录。

本地 Mock 全绿或 HTTP 200 只能作为对应层级（软件实现/网络合同）各自的证据，不能替代 ECS 或物理设备的现场稳定性
验收结论。时长、事件频率和允许失败阈值由具体现场验收任务书给出，本 Runbook 不内建判定标准。

## 11. 升级与回归检查

```bash
uv run alembic upgrade head
uv run pytest tests/workline/test_workline_start_service.py tests/workline/test_workline_configuration_service.py -q
uv run pytest tests/runtime/device_command/test_evidence_service.py tests/runtime/device_command/test_device_command_service.py -q
uv run pytest tests/runtime/transport_debug/test_transport_debug_run_service.py tests/api/test_device_ecs_callbacks.py -q
```

真实 Postgres 集成核对（需要独立 Postgres 实例，指向隔离测试库，不能是生产/共享库）：

```bash
INTEGRATION_DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:5432/postgres \
  uv run pytest tests/integration/workline_capabilities/test_workline_start_postgresql.py -q

RUN_WORKLINE_INTEGRATION=1 INTEGRATION_DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:5432/<test_db> \
  uv run pytest tests/integration/wms_adapter/outbound_picking/test_issued_postgresql.py \
    tests/integration/transport_debug/test_transport_debug_auto_run.py -q
```

环境缺失导致的 skip 不算通过。
