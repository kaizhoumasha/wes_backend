# WORKLINE 诊断快速开始

> 状态：`implementation_baseline`。本指南只用于 Phase 5 零插件基线仍保留的诊断、replay 和 sandbox 查询 API 排障，
> 不得用于设计新 Runtime 或业务插件执行能力。

本指南用于在不查数据库、不 grep 日志的前提下，用 API 完成一条 WORKLINE 诊断链路：

```text
callback fixture -> trace_id / event_id -> blocking-point -> diagnostic card -> replay/sandbox 查询
```

## 前置条件

1. 后端、Postgres/TimescaleDB、Redis 已启动。
2. 数据库迁移已执行。
3. 当前用户或 API token 具备以下权限：
   - `api:callback:event`
   - `api:callback:result`
   - `biz:workline:list`
   - `biz:workline:update`
4. 已有可校验的设备和 WorkLine 配置。Phase 5 当前没有业务插件，本指南只验证入站、诊断和 replay 等通用能力；
   不把事件最终进入业务动作当作验收结果。sandbox 查询仅适用于数据库中已有的历史或测试 Outbox。

示例命令默认：

```bash
export WES_API=http://localhost:8001/api/v1
export WES_TOKEN=<your-token>
```

## 1. 发送设备事件

入口只接受 `trace_id`、`event_id`、`causation_id`。本系统未发布，不保留旧命名兼容层。

```bash
curl -sS -X POST "$WES_API/callback/event" \
  -H "Authorization: Bearer $WES_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "ARM01",
    "event_type": "SCAN_COMPLETED",
    "timestamp": 1777200000000,
    "trace_id": "trace-demo-0001",
    "event_id": "evt-scan-0001",
    "data": {
      "location": "ARM01",
      "HHPN": "620100L00-011-G",
      "MfrPN": "CC0402JRNPO9BN220",
      "Qty": "7387",
      "DateCode": "122625",
      "LotCode": "8904936031",
      "PkgID": "SVYU00125TP4LCR02_9"
    }
  }'
```

期望 ACK 至少包含：

```json
{
  "code": 200,
  "data": {
    "request_id": "...",
    "trace_id": "trace-demo-0001",
    "event_id": "evt-scan-0001",
    "causation_id": null
  }
}
```

## 2. 查询阻塞点诊断卡

现场排障优先使用 blocking-point API：

```bash
curl -sS "$WES_API/workline/trace/trace-demo-0001/blocking-point" \
  -H "Authorization: Bearer $WES_TOKEN"
```

响应契约：

```json
{
  "code": 200,
  "data": {
    "trace_id": "trace-demo-0001",
    "blocking_point": "OUTBOX",
    "owner": "integration",
    "recoverability": "AUTO_RETRYABLE",
    "operator_action": "检查目标配置、最近一次 attempt 错误和外部服务可用性后重试。",
    "diagnostic_card": {
      "error_code": "OUTBOX_DISPATCH_FAILED",
      "error_domain": "INTEGRATION",
      "severity": "ERROR",
      "problem_class": "RECOVERABLE",
      "next_steps": []
    },
    "evidence": {}
  }
}
```

## 3. 查询完整 Trace

按 trace 查询：

```bash
curl -sS "$WES_API/workline/trace/trace/trace-demo-0001" \
  -H "Authorization: Bearer $WES_TOKEN"
```

按设备命令恢复链路：

```bash
curl -sS "$WES_API/workline/trace/command/<command_code>" \
  -H "Authorization: Bearer $WES_TOKEN"
```

完整 Trace 响应包含：

- `callback_logs`
- `inboxes`
- `sessions`
- `commands`
- `outboxes`
- `dispatch_attempts`
- `timelines`
- `diagnostics`

## 4. 处理设备结果

`callback/result` 的恢复锚点是 `command_code`。即使供应商没有回传 `trace_id`，WES 也会先按 `command_code` 恢复链路，再诊断设备上下文是否匹配。

```bash
curl -sS -X POST "$WES_API/callback/result" \
  -H "Authorization: Bearer $WES_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "command_code": "<command_code>",
    "device_code": "ARM01",
    "result": "SUCCESS",
    "finish_time": 1777200060000,
    "trace_id": "trace-demo-0001",
    "event_id": "evt-result-0001",
    "causation_id": "evt-scan-0001",
    "data": {
      "current_location": "BUFFER-01",
      "handled_qty": 1
    }
  }'
```

## 5. SANDBOX 待处理 Outbox

SANDBOX 不向设备 payload 注入 `sandbox` 字段。调试人员通过操作 API 查看 SIMULATION 模式下等待处理的 outbox：

```bash
curl -sS "$WES_API/workline/operations/sandbox/pending?limit=20" \
  -H "Authorization: Bearer $WES_TOKEN"
```

响应中的 `payload_json` 是回灌 `callback/result` 的依据；不要把内部 `session_id`、`inbox_id`、`outbox_id` 暴露给真实设备。

## 6. Replay 历史 Inbox

Replay 创建新事件，不修改旧 inbox：

```bash
curl -sS -X POST "$WES_API/workline/operations/replay/inboxes/<inbox_id>" \
  -H "Authorization: Bearer $WES_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "replay-<inbox_id>-after-device-fix",
    "reason": "修复设备配置后重放"
  }'
```

## 7. 标准诊断码

| 诊断码 | 默认 owner | 默认恢复动作 |
| --- | --- | --- |
| `CALLBACK_SCHEMA_INVALID` | `integration` | 按 callback 协议修正顶层字段和 `data` 结构后重发。 |
| `SESSION_CONTEXT_MISSING` | `workflow` | 检查 inbox 与 session 归属字段；没有权威 owner 时停止重放并修复上游事件。 |
| `SESSION_RESOLVE_FAILED` | `workflow` | 检查 `business_key`、`trace_id`、设备绑定和 SessionResolver 规则。 |
| `CONTRACT_MISMATCH` | `integration` | 核对事件身份、设备或指令归属、协议字段与 WES 权威记录。 |
| `DEVICE_UNREACHABLE` | `device` | 检查设备电源、网络、host/port 和 `callback_path` 配置。 |
| `DEVICE_TIMEOUT` | `device` | 检查设备执行状态，必要时人工完成或重试命令。 |
| `OUTBOX_DISPATCH_FAILED` | `integration` | 检查目标配置、最近一次 attempt 错误和外部服务可用性后重试。 |
| `RESOURCE_WAIT` | `workflow` | 查看 `resource_kind`、`resource_key`、首次等待、最近等待和等待次数；释放对应资源后等待自动重试。 |
| `INBOX_RETRY_EXHAUSTED` | `workflow` | 查看诊断卡 evidence，修复根因后通过 replay 创建新事件。 |
| `CONFIG_INVALID` | `configuration` | 修正主数据配置并重新触发事件。 |
| `UNKNOWN` | `platform` | 补充 callback、inbox、timeline 和 outbox evidence 后重新诊断。 |

`RESOURCE_WAIT` 表示编排阶段已经知道某个 Station、rack/bin/cell 或外部资源暂时不可用。它是自动等待态，不是人工 Hold，也不是设备失败。现场排障应先释放或补齐 `resource_key` 指向的资源，再观察同一 Inbox 的重试结果。

Inbox `RESOURCE_WAIT` 与 Outbox `BLOCKED_RESOURCE` 都可以在 UI/Trace 中展示为资源等待，但写入边界不同：前者由 Runtime decision 表达“下一步资源暂不可用”，后者由设备派发前的实时 ECS `IDLE` probe 表达“目标设备暂忙”。不要把本地 DeviceStatus 投影当作 blocked outbox 放行事实。

`WORKLINE_ENTRY_ADMISSION_BLOCKED` 不再是新运行过程的正常诊断。看到该诊断时，按历史数据或旧版本残留处理，不作为当前工作线并发容量判断依据。

诊断码来源：`src/app/runtime/orchestration/diagnostics/registry.py`。

## 8. 合同规则

- 所有入口、表字段、运行时上下文和 API 响应统一使用 `trace_id`。
- `command_code` 是设备结果恢复的硬锚点，不依赖供应商回传 trace。
- 事实查询走 FastAPI trace/diagnostics/blocking-point API，SSE 只作为轻通知。
- 本分支基于 `develop` 创建轻量功能/修复分支；后续 QA/ship 仍基于 `develop`。
