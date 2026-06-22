# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- (Future changes will be listed here)

## [0.8.0.0] - 2026-06-22

### Added

- 新增料盘根实体表 `material_units`，料盘（REEL）从粗分机扫码起即建立独立实体记录，后续插件操作都是对该料盘状态及位置的变化，取代原散落在 `context_json` 的料盘身份记录。
- 料盘状态机 `material_units.status` 落地 5 态物视角（`IN_TRANSIT`/`STORED`/`COMPLETED`/`NG`/`RECONCILING`）：NG 是业务问题单向进 NG 域，RECONCILING 是功能问题对账后可回正常态，两者不重叠。
- 新增 `RuntimeIntent.create_material_unit` / `update_material_unit_status` 两个意图，插件作者通过 RuntimeIntent 显式表达建/更新料盘实体，状态/位置变化由 Runtime 统一写入并校验。
- WorkLine 插件 manifest 顶层新增 `session_subject` / `state_machines` / `pipeline_queues` 三字段合同，粗分机与 SMT 分拣机 manifest 已填充料盘状态机 transition 合同与管线队列声明，`state_owner` 指向 `material_units.status`。
- 非法料盘状态转移在写入时输出 WARN 软告警（不阻断业务），日志含 `object_type/object_id/from_state/to_state/pkg_code/plugin_key`，为 C 阶段 Runtime 强校验预热。
- 跨 Session 料盘身份关联改为 `pkg_code` + `current_session_id` 直连，handoff claim、resource mount/unmount、诊断查询改用 material_unit 直连，料盘定位一次查询 `current_location` 即得。

### Changed

- SMT 分拣机目标放盘成功后改为 `RuntimeIntent.complete()` 自动收尾，移除 `SORTING_SESSION_COMPLETE_REQUESTED` 人工完成事件，与粗分机行为一致；放置成功后异常自动转 NG（写 `ng_return_items` + 清空 Session 绑定，保留 material_unit 支持追溯）。
- 粗分机与 SMT 分拣机 manifest `contract_version` bump（`rough_sorter.v2` / `2026-06-21.p1`），orchestrator 拒绝旧 contract_version 的 Session，强制对齐。
- 粗分机扫码缺 PkgID、补建料盘实体缺 PkgID 时统一 fail-fast 阻断，不再回退 `business_key` 哈希作为 `pkg_code`。
- manifest loader 对 `PipelineQueue.role` / `StateMachine.granularity` 加白名单校验，`PipelineQueue.capacity` 收紧为正整数或 `MANY`，`_MATERIAL_UNIT_STATUS_VALUES` 从 `MaterialUnitStatus` 枚举派生并加漂移校验。

### Fixed

- 修复料盘 `six_in_one` 跨 Session handoff 复用时被 SMT 瘦构造 dict 覆盖的数据丢失，改为合并保留已有字段。
- 修复 NG 料盘在 `NgMaterialConflictError` 进 MANUAL_HOLD 后跨 inbox 批次恢复到 COMPLETE 时的永久孤儿，待清理 ID 持久化到 `session.context_json`。
- 新增跨线并发 CREATE_MATERIAL_UNIT 所有权拒绝：料盘仍被另一非终态 Session 持有时拒绝静默窃取，复用路径 `select ... with_for_update()` 行锁消除 TOCTOU。
- 已 COMPLETED 的 Session 重入完成收尾时仍调用 `record_completed_ng_flow` 记账（NG 料盘完成需写 `ng_return_items`），仅在 `NgMaterialConflictError` 冲突或正常完成分支按已 COMPLETED 标志早退，避免重复 lifecycle/持久化。
- SMT handoff claim 路径补料盘所有权检查：claim 时若料盘仍被另一非终态 Session 持有则拒绝静默窃取，与 `CREATE_MATERIAL_UNIT` 路径对称；`select ... with_for_update()` 锁行消除 TOCTOU。
- `RuntimeIntent` 的料盘状态在构造时 fail-fast 预检 `MaterialUnitStatus`，畸形 `material_unit_id` 不再崩整个意图批次。

## [0.7.3.0] - 2026-06-18

### Changed

- WorkLine 插件 manifest 不再把命令执行结果声明为 `events`，插件作者和沙箱调试现在按 `/callback/result` 理解命令结果链路。
- 插件开发指南和 WorkLine 插件模板同步澄清 manifest events 的边界：保留设备主动事件和 `INTERNAL` / `OPERATOR` / `SAFETY` 等运行时可见事件，命令结果进入 `COMMAND_RESULT` Inbox。

### Removed

- 移除粗分机和 SMT 分拣入库真实 manifest 中误建模的 `_RESULT/category: COMMAND_RESULT` 事件定义。
- 移除 SMT 分拣入库未再使用的 `_RESULT` event 常量，避免后续测试或插件实现继续引用错误事件合同。

## [0.7.2.0] - 2026-06-18

### Added

- WorkLine 插件现在可以通过插件目录内的 `manifest.yaml` 声明设备角色能力、货架位、物理拓扑和资源边界，插件作者不再需要在 Python 代码里拼装 manifest dataclass。
- YAML manifest loader 新增严格校验，覆盖重复 key、未知字段、旧 payload binding 字段、重复 command、event category 冲突、拓扑引用和资源边界引用错误。
- SMT 分拣入库和粗分机内置插件新增静态 YAML manifest，API summary 可直接返回面向物理流程的拓扑边。

### Changed

- WorkLine manifest 合同清理为静态能力目录，`CommandBinding` 只保留命令和目标设备角色，`EventBinding` 只保留事件、来源设备角色和分类。
- 插件开发指南和插件模板改为 YAML authoring，并移除旧 Python manifest、payload schema、result binding 和货架位参数绑定示例。
- `pyyaml` 作为后端直接依赖纳入锁文件，避免 manifest loader 依赖传递安装。

### Removed

- 移除 `RackPositionArg*`、`CommandResultBinding`、`rack_position_args`、`result_bindings` 和 `payload_schema_ref` 等未发布旧 manifest 合同字段。

## [0.7.1.0] - 2026-06-17

### Added

- 运行监控设备节点现在返回当前指令快照，前端可直接展示当前 command code、状态、发送时间和 ACK 信息。
- Command 状态变更新增统一 SSE 通知，真实 ECS ACK、沙箱 ACK、沙箱 Result 和 ACK 重试耗尽路径都会触发运行监控刷新。

### Changed

- 粗分机 manifest 拓扑补充输入机械臂、输送线和输出机械臂之间的 operation edge，让设备动作链路在拓扑视图中更完整。
- 已完成的 superpowers 规格和计划统一归档，并同步更新相关文档、TODO 和测试引用路径。
- Agent 项目入口文档同步到当前 GitNexus 工具命名和索引规模。

### Fixed

- 沙箱 ACK 和 Result API 现在会在提交后发布延迟登记的 SSE 事件，避免前端错过手动联调中的 command 状态刷新。

## [0.7.0.0] - 2026-06-17

### Added

- SMT 分拣入库现在可以承接粗分机释放后的 handoff 闭环，按 manifest source boundary 选择目标分拣线，并生成 typed source-pick context、内部 Inbox 和可追踪 route evidence。
- SMT handoff source item 支持两阶段 claim、目标 WorkLine 串行保护、ECS probe freshness 复查和 Celery READY claim 兜底扫描，避免重复物理取盘或提前进入分拣。
- Source-pick 成功、目标投放成功和 NG 投放成功会写入 handoff ledger，自动推进 SORTED/SKIPPED 终态，并在可继续分拣时按 demand scope 认领下一盘料。
- 新增 SMT handoff release-to-terminal、runtime intent effects、PostgreSQL claim/recovery guard 和 Celery recovery contract 回归覆盖。

### Changed

- SMT 分拣入库 manifest 货架位合同收敛为单层 source station 和五层 target station，移除旧 NG/WORK station 边界，运行时与测试 seed 同步使用新合同。
- SMT handoff recovery task 拆分 `scan_limit`、`recovery_limit` 和 `claim_limit`，保留旧 `limit` 调用的兼容行为。

### Fixed

- 修复 `WAITING_FULL_BOX_EXCHANGE` demand 的 READY source item 可能在满箱交换完成前被全局 claim 或 recovery claim 推入物理分拣的问题。
- 修复 source-pick success 和 terminal ledger 的 evidence 串线、终态冲突、replay 重复 claim 和非 SMT session 误触发风险。

## [0.6.2.0] - 2026-06-15

### Added

- WorkLine 插件 manifest 查询支持传入合同版本，调用方可按指定合同读取插件能力视图。

### Changed

- WorkLine 插件 manifest 货架位合同统一改为 `RackPosition*` / `rack_positions` / `rack_position_args`，API summary、真实插件、插件模板和运行时查询同步使用新命名。
- 新增 `.sources/*` 本地生成目录忽略规则，避免源码工作区临时文件进入提交范围。

### Fixed

- 修复粗分机入库分配上下文仍读取旧 `ResourceBoundary.position_code` 字段的问题，避免插件运行时因边界字段重命名触发异常。

## [0.6.1.0] - 2026-06-14

### Added

- WorkLine 插件 manifest 支持纯数据合同，插件可声明设备角色、事件、命令、位置参数、资源边界、物料流和承载能力，前端和运行时可基于同一事实源渲染与校验。
- 新增 manifest 拓扑、资源边界、物料身份、Station lease、runtime monitor、插件模板和真实 mock E2E 回归覆盖，防止旧 manifest 字段和运行时投影再次漂移。

### Changed

- WorkLine 插件详情 API、配置预检、激活校验、runtime 查询、Inbox/Outbox 处理和 session 解析迁移到新 manifest 合同。
- 粗分机与 SMT 分拣入库插件迁移到角色驱动的新 manifest，并将 SMT 目标位统一为五层货架工位合同。
- 插件 options 保持 selector-only，完整设备、事件、命令和拓扑能力由 manifest detail 提供，避免前端合同重复维护。
- 插件开发模板、沙盒文档和测试模板同步到新 manifest/result binding 合同。

### Fixed

- 修复 SMT 五层目标位与 Station lease、runtime monitor smoke seed、真实 mock E2E 夹具之间的货架类型不一致。
- 修复插件 material identity 未解析时丢失 raw evidence hash，NG evidence 继续保留扫码和交接输入审计证据。
- 修复 runtime monitor request_id 映射回归，并补充对应测试。
- 修复 manifest/result binding 中失败结果路径与插件 handler 不一致导致设备失败回调被吞的问题。

### Removed

- 移除旧 manifest 扫描、缓存和 sandbox 兼容分支，运行时只保留新合同入口。

## [0.6.0.0] - 2026-06-11

### Added

- 新增 SMT 入库 Handoff 后端闭环，覆盖粗分机释放单层货架后的 handoff demand/source item 账本、状态聚合和原因码 catalog。
- 新增 SMT handoff 查询与处置 API，支持 demand 列表、详情 evidence 和 source-pick 重试动作。
- 新增 `SORTING_SOURCE_PICK_REQUESTED` 内部 Inbox 合同，分拣首盘 source pick 通过 `SMT_SORTING_INBOUND` 插件进入 runtime 并写回 command/outbox correlation。
- 新增 SMT handoff Celery 兜底扫描和 Docker/PostgreSQL 并发 claim、recovery/EXPLAIN、端到端 smoke 验收。

### Changed

- 粗分机 `ROUGH_SORTER_RELEASE_FACT` / resource fact 链路接入 SMT handoff producer，重复 release、callback 和扫描保持幂等。
- SMT usage 口径抽为共享 policy，handoff 与现有 SMT rack/bin 调度统一使用 `0..1` usage 规范。
- SMT 分拣入库插件 manifest 和事件处理扩展为支持 handoff source-pick 内部事件。

### Fixed

- 修复 SMT handoff detail API 权限合同，详情接口使用 `biz:workline:detail`，动作接口继续使用 `biz:workline:update`。

## [0.5.0.0] - 2026-06-10

### Added

- WorkLine runtime 支持按设备、工位和 Session 资源约束并发处理，同一资源保持 FIFO 串行，不同资源可并行推进。
- 新增 Inbox `claim_bucket_key` 热队列合同和 PostgreSQL-backed claim/EXPLAIN 验收，覆盖同 bucket 队首围栏、到期重试和 stale `PROCESSING` 回收。
- 新增 `RESOURCE_WAIT` 运行时等待语义、诊断证据和 runtime/trace 资源证据视图，操作员可看到当前阻塞资源、等待次数和最近等待时间。
- 新增粗分机/SMT mock 与 Docker 集成验证支撑，覆盖 WMS 有状态货架池、ECS 随机延迟和 WorkLine runtime smoke 数据。

### Changed

- WorkLine Inbox worker 从工作线级 Session 串行改为资源约束 claim 模型，并移除旧 parallelism/bucket lock 参数与入口 blocker 合同。
- SMT 分拣入库在目标工位资源忙时进入 `RESOURCE_WAIT`，未知资源仍保持明确阻断，避免把正常资源等待计入普通失败。
- WorkLine runtime、诊断、trace、操作文档和硬件流程文档同步到多 open session 与资源决定并发的运行模型。

### Fixed

- 修复 station dispatch lease 并发写入竞争可能被当作普通失败并进入死信的问题，竞争失败现在按资源等待重试处理。
- 修复 `RESOURCE_WAIT` 成功重试后旧 ACTIVE 诊断和 session 等待上下文未及时关闭的问题。
- 修复设备上下文缺失的 callback event 返回合同，未知设备现在返回 HTTP 404、业务码 `3000` 和 `ack=false`。
- 修复 Inbox claim bucket 模型索引声明与 Alembic migration 之间的 schema drift 风险。
- 修复 stale `PROCESSING` 回收缺少专用热队列 partial index 导致 PostgreSQL claim 执行计划可能退化的问题。

## [0.4.8.0] - 2026-06-08

### Added

- 新增 WES 单层货架执行编排边界，覆盖 Station lease、WMS 运输需求合同、插件 single-layer boundary manifest 和运行态结构化展示字段。
- 新增单层货架编排、station lease、runtime detail、WMS transport contract、插件边界和真实 mock E2E 回归测试。

### Changed

- 粗分机和 SMT 分拣入库改为显式声明单层货架 boundary，并通过 Station lease 与 active rack snapshot 进入 WMS/rack operation 流程。
- Active rack snapshot 恢复在清理旧物料派生状态时保留空格容量模板，并将空格 used depth 归零，保证分格策略可继续使用资源投影。

### Fixed

- 修复同一 rack operation 创建 station dispatch lease 时被当前 session 自身等待记录阻断的问题。
- 修复 station dispatch 幂等重试被自身 active lease 阻断的问题，并在重复 station claim outbox 复用时校验 session、workline、station 与活跃状态。
- 修复单层货架 move-out 任务未继承 `rack_code`、同一换架操作补给被旧架占用阻断，以及超过 50 个 open session 时 station 冲突漏检的问题。
- 修复已派发 rack operation outbox 在重试时被新 payload 改写的问题，保留派发审计记录不可变。
- 修复 SMT target active snapshot 未传入 provider、粗分机 rack operation 缺少显式 work position，以及相关 E2E 测试夹具缺少目标 station 资源投影的问题。

## [0.4.7.0] - 2026-06-06

### Added

- 新增单插件 WorkLine manifest 摘要接口，前端可按 `plugin_key` 获取设备角色、事件来源角色、命令目标角色和支持的事件/命令，用于 runtime scene 现场态势图适配。

### Changed

- 插件选项与 manifest 摘要复用统一的 manifest 字段归一化校验，输出稳定排序并拒绝错误类型，避免前端把异常 manifest 当作有效语义消费。

### Fixed

- manifest 摘要加载失败时返回统一校验错误，未知插件返回统一不存在响应，避免坏插件声明变成内部异常。

## [0.4.6.0] - 2026-06-05

### Added

- 新增 WorkLine trace 统一诊断结论与 trace path 证据契约，聚合诊断 verdict、blocking point、outbox、设备、资源快照和执行路径证据。
- 新增 SMT 分拣入库真实 mock 驱动沙箱 E2E，覆盖从事件入站、命令派发、WMS 有状态货架池到 trace 证据查询的闭环。
- 新增有状态 WMS mock 货架池、活动货架快照恢复和 SMT 料格资源视图，支持按真实资源投影诊断执行路径。

### Changed

- Runtime trace、integration debug、inbox batch 和 session 解析流程接入统一诊断构建器，返回更稳定的操作员排障合同。
- 调试清理、资源投影、货架调度和 rack 操作边界收紧，避免调试数据和资源快照跨测试或跨会话污染。
- 同步 WorkLine、SMT 和 mock 设计文档，明确 trace path evidence contract 与真实 mock 沙箱验收边界。

### Fixed

- 修复 trace 查询中 session、outbox、资源和设备证据不完整时诊断链路难以定位阻塞点的问题。
- 修复调试清理和资源投影对 rack/bin 边界处理不一致，可能影响 SMT 沙箱重复执行的问题。

## [0.4.5.0] - 2026-06-02

### Added

- 新增跨计划 WorkLine 沙箱 smoke，验证 STOPPED guard、START 准入、SMT Sorting P0 intent、命令派发前实时设备状态检查、本地 NG 和 Session 完成合同串联可用。

### Changed

- 同步 WorkLine Fast-Fail 与 SMT 分拣入库计划依赖状态，明确已完成后端 stitching smoke，并保留 runtime orchestrator/effect 层与 `NG_MATERIAL_CONFLICT` 后续验收项。
- 更新 SMT 分拣入库设计验证计划，标记跨计划 smoke 覆盖范围和仍需补齐的验收边界。

## [0.4.4.0] - 2026-06-02

### Added

- 新增 SMT 分拣入库 WorkLine 插件、上下文合同和 P0 编排流，覆盖源格取盘、工作料盘扫码、目标料格放盘、本地 NG 与会话闭环。
- 新增 SMT 料格分配纯策略、活动货架快照恢复和料格深度 Numeric 迁移，支持按料盘厚度进行稳定料格分配。
- 新增 SMT 分拣入库插件、资源投影、料格分配、NG 回流和 P0 集成回归测试。

### Changed

- WorkLine 插件注册、配置校验、运行时 intent effects 和写回服务接入 SMT 分拣入库业务合同。
- 资源投影和 SMT 货架/料箱调度服务改为复用共享料格分配策略，并保留 Decimal 深度证据。

### Fixed

- 修复 SMT command-result 被误归类为设备事件源能力导致激活被阻断的问题。
- 修复非 HTTP 设备协议参与 START 状态探活时生成 `tcp://`、`mqtt://` 等 httpx 不支持 URL 的问题。
- 修复设备状态预检对配置化 `status_path`、特殊字符 `device_code` 和结构化 DEVICE_BUSY 诊断的兼容性。

## [0.4.3.0] - 2026-06-02

### Added

- 新增 WorkLine `STOPPED` 运行态和 START 准入服务，现场恢复后需要平台 START 事件完成 ECS 状态探测，所有必需设备 AUTO/IDLE 后才释放派发。
- 新增 WorkLine START 准入投影字段、数据库迁移、运行态查询返回字段和 `WORKLINE_START_REQUESTED` 平台控制事件。
- 新增命令派发前实时 ECS 设备状态预检、设备忙停放、STOPPED outbox 停放和 dev mock START 调试事件支持。
- 新增 START 准入、callback 入站、runtime hold 释放、outbox 派发、事件映射和 mock 端点的回归测试。

### Changed

- Callback event 入站区分平台控制事件、平台安全事件和生产事件，STOPPED/RECONCILING/ESTOPPED 期间拒收生产事件。
- Runtime Hold 和 ESTOP 恢复后不再直接回到 READY，而是转入 STOPPED 等待现场 START；已阻断 outbox 会停放到 workline 维度等待 START 释放。
- WorkLine 配置校验补充命令目标设备能力、通信配置和 runtime event mapping 保留事件校验。

### Fixed

- 修复 runtime event mapping 可把生产事件映射为 START 并绕过生产事件安全门禁的问题。
- 修复 START 探测期间并发安全状态变化可能被 stale ORM 实例覆盖为 READY 的问题。
- 修复 STOPPED 等待 START 窗口中新增 outbox 被误标记为永久失败的问题。
- 修复设备状态预检 URL 未编码 `device_code`、设备忙态和同命令 ACK 超时重试被当作普通派发失败消耗重试的问题。

## [0.4.2.0] - 2026-05-28

### Added

- Workline Inbox 支持原子 claim、处理器 token fencing、stale PROCESSING 回收和按设备/Session bucket 的并发处理保护，worker 可在保持同一冲突域串行的前提下提高吞吐。
- 新增 Workline Unit of Work、SessionLifecycleService、DeviceCommandGateway、OutboxDispatchService、OrchestratorWriteBackService 和任务队列网关，运行态提交、设备指令、outbox 派发和写回职责从 Celery 入口拆出。
- SMT 扫码链路接入 WMS 库存 typed port，并补充本地 WMS mock、E2E 配置、服务定位和调用证据测试。
- 新增 Workline inbox 热队列部分索引、handling 满箱交换 completion policy 补齐迁移，以及可信代理来源配置。

### Changed

- Callback、Runtime Hold、Workline 操作和 Celery 任务入口改为复用服务层与 UoW 边界，避免 API/Celery 入口直接承载业务编排细节。
- RuntimeIntent effect、诊断上下文、outbox delivery、系统 outbox engine、rack/handling 完成策略和货架投影枚举归一化到共享服务/工具函数。
- Workline 文档、SMT classifier runtime flow、物料流 runtime 和 outbox 派发指南同步到新的职责拆分与运行态合同。

### Fixed

- 修复 Inbox 并发消费中重复 claim、stale worker 终态覆盖、bucket 失败回滚范围和 Redis SSE 降级等可靠性问题。
- 修复 Runtime Hold 释放后失败原因丢失、重复释放幂等、对账 ACK 误清理终端会话以及运行态写回边界问题。
- 修复 PostgreSQL advisory lock SQL 参数化、可信代理客户端 IP 解析、rack 投影枚举归一化、handling completion policy 历史数据补齐和 SMT 参数类型收窄。

### Removed

- 移除旧 `ProcessInboxMessages`、`OutboxDispatcher` 和 `process_inbox_messages` 内部合同，测试和运行入口统一指向新的服务层。

## [0.4.1.0] - 2026-05-27

### Added

- 新增 WMS 对接辅助域，提供库存查询、预留释放、入库确认、出库确认等内部 typed ports。
- 新增 WMS 调用证据、脱敏快照、canonical hash 和 DB-backed 熔断状态，支持跨 API/Celery 实例共享依赖故障状态。
- 新增 WMS/RCS 回调标准化、运输合同构造、短时查询缓存和调用方接入 checklist。

### Changed

- Rack、Handling 和 callback 运输/回调链路改为复用 WMS integration 合同与 normalizer，保持现有入口和业务分发不变。
- Redis 缓存助手补充 WMS 查询降级语义，坏缓存或 Redis 不可用时回源 WMS。

### Fixed

- WMS typed ports 兼容 `data` 返回 list 的响应结构，避免批量查询数组被当作普通对象吞掉。

## [0.4.0.0] - 2026-05-12

### Added

- 新增作业线物料流 Runtime，插件可通过统一 RuntimeIntent 驱动物料到达、目的地解析、出站派发和业务身份追踪。
- 新增 Runtime Hold、运行态对账、安全事件和 NG 退料能力，现场可阻断、诊断、释放和修复异常运行链路。
- 新增 callback ingress service 与命名响应模型，回调入口统一校验最小包络、记录诊断并返回明确的接收/拒绝结果。
- 新增运行态指标、告警、投影、trace response builder 和修复脚本，支持从 callback、inbox、outbox、session 到设备动作的完整调查。
- 新增 Celery worker 进程级健康检查，开发热重载场景下不再依赖 remote-control ping 判断容器健康。

### Changed

- 内置插件迁移到 RuntimeIntent 模型，移除旧状态机与插件结果链路，把运行状态所有权收敛到 Runtime 层。
- callback API 不再因为 Celery 控制面抖动快速失败；回调先提交入库，再依赖即时任务、Beat 或重试继续处理。
- WorkLine、Device、Outbox、Session、Inbox、Trace 和 Safety 服务围绕运行态阻断、对账恢复和物料身份重新组织。
- 文档、插件模板和开发指南同步到新的物料流 Runtime、Runtime Hold 和插件无状态开发模式。

### Fixed

- 修复 callback 已提交但即时触发 Celery 失败时会影响调用方的问题，改为记录警告并走 Beat/重试兜底。
- 修复 worker 健康检查可能把 `worker_healthcheck.py` 自身误判为 Celery worker 的问题。
- 修复运行态对账、超时扫描、沙箱派发、NG 退料和插件契约中的多处回归，并补充 API、服务和 Runtime 单元测试。

### Removed

- 移除旧插件状态字段、旧设备目标解析器、状态机模板和旧插件结果链路，避免运行态存在第二套事实来源。

## [0.3.0.0] - 2026-04-27

### Added

- 新增 WORKLINE 诊断账本能力，现场可通过 trace、blocking point、诊断卡、dispatch attempt 复盘 callback、inbox、session、command、outbox 和 timeline 链路。
- 新增 workline diagnostics 与 dispatch attempts 持久化模型、迁移、Repository、Service 和 trace read model 聚合响应。
- 新增 WORKLINE trace、sandbox pending、replay 和 manual operation API，并补充快速开始文档与架构索引入口。
- 新增 timeline seq_no 事务级 advisory lock 分配、dispatch attempt lease/finalize 语义和 no-SQL 诊断快速路径测试。

### Changed

- callback/event、callback/result、callback/external 统一返回 trace/event/causation identity，并把 callback result 的恢复锚点收口到 `command_code`。
- Runtime、插件上下文、SMT classifier、mock 和 E2E fixture 统一投影 trace 信息，Trace API 返回 sessions、dispatch attempts 和诊断上下文。
- Replay 现在创建新的 replay event，并把原 event 作为 causation/evidence 保留，不改写历史 inbox。

### Fixed

- 修复重复 callback/event 使用顶层 `event_id` 时仍可能产生新副作用的问题，并在 DB unique 并发冲突后回读原 inbox 返回 duplicate outcome。
- 修复重复事件 ACK 未回填原业务 trace 的问题，调用方不再拿到只有重复日志的新 trace。
- 修复 replay/manual 传入不存在资源时返回全局 500 的问题，改为明确资源不存在响应。

## [0.2.0.0] - 2026-04-25

### Added

- 新增 WorkLine 运行模式治理，支持 `AUTO` / `MANUAL` / `SIMULATION`，并限制沙箱模拟模式只能在 dev/test 环境启用。
- 新增 WorkLine 插件 manifest、拓扑校验、插件状态投影和运行时上下文快照能力。
- 新增 SMT classifier 的 typed context、状态机、诊断结果和更完整的命令/回调契约测试。
- 新增 inbound tote QC 第二插件 spike，覆盖 `WEIGH_TOTE` / `DIVERT_TOTE` 命令、手工回调和异常路径。
- 新增中文插件开发指南、插件模板、沙箱 happy path 和模板资产回归测试。
- 新增设备运行态治理字段和服务逻辑，维护 `IDLE` / `RUNNING` / `ERROR` / `OFFLINE` / `MAINTENANCE` 以及当前指令、工作线和 Session 占用信息。
- 新增系统事件流服务和 `/sys/events/stream` SSE 入口，用于推送设备状态、指令和工作线运行事件。

### Changed

- 调整 WorkLine outbox 派发逻辑，`SIMULATION` 会进入沙箱出口并保留真实 payload 供调试。
- 将设备指令 `task_type` 从中心枚举约束改为可扩展字符串，允许插件定义自己的设备任务类型。
- 将需要真实 WES、Celery、种子数据和本地 mock 服务的 SMT mock 集成测试改为显式 live gate。
- 调整真实设备 outbox 派发治理，同一设备上一条硬件任务未完成前不会派发下一条；`PENDING` 仅表示 WES 队列，不再视为设备占用。

### Fixed

- 修复插件模板和开发指南中已不存在的 `ClassificationResult` 示例，避免新插件从错误契约起步。
- 移除运行时契约层中重复的 Session/Inbox 状态枚举定义，统一引用模型层枚举。
- 修复本地 SMT mock 集成测试会通过系统代理误判服务状态的问题。
- 修复 TimescaleDB 未在 Postgres 启动时预加载导致迁移 DDL 被服务器中断的问题。
- 修复 SMT mock 链路中插件任务类型被旧映射改写为 `PROCESS` / `PICK_AND_PLACE` 的问题。
- 修复指令结果回调后设备运行态不会按硬件成功/失败释放或转故障的问题。
- 修复失败 outbox 可能遗留活跃设备指令和 `RUNNING` 设备占用的问题，并通过迁移修复历史数据。

## [0.1.0.0] - 2026-03-23

### Added

**Core Framework**
- FastAPI + SQLModel + SQLAlchemy 2.0 分层架构（API → Service → Repository）
- ModelFactory：自动生成 Create/Update Schema，支持乐观锁更新
- Mixin 系统：AuditMixin, OptimisticLockMixin, SoftDeleteMixin, DataTableMixin, EnterpriseMixin
- Hook 系统：Repository 层业务逻辑扩展点
-雪花 ID 生成器，支持分布式环境
- 全局异常处理系统，统一错误响应格式
- 可配置日志系统，请求上下文管理

**认证与授权**
- JWT 认证系统，支持令牌刷新和黑名单
- 会话管理和多设备登录控制
- RBAC 权限模型（用户-角色-权限-菜单）
- 动态权限缓存失效机制
- API 应用签名认证（HMAC-SHA256）
- API 应用有效期管理和权限类型细分
- 权限与菜单自动同步机制

**管理员模块**
- 用户管理：CRUD、密码重置、软删除
- 角色管理：动态权限分配
- 权限管理：树形结构支持、菜单和按钮权限
- 菜单管理：前后端统一注入机制

**设备管理模块**
- 设备 CRUD 操作，支持乐观锁并发控制
- 设备指令和事件回调功能
- 回调日志记录（callback_logs 表）
- 统一外部 device_code / 内部 device_id 契约

**作业线模块**
- WorklineInbox 收件箱功能
- 作业线插件化编排与全链路追踪设计
- E2E 测试数据初始化脚本

**数据层**
- Alembic 数据库迁移工具集成，支持多 Schema 配置
- 软删除与唯一约束冲突解决方案（部分唯一索引）
- 审计日志：操作耗时统计、后台任务模式
- 智能关系加载策略，优化查询性能
- 统一 ENUM 类型规范（VARCHAR + CHECK 约束，禁用 PostgreSQL 原生 ENUM）

**缓存系统**
- 统一缓存配置（Redis）
- 缓存装饰器，支持函数级缓存控制
- 权限缓存自动失效

**开发工具**
- Ruff 代码规范和格式化配置
- basedpyright 类型检查
- VSCode 工作区设置优化
- pytest 测试框架，254+ 测试用例

**部署与运维**
- Docker Compose 多环境配置
- GitLab CI/CD Pipeline（已迁移至 Jenkins）
- Rocky Linux 环境搭建脚本
- 健康检查端点
- CORS 跨域配置，支持多格式解析

**文档**
- CLAUDE.md：开发指南和架构规则
- 软件需求规格说明书 (SRS)
- P9 WES 第三方设备接入白皮书
- CI/CD 环境搭建文档

### Changed

**架构重构**
- 修复 Service 层分层违规，确保 API → Service → Repository 严格调用链
- 统一 ENUM 类型并新增 workline 模型
- 将 get_all 方法替换为 get_list 方法，优化查询逻辑
- 重构 mixins 模块结构
- 收敛乐观锁更新模型
- 迁移至基于 pyright 的类型检查

**接口优化**
- 统一 API 响应格式为标准响应模式
- 收敛菜单树接口并精简快速回归集
- 为 BaseAPI 添加 response_model 标准化响应格式
- 对齐认证菜单接口并修复菜单查询空结果

**代码质量**
- RUFF 格式化和检查修复
- 消除 basedpyright 类型检查问题
- 移除未使用的导入，优化代码结构
- 修复 Mixin 继承 MRO 错误

**依赖管理**
- 重构 Celery 应用结构
- 从 GitLab CI 迁移到 Jenkins Pipeline

### Fixed

- 修复应用 ID 为 None 时权限查询异常
- 修复用户可选字段清空更新失效
- 修复乐观锁并发更新检测
- 修复软删除模型查询问题
- 修复 FastAPI 依赖注入类型问题
- 统一数据脚本入口并修复迁移约束命名
