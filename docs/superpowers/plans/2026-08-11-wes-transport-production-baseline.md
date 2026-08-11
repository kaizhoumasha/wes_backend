# WES Transport 正式基础基线实施计划

> 状态：Approved
>
> 适用阶段：Phase 6
>
> 实施基线：`develop@b1a8fda9c3ea67fe019dc1ed2d5f23a21f796645`
>
> 评审结论：三项前置冻结已完成。将本计划与下述合同基线一并纳入实施分支后，可以开始 Phase 6 代码实施。

## 1. 目标

Phase 6 把 Phase 4 已验收的 Transport 暗构建收敛成唯一、可安装、可长期运行的基础能力。完成后，WES 具备以下事实：

1. `TransportTask` 使用最终的 `operation + operation_id` wire 合同，提交、ACK、位置证据、结果证据和持久化模型说同一种语言。
2. API 进程和 Celery worker 都从同一静态 Composition Root 构造 Transport 运行时，每个进程只持有一个长期复用的 `WmsClient`。
3. Transport 的提交、证据处理和超时对账由有界 worker 驱动；结果发布入口保留，但要等 Phase 8 的真实业务消费者显式绑定。
4. 旧 `request_rack_transport`、`request_load_unit_transport` Effect 路径退出生产闭包，相关代码和测试各自落到唯一 successor 或 `NONE`。
5. 核心、WMS Adapter、API 和 PostgreSQL 可靠性测试各自证明自己的边界，不借用插件、设备或厂商场景。

这里的“正式”指生产代码已经有唯一安装入口、明确生命周期和可运行的后台驱动，不表示已经产生业务流量。Phase 6 完成时允许系统处于零业务插件、零 TransportTask、零 outcome 发布的状态。

## 2. 批准基线

实施分支必须包含本计划和以下合同稿。文件内容变化时，只复审受影响的冻结项，不靠口头约定继续实施。

| 输入 | SHA-256 | 用途 |
| --- | --- | --- |
| `docs/contracts/transport-fulfillment-contract.md` | `28241057f213bf03d637a1e0e382e8454481ac75ceafabf7a5ed31fecf775d99` | Transport 对象、提交、ACK、证据和可靠性真源 |
| `docs/contracts/wms-async-callback-envelope-contract.md` | `4b0aaf8bac29eeaee481c50b4b0920549fca698ab73ac6aed55ae43364b8229f` | WMS 入站公共信封、幂等和 ACK 真源 |
| `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md` | `7e037f521371bb5296e1c1d17e1b8db3e93df9505efa07d7ac69b7ca1930ab3e` | 阶段顺序、入口和退出门禁 |

实现开始前再确认：

```bash
git rev-parse HEAD
shasum -a 256 \
  docs/contracts/transport-fulfillment-contract.md \
  docs/contracts/wms-async-callback-envelope-contract.md \
  docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md
```

`docs/contracts/wms-outbound-picking-task-integration-requirements.md` 是 Phase 8 的业务交接参考，不参与 Phase 6 哈希门禁。该文档
后续变化不会阻塞 Transport 基础能力实施；只有变化直接改动本节三项冻结时，才复审对应内容。

### 2.1 现有基础

Phase 4 已经交付 `TransportTask`、四个 Port 方法、Repository、Service、WMS Adapter、evidence Handler 和四个批处理入口；
对应 FAST、WMS Adapter contract 和 PostgreSQL 测试已经存在。Phase 3 的 `WmsClient` 也已经通过 Phase 2 Outbound HTTP
复用连接池和统一请求限制。这些是本计划直接承接的代码，不重新设计一套搬运平台。

当前实现仍是暗构建：wire 还在使用 `request_id`、`event_id`，`build_transport_service()` 必须注入 publisher，生产 API 没有
`/api/v1/wms/events` 路由，Celery 没有 Transport worker，旧 WMS Effect catalog 仍保留两项 Transport operation。Phase 4 的
验收只能证明基础能力可用，不能替代本阶段的生产安装、生命周期和旧 owner 收口。

## 3. 三项冻结

### 3.1 Wire 身份与持久化冻结

Transport submit 使用固定顶层信封：

```text
operation_id
operation = transport.task.submit@v1
timestamp
data
```

`operation_id` 由 WES Transport 在首次形成不可变提交时生成 UUIDv7，并与 `timestamp`、规范化 Payload 和摘要在同一事务写入 `TransportTask`。安全重提读取原快照，保持原 ID、时间和 Payload。

位置与结果 evidence 是 WMS 发起的独立交互。每条 evidence 的 UUIDv7 `operation_id` 由 WMS 生成，WES 只用 `transport_task_id` 找到任务，不把 submit `operation_id` 当作回调因果 ID。

HTTP 语义固定为：

| 场景 | 响应 | 持久化结果 |
| --- | --- | --- |
| JSON 非法或无法提取合法 UUIDv7 | `400`，空响应体 | 不建立幂等摘要 |
| 原始 Body 超过 `256 KiB` | `413`，空响应体 | 不解码、不建立幂等摘要 |
| 已取得合法 ID，但信封或 DTO 不合法 | `422 / REJECTED` | 不保存被拒 Payload 摘要；发送方修正后使用新 ID |
| 首次可靠接纳 | `202 / RECEIVED` | 原子保存身份、摘要、首次 ACK 时间和稳定 data |
| 相同身份、相同 Payload | `200 / DUPLICATE` | 复用首次 ACK 的 `timestamp + data` |
| 相同身份、不同 Payload | `409 / CONFLICT` | 保留冲突事实，不覆盖原记录 |
| 暂时无接收能力 | `429 / BUSY` 或 `503 / UNAVAILABLE` | 不接纳，原身份按合同重试 |

最终生产 JSON 不再包含业务 `request_id` 或 `event_id`。HTTP 日志可以继续使用 `X-Request-ID`，它只服务单次访问追踪。

Evidence 行保存首次 ACK 的 `timestamp + data`。重复请求把 code 改为 `DUPLICATE`，但不刷新时间或改写 data；并发重复和进程
重启后也遵守同一规则。被 `400`、`413`、`422` 拒绝的 Payload 不占用该 `operation_id` 的幂等摘要。

项目运行于 Python 3.13。标准库尚不能直接生成 UUIDv7，因此在 `src/core/uuid7.py` 提供一个小型、无状态的 UUIDv7 生成与校验
函数，供 WES 发起的 WMS wire 交互复用。实现只覆盖 RFC 9562 所需格式和当前调用，不引入第三方依赖、通用 ID 框架或可配置
策略；Transport 不建立自己的第二份 ID 工具。

### 3.2 唯一生产装配与生命周期冻结

`src/app/transport/composition.py` 是唯一 Composition Root，公开 `TransportRuntime` 和 `build_transport_runtime(...)`。运行时固定持有：

- 一个 `WmsClient`，底层连接池在进程内长期复用；
- 一个 `TransportRepository`；
- 一个 `WmsTransportAdapter`；
- 一个 `TransportService`；
- 一个 `TransportEventHandler`；
- 一个幂等的 `aclose()`。

API lifespan 构造一个 API 进程实例并挂到 `app.state.transport_runtime`。每个 Celery prefork child 由
`CeleryAsyncRuntime` 构造自己的实例，并在 child 退出时关闭。测试通过显式参数注入 transport 或 clock，不增加全局
registry、Service Locator 或第二条工厂路径。

配置来源固定为现有 WMS provider profile 的 `server_url`，HTTP 硬超时固定为合同规定的 10 秒。Phase 6 的 Transport 运行时只接纳
`network_trust_mode=isolated_lan`，且 `outbound_auth.scheme` 与 `inbound_auth.scheme` 都为 `NONE` 的 profile。Composition Root 在创建
Client、连接池或后台任务前完成校验；任一条件不满足，API 和 Celery child 都必须启动失败，并给出明确的配置错误。这样当前纯
局域网部署不需要第二套认证实现，同时不会把尚未接入 Transport 的 HMAC 能力伪装成可用能力。HMAC Transport 认证必须经过独立
规划和评审，不能通过复用 API Application/HMAC 或忽略 `credential_reference` 临时拼接。

生产和测试部署使用宿主机只读挂载的 Provider profile。该文件不在 Git 仓库内，但属于 Phase 6 必需的部署输入：部署配置
owner 必须在 Task 5 删除两项旧 operation 的同时，从各环境 profile 的 `operations` 中删除对应键，保留 `server_url`、NONE
认证配置和其余 operation。最终验收使用更新后的实际挂载文件，不能用测试 fixture 或旧 profile 代替。

`TransportService` 构造时不要求 outcome publisher。`publish_pending_outcomes(limit, publisher)` 在调用点接收真实 publisher，这样 Transport 基础运行时可以独立安装，Phase 8 再以静态代码绑定首个业务 owner。

### 3.3 旧 owner 与测试承接冻结

两项旧 WMS Effect 身份退出生产目录：

- `wms.fulfillment.request_rack_transport@v1`
- `wms.fulfillment.request_load_unit_transport@v1`

`request_rack_transport` 的基础 successor 是 `TransportPort.move_rack()`，业务调用方留给 Phase 8。
`request_load_unit_transport` 没有语义等价的 successor，固定记为 `NONE`：旧 DTO 同时表示 `PALLET | MAGAZINE | OTHER` 和通用位置，
不能改名后塞进只负责料箱的 `move_bins()`。未来若出现真实的托盘或料架搬运需求，按具体对象重新评审合同。

共享 WMS Effect 管线继续服务 `request_rack_supply` 等非 Transport operation。实施时只收掉上述两项的 DTO、catalog、
capability、投影分支、fixture 和测试；通用 `SystemOutbox`、Effect reducer、状态扫描和 Phase 2 Outbound HTTP 仍由原所有者
维护。

## 4. 目标架构

### 4.1 数据流

```text
未来业务插件（Phase 8）
        |
        | TransportPort.move_*
        v
TransportService -----> TransportRepository -----> PostgreSQL
        |
        | 有界 submit worker
        v
WmsTransportAdapter -> WmsClient -> Phase 2 Outbound HTTP -> WMS

WMS -> POST /api/v1/wms/events -> TransportEventHandler
                                      |
                                      v
                               TransportEvidence
                                      |
                               evidence worker
                                      v
                        TransportMember / TransportTask

TransportOutcome -- publish_pending_outcomes(real publisher) --> Phase 8 插件投影
```

API route 有界读取一次 ASGI stream。Body 超过 `256 KiB` 时直接返回空体 `413`；其余原始 bytes 缓存在 Request 上，交给
Handler 解析。Transport route 只在启动时已经冻结为 `isolated_lan + inbound NONE + outbound NONE` 的进程中注册和运行，按固定
Transport endpoint 准入，不为认证预解析业务 DTO，也不借用现有 API Application/HMAC 冒充 Provider profile 的 HMAC
`credential_reference`。Handler 负责 JSON、UUIDv7、信封和专属 DTO，业务状态推进留在 Service，数据库访问留在 Repository。

### 4.2 生产 worker 清单

| Celery task | 队列 | Beat 兜底 | 固定批量 | 职责 |
| --- | --- | ---: | ---: | --- |
| `src.celery_app.tasks.transport.submit_transport_tasks_batch` | `wms-fulfillment` | 30 秒 | 最多 100 条，5 秒继续领取预算 | 从数据库领取状态为 `PENDING` 的 `TransportTask` 记录，并逐条提交到 WMS |
| `src.celery_app.tasks.transport.process_transport_evidence_batch` | `wms-fulfillment` | 10 秒 | 100 | 处理已接纳 evidence |
| `src.celery_app.tasks.transport.reconcile_transport_tasks_batch` | `wms-fulfillment` | 30 秒 | 100 | 收敛超时和未知状态 |

这里的待发送对象是已经写入数据库、状态为 `PENDING` 的 `TransportTask`。后续业务流程形成确定搬运需求后，会把货架搬运或料箱
搬运请求保存成这类记录。`submit` 定时任务只负责发送已经保存的记录，不负责决定搬运哪个货架或料箱。如果记录已经保存，
但进程在提交到 WMS 前中断，定时任务可在系统恢复后继续提交。Phase 6 尚未接入创建 `TransportTask` 的业务代码，所以该任务
正常情况下每次都找不到待发送记录，处理数量为 0；Phase 8 接入真实业务后才会开始处理实际记录。

三项 task 都通过当前 child 的 `CeleryAsyncRuntime` 取得唯一 `TransportRuntime`，只调用 Service 的批处理入口。submit 每次只领取并
标记一条，再到事务外发送；完成后才决定是否领取下一条。继续领取预算使用 `time.monotonic()` 计算，已用时间达到 5 秒后不再
领取新任务，已经开始的 HTTP 仍使用完整
10 秒超时，因此一次 task 最坏约占用 15 秒。入站 route 在 `RECEIVED | DUPLICATE` 后通过 `TaskQueueGateway` 即时投递 evidence
task；即时投递失败记录 `transport.evidence.enqueue_failed` 结构化日志，10 秒 Beat 负责兜底。

`wms-fulfillment` 是单副本、单并发队列。所有路由到该队列的 Beat 兜底消息都设置 `expires`，过期时间不大于各自调度周期；
worker 恢复后直接丢弃已经失去意义的旧扫描消息。Beat 消息只负责唤醒数据库扫描，不拥有 TransportTask、evidence、WMS Effect
Outbox 或状态事实；旧消息过期等价于由下一周期替代，不等价于丢失数据库事实。submit 的 30 秒周期大于单次最坏 15 秒运行
时间，不引入分布式锁或第二条队列。

integration 必须覆盖最坏竞争：一个 15 秒 submit 占用 worker 时，10 秒周期的旧 evidence 扫描可以过期，但下一周期必须处理已
持久化 evidence；既有 fulfillment Outbox 和 Effect status 扫描也必须在 submit 结束后的下一可用周期得到执行。Phase 6 以该
有界竞争证明共享队列不会饥饿；Phase 8 接入真实 producer 前，必须使用预期吞吐重新评估队列容量，届时才能决定是否拆分队列。

`publish_pending_outcomes` 在 Phase 6 没有 Celery task、Beat schedule 或默认 consumer。Phase 8 绑定真实 publisher 时再增加唯一调度入口。

### 4.3 状态与失败收敛

```text
本地接纳
  PENDING
     | RECEIVED / DUPLICATE
     v
  ACCEPTED ---- evidence ----> EXECUTING ---- terminal evidence ----> SUCCEEDED | FAILED
     |                               |
     | delivery unknown              | contradiction / overdue
     v                               v
  RECONCILING <---------------- RECONCILING
     |
     +---- 权威证据 ----> 单调终态

确定未送达、429、503：submit 使用原 operation_id 和原快照，受 3 次发送预算约束重试
submit 收到 400、413、422：当前 TransportTask 进入 REJECTED，不做自动修正或重试
evidence 收到 400、413、422：WMS 修正内容后使用新 operation_id，被拒 Payload 不进入 WES 幂等摘要
```

submit 不批量预领尚未开始的任务。每轮在同一个短事务中领取一条、写入 `send_started_at` 并取得不可变快照，HTTP 在事务外
执行。所有写回先匹配 `operation_id + transport_task_id + payload_digest`，任一不匹配都失败关闭；随后按事实所有者分开处理：

- `RECEIVED / DUPLICATE` 是 WMS 已接纳的权威结论，即使原 lease 已过期，也按不可变身份单调收敛为已接纳，但不得回退已经
  形成的 evidence 或终态；
- `REJECTED / CONFLICT` 是确定性非接纳结论。当前任务尚未被接纳时可以按不可变身份收敛；如果当前任务已经接纳或出现更强
  evidence，则不回退状态，转入现有冲突诊断和 `RECONCILING` 路径；
- `NOT_SENT / BUSY / UNAVAILABLE / DELIVERY_UNKNOWN` 以及重试时间、发送开始事实和领取字段属于本地执行状态，必须继续
  匹配当前 lease token。旧 worker 不得清除或覆盖新尝试的计数、时间和租约。

lease 只隔离本地执行权，不能否定匹配不可变身份的 WMS 权威事实。`DELIVERY_UNKNOWN` 进入对账，不自动换号或盲目重发。

## 5. 逐文件处置矩阵

### 5.1 Transport 最终所有者

| 文件 | 处置 | 冻结结果 |
| --- | --- | --- |
| `src/core/uuid7.py` | 新增 | WES 发起的 WMS wire 交互共用的最小 UUIDv7 生成与校验 |
| `src/app/transport/contracts.py` | 修改 | Port 保留本地调用幂等身份 `client_request_id`；Transport 内部 submit wire 使用 `operation_id`；`TransportCaller` 只保留本地路由字段并删除 `correlation_id`；publisher 改为调用点依赖 |
| `src/app/transport/models.py` | 修改 | 保存 submit 快照；evidence 唯一键改为 `operation + operation_id` 并保存首次 ACK 时间和 data；`caller_json` 只保留 `workline_id + station_id?`；删除旧身份字段 |
| `src/app/transport/repository.py` | 修改 | 以最终身份、摘要和 lease 条件读写；保持 Repository 层数据库所有权 |
| `src/app/transport/service.py` | 修改 | 首次提交前原子冻结 wire；submit 逐条领取并受 5 秒继续领取预算约束；四个批处理入口保持唯一领域实现 |
| `src/app/transport/composition.py` | 重写 | 唯一 `TransportRuntime`、工厂与关闭路径；只接纳 `isolated_lan + inbound/outbound NONE`，其他 profile 启动失败 |
| `src/app/transport/__init__.py` | 修改 | 只导出稳定 Port、DTO 和生产装配入口 |
| `src/app/wms_adapter/transport_wire.py` | 修改 | submit、ACK 和 evidence 采用最终闭集信封；submit 不复制本地 `TransportCaller` |
| `src/app/wms_adapter/transport_adapter.py` | 修改 | 发送已持久化快照；精确解释 400/413/422/429/503 和 unknown |
| `src/app/wms_adapter/transport_event_handler.py` | 修改 | 先做 256 KiB 检查，再解析合法 ID；生成稳定 ACK |
| `src/app/wms_adapter/client.py`、`factory.py` | 保留，不修改 | 继续拥有 WMS HTTP 薄客户端和长期连接池构造 |
| `src/app/wms_adapter/v1/events.py`、`v1/__init__.py` | 新增 | 唯一 `/api/v1/wms/events` route |
| `src/app/wms_adapter/__init__.py` | 修改 | 导出 route、Adapter 与 `WmsInboundAuthPolicy` 公共入口 |
| `src/app/wms_adapter/inbound_auth.py` | 新增 | WMS 入站认证策略的唯一所有者；固定 Transport endpoint 只执行已冻结的 NONE 准入 |
| `src/app/callback/services/wms_inbound_auth.py` | 删除 | 旧 callback 所有权退出，不保留 re-export 或兼容层 |
| `src/app/callback/services/__init__.py` | 修改 | 删除 `WmsInboundAuthPolicy` 旧导出，不保留转发入口 |
| `src/app/callback/v1/callback.py` | 修改 | 改用 `wms_adapter` 的唯一公共入口 |
| `src/core/task_queue_gateway.py` | 修改 | 增加 `enqueue_transport_evidence(limit=100)`，隐藏 Celery 任务名 |
| `src/register.py` | 修改 | 从 `wms_adapter` 导入认证策略；API lifespan 安装、关闭 Transport runtime 并注册唯一 route |
| `src/celery_app/async_runtime.py` | 修改 | 每个 worker child 安装、暴露和关闭唯一 Transport runtime |
| `src/celery_app/tasks/transport.py` | 新增 | 三个薄 Celery task |
| `src/celery_app/app.py` | 修改 | 在显式 `include` 清单注册 Transport task 模块 |
| `src/celery_app/config.py` | 修改 | 三项 Beat schedule、`wms-fulfillment` 静态路由，以及该队列全部 Beat 兜底消息的 `expires` |
| `docs/runbooks/transport-operations.md` | 新增 | 当前态只读诊断入口：未知任务、过期 claim、待处理 evidence、未发布 outcome 和未释放资源绑定 |
| `migrations/versions/20260809_2029_a8d9b9eba49b_新增_agv_ctu_通用搬运聚合.py` | 修改 | 原 Phase 4 revision 直接建立最终 Transport schema |
| `migrations/versions/20260730_0340_f9ffbef8992a_新增_wms_履约领域关系.py` | 修改 | 原 WMS 履约 revision 直接删除 E09 身份、字段、约束和外键，只建立 rack supply 需要的 schema |

系统尚未发布，Phase 6 直接改写原 Transport revision `a8d9b9eba49b` 和原 WMS 履约 revision `f9ffbef8992a`，不新增“旧表删除后
重建”的纠偏 revision。开发和测试库在实施前清理，从空库执行 `alembic upgrade head`；不编写数据搬运、双读、兼容视图或
旧字段读取。

### 5.2 旧 WMS Effect Transport 闭包

| 文件或范围 | 处置 | successor / `NONE` |
| --- | --- | --- |
| `src/app/runtime/system_capabilities/wms/fulfillment/request_rack_transport/` | 删除 | `TransportPort.move_rack`，业务调用留给 Phase 8 |
| `src/app/runtime/system_capabilities/wms/fulfillment/request_load_unit_transport/` | 删除 | `NONE`；旧通用 load-unit 语义不等价于 `move_bins` |
| `src/app/runtime/system_capabilities/generated_index.py` | 通过 `uv run scripts/generate_runtime_extensions.py` 重建 | 不再列出两项旧 capability |
| `src/app/wms_integration/ports/fulfillment_operations.py` | 修改 | 删除两项旧 operation、DTO、result 和导出 |
| `src/app/wms_integration/ports/effect_status.py` | 修改 | 删除两项旧 DTO 导入和终局关联分支 |
| `src/app/wms_integration/operation_contract.py` | 修改 | 删除 `RACK_TRANSPORT_DEMAND` 和旧 operation 映射 |
| `src/app/wms_integration/provider_manifest.py` | 修改 | provider 清单不再要求两项旧 endpoint |
| `src/app/runtime/orchestration/services/rack_demand_service.py` | 修改 | 只构造 rack supply demand，删除 known-rack/E09 选择参数和分支 |
| `src/app/runtime/orchestration/services/wms_fulfillment_domain_projector.py` | 修改 | 只保留 rack supply 投影，移除 E09 资源交接和冗余 operation 身份校验 |
| `src/app/runtime/orchestration/repositories/wms_fulfillment_domain_repository.py` | 修改 | 删除 E09 资源交接方法及三个退役字段的读写参数 |
| `src/app/runtime/orchestration/services/wms_effect_status_service.py` | 修改 | fulfillment 投影集合只保留非 Transport operation |
| `src/app/runtime/orchestration/effect_bridges.py`、`effect_preparation_runtime.py`、`effect_runtime.py` | 保留，不修改 | 继续服务非 Transport WMS Effect；当前没有两项旧身份的直接引用 |
| `src/app/runtime/orchestration/wms_rack_demand.py` 及其表 | 保留并窄改 | 只服务 rack supply；删除 `required_rack_code`、`handoff_from_owner_id`、`root_operation_identity` 及相应约束、外键 |
| `migrations/versions/20260730_0340_f9ffbef8992a_新增_wms_履约领域关系.py` | 原 revision 直接改写 | 空库不再创建 E09 字面量和专属 schema；不新增兼容 migration |
| `src/celery_app/tasks/workline.py` 中 WMS Effect status task | 保留 | 继续服务剩余异步 WMS Effect |
| `src/celery_app/config.py` 中 WMS Effect status schedule/route | 保留 | 与新增 Transport worker 并行、职责不同 |
| `src/core/outbound_http/` | 保留 | Phase 2 基础传输，不承担 Transport 业务语义 |

`docs/architecture/legacy-cleanup-matrix.csv` 中名称含 `transport` 的旧 Effect reducer 条目描述的是通用 HTTP 传输结果，不按关键词删除。只有能够证明直接拥有上述两项 operation 的行才在本阶段更新。

### 5.3 测试 owner

| 测试范围 | 处置 | 唯一证明内容 |
| --- | --- | --- |
| `tests/core/test_uuid7.py` | 新增 | UUIDv7 版本、variant、毫秒时间、校验和同毫秒唯一性 |
| `tests/runtime/transport/` | 修改 | 纯内存状态机、不可变快照、逐条 submit 领取、重试预算和 publisher 绑定 |
| `tests/runtime/transport/test_transport_observability.py` | 新增 | 稳定日志事件名、批次摘要、对账原因、迟到写回和 lease 替代上下文 |
| `tests/contracts/wms_adapter/` | 修改 | 最终 wire、HTTP 结果分类、Body 限制、首次 ACK 快照和入站认证策略 |
| `tests/api/test_wms_transport_events.py` | 新增 | 有界 stream、认证顺序、状态码、空响应体、即时投递和 app.state 装配 |
| `tests/api/test_callback_wms_inbound_auth.py` | 修改 | callback 行为保持不变；改用 `wms_adapter` 唯一认证入口并删除 callback services 旧导出断言 |
| `tests/core/test_outbox_dispatch_target_gateway.py` | 修改 | Transport evidence 任务名和固定 kwargs 映射 |
| `tests/architecture/test_transport_boundaries.py` | 修改 | 从“保持暗构建”改为“唯一生产装配” |
| `tests/architecture/test_transport_test_boundaries.py` | 修改 | 核心、Adapter、API、integration 测试边界 |
| `tests/integration/transport/` | 修改并补充 | PostgreSQL 单条 claim、快照原子性、并发 evidence ACK、迁移和崩溃恢复 |
| `tests/integration/test_celery_async_runtime_postgresql.py` | 补充精确场景 | worker child 生命周期和三个 task 的真实运行时接线 |
| `tests/integration/test_transport_fulfillment_queue.py` | 新增 | 15 秒慢 submit 下，下一周期 evidence、既有 fulfillment Outbox 和 Effect status 扫描仍可执行 |
| `tests/e2e/transport/test_transport_production_wiring.py` | 新增 | 不依赖业务插件的真实 broker、WMS HTTP、入站 route、evidence worker 和 PostgreSQL 收敛链路 |
| `tests/deployment/test_celery_task_runtime_contract.py` | 修改 | task 注册、队列和 Beat 清单 |
| `tests/workline_runtime/system_capabilities/test_wms_fulfillment_domain_projection_hooks.py` | 删除 Transport 场景 | `NONE`；旧 E09 业务投影不属于基础 successor |
| `tests/contracts/wms_integration/test_effect_status_contract.py` | 删除两项旧 operation 用例 | `tests/contracts/wms_adapter/` 承接 Transport wire；其余 Effect 用例保留 |
| `tests/contracts/wms_integration/test_wms_operation_catalog.py` | 修改 | 证明旧 operation 已从 catalog 缺席 |
| `tests/mock/wms_operation_fixtures.py`、`tests/mock/test_wms_northbound_contract.py` | 修改 | 删除两项旧 fixture 和旧 HTTP 合同 |
| `tests/runtime/orchestration/test_wms_effect_observability.py`、`tests/sys/test_outbox_delivery.py`、`tests/workline_runtime/system_capabilities/test_wms_effect_status_repository.py` | 替换示例身份 | 继续验证通用 owner，不再借用已退役 Transport operation |
| `tests/integration/test_external_http_transport_attempt_postgresql.py`、`tests/resilience/test_external_http_effect_crash_matrix_postgresql.py` | 保留 | 继续证明通用 outbound/effect，不作为 TransportTask 验收证据 |

删除旧测试之前，目标 Transport 测试必须已经通过。测试删除的 Commit 和 PR 描述明确写出 successor 路径，确无承接价值时写 `NONE`。

### 5.4 外部 Provider profile 发布闭环

`WMS_PROVIDER_PROFILE_HOST_FILE` 指向每个部署环境实际只读挂载的 Provider profile，由部署配置 owner 维护。Task 5 退役两项旧
operation 时，同一变更窗口删除：

- `wms.fulfillment.request_rack_transport@v1`
- `wms.fulfillment.request_load_unit_transport@v1`

更新只收敛静态 operation 清单，不改写其他 endpoint、`server_url` 或认证字段。实施者先确认文件存在且可读，再用当前代码编译
实际文件；编译失败、仍含旧键或缺少任一保留 operation 都阻断 Task 5。Task 6 必须基于最终代码和该最终 profile 重新启动 API、
fulfillment worker 与 Beat。外部文件不可用时，Phase 6 只能保持 `ReviewRequired`，不能用单元测试替代发布验收。

测试路径固定如下：

```text
业务 Port（Phase 8，不在本阶段）
        |
        v
TransportService.create ---------- runtime FAST + PostgreSQL aggregate
        |
        v
submit task -> 单条 claim -> Adapter -> WmsClient -> WMS
   |              |           |          |
deployment      integration  contract   既有 Phase 3 contract

WMS -> 有界 route -> Auth -> Handler -> record_evidence + 首次 ACK
         |           |        |                 |
        API       contract  contract          integration
         |
         +-> TaskQueueGateway -> evidence task -> apply/reconcile
                 core             deployment       runtime + integration

TransportOutcome -> publish_pending_outcomes(real publisher)
                         runtime FAST；Phase 6 不安装生产 consumer
```

每条箭头只设置一个主要测试 owner。API 测试不代替 Service 或 Repository 测试，PostgreSQL 测试也不借用 Phase 8 业务场景。

## Implementation Tasks

以下任务来自本轮工程评审。先按 TDD 形成失败测试，再修改生产行为；纯文档收口只做文档校验。

- [ ] **T1（P1，人工约 4 小时 / Codex 约 45 分钟）**：wire 与 ACK：冻结最终身份、UUIDv7 和稳定 ACK
  - 来源：测试审查，公共 ACK 合同对 `DUPLICATE` 和首次应答 code 的定义冲突。
  - 文件：`src/core/uuid7.py`、`src/app/transport/models.py`、`src/app/wms_adapter/transport_wire.py`、`src/app/wms_adapter/transport_event_handler.py` 及对应测试。
  - 验证：`uv run pytest tests/core/test_uuid7.py tests/contracts/wms_adapter tests/runtime/transport -q`
- [ ] **T2（P1，人工约 6 小时 / Codex 约 1 小时）**：可靠提交：改为单条 claim、分层 fenced 写回和 5 秒继续领取预算
  - 来源：架构与性能审查，批量预领会让 lease 过期，100 次串行 HTTP 会占满单并发 worker；统一要求当前 lease 还会丢弃迟到的 WMS 权威 ACK。
  - 文件：`src/app/transport/service.py`、`src/app/transport/repository.py`、原 Transport revision 及 runtime/integration/observability 测试。
  - 验证：`RUN_WORKLINE_INTEGRATION=1 uv run pytest tests/integration/transport tests/runtime/transport -q`
- [ ] **T3（P1，人工约 4 小时 / Codex 约 40 分钟）**：生产运行时：安装 API 与 Celery child 生命周期
  - 来源：架构审查，Transport 暗装配还没有生产生命周期，零插件启动仍被 publisher 构造依赖阻断，Provider profile 的认证能力范围也未闭合。
  - 文件：`src/app/transport/composition.py`、`src/register.py`、`src/celery_app/async_runtime.py` 及生命周期测试。
  - 验证：`RUN_WORKLINE_INTEGRATION=1 uv run pytest tests/contracts/wms_adapter tests/architecture/test_transport_boundaries.py tests/integration/test_celery_async_runtime_postgresql.py -q`
- [ ] **T4（P1，人工约 5 小时 / Codex 约 50 分钟）**：入站与后台驱动：建立唯一认证 owner，接入有界 route、排队网关、三个 task 与 Beat
  - 来源：架构、代码质量与性能审查，原计划存在 callback 反向依赖、认证前无 Body 上限、新 task 未显式注册及单并发队列积压风险；共享队列还需要证明慢 submit 不会让后续扫描饥饿，空批次 smoke 也不能代替生产接线 E2E。
  - 文件：`src/app/wms_adapter/inbound_auth.py`、`src/app/wms_adapter/v1/`、`src/app/callback/services/__init__.py`、
    `src/app/callback/v1/callback.py`、`src/core/task_queue_gateway.py`、`src/celery_app/tasks/transport.py`、
    `src/celery_app/app.py`、`src/celery_app/config.py`、`src/register.py` 及对应测试。
  - 验证：`uv run pytest tests/api/test_wms_transport_events.py tests/api/test_callback_wms_inbound_auth.py tests/core/test_outbox_dispatch_target_gateway.py tests/contracts/wms_adapter tests/deployment/test_celery_task_runtime_contract.py tests/architecture/test_transport_boundaries.py -q`
- [ ] **T5（P1，人工约 5 小时 / Codex 约 50 分钟）**：旧 owner 退出：删除两项 Effect 身份并保留真实 successor 语义
  - 来源：架构审查，旧 load-unit DTO 与 `move_bins()` 不等价，E09 身份及专属字段也不能继续由正式 schema 创建。
  - 文件：5.2 节列出的 WMS capability、catalog、投影、原 WMS 履约 revision、fixture 和测试。
  - 验证：运行 Task 5 的缺席扫描和共享 WMS Effect 回归。
- [ ] **T6（P2，人工约 2 小时 / Codex 约 20 分钟）**：验收收口：刷新基线、运行门禁并归档完成计划
  - 来源：代码质量与运行审查，Phase 8 Picking 文档不应成为 Phase 6 哈希门禁，`RECONCILING` 和过期 claim 也需要初级维护人员可直接使用的诊断入口。
  - 文件：`docs/architecture/heavy-test-impact.toml`、`docs/runbooks/transport-operations.md`、`docs/superpowers/README.md`、总控计划和本计划。
  - 验证：运行 Task 6 的完整验证命令，并执行 `git diff --check`。

### Task 1：锁定失败测试与唯一代码清单

**修改测试：**

- `tests/runtime/transport/test_transport_contracts.py`
- `tests/runtime/transport/test_transport_service.py`
- `tests/core/test_uuid7.py`
- `tests/core/test_outbox_dispatch_target_gateway.py`
- `tests/contracts/wms_adapter/test_transport_wire_acceptance.py`
- `tests/contracts/wms_adapter/test_transport_adapter.py`
- `tests/contracts/wms_adapter/test_transport_event_handler.py`

**步骤：**

1. 先写本地 `client_request_id` 幂等、Transport 内部 submit `operation_id`、不可变 submit 快照、400/413 空 body、422 新 ID、
   evidence 独立 ID、稳定 ACK、Transport Port 不再接收 `correlation_id`，以及 submit wire 不携带 `caller` 的失败测试。
2. 用 `rg` 生成 `request_id`、`event_id` 和两项旧 operation 的生产引用清单，逐项归入本计划矩阵。
3. 运行目标测试，保存失败摘要，确认失败都指向计划内合同差异。

**验证：**

```bash
uv run pytest \
  tests/runtime/transport/test_transport_contracts.py \
  tests/runtime/transport/test_transport_service.py \
  tests/core/test_uuid7.py \
  tests/core/test_outbox_dispatch_target_gateway.py \
  tests/contracts/wms_adapter/test_transport_wire_acceptance.py \
  tests/contracts/wms_adapter/test_transport_adapter.py \
  tests/contracts/wms_adapter/test_transport_event_handler.py -q
```

通过标准：测试先以预期原因失败；引用清单中的每一项都有一个文件处置，不出现“后续再看”。

### Task 2：完成最终 wire、模型和原 Transport revision

**修改：** `src/core/uuid7.py`、`src/app/transport/`、`src/app/wms_adapter/transport_*.py`、原 Transport Alembic revision 及对应
FAST/contract/integration 测试。

**步骤：**

1. 在修改每个函数、类或方法前运行 GitNexus upstream impact analysis；HIGH/CRITICAL 先停下汇报。
2. 实现共享的最小 UUIDv7 helper，并以固定 clock/entropy 做确定性测试；Transport 和后续 WMS 业务不得复制生成算法。
3. 让 `TransportTask` 在首次提交前原子保存 `submit_operation_id`、`submit_timestamp_ms`、规范化 Payload 和摘要。
4. 把 evidence 唯一键改为 `operation + operation_id`，原子保存首次 ACK 的 `timestamp + data`；重复请求返回 `DUPLICATE`，
   并复用首次 ACK 时间和 data。成员和投影引用最终 evidence 身份；删除
   `TransportCaller.correlation_id`，既有 `caller_json` 直接重建，不保留旧字段读取。
5. 让 Adapter 只发送不含本地 `TransportCaller` 的持久化 wire 快照，让 Handler 严格按合同次序处理 body、JSON、ID、DTO 和
   持久化。
6. 直接改写 `a8d9b9eba49b`，空库升级后只出现最终字段和约束；迁移图不增加 Transport 纠偏 revision。
7. submit 每轮原子领取并标记一条任务，事务外发送，完成后按 `time.monotonic()` 的 5 秒继续领取预算决定是否进入下一轮；
   不得使用墙上时钟计算预算，也不得批量预领尚未开始的任务。
8. 所有 submit 写回先校验不可变 wire 身份和摘要；`RECEIVED/DUPLICATE` 可跨过期 lease 单调接纳，`REJECTED/CONFLICT` 只在
   未出现更强事实时收敛，`NOT_SENT/BUSY/UNAVAILABLE/DELIVERY_UNKNOWN` 及重试字段必须匹配当前 lease。
9. 增加迟到 ACK 测试：旧 lease 的匹配 `RECEIVED` 仍被接纳；迟到拒绝不回退已接纳/evidence 状态；旧 lease 的重试结果不能
   清除新尝试的 `send_started_at`、重试时间或 claim；身份或摘要不匹配时不写入任何状态。
10. 为批次完成、进入 `RECONCILING`、迟到写回、lease 被替代和 outcome 发布失败记录稳定结构化日志。event name 与字段集合
    固定，任务 ID、operation ID 和 reason 只作为日志上下文，不提升为 metric label，也不记录 Payload 或 claim token 原值。

**验证：**

```bash
uv run pytest tests/runtime/transport tests/contracts/wms_adapter -q
uv run pytest tests/runtime/transport/test_transport_observability.py -q
uv run pytest tests/core/test_uuid7.py -q

RUN_WORKLINE_INTEGRATION=1 \
ALEMBIC_DATABASE_URL="$INTEGRATION_DATABASE_URL" \
uv run alembic upgrade head

RUN_WORKLINE_INTEGRATION=1 \
uv run pytest tests/integration/transport -q
```

通过标准：wire、ORM 和数据库约束一致；并发重复及进程重启后 ACK 时间和 data 保持不变；过期 lease 不会使尚未开始的任务被
批量重复领取；匹配不可变身份的迟到权威 ACK 可以单调收敛，而旧 lease 不能改写新尝试的本地重试字段；旧业务 `request_id`、
evidence `event_id`、Transport 专用 `correlation_id`、submit wire 的 `caller` 和历史表读取在 Transport 生产代码中为零；本地
`TransportCaller(workline_id, station_id?)` 继续服务结果路由。

### Task 3：安装唯一生产运行时

**修改：** `src/app/transport/composition.py`、`src/register.py`、`src/celery_app/async_runtime.py` 及生命周期测试。

**步骤：**

1. 用 `TransportRuntime` 集中构造并持有 Client、Adapter、Repository、Service 和 Handler。
2. runtime 构造前校验唯一 Provider profile：仅 `isolated_lan + inbound NONE + outbound NONE` 通过；HMAC、非局域网或混合认证配置均失败关闭，且不得先创建 Client、连接池或 task。
3. API lifespan 和每个 Celery child 分别创建一个 runtime，并在原生命周期终点关闭。
4. 把 publisher 从构造依赖改为 `publish_pending_outcomes` 的显式调用参数。
5. 增加受支持 profile、三类不受支持 profile、重复初始化、跨 PID 继承、关闭后复用和部分初始化失败的测试。

**验证：**

```bash
RUN_WORKLINE_INTEGRATION=1 \
uv run pytest \
  tests/contracts/wms_adapter \
  tests/architecture/test_transport_boundaries.py \
  tests/integration/test_celery_async_runtime_postgresql.py -q
```

通过标准：生产源码只有一个 runtime 工厂；同一进程/event loop 只出现一个长期 `WmsClient`；零插件启动不需要 publisher；
支持的局域网 NONE profile 可以启动，不受支持的 profile 在任何 Transport 资源创建前失败关闭。

### Task 4：接入唯一 route、认证 owner、排队网关和三个 worker

**新增/修改：** `src/app/wms_adapter/v1/`、`src/app/wms_adapter/inbound_auth.py`、`src/app/callback/services/__init__.py`、
`src/app/callback/v1/callback.py`、`src/core/task_queue_gateway.py`、`src/celery_app/tasks/transport.py`、
`src/celery_app/app.py`、`src/celery_app/config.py`、`src/register.py` 和对应测试。

**步骤：**

1. 先写 API route 与 Celery task 的失败测试。
2. route 有界读取原始 bytes；超限直接返回空体 `413`，其余请求在已冻结的 NONE 策略下完成准入并交给 Handler。
3. 把 `WmsInboundAuthPolicy` 移到 `wms_adapter`，callback 改用新 owner，删除旧文件且不保留兼容导出。
4. 在 `TaskQueueGateway` 增加 evidence 唤醒方法；route 只在 `RECEIVED | DUPLICATE` 后调用，投递异常记录
   `transport.evidence.enqueue_failed` 结构化日志，不改变已经持久化的 ACK。
5. 三个 Celery task 取得当前 child 的 runtime，按冻结批量调用 Service；`app.py` 显式 include 新 task 模块。
6. 注册固定 schedule 和 `wms-fulfillment` route；submit Beat 固定为 30 秒，并为该队列现有及新增的全部 Beat 兜底消息设置
   不大于各自调度周期的 `expires`；更新 deployment attestation 的静态清单。
7. 增加共享队列竞争 integration：让一个 submit 占用 worker 15 秒，确认过期的旧 evidence 扫描由下一周期替代，已持久化
   evidence 被处理，并且既有 fulfillment Outbox 与 Effect status 扫描在下一可用周期执行。
8. 增加一条纯 Transport E2E：测试夹具从生产 runtime 的 `TransportPort` 创建一个代表性任务，通过真实 broker 投递 submit
   task，fulfillment worker 调用 mock WMS HTTP 并保存 `RECEIVED`；随后向真实 `/api/v1/wms/events` 提交成功 evidence，由
   `TaskQueueGateway` 经 broker 唤醒 evidence task，最终在 PostgreSQL 验证任务和成员收敛。
9. E2E 只使用 Transport 公共 DTO、固定 wire 和基础设施夹具，不导入业务插件、WMS 业务授权、PickingTask、ECS 或
   DeviceCommand，也不把测试夹具注册成生产 producer。

**验证：**

```bash
uv run pytest \
  tests/api/test_wms_transport_events.py \
  tests/api/test_callback_wms_inbound_auth.py \
  tests/core/test_outbox_dispatch_target_gateway.py \
  tests/contracts/wms_adapter \
  tests/deployment/test_celery_task_runtime_contract.py \
  tests/architecture/test_transport_boundaries.py -q

RUN_WORKLINE_INTEGRATION=1 \
uv run pytest tests/integration/test_transport_fulfillment_queue.py -q

RUN_WORKLINE_INTEGRATION=1 \
uv run pytest tests/e2e/transport/test_transport_production_wiring.py -q
```

通过标准：唯一 route 可达；超限请求在准入和 JSON 解码前返回空体 `413`；局域网 NONE 准入和非支持 profile 的启动失败都有
明确测试；任务模块已注册；三项 task 都使用当前 runtime；deployment contract 证明 submit 周期和全部 fulfillment Beat
消息的 `expires`；即时投递失败可由 Beat 收敛；15 秒慢 submit 后，下一周期 evidence、fulfillment Outbox 和 Effect status 扫描
均可执行；生产接线 E2E 真实经过 broker、WMS HTTP、入站 route、evidence worker 和 PostgreSQL；空队列不制造
TransportTask、outcome 或业务投影。

### Task 5：原子退出旧 WMS Effect Transport owner

**修改/删除：** 本计划 5.2、5.3 节列出的旧 operation、capability、投影分支、fixture 和测试。

**步骤：**

1. 先确认 Tasks 1 至 4 的目标测试全部通过。
2. 删除两项 capability 目录和 operation DTO，重建 generated index；`request_load_unit_transport` 的 successor 明确记录为 `NONE`。
3. 从共享 projector、repository、status service 和 provider manifest 中移除 Transport 专属分支；RackDemand Service 固定只构造
   rack supply，不再接受 known-rack/E09 参数。
4. 从 `WmsRackDemand` 删除 `required_rack_code`、`handoff_from_owner_id`、`root_operation_identity` 以及相应约束和外键，并直接
   改写原 `f9ffbef8992a` revision，使空库只建立 rack supply 所需结构。
5. 由部署配置 owner 更新各环境 `WMS_PROVIDER_PROFILE_HOST_FILE`，从 `operations` 删除两项旧身份；使用当前代码编译实际文件，
   确认严格 operation 清单与 `isolated_lan + inbound/outbound NONE` 都通过。
6. 把仍需验证通用 Effect 的测试示例换成现存非 Transport operation。
7. 删除 `NONE` 测试，运行旧身份、E09 schema 缺席扫描和共享 WMS Effect 回归。

**验证：**

```bash
uv run scripts/generate_runtime_extensions.py --check

uv run pytest \
  tests/contracts/wms_integration \
  tests/runtime/orchestration/test_wms_effect_observability.py \
  tests/sys/test_outbox_delivery.py \
  tests/workline_runtime/system_capabilities \
  tests/architecture/test_wms_shared_effect_pipeline_guardrail.py -q

uv run pytest tests/mock/test_wms_northbound_contract.py -q

test -n "$WMS_PROVIDER_PROFILE_HOST_FILE"
test -f "$WMS_PROVIDER_PROFILE_HOST_FILE"
test -r "$WMS_PROVIDER_PROFILE_HOST_FILE"
WMS_PROVIDER_PROFILE_FILE="$WMS_PROVIDER_PROFILE_HOST_FILE" \
uv run python -c 'import os; from pathlib import Path; from src.app.wms_integration.endpoint_compiler import compile_wms_provider_profile; from src.app.wms_integration.provider_profile import load_wms_provider_profile; compile_wms_provider_profile(load_wms_provider_profile(Path(os.environ["WMS_PROVIDER_PROFILE_FILE"])))'

rtk rg -n \
  "wms\.fulfillment\.request_(rack|load_unit)_transport@v1|RACK_TRANSPORT_DEMAND|REQUEST_RACK_TRANSPORT|REQUEST_LOAD_UNIT_TRANSPORT" \
  src tests migrations

rtk rg -n \
  "\b(required_rack_code|handoff_from_owner_id|root_operation_identity)\b" \
  src/app/runtime/orchestration/wms_rack_demand.py \
  src/app/runtime/orchestration/services/rack_demand_service.py \
  src/app/runtime/orchestration/services/wms_fulfillment_domain_projector.py \
  src/app/runtime/orchestration/repositories/wms_fulfillment_domain_repository.py \
  migrations/versions/20260730_0340_f9ffbef8992a_新增_wms_履约领域关系.py
```

通过标准：旧身份扫描只允许命中明确的缺席断言，E09 schema 字段扫描无结果；从空库升级后 `wms_rack_demands` 只包含 rack
supply 所需字段和约束；共享非 Transport Effect 测试保持通过。

### Task 6：完整验收与文档收口

**修改：** `docs/architecture/heavy-test-impact.toml`、`docs/superpowers/README.md`、总控计划和本计划状态。

**步骤：**

1. 为新增 route、worker、迁移和运行时路径补精确 HEAVY mapping。
2. 在各任务目标测试通过并获得提交授权后，让 Tasks 1 至 5 的候选实现和 HEAVY mapping 进入当前分支 HEAD；最终 selector
   相对本计划冻结基线检查完整 Phase 6 提交差异。
3. 执行测试拓扑、FAST、QUALITY、selector 选中的 HEAVY 和隔离 PostgreSQL 验收。
4. 运行 GitNexus detect changes，确认影响范围等于本计划矩阵。
5. 使用最终镜像和最终 `WMS_PROVIDER_PROFILE_HOST_FILE`，通过生产部署 Compose overlay 启动 API、fulfillment worker 与 Beat，
   执行三项 Transport task smoke；记录镜像、命令、通过数量、skip 原因和运行态证据。缺少显式 integration 环境、实际 profile
   或最终 smoke 都记为未验收。
6. 新增当前态 `docs/runbooks/transport-operations.md`，只提供只读诊断：按状态与 age 查找未知任务、过期 claim、待处理
   evidence、未发布 outcome 和未释放绑定，并说明从日志事件定位到数据库事实的顺序。Runbook 不提供直接修改状态、清理绑定或
   换号重提的 SQL；资源释放和状态收敛仍只能经过 Transport Service 与权威 evidence。
7. Phase 6 全部门禁通过后，把本计划状态改为 `Completed`，更新总控下一阶段为 Phase 7，并删除 README、总控计划中把本文件
   作为当前真源的项目内引用。
8. 确认外部目标文件不存在后，把本计划完整移动到
   `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-11-wes-transport-production-baseline.md`。移动后确认外部文件存在、
   项目内原路径缺席且当前文档不再引用原路径，再执行一次 `git diff --check`；项目内不保留副本、占位文件或转发文档。

**完整验证：**

```bash
uv run pytest \
  tests/architecture/test_suite_topology_guardrail.py \
  tests/architecture/test_core_plugin_test_ownership_guardrail.py -q

uv run pytest tests/runtime/transport tests/contracts/wms_adapter tests/api/test_wms_transport_events.py -q
uv run pytest tests/runtime/transport/test_transport_observability.py -q
uv run pytest tests/mock/test_wms_northbound_contract.py -q

test -n "$INTEGRATION_DATABASE_URL"
test -n "$INTEGRATION_REDIS_URL"
test -n "$BACKEND_IMAGE"
test -n "$WMS_PROVIDER_PROFILE_HOST_FILE"
test -f "$WMS_PROVIDER_PROFILE_HOST_FILE"
test -r "$WMS_PROVIDER_PROFILE_HOST_FILE"
WMS_PROVIDER_PROFILE_FILE="$WMS_PROVIDER_PROFILE_HOST_FILE" \
uv run python -c 'import os; from pathlib import Path; from src.app.wms_integration.endpoint_compiler import compile_wms_provider_profile; from src.app.wms_integration.provider_profile import load_wms_provider_profile; compile_wms_provider_profile(load_wms_provider_profile(Path(os.environ["WMS_PROVIDER_PROFILE_FILE"])))'
RUN_WORKLINE_INTEGRATION=1 \
uv run pytest \
  tests/integration/transport \
  tests/integration/test_transport_fulfillment_queue.py \
  tests/integration/test_celery_async_runtime_postgresql.py -q

RUN_WORKLINE_INTEGRATION=1 \
uv run pytest tests/e2e/transport/test_transport_production_wiring.py -q

uv run pytest --collect-only -q -o addopts='' | tail -5

mkdir -p reports
uv run scripts/select_heavy_tests.py \
  --base b1a8fda9c3ea67fe019dc1ed2d5f23a21f796645 \
  > reports/phase6-heavy-tests.txt
if test -s reports/phase6-heavy-tests.txt; then
  RUN_WORKLINE_INTEGRATION=1 \
  uv run scripts/run_selected_heavy_tests.py \
    reports/phase6-heavy-tests.txt \
    reports/phase6-heavy-tests.xml
else
  printf '%s\n' '本次 Phase 6 差异未选择核心 HEAVY 测试。'
fi

./scripts/git-quality-gate.sh --profile quality

rtk rg -n "NoOpTransport|default.*publisher|\b(request_id|event_id)\b" \
  src/app/transport src/app/wms_adapter src/celery_app/tasks/transport.py
rtk rg -n "\bcorrelation_id\b" \
  src/app/transport src/app/wms_adapter/transport_wire.py src/app/wms_adapter/transport_adapter.py
rtk rg -n '"caller"' src/app/wms_adapter/transport_wire.py src/app/wms_adapter/transport_adapter.py
rtk rg -n \
  "wms\.fulfillment\.request_(rack|load_unit)_transport@v1|RACK_TRANSPORT_DEMAND|REQUEST_RACK_TRANSPORT|REQUEST_LOAD_UNIT_TRANSPORT" \
  src tests migrations
git diff --check
```

随后使用唯一的生产式 smoke 命令启动最终镜像：

```bash
docker compose \
  --env-file .env.prod \
  -f docker-compose.yml \
  -f docker-compose.deploy.yml \
  --profile prod \
  up --no-build api celery-wms-fulfillment celery_beat
```

Shell 必须提供已经校验的 `BACKEND_IMAGE` 和 `WMS_PROVIDER_PROFILE_HOST_FILE`，并覆盖 `.env.prod` 中对应空值；deploy overlay 把同一实际 profile 只读
挂载到 API、fulfillment worker 与 Beat。观察至少一个 30 秒周期，确认三项 Transport task 完成空批次处理，随后使用
`Ctrl-C` 关闭。该 smoke 只执行一次，且必须发生在 Task 5 的代码删除、实际 profile 更新和最终镜像构建之后；不再维护工作区
进程与生产镜像两套运行证据。

通过标准：FAST、QUALITY 和 selector 指定的 HEAVY 全部通过；integration 与纯 Transport E2E 在隔离 PostgreSQL、Redis 和 mock
WMS 上真实执行；实际 Provider profile 由当前代码严格编译；最终 API、worker、Beat 可以同时启动并执行三项 Transport task；
生产安装唯一；旧 owner 零引用；Phase 7/8 范围没有混入。

测试和代码门禁通过后执行纯文档归档校验：

```bash
test ! -e ../archive_docs/wes_backend/docs/superpowers/plans/2026-08-11-wes-transport-production-baseline.md
# 完成状态与当前引用更新后，把计划移动到上述外部路径。
test -f ../archive_docs/wes_backend/docs/superpowers/plans/2026-08-11-wes-transport-production-baseline.md
test ! -e docs/superpowers/plans/2026-08-11-wes-transport-production-baseline.md
test -z "$(rg -l 'docs/superpowers/plans/2026-08-11-wes-transport-production-baseline\.md' docs)"
git diff --check
```

归档校验只检查文档位置、当前引用和 diff 格式，不新增或运行读取文档正文的测试。

## 7. 失败模式与处理

| 失败模式 | 处理 | 测试 owner | 对外表现 |
| --- | --- | --- | --- |
| worker 在 HTTP 返回前退出 | `send_started_at` 令任务进入 `RECONCILING`，不自动重发 | integration/transport | `transport.task.reconciling` 日志和 Runbook 只读查询可定位 |
| WMS 重放相同 evidence | 返回 `DUPLICATE`，复用首次 ACK 的 `timestamp + data` | contract + integration | `200 / DUPLICATE` |
| WMS 复用 ID 发送不同 Payload | `409 / CONFLICT`，原事实保持不变 | contract + integration | `409 / CONFLICT` |
| submit 批量预领后 lease 提前过期 | 每轮只领取并标记一条，完成后再进入下一轮 | runtime + integration | 无重复 HTTP；稳定日志和 Runbook 可定位过期 claim |
| submit 单次执行时间过长 | 5 秒后停止领取新任务；已开始的 HTTP 最长再运行 10 秒 | runtime + deployment | 单次 submit 最长约运行 15 秒 |
| Beat 重叠投递或 worker 停机后积累陈旧消息 | submit 兜底周期固定为 30 秒；全部 fulfillment Beat 消息设置不大于调度周期的 `expires` | deployment | 过期兜底消息被丢弃，最新周期继续扫描 |
| 15 秒 submit 跨过 evidence 和既有 Effect 的 10 秒周期 | 数据库事实保持不变，旧扫描消息过期后由下一周期接替 | integration/queue | submit 结束后的下一可用周期继续处理 |
| 超大 Body 在认证时被完整读取 | route 先有界读取；超限请求直接结束 | API | 空体 `413` |
| API 已持久化但即时 Celery 投递失败 | 记录诊断，由 10 秒 evidence scan 兜底 | API + deployment | ACK 保持成功，日志说明进入兜底 |
| API 或 worker 部分初始化失败 | 关闭已创建的 client，不发布半成品 runtime | lifecycle integration | 进程启动失败并记录阶段 |
| 旧测试仍使用退役 operation 当通用样例 | 改成现存非 Transport operation | WMS shared-effect FAST | 门禁直接失败，不进入生产 |
| 删除 E09 时误伤 E08 rack supply | 共享文件按分支窄改，保留 supply 模型、投影和测试 | workline capability FAST | 门禁直接失败，不进入生产 |
| integration 因环境变量缺失而 skip | 记为未验收，补独立数据库后重跑 | 验收记录 | 不允许把 skip 写成通过 |

所有生产失败路径都有明确处理和测试 owner。本轮没有“无测试、无处理且静默失败”的关键缺口。

## 8. NOT in scope

本阶段交付 Transport 基础对象、WMS Adapter、生产装配、入站 route、后台驱动、数据库可靠性和旧直接 owner 收口。

以下工作已经审查并明确推迟：

- 首个 Transport producer、业务授权和 outcome 投影交给 Phase 8 自动出库插件，因为这些是业务 owner 的职责；Phase 8 producer
  在业务事务提交后即时唤醒 submit，30 秒 Beat 只负责遗漏和重试兜底；
- `DeviceCommand`、设备状态、统一 ECS 接口和设备事件交给 Phase 7，Transport 不借设备能力证明自身可靠性；
- 供应商私有 DTO、认证和行为验收留在供应商 ECS/网关，核心仓库只验证统一 wire；
- 具体工作线流程、fixture 和 E2E 放在对应插件包，Phase 6 保持零业务插件可运行；
- 通用 `move_load_unit` 合同不进入本阶段，当前没有可审计需求证明托盘、料架和其他载具应共享一个抽象。

Phase 6 不增加空插件、默认 publisher、no-op consumer、动态 registry、兼容解析、双写、旧表读取或数据迁移。`docs/hardware/` 保持原貌，不参与本阶段清理。

## 9. 实施顺序与提交建议

本计划采用一个实施通道，Tasks 1 至 6 顺序执行。wire、模型、Service、Composition Root 和旧 owner 删除之间有直接依赖，拆成
多个 worktree 会增加合同漂移和合并冲突，收益不足。测试用例可以在当前任务内部并行准备，但共享生产文件由一个实现者串行
落地。

Lane A：Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6

建议提交边界：

1. `test(transport): 冻结正式 wire 与运行时验收`
2. `feat(transport): 建立正式基础基线`
3. `refactor(wms): 退役旧 Transport Effect owner`
4. `docs(transport): 记录 Phase 6 验收并收口`

每个提交只包含当前步骤已经通过的测试。提交前运行 GitNexus detect changes；本计划不授权自动提交或推送。

## 10. 工程评审

### 10.1 范围审查

本计划涉及文件较多，主要原因是两项旧业务身份横跨静态 catalog、共享 Effect 管线、生成索引、fixture 和测试。新增生产概念保持在一个 runtime、一个 route 和三个薄 task 内；其余工作是按 owner 删除旧分支。这个范围是原子收口所需的最小闭包。

### 10.2 架构审查

- 分层关系明确：API route → Handler/Service → Repository → Database。
- Transport 与 DeviceCommand 保持平行，Phase 6 不借设备能力证明搬运能力。
- 生产接线 E2E 只从 Transport 公共 Port 注入代表性任务，不导入业务授权、插件、ECS 或 DeviceCommand，因此仍是基础能力验收。
- WmsClient 只解释 HTTP 传输，WmsTransportAdapter 解释 Transport wire，TransportService 解释可靠对象状态。
- publisher 在真实调用点绑定，零插件状态仍是完整、可运行的基础基线。
- 入站认证策略由 `wms_adapter` 统一拥有，callback 和 Transport route 都依赖这个稳定边界；Phase 6 的 Transport 只声明并验证
  `isolated_lan + inbound/outbound NONE`，不声称支持未接线的 HMAC profile。
- submit 使用单条 claim；不可变身份保护权威 ACK 收敛，lease token 保护本地重试写回，数据库事务中没有 HTTP 调用。

### 10.3 可维护性审查

- 初级开发人员从 `TransportPort`、`TransportRuntime` 和 `/api/v1/wms/events` 三个入口即可理解主链路。
- 固定批量、固定 schedule、5 秒继续领取预算和三次发送预算都写在唯一 Service 中，排障时不需要追动态策略。
- UUIDv7 helper 只提供 WMS wire 当前需要的生成与校验，放在 `src/core` 复用，但不扩展成公共 ID 框架。
- 旧 owner 使用逐文件矩阵收口，不做关键词批量删除。
- `src/app/transport/service.py` 保留一段简短 ASCII 注释，说明“单条 claim → 标记发送 → 事务外 HTTP → 身份收敛 ACK / lease 隔离重试”；
  `src/app/transport/models.py` 保留状态迁移注释。route 和薄 Celery task 流程直观，不增加重复图示。

### 10.4 测试审查

```text
Transport 纯状态与合同 ------ tests/runtime/transport
WMS wire 与 HTTP 分类 ------- tests/contracts/wms_adapter
FastAPI facade -------------- tests/api/test_wms_transport_events.py
UUIDv7 与排队映射 ----------- tests/core
PostgreSQL 事务与迁移 ------- tests/integration/transport
Celery child 生命周期 ------- tests/integration/test_celery_async_runtime_postgresql.py
生产接线全链路 ------------- tests/e2e/transport/test_transport_production_wiring.py
边界与缺席 ------------------ tests/architecture/test_transport_*.py
业务 producer/outcome ------- Phase 8 插件测试（本阶段不使用）
```

每种行为只有一个主要测试 owner。基础能力不依赖具体 rack、bin 业务决策，旧业务测试也不反向成为 Transport 验收依据。

### 10.5 性能与运行审查

单进程长期复用连接池，HTTP 在数据库事务外执行。submit 最多处理 100 条，但只有前 5 秒可以继续领取；已开始的 HTTP 仍保留
10 秒硬超时，所以单次 task 最坏约占用 15 秒。submit 使用 30 秒兜底周期，evidence 使用 10 秒兜底周期，reconcile 使用 30 秒
兜底周期；所有 fulfillment Beat 消息在下一个周期前过期，避免单并发 worker 恢复后处理陈旧扫描。竞争 integration 证明慢
submit 结束后，最新周期可以继续处理持久化事实和既有 Effect 扫描。Phase 8 接入真实 producer 前按预期吞吐复核容量；本阶段
不增加分布式锁、第二条队列或动态调优平台。

### 10.6 可运营性审查

Transport 不新增指标平台、管理 API 或专属看板。生产行为使用稳定结构化日志记录批次结果、对账原因、迟到写回、lease 替代和
即时投递失败；`docs/runbooks/transport-operations.md` 提供对应数据库事实的只读定位步骤。运维人员可以找到卡住对象及其 owner，
但不能绕过 Service 直接改状态、释放资源绑定或使用新 ID 重提。

### 10.7 TODOS.md

本轮没有新增 TODO。所有 P1/P2 发现都属于 Phase 6 完成条件，已经写入 Implementation Tasks；把它们推迟到 TODO 会留下不完整的
生产基线。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | ---: | --- | ---: |
| CEO Review | `/plan-ceo-review` | 范围与产品策略 | 0 | N/A | 0 |
| Codex Review | `/codex plan review` | 独立复核 | 7 | CLEAR（7 项新发现已闭合） | 15 |
| Eng Review | `/plan-eng-review` | 架构、测试与性能门禁 | 8 | CLEAR（实施前复核，4 项已闭合） | 31 |
| Design Review | `/plan-design-review` | UI/UX 缺口 | 0 | N/A | 0 |
| DX Review | `/plan-devex-review` | 开发体验缺口 | 0 | N/A | 0 |

**VERDICT：ENG CLEARED。Phase 6 从 Task 1 的红灯测试开始，Port 保留 `client_request_id`，wire 独立使用 `operation_id`；只有全部退出门禁和唯一生产式 smoke 通过，Transport 才完成正式基础基线。业务生产者和业务流量仍属于 Phase 8。**

NO UNRESOLVED DECISIONS
