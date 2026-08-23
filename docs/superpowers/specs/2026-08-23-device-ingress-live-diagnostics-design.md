# Device Ingress Live Diagnostics Design

**Status:** 当前实现合同

**Date:** 2026-08-23

## 1. 目标

为现场联调提供仅超级用户可访问的设备入站实时诊断页：

1. 实时展示 ECS 调用 `POST /api/v1/callback/result` 和 `POST /api/v1/callback/event` 的每次 HTTP 尝试，以及对应 evidence 的异步应用状态。
2. 允许超级用户基于指定 ECS Server 的实时设备状态，创建一条经过审计和双重准入的真实 `MANUAL_DEBUG` DeviceCommand。

该页面是诊断旁路，不是可靠消息队列、历史审计库或设备控制平台。

## 2. 已确认约束

- 只展示页面连接活动期间的消息；无 replay、`Last-Event-ID` 或断线补偿。
- 同时展示 RESULT 与 EVENT；每次 callback HTTP 尝试独立成行。
- 浏览器只保留最近 200 行，并以解析后 JSON 的 UTF-8 序列化长度估算 16 MiB 上限；刷新即清空。
- ECS URL 只存在页面会话内，不写浏览器持久化或后端配置。
- 用户输入 `params` JSON object；WES 不建立设备型号或 task type 参数注册中心。
- 真实下发必须展示不可编辑预览、执行原因和二次确认；无 force、override、取消或伪造 callback。
- 整套诊断能力只允许 `is_superuser=True` 的超级用户使用。
- 不新增设备诊断权限项，不修改内置角色自动授权规则，不增加路径或角色白名单。

## 3. 非目标

- 不扩展 `APIAccessLog`。它只适合 HTTP 访问元数据，不持有 callback body、evidence 状态或实时订阅语义。
- 不把 WES→ECS 的出站 HTTP 日志混进入站表；命令状态通过既有持久化详情接口展示。
- 不保存请求头、Authorization、Cookie、token 或字节级原始 body。
- 不改变业务 DeviceCommand 的 binding、contract、状态新鲜度和可靠派发规则。
- 不以本地 Mock、SSE 可见或 HTTP `202` 证明 ECS 接纳、物理完成、供应商验收或生产业务验收。

## 4. 架构边界与复用决策

```text
ECS callback
   │
   ├─ 限长读取 / JSON 与 DTO 校验
   ├─ DeviceEvidenceService 持久化（可靠链）
   └─ 事务结果确定后 best-effort publish（诊断旁路）
                              │
                              ▼
                    Redis Pub/Sub 专用频道
                    device:evidence:stream
                              │
                              ▼
                  超级用户 SSE route + filters
                              │
                              ▼
                       浏览器会话内 200 行

DeviceEvidenceService.process_one
   └─ APPLIED / RECONCILING 提交后 ────────────────┘

超级用户 debug dialog
   ├─ DeviceEndpointAdapterProvider（既有）
   ├─ EcsAdapter.fetch_statuses（扩展既有 adapter）
   ├─ create 前运行态准入
   └─ worker send 前再次运行同一准入函数
```

复用原则：

- 保持既有 `EventStreamService.publish(event_type, payload)` 合同，通过同一 service 增加指定 channel 发布能力并抽出共享订阅迭代器；不创建第二套 Redis publish/subscribe service。
- 保留专用 Redis channel 和专用 device SSE route，避免把含解析 payload 的诊断事件广播给全局 sys stream。
- 扩展既有 `EcsAdapter.fetch_status` 的内部解码能力，新增 `fetch_statuses`；不在 debug 模块复制 ECS status 客户端。
- 从既有 dispatch 准入中提取纯运行态检查供业务派发与 MANUAL_DEBUG 共用；业务 binding、contract 和 freshness 检查仍留在业务派发路径。
- 前端普通 HTTP 继续使用生成的 `deviceApiMethods`；只有无限 SSE 响应使用原生 `fetch`。
- 前端复用 `DataTable`、`StandardDialog`、`StandardDrawer`、`AppButton` 和 token refresh；不创建同义 API wrapper 或薄 UI 包装层。

## 5. 超级用户边界

后端新增或收敛的诊断入口统一依赖既有 `require_superuser`：

- `GET /api/v1/device/evidences/stream`
- `POST /api/v1/device/commands/debug/preflight`
- `POST /api/v1/device/commands/debug`
- `GET /api/v1/device/commands/{command_code}`（该接口只查询 `MANUAL_DEBUG`）

不新增 `ops:device:evidence-stream` 等权限，不修改 `AuthorizationBootstrapService`。

前端路由复用既有 `SUPERUSER_PERMISSION = "*"`。权限目录加载失败时受保护路由 fail closed 到 403。通用菜单树按匹配路由的
`meta.permission` 调用既有 `hasPermission()` 过滤；父菜单无可见子项且自身不可导航时一并移除。该过滤不识别具体路径或角色名。

## 6. 入站实时流

### 6.1 发布时序

- ACCEPTED / DUPLICATE：`DeviceEvidenceService` 返回时事务已经提交，由 callback route 随后发布 attempt。
- CONFLICT / REJECTED：HTTP 结果确定后发布 attempt；不存在需要等待的 evidence 事务。
- evidence update：`process_one` 事务提交后发布最终 `APPLIED` 或 `RECONCILING` snapshot。
- Redis 不可用、序列化失败、publish 失败或超过 1 秒只记录诊断日志，不改变 evidence、callback HTTP 响应或 ECS 重试语义。

Redis Pub/Sub 只提供活动期间 at-most-once 通知，恰好符合 live-only 需求；不得在其上增加 replay 或持久化补偿。

### 6.2 SSE 接口

```http
GET /api/v1/device/evidences/stream
Authorization: Bearer <access-token>
Accept: text/event-stream
```

可选 filter：`device_code`、`kind`、`command_code`、`apply_status`。filter 使用统一纯谓词同时处理 attempt 与 update。

- 每 25 秒发送 heartbeat 注释。
- 不发送 SSE `id`，忽略 `Last-Event-ID`。
- query token 无效；认证只走现有 Bearer middleware。
- 单条坏消息被记录并跳过，不关闭整个订阅。
- 订阅必须在有界启动超时内确认 Redis `subscribe` ACK 后才发送首 heartbeat；不得以
  `ignore_subscribe_messages=True` 预读并丢弃首条 live message。
- Nginx 为该精确路径关闭 buffering、cache 和 gzip，并设置大于 heartbeat 的读超时。

### 6.3 事件合同

`device_ingress.attempted` 必须包含：

| 字段 | 说明 |
| --- | --- |
| `request_id` | 每次 HTTP 尝试独立生成的 UUID7 |
| `kind` | `DEVICE_RESULT` 或 `DEVICE_EVENT` |
| `path`、`received_at` | 固定 callback 路径与 WES 接收时间 |
| `disposition` | `ACCEPTED`、`DUPLICATE`、`CONFLICT`、`REJECTED` |
| `status_code` | callback 的真实 HTTP 状态 |
| `evidence_id`、`source_event_id` | 可用时填写 |
| `device_code`、`command_code`、`event_type` | DTO 解析成功时填写 |
| `apply_status` | 可用时填写，接纳后通常为 `PENDING` |
| `error_code` | 安全、稳定的拒绝原因 |
| `observed_body_bytes` | 实际已读取字节数；413 时只是已观察下界，不宣称完整 body 大小 |
| `raw_payload` | 仅 DTO 合法时携带解析后的 JSON object |

RESULT 的 `raw_payload` 必须严格等于当前 wire：`command_code`、`device_code`、`result`、`finish_time`、`data`、`error_detail`；不得虚构 `source_event_id` 输入字段。RESULT 的内部 `source_event_id` 继续由现有 evidence 规范化逻辑生成。

超限、非法 UTF-8、非标准 JSON 常量、非有限数值、过深嵌套、非法 JSON 或 DTO 校验失败时，`raw_payload=null`。现有解析防护必须保留；任何事件都不得包含 header、Cookie、token 或异常堆栈。

`device_evidence.updated` 包含 `evidence_id`、`kind`、关联身份、`apply_status` 和 `processed_at`。前端更新当前内存中全部相同 `evidence_id` 的 attempt 行；若没有关联行，再创建无 payload 的状态行。

## 7. ECS 状态枚举与准入

```text
EcsAdapter.fetch_statuses(device_code: str | None = None)
  -> tuple[EcsDeviceStatus, ...]

EcsAdapter.fetch_status(device_code: str)
  -> EcsDeviceStatus
```

- 无 `device_code` 时调用 ECS `GET /api/v1/device/status`，不带 query，保留 wire 顺序。
- 列表拒绝重复 `device_code`；单设备方法继续要求恰好一条且身份匹配。
- 两个方法复用同一响应大小、Content-Type、JSON 和 Pydantic 解码路径。
- preflight 复用既有 `DeviceEndpointAdapterProvider` 按 canonical endpoint 获取 adapter，不创建新的 transport factory。

MANUAL_DEBUG 运行态准入固定为：身份匹配、在线、`AUTO`、`IDLE`、无活动 command，且 `task_type` 位于非空 `supported_commands`。业务 DeviceCommand 仍额外执行既有 binding、contract 和 freshness 检查。

## 8. MANUAL_DEBUG 创建与派发

### 8.1 Preflight

`POST /api/v1/device/commands/debug/preflight` 接收 session ECS URL，返回全部合法设备状态及每台设备的可执行/拒绝原因。响应使用 WES DTO，不透传 ECS 原始响应。

### 8.2 创建

现有 `POST /api/v1/device/commands/debug` 增加 1–500 字符 `reason`。API 从认证上下文传入 `created_by`。

幂等顺序不得被远程 preflight 破坏：

1. 锁定并查询 `client_request_id`；相同执行字段、reason 和 created_by 时直接返回原命令，不访问 ECS。
2. 同 identity 但任一不可变字段不同则返回冲突。
3. 仅新 identity 在数据库事务外查询 ECS 并执行准入。
4. 返回数据库事务后再次锁定 identity 与 device slot，二次查询，再创建命令。

`payload_digest` 继续只表达实际 ECS 命令载荷；reason/created_by 单独比较，避免污染设备载荷身份。

数据库新增 nullable `execution_reason`，并使用条件约束保证：

- `execution_ref_type='MANUAL_DEBUG'` 时不关联 `MaterialExecution`，reason 为 trim 后非空且既有 `created_by` 非空；
- 非 MANUAL_DEBUG 时必须为 `NULL`。

`created_by` 复用 `EnterpriseMixin` 既有字段，不重复建列。

### 8.3 发送前复检

worker 对 MANUAL_DEBUG 在调用 ECS `POST /api/v1/device/command` 前重新 `fetch_status(device_code)` 并运行同一纯准入函数：

- 状态查询失败时可证明命令尚未发送，沿既有 retry/deadline 路径处理。
- 不满足准入时以精确失败码结束，且不得调用 submit。
- deadline 在查询前或查询后已到期时均不得 submit。

HTTP `202` 只表示 WES 已持久化 `PENDING`。命令详情持久化快照是最终 UI 真源；SSE RESULT 仅用于入站行关联，不再作为轮询唤醒通道。

## 9. 前端会话模型

- mount 后连接 SSE；filter 变化或手动重连先 abort 旧连接。
- 非主动断线且连接曾成功建立时，重连后插入一个“期间可能缺消息”标记。
- row 同时保存 `attempt` 与 `latestUpdate`，不得用单一 union 字段互相覆盖。
- 普通 API 使用生成的 `deviceApiMethods`；命令详情按固定 2 秒轮询，terminal 后停止。
- 行内打开 debug dialog 时只把 `device_code` 作为候选；ECS preflight 返回后才真正选中。全局打开不预选。
- ECS URL、rows、payload、draft 和 lifecycle 全部只在组件会话内存中。
- SSE 请求显式发送 `Accept: text/event-stream`，2xx 响应必须验证 Content-Type，并在收到首个完整 SSE frame 后才能标记连接
  成功；重试前释放未消费的响应体，EOF 未闭合尾帧不得派发。
- 16 MiB 预算按 `raw_payload` 的 JSON UTF-8 序列化长度计算，不复用 HTTP 入站字节数；详情展示完整 attempt/update，
  即使 REJECTED 没有 raw payload 也保留处置和错误元数据。

## 10. 验收边界

实施完成至少证明：

- callback ACCEPTED/DUPLICATE/CONFLICT/REJECTED attempt 合同和 publish failure isolation。
- evidence update 只在提交后发布，且失败不回滚状态。
- `fetch_statuses` 与原 `fetch_status` 的统一 wire 合同。
- create 幂等优先、create 前准入、worker send 前复检和绝不越过 submit 的测试。
- reason/created_by 持久化与 PostgreSQL 条件约束。
- 后端四个诊断入口只允许 superuser；普通用户返回 403。
- 前端直达路由与菜单都复用 `'*'` 判定；无空父菜单。
- SSE parser、401 单次 refresh、abort、gap、200 行/16 MiB、重复 attempt update 和 payload drawer测试。
- 生成合同、菜单 artifact、前后端 QUALITY、受影响 HEAVY、迁移链和本地 Mock 浏览器 QA。

真实 ECS/设备联调只可在另行授权的现场窗口执行。
