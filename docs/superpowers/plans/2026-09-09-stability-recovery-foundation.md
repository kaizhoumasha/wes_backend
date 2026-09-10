# 基础可靠执行与事实查询 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用现有可靠对象提供可诊断、可验证的中断恢复能力，不依赖具体工作线业务。

**Architecture:** 保留既有 Transport/DeviceCommand/WmsConfirmation/InboundEvidence 及提交后唤醒和 Beat 兜底。扩展原 Transport 详情，新增窄的执行事实只读查询；不增加恢复表、通用恢复状态或业务 dispatcher。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、SQLAlchemy、PostgreSQL、Redis、Celery、pytest。

**Spec:** [总计划需求 R1/R3/R4/R7](2026-09-09-stability-recovery-master.md)；`docs/contracts/transport-fulfillment-contract.md`；`docs/architecture/device-command-contract.md`；`docs/contracts/wms-async-callback-envelope-contract.md`。

## Global Constraints

- 总计划全部红线与验证门禁适用；本文件已随总计划获批，按切片实施；运行参数的现场适配仍独立验收。
- 基础查询不访问具体插件表，不触发 HTTP、重试、清理或状态转换；零业务插件安装为必测部署形态。
- 不创建新 schema；使用现有字段和外键。发现必须改 schema 时先报告缺失事实，不临时加入冗余持久化字段。
- 不新增启动恢复协调器；先验证现有扫描器，只有失败证据证明缺口才最小修改对应 owner。
- 本文列出行为、接口与验收案例，不粘贴实现函数或测试正文；遵循仓库计划文档规则。

## 文件与所有权

| 单元 | 生产 owner | 测试 owner |
| --- | --- | --- |
| A1 deadline | `src/core/conf.py`；Transport Service/Composition | 现有 settings、Transport Service/Composition 测试 |
| A2 Transport 详情 | 原 Service、Repository、`v1/tasks.py` | 原 Transport API、领域与 PostgreSQL 测试 |
| A3 可靠事实查询 | execution 内窄 query service/repository；独立只读 route | execution 领域/API/数据库测试 |
| A4 崩溃验证 | 既有任务扫描器、事务唤醒、结果发布 | 真实 worker 支撑与领域 HEAVY；不导入具体插件 |

### Task A1：统一结果等待期限与唯一配置

**Files:**
- Modify: `src/core/conf.py`、`src/app/transport/service.py`、`src/app/transport/composition.py`。
- Test: `tests/core/test_settings_environment_precedence.py`、`tests/runtime/transport/test_transport_service.py`、`tests/runtime/transport/test_transport_composition.py`、`tests/runtime/transport/test_transport_acceptance_edges.py`。
- Docs/config: `docs/contracts/transport-fulfillment-contract.md`、`docs/devops/configuration-index.md`、`docs/architecture/heavy-test-impact.toml`。

**Interfaces:** Settings 新增 `TRANSPORT_RESULT_TIMEOUT_SECONDS: int`，默认 1200、合法范围 60–86400 秒；此范围是明确的首版运维输入限制，非物理安全阈值。Composition 读取并传入 `TransportService.__init__(..., result_timeout: timedelta)` 必需参数；测试构造处显式提供值，不保留旧签名 wrapper。位置事实 helper 改为 `_accept_position_fact(task: TransportTask, now: datetime, result_timeout: timedelta) -> None`，两个调用处显式传入相同配置。同步 ACK 与先到位置事实共享传入值，继续冻结已有 `result_deadline_at`。

- [x] 枚举 `TransportService(` 全部构造者、两个 deadline 写入点及各自 helper；冻结接口传播清单和 upstream impact。读取当前 deadline 合同与 Settings 校验模式。
- [x] 建立 RED：默认配置/覆盖/上下界；首次同步 ACK、先到位置事实两条路径获得相同窗口；重复 ACK/位置事件不延期；配置变更不改写已有 deadline；先到确定结果无需 deadline；超时仍 UNKNOWN、锁保留、迟到合法结果闭合。
- [x] 运行上述四个聚焦文件并核对失败原因。不要通过把旧 10 分钟断言全部机械改为 20 分钟掩盖冻结语义。
- [x] 最小接线 Settings → Composition → Service，删除 `_RESULT_TIMEOUT` 的硬编码定义，批量迁移调用者；不改 HTTP 10 秒、发送次数、claim、设备完成窗口或插件参数。
- [x] 合同将窗口来源指向唯一配置入口，保留冻结与 UNKNOWN 语义；配置索引只写入口和生效方式，不复制默认值。配置仅在进程重启后影响新冻结期限。
- [x] 运行 `uv run pytest tests/core/test_settings_environment_precedence.py tests/runtime/transport/test_transport_service.py tests/runtime/transport/test_transport_composition.py tests/runtime/transport/test_transport_acceptance_edges.py -q`；扫描旧常量及全部构造处残留，闭合 HEAVY mapping。

**验收：** 代码/配置/合同不再各持一套默认值。C1 的配置生效验收负责核对实际消费进程加载值、新任务首次 ACCEPTED 的冻结 deadline，以及配置变更后原任务 deadline 不变；路线耗时适配由 C1 中另列的现场运行负责人确认。两项分别记录，配置生效或测试通过不能证明 20 分钟适合所有路线。

### Task A2：扩展现有 Transport 详情，呈现执行与发布事实

**Files:**
- Modify: `src/app/transport/service.py`、`src/app/transport/repository.py`、`src/app/transport/v1/tasks.py`。
- Test: `tests/api/test_transport_tasks.py`、`tests/runtime/transport/test_transport_observability.py`。
- Reuse: `tests/integration/transport/test_transport_repository.py` 的 coherent snapshot 数据库测试。
- Docs/config: `docs/contracts/transport-fulfillment-contract.md`、`docs/architecture/heavy-test-impact.toml`；按原导出流程刷新 OpenAPI。

**Interfaces:** 保留 `TransportService.get_task_snapshot(transport_task_id: str) -> TransportTaskSnapshot` 与原 GET `/api/v1/transport/tasks/{transport_task_id}`。扩展原详情 DTO：`send_started_at: str | None`、`result_deadline_at: str | None`、`submit_attempt_count: int`、`outcome_version: int`、`published_outcome_version: int`、`pending_evidence_count: int`、`active_binding_count: int`。时间使用 UTC ISO；未知时间保持 null，不推算“设备已开始/已到位”。

补充持久化拒绝查询：新增 `TransportService.get_callback_receipt_snapshot(operation: str, operation_id: str) -> TransportCallbackReceiptSnapshot | None`，复用原 Repository `get_callback_receipt(..., for_update=False)`；GET `/api/v1/transport/callback-receipts?operation=...&operation_id=...` 使用已批准独立 `ops:transport-callback-receipt:read` 权限。`TransportCallbackReceiptSnapshot` 在原 service 定义、API 在原 tasks.py 映射：operation/operation_id 为 str、response_http_status 为 int、response_code 为 str、response_data 为原 `response_data_json` 字典、received_at 为 UTC str、conflict_code 为 str/null。无记录 404，不执行重新解析、回放或修改。

- [x] 用原 Repository 的 `get_task_with_latest_evidence`、成员/绑定模型核对字段来源；计数使用有界聚合，详情一个 statement snapshot，避免将不同时刻状态拼成已闭合结论。
- [x] 建立 RED：未发送；ACCEPTED 等结果；Evidence 待应用；结果已形成未发布；已发布；未知结果仍占资源。API 测响应类型，领域测字段映射，PostgreSQL 测聚合与一致快照，不三处重复整套矩阵。
- [x] 补充拒绝收据案例：非法结果已持久拒绝但没有 TransportEvidence，仍可按回调身份看到 `REJECTED/INVALID_EVIDENCE`；Redis 不可用不影响查询。只能显示原收据保存的错误细度，不能将当前解析器推断伪称历史错误详情。
- [x] 扩展现有详情路径；列表仍轻量，不逐条拼完整详情；不新建全历史追踪表，不复制原始大报文到新字段。
- [x] 明确字段局限：`latest_evidence` 是最新一条，不能证明没有更早待处理/冲突事实；计数辅助定位，结果权威仍是任务与匹配 Evidence。
- [x] 运行 `uv run pytest tests/api/test_transport_tasks.py tests/runtime/transport/test_transport_observability.py -q`；在已就绪隔离数据库运行新增 PostgreSQL 测试并映射到 selector。
- [ ] 确认 GET 不修改任务、不访问 WMS/ECS、不加载插件；刷新合同生成物并交付 B1。

**验收：** 仅靠任务 ID 即能区分未发、等外部结果、等应用、等发布；不把“已发布”当作业务已消费。

被拒绝报文不一定有可信的 task 关联，因此通过发送方提供的 operation/operation_id 精确定位收据；不得仅按时间接近或报文中的未验证 task 字段自动关联到执行任务。

### Task A3：从持久化执行对象查询 WMS 接收与发送状态

**Files:**
- Create: `src/app/execution/services/execution_observation_service.py`、`src/app/execution/repositories/execution_observation_repository.py`、`src/app/execution/observation.py`。
- Create: `src/app/wms_diagnostics/v1/execution.py`。
- Modify: `src/app/execution/services/__init__.py`、`src/app/execution/repositories/__init__.py`、`src/app/wms_diagnostics/v1/__init__.py`；`src/register.py` 原装配足够，复用不修改。
- Create: `tests/runtime/execution/test_execution_observation_service.py`、`tests/api/test_execution_observation.py`、`tests/integration/execution/test_execution_observation_postgresql.py`。
- Docs/config: `docs/architecture/file_index.md`、`docs/architecture/heavy-test-impact.toml`；按原流程刷新 API/权限合同。

**Interfaces:**
- `ExecutionObservationService.get_confirmation(operation: str, operation_id: str) -> ConfirmationObservation | None`。
- `ExecutionObservationService.get_evidence(operation: str, operation_id: str) -> EvidenceObservation | None`。
- `ConfirmationObservation` 为不可变 DTO：`operation: str`、`operation_id: str`、`status: WmsConfirmationStatus`、`attempt_count: int`、`retry_eligible: bool`、`next_attempt_at: str | None`、`deadline_at: str`、`last_dispatch_at: str | None`、`response_evidence_id: int | None`、`response_result: str | None`、`updated_at: str | None`（新建后未更新时为 null）。
- `EvidenceObservation` 为不可变 DTO：`operation: str`、`operation_id: str`、`apply_status: InboundEvidenceApplyStatus`、`received_at: str`、`processed_at: str | None`、`published_at: str | None`、`decision_attempt_count: int`、`decision_next_attempt_at: str | None`。只查询 `WMS_EVENT/WMS_RESULT`，复用现有枚举，所有时间均 UTC ISO；该模型没有错误码字段，不从 apply_status 猜错误原因。
- GET `/api/v1/wms-diagnostics/confirmations?operation=...&operation_id=...` 与 GET `/api/v1/wms-diagnostics/evidences?operation=...&operation_id=...`。固定两条只读入口，分别使用已批准独立 `ops:wms-confirmation:read`、`ops:wms-evidence:read`；无记录 404、数据库不可用 503。

该 Service 注入 session factory 与 Repository；Repository 仅查询 `WmsConfirmation`、`InboundEvidence`。路由只调用 Service。DTO 全部在 `observation.py` 定义，不放 SDK；展示并不构成业务 Fact。

- [x] 使用现有 `(operation, operation_id)` 唯一索引查询；Evidence 同时限定 `kind IN (WMS_EVENT, WMS_RESULT)`，不手拼另一套 source identity。输入有界并使用现有 operation/operation_id 校验规则。Transport 回调走 A2 自有证据模型；不得把其 absence 解释成没有接收回调。
- [x] 建立 RED：同 operation 不同 identity；同 identity 不同 operation；未发送、已保存响应、Evidence 尚未应用；404/503；Redis 不可用时 PostgreSQL 查询仍成功；插件未安装仍可查询已保存记录。
- [x] 实现窄的只读 Service/Repository 和两个 route，注册到现有 app；不使用 `DiagnosticsRepository` 的 Redis 缓存判断业务状态，不增加通用跨表 registry。
- [x] 返回原持久状态，不返回 `can_retry=true` 或虚构“业务已完成”；业务操作准入由原领域 Service 再查事实决定。
- [x] 运行 `uv run pytest tests/runtime/execution/test_execution_observation_service.py tests/api/test_execution_observation.py -q`，再在隔离 PostgreSQL 运行新集成文件；检查两条查询使用现有身份索引。
- [ ] 闭合三层边界、服务导出、权限与 OpenAPI 生成物，交付 B1。若 route 注册改变共享装配，纳入其直接测试和 HEAVY。

**验收：** HTTP ACK、可靠接收、Evidence 应用明确分开；近期诊断缓存过期或 Redis 清空不导致可靠事实查询丢失。

### Task A4：验证真正的中断窗口，按失败证据修复原 owner

**Files:**
- Reuse/modify tests: `tests/e2e/test_active_dispatch_wakeup.py`、`tests/support/transport_broker.py`、`tests/integration/test_transport_broker_harness_cleanup.py`。
- Create: `tests/e2e/test_execution_interruption_recovery.py`，只拥有真实 worker 中断矩阵。
- Production inspect/fix only on reproduced failure: `src/core/transaction_wakeup.py`、`src/core/task_queue_gateway.py`、`src/celery_app/tasks/transport.py`、`src/celery_app/tasks/device_command.py`、`src/celery_app/tasks/wms_confirmation.py`、`src/celery_app/tasks/execution.py`、对应领域 Service/Repository。
- Docs/config: `docs/architecture/heavy-test-impact.toml`、`docs/devops/execution-recovery.md`（B2 拥有正文，本任务只交付实测数据）。

**Interfaces:** 原 `defer_wakeup`、原 queue gateway、原数据库 claim 与扫描任务名全部保留。故障切点由测试进程控制、真实 HTTP stub 的接收屏障与现有 broker harness 实现，不在生产加入故障注入配置。

| 切点 | 注入方式 | 必须观察到的结果 |
| --- | --- | --- |
| commit 成功、broker publish 失败 | 仅测试替换发布端使其失败 | DB 义务保留；恢复 broker 后 Beat 扫描处理原身份 |
| claim 后发送前 worker 退出 | 独占 worker 子进程受控退出；读取 send_started/lease 证据 | 只有证明未发出的记录可继续；发送已开始则走原歧义处理 |
| 对端保存请求后响应连接断开 | HTTP stub 先记身份再断连接 | 原合同允许的原身份重试或 RECONCILING；不创建新物理身份 |
| callback commit 后处理进程退出 | 持久化接收后停止独占消费者 | 原 Evidence 被重新领取；ACK 不早于提交 |
| outcome commit 后发布前退出 | 停止发布消费者 | 原 outcome_version 后续发布；重复消费无重复动作 |
| 结果 deadline 后收到合法终态 | 测试时钟/隔离记录建立超期，随后正规 ingress | 同任务从 UNKNOWN 收敛；不先释放资源 |
| Redis broker 数据丢失 | 专用实例/命名空间清理，不碰共享 Redis | 依赖恢复后由 DB 扫描继续；数据库未知物理事实仍等待 |

- [x] 列出每个切点现有测试覆盖，已有用例直接复用；当前无 Beat 正常闭环用例不当作崩溃证据。
- [x] 完善 harness 的子进程退出/等待/清理能力；扩展其现有清理测试，确保失败也清理独占资源。子进程继承环境按仓库规则清除 Git 局部变量。
- [x] 在独占 PostgreSQL/Redis 和真实 worker 上逐场景执行；固定环境指纹、原身份、提交点、发送次数、物理动作模拟计数、状态和 resource binding。
- [x] 对失败先归因：合同预期、测试屏障错误、环境未就绪或真实恢复缺口。仅真实缺口修改表中对应生产 owner，先补可重复 RED；不得放宽断言或增加新的全局恢复器。
- [x] 用无实际业务规则的 fake outcome consumer 验证基础重复消费；零安装插件的装配不能偷偷导入 `workline_plugins`。不把 fake 的成功称为正式业务恢复。
- [x] 按 selector manifest 运行最终相关 HEAVY；记录恢复耗时与原扫描周期/lease/排队量，未启用或 skip 不算通过。绿色实现只补证据，不为形成代码 diff 强行改动。

## 完成门禁

- [x] A1–A4 各自直接/间接测试闭合，唯一主 Review 无阻断项。
- [x] 执行总计划要求的有效快照 QUALITY、选中 HEAVY、生成物与 `git diff --check`。
- [x] A4 无需新业务插件即可完成；真实 WMS/ECS、现场放行和业务消费明确单列。
- [x] 用户已授权通过 ship 提交、推送并经 PR 合入 develop；部署与现场验收另行处理。

## 实施冻结记录

2026-09-09：用户已授权 A/B/C 按切片实施；主 owner 为当前 Agent，后端 worktree 为
`/Users/kaizhou/codeDev/wes_backend-worktrees/codex-stability-recovery`，base 为
`9e2104713ecd181e7eefa5bea133efcaf11a8a21`。无生产 dirty；迁入 8 项已暂存计划/引用差异。
主工作区另有并行文档修改，本实施不同步或覆盖它们。

A1 风险为 LARGE/HIGH-RISK（期限与公共构造签名）；授权范围为本计划 A1，未涉及物理终态、身份、摘要或资源围栏变化。
生产符号：Settings 新字段、TransportService.__init__/_apply_submit_result/_apply_position_evidence、
_accept_position_fact、build_transport_runtime。生产构造者仅 Transport Composition；位置 helper 有两处消费者。
GitNexus CLI 1.6.11 可用，TransportService upstream 为 MEDIUM，位置 helper 为 LOW；
MCP 因数据库 storage version 43/40 不匹配不可用，使用同 worktree CLI 和精确 rg 补全。

签名传播涉及 tests/runtime/transport 的 service、observability、composition、acceptance_edges、
reconciling_facts、submit_fencing 和 conftest 中 OutcomeTransportService；
HEAVY 直接构造者为 tests/integration/transport/{test_transport_debug_reset,test_transport_evidence_transaction}.py、
tests/integration/wms_adapter/test_transport_callback_receipts.py、
tests/integration/execution/test_decision_processing_postgresql.py。
全部原断言保留；仅显式提供测试期限。既有超时/迟到事实/绑定测试仍归 Transport。
HEAVY 使用现有 service/composition/conf 映射并补充配置接线影响；本切片无 schema、migration、SDK、插件或 OpenAPI 变更。
验证顺序为原四文件基线、配置和期限行为 RED、批量传播与实现、Transport 领域 GREEN、selector 闭合；
最终 Review/QUALITY/HEAVY 在最终有效快照执行，不把聚焦验证称为现场验收。

A1 聚焦结果：原 92 项基线通过；新配置缺失及构造签名 RED 后，379 项 Settings/Transport FAST 通过。
默认配置测试的一次失败属于 Settings 环境 fixture 缺失，已改为复用项目正常配置入口；未修改生产校验。
A2 冻结：修改原 get_task_with_latest_evidence/get_task_snapshot、TransportTaskSnapshot/TransportTaskResponse；
新增原 service 内收据 DTO/query 与 tasks.py 固定只读 route。Repository 返回元组从二项扩为四项，
唯一生产消费者为 get_task_snapshot，唯一直接数据库测试为 test_transport_repository.py 的 coherent snapshot。
已有 PostgreSQL owner 承接聚合，不创建计划中重复的新文件。测试 owner 为 observability/API/repository；
API/Service 间接消费者包括 workline_integration_debug refresh_transport_action，纳入领域回归。
无 schema、身份、摘要和写入状态变化；HEAVY 沿用 repository/service/API 映射。OpenAPI 随 A3 一次导出。

A2 聚焦验证 65 项通过；原持久拒绝 API 的 payload=None/response_code=REJECTED 合同用于收据测试，未改变 ingress。
A3 冻结：新增 observation DTO、ExecutionObservationRepository/Service 与两个固定只读路由。
复用 v1 聚合 router 导出及既有 register_routers 关联，不再修改已足够的 register.py。
查询只涉及 WmsConfirmation 和 WMS_EVENT/WMS_RESULT InboundEvidence，使用现有唯一身份索引；
服务注入 session factory/repository，HTTP 入口复用 wire 身份校验；使用用户确认的独立只读权限。
新增 FAST Service/API 和 PostgreSQL 查询测试独立拥有三层行为；HEAVY 映射新增路径到该查询集成测试，
聚合装配同时由现有诊断 API 与 app 注册测试验证。无迁移、状态转换或插件依赖。

权限修正已确认并接入。当前完整目录 195 项，移除本轮三条新路由后为 189 项；原 bootstrap 固定 160 项已过期。独立核对当前 GET 语义与 admin/auditlog 策略后更新精确计数，并保留新增只读集合与写权限排除断言；未修改角色策略。

A4 冻结：由主 Agent 独占 tests/e2e/test_execution_interruption_recovery.py 与
tests/support/transport_broker.py、tests/support/transport_interruption.py，以及原 harness 清理测试。
只增加测试侧 HTTP 收到后断连接、worker 子进程退出屏障、Git 环境过滤；生产代码不加故障开关。
复用 Celery 原 beat_schedule 的周期，由真实 Scheduler 向独占 broker/worker 发送原扫描任务。
以 Transport 验证提交丢唤醒/独占 Redis 队列丢失、claim 后 HTTP 前退出、响应丢失、
消费者停止期间 callback commit/outcome commit、deadline 后迟到终态与单调发布。
资源均由原 cleanup owner 清理；现有 Device/Confirmation 实际派发矩阵继续复用，
不把 Transport 场景自动等同为其他领域完整崩溃窗口覆盖。

### 当前验证边界

A1/A2/A3 聚焦回归共 468 项通过；完整 QUALITY 通过。A4 的四项真实进程/HTTP 中断场景通过，
两个丢唤醒样本重新领取分别约 30.124 秒和 30.128 秒，沿用生产 30 秒 submit 扫描周期。
这不是现场业务恢复耗时：未包含人工对账、真实设备运动或多任务排队。最终 HEAVY 和 Review 以总计划最新记录为准。

Provider 使用 `uv run python scripts/export_release_provider.py --out-dir reports/stability-recovery-provider` 导出；
尚未进入前端 canonical。前端冻结入口要求干净后端 develop，当前实施 worktree 不满足该交付条件。

A4 的 fake outcome 发布/重复语义复用 `tests/runtime/transport/test_transport_outcome.py` 与
`conftest.py` 的 FakePublisher；核心无具体插件导入由既有 ownership guardrail 验证。真实 worker 主集与
共享 harness 继承消费者补充集合计 166 项通过，33 个最终 selector 文件全部覆盖，无 skip。
这组 WMS operation 回归承接共享测试支撑的影响，不自动扩称为各 operation 的全部中断窗口矩阵。
