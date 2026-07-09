# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.15.3.0] - 2026-07-09

### Changed
- 将退役的 WorkLine restructuring readiness gate 替换为 runtime production closure 与 RuntimeInbox authority 等稳定合同护栏，并接入 quality profile。
- Callback result/event/external 入站 ACK 统一以 RuntimeInbox 为权威，过渡 Workline inbox 的重复仅跳过兼容副作用，不再污染对外幂等语义。
- 收束默认快速回归中的过期 mirror/compat/restructuring 测试命名，测试拓扑和架构文档同步到稳定 runtime/workline 边界。

### Fixed
- 修复 basedpyright 异常并补齐相关回归，保持 `uv run basedpyright .` 零错误零警告。
- 补齐 Inbox batch processor 的 resource retry 回归，确保资源等待被统计为 `resource_wait` 并停放重试，而不是误标为已处理。

### Removed
- 删除已退役的 `check_workline_restructuring_readiness_gate.py` 及对应过期 readiness/mirror/compat 测试文件。

## [0.15.2.0] - 2026-07-09

### Added
- 新增过程缩写命名回归护栏，覆盖 active code、脚本、git hook、默认测试集合和当前架构文档，防止 `C3`、`R-I3c`、`R-WLR`、`wlr` 等重构短码重新进入当前 surface。
- 新增 legacy matrix generator 和 SMT 货架槽位别名回归测试，确保稳定 guardrail seed 名称可重复生成，并保留真实业务槽位 `C1` 的映射行为。

### Changed
- 将 architecture guardrails 的 rule ID、函数名、allowlist、测试文件名和 quality gate 注释迁移到稳定架构边界名。
- 将 legacy cleanup matrix、当前架构文档和生产注释中的过程缩写表述改为稳定领域语义，历史审计记录仍保留可追溯事实。

### Fixed
- 修复 legacy matrix 生成器仍会产出旧测试路径、旧 helper 名和旧 seed 名称的问题，避免重新生成时带回过程命名残留。
- 将 SMT 货架槽位 `C1` 业务别名收束为语义常量，让过程命名护栏只允许这个精确业务例外。

### Removed
- 删除默认快速回归测试和 active guardrail surface 中的 `C1`/`C2`/`C3`/`C4`/`C5`、`R-I3*`、`R-WLR`、`wlr` 过程命名。

## [0.15.1.0] - 2026-07-08

### Added
- 新增过程命名策略文档，明确 `phase/stage/wave/c3/c4/ri3/WLR` 等迁移批次词只允许保留在历史证据和归档上下文中。
- 新增过程命名架构守护测试，并接入 quality profile，阻断活跃代码、脚本和默认测试集合重新引入阶段性目录、文件、注释或测试命名。

### Changed
- 将 runtime、WorkLine、callback、device、WMS integration 等活跃 surface 的注释、docstring、测试名和工具名改为稳定领域语义。
- 将 runtime capability、business legacy absence、runtime evidence、production closure 等脚本、测试和文档引用统一到目标态命名。
- 将 Jenkins、git hook、质量门禁和 legacy cleanup matrix 同步到过程命名清理后的稳定入口。

### Fixed
- 修复根 Jenkinsfile 与 backend CI 仍引用阶段性架构门禁命名的问题，确保 CI 与本地 quality profile 使用同一套稳定检查入口。

### Removed
- 删除活跃代码和默认快速回归测试中的 `Phase4/Phase5/stage/wave/final cleanup` 等过程性命名残留。
- 删除 `runtime migration`、技术/业务 lane 等阶段描述在 active surface 中的残留表述，保留历史文档和已归档证据的可追溯性。

## [0.15.0.0] - 2026-07-08

### Added
- 新增 runtime/orchestration 原生 WorkLine 运行状态投影表、repository/service 与 Alembic final cleanup migration smoke。

### Changed
- handling lifecycle 改为通过 `ConveyorQueueMembershipWriterService` 写入队列 membership evidence，WorkLine 安全、START admission、query、trace 与 callback 接收校验统一读取 runtime projection snapshot。
- Phase5 readiness/final cleanup gate 与 legacy cleanup matrix 状态推进到 `final-cleanup-complete`。

### Fixed
- 修复 callback event idempotency 回归用例的 runtime projection snapshot 与设备能力 fixture，确保生产事件 guard 覆盖真实目标态输入。

### Removed
- 删除旧 `BinTransitMembership/BinTransitQueue` production surface 和 WorkLine 配置表中的运行态物理列。

## [0.14.0.0] - 2026-07-07

> **Note**: Phase5 business lane 发布。本 minor 关闭业务承载 legacy cleanup 阻塞，补齐 destructive cleanup ledger/final gate，将仍有价值的 WorkLine 业务合同迁入 Phase4 runtime capability contracts，并保持 `WorkLine.runtime_status` schema/data 删除独立延期。

### Added
- 新增 Phase5 business destructive cleanup ledger 与 final gate，校验 104 个 business carrier 条目的 matrix identity、处置状态、目标 capability、reference scan、外部 alias 状态和 Phase4 contract layer 边界。
- 新增 Phase4 business contracts 包，承载 RoughSorter、SixInOne、material identity、NG reason、SMT inbound handoff route/reason/usage policy 等低层业务合同。
- 新增 state-aware legacy absence/no-cycle guardrails，阻断已迁移 legacy path、`src/workline_plugins` 旧入口和 Phase4 contracts 回流 service/repository/database 层。

### Changed
- 将 business lane readiness 从 production evidence blocker 推进为 `PHASE5_BUSINESS_READY`，并把 matrix closure guardrail 纳入 `check_phase5_readiness_gate.py --lane business`。
- 将 runtime capability catalog、runtime services、repair/sync scripts 和 contract tests 改为依赖 Phase4 contracts 目标态路径。
- 将 legacy inbox 判重后的 result/event/external callback ACK 统一标记为 duplicate，避免 RuntimeInbox cutover 后旧 inbox 重复被误报为 accepted。
- 将 destructive cleanup final gate 接入 quality profile，让本地提交门禁和 CI 使用同一 cleanup 检查。

### Fixed
- 修复 Phase4 contracts layer guardrail 漏检相对导入的问题，`from ..service import X` 现在会解析为绝对模块并被 final gate 阻断。

### Removed
- 删除业务承载 legacy WorkLine domain/service/plugin test surfaces，业务断言迁移到 `tests/contracts/` 或反转为 absence guardrail。

## [0.13.0.0] - 2026-07-07

> **Note**: Phase5 technical lane 发布。本 minor 完成 legacy WorkLine plugin runtime 路径退出、RuntimeInbox 到 Phase4 runtime service 的目标链路切换，以及 absence guardrail/cleanup evidence 更新；business lane 继续被 Phase3 production closure provenance 阻塞。

### Added
- 新增 `RuntimeCapabilityDispatcher` 与 runtime capability catalog，让 RuntimeInbox 通过静态 capability wiring 路由到 Phase4 runtime service，不在热路径做动态 plugin import。
- 新增 Phase5 legacy absence guardrail，强制阻断 `src.app.workline.plugins.*`、`src.workline_plugin_registry` 与 `src.workline_plugins.*` 回流到可 import 运行路径。
- 新增 RuntimeInbox -> dispatcher -> Phase4 service 回归覆盖，覆盖成功路由、未知 capability、未声明 provider profile、重复 callback 和 legacy import fail 场景。

### Changed
- 将 inbound normalization、runtime config、result classifier、session resolver 等旧 plugin SDK 能力迁入 `src.app.runtime` / `src.app.workline.domain` 目标态服务与 catalog。
- 将旧 `src/workline_plugins/*` 和旧 plugin template 迁入 `docs/archive/legacy-workline-plugins/`，仅作为历史样本，不再保留在 `src/` 可 import 路径。
- 同步 cleanup matrix、Phase5 execution plan 与主架构设计，明确 `phase5-tech` 已关闭、`phase5-business` 仍等待 Phase3 production closure artifacts。

### Fixed
- 修复标准 RuntimeInbox result callback 取消链路在 legacy plugin 删除后无法完成 `CANCELLED` runtime intent 的问题。
- 修复 SystemOutbox repository `_clean_metadata_for_update` 对 readonly field 的 update 过滤，避免 Phase5 callback/idempotency 路径写回时触发持久化错误。

## [0.12.0.0] - 2026-07-06

> **Note**: Phase1~Phase4 residuals 发布。本 minor 关闭 Phase5 前必须先处理的 runtime owner、callback 入站权威、late callback evidence 和生产 evidence 门禁遗留项；Phase5 technical lane 可启动，business lane 仍等待真实 production evidence。

### Added
- 新增 Phase5 readiness gate，并接入 quality profile；technical lane 校验 Phase2 owner guardrail、RuntimeInbox cutover、Phase3 mock closure 与合同测试，business lane 显式要求 Phase3/Phase4 production evidence。
- 新增 RuntimeInbox callback cutover writer，让 result/event/external callback 在旧 Workline inbox 过渡消费前先写入 RuntimeInbox，并由 RuntimeInbox 控制对外重复 ACK 与 payload conflict。
- 新增 Phase3 production P0 E2E、production-scale benchmark、Phase4 runtime evidence artifact 的 composer/gate 校验，统一检查 provenance、workload metadata、evidence manifest、文件存在和 hash 一致性。
- 新增 Phase2 runtime status owner guardrail 与 Phase5 legacy cleanup matrix 校验，防止 WorkLine 域和 Phase4 capability 重新直接拥有 `WorkLine.runtime_status`。

### Changed
- WorkLine `runtime_status` 收敛为 runtime/orchestration compatibility projection；safety、START admission、query、trace 和 reconciliation 路径通过投影服务读写运行态。
- Callback 编排改为 RuntimeInbox 优先：RuntimeInbox 重复返回 duplicate ACK，legacy inbox 重复只跳过过渡副作用，不再污染对外 ACK 语义。
- CB late callback 与 WMS fulfillment 状态机保持 evidence-first 语义，`BLOCKED_BY_CB` 只代表出站 effect 被 circuit breaker 阻塞，不覆盖已到达的 callback evidence。
- Phase4 runtime capability 与 evidence profile 从开发/测试 readiness 推进到 production-capable gate 口径，site/production profile 只提高 evidence 要求，不改变 runtime service 行为。

### Fixed
- 修复 RuntimeInbox 与 legacy inbox duplicate 语义混淆导致 callback idempotency/API 合同测试失败的问题，并补 result/event/external 回归覆盖。
- 修复 reconciliation manager fallback、late callback owner 校验、source event/correlation key 与 runtime evidence 登记边界，避免无权威证据被当作业务完成或错误对账。
- 重新生成 legacy cleanup matrix，补齐新增 Phase4 runtime intent 和 runtime status projection 测试入口，确保 Phase5 删除前的业务/技术 lane 分类完整。

## [0.11.0.0] - 2026-07-05

> **Note**: Phase 4 production-capable runtime path 发布。本 minor 完成 Wave2/Wave3 runtime capability builder、evidence profile gate 和 evidence artifact composer；业务代码只面向 provider contract，不根据外部设备是真实、sandbox、MOCK 或 simulator 分支。

### Added
- 新增 Phase4 sorter inbound runtime capability builder，可生成 `RuntimeIntent`、CellReservation/RuntimeLocationEvent evidence、PKG binding fulfillment effect、库存事务 effect，以及 join gate object-scope reconciliation plan。
- 新增 Phase4 SMT/NG/WMS reconciliation runtime capability builder，可生成 RuntimeInbox 上游 callback evidence、重复 callback 幂等合并、WMS reject/source_version drift RuntimeHold plan 与 scope-only release plan。
- 新增 Phase4 runtime evidence artifact composer，支持 simulator/site/production profile 生成统一 evidence manifest。
- 新增 site/production evidence profile gate，校验 provider contract、effect dispatch trace、RuntimeInbox worker trace、RuntimeHold/Reconciliation trace、benchmark 和 Phase3 production closure artifact。

### Changed
- Phase4 主计划和 sorter/SMT specs 从“生产接入”口径调整为 “production-capable runtime path”，明确外部 provider 可替换，真实设备、sandbox、MOCK 或 simulator 只由部署 wiring 与 evidence 区分。
- Phase4 readiness gate 从开发/测试 MOCK gate 扩展为 profile-aware gate：development/test profile 继续用于本机推进，simulator/site/production profile 只提高 evidence 要求，不改变 service 行为。
- Legacy cleanup matrix 重新生成并同步摘要，覆盖新增 Phase4 runtime capability 与合同测试入口。

### Fixed
- 补齐 sorter runtime 成功 join gate、本地位置事实、非正库存数量拒绝、SMT/NG/WMS callback 缺 source event 拒绝等合同测试，防止 runtime builder 生成不完整 evidence。

## [0.10.6.0] - 2026-07-05

> **Note**: Phase 4 runtime readiness 开发/测试范围发布。本 patch 完成 Phase4 SPEC 同步、CellReservation 目标生命周期、RuntimeLocationEvent 位置事实、MaterialLocationQuery 与 WorklineActiveObjects 只读能力，并把 Wave2/Wave3 降级为本机 MOCK 验收；生产热路径仍保持关闭，发布前需显式通过 production closure profile 与上线门禁。

### Added
- 新增 Phase4 设计包与运行时实施计划，覆盖 CellReservation、MaterialLocationQuery、WorklineActiveObjects、sorter inbound capability、SMT/NG/WMS reconciliation，并在主规划中同步开发/测试 MOCK 与生产 gate 状态。
- Runtime/Orchestration 新增 `RuntimeLocationEvent` append-only 位置事实表、幂等写入 repository/service、查询索引和 Alembic 迁移。
- CellReservation 复用 `WorklineBinCellReservation`，新增 `RECONCILING` 持久状态、correlation/evidence 字段、active/frozen 唯一约束和目标语义 mapper。
- 新增 MaterialLocationQuery 与 WorklineActiveObjects 查询服务及只读 API facade，支持物料身份、package/bin、rack/side、workline active object、ExternalReference、correlation_id 查询入口。
- 新增 Phase4 sorter inbound 与 SMT/NG/WMS reconciliation preview service，将 Wave2/Wave3 验收沉淀为 runtime capability 级本机 MOCK 能力，不访问 DB、不发 WMS/ECS/NG/PDA effect。
- 新增 `scripts/check_phase4_runtime_readiness_gate.py` 并接入 quality profile，默认 development/test mock profile 通过，production profile 明确阻断生产热路径。

### Changed
- Callback 热路径接入 provider profile admission，未声明 callback/event/result normalizer 的 provider 在入口阶段被拒绝。
- Phase3 closure gate 在当前未发布项目的开发/测试范围改为 MOCK closure，真实 production artifact 不再阻塞本地推进；production profile 仍保留显式门禁。
- WorkLine `runtime_status` 写入收敛到 `WorkLineRuntimeStatusProjectionService` 兼容投影，减少 Phase4 新业务对 legacy 字段的直接依赖。
- Workline restructuring 主计划同步 Phase4 SPEC 与 runtime readiness 状态，明确 Wave2/Wave3 生产热路径未实施、Phase5 legacy drop 不可提前。

### Fixed
- 修复 Phase4 SPEC 中 sorter runtime mapping 对 `RuntimeLocationEvent`、CellReservation 复用口径和 WMS PKG binding port 归属的误标，并补合同测试防回归。
- 修复 MaterialLocationQuery 对 frozen/reconciling CellReservation evidence 的冲突展示口径，确保冲突返回 `RECONCILING` 而不是静默选边。
- 补齐 CellReservation TTL、reservation key、provider/source_version/correlation evidence 与 owner mismatch/WMS reject/source_version drift 的对账路径覆盖。

## [0.10.5.0] - 2026-07-03

> **Note**: Phase 3 closure 本地合同与门禁补齐发布。本 patch 完成运行态安全/恢复热路径、production evidence composer 与总门禁、benchmark provenance/workload 校验、观测与 toggle 发布门禁；Phase 3 生产/预生产 P0 E2E artifact 与 production-scale benchmark artifact 仍作为外部 evidence 后续补齐，不在本版本伪完成。

### Added
- Runtime/Orchestration 新增 Phase 3 closure gate、P0 E2E artifact gate/composer、benchmark artifact composer/gate 与生产 evidence 一致性校验，要求 trace、异常路径和 benchmark scenario evidence 文件存在且内容一致。
- 新增 DB-backed conveyor queue membership writer、DeviceRuntimeProjection 持久投影与 DeviceDispatchPolicy dispatch 预检，覆盖 placeholder resolve、跨队列 RECONCILING、唯一冲突重读和设备状态 TTL/实时 probe 策略。
- 新增 RuntimeObservabilityRegistry、OpenTelemetry bridge/backend adapter、runtime toggle release gate、外部 evidence catalog、WMS evidence archive/GIN index 和多类 Phase 3 resilience/load/contract fixture。
- WorkLine plane read 补齐 owner/superuser 行级过滤、独立权限和读取审计；full-box / RACK_BIN exchange 合同转为真实 callback + reconciliation 完成语义。

### Changed
- 收紧 RuntimeBenchmarkGate 的 production-scale provenance/workload metadata：禁止 lightweight/sandbox artifact 冒充生产基线，并要求 PostgreSQL、ECS HTTP、API HTTP 来源证据。
- `src/app/workline/services` package facade 只导出真实 service 与保留 tombstone；PlaneRead 安全 helper 改由 `plane_service.py` 具体模块直接导入。
- Docker compose 默认将 API、Nginx、Postgres、Redis、Flower、Locust、mock ECS/WMS 和前端开发服务端口绑定到 `127.0.0.1`，可通过 `DOCKER_HOST_BIND_IP` 覆盖。
- Legacy cleanup matrix 重新生成并同步摘要，当前 707 条 legacy entry、0 pending-review。

### Fixed
- 修复评审发现的 fulfillment 幂等 MATCH 重复派发、设备预占命令本地 busy 阻断、DeviceRuntimeProjection 并发 upsert、placeholder resolve 唯一冲突、RACK_BIN exchange 协议误判和 breaker 观测失败反向影响主流程等 Phase 3 热路径问题。
- 修复 plane read 安全 helper 包入口误导出导致 Stage 6 service-shim guardrail 失败的问题，并同步更新相关 API/模型测试导入路径。

## [0.10.4.0] - 2026-07-01

> **Note**: Phase 3 执行安全与恢复发布。本 patch 线新增 callback replay 防护、RuntimeInbox 幂等入口、Reconciliation 决策合同、WMS 履约状态保护，以及 WorkLine plane/manifest 读面；不包含数据库 schema 变更。

### Added
- Runtime callback 新增 body-bound HMAC 路径，签名覆盖 method、path、timestamp、nonce、body hash 与 app id，外部 callback 可使用更严格的签名合同，不再只依赖 legacy header-only 路径。
- `RuntimeInboxService` 新增 provider callback 幂等 source-event 入口，记录 payload hash，支持 manual replay record，并向审计与恢复流程暴露 payload conflict 细节。
- Active object ownership 与 Reconciliation 合同新增 owner-scoped decision、升级 severity、hold/freeze action 与 evidence reference。
- Device command 恢复策略新增 inbox backpressure、dead-letter operator attention 与可过期 command lease。
- WMS fulfillment 新增类型化状态转移、callback inbox handoff 语义、circuit breaker 阻断行为、类型化 evidence envelope 与 lifecycle helper。
- WorkLine 配置面新增 plane scene/snapshot 读模型，并可在激活前校验 manifest queue code、device role 与 required capability 缺口。
- Phase 3 运维合同新增 runtime observability signal 与 runtime toggle governance，明确禁止安全绕过开关。

### Changed
- WorkLine service export 纳入 Phase 3 manifest validator 与 plane service，并重新生成 legacy cleanup matrix，覆盖新增 plane、manifest 与 route 入口。
- Callback path 检测改为跟随配置化 `API_PATH`，部署调整 API 前缀后 callback HMAC 请求仍会强制要求 nonce 与 body-hash header。

### Fixed
- Callback nonce replay 防护改用原子 Redis `SET NX EX` 固定 TTL；nonce 存储不可用时 fail closed。
- 并发重复 RuntimeInbox callback 现在会重读既有 source event 并比较 payload hash，不再把唯一索引冲突泄漏成 `IntegrityError`。
- WMS fulfillment 终态现在会忽略迟到或乱序 provider/callback event，防止 `SUCCEEDED`、`REJECTED`、`FAILED`、`TIMEOUT` 或 `CANCELLED` 被覆盖。
- Release 验证补齐 generated legacy cleanup matrix 与 Stage 6 service-shim contract，使其与 Phase 3 WorkLine export 保持一致。

## [0.10.3.0] - 2026-06-30

> **Note**:Phase 2 burn-down F-1/F-2 收尾 PR(`feature/phase2-burndown-f1-f2`)。把 workline 域 14 个运行态 model + 10 个运行态 repository 物理迁入 `src/app/runtime/orchestration/{models,repositories}/`,同步重写 262 条跨域 import(81 文件),2 个 xfail 契约测试转硬绿。物理迁移是文件位置变更,`__tablename__` 不变,数据库 schema 不变,沿用 0.x patch 表达"安全清理"语义。

### Removed
- `src/app/workline/models/` 下 14 个运行态 model 物理删除(`bin_cell_reservation` / `diagnostic` / `dispatch_attempt` / `inbox` / `material_unit` / `object_transition_event` / `operation` / `rack_position` / `runtime` / `runtime_hold` / `runtime_hold_api` / `session` / `smt_inbound_handoff` / `timeline`)— 整体 `git mv` 到 `src/app/runtime/orchestration/models/`。`workline/models/` 收缩为 `workline.py` + `safety.py` + `__init__.py`。
- `src/app/workline/repositories/` 下 10 个运行态 repository 物理删除(`bin_cell_reservation` / `diagnostic` / `dispatch_attempt` / `inbox` / `material_unit` / `object_transition_event` / `rack_position` / `runtime_hold` / `session` / `smt_inbound_handoff` 各 `_repository.py`)— 整体 `git mv` 到 `src/app/runtime/orchestration/repositories/`。`workline/repositories/` 收缩为 `workline_repository.py` + `safety_incident_repository.py` + `__init__.py`。

### Changed
- 81 文件 262 条跨域 import 批量改写:`from src.app.workline.{models,repositories}.<待迁>` → `from src.app.runtime.orchestration.{models,repositories}.<待迁>`,覆盖 runtime/handling/rack/resource/device/callback/sys/celery_app/workline 内部/workline_plugins/scripts/tests 全部 caller。保留文件(`workline.py` / `safety.py` / `workline_repository.py` / `safety_incident_repository.py`)的 import 路径不变。
- `src/app/workline/models/__init__.py` 收缩为纯配置域聚合(WorkLine + safety 跨域 enum + rack.model 透传);`src/app/workline/repositories/__init__.py` 收缩为 workline_repository + safety_incident_repository + rack.repository 透传。
- `src/app/workline/repositories/workline_repository.py` 跨域 bridge import 修正:`runtime_hold_repository` 改指 `src.app.runtime.orchestration.repositories.runtime_hold_repository` 新路径。
- `migrations/env.py` mapper 注册链拆分:`WorklineBinCellReservation` / `WorklineInbox` / `WorklineRackPosition` / `WorklineSession` / `WorklineTimeline` 5 个已迁 symbol 改指 `src.app.runtime.orchestration.models`,`WorkLine` 保留 `src.app.workline.models`。
- `scripts/architecture-guardrails.allowlist` 第 55-56 行 R-I3b path 字段同步到 `src/app/runtime/orchestration/repositories/{session,smt_inbound_handoff}_repository.py` 新路径;`legacy_entry_id` 保留旧版 `legacy:src/app/workline/repositories/...` 以稳定 audit trace 反向查找。
- `scripts/generate_legacy_matrix.py` 扩展 `MIGRATED_REPOSITORIES` 映射(10 条 legacy → runtime 路径)+ `_append_ri3b_seed_paths` 的 `scan_paths` 增加 `src/app/runtime/orchestration/repositories`,并把扫描到的新路径通过 `MIGRATED_REPOSITORIES_TO_LEGACY` 映射回旧版路径,保证迁移后 R-I3b seed 条目仍以旧版路径记入 CSV(audit trace 稳定性)。
- `docs/architecture/legacy-cleanup-matrix.csv` 重新生成:668 条(原 831 条减少 165 条已迁文件条目,新增 2 条 R-I3b seed)。
- `docs/architecture/legacy-cleanup-matrix.md` 统计表同步:total 668 / model 39 / repository 7 / rebuild 412 / phase2 199 / workline 455。
- `tests/characterization/workline_legacy/test_business_semantics_characterization.py` 硬编码路径修正:`smt_inbound_handoff.py` 改指 `src/app/runtime/orchestration/models/smt_inbound_handoff.py`。

### Fixed
- `tests/architecture/test_workline_service_shim_contract.py` 2 个 xfail 契约转硬断言:`test_workline_models_shrunk_to_workline_only_after_stage6` + `test_workline_repositories_shrunk_to_workline_only_after_stage6` 现以 `assert not _file_exists(...)` 强制验证 workline/models/ 与 workline/repositories/ 下运行态文件物理删除。

### Added (F-3..F-7 阶段 6 评审 follow-ups)
- **F-5** `tests/architecture/test_cleanup_matrix_guardrail.py` 新增 5 个 audit trace 守护测试,补 `test_phase0_legacy_matrix_contract.py` 未覆盖的 8 个审计字段一致性(entry_type / relative_path / symbol_or_route / current_owner / business_semantics / phase4_carrier / classification_status / risk)+ entry_id 格式 `legacy:<path>:<symbol>` + entry_id 唯一性 + classification_status 枚举收敛到 `{final, pending-review}` + allowlist 第 5 列 legacy_entry_id 反向引用必须在 CSV 中存在。
- **F-7** `tests/runtime/orchestration/test_device_command_gateway.py` 新增 7 个 runtime 行为锁定测试,锁定 `device_command_gateway` 迁入 `runtime/orchestration/services/` 后的 runtime 行为契约:模块路径与单例符号可导入 + reserve_sandbox_command 设备不存在返回 False / maintenance_mode 拒绝抛 `_DeviceCommandGovernanceError` + dispatch 设备/通信配置缺失返回 False / httpx ACK 超时转 `RuntimeError("OUTBOX_ACK_TIMEOUT")`。用 `SimpleNamespace` + `AsyncMock` + `patch` 隔离,不依赖真实 DB/httpx。

### Changed (F-3..F-7 阶段 6 评审 follow-ups)
- **F-3** `src/app/workline/services/diagnosis_verdict_builder.py` 改名为 `diagnosis_verdict_builder_service.py`,对齐目录内 `_service.py` 命名约定;`__init__.py` export 路径同步。
- **F-4** `tests/architecture/test_workline_service_shim_contract.py::test_workline_service_config_only_after_stage6` 由 `hasattr` 存在性守卫改为行为验证:`asyncio.iscoroutinefunction` 校验 async callable + `inspect.signature` 校验 `db` 入参与返回类型注解,并用 `typing.get_args` 提取 union 成员验证返回类型契约,确保配置域 CRUD 方法形态稳定。
- **F-6** `src/app/workline/services/__init__.py` `_LAZY_SHIM_MAP` docstring 改写:从"live caller 死引用/死代码,未触发"夸饰改为"未初始化 service 属性的 fallback"准确描述。3 处 caller 真实情况精确标注:`runtime_intent_effects.py:1545/1627` 是 `self._inbox_service`/`self._bin_cell_reservation_service` 属性未注入时的 fallback import(属性注入后不触发,路径是活的防御性兜底,非死代码);`callback_orchestration_service.py:35` 是 TYPE_CHECKING 块内 type hint(运行时不触发,静态类型检查用)。`_LAZY_SHIM_MAP` 内容不变。

## [0.10.2.1] - 2026-06-30

> **Note**:0.10.1.0(阶段 3)与 0.10.2.0(阶段 4)版本号 bump commit 已落地(`f0aab25a` / `f492f16a`),但阶段 3/4 改动描述未单独切分为独立 [0.10.1.0] / [0.10.2.0] 段,统一累积在 [Unreleased] 段中;659b9e78 阶段 6 重新校准后该累积段正式落盘为 [0.10.2.1]。如需补拆,可对照 `f0aab25a` / `f492f16a` / `8ff83d5c` / `6cd0aa23` / `628dbfdf` / `2905eb54` / `34c10eae` / `f7970a5d` / `4bb76b00` 9 个阶段 3+4 提交单独回填。

### Removed
- `src/workline_runtime/` 整目录物理删除 (50 源文件: contracts/、diagnostics/、plugin_sdk/ 子包 + plugin_base 等 15 顶层模块) — Phase 2 burn-down 阶段 3 目标态锁定。
- `tests/workline_runtime/` 117 文件 + `tests/integration/workline_runtime/` 6 文件 整目录删除;行为契约已由 `tests/contracts/workline/` 9 个下游 contract 持续覆盖 (107 passed, 2 xfailed)。
- `src/app/runtime/orchestration/services/runtime_reconciliation_service.py` 整文件删除 (`RuntimeReconciliationFacade` + `runtime_reconciliation_facade` 公开 API 物理删除) — Phase 2 burn-down 阶段 5 完成。`src/app/runtime/orchestration/services/__init__.py` 同步移除 export。`workline_runtime_reconciliation_service` 单例保留为 `WorklineRuntimeReconciliationService` 公开 API,workline/services/runtime_reconciliation_service.py 20 行 shim 仍在,阶段 6 整 workline 域清空时一并删除。
- **阶段 6 (T6) workline 运行态 service 物理删除**:workline/services/ 下 19 个 service 文件 + 顶层 helper 4 文件 (runtime_services / inbox_claim_bucket / outbox_dispatch_support / diagnostic_support) + 1 facade (RuntimeReconciliationFacade) + 4 v1 router 物理删除 (runtime / runtime_hold / trace / inbound_handoff);v1/__init__.py 改写仅导出 `workline` + `operation` router。
- 5 个 dead test 物理删除 (`tests/api/test_runtime_hold_api.py` + `tests/api/test_smt_inbound_handoff_api.py` + `tests/api/test_workline_runtime_api.py` + `tests/resource/test_resource_projection_service.py` + `tests/workline/test_object_transition_event.py`) — C2 阶段未清空 dead test 一起清理。

### Changed
- `src/app/runtime/orchestration/diagnostics/` 子目录建立 (5 子模块: builder / codes / failure_mapper / models / registry + 聚合层 `__init__.py`) — 完整迁移 wlr `diagnostics/` 子包,实现 diagnostics 公开 16 符号垂直内部化。原 `consumers/diagnostics_bridge.py` 改名为 `diagnostics.py` 并迁出 `consumers/` 子目录 (与 `events_bridge.py` 等 bridge 平级)。
- `consumers/` 子包退出 R-WLR trust zone (`EXCLUDED_PREFIXES` 在阶段 2 已为终态空 tuple);`consumers/runtime_inbox_consumer.py` 持续作为 RuntimeInbox 单点入口,无 wlr 真引用。
- `scripts/architecture-guardrails.sh` 中 `rule_wlr_import()` 函数永久保留;`tests/architecture/test_wlr_import_guardrail.py` 新增 `test_excluded_prefixes_does_not_contain_consumers` + `test_no_consumers_in_wlr_allowed_paths` + `test_consumers_directory_still_exists` 三个新增测试 + WLR_ALLOWED_PATHS 移除 `consumers/` 路径,作为永久安全网防止 wlr 残留回归。
- 8 个 tests (`tests/contracts/workline/test_callback_runtime_contracts.py` + `tests/mock/{test_wms_mock_server, test_ecs_mock_server, ecs_mock_server}.py` + `tests/api/{test_callback_route_contracts, test_runtime_hold_api}.py` + `tests/workline_plugins/test_rough_sorter_plugin.py` + `tests/helpers/workline_test_plugin.py`) 与 2 个 scripts (`scripts/data/sync_test_workline_devices.py` + `scripts/data/repair_runtime_holds.py`) 的 wlr import 重定向到 mirror;`tests/architecture/test_workline_compat_mirror.py` + `tests/characterization/workline_legacy/test_business_semantics_characterization.py` 调整到 wlr 物理删除后的自包含校验;`tests/workline_plugins/test_plugin_template_assets.py` 保持原 reverse-validation 断言。
- `src/app/workline/models/runtime_hold.py` 内联 `_LocalNgReasonSource` 本地副本 (PLUGIN / DEVICE_ERROR / RUNTIME / MANUAL 四值),避免引入 `src.app.workline.domain.ng_reason` 触发反向循环 (domain.services → resource.services → workline.repositories → models)。
- **阶段 6 (T6)** `device_command_gateway.py` 物理迁入 `src/app/runtime/orchestration/services/device_command_gateway.py`;workline 域对应位置删除。跨域调用方 (outbox_dispatch_service / outbox_engine / smt_inbound_handoff_route_service / write_back_service) import 路径跟随新位置。`scripts/architecture-guardrails.allowlist` R-I3b 行路径同步跟随;`scripts/generate_legacy_matrix.py` 新增 R-I3b 物理迁入后 path 跟踪 hardcoded seed,`docs/architecture/legacy-cleanup-matrix.{md,csv}` 同步 (830 条, runtime 7→8, service 324→325, keep-contract 195→196, phase5-tech 277→278)。
- **阶段 6 (T6)** `workline_service.py` 拆分保留 WorkLine 配置 CRUD,删除运行态方法(已迁入 phase4 capability);`workline/v1/__init__.py` + `workline/v1/operation.py` 修 C2 incomplete cleanup,删除已删 router 的 import,改写为 `src.app.runtime.capabilities.phase4` + `src.app.runtime.orchestration.services.intent` 直连。
- **阶段 6 (T6)** WorkLine 配置域收敛:workline 域 22 个 service shim 物理删除,保留配置 CRUD + manifest + plane scene + 4 个 domain service + plugin SDK + diagnostic_service (C4d keep-contract);`__all__` / `_LAZY_SHIM_MAP` 收敛到 9 个真实 module export + 3 个死引用 tombstone。
- 8 个 workline API tests 与 17 个 R-I3 guardrail tests 的 import 路径改写 (workline.services.* → runtime.orchestration.services.* 与 workline.v1.* → workline.v1.{workline,operation})。

### Added
- **阶段 4 (T4)** C1 + C2 + C3:13 service 物理迁入 `src/app/runtime/orchestration/services/{facade_impl,inbox,hold,intent,query,trace}` — facade 委托本地化 + 7 service 迁入 + 6 service 迁入 + 跨子包循环修复;workline 侧对应位置改写为 PEP 562 lazy shim (`_LAZY_SHIM_MAP` + `__getattr__`),保留全部 public API。
- **阶段 4 (T4)** C4a:2 service phase2 rebuild — `smt_inbound_handoff_service` 与 `runtime_query_service` 物理迁入 `src/app/runtime/orchestration/services/{intent,query}/`;workline 侧 shim 文件缩至 ~15 行;`importlib.import_module` priming 防御 `runtime_reconciliation_service_impl → trace 子模块` 部分模块循环(防御点写于 `__init__.py` 顶部注释)。
- **阶段 4 (T4)** C4b:5 service phase4 capabilities 重建 — `bin_cell_reservation` / `ng_return_item` / `single_layer_rack_orchestration` / `start_admission` / `station_lease` 物理迁入 `src/app/runtime/capabilities/phase4/`,同样 PEP 562 lazy shim 模式。
- **阶段 4 (T4)** C4c:3 debug service 物理删除 — `integration_debug_service` / `debug_data_cleanup_service` / `sandbox_cleanup_service` 连同 2 个 repository / `models/integration_debug.py` / `v1/integration_debug.py` 物理删除,3 个 v1 endpoint (`cleanup_sandbox_workline` / `cleanup_debug_data_workline` / `cleanup_all_debug_data`) + `_is_debug_cleanup_enabled` + `debug_router` 整段子树拆解。`diagnostic_service` 因 5 个 production critical path 调用方(intent_effects / reconciliation_impl / inbox_batch_processor / callback_ingress / diagnostic_support) keep-contract 保留(归 C4d)。
- `tests/runtime/orchestration/test_outbox_dispatch_async_guard.py` 新增 4 个 isawaitable 防御回归测试,锁住 sync/async repo 双路径行为。

### Fixed
- **阶段 4 (T4)** 跨子包循环导入修复:`runtime_reconciliation_service_impl → trace 子模块 → workline.services` 部分模块循环通过 `importlib.import_module` priming 阻断;C4a 与 C4b 走同模式 PEP 562 lazy shim 推迟到首次属性访问再加载。
- **阶段 4 (T4)** 跨层 guardrail `R-I3b` allowlist 同步:阶段 4 迁入路径下 service 跨层调用 entry path 跟随新位置,allowlist 验证路径精确匹配。
- **阶段 6 (T6)** `outbox_dispatch_service._escalate_status_precheck_wait_if_needed` 与 `_dispatch_blocked_resource_heads` 之前直接 `await updater(...)` / `await getter(...)`,假定 repo 返回 awaitable。runtime fallback 路径(repo 走同步实现)下会抛 "object dict can't be used in 'await' expression"。加 `isawaitable` 防御,与同模块 `dispatch_attempt_service` / `timeline_sequence_service` / `outbox_engine` 已有的 isawaitable 模式一致。回归测试 `tests/runtime/orchestration/test_outbox_dispatch_async_guard.py` 锁住 sync/async 双路径。

### Migration notes
- 公共 import 路径不变:workline 侧消费者仍可 `from src.app.workline.services.smt_inbound_handoff_service import smt_inbound_handoff_service` 走 lazy shim;新代码推荐直接 `from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import smt_inbound_handoff_service`。
- 3 个 debug v1 endpoint (sandbox / debug_data cleanup) 已下线,调用方如有依赖需在阶段 5 / 主计划重新审视。

### Plan deviation (阶段 6)
本 PR 不完成 `WorkLine 不再拥有运行状态` 门禁,workline 域以下 2 个 follow-up 子门禁转入后续 PR(由 `tests/architecture/test_workline_service_shim_contract.py` 中 2 个 xfailed 锁定):
- **F-1** workline/models/ 16 个运行态 model 与 workline/repositories/ 11 个 repository 物理删除(从 workline 域迁入 runtime/orchestration 域 models/repositories)。前置:53+ 处 `from src.app.workline.models.{inbox,session,timeline,...}` 跨子包 import 改写。`safety.py` 与 `safety_incident_repository.py` 例外保留(承载 `WorkLineRuntimeStatus` 跨域 enum 与配置域审计表)。
- **F-2** 28 处 `from src.app.workline.X` runtime 域 import 改写(其中 7 处 `workline_repository` 跨域桥接,21 处 model / service / v1 router 引用),workline_repository 迁入 runtime/orchestration/repositories/。

完成 F-1 + F-2 后 Phase 2 burn-down 阶段 6 门禁才真正关闭。

### 版本
- 版本 `0.10.2.0 → 0.10.2.1` patch bump — 阶段 5/6 cleanup patch 增量(workline 域 service shim 大幅瘦身 + facade 物理删除 + device_command_gateway 迁出),阶段 6 门禁 follow-up 子项 F-1 + F-2 转入后续 PR。

## [0.10.0.0] - 2026-06-29

### Added

- **RuntimeInbox 单点入口落地**。`src/app/runtime/orchestration/consumers/` 成为 RuntimeInbox 唯一允许访问 wlr 的 production 入口；`RuntimeInboxConsumer` 委托既有 workline inbox batch processor 实现，阶段 3 业务迁入前作占位 facade。所有 28 处外部生产路径对 wlr 的引用已收敛到这一处 trust zone。
- **运行时工具与概念镜像**。`src/app/workline/utils.py` 与 `src/app/workline/trace_context.py` 完整镜像 wlr 顶层符号，`src/app/runtime/orchestration/diagnostics_bridge.py` 聚合 12 个 wlr diagnostics 公开符号；`src/app/runtime/orchestration/runtime_inbox.py` 等模块现在通过新镜像访问 capabilities，不再直接 import wlr。
- **workline 域业务概念镜像**。`src/app/workline/domain/{ng_reason, material_identity, plugin_manifest, contracts}.py` 镜像 wlr 同名模块，使 ng 决策、material 标识、plugin manifest、SixInOne 契约等业务概念在 workline 域内自洽，不再跨域访问 wlr。
- **workline plugins 子目录与镜像**。新建 `src/app/workline/plugins/`，提供 `plugin_base` / `plugin_context` / `session_resolver` / `null_plugin` / `plugin_next` 与完整 `plugin_sdk` 包（含 classifiers、contracts、normalizers 等子模块），workline 域插件机制具备独立命名空间。
- **orchestration bridge 聚合**。`src/app/runtime/orchestration/{intent_bridge, orchestrator_bridge, topology_bridge, events_bridge, sandbox_catalog_bridge, resource_wait_evidence_bridge, lock_bridge, business_identity_bridge, enums}.py` 与 `src/app/workline/runtime_services.py` 统一聚合 wlr 编排型符号，外部服务可通过专属 bridge 访问 capabilities。

### Changed

- **R-WLR 护栏严格生效**。`scripts/architecture-guardrails.allowlist` 的 28 条 `R-WLR` 例外全部清空；任何 production 路径 import `src.workline_runtime` 都必须通过 `consumers/` trust zone 唯一出口，guardrail 在 pre-commit hook 与 CI 中常态运行。
- **架构护栏测试套件扩展**。`tests/architecture/test_wlr_import_guardrail.py` 与新建的 `tests/architecture/test_workline_compat_mirror.py` / `test_plugin_mirrors_mirror.py` / `test_bridges_smoke.py` / `test_runtime_inbox_consumer.py` 持续验证镜像 AST 签名、consumer 仅在 trust zone、trust zone 文件无遗漏导入。

### Fixed

- `RuntimeInboxConsumer.consume_sync` 现在通过 `payload_dict.setdefault("consumer_id", ...)` 注入 `consumer_id`，同时保留 caller 明示值；`_consumed_ids` 改为 `deque(maxlen=10_000)` 环形缓冲并对 `source_event_id` 自动去重，防止长跑消费者内存泄漏与重复回放。
- `tests/architecture/test_plugin_mirrors_mirror.PROJECT_ROOT` 由硬编码 worktree 路径改为 `Path(__file__).resolve().parents[3]`，解 worktree 切换与 CI 路径依赖。

## [0.9.1.0] - 2026-06-28

### Added

- WorkLine Phase 1 Packet D 完成 capability 边界交付：业务 capability 只能取得 query/effect port contract，入站 normalizer 通过独立上下文与注册表管理，不再进入通用 `RuntimeCapabilityContext`。
- WMS 能力面补齐剩余 4 个 port：`WmsDocumentPort`、`WmsFulfillmentPort`、`WmsEventPort` 和 `WmsReconciliationQueryPort`，Phase 1 目标态 WMS 7 ports 全部落地。
- `InboundNormalizerProfile` 新增 provider/event/correlation 三层校验，阻止未声明或来源不一致的 WMS/ECS/device event profile 进入入站边界。
- 新增 import-linter `capability-isolation` contract，并接入 `scripts/git-quality-gate.sh`，持续检查 runtime capability registry 不依赖 WMS/device/callback/orchestration 实现模块。
- 新增 R-I3c 架构护栏和回归测试，覆盖非 consumer orchestration 文件、多行 import、alias-qualified type hint、普通表达式引用以及目录前缀 allowlist 绕过。

### Changed

- `docs/architecture/workline-and-plugin-restructuring.md` 与 `docs/architecture/file_index.md` 同步 Phase 1 Packet D 完成状态、文件索引和验证证据。

### Fixed

- 收紧 inbound normalizer 边界修复 code review 发现：R-I3c 不再 broad allowlist 整个 orchestration 目录，import-linter 同时检查 `inbound_normalizer_registry`，并移除可被字符串前缀伪造的 caller-module runtime guard。

## [0.9.0.0] - 2026-06-25

### Added

- WorkLine 重构 **Phase 0：目标态锁定与架构护栏** 全部 7 任务交付完成。Phase 0 是整个 WorkLine + plugin 体系重构（主计划 `docs/architecture/workline-and-plugin-restructuring.md`）的目标态基线，锁定后续 Phase 1-5 实施边界。
- **P0-001 Target State Contract** (`docs/architecture/target-state-contract.md`)：抽取主计划可执行合同，含 P0 系统能力 10 项、域边界 8 域、状态所有权矩阵 7 类对象、Authority Matrix 11 类事实权威来源、Plane 读模型边界、不做清单 14 条。
- **P0-002 Legacy Cleanup Matrix** (`docs/architecture/legacy-cleanup-matrix.{md,csv}`，2191 entries / 0 pending-review)：逐入口标记 delete/rebuild/move/keep-contract 策略，含 service module-level def + `__all__` 导出符号穷尽覆盖。生成器 `scripts/generate_legacy_matrix.py` 可复现。
- **P0-003 Behavior Contract Baseline** (`tests/contracts/workline/`, `tests/characterization/workline_legacy/`, `tests/fixtures/workline_contract/`)：10 BC 全覆盖（强制 5 contract + 1 characterization + 4 strict xfail 壳），覆盖 start admission / runtime snapshot / handoff / resource projection / 粗分机入库 / 满箱交换 / 分拣机入库 / 缺 event_id / WMS authority cache / Event_Push 响应。28 pass + 3 strict xfail。
- **P0-004 ExecutionCorrelation Migration Matrix** (`docs/architecture/session-correlation-matrix.md`)：逐文件列 39 个跨域 session FK 迁移路径（0 遗漏），按 resource/handling/rack/device/wms_integration/workline-runtime/material/sys 域分组。发现 device `session_id_int` ↔ session `awaiting_command_id` 外键环（HIGH 风险，进入 Phase 1 CEO-010）。ExecutionCorrelation schema 字段对齐主计划 §9.2（trace_id / source_event_id / business_owner_key），idempotency 引用主计划 §5.4 独立 idempotency_keys 表。
- **P0-005 Device Command Contract** (`docs/architecture/device-command-contract.md`)：以第三方设备白皮书为权威输入，锁定 Command-Ack-Callback 异步闭环、设备 6 态（IDLE/RUNNING/ERROR/OFFLINE/UNKNOWN/MAINTENANCE）、Event_Push 固定 ACK、DeviceCommand 顶层字段白名单 + 禁止字段（PLC/坐标/关节/安全回路）、扫码平台互锁与预取约束。
- **P0-006 External Contract Profile + IntegrationLab** (`docs/contracts/external-contract-profile.md`, `docs/architecture/integration-lab-and-simulator.md`, `tests/support/external_contract_profile.py`, `tests/fixtures/external_contracts/wms/default/`)：按 `provider_code + contract_version` 描述 WMS/ECS provider 外部合同；Pydantic schema 落 tests/support/（禁止 src/app/ import，Phase 1 CEO-013 升级到生产路径）；WMS fixture 5 个必填 case（success/reject/timeout/duplicate/missing_event_id）。
- **P0-007 Architecture Guardrails** (`scripts/architecture-guardrails.sh`, `scripts/architecture-guardrails.allowlist`, `tests/architecture/`, `docs/architecture/architecture-guardrails-spec.md`)：将主计划 §7.5 核心 5 条不变量（C1-C5）+ I3 capability 注入（R-I3a/R-I3b）映射为可执行扫描脚本。phase-aware 模式（phase0 warn-only / phase1 enforced / phase2 缩减 allowlist）；seed allowlist 31 条全部关联 `legacy_entry_id`（C1×5 + C2×22 + R-I3b×4 含 device 实现）。删任意 seed 行后 phase1 退出码 1（enforcement 真生效）。
- `scripts/git-quality-gate.sh --check architecture` 新增。Jenkinsfile `Quality Checks` stage 新增 `Architecture Guardrails` 并行步骤，默认 phase0，可通过 `ARCHITECTURE_PHASE` 环境变量切 phase1。
- `tests/architecture/` 6 个测试文件（C1-C5 + R-I3a/R-I3b，24 tests）；C5 使用 `tests/support/runtime_inbox_contract.py` 目标态状态机（RECEIVED/PROCESSING/PROCESSED/FAILED/DEAD_LETTER 6 转移），不 import legacy `WorklineInbox`。
- 3 个测试锁定 SPEC 验收为不变量：`test_phase0_legacy_matrix_contract.py`（service inventory + rebuild/move 必填 target/blocking + CSV 与生成器一致防漂移 + `__all__` 入矩阵 + R-I3b seed 指向具体 port）、`test_external_contract_profile_fixtures.py`（P0-006 5 fixture 真实可校验）、`test_git_quality_gate_architecture_profile.py`（quality profile 真执行 guardrails）。

### Changed

- 主计划 `docs/architecture/workline-and-plugin-restructuring.md` §9.7 `wms_integration` API 入口边界收口：履约请求端点从 POST 改为 GET 只读；补充 effect 出口约束（出站 WMS 履约/库存事务/PKG 绑定只能由 runtime/orchestration 经 RuntimeIntentLog + EffectPort 调用，wms_integration 不提供公开创建履约请求的 POST API）。

### Notes

- Phase 0 不修改任何生产代码（`src/app/**`、`src/workline_runtime/**`、`src/workline_plugins/**` 零变更），符合 Phase 0 硬边界 T8。
- 后续 Phase 1 启动条件：本 PR merge + 重新评审（autoplan）确认 B 方案可执行，然后启动 CEO-001 wms_integration 7 ports / CEO-007 runtime/orchestration 骨架 / CEO-010 DeviceCommand contract。

## [0.8.1.0] - 2026-06-23

### Added

- 建立 WorkLine C0 resource projection 基座：resource active projection 统一切到 `workline_session_id`，补齐 FK、清理报告、integrity check 和 material location drift 诊断，后续 Trace 和强校验不再依赖 string session 双轨。
- 新增统一 `object_transition_events` 事件账本，resource projection 与 handling queue membership 可按 trace、session、object 和 source event 回放 from/to transition，并通过派生幂等键避免重复写入。
- 新增 `BinTransitMembership` active/history 投影，支持真实 bin、placeholder、队列切换、离队、冲突 RECONCILING 和 handling callback best-effort 写入。
- `RESOURCE_WAIT` 等待合同升级为 manifest 声明的 subject 语义，context、diagnostic 和 timeline evidence 都带 `subject_type`、`subject_key`、`projection_type`，便于前端和后续强校验准确归类等待资源。
- 补充 WES 领域边界与调度 Adapter 设计，明确 WMS 是货架、料箱、库位、库存规划和首版搬运调度权威，WES 通过 Adapter port 发出调度意图并保留 evidence。

### Changed

- Docker compose 和 mock Dockerfile 同步代理/no-proxy 配置，构建和本地联调环境在公司网络下更稳定。
- Agent 项目入口文档同步当前仓库路径、GitNexus 索引规模和团队入口规则，减少跨工具规则漂移。

### Fixed

- 修正 resource projection、handling lifecycle 和 WorkLine runtime 的回归覆盖，确保迁移、导出合同、幂等重放、冲突路径和 RESOURCE_WAIT subject 合同都有对应测试。

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
