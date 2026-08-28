# 运输接入诊断实施计划

**Status:** Approved for implementation；当前仅完成设计与计划，生产代码尚未实施。

**Goal:** 复用现有 Transport、WMS callback、共享 SSE 和设备诊断交互基础，交付最近任务查询、按需详情、在线诊断通知与四类调试下发，不新增 Preflight、周期轮询或第二套 callback-result 接口。

**Spec:** `docs/superpowers/specs/2026-08-26-transport-integration-diagnostics-design.md`

## 1. 实施原则

- 执行使用 `wes-implementation`。公共 API、事务边界、共享 SSE 和跨仓合同属于大型/高风险切片，采用内聚 RED → DEV → GREEN；纯文档不走代码式 TDD。
- 后端和前端分别提交、验证和评审，不建立跨仓原子 Commit；前端只消费冻结后的 OpenAPI 生成物。
- 复用 `TransportService`、`TransportRepository`、`EventStreamService.publish_to()`、现有 WMS callback route、认证 SSE transport、设备诊断组件和 Transport 调试 API。
- SSE 只通知在线会话，不重放、不承载完整结果；数据库投影是状态真源，页面只在初始加载、用户选择或收到相关通知时查询。
- 不修改 `EventStreamService.publish(event_type, payload)` 既有行为，不复制 Redis/SSE 基础设施。
- 不暴露原始 callback body、鉴权头、receipt body、Provider profile、密钥或内部 digest。
- Commit、Push、PR、Merge、Deploy 和现场物理动作分别授权。本计划默认只实施与验证。

## 2. 交付切片

### Task 0：冻结执行基线与影响面

**Classification:** 只读实施前审计。

**Inspect:** 后端 Transport/WMS callback/SSE/Nginx/OpenAPI/HEAVY，前端设备诊断/SSE/API 生成物/路由，以及两个仓库的 Git 状态和目标路径 owner。

**Exit:**

- 固定生产符号、直接/间接测试 owner、共享消费者、HEAVY mapping、跨仓合同生成顺序和无关 dirty 指纹；
- 对计划内生产符号完成 GitNexus upstream impact；HIGH/CRITICAL 影响在首次修改前取得范围确认；
- 发现当前合同矛盾、目标路径重叠或计划外共享消费者时停止并修订本计划。

### Task 1：后端最近任务与规范化详情

**Classification:** 大型/高风险 API 合同切片，RED → DEV → GREEN。

**Owned paths:** Transport contracts/repository/service/tasks route 及既有 Service/API 测试 owner。

**Contract:**

- `GET /api/v1/transport/tasks` 返回最近任务摘要，默认 `limit=20`、范围 `1..100`，支持 cursor、kind、status；
- 列表与详情分别使用 `ops:transport-task:list`、`ops:transport-task:read`，保持权限叶子与 HTTP 定义一一对应；
- 使用 `(created_at DESC, id DESC)` 的稳定 keyset 分页，一次查询带出最新 evidence，不产生逐任务查询；
- 列表不返回 `request`、`result`、原始报文或虚构的 `source`；
- 单条详情增加规范化 `request` 和可空 `result`，覆盖四种现有 Transport 请求；
- 结果成员先处理 `position_unknown`，再处理确定失败，其余才是成功；
- Repository 只执行查询/flush，不 commit，API 不越层访问 Repository。

**Verification:** 复用现有 Transport Service/API 测试覆盖分页稳定性、过滤、权限、非法 cursor、四种请求、空结果和成员结果判定；运行目标文件 Ruff 检查。

### Task 2：后端 callback/evidence 诊断事件

**Classification:** 大型/高风险事务与可靠性切片，RED → DEV → GREEN。

**Owned paths:** 新增 Transport 诊断事件合同，修改 Transport service/composition 和现有 WMS event route，复用其现有测试 owner。

**Contract:**

- 独立 channel `transport:evidence:stream`；事件仅为 `transport_ingress.attempted` 与 `transport_evidence.updated`；
- ingress attempt 覆盖当前 route 已产生的 400、401、409、413、422、503 及接收/重复结果；恢复类 WMS event 不发布 Transport 通知；
- evidence update 只在数据库事务提交后发布；通知失败不回滚 evidence/task、不改变 WMS HTTP response，也不中断后续 evidence；
- 事件模型封闭且不可变，只包含稳定标识、处置、时间和最终持久状态。

**Verification:** 复用现有测试验证 HTTP 响应不变、事务提交后可见、冲突/缺失任务、publisher 失败隔离、Composition 注入和脱敏字段。

### Task 3：Transport SSE、Nginx、OpenAPI 与 HEAVY

**Classification:** 大型/高风险共享入口切片，RED → DEV → GREEN。

**Owned paths:** 新增 Transport evidence stream route，修改 Transport v1 router、Nginx exact location、OpenAPI regression 和 HEAVY mapping。

**Contract:**

- 权限为 `ops:transport-evidence:stream`，仅转发 Task 2 的两种合法事件；未知或非法 payload 跳过；
- live-only、无 replay、无 `Last-Event-ID`，空闲时发送 heartbeat；
- 设置 `Cache-Control: no-cache`、`X-Accel-Buffering: no`；Nginx 对精确路径关闭 buffering/cache/gzip 并使用长读取超时；
- OpenAPI 同时冻结 collection、detail、stream 和原四种 debug request；
- 新增/修改的生产、Nginx 和测试资产具有精确 HEAVY mapping，未知路径 fail closed。

**Verification:** 聚焦 API、Nginx、OpenAPI 和 selector 测试；最终 HEAVY 只执行 selector manifest。

### Task 4：前端复用认证 SSE transport

**Classification:** 大型/高风险共享前端基础切片，RED → DEV → GREEN。

**Contract:**

- 提取的通用 transport 继续拥有认证、重连、401/403 处理、连接状态和显式关闭；
- 设备诊断对外行为不变；Transport 诊断只提供 URL、事件 allowlist 和事件处理器；
- HMR、卸载和手动断开不会留下重复连接或重连定时器；不创建第二个 EventSource 客户端。

**Verification:** 先用现有设备诊断测试锁定行为，再加入 Transport channel 场景；运行目标 Vitest 和类型检查。

### Task 5：冻结后端合同并生成前端 API

**Classification:** 跨仓机器合同切片，不单独制造代码式 TDD。

**Contract:** 后端 Task 1—3 聚焦 GREEN 后导出 OpenAPI；运行前端现有合同冻结与类型生成入口，不手写重复 DTO；生成物不引入来源过滤或第二套回调结果接口。

**Verification:** 后端 OpenAPI regression、前端 contract freeze/verify 和生成物 diff。

### Task 6：前端最近任务与按需详情

**Classification:** 小型/低风险页面数据切片，优先调整现有测试。

**Contract:**

- 首次进入加载一次最近任务；筛选或用户显式刷新时重新查询；
- 选择任务后才加载详情，列表不解析 request/result；
- cursor 只用于“加载更多”，去重以任务 ID 为准；
- 网络失败保留已加载证据并显示错误，不启动周期轮询。

**Verification:** 复用 API client/composable 测试验证初始加载、筛选、分页、详情按需、失败保留和无 timer。

### Task 7：前端四类调试任务

**Classification:** 大型/高风险现场操作切片，RED → DEV → GREEN。

**Contract:**

- 页面复用现有 Transport 调试接口创建 `RACK_MOVE`、`RACK_ROTATE`、`BIN_MOVE`、`BIN_EXCHANGE`；
- 表单、确认对话框、权限和错误呈现复用设备诊断现有模式；
- 不增加 Preflight，不把 WES 接纳、SSE 通知或 HTTP 成功显示为物理完成；
- 创建成功后刷新最近任务并选择对应任务，不自动重发未知结果任务。

**Verification:** 复用 Transport API/UI 测试验证四种 payload、确认/取消、错误分层、权限和防重复提交。

### Task 8：前端 Transport SSE、页面与路由

**Classification:** 大型/高风险交互切片，RED → DEV → GREEN。

**Contract:**

- 新页面按列表、详情和 SSE 分别使用 `ops:transport-task:list`、`ops:transport-task:read`、
  `ops:transport-evidence:stream`，不增加角色或权限体系；
- SSE 更新只触发相关任务摘要/详情查询，断线明确显示“实时通知不可用”，历史证据仍可查询；
- 页面区分提交接纳、持久证据、Transport 终态、物理事实和现场验收；
- 复用现有布局、状态组件、确认对话框和断线提示。

**Verification:** 聚焦 composable/component/route 测试，随后执行类型检查、lint 和必要浏览器 QA。

### Task 9：最终跨仓验证与现场交接

**Classification:** 最终交付门禁，不增加功能。

**Backend:** 聚焦测试、Ruff、OpenAPI/permission regression、QUALITY、staged HEAVY selector 及其 manifest。

**Frontend:** 聚焦测试、type check、lint、build、contract freeze/verify、permission verify 和真实浏览器 QA。

**Review:** 后端和前端各执行一次主 Review；后续仅在生产代码、机器合同或运行时配置修复使证据失效时刷新相关范围。

**Field handoff:** 记录 source/image/config digest、route、Nginx、持久任务、SSE、WMS/RCS 记录和现场物理观察。部署成功、HTTP ACK、SSE 通知和页面状态均不等于供应商一致性或物理验收。

## 3. 完成标准

- 后端 list/detail/SSE 合同、事务提交后通知和脱敏边界通过聚焦测试；
- 前端只复用一套认证 SSE transport，设备诊断回归不变；
- 四种调试任务可由授权用户确认后下发，且状态语义不夸大；
- OpenAPI、前端生成物、权限、Nginx 和 HEAVY mapping 同步；
- 无 Preflight、周期轮询、第二套 callback-result API、第二套 SSE 基础设施或手写重复 DTO；
- 两个仓库分别记录当前快照的验证与 Review 证据；未执行现场动作时明确报告 `NOT ONSITE VERIFIED`。
