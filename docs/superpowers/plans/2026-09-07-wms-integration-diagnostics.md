# WMS 联调诊断实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本任务默认由当前主 Agent 顺序实施；不为形式拆分 Subagent，不按任务重复完整 Review 或 Commit。

**Goal:** 工程人员通过前端定位双向 WMS 交互，查看正式合同要求、实际字段差异、异常和可复制的诊断信息。

**Architecture:** 在现有入站 Event 和出站 Client/Adapter 边界采集当次观察，复用正式 DTO 与校验结果，宿主诊断服务将有界脱敏记录保存到 Redis Stream。复用既有 SSE 实时传输，近期记录通过只读 API 经 Service/Repository 查询，前端以控制台显示 WES 观察到的请求、响应和后端生成的比较项，不增加业务实体、业务状态或重试机制。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、现有 Redis 客户端；Vue 3 Composition API、TypeScript、现有 alova/权限/合同生成能力。

**Spec:** [已批准设计](../specs/2026-09-07-wms-integration-diagnostics-design.md)。本计划确定实施与验收顺序，不重复业务设计，不粘贴生产实现或测试函数。

## 全局约束

- 第一版仅 WES 观察到的双向请求/响应/校验日志、SSE 控制台、近期记录、字段对比、异常解释、诊断信息复制；不接入 WMS 内部日志；无编辑、重发和状态修改。
- 最近 24 小时、最多 1000 条；完整记录最大 32 KiB；每次诊断处理／查询总预算 100 ms；实时模式使用 SSE，隐藏/离开或查看近期记录时断开，返回后重连并提示间隙；不保留定时轮询。
- 预期来自正式 DTO、当次校验和已关联冻结事实；合法拒绝、合法等待、DUPLICATE 与合同错误分开。
- 未捕获、截断、无法识别合同显示信息不足，不将没有日志当成没有请求。
- 先脱敏再存储；诊断失败不改变 ACK、Evidence、单次发送、重试、业务事务或取消行为。
- 页面只展示“当次接口结果／采集时状态”及 `observed_at`，不把记录刷新解释为当前业务状态更新；当前状态通过已有权威业务详情查看。
- 单次列表查询累计读取预算 2 MiB（包含预留的 Stream 字段开销），输出预算 256 KiB；最多扫描 200 条。预算与条数任一耗尽即返回继续游标，不要求每页填满。
- 严格 API → Service → Repository；SDK、插件与底层 HTTP 不导入诊断持久化实现。
- 生产变更按高风险执行 TDD；纯文档只检查结构、路径、引用和 diff。
- Commit、Push、PR、Merge、Deploy 不在此计划的自动授权中；保护实施时两个仓库的全部无关未提交工作。

## 文件与职责

后端根：`/Users/kaizhou/codeDev/wes_backend`。执行时使用隔离 worktree 的明确绝对路径，不复用主仓库环境。

| 文件或范围 | 职责 |
| --- | --- |
| 新增 `src/app/wms_diagnostics/contracts.py` | 诊断记录、字段比较项、查询及响应 DTO |
| 新增 `src/app/wms_diagnostics/config.py` | 保留期、数量、记录大小、超时的唯一配置入口 |
| 新增 `src/app/wms_diagnostics/comparison.py` | 从正式 schema/结构化校验事实形成可读比较项；无业务调用 |
| 新增 `src/app/wms_diagnostics/redaction.py` | 脱敏、有界展示和完整性标记 |
| 新增 `src/app/wms_diagnostics/repository.py` | Redis Stream 数量/时间限制、游标与读取 |
| 新增 `src/app/wms_diagnostics/service.py`、`__init__.py` | 保存观察、读取记录、失败隔离与 Service 导出 |
| 新增 `src/app/wms_diagnostics/v1/exchanges.py` 及包入口 | SSE、近期记录与详情 API，分别声明 query/read/stream 只读权限 |
| `src/app/sys/services/event_stream_service.py` 及既有 SSE 路由/代理配置 | 复用公共发布、订阅与心跳，核实发送超时、关闭缓冲；仅实际缺失时补通用边界 |
| `src/app/wms_adapter/client.py`、`dispatch.py`、`v1/events.py` | 捕获统一收发与入站事实，不改变原业务结果 |
| 新增 `src/app/wms_adapter/observation.py` | 无 I/O 的单次调用观察类型，供 Client、Adapter 与宿主显式传递；不依赖诊断存储 |
| 当前静态分派使用的 `wms_adapter` 各域 Adapter/parser | 保留本次校验错误、合同来源与关联，不复制规则 |
| `src/app/wms_adapter/factory.py`、`src/register.py` 及真实宿主装配消费者 | 将观察接到宿主服务，闭合 API/worker 生命周期 |
| `docs/architecture/heavy-test-impact.toml` | 精确覆盖新模块和实际受影响共享路径 |
| `docs/contracts/wms-northbound-interaction-contract.md`、`docs/devops/configuration-index.md` | Client 观察边界与配置入口引用 |

前端根：`/Users/kaizhou/codeDev/wes_frontend`。计划新增页面位于 `src/views/ops/wms-diagnostics/`，包括 `WmsDiagnosticsPage.vue`、`ExchangeDetail.vue`、`FieldComparisonTable.vue`、`useWmsDiagnostics.ts`；新增 `src/api/streaming/wmsDiagnosticsStream.ts` 仅做事件解析，复用 `authenticatedSseStream.ts`，不复制认证、重连与帧读取；复用现有列表、分页、JSON 展示、复制与样式能力，不新建通用 UI 框架。修改 `src/router/routes/ops.ts`，API 定义与生成物使用现有目录约定。

## 任务 1：固定实施现场与完整覆盖清单

**产物：** 一个绑定 base/head 和文件指纹的变更面清单，追加到本计划执行证据中；未闭合前不写生产补丁。

- [ ] 记录两个仓库的 HEAD、branch、status、worktree、相关 dirty 文件 stat/指纹；不读取无关完整 diff。
- [ ] 使用 `using-git-worktrees` 建立后端隔离现场并初始化独立环境；前端根据实际 dirty/生成物情况决定隔离。将已批准的设计与计划带入实施现场，不覆盖主仓库文件。
- [ ] 读取实施入口规则、`wes-implementation`、适用分层/测试合同；运行 GitNexus status，仅 stale 时索引一次并核对入口文件。
- [ ] 由 `northbound-wms-operation-inventory.csv`、Event 静态分派、`confirmation_adapter.py`、各域 Adapter 和 Transport 调用建立逐 operation 矩阵：方向、path、请求 parser、响应联合、调用 owner、直接测试、间接 fixture、HEAVY owner。
- [ ] 批量对拟改生产符号做 upstream impact，枚举 API 与 worker 工厂和共享调用者；有 HIGH/CRITICAL 时一次说明准确影响范围并按项目规则确认，不用产品设计批准代替具体风险确认。
- [ ] 核实前端路由守卫和菜单过滤；现有守卫读合并后的 `to.meta`，不能仅因 ops 父节点有超级管理员权限就删除父级限制。验证子页面可达性及父菜单显示，精确选择需要修改的文件。

**验证：** 矩阵每项有合同来源和测试 owner；无生产变更；隔离环境不会写入其他任务现场或其共享数据库。

## 任务 2：有界诊断记录、脱敏与查询基础

**文件：** 新增 contracts/config/redaction/repository/service；测试放 `tests/runtime/wms_diagnostics/`，真实 Redis 放 `tests/integration/wms_diagnostics/test_exchange_store.py`。

**接口约定：** `ExchangeObservation` 是记录前不可变观察；`ExchangeDetail` 为保存后的详情，增加 Redis Stream `exchange_id`；`ExchangeSummary` 不含正文。`ExchangeQuery` 保存筛选、游标和页大小；`ExchangePage` 包含 items、next_cursor、retention 信息及 `scan_incomplete`。`WmsDiagnosticsService.start(...)` 建立单次观察并发布 started；`finish(observation)` 最多结束一次，返回保存成功与否；`list_exchanges(query)` / `get_exchange(exchange_id)` 返回对应只读 DTO。列表及详情的状态明确为采集时快照，保存 `observed_at`；关联详情不存在或权限不足时不提供不可用跳转。

- [ ] RED：验证两个相同 operation identity 的尝试保留两条记录，过滤后的游标按最后扫描项推进；无匹配项且还有候选时不能错误终止分页。
- [ ] RED：验证满尺寸记录、稀疏筛选下读到字节预算即停止；空 items + next_cursor 表示本批未匹配，不能呈现整个范围无结果。游标不能跳过已读取但尚未判定／返回的记录。
- [ ] RED：验证 24 小时时间边界、1000 条数量边界、32 KiB 完整 UTF-8 JSON 序列化边界、100 ms 超时，以及详情已过期与 Redis 不可用的区别。
- [ ] RED：验证嵌套密码/token/header 脱敏；非法 JSON 不保存无法安全脱敏的正文；比较项 actual/expected、异常文本和复制信息同样不能泄漏敏感值。
- [ ] DEV：配置默认值使用设计值；允许范围分别限定为保留 1–168 小时、记录 1–10000 条、记录大小 4–64 KiB、超时 10–500 ms，启动时加载且修改后重启生效。环境之间使用已有部署隔离，不允许查询参数选择其他环境。
- [ ] DEV：专用 Stream 用精确数量裁剪和时间裁剪，设置空闲过期；查询不返回过期项。页大小默认 50、上限 100；每次扫描最多 200 条，累计读取不超过 2 MiB、输出不超过 256 KiB。预取前按历史记录硬上限 64 KiB 加每条 1 KiB 元数据预留计算剩余可读数量，不能读取超预算后再丢弃。调低配置也不能按较小新值估算旧记录。摘要单条限制 2 KiB，分页元数据限制 4 KiB。
- [ ] DEV：达到条数、字节或剩余处理预算时，以最后实际消费项生成继续游标并设置 `scan_incomplete`；过期和不匹配项也属于消费项，已读取但未消费项不得跳过。保留单 Stream，不预先增加摘要索引或第二份正文存储。
- [ ] DEV：记录先脱敏再预算；超预算先缩减预览与比较项，保留身份、状态、错误摘要并标记截断；超长不可控值预先限制，避免先构造无界对象再截断。
- [ ] GREEN：FAST 测试使用纯内存依赖替身，不连接真实 Redis；真实 Redis 测试单独证明原子保存/裁剪、并发写入、到期、分页和失败语义。
- [ ] GREEN：真实 Redis 填满 1000 条与 10000 条、32 KiB 与 64 KiB 记录，分别测试首批匹配和末尾稀疏匹配；10 个查看者进行有界近期记录查询持续 60 秒（每个查看者每 5 秒发起一批，仅为测试负载，不是产品轮询），记录读取字节、返回字节、p95 延迟、超时率与 Redis 内存。通过要求：每次满足字节/数量边界，正常隔离环境列表 p95 ≤ 100 ms 且无超时，分页遍历无遗漏或重复；失败只优化当前读取方式，不自动扩建索引系统。

**完成条件：** 有限存储、有限扫描、有限等待；读取故障明确报错，写入故障返回失败且不抛出业务异常。此任务不新增数据库表或迁移。

## 任务 3：双向采集与正式合同字段比较

**文件：** comparison、Client/Event/dispatch、清单内各域 Adapter/parser 和宿主装配；主要 FAST owner 为 `tests/contracts/wms_adapter/` 的现有域文件及新增 `tests/runtime/wms_diagnostics/test_comparison.py`。

**接口约定：** `FieldComparison` 包含 side、JSON 字段路径、expected_rule、可用 expected_value、actual_present、actual_value、verdict、source；缺失与 JSON null 独立表示。`ExchangeObservation` 包含 request/response 比较项、当次业务结果及独立的传输事实；不从响应 code 推断物理完成。

**唯一观察 owner 与结束规则：**

- 宿主每次调用创建一个 `WmsCallObservation`，属于 Adapter 访问合同中的纯技术观察类型，包含独立 attempt_id。出站由调用 Adapter 的宿主 Service 拥有；入站由 Event 入口的调用作用域拥有。它不存放在长期复用 Client 属性、进程全局变量或 SDK 中。
- 调用者将同一个观察参数显式传给所经 Client、Adapter/parser；各层只补充所属阶段事实，不保存 Redis。Client 提供请求/传输/响应事实，Adapter 提供实际采用的合同和校验结果。任务 1 枚举全部签名消费者后一次迁移，不保留旧签名 wrapper 或暗含默认观察器。
- 出站 owner 在 Adapter 返回或抛出后结束观察，因此包含响应校验结论；发送前拒绝也结束一次，标记未发送且无 HTTP 响应。完成观察后最多保存一次历史记录并发布一次 completed，再继续原业务结果处理。后续数据库事务状态尚未取得，不填入这条记录。
- 入站 owner 在原 handler 已产生实际响应后结束一次，保留原提交与 background 顺序；未进入 handler 的有界读取、信封解析拒绝也在同一 owner 结束。已经完成的观察禁止再次落记录；开始与完成事件分别最多发布一次。
- 原业务异常保持原异常与 traceback；取消路径立即传播 `CancelledError`，不为诊断使用 shield、额外 await、后台任务或延迟取消。取消时仅能保留当时已安全获得的本地摘要，Redis 记录允许缺失，不保证每个取消请求可回看；页面沿用采集不完整提示。

```text
宿主单次调用 owner
  → 创建观察 → started（live-only）→ Client 收发事实 → Adapter 合同结论
  → 结束观察一次 → 历史保存一次 + completed（均有失败隔离）
  → 原业务结果处理与事务
取消 → 直接传播；诊断不可用 → 原业务路径继续
```

**实时事件：** `wms_exchange.started` 与 `wms_exchange.completed` 使用同一 attempt_id，每个事件携带 observed_at、阶段、方向和已知 operation identity。出站 owner 调用 Adapter 前发布 started，标签为“准备调用”，预览来源标为宿主冻结请求，不能冒充实际发送字节；Client 完成后以真实观察补充请求/响应 WIRE。入站 owner 在有界读取后、handler 前发布 started，标签为“请求已接收”；无法安全解析时仅摘要，早期拒绝允许只有 completed。completed 包含当次响应/校验摘要、预览及保存成功时的 exchange_id，仍不推断后续业务提交。

**整个诊断路径的隔离：** 观察对象的初始化、各层补充、schema 展示、比较、脱敏、序列化、存储及 SSE 发布都不得把诊断异常传入业务路径。诊断处理使用单个总预算，默认 100 ms，按 10%/60%/30% 固定分配给开始观察与发布、完成整理与历史保存、最小完成摘要与发布；默认分别为 10/60/30 ms。各阶段用单调时钟截止，未用份额不挪用，不给每阶段重置完整预算，也不增加独立阶段配置。同步遍历另设深度 32、最多 2048 个节点、最多 256 个比较项的硬上限，预算耗尽标记诊断不完整；不能仅依靠 asyncio timeout 中断不含 await 的递归。异常降级只产生安全的故障类别/attempt_id，禁止在降级路径再次调用可能失败的比较或报文序列化。原业务异常和取消不被诊断 catch 吞掉。

预算只累计新增诊断工作的耗时，不计原 HTTP 等待、业务校验或事务执行；跨业务调用的观察补充按所属阶段累计扣减预算，完成整理与保存共享 60% 份额，不消耗最后 30% 的完成发布份额。额外诊断耗尽预算不取消原业务调用；采集时状态未取得时显示未知，不把后续事务尚未执行写成业务失败。

- [ ] RED：从矩阵选代表性正式请求及响应 fixture，验证必填缺失、严格类型、额外字段、嵌套数组、长度和联合分支；合法非成功响应必须符合合同，未知合同为未校验。
- [ ] RED：验证每次 inbound 尝试记录真实返回；重复/冲突不覆盖前次；无 identity、非法 UTF-8/JSON、超限均有安全摘要。出站错误状态码、非法媒体类型、非法 JSON、未发出、超时、取消各保留真实事实。
- [ ] DEV：优先在原 parser/Adapter 校验位置传递结构化错误与 schema 来源，不能调用业务 handler 做第二次诊断；简单值已验证时直接使用。纯展示需要解析 schema 时有深度、节点和输出预算，自引用不得递归无限展开。
- [ ] RED：并发两个不同请求、同 identity 两次重试、发送前拒绝、Adapter 校验失败分别验证观察归属和每个 owner 最多保存一次；取消用例验证即时传播、没有额外网络发送、诊断无后台任务。
- [ ] RED：逐阶段注入观察初始化/补充、schema 展开、比较、脱敏、序列化、Redis 异常；断言业务响应或原异常、发送次数、业务提交、Evidence 和后续重试意图与未发生诊断故障的基线一致。另用深层/大量字段输入及硬超时证明同步处理有界。
- [ ] DEV：按上述显式观察参数和唯一 owner 接线；Client 不导入诊断 Repository/Service。详细签名按任务 1 的全部调用者一次迁移，取消仍向调用者传播，不引入后台悬空任务。
- [ ] DEV：每次调用的观察在同一调用上下文隔离；并发交互不得串报文。request_id 与 Redis exchange_id 是技术标识，operation identity 保持原值。
- [ ] DEV：入站复用已读取的 body，最终记录实际 ACK；保留原 background/post-commit 行为。出站由原 Adapter 选择具体响应合同，不能以共享 Client 判断业务响应联合。
- [ ] DEV：复用当次已取得的业务关联和幂等结果；没有首次冻结内容时只显示冲突与证据引用，不额外开嵌套 DB session。对比项保存采集时规则与构建版本；WMS 版本无可靠来源时显示未知。
- [ ] GREEN：逐 operation 证明采集接线和合同来源；共享传输/规范化测试不复制到所有域。执行一次旧签名残留扫描和受影响领域回归。

**验证命令基线：** `uv run pytest tests/contracts/wms_adapter tests/api/test_wms_events.py tests/runtime/wms_diagnostics -q`；补充任务 1 实际发现的直接/间接 owner，不将此命令自动当成最终完整门禁。

## 任务 4：SSE、只读查询、权限与跨进程证据

**文件：** `v1/exchanges.py`、包入口和 `src/register.py`；新增 `tests/api/test_wms_diagnostics.py`、`tests/integration/wms_diagnostics/test_production_wiring.py`；同步精确 HEAVY mapping。

**API：** `GET /api/v1/wms-diagnostics/exchanges/stream`、`GET /api/v1/wms-diagnostics/exchanges` 与 `GET /api/v1/wms-diagnostics/exchanges/{exchange_id}`。stream 静态路由优先于动态详情路由。列表、详情、实时流分别使用 `ops:wms-diagnostics:query`、`ops:wms-diagnostics:read`、`ops:wms-diagnostics:stream` 权限；所有响应与错误封装沿用项目惯例。列表接收时间范围、direction、operation、operation_id、business_reference、异常筛选、cursor、page_size；详情过期为 404，存储不可用为 503，非法过滤参数为 422，未授权按现有 401/403 语义。

- [ ] RED：ASGI 验证只读权限、分页约束、过滤、详情不存在、Redis 不可用、列表不含正文及 OpenAPI schema；没有权限的账号不能通过直接 URL 查看详情。
- [ ] DEV：只从 Service 查询；通过现有权限生成/种子机制发布权限定义，不给工程人员自动分配超级管理员，也不直接修改现场账号。
- [ ] GREEN：真实隔离 Redis + API + Celery worker 验证 worker 出站记录可在 API 查询、API 入站记录刷新后仍可读，进程重启不会凭空生成历史记录。
- [ ] 验证整个诊断链路故障下当次业务响应、Evidence 和重试策略不变：FAST 逐阶段故障注入，真实 API/worker 验证代表性的存储前处理异常与 Redis 超时。测试必须运行真实 worker 才能声称 worker wiring 已验证。依赖未准备不能以 skip 充当通过。
- [ ] 为新增模块及共享修改按实际影响填写 HEAVY mapping；映射已有 WMS/Transport 事务测试，不扩为全量 HEAVY。
- [ ] 更新 Client 合同说明纯观察结果边界，以及配置索引对唯一入口的引用。

- [ ] RED/DEV：复用 EventStreamService 与现有认证 SSE 客户端，新增 WMS 严格事件 DTO 与频道；共享传输不导入 WMS/插件模块，WMS 层提供事件内容。公共层用无业务语义 fixture 独立验证，WMS 测试只证明映射与接线。
- [ ] **D1 已接受，公共 SSE 回归：** `EventStreamService.subscribe` 在 Redis 订阅异常时结束并使对应 SSE 连接关闭；禁止异常分支 `yield None` 继续伪装心跳。正常无消息才产生心跳，主动取消及时清理。先枚举所有 subscribe 消费者，保持设备/运输等原事件合同；不增加业务专属故障事件或恢复状态机。
- [ ] D1 验证 owner：`tests/sys/test_event_stream_service.py` 测试正常空闲、建立失败、建立后 Redis 中断、取消和清理；现有各 SSE API 测试验证响应流结束。前端复用 `tests/unit/api/authenticatedSseConnection.test.ts` 验证 EOF/异常触发 onGap 与重连，不再显示 CONNECTED；WMS 页面测试间隙文案，真实 Redis/代理测试故障恢复与其他订阅者资源释放。
- [ ] RED/DEV：每个事件完整编码不超过 32 KiB，预览合计不超过 16 KiB，先脱敏后截断；一次尝试的两次发布与历史写入使用 D2 固定份额，总计不超过默认 100 ms，发布器既有默认超时不得另行叠加。取消立即传播，订阅者不能反压业务执行。
- [ ] **D2 已接受：** 默认开始阶段最多 10 ms、完成整理/存储最多 60 ms，最小完成摘要及发布预留 30 ms（总预算变更按固定比例计算）。历史保存超时不重新比较或序列化完整报文，改用已知安全身份/当次结果/完整性标记发布最小 completed；原业务异常与取消不改变。FAST 使用受控时钟模拟 started 耗尽、保存耗尽及发布超时，断言前序不得侵占完成份额；真实 Redis 验证保存路径超时但发布可用时仍尝试发布，记录实际耗时。
- [ ] RED/DEV：历史保存失败但发布可用时 completed 可显示且 exchange_id 为空；发布失败但保存成功可通过近期记录查询。任一路径都不得重发业务请求或建立可靠日志 outbox。
- [ ] RED/DEV：SSE 为 live-only，无无损回放承诺；开始事件丢失、孤立完成事件、客户端中途连接均有效。慢客户端发送阻塞 5 秒关闭，禁止无界队列。核实心跳、代理缓冲与连接关闭；只有公共层缺失这些边界时作最小补齐。
- [ ] GREEN：真实 API/worker/Redis、10 个 SSE 连接、每秒 10 次交互持续 60 秒；局域网 completed 到浏览器 p95 ≤ 500 ms，记录实际环境/事件大小/延迟；慢客户端与断网单独验证不会拖慢业务或其他连接。共同确认消息大小、单次诊断预算和连接释放，不将正常 Redis 可用当作端到端通过。

**完成条件：** 后端行为与可供前端消费的 API 合同就绪；前端尚未因此自动获得 canonical freeze 资格。

## 任务 5：前端实时控制台、比较详情与近期记录

**依赖：** 任务 4 的 DTO/API；正式 canonical 生成必须使用包含后端改动的干净 develop。未获 Commit/PR/Merge 授权前，该发布条件保持未完成；可以先实现不依赖生成物的展示组件与组件测试，不能手改 canonical 或伪造生成权限。

**文件：** 既定四个 Vue/TS 页面文件、`src/router/routes/ops.ts`、现有 API 模块/生成物；测试放 `tests/unit/views/ops/wms-diagnostics/`，必要的路由权限回归归 `tests/unit/router/permission-guard.test.ts` 和现有菜单 owner。

**接口约定：** `useWmsDiagnostics` 消费生成的 ExchangePage/ExchangeDetail API 类型，输出筛选、实时缓冲、SSE 连接/间隙状态、近期列表、详情、加载与错误状态；`FieldComparisonTable` 仅接受后端 FieldComparison 数据，不导入或实现 WMS 校验器。

- [ ] RED：列表筛选与分页、详情请求/响应切换、只看差异、缺失与 null、合法拒绝、未校验、截断、过期详情、复制失败提示。
- [ ] RED/DEV：状态标题与复制文案统一为“当次接口结果／采集时状态”，显示 observed_at；后台业务状态变化不修改历史快照。只链接已存在且当前账号可访问的业务详情；不新增业务状态轮询或 Redis 状态同步。
- [ ] RED/DEV：scan_incomplete 与空 items + next_cursor 显示“本批未找到匹配记录，可继续查询”，通过用户操作继续；近期记录模式断开 SSE，不自动遍历所有历史。回到实时模式重新连接并提示间隙，初次连接不自动加载历史，避免快照与订阅竞态。
- [ ] DEV：按设计的蓝灰色板、本机中文/等宽字体、紧凑日志行与固定详情区实现，复用现有 UI 组件；窄屏使用详情抽屉。顶部异常结论优先，详情展示预期规则/实际值/来源；报文以文本呈现，禁止 HTML 注入。复制内容包含完整性标记且复用已脱敏数据。
- [ ] RED/DEV：复用 authenticatedSseStream 处理认证、重连和取消；按 attempt_id + 阶段去重，同一交互以 started/completed 更新，迟到 started 不覆盖 completed。未收到完成事件显示“未观察到完成”，不能判为超时或业务失败。
- [ ] RED/DEV：暂停滚动仍有界接收，显示新增数量；清空视图不请求删除 API，不自动回灌历史。浏览器缓冲限制 500 条交互及 2 MiB 序列化事件，超限淘汰并提示，选中详情另限一条。批量渲染，消息不抢焦点；文字/图标与颜色共同区分合同错误、传输失败、合法拒绝。
- [ ] RED/DEV：隐藏、离开、切换近期模式或筛选时取消旧连接，筛选切换后旧连接事件不能混入新视图；重连显示断线间隙，连接建立不清除旧间隙提示。不新增轮询 fallback，自动重连不能宣称补齐日志。
- [ ] RED/DEV：拥有 query 或 stream 权限的普通工程账号可见菜单并能访问对应模式；详情另需 read 权限；无权限禁止直接访问；设备/运输等其他页面原权限保持一致。仅修改实际阻挡此页面的菜单/路由声明，不扩大全局守卫语义。
- [ ] 获得符合仓库规则的后端基线后执行 `pnpm contract:freeze -- --backend-root <明确的后端绝对路径>`，再运行 `pnpm generate:types`、`pnpm generate:zod`、`pnpm generate:permissions`；重复生成必须无差异。
- [ ] GREEN：聚焦 Vitest 后执行适用 `pnpm type:check`、`pnpm permission:verify`、`pnpm contract:test`、`pnpm contract:verify`；完整前端门禁在最终快照按项目要求运行一次。

**完成条件：** 页面实际接入后端，生成合同与权限一致；仅组件测试或静态画面不算功能交付。

## 任务 6：最终证据闭合与浏览器验收

- [ ] 固定后端/前端最终可执行快照，闭合 operation 矩阵、所有直接/间接测试、HEAVY owner、生成物和相关文档；运行 `git diff --check`。
- [ ] 使用唯一主 Review 流程；若修复生产代码，由同一 Reviewer 一轮同时复核旧意见与当前完整 diff，主 Agent 不重复并行全审。
- [ ] 后端执行 `./scripts/git-quality-gate.sh --profile quality` 和 `uv run scripts/select_heavy_tests.py --scope unstaged`，只运行对应 manifest。若随后授权提交，最终按 staged scope 验证并复用有效快照证据，不重复不失效的门禁。
- [ ] 新增 Redis 能力无需 Alembic；若实施意外引入 schema 变化先报告范围变化，不能默认新增迁移。
- [ ] 前端最终执行项目要求的 lint/test/build 与合同门禁；通过后才进入真实浏览器 QA。
- [ ] 使用后端唯一 `scripts/dev-env.sh` 编排入口，显式 WES_FRONTEND_ROOT 指向本任务前端；不得重指向或重建其他任务正在使用的共享实例，冲突时使用独立 Compose 项目、端口及数据目录。
- [ ] 浏览器用例：查询一个合法 inbound、一个字段错误 inbound、一个 outbound 非法响应、一个合法业务拒绝；逐条确认实际报文、规则来源、异常分类与复制内容。
- [ ] 浏览器验证 read-only 工程账号、无权限账号、记录过期、API/SSE 不可用、断线间隙、暂停滚动、清空视图、缓冲淘汰、孤立完成事件、隐藏恢复和离开页面连接取消；保存截图和命令结果。
- [ ] 最终报告区分后端实现、前端组件、正式合同接入、worker/浏览器验证、部署和 WMS 联合验收；存在未完成必选项时不声明全部完成。

## 计划自检与执行记录

覆盖关系：设计 1/2 → 任务 1；设计 3 → 任务 5/6；设计 4 → 任务 3；设计 5 → 任务 2/3/4；设计 6 → 任务 4/5；设计 7 → 任务 1/6；设计 8 → 任务 4 文档同步。

本次仅形成计划并标记设计已批准，未执行上述复选项。执行时把命令、结果、HEAD/可执行树指纹、环境与发现的具体影响清单追加到本节，绿色证据只在相关快照未变化时复用。

### 实施前评审修订（2026-09-07）

| 评审意见 | 本轮修订 | 实施验收归属 |
| --- | --- | --- |
| P1：收集与结束时机不确定 | 显式单次观察参数、唯一宿主 owner、Adapter 完成后结束一次、取消立即传播 | 任务 3 生命周期与并发测试 |
| P1：失败隔离仅覆盖 Redis | 整个诊断链路隔离、D2 分阶段预算、同步遍历硬上限、逐阶段故障注入 | 任务 3 FAST + 任务 4 实际 API/worker |
| P2：历史状态误作当前状态 | 采集时快照 + observed_at + 已有权威详情链接 | 任务 2 DTO + 任务 5 页面与复制 |
| P2：列表读取成本未闭合 | 预取前累计字节预算、未完成扫描语义、多查看者性能验收 | 任务 2 Redis + 任务 5 分页 |

本轮仅优化计划并同步设计边界；完成文档相称检查不代表独立复审 CLEAR，也不代表上述生产验收已经执行。任务 1 必须按实施时快照核对共享路径与其他工作，禁止覆盖无关内容。


### SSE 控制台范围确认（2026-09-07）

用户确认“双方日志”仅指 WES 观察到的请求、响应与校验日志。实时主流程改为 SSE 控制台，保留手动近期记录与字段对比；原定时刷新方案已直接替换，不保留兼容入口。单次历史记录不变，增加有界 started/completed 实时事件，补齐断线、慢客户端、消息大小、浏览器缓冲和跨进程验收。

本次原位更新设计和实施计划，两份仍为当前真源，不新增替代版本或重复副本，因而没有被替代的整份过程文档需要归档。此修订仅完成文档检查，尚未实施或独立复审 CLEAR。

### 当前工程评审决策

- D1：用户已接受公共订阅异常结束连接并复用重连/间隙提示；已同步设计及任务 4。尚未修改生产代码或执行回归；本轮完整结论见文末报告。
- D2：用户已接受固定预算份额并为 completed 预留时间；已同步设计、任务 3/4 和故障验证要求。


### 工程评审闭合（2026-09-07）

本轮按 plan-eng-review 完成架构、代码组织、测试计划、性能四节评审；前述“尚未复审”描述的是修订当时的阶段，本节及文末报告为当前结论。评审范围为本计划与对应设计，并核对已有 SSE 实现和测试入口；没有独立第二评审者，也没有运行生产验收。

| 评审维度 | 结论 |
| --- | --- |
| 范围 | 保持用户确认的 SSE 控制台、近期查询与字段对比 |
| 架构 | D1：公共订阅故障不能伪装心跳；已接受并写入任务 4 |
| 代码组织 | 未发现新增问题；复用 Client、正式 parser、公共 SSE 与认证客户端，基础层不依赖插件 |
| 测试计划 | 未发现新增缺口；D1/D2 的测试要求已纳入原任务，以下覆盖关系均待实施 |
| 性能 | D2：为 completed 预留固定份额；已接受并写入任务 3/4，延迟目标待实测 |

**已存在、应复用：** EventStreamService、authenticatedSseStream、正式 operation DTO/parser、WmsConfirmation/InboundEvidence、权限及前端合同生成机制。可靠业务证据继续由原能力负责。

**不在范围：** WMS 内部日志（无该数据源）；日志无损回放/outbox（诊断不承担可靠义务）；重发与业务状态修改（只读诊断）；摘要索引/第二套正文存储（先验证单 Stream）；兼容路径及通用日志平台（无已确认需求）。未新增延期 TODO。

**计划测试覆盖图（不是通过证据）：**

```text
入站 Event / 出站宿主 owner
  ├─ Client、Adapter、parser → WMS FAST 合同：方向、校验、身份、取消、原业务结果
  ├─ 比较、脱敏、预算 → diagnostics FAST：异常注入、大小/遍历边界、D2 份额
  ├─ Redis 历史 → 独立 Redis 集成：保存、裁剪、过期、分页、失败语义
  └─ 公共 SSE → sys FAST + 既有消费者回归：D1 故障、空闲、清理
       └─ API/worker/Redis/代理 → HEAVY：跨进程、故障恢复、慢连接、延迟
            └─ 认证客户端/页面 → Vitest + 浏览器：间隙、权限、对比、缓冲、复制
```

| 现实故障 | 已约定行为 | 验收任务 |
| --- | --- | --- |
| Redis 订阅中断但浏览器网络正常 | 关闭 SSE，前端显示间隙并重连 | 4/5：D1 公共及消费者回归 |
| started 或历史保存耗尽份额 | 不侵占 completed 份额；可用时尝试最小摘要发布 | 3/4：D2 时钟及真实 Redis 故障注入 |
| 比较/脱敏失败或原请求取消 | 安全降级或立即取消，不改 ACK、Evidence、重试 | 3/4：逐阶段与原业务回归 |
| 浏览器断线、慢读、缓冲淘汰 | 有界关闭/淘汰，明确间隙，不推定业务失败 | 4/5/6 |
| 历史过期、稀疏匹配、读取失败 | 区分 404/503 与可继续扫描，不伪装空结果 | 2/4/5 |

**执行依赖：** 一个主实施 owner 顺序推进任务 1 → 2 → 3 → 4 → 5 → 6；独立只读检查及 FAST 可批量执行。共享 Client、SSE、生成物不并行写入。前端 canonical 仍依赖符合仓库规则的后端基线，不以本次评审代替合入授权。

### Implementation Tasks

以下只列本轮两项发现对应的实施工作，已合入上述任务，不另建平行实施清单。工时为粗估，实际受共享消费者范围影响。

- [ ] **D1（P1；人工约 2–4 小时 / Agent 约 30–60 分钟）**：修改公共订阅异常退出语义；来源为架构评审，`src/app/sys/services/event_stream_service.py:97` 的异常分支仍 `yield None`。验证归任务 4，覆盖公共服务、全部 SSE 消费者及认证客户端 EOF/异常重连。
- [ ] **D2（P2；人工约 2–4 小时 / Agent 约 30–60 分钟）**：实现 10%/60%/30% 诊断预算与最小完成摘要；来源为性能评审，原共享预算无法保证完成发布获得执行机会。验证归任务 3/4，包括前序耗尽、保存失败、发布超时与取消。

代码组织与测试计划未产生额外实施任务。两项建议均已接受，未决设计选择 0，未覆盖关键故障 0；代码修复和测试执行均未完成。Outside voice 未运行；不声称跨模型或独立复审通过。

### 实施启动与共享影响范围（2026-09-07）

状态：用户已确认本节 HIGH 风险范围及两项后续修正，按 Execution Lock 实施。后端已形成可审查实现并完成聚焦验证；任务 5–6 未完成。当前证据见 2026-09-08 更新，聚焦绿灯不代表整项功能完成或现场验收。

### 当前实施记录与未决项

- 后端工作树已落地纯观察合同、DTO/原校验结果采集、脱敏及有界 Redis 近期记录、诊断 API/SSE。WmsConfirmation、Transport 提交和 Picking/Transport 入站已经显式传递单次观察；业务原事务、身份、ACK 和取消行为由原调用者继续拥有。
- 纯观察合同位于 `src/app/wms_diagnostics/observation.py`。首次接入暴露 Transport Service 反向依赖 Adapter 的架构回归，已迁移全部消费者并保留原架构断言，没有兼容导出。
- 配置由 `DiagnosticsConfig` 从环境或运行时 `.env` 加载；API lifecycle 与 worker 同步启动门禁校验，不创建新的异步资源。必要 header 使用白名单；合法拒绝与合同异常分别记录。成功联合 DTO 使用原校验实际返回的模型作为比较依据，不重新选择或验证分支。
- 真实 Redis 测试发现外层流关闭未同步释放内层订阅；已使用显式关闭修复，慢发送超时后订阅数归零。
- 前端隔离目录为 `/Users/kaizhou/codeDev/wes_frontend-worktrees/codex-wms-integration-diagnostics`，同名分支从 `origin/develop` 的 `77618cc627d7cc330f411f2c1c1295e6465a7ce2` 创建，独立安装 frozen lockfile 依赖。当前只实现不依赖 canonical 的 `FieldComparisonTable.vue` 与测试，尚未接入页面、路由或 API。

**实施中确认的合同修正（用户已接受，两项均纳入 Execution Lock）：**

1. `inbound.execution.recovery_decided@v1` 的实际校验仍由 `rough_sorter` 插件拥有，宿主没有其结构化校验日志。本轮只观察 WIRE/响应并显示宿主已有正式合同，明确“未取得校验日志”；将 handler 基础化作为独立重构。未擅自迁移插件逻辑。
2. `scripts/data/sync_permissions.py --preview` 实际失败：一个 `ops:wms-diagnostics:read` 对应三个不同路径，违反当前权限目录唯一性合同。本轮分别使用 `ops:wms-diagnostics:query`、`ops:wms-diagnostics:read`、`ops:wms-diagnostics:stream`，沿用现有角色授权和一个前端菜单。按本次确认替换权限约定，不放宽 scanner。

**聚焦证据：**

- WMS Adapter 合同、诊断基础、Transport、Confirmation dispatch、入站/诊断 API、SSE、worker 启动及测试所有权：`1496 passed`；两条 warning 来自已有多线程进程 `os.fork()` 测试。随后将诊断 operation 字面量替换为既有 `SUBMIT_OPERATION`，定向 Transport 回归 `360 passed`。
- 独占 Redis 8，`INTEGRATION_REDIS_URL=redis://127.0.0.1:63510/0`：`tests/integration/wms_diagnostics/test_exchange_store.py` 五项通过，覆盖裁剪/过期/游标、独立 Python 进程收发与历史关联、慢 SSE 发送关闭后订阅释放。只使用 UUID 测试键；不接触共享开发 Redis/数据库。这不是 Celery 生产接线或浏览器性能证据。
- 新诊断模块与通用 SSE response 的 basedpyright 无错误；扩展检查旧生产模块发现 12 项错误，均在主仓库相同基线源码复现，未顺手修复。
- 变更 Python 文件 Ruff 通过；前端组件两项 Vitest、`pnpm type:check`、定向 ESLint/Stylelint/Prettier 通过。两仓 `git diff --check` 通过。HEAVY selector 已生成 manifest，未执行整组 HEAVY。
- 上一阶段可执行变更指纹（已被下节新快照替代，不再作为当前证据）：后端 78 文件 `f58062208b3781ba8809dde2581cc13edbfa5ce1b737d62337eceda38642ea20`；前端 2 文件 `9614e0a780c5dd3fcf40725063e0e07f862f8a56c0a9b276680a268010a0b9a4`。后端包括本任务 Python 和 HEAVY TOML，前端包括本任务 TS/Vue；不含人类文档。

**后续验收：** 全部 operation 采集矩阵与预算边界审查、实时筛选语义、普通工程账号权限实测、真实 worker/代理/多连接性能、控制台完整页面、canonical 冻结与正式接入、唯一主 Review、最终 QUALITY/选中 HEAVY 和浏览器 QA。未 Commit、Push、PR、Merge 或 Deploy。主仓库原文件未被覆盖；GitNexus 自动生成的六份技能副本已从本任务工作树移除。

- 后端隔离目录：`/Users/kaizhou/codeDev/wes_backend-worktrees/codex-wms-integration-diagnostics`；分支 `codex/wms-integration-diagnostics`；base/head `92ffdbaf4fbeb263f6b24f3f3a9fb8915e14d56f`，来自已 fetch 的 origin/develop。
- 前端目录：`/Users/kaizhou/codeDev/wes_frontend`；HEAD `77618cc627d7cc330f411f2c1c1295e6465a7ce2`；启动时 status 为空。前端尚未写入，正式生成仍遵守干净后端 develop 条件。
- 主仓库仅两份本任务 untracked 文档；原内容保留。隔离目录已复制批准文档、独立初始化 `.env`/`.venv`，未启动数据库、Redis、worker 或 Compose。
- `./scripts/init-env.sh dev`、`uv sync --dev` 成功；不连接生成配置默认指向的共享数据库，HEAVY 使用任务独占环境。
- `npx gitnexus status` 显示未索引，执行一次 `npx gitnexus analyze` 成功；工具生成的 AGENTS.md/CLAUDE.md 改动已与原 HEAD/主仓库比对后恢复。
- 已请求 WmsClient、receive_json、receive_wms_event、_dispatch_claimed、subscribe 的 upstream impact；MCP 因 LadybugDB 存储版本 43/40 不兼容失败。UNKNOWN 不作为低风险，按精确调用点/测试/合同补查，整体判为 LARGE/HIGH-RISK。
- 聚焦基线：`uv run pytest tests/sys/test_event_stream_service.py tests/contracts/wms_adapter/test_client.py -q`，92 passed，0.46 s；Python 3.13.14，pytest 9.0.3。仅证明改动前 Client/SSE FAST 基线，不是新功能验收。

**冻结的共享修改范围（确认后本范围同类风险不重复询问）：**

| 共享路径/符号 | 直接与间接消费者 | 必须保持的语义 |
| --- | --- | --- |
| WmsClient.request/post/post_json_bytes、receive_json；各域 Adapter 的 dispatch/send 和 WmsTransportAdapter.submit | InboundMaterialAdapter、10 个 outbound_picking Adapter、WmsConfirmationAdapter、TransportService.submit_pending_tasks | 单次发送、原冻结 payload/digest、原取消/交付状态；新增观察不改变业务合同 |
| WmsConfirmationService._dispatch_claimed、WmsConfirmationAdapterPort、TransportService.submit_pending_tasks | API/worker 宿主装配、execution/composition.py、transport/composition.py、deployment/plugin_composition.py 及对应测试替身 | owner 明确；领取/事务/重试/围栏不变；观察完成后继续原结果处理 |
| receive_wms_event/_receive_wms_event 及静态 parser/handler 的观察传递 | 入站 Transport 两类事件、恢复回调、Picking issued/plan_delta/queue_changed | 唯一 Event 路由、原 ACK 提交顺序、Evidence/幂等/内容冲突不变 |
| EventStreamService.subscribe/publish_to 与现有响应流边界 | sys/v1/events.py、device/v1/evidence_stream.py、transport/v1/evidence_stream.py、transport/v1/debug_runs.py；前端共享认证 SSE | D1 故障退出影响全部消费者；正常心跳、权限与业务事件合同保持 |
| 新诊断模块、register、权限和前端 ops 页面/菜单 | 新 read API、SSE 与现有合同/权限生成器 | 基础层不依赖插件；普通工程账号分别按 query/read/stream 权限访问；不放宽其他页面 |

**测试与生成物边界：** Client/dispatch 与 operation 接入归 `tests/contracts/wms_adapter/`；可靠义务归 `tests/runtime/execution/test_wms_confirmation_dispatch.py`、`test_wms_confirmation_service.py`，Transport 归 `tests/runtime/transport/` 及其 conftest；公共 SSE 归 `tests/sys/test_event_stream_service.py`，响应流归现有 device/transport API 测试及 debug runs 消费者。fixture 传播包含 inbound_material/support.py、outbound_picking/confirmation_support.py 与直接 Adapter/Client 替身。实施相应切片前完成精确符号签名和替身残留核对，尚未闭合的切片不得宣称最终覆盖。

HEAVY 复用当前 `heavy-test-impact.toml` 对 WMS 各域生产 wiring/事务、Transport 和 Device e2e 的已有映射；新增 diagnostics Redis/worker/代理资产须增加精确 mapping，最终只执行 selector manifest。无数据库模型变化和 Alembic migration；前端生成物仅按批准流程生成。SDK/插件业务实现、规范化算法与摘要身份不是本次重构对象，不增加兼容 wrapper。无废弃整份文档需归档，hardware 保留。

**生产 owner 核查证据：** `wms_confirmation_service.py:469` 调用 Adapter；`transport/service.py:625` 在原 HTTP 超时边界调用 provider；`deployment/plugin_composition.py:102` 装配 WmsConfirmationAdapter。Transport 诊断预算必须置于原 HTTP 超时边界之外，不能使新增诊断消耗原物理提交超时并改变 DELIVERY_UNKNOWN 判定。

**当前静态 wire 身份核对结果：** 下表来自当前源码，不以旧 CSV 未列出新 Picking operations 为漏采依据；唯一静态分派与各域合同仍是实施来源。

| Operation | 声明文件 |
| --- | --- |
| `inbound.execution.recovery_decided@v1` | `src/app/wms_adapter/inbound_material/wire.py` |
| `inbound.material.admission_decide@v1` | `src/app/wms_adapter/inbound_material/wire.py` |
| `inbound.material.ng_placement_report@v1` | `src/app/wms_adapter/inbound_material/wire.py` |
| `inbound.material.placement_report@v1` | `src/app/wms_adapter/inbound_material/wire.py` |
| `inbound.material.target_decide@v1` | `src/app/wms_adapter/inbound_material/wire.py` |
| `inbound.source_rack.replacement_plan_decide@v1` | `src/app/wms_adapter/inbound_material/wire.py` |
| `outbound.bin.inbound_batch@v1` | `src/app/wms_adapter/outbound_picking/inbound_batch_wire.py` |
| `outbound.bin.return_batch@v1` | `src/app/wms_adapter/outbound_picking/return_batch_wire.py` |
| `outbound.bin.work_plan@v1` | `src/app/wms_adapter/outbound_picking/work_plan_wire.py` |
| `outbound.material.decide@v1` | `src/app/wms_adapter/outbound_picking/material_decide_wire.py` |
| `outbound.material.movement_report@v1` | `src/app/wms_adapter/outbound_picking/movement_report_wire.py` |
| `outbound.picking_task.completion_confirm@v1` | `src/app/wms_adapter/outbound_picking/completion_confirm_wire.py` |
| `outbound.picking_task.issued@v1` | `src/app/wms_adapter/outbound_picking/wire.py` |
| `outbound.picking_task.plan_delta@v1` | `src/app/wms_adapter/outbound_picking/plan_delta_wire.py` |
| `outbound.picking_task.prepare@v1` | `src/app/wms_adapter/outbound_picking/wire.py` |
| `outbound.picking_task.queue_changed@v1` | `src/app/wms_adapter/outbound_picking/queue_changed_wire.py` |
| `outbound.rack.departure_decide@v1` | `src/app/wms_adapter/outbound_picking/departure_wire.py` |
| `outbound.return_rack.arrival_report@v1` | `src/app/wms_adapter/outbound_picking/arrival_report_wire.py` |
| `outbound.source.empty_decide@v1` | `src/app/wms_adapter/outbound_picking/source_empty_wire.py` |

Transport 的 submit/position/result 身份通过其已批准合同及 SDK 常量单独核对；表格仅列 wire 本地字面量声明，不代表未列出的 import 常量不存在。

## 2026-09-08 实施更新：已接受修正与当前证据

本节替代上文实施阶段的当前状态描述；计划工程评审结论与历史基线仍按各自阶段理解。

- Recovery handler 保持插件所有权；宿主只采集可见请求、响应，并引用现有 Recovery schema；没有取得插件校验日志时明确 NOT_VALIDATED。
- 查询、详情、SSE 分别声明 query/read/stream 权限；权限扫描器通过。ASGI 测试使用真实 RBAC 依赖验证普通工程身份的授权/拒绝，未把依赖替身称为真实 JWT 账号或浏览器验收。
- DTO 后的 identity、版本、FIFO 和冻结货架约束复用原判断记录失败；显示已有校验器的路径、原因和可取得的冻结预期值。冻结请求不匹配、响应头/正文异常增加技术错误标记，不改变原返回值、重试和 ACK。
- 已成功校验的嵌套 discriminator 按正式 schema 的 mapping 展开实际分支，不试验候选分支；未校验时不推断分支。无 HTTP 响应与空响应区分。
- 前端已实现 FieldComparisonTable 和 ExchangeDetail 两个展示组件，覆盖缺失/null、未校验、文本转义、请求/响应切换、采集时状态、未保存说明、只看差异选择和复制失败。详情通过 slots 接入已脱敏展示，尚无正式页面、API 调用或手写 canonical 类型。

**验证快照：** 仅包含本任务可执行文件，按排序的相对路径 + NUL + 正文 + NUL 计算 SHA-256。

| 仓库 | 文件数 | SHA-256 |
| --- | --- | --- |
| 后端 | 79 | `4ff2d2f1fef7cfeab93cc74b3823dfa2ffef51828090c41a424e96dbd72ed926` |
| 前端 | 4 | `9d79624cc95ab6452460f8a90c21502fb4c7e19e7912f42516ac5db2730f9020` |

后端聚焦命令：`uv run pytest tests/contracts/wms_adapter tests/api/test_wms_events.py tests/api/test_wms_diagnostics.py tests/runtime/wms_diagnostics tests/runtime/transport tests/runtime/execution/test_wms_confirmation_dispatch.py tests/runtime/execution/test_wms_confirmation_service.py tests/sys/test_event_stream_service.py tests/core/test_sse_response.py tests/deployment/test_execution_worker_startup.py tests/api/test_device_evidence_stream.py tests/api/test_transport_evidence_stream.py tests/api/test_transport_debug_runs.py -q`，1524 passed；7 个既有 fork/HTTP 422 弃用警告。报告：`/tmp/wms-diagnostics-focused-final.txt`。

真实 Redis 和 worker 使用本任务独占 `codex-wms-diagnostics-heavy` 环境，复用现有完成确认 production wiring owner，命令为 `uv run pytest tests/integration/wms_diagnostics/test_exchange_store.py tests/integration/wms_adapter/outbound_picking/test_completion_confirm_production_wiring.py -q`。最终快照 11 passed，21.65 s，报告 `/tmp/wms-diagnostics-real-final.txt`。没有新数据库模型或 migration。

近期查询性能使用独占 Redis 8、4 组历史最大尺寸 fixture。每组 10 个查看者持续 60 秒，每人每 5 秒查询一次，共 120 次请求；随后完整遍历稀疏匹配游标验证无遗漏或重复。测试只模拟查看负载，未给产品增加轮询。结果如下：

| 记录数 | 单条尺寸 | p95 | 超时/错误 | Redis 该键内存 |
| --- | --- | --- | --- | --- |
| 1000 | 32 KiB | 41.15 ms | 0 | 32,835,047 B |
| 1000 | 64 KiB | 72.12 ms | 0 | 65,607,535 B |
| 10000 | 32 KiB | 37.37 ms | 0 | 328,348,063 B |
| 10000 | 64 KiB | 86.42 ms | 0 | 656,069,375 B |

各次预取满足 2 MiB 边界，响应满足 256 KiB 边界；稀疏首批返回空 items 和可继续游标。临时 fixture 键已清除。命令：`uv run python -c 'import runpy; runpy.run_path("/tmp/wms-diagnostics-history-perf.py")'`；原始报告 `/tmp/wms-diagnostics-history-perf.json`。该证据覆盖 Repository/Service 的局部性能，不能替代真实 API/代理/浏览器的 SSE 到达延迟验收。

**Review 状态：** 主 Agent 按 review 清单审查当前切片，已修复发送前异常标记、公共收发异常标记、嵌套已校验分支规则缺失；不声称独立第二意见。29 个 Adapter/parser/handler 文件在剥离观察写入、参数传播及原校验调用包装后，与 base 的业务 AST 一致；Transport handler、3 个采集 owner、共享 SSE、配置和 API 单独核对。当前后端切片及两个前端展示组件未发现尚未关闭的代码问题；这不是完整功能的 Review CLEAR。正式前端接入后的跨仓 Review 与最终门禁仍待闭合。

**明确未完成：** 正式 canonical 冻结和 API/权限生成、前端页面与有界 SSE 缓冲、菜单/路由、真实账号浏览器 QA、API/代理/SSE 多连接到达延迟、最终 QUALITY 和 selector 选中的完整 HEAVY。依据 wes-implementation，未进入已授权 Commit/PR 边界前不提前重复完整门禁。后端本次 selector 输出 37 个 HEAVY 文件，保留 `/tmp/wms-diagnostics-heavy-selection-current.txt` 供最终快照复核；聚焦 11 项不替代该 manifest。

用户随后已授权后端 Commit、Push、创建 PR 并在审查与必选门禁通过后合入 develop，再继续正式前端接入；尚未执行合入，不包含部署授权。任务 5 仍要求含后端变更的干净 develop 作为正式 canonical 来源。主仓库和其他本地服务未被重指向或覆盖。

后端交付验证补齐了权限节点/角色关联的精确数量和共享观察参数在集成测试替身中的机械传播；原业务断言保留。生产 worker 诊断接线复用 `outbound_picking/test_completion_confirm_production_wiring.py`，不再新增重复的 `wms_diagnostics/test_production_wiring.py`。

最终后端 staged HEAVY manifest 包含 39 个文件；在独占 PostgreSQL 干净逻辑库迁移到 head、独占 Redis 环境执行 `uv run scripts/run_selected_heavy_tests.py <manifest> <junit>`：**282 passed、零跳过，293.48 s**。报告 `/tmp/wms-diagnostics-heavy-final4.xml`；此前失败运行不作通过证据。覆盖可执行差异 SHA-256 `052bda9c8dd4cc34bd5ee23ad12292f08841623660ccde7717bffe18f8472aff`，83 个生产/测试/机器配置文件，清单 `/tmp/wms-diagnostics-backend-final-manifest.json`。GitNexus staged 检查成功，critical 影响仍在已批准共享 Client/Event/SSE/宿主装配范围。新增测试传播与文档差异经主 Agent 定向复核，未改变原业务断言；完整 QUALITY 由提交 hook 执行。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | /plan-ceo-review | 范围与策略 | 0 | 本范围未运行 | 不引用其他任务结果 |
| Codex Review | /codex review | 独立第二意见 | 0 | SKIPPED | 本轮无独立评审 |
| Eng Review | /plan-eng-review | 架构、组织、测试、性能 | 1 | CLEAR (PLAN) | 2 项建议接受并写入；未决 0 |
| Design Review | /plan-design-review | 专项 UI/UX 评审 | 0 | 本范围未运行 | frontend-design 指导不等同于此评审 |
| DX Review | /plan-devex-review | 专项开发体验评审 | 0 | 本范围未运行 | 不增加额外门禁 |

**VERDICT:** ENG CLEARED。当前设计与实施计划通过本轮工程评审；可按任务 1 冻结实施现场。仅文档检查完成，不代表代码已实现、测试通过、可合并、已部署或 WMS 联合验收通过。

NO UNRESOLVED DECISIONS
