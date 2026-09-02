# 运输接入诊断设计方案

**日期：** 2026-08-26

**状态：** 诊断功能及 Transport 0.3.0 请求字段已完成仓内对齐；`TRANSPORT_DEBUG` 联调当前位置投影已在当前分支实现。
该状态不代表已合并或正式发布，也不替代供应商一致性、现场物理动作或业务验收

**范围：** WES 后端、WES 前端，以及 WMS 回调进入 WES 后的可观测链路

## 1. 目标

在现有“设备接入诊断”的交互与 SSE 基础上，新增独立的“运输接入诊断”页面，用于联调并判断：

- WES 是否按合同创建并发送 `RACK_MOVE`、`RACK_ROTATE`、`BIN_MOVE`、`BIN_EXCHANGE`；
- WMS 是否接纳或拒绝 Transport 请求；
- WMS 是否通过 `POST /api/v1/wms/events` 回调结果；
- WES 是否持久化并应用回调证据，最终形成可查询的 Transport 结果；
- RCS、AGV、CTU 是否真实具备对应能力。

页面提供“证据入口”，但不把网络可达、HTTP ACK、WMS 接纳或 SSE 通知夸大为物理完成。物理动作仍须结合现场设备和 WMS/RCS 记录验收。

## 2. 已确认的产品决策

1. 页面路由为 `/ops/transport-diagnostics`，与“设备接入诊断”并列，不合并成通用诊断中心。
2. 四种能力必须全部可发起：`RACK_MOVE`、`RACK_ROTATE`、`BIN_MOVE`、`BIN_EXCHANGE`。
3. 不新增 Preflight。参数、资源映射、设备能力和物理可执行性由 WMS、RCS 或硬件侧处理；WES 继续执行现有 DTO、幂等、资源占用和状态约束。
4. 不新增周期轮询。页面使用“一次初始查询 + 共享 SSE 实时通知 + 用户点击后查询持久结果”。
5. 默认加载最近 20 条全部 `TransportTask`，支持继续加载，单次上限 100 条。
6. 不提供“全部/联调/业务”来源过滤，也不新增来源字段。允许按 `kind`、`status` 缩小列表，并可按精确 `transport_task_id` 查询。
7. WMS 回调仍统一进入 `/api/v1/wms/events`。不为 Transport 新增 ECS 风格的 callback-result 接口，也不让浏览器接收 WMS 回调。
8. SSE 只通知本次在线会话期间发生的回调接入和证据处理状态，不重放、不替代数据库、不承载完整结果。
9. 用户点击通知或任务行后，前端调用持久化查询接口查看规范化请求和结果。
10. `RECONCILING` 表示交付事实未知。页面必须保留原 `submit_operation_id + transport_task_id`，禁止提示重发或自动创建替代任务。

## 3. 当前基础

当前后端已经具备：

- `POST /api/v1/transport/debug-tasks`：创建四种 Transport 调试任务；
- `GET /api/v1/transport/tasks/{transport_task_id}`：查询单条任务状态和最新 evidence；
- `POST /api/v1/wms/events`：接收并持久化 WMS Transport 结果事件；
- `TransportTask.request_json`、`TransportTask.outcome_json`：保存规范化请求和终态结果；
- `EventStreamService`：基于 Redis Pub/Sub 的共享、在线态、best-effort SSE 基础设施。

当前前端已经具备：

- “设备接入诊断”的页面、确认对话框、SSE 状态和断线提示模式；
- Transport 调试创建和单条查询的生成 API；
- `ops:transport:debug-create`、`ops:transport-task:list`、`ops:transport-task:read`、
  `ops:transport-evidence:stream` 权限常量。

因此本功能只补齐 Transport 列表、详情投影、Transport 专用 SSE 事件和前端页面，不复制 Redis/SSE 基础设施，不改变 WMS Provider profile。

## 4. 总体数据流

```text
用户打开页面
  ├─ 连接 GET /api/v1/transport/evidences/stream
  └─ 查询 GET /api/v1/transport/tasks?limit=20

用户确认四类真实调试任务之一
  → POST /api/v1/transport/debug-tasks
  → WES 持久化 TransportTask
  → 既有异步发送链路调用 WMS
  → WMS/RCS/AGV/CTU 处理

WMS 回调
  → POST /api/v1/wms/events
  → WES 持久化 receipt/evidence
  → SSE 通知 transport_ingress.attempted
  → 后台应用 evidence，更新 TransportTask/outcome
  → SSE 通知 transport_evidence.updated

用户点击通知或任务行
  → GET /api/v1/transport/tasks/{transport_task_id}
  → 展示数据库中的规范化请求、最新 evidence 和规范化结果
```

页面恢复、刷新或 SSE 重连时只重新加载一次最近任务，不启动定时器。

## 5. 后端 API 合同

### 5.1 最近任务列表

新增：

```http
GET /api/v1/transport/tasks?limit=20&cursor=...&kind=RACK_MOVE&status=FAILED
```

权限：`ops:transport-task:list`。

查询参数：

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `limit` | integer | 默认 20，最小 1，最大 100 |
| `cursor` | string | 可选，不透明 keyset cursor |
| `kind` | Transport kind | 可选，四种能力之一 |
| `status` | Transport status | 可选 |

响应：

```json
{
  "data": {
    "items": [
      {
        "transport_task_id": "transport-...",
        "client_request_id": "...",
        "submit_operation_id": "...",
        "kind": "RACK_MOVE",
        "status": "FAILED",
        "reason_code": "TARGET_BLOCKED",
        "created_at": "2026-08-26T10:00:00Z",
        "updated_at": "2026-08-26T10:00:12Z",
        "latest_evidence": {
          "operation": "transport.task.resulted@v1",
          "operation_id": "...",
          "outcome_revision": 1,
          "status": "APPLIED",
          "conflict_code": null,
          "processed_at": "2026-08-26T10:00:12Z"
        }
      }
    ],
    "next_cursor": null
  }
}
```

规则：

- 返回全部来源的 `TransportTask`，不新增或返回 `source`；
- 以 `(created_at DESC, id DESC)` 稳定排序，cursor 编码最后一条的排序键；
- Repository 使用 `limit + 1` 判断 `next_cursor`，不返回 offset 总数；
- 列表只返回摘要，不包含 `request_json`、`outcome_json` 或 WMS 原始报文；
- 最新 evidence 使用单次查询投影，避免逐行查询。

### 5.2 单条任务详情

扩展现有：

```http
GET /api/v1/transport/tasks/{transport_task_id}
```

权限：`ops:transport-task:read`。

在现有任务标识、状态、原因、时间和最新 evidence 之外，新增：

```json
{
  "request": {
    "client_request_id": "0198c480-5a00-7c31-8000-000000000003",
    "kind": "BIN_MOVE",
    "caller": {
      "workline_id": "TRANSPORT_DEBUG",
      "station_id": "STATION-DEBUG"
    },
    "moves": [
      {
        "bin_id": "A000001922",
        "source": {"kind": "RACK_BIN_SLOT", "rack_id": "510056", "rack_face": "90", "slot_id": "510056A3F2C101"},
        "target": {"kind": "HANDOFF_POSITION", "location_code": "CNV0301"}
      }
    ]
  },
  "result": {
    "outcome_version": 1,
    "status": "FAILED",
    "reason_code": "TARGET_BLOCKED",
    "members": [
      {
        "object_id": "A000001922",
        "status": "FAILED",
        "final_position": null,
        "position_unknown": false,
        "failure_code": "TARGET_BLOCKED",
        "arrival_face": null
      }
    ]
  }
}
```

`member.status` 由持久化 outcome 规范化计算：

- `position_unknown = true` → `UNKNOWN`；
- `failure_code` 非空 → `FAILED`；
- 其余 → `SUCCEEDED`。

`result` 在尚无已应用结果时为 `null`。详情不暴露原始 callback body、鉴权头、receipt body 或 Provider ACK body。

### 5.3 Transport SSE

新增：

```http
GET /api/v1/transport/evidences/stream
Accept: text/event-stream
```

权限：`ops:transport-evidence:stream`。复用 `EventStreamService`，使用独立 Redis channel
`transport:evidence:stream`。

流语义：

- live-only，不设置 `id`，不处理 `Last-Event-ID`，不重放；
- 25 秒 heartbeat；
- Redis 发布失败不改变 WMS callback HTTP 结果，也不回滚已经持久化的 evidence；
- `/api/v1/wms/events` 中的非 Transport recovery event 不发布到该 channel；
- 客户端必须把断线期间状态标记为“可能有缺口”，重连后重新查询最近任务一次；
- Nginx 对该精确路径关闭 buffering、cache 和 gzip，并设置长读取超时。

事件一：回调接入结果。

```text
event: transport_ingress.attempted
data: {
  "request_id": "...",
  "operation_id": "...",
  "operation": "transport.task.resulted@v1",
  "transport_task_id": "transport-...",
  "kind": "RACK_MOVE",
  "outcome_revision": 1,
  "received_at": "...",
  "disposition": "RECEIVED",
  "status_code": 202,
  "error_code": null,
  "observed_body_bytes": 1234
}
```

`disposition` 固定为：`RECEIVED | DUPLICATE | CONFLICT | REJECTED | UNAVAILABLE`。对于无法安全解析标识的请求，相关标识字段可为 `null`，但保留稳定 `error_code`、HTTP 状态和观测字节数。

事件二：evidence 应用结果。

```text
event: transport_evidence.updated
data: {
  "evidence_id": "...",
  "operation_id": "...",
  "operation": "transport.task.resulted@v1",
  "transport_task_id": "transport-...",
  "outcome_revision": 1,
  "status": "APPLIED",
  "conflict_code": null,
  "task_status": "FAILED",
  "reason_code": "TARGET_BLOCKED",
  "processed_at": "..."
}
```

`status` 固定为 `APPLIED | CONFLICT`。此事件在 evidence 事务提交后发布。

## 6. 前端交互

### 6.1 页面结构

页面包括四个区域：

1. 顶部状态：SSE `CONNECTING | LIVE | RECONNECTING | DISCONNECTED | FORBIDDEN`、最后事件时间和“断线期间可能有缺口”提示；
2. 最近任务：默认 20 条全部 Transport，支持 `kind`、`status`、手动刷新和“加载更多”；
3. 实时通知：本次页面会话最多保留 200 条回调接入/evidence 更新通知；
4. 任务详情抽屉：用户点击任务行或带 task ID 的通知后才查询并展示持久结果。

精确 `transport_task_id` 查询独立于列表过滤，直接复用单条详情接口。

### 6.2 四类调试任务

“新建联调任务”对话框必须提供四个显式类型表单：

- `RACK_MOVE`；
- `RACK_ROTATE`；
- `BIN_MOVE`；
- `BIN_EXCHANGE`。

表单字段直接对应现有后端 discriminated union，不提供任意 JSON 编辑器。用户提交前展示不可编辑的规范化 JSON 预览，并进行二次确认，明确提示“可能触发真实 RCS/AGV/CTU 动作”。

对话框不调用 Preflight，不读取或编辑 Provider URL，不提供 force、cancel、fake callback、retry/resend 或绕过资源约束的按钮。

`TRANSPORT_DEBUG` 的已应用终态由 Transport 模块维护独立、可丢弃的联调当前位置投影；后续 `RACK_ROTATE` 和 `BIN_EXCHANGE`
只使用该投影校验位置与朝向，不读取或污染绑定活动 `LineRunEpoch` 的业务 `PositionProjection`。定向清理当前来源任务时同步删除该
联调投影，禁止回退到更早的历史任务位置。

### 6.3 SSE 客户端复用

从现有设备 SSE 客户端中提取仅负责以下职责的通用模块：

- 使用 access token 发起 `fetch`；
- 遇到一次 `401` 后刷新 token 并重试一次；
- 校验 `Content-Type: text/event-stream`；
- 解析 SSE frame、heartbeat 和多行 `data`；
- 在 abort、EOF 或异常时释放 reader/body；
- 限制未完成 frame buffer 为 512 KiB。

设备和 Transport 各自保留事件联合类型、payload 校验与业务状态机。该提取不得改变现有设备诊断行为。

## 7. 状态与证据解释

页面应明确区分以下层次：

| 层次 | 可用证据 | 不代表 |
| --- | --- | --- |
| WES 已创建 | `TransportTask` 可查询 | WMS 已收到 |
| WMS 已接纳 | `ACCEPTED` 或匹配 ACK | RCS 已创建任务、设备已动作 |
| 回调已接入 | `transport_ingress.attempted` | evidence 已应用、任务已终态 |
| evidence 已应用 | `transport_evidence.updated/APPLIED` | 现场物理位置一定正确 |
| WES 终态 | `SUCCEEDED` 或 `FAILED` 及 members | WMS 业务单据已关闭 |
| 物理能力验收 | 现场设备、WMS/RCS 任务和 WES 证据一致 | 不能仅由页面推断 |

`RECONCILING`、`position_unknown`、回调冲突和缺少结果必须醒目标识，不提供自动重发建议。

## 8. 权限、安全与隐私

- 页面延续 `/ops` 现有超级用户菜单可见性；后端接口按用途分别校验 `ops:transport-task:list`、
  `ops:transport-task:read`、`ops:transport-evidence:stream` 和 `ops:transport:debug-create`；
- SSE 的 `401`、`403` 不降级为普通断线：`401` 仅刷新一次，`403` 进入 `FORBIDDEN` 并停止自动重连；
- 前端不在 URL、日志、通知或错误详情中输出 access token；
- SSE 和详情 API 都不返回 WMS 原始报文、鉴权材料或 Provider profile；
- 调试任务创建完全复用后端现有合同、幂等与资源约束，不加入前端绕过逻辑。

## 9. 不在本次范围

- 修改本地或服务器上的 WMS Provider profile；
- 新增 WMS callback-result 专用接口；
- 周期轮询、SSE replay、事件历史表或 WebSocket；
- 原始 WMS 请求/响应代理、原始 callback 浏览器；
- Preflight、能力注册表、安全点注册表或参数自动修复；
- fake callback、force complete、cancel、retry/resend；
- 自动判定 RCS/AGV/CTU 物理验收通过；
- 部署到联调服务器或真实触发硬件动作。

## 10. 验收标准

1. 页面首次进入时显示最近 20 条全部 Transport，无来源过滤；
2. 四种 `kind` 均可通过结构化表单创建，提交前有不可编辑预览和真实动作确认；
3. 无定时轮询；SSE 首连/重连只触发一次列表刷新；
4. WMS callback 的接纳/重复/冲突/拒绝/不可用结果可形成在线通知；
5. evidence 应用或冲突可形成在线通知；
6. 点击任务或通知后，页面从数据库查询并显示规范化请求、evidence 和 result；
7. SSE 断线、权限拒绝、消息缺口和 `RECONCILING` 均有明确状态；
8. 页面和接口不暴露原始回调、token、secret 或 Provider profile；
9. 设备接入诊断的 SSE 行为无回归；
10. 自动化验证只证明合同和页面行为；真实四能力仍需联调环境逐项执行并由现场共同验收。
