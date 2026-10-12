# API 访问日志：外部系统交互基础能力

- **维护日期**：2026-10-10
- **状态**：当前实现合同（工作分支，尚未发布）。
- **范围**：WMS、ECS 等外部系统与 WES 的双向 HTTP 交互及现有 `/logs/api-access` 页面。
- **实施基线**：backend `959cc29938db0b454ca68dce49d696955a248e68`；frontend `40dcc65f625621003cf1e9781bfb2b4d0ee08095`。

## 1. 目标与边界

API 访问日志用于外部接入排障：识别调用方向、外部系统、HTTP 尝试及其结果。现有审计日志继续承担操作和数据变更追溯。

| 决策 | 当前要求 |
| --- | --- |
| 范围 | WMS→WES、WES→WMS、ECS→WES、WES→ECS；其他外部系统按实际接入合同扩展 |
| 存储 | 一张 `wes_sys.api_access_logs`，一个模型、一套 Service/Repository；旧重复实现已退出 |
| 区分方向 | 使用 `direction` 字段，不按入站/出站拆表 |
| 摘要与详情 | 同一行保存元数据和可空 `details`；列表返回摘要，详情按需返回已有有界快照 |
| 可靠性 | 四向诊断均后台尽力写入、业务优先；operation 消息收据/Evidence 的可靠事务与 ACK 模式保持 |
| 保留 | 默认 7 天，基础调度自动清理 |
| 替换 | 一次性替换两套旧实现，不保留旧表、别名、兼容端点或历史数据转换 |

普通页面请求、CRUD、登录、权限查询、API 文档、CORS 预检、日志查询及浏览器 SSE 订阅不属于本页面的外部交互。浏览器点击调试后真正发生的 WES→WMS/ECS HTTP 发送属于本范围。

成功标准：四个方向共用一个持久模型，每次 HTTP 尝试最多创建一行；重试是独立尝试；查询无需跨日志表；诊断数据不承担业务幂等、恢复或物理完成判断。

## 2. 复用依据与唯一归属

基础实现统一位于 `src/app/sys/`：模型、Repository、Service 和两个只读管理 API 各有唯一 owner。复用 DataTableMixin、BaseService.create、get_db_context、QueryOptions/QueryBuilder 及已有后台宿主；出站复用 core.outbound_http 的一次有界收发。前端复用通用只读页面、表格、列管理器和全局抽屉。

旧 api_auth 访问日志骨架没有请求写入，实际入站诊断由旧 CallbackLog 承担。两套 HTTP 元数据与持久化职责已统一，旧模型、表、导出、管理端点和权限全部退出，不保留兼容路径或历史数据转换。

现有审计主要由创建/更新/删除 hook 触发，并非完整的 HTTP 请求审计。外部交互若同时修改数据，通信记录和对象操作审计分别说明各自事实；本能力不补齐审计覆盖范围。

## 3. 所有权与架构边界

遵循 [SRS](SRS.md) 的基础能力与业务能力划分：

```text
协议入口 / 出站结果观察 → APIAccessLogService → APIAccessLogRepository → Database
管理 API → APIAccessLogService → APIAccessLogRepository → Database
```

- 唯一实现位于 `src/app/sys/{models,repositories,services,v1}/`，数据库位于 `wes_sys`。没有新日志平台或独立实现根。
- 通用日志 Service 不导入具体业务插件，不读任务、库存、工作线或 Evidence，不解析业务正文决定状态或日志资格。
- 来源方决定 `system_id`、方向和可保存快照，基础层只保存冻结记录；日志不触发业务动作。
- 列表与详情仅查询统一日志。ECS 专用历史仍由 DeviceIngressHistoryService 关联已有 Evidence 展示设备事实；通用 API 日志不获得该职责。
- 详情中的 attempt、apply_status 等属于记录时快照，不能替代当前业务/设备事实。日志历史可清理，业务恢复不得依赖已闭合日志。
- 清空可清理日志后，新的记录、可靠接收和业务执行仍能运行；基础验证不依赖安装某个工作线插件。

API Application 管理继续属于 `api_auth`。外部交互日志不以应用授权模型为身份来源，不把请求头中的应用名或 IP 猜测当作验证身份。

## 4. 采集范围与一行一次尝试

| 方向 | 唯一接入点 | 保存记录 |
| --- | --- | --- |
| WMS→WES | 默认 `POST /api/v1/wms/events` 的 WmsCallbackReceiptService | INBOUND、system_id=wms；原逐请求快照改为统一表后台写入 |
| ECS→WES | 默认 `POST /api/v1/callback/event`、`/result` 的 DeviceIngressHistoryService | INBOUND、system_id=ecs；沿用原 attempt 写入，改写统一表 |
| WES→WMS | `build_wms_client` 构造的共享 Transport | OUTBOUND、system_id=wms；每次 send 的最终传输事实 |
| WES→ECS | DeviceEndpointAdapterProvider 构造的共享 Transport | OUTBOUND、system_id=ecs；包括命令提交、状态查询 |

入站路径以实际 `settings.API_PATH` 和注册入口为准。来源标签由明确的协议入口或既有 Transport 构造参数确定，不泛化到整个 `/api`，不通过请求正文猜测系统。

入站成功、重复、冲突、解析/校验拒绝均沿用现有处理和留痕。通用限流、方法匹配等在进入协议 handler 前拒绝的请求可能只存在通用 HTTP 文本日志，本需求不增加另一套全局采集器。进程退出和普通诊断失败也允许漏记，页面无记录不能单独证明请求未到达。

出站参数校验或 ECS deadline 准入检查在 send 之前终止的动作，不构造已发送记录。send 内的连接/池等待失败按既有 NOT_SENT 事实记录；技术重试沿用原业务身份，但每次尝试创建新日志主键。传输尚未取得结果时不假造完成记录。

WMS 调度 RCS 时，WES 记录自己与 WMS 的 HTTP，不虚构 WES→RCS。尚未接入的系统不预建 handler、动态 registry 或配置框架。

## 5. 唯一模型：公共元数据与既有详情

### 5.1 模型与字段

模型：`src/app/sys/models/api_access_log.py` 中的 `APIAccessLog`，表名为 `wes_sys.api_access_logs`。以原 CallbackLog 能力收敛，复用 DataTableMixin 和现有字段/创建能力，不保留第二个定义。

| 字段 | 来源 / 约束 | 用途与复用依据 |
| --- | --- | --- |
| `id`、`created_at` | 现有主键和 UTC 数据库创建时间；单表普通主键，沿用项目 ID 编码 | 详情、排序和清理，不引入复合身份 |
| `system_id: str` | 明确入口标签或现有 Transport system_id，最多 64 字符 | 区分 WMS、ECS，复用已有系统标识 |
| `direction: INBOUND/OUTBOUND` | 来源方明确给出，模型约束两种值 | 一张表区分方向，仅增加必要维度 |
| `method: str`、`path: str` | 实际方法和路径，长度沿用 10/500；不含 query | 定位端点，来自实际 Request/OutboundHttpRequest |
| `peer_address: str|null` | 出站已校验 endpoint origin；入站已有可信 IP，否则 null；最多 500 字符 | 区分外部地址，替代仅适合入站的 IP 命名 |
| `request_id: str|null` | 原收据/attempt ID 或已有请求上下文，最多 100 字符 | 复用链路身份；ECS attempt ID 不假称等于全局 X-Request-ID |
| `trace_id`、`event_id`、`causation_id` | 保留既有追踪字段和值，长度沿用 100/200/200；未提供则 null | 承接现有追踪检索，不另造关联身份 |
| `status_code: int|null` | 实际 HTTP 状态；无响应时 null | 统一 response_status 与 status_code，不能用 0/500/504 代替无响应 |
| `response_time_ms: int|null` | 来源方已有单调计时，非负整数；当前 ECS 未测量时 null | 复用真实测量，不把旧默认 0 当作实际耗时 |
| `delivery_state: str|null` | 出站复用 OutboundHttpDeliveryState；入站 null | 保留 NOT_SENT、DELIVERY_UNKNOWN、RESPONSE_RECEIVED 的既有语义 |
| `error_code: str|null` | 出站既有 failure_kind；ECS attempt.error_code；其他 HTTP>=400 时 HTTP_{status} | 简明稳定摘要，不输出任意异常消息或重复解释业务码 |
| `details: dict|null` | 入站已有有界收据/attempt 快照；出站本次为 null | 将旧 request_body 中实际存放的诊断快照准确命名为详情，不新建正文表或副本 |

旧 `callback_type`、`subject_code` 的来源区分由 `system_id + direction` 承接；设备 event/result 种类、设备/命令标识和处置保留在原 attempt 详情中。`ingress_outcome`、`failure_stage`、原错误信息及已保存 User-Agent 如需保留，由所属入口放在其既有详情快照，通用模型不再扩成一组仅适合某个方向的业务列。

旧 APIAccessLog 的 `app_id/app_name` 和 CallbackLog 的旧字段定义退出。追踪字段已有能力保留，设备/operation 等业务字段不新增为通用日志列。

必要索引为 `(created_at, id)`、`(system_id, direction, created_at)`；复用现有 request_id/trace_id 检索索引。删除依赖废弃字段的索引，不默认追加所有可能的组合索引或分区。

数据库使用 `timezone.now_for_db()`；API 响应复用 `timezone.to_utc()` 输出带 UTC 偏移的 ISO 时间，前端按配置时区显示。`created_at` 表示记录创建时间，页面标为“记录时间”。不对 naive datetime 调用 `.timestamp()`。

### 5.2 详情保存与展示

- WMS 继续使用现有 64 KiB 原文截断、base64、观测字节数、截断标记、摘要及 ACK 响应记录；保持非法 JSON、重复键、非法 UTF-8 的已有处理，不先转成 JSON 冒充原文。
- ECS 继续保存已通过既有入口预算和脱敏规则的 DeviceIngressAttempt 快照；现有 256 KiB 请求读取上限和安全解析保持，非法报文仍可没有 raw_payload。
- 来源方对 private details 负责。通用 Service 不重复做协议预览、JSON 比对、敏感字段算法或业务字段解析。
- 出站本次只补已存在传输事实的元数据，details 为 null；WMS 报文和协议校验继续复用现有专用诊断，不为了填满字段再复制正文。
- 列表响应和列表数据SELECT均不包含 details，详情接口才读取并返回该行 details。API日志Repository提供本资源的摘要列查询，复用现有QueryBuilder筛选/排序、BaseRepository.count、标准分页和DTO转换；一次count加一次摘要数据查询，不触发隐式或逐行details回读。摘要列以唯一APIAccessLogSummary合同为依据，完整详情与设备历史保持。不修改现有CRUD + QUERY通用能力，不扩展QueryBuilder、BaseAPI、BaseService、BaseRepository、schema_loader或全局模型延迟加载，不建立新的查询框架。

“交互耗时”在 OUTBOUND 为单次收发至清理完成，在 INBOUND 使用入口已有处理耗时，均不含本次新增诊断写入。HTTP 200 中的业务错误仍记录 HTTP 200；物理结果由原协议和 Evidence 判断。

## 6. 唯一写入实现与可靠性

APIAccessLogService 复用 BaseService，关闭列表缓存，保留唯一数据库写入及尽力包装，并提供同步后台登记入口：

```text
record(entry: APIAccessLogCreate) -> APIAccessLog       # async，唯一数据库创建
try_record(entry: APIAccessLogCreate) -> None          # async，有界尽力写入
defer_record(entry: APIAccessLogCreate) -> None       # sync，只冻结并登记后台任务
```

`record` 用现有 `get_db_context()` 打开独立 Session，调用继承的 `BaseService.create`。后者已经提交，调用方不重复 commit。数据库异常向调用方传播，不复用业务 Session，不重建 INSERT/事务机制。

`try_record` 只提供容量/预算与失败收口，内部调用同一 `record`；没有第二套创建实现。默认每个 API/worker 进程最多 2 个诊断写入，容量不足直接跳过，不等待；默认单条写入预算 100 ms，包括连接获取、创建、提交。失败用现有 Logger 的固定原因和异常类型报告，不重试，不修改原结果；外部取消继续传播。超时后的驱动回滚可能超过预算，不承诺严格墙钟上限。

worker 的批截止只通过日志专属上下文收窄单条预算，由一个有效计时器执行取消。写入协程由当前调用明确拥有；计时器退出后只取消写入一次，等待会话清理完成再释放容量，外部取消继续传播。

`defer_record` 不访问数据库、不等待容量，只冻结该次诊断数据并将 `try_record` 登记到现有 BackgroundTasks 容器；不捕获 Request、业务 Session 或可变业务对象。API 复用已全局注入的请求级 BackgroundTasks，响应发送后执行。worker 使用仅属于日志的本轮作用域，在本轮可靠业务处理完成后、原 Runner 内后置执行同一容器；无论成功、异常或取消均释放本轮数据，不允许任务跨消息遗留。worker 不设置现有审计的全局 BackgroundTasks ContextVar，避免改变审计的同步降级行为。缺少有效宿主作用域时报告并丢弃普通诊断，不同步降级。

ECS未知异常分支在本路由内复用现有已注册的、与异常类型对应的错误处理器及响应格式器，先形成错误Response，再正常返回，由FastAPI挂载现有BackgroundTasks。保留原状态、正文、headers及错误日志；不得仅调用统一500处理器或新建错误DTO/异常注册表。按实际错误Response状态冻结attempt，仍在原分支发布SSE，响应后才尝试诊断写入。没有可靠提交的Evidence不得返回成功ACK。改动限定ECS入口及必要Service协作，不修改全局异常注册、ASGI/CORS、通用审计或CRUD + QUERY；普通异常类型与数据库异常均须承接原错误响应回归证明。

| 来源 owner | 调用方式 | 失败与时序 |
| --- | --- | --- |
| WmsCallbackReceiptService | 登记 defer_record | 冻结最终响应及有界原文，响应后尽力写入；失败/超时/满额不覆盖原状态、响应体或头，不再因诊断失败返回503或追加Retry-After |
| DeviceIngressHistoryService | 登记 defer_record | attempt 诊断后置写入，不影响 Evidence 接收和原响应；SSE 在原入口按原预算发布，不等待诊断落库；实时通知不是落库确认，立即加载历史可暂时缺行 |
| WMS/ECS 出站观察函数 | 登记 defer_record | 已冻结传输结果之后只登记后台诊断，立即返回原结果，失败不重新解释结果或业务资格 |

基础层只提供唯一创建、有界尽力写入和宿主后台登记。不增加可靠性模式枚举、可靠日志队列、重试框架或永久保留标志。WMS 请求级原文快照属于诊断，不能作为 ACK 条件；各 operation 的消息收据、Evidence、幂等及 ack_commit_facts 仍按原可靠事务提交，提交失败不得成功 ACK。不得将可靠接收迁入诊断后台任务；通用日志 Service 不获得业务接收职责。

出站在现有 `_HttpxOutboundHttpTransport.send` 响应读取/清理完成、并发槽位释放后的完成边界观察一次。观察函数同步冻结并登记诊断，不等待数据库；调用方取得原结果后正常分类/保存 ACK 或结果，不为日志新增 typed result 时间字段。构造处在 `src/app/wms_adapter/factory.py` 和 `src/app/device/composition.py` 显式关联同一观察函数；核心 Transport 不导入具体应用 Service、数据库或插件，公开 `send(request)` 合同保持。

不追加 HTTP 发送、不重读响应正文、不重新定义失败分类。观察函数普通异常必须被隔离，外部取消保持传播。复用现有后台任务和 worker 运行时，不创建无人管理的任务池、线程、独立循环或新队列；取消/进程退出允许未写诊断丢失。worker 后置执行仍占用本轮消息的处理时间，每条预算不等于全批预算。其诊断阶段采用固定1秒目标总预算，从本轮可靠处理结束后以单调时钟计时；单条预算保持，并受整批剩余时间限制。预算耗尽停止当前尽力写入，等待独立会话回滚/容量释放，丢弃未执行诊断并报告，清空本轮数据，不遗留任务。驱动清理可超目标，不承诺严格1秒墙钟上限。该预算只约束worker诊断，不限制可靠业务、不覆盖API宿主或已批准SSE时序；不增加第四配置。1秒是诊断阶段的目标预算，慢库下可能较多漏记；测试记录实际占用、清理与漏记，不追加第二套限流机制。

## 7. 单表查询、API 与前端合同

### 7.1 管理 API 归属

统一管理 API 属于 `sys` 基础日志模块，仅保留：

- `POST /api/v1/sys/api-access-logs/query`，权限 `sys:apiaccesslog:list`。
- `GET /api/v1/sys/api-access-logs/{id}`，权限 `sys:apiaccesslog:detail`；id 为现有普通主键。

复用 QueryOptions、QueryBuilder、现有响应信封和 DTO 转换。查询响应为 APIAccessLogSummary，由所属Repository的摘要列查询承接；Service只负责调用和DTO边界，API不得直接调用Repository。详情为 APIAccessLogResponse（摘要字段加 details），继续复用现有完整行读取。必要的两条只读 facade 不改变通用CRUD + QUERY合同/实现，不重写筛选或扩展通用框架。

旧 `/api/v1/api_auth/access-log`、`/api/v1/callback/logs` 管理 API、特殊 trace/subject 旧端点和对应权限定义退出。已有 request/trace 查询用统一 query 表达，来源筛选用 system_id/direction 表达。外部 `/api/v1/wms/events`、`/api/v1/callback/event`、`/result` wire 和可靠 ACK 提交模式保持；WMS 仅解除诊断失败覆盖原响应的分支，按已修订的有效 Transport 合同执行。

以下是当前接口合同示例。示例路径采用默认 `/api` 前缀。

### 7.2 查询示例

请求 `POST /api/v1/sys/api-access-logs/query`：

```json
{
  "filters": {
    "couple": "and",
    "conditions": [
      { "field": "system_id", "op": "eq", "value": "ecs" },
      { "field": "direction", "op": "eq", "value": "OUTBOUND" },
      { "field": "status_code", "op": "is_null" }
    ]
  },
  "sort": [
    { "field": "created_at", "order": "desc" },
    { "field": "id", "order": "desc" }
  ],
  "offset": 0,
  "limit": 20
}
```

响应（HTTP 200）：

```json
{
  "code": "1000",
  "message": "操作成功",
  "data": {
    "total": 1,
    "items": [
      {
        "id": 101,
        "system_id": "ecs",
        "direction": "OUTBOUND",
        "method": "POST",
        "path": "/api/v1/device/command",
        "peer_address": "http://192.168.1.30:8080",
        "request_id": null,
        "trace_id": null,
        "event_id": null,
        "causation_id": null,
        "status_code": null,
        "response_time_ms": 3000,
        "delivery_state": "DELIVERY_UNKNOWN",
        "error_code": "READ_TIMEOUT",
        "created_at": "2026-10-08T12:00:00Z"
      }
    ],
    "offset": 0,
    "limit": 20
  },
  "timestamp": "2026-10-08T12:00:01Z"
}
```

### 7.3 详情示例

请求 `GET /api/v1/sys/api-access-logs/202`，无请求正文；响应（HTTP 200）：

```json
{
  "code": "1000",
  "message": "操作成功",
  "data": {
    "id": 202,
    "system_id": "ecs",
    "direction": "INBOUND",
    "method": "POST",
    "path": "/api/v1/callback/result",
    "peer_address": null,
    "request_id": "019a6c91-0000-7000-8000-000000000001",
    "trace_id": null,
    "event_id": null,
    "causation_id": null,
    "status_code": 422,
    "response_time_ms": null,
    "delivery_state": null,
    "error_code": "INVALID_ENVELOPE",
    "created_at": "2026-10-08T12:00:00Z",
    "details": {
      "request_id": "019a6c91-0000-7000-8000-000000000001",
      "kind": "DEVICE_RESULT",
      "path": "/api/v1/callback/result",
      "received_at": "2026-10-08T12:00:00Z",
      "disposition": "REJECTED",
      "status_code": 422,
      "observed_body_bytes": 37,
      "error_code": "INVALID_ENVELOPE",
      "raw_payload": null
    }
  },
  "timestamp": "2026-10-08T12:00:01Z"
}
```

details 为 null 的出站详情也正常返回；记录不存在/已清理、参数非法和无权限沿用基础错误合同，不构造空记录或虚假成功。分页 limit 沿用 1–100，wire 比较操作符仍为 ge/le，无关系或业务表筛选。

### 7.4 前端

页面导航保留 `/logs/api-access`，副标题为“WMS、ECS 等外部系统交互”。通过现有 `pnpm contract:freeze` 更新 API、权限、类型、元数据；不手改生成区、不保留旧路由适配或复合 ID。

- 使用 sys 的新只读资源，所有 API 模块、共享日志类型、字段配置和实际消费者一次性更新。
- 显示系统、方向、路径、地址、HTTP 状态、交互耗时、送达事实、错误码、记录时间；追踪字段按需查看/筛选。
- null 显示 `—`；“无 HTTP 响应”“送达未知”和 HTTP 4xx/5xx 分开展示，不把超时标为外部任务执行失败。
- 默认 `created_at desc, id desc`；单表分页仍用已有 offset，不扩展游标协议。
- 列表不显示详情正文；打开详情才获取 details，复用已有 JSON 展示和有界预览规则，不新建报文校验器。
- ECS 专用历史与 SSE 继续显示设备 facts，日志统一只改变其存储消费者，不改设备协议和物理状态真源。
- 现有时间字段及快捷筛选统一标为“记录时间”；原 HTTP 状态 >=400 的数字快捷筛选明确标为“HTTP 4xx/5xx”，不将 null 无响应、业务错误或物理失败混入这一数字条件。不新增无响应快捷筛选或修改通用查询操作符。

#### 列表与列设置

采用比较板 A“摘要均衡”。默认可见列从左到右为：记录时间、系统、方向、HTTP 状态、交互耗时、路径、操作。地址、送达事实、错误码及追踪字段由既有列配置按需显示，并在详情中完整提供；列显示选择不改变后端摘要合同。路径单元格同时显示现有 `method` 与 `path`，不增加后端字段。

默认隐藏的候选列仍须出现在现有列设置中。当前字段级 `table.visibleFrom=null` 会将字段完全移出候选列表，不能用它表示该要求。候选字段保留正常的 table 配置和 `hideable=true`；在本资源配置构造完成、尚未调用 `createManager()` 时，将已生成的 `table.columns.defaultColumns` 中对应元素的 `visibleFrom` 原位设为 null。现有管理器和列设置共用该默认数组，列定义仍由原 `columnMap` 提供；不替换数组，不新增列管理器或另一份持久状态。恢复默认也应回到七列；已有用户选择仍按通用规则处理。

```text
现有日志导航 → /logs/api-access
  API 访问日志 / WMS、ECS 等外部系统交互
  既有搜索与高级筛选 / 刷新 / 重置条件 / 列设置
  记录时间 | 系统 | 方向 | HTTP 状态 | 交互耗时 | 路径 | 操作
  既有分页                                      查看详情 → 既有详情抽屉
```

优先让用户看清记录时间、外部系统和调用方向，再看 HTTP 状态、耗时与端点。复用现有表格、筛选、分页和详情抽屉；页面差异在日志资源的字段与页面配置中实现，现有 CRUD + QUERY 通用能力保持。视觉基准本身不批准新的行为；详情层次、页面状态与适配要求分别按下述独立决策执行。

#### 详情信息层次

抽屉必须使用前端全局通用代码：沿用 `CrudPageContainer` → `CrudDetailPanel` → `StandardDrawer` 的现有入口。本页面只在已有详情配置中指定字段、分组和展示规则，不实现独立抽屉，不复制打开/关闭、按 ID 加载、加载失败/重试、切换行竞态或响应式处理，也不修改通用 CRUD + QUERY 能力。

| 顺序 | 区域 | 字段 |
| --- | --- | --- |
| 1 | 交互摘要 | 记录时间、系统、方向、方法、路径、HTTP 状态、交互耗时、送达事实、错误码 |
| 2 | 追踪与地址 | 对端地址、request_id、trace_id、event_id、causation_id、日志 ID |
| 3 | 已保存快照 | 当前行的 `details`，复用现有 JSON 展示和来源有界预览 |

三个区域按上述顺序展示；先判断交互结果，再查看关联身份，最后读取已保存快照。入站或出站都采用同一分组，不新增业务状态或关联业务表查询。

#### 交互状态

列表空状态通过本页面已有 `table.emptyText` 配置显示：“当前查询范围内暂无外部交互记录。可调整筛选或刷新；日志可能延迟或漏记，无记录不代表请求未到达。” 使用现有筛选、刷新和重置条件入口，不新增空状态按钮、自动轮询或原因诊断。查询失败使用现有错误状态，不能显示为空列表。

| 场景 | 用户看到的内容 | 复用边界 |
| --- | --- | --- |
| 首次列表加载、尚无数据 | 现有表格骨架 | 沿用全局表格加载状态 |
| 已有列表重新查询 | 沿用全局查询期间的列表与加载表现 | 不新增页面加载状态机或声称旧行是新查询结果 |
| 列表查询成功且有记录 | 摘要行、总条数和分页 | 沿用现有表格与分页 |
| 列表查询成功但无记录 | 上述固定空状态文案，现有筛选/刷新/重置入口可用 | 本页面 `emptyText` 配置 |
| 列表查询失败 | 现有错误提示与“重新加载”入口 | 沿用全局错误处理，不假造空结果 |
| 打开或切换详情时加载 | 现有抽屉详情骨架 | 沿用按 ID 加载与请求竞态控制 |
| 详情请求失败，含记录不存在/已清理或无权限 | 现有“加载失败”及实际错误信息、“重试”入口 | 沿用基础错误合同与全局详情错误展示，不返回或展示空记录假成功 |
| 详情请求成功 | 当前行的三个详情区域 | 不以列表摘要冒充已加载详情，不展示其他行的快照 |

详情成功且 `details=null`时，“已保存快照”区域显示“此记录未保存详情快照。”；出站记录补充“本次出站日志仅保存交互元数据。” 摘要和追踪区域照常显示，该说明不能作为加载错误，也不触发额外报文请求或业务表查询。已保存对象中的 `raw_payload=null`、截断标记及长度等继续按来源快照展示，不推断被省略的内容。

实现沿用全局详情的条件显示和字段配置。当前全局空值分支先于 formatter，不能仅给 `details` 配置 formatter 就假设 null 会进入它；使用已有 `showWhen` 与具备非空元数据的说明字段配置表达上述文案，不增加后端字段、不改写 API 详情对象、不修改全局空值处理。HTTP 无响应等已确认的空值语义同样须由本资源配置正确表达；其他空值仍为 `—`。

#### 排障流程与设计规范复用

| 步骤 | 用户操作 | 用户需要确定什么 | 已确认的页面支持 |
| --- | --- | --- | --- |
| 1 | 进入 API 访问日志 | 当前页只记录外部系统交互 | 页面标题、副标题与原日志导航 |
| 2 | 查看或筛选时间、系统和方向 | 先缩小本次交互范围 | 既有筛选、配置时区显示与默认列优先级 |
| 3 | 扫描 HTTP 状态、耗时和端点 | 判断传输层观察结果，找到端点 | “无 HTTP 响应”、送达未知、HTTP 数字状态分别显示，不推断业务或物理失败 |
| 4 | 打开当前行详情 | 先判断交互，再核对关联身份 | 全局抽屉；交互摘要、追踪与地址、已保存快照三个区域 |
| 5 | 查看快照或缺失说明 | 判断记录实际保存了什么 | 已有 JSON 展示、有界快照；正常未保存与加载失败分开 |
| 6 | 查询为空、失败或记录已清理 | 知道哪些事实仍不能确认，以及下一步入口 | 固定空状态说明；现有筛选/刷新/重置、错误与重试；无记录不证明未到达 |

共享详情状态保存当前请求 ID，首次详情加载失败后仍使用同一 ID 重试；受控页面与非受控面板共用该状态能力。未打开实体时不请求，既有请求序号继续隔离迟到响应。该修复恢复共享详情的既定重试语义，API 与 CRUD + QUERY 合同保持。

前 5 秒依靠标题和前三列定位范围；5 分钟内通过筛选和详情完成一次交互排查；长期维护依靠同一通用页面配置和明确的 HTTP/业务/物理事实边界。没有新增业务恢复操作、自动补查、统计卡片或新的导航层级。

主题、字体、间距、焦点与控件样式继承前端现有 `DESIGN.md`、全局样式和通用组件。继续使用全局 CSS/Element Plus 变量、Inter UI 字体和 JetBrains Mono 数值/编码体系，支持已有深浅主题；不将比较板中的固定色值、字号或抽屉宽度复制为页面样式。状态保留文字含义，颜色只作辅助；已保存快照复用 JSON 展示，不用颜色猜测业务结果。技能通用模板中的字体或字号偏好不替换本项目既有规范，不新增全局业务 token 或页面主题。

#### 响应式与无障碍

复用前端 `BREAKPOINTS` 与 `useResponsiveLayout` 的实际判定，不在本页面增加断点或更改全局布局。桌面默认列顺序保持；窄屏只改变默认列可见性，不改变摘要 API、筛选、排序、分页或详情合同。

| 实际共享断点 | 默认表格内容 | 详情表现 |
| --- | --- | --- |
| < 480px、480–767px | 记录时间、系统、方向、HTTP 状态、查看详情；耗时与路径不列为默认可见列，保留通用水平滚动 | 沿用全局默认全屏详情，三个区域全部保留 |
| 768–1279px | 默认七列，空间不足时使用现有水平滚动 | 沿用全局平板抽屉宽度与处理 |
| ≥ 1280px | 默认七列 | 沿用现有桌面抽屉及 `lg` 尺寸配置 |

通过本资源已有 `visibleFrom` 与列配置实现；手机仍可在详情查看耗时、方法和路径。列设置、排序、筛选和用户列配置继续按通用能力处理，不新增另一套列状态或本地存储兼容逻辑。布局、工具栏和导航适配沿用全局组件，不将手机表格替换为卡片列表。

键盘与辅助阅读沿用现有通用代码：详情入口使用可聚焦的操作控件，保留现有抽屉/对话框标题关联、Escape 关闭、面板内焦点处理和关闭后返回触发控件；不在页面注册重复键盘处理器。系统、方向、HTTP 状态与送达事实保留可读文字，不能只靠颜色或悬停提示；新增说明使用普通可读文本。字号、焦点环、对比度与触控区域继承项目规范（按钮及图标点击区域最低 40px，列表项最低 44px），不为该页面修改全局控件规范。键盘与布局须经实际浏览器验证；文档与测试不能替代真实辅助技术验收。

现有 `DESIGN.md` 的布局分类表与共享响应式代码对 1024–1279px 的设备命名有差异；本页按上述实际共享代码执行，不把通用断点治理扩入本需求，也不创建第二套判定。该差异不改变本页七列与全局抽屉复用要求。

## 8. 保留、清理和配置

现有核心 Beat/worker 每 60 秒调度 `cleanup_api_access_logs`，薄任务调用 Service；复用 default 队列、核心任务 include 和异步执行桥接。

Repository 按 `(created_at, id)` 删除严格满足 cutoff 的最多 5000 行；cutoff 为 `timezone.now_for_db() - timedelta(days=retention_days)`。Service 用独立事务提交，实际删除数为准。一轮只处理一批，预算 5 秒，不无限循环、不分布式锁、不清理其他表。失败回滚，下一周期继续，重复调度安全。

只清理统一 api_access_logs；不得删除 AuditLog、Evidence、设备命令或可靠交付对象。清理前需证明逐请求诊断快照不承接后续非终态恢复，可靠身份和业务事实已由原消息收据、Evidence/义务保存；它们不在清理范围，可靠接收仍按原 ACK 模式提交。发现实际可靠消费者时须先解决所有权，不能放宽清理断言。

7 天表示超过截止后可以删除，实际删除允许调度或积压延迟。停用 Beat 时记录和查询仍能运行，自动清理不发生。现有 WMS Redis 协议预览仍遵循其独立 24 小时及条数预算，可能早于数据库记录过期；本次不另存一份出站报文。

唯一新增配置入口为 `src/core/conf.py`，启动读取、修改后重启相应进程；配置索引只导航到入口：

| 配置 | 默认 / 范围 | 必要性 |
| --- | --- | --- |
| API_ACCESS_LOG_RETENTION_DAYS | 7 / 1–365 | 日志保留周期 |
| API_ACCESS_LOG_WRITE_TIMEOUT_MS | 100 / 10–1000 | 四向 try_record 的单条诊断写入预算，不覆盖 operation 可靠接收事务 |
| API_ACCESS_LOG_MAX_CONCURRENT_WRITES | 2 / 1–8 | 每进程 try_record 并发上限，无等待队列 |

清理周期、批量、清理预算为所属任务常量。现有 cleanup_old_logs 保持文件清理职责，不建立另一套调度。

## 9. 验收边界


1. 目标 schema 只有统一日志表，模型/创建/查询/清理各有唯一 owner，无旧表或兼容路径；普通 API 不新增交互记录。
2. 四个方向写入同一模型，来源和方向准确；入站没有第二次副本，重试以独立尝试行保留。
3. WMS 原文日志与 ECS/出站统一后台尽力写入；诊断故障/超时/满额不覆盖原状态、响应体或头，不为诊断失败追加 Retry-After。消息收据、Evidence 和各 operation 的 ack_commit_facts 保持可靠事务，成功 ACK 晚于提交；可靠事务失败不得成功 ACK。
4. 四向交互只登记后台诊断，返回结果前不等待诊断数据库；API 响应后执行，worker 本轮可靠处理后在原 Runner 执行并清理；无有效作用域报告并丢弃，不同步降级、不改变审计行为、不跨消息遗留。独立 Session、一次 commit、单条预算及配置并发保持；普通失败不改原结果，取消继续传播；worker整批诊断采用1秒目标预算，耗尽后停止、清理并丢弃剩余日志，驱动清理允许超目标，可靠业务不受该预算限制。
5. 无响应时 status=null，送达事实和失败分类与原 Transport 一致，不新增HTTP发送、不重读正文、不假造物理完成。
6. 普通主键详情、单表筛选/计数/排序/分页正确；摘要SQL不选details且无隐式/逐行回读，列表响应没有details，详情正确返回已有快照或null。现有CRUD + QUERY通用能力与其他资源行为不变。
7. 非法/超限/重复键等原接收防护及 WMS 原文保存、ECS 脱敏保持，不以统一存储重做协议算法。
8. DeviceIngressHistory/SSE 和当前 Evidence 状态展示保持；ECS入口只登记诊断后按原流程发布SSE，不等待诊断DB，可靠Evidence先提交。SSE不确认诊断落库，立即历史查询可暂时缺行；日志失败/退出可缺行，不新增补偿/轮询。通用日志 API 不读取业务表，快照不成为当前物理真源。ECS未知异常在局部复用原错误处理器返回Response并承载后台日志，原错误状态/正文/headers/日志保持，attempt反映实际响应状态，不改变全局异常注册或误返回成功ACK。
9. 无业务插件时，统一记录、管理查询与维护可独立运行；清理诊断历史后可靠接收和恢复仍正常。
10. 清理严格小于 UTC cutoff，边界保留、单批不超5000、失败回滚且只触及统一表；真实 Beat/worker 执行维护。
11. 前端合同、权限、普通ID、UTC时区、空值、方向、未知送达、详情按需显示一致；查询自身不生成外部行，管理只读。
12. 干净库 schema、消费者残留、测试所有权和 HEAVY mapping 闭合，通过实际必要门禁后才能宣称实施完成；健康检查、Mock 和页面可见不能替代可靠接收或业务验收。

## 10. 实现与验证入口

- 基础记录、查询与清理：`tests/sys/`、`tests/api/test_api_access_log_api.py`、`tests/integration/sys/test_api_access_log_postgresql.py`。
- WMS 可靠接收与诊断隔离：`tests/contracts/wms_adapter/test_callback_receipt_service.py`、`tests/api/test_wms_events.py`、`tests/integration/wms_adapter/test_transport_callback_receipts.py`。
- ECS attempt 与 Evidence：`tests/api/test_device_ecs_callbacks.py`、`tests/runtime/device_command/test_ingress_history_service.py`、`tests/integration/device_command/test_ingress_history_postgresql.py`。
- 出站观察与 worker：`tests/core/outbound_http/`、`tests/sys/test_api_access_log_observer.py`、`tests/runtime/test_api_access_log_worker.py`、`tests/integration/test_celery_async_runtime_postgresql.py`。
- Beat/default worker 维护：`tests/e2e/api_access_log/test_maintenance_wiring.py`。精确 HEAVY 所有权以 [映射](heavy-test-impact.toml) 为准。

本地代码、数据库、worker 和页面验证不代表外部系统接纳、物理完成或现场验收。日志预算是尽力写入约束，突发或慢库可能丢失较多记录；驱动清理允许超过目标时间。发布前从实际合入的 develop 重新冻结前端合同，不把临时工具快照作为发布来源。
