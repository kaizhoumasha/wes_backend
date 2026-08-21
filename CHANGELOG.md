# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.27.2.0] - 2026-08-22

### Changed

- 将 Phase 9—13 重排为人工 Bin 纵向闭环、旧平台清理、单次初始 Schema 基线、自动业务插件持续交付和当前范围系统验收，后续实施可按真实产品节奏线性推进。
- 明确人工物料业务继续由 WMS/PDA 拥有，WES 只负责 Bin 物理执行；自动上架与自动拣货移至 Phase 12，并继续受独立合同和计划批准约束。
- 同步 SRS、顶层架构 SPEC、总控计划、文档索引、运行手册和 TODO 的阶段归属，消除旧十二阶段及 Phase 9 自动上架表述。

### Removed

- 将已被新阶段策略取代的旧 Phase 11 最终 Schema 基线过程计划移出项目目录，项目内不保留副本、占位或转发入口。

### Verification

- 本次仅修改人类可读文档与 Release 元数据；`git diff --check`、相对链接、归档目标哈希和原路径缺席检查均通过。
- 未修改生产代码、测试、迁移、机器可读合同或部署配置；候选人工 Bin 严格合同仍保持独立评审状态，未包含在本发布中。
- 状态边界为 `DOCUMENTED — NOT IMPLEMENTED — NOT DEPLOYED`。

## [0.27.1.0] - 2026-08-22

### Changed

- 将 RuntimeInbox PostgreSQL 验收改为按变更影响选择，并稳定采样窗口、预算基线、Git provenance 与 HEAVY 映射缓存；未触及相关路径的提交不再重复承担完整性能验收耗时。
- 将 TEST 部署的源码验证与运行镜像解耦，CI 治理文件和 HEAVY 映射变更通过显式 allowlist 进入发布验证，避免无关源码差异阻断不可变镜像切换。
- 保留跨提交的 Docker 依赖层缓存，并让授权初始化、数据库创建和部署检查统一使用镜像内可用的 Python/runtime 合同。

### Fixed

- 修复 WMS Provider 空 profile、WorkLine 冷导入、Compose 前端镜像占位、Transport worker 冷启动与时序测试裕量导致的 CI 偶发失败。
- 修复构建隔离 Redis 接线与带密码 readiness、从 `template0` 创建全新测试数据库、SQL 转义、基础管理员凭据注入和授权 bootstrap 的发布阻断问题。
- 在应用启动失败时输出诊断日志，并在 TEST 切换前 fail closed 校验 WMS profile，避免进入维护态后才发现配置缺失。

### Verification

- 本发布批次汇总 PR #126–#149；对应代码已逐项评审并合入 `develop`，发布汇总不重放或改写历史提交。
- 精确代码快照 `4ca6045` 的 QUALITY 为 3791 passed、4 个既有外部条件 skip；本机完整编排的 11 个容器均通过健康检查。
- 本条目仅完成 `0.27.1.0` 发布元数据汇总，状态边界为 `IMPLEMENTED — NOT DEPLOYED`。

## [0.27.0.0] - 2026-08-21

### Added
- 新增代码所有的 canonical 权限目录与唯一 `AuthorizationBootstrapService`，统一完成权限同步、系统角色匹配、管理员初始化和精确缓存修复，并对重复或畸形权限码 fail closed。
- TEST 配对切换新增前后端不可变 revision、OpenAPI 与权限摘要校验；新数据库使用独立 Redis cache namespace，避免复用旧数据库缓存数据。

### Changed
- 权限定义改为运行时只读：权限树保留查询导航，创建、更新、删除、恢复和运行时同步入口全部退役；初始化、同步、开发 seed 与 E2E provisioning 统一走同一授权服务。
- 通用 CRUD 生成标志现在完整约束单条与批量写路由，并为批量删除、永久删除等不同操作保留独立权限码。
- WMS Transport 回调继续走 Transport 专属合同与持久化 evidence；浏览器 RBAC 权限和厂商 API permission 保持分层，不再依赖通用外部回调入口。

### Fixed
- 修复树父节点与 Snowflake 主键类型不一致、授权预览产生副作用、缺失系统角色未纳入缓存失效用户，以及既有数据库授权恢复标记不完整的问题。
- 修复部署切换在 provenance 校验、维护态菜单清单提取、数据库隔离和失败恢复上的窗口，使任何不匹配组合在暴露流量前终止。

### Removed
- 删除旧 `/callback/external` 路由及其 E2E provisioner、运行时权限写接口、过期 bootstrap 脚本和静态初始化 SQL，不保留 shim、别名或兼容双路径。

### Verification
- 最终候选 QUALITY 全门禁通过：3771 passed、4 个既有外部条件 skip；Ruff、Bandit、架构、测试拓扑和 FAST 预算检查均通过。
- 相对原始分支基线选择的 10 个 HEAVY owner 在隔离 PostgreSQL、Redis 与迁移环境中 83 passed、0 skipped。
- 跨仓计划审计为 48 DONE、2 项等价 CHANGED、0 PARTIAL；最终 frozen-head review 为 No issues found。状态边界为 `IMPLEMENTED — NOT DEPLOYED`。

## [0.26.10.0] - 2026-08-21

### Added
- 现场开发与运维可通过受权限保护的 Swagger 接口创建四类真实 `TransportTask`，并查询 WES 已持久化的任务状态与最新 callback evidence。
- 补充 Transport 调试入口的合同、架构边界和运维说明；实施计划完成后移至项目外归档，并明确本地观察不能替代 WMS/RCS 联调、物理完成或业务验收。

### Changed
- 调试任务完整复用既有 Transport 创建、提交、ACK、callback、evidence 和终态链路；粗分发布器仅精确消费 `TRANSPORT_DEBUG` 的无业务绑定 outcome，普通业务 caller 继续 fail closed。
- 状态查询在单条 PostgreSQL 语句中读取任务及最新 evidence，避免并发 callback 提交期间返回不一致的组合快照。

### Fixed
- 补齐 Transport 调试 API 在 OpenAPI 中的 `400/404/409/422/503` 失败响应合同，Swagger 可直接识别成功与失败结果。

### Verification
- 当前 `3cfca6ba` 快照的 QUALITY 全门禁通过：3683 passed、4 个既有外部条件 skip；Ruff、Bandit、架构、测试拓扑和 FAST 预算检查均通过。
- 相对 `origin/develop` 选择的 12 个 HEAVY owner 在隔离 PostgreSQL、Redis、Celery 与迁移环境中 129 passed、0 skipped。
- 行为路径覆盖审计为 94%（30/32），计划完成度 6/6；预落地 Review 无剩余 P1/P2，原状态快照并发一致性意见已闭环。
- 浏览器 QA 已验证 Swagger 创建、状态观察与错误合同；真实远端 WMS/RCS、现场设备完成和业务验收仍未执行。

## [0.26.9.0] - 2026-08-20

### Added
- WMS Provider profile 新增必填 `transport_submit_path`，支持按目标 WMS OpenAPI 配置并冻结搬运提交相对路径，同时保持路径大小写。
- 新增权限目录、基础角色、bootstrap 与菜单真源收敛的设计和后续实施计划；本版本仅交付方案，不包含权限生产实现。

### Changed
- 将搬运交互阶段统一改为“搬运提交、容器中间位置事件、搬运最终结果”等语义名称，不再使用数字阶段代号。
- 明确 `TransportTask` 负责入线前搬运及资源围栏；只有最终结果确认 Bin 到达工作线且扫码身份匹配后才创建 `BinExecution`。

### Fixed
- 本机 8012 WMS Provider Mock 按同一 profile 注册搬运提交路径，并与 8011 Transport Mock 复用 `256 KiB` 预缓冲请求体限制。
- 补齐自定义路径的真实 worker/HTTP E2E、Provider Mock `202/413` 合同测试及共享 Mock 的 HEAVY 精确所有权。

### Verification
- 提交钩子 QUALITY 全门禁通过：FAST 为 3641 passed、4 个既有外部条件 skip；Ruff、Bandit、架构、测试拓扑和预算检查均通过。
- selector 合同 286 passed；相对 `origin/develop` 选出的 HEAVY owner 在隔离 PostgreSQL、Redis、Celery 与迁移环境中 187 passed、0 skipped。
- 行为路径覆盖审计为 100%，计划/范围审计 4/4 完成，Greptile 评论 0 条，预落地 Review 无剩余意见。
- 本次证据证明代码、合同和本地 Mock 闭合，不替代真实 WMS/RCS 联调、现场验证或业务验收；权限目录/bootstrap 仍属于后续实施范围。

## [0.26.8.0] - 2026-08-20

### Fixed
- WorkLine 查询在应用首次加载、重启及不同模块导入顺序下均会绑定正式 `WorkLineService`，不再因误用同名模块返回 HTTP 500。

### Verification
- 合并最新 `develop` 后，QUALITY 全门禁通过：3626 passed、4 个既有外部条件 skip；Ruff、Bandit、架构、测试拓扑和 FAST 预算检查均通过。
- 当前差异未选择核心 HEAVY 测试；fresh-process 路由回归测试通过，行为路径覆盖审计为 100%，预落地 Review 无剩余问题。
- 完整本地环境停止并重新启动后，正式路由 `POST /api/v1/workline/work_lines/query` 返回 HTTP 200 和标准分页响应。

## [0.26.7.0] - 2026-08-20

### Changed
- 将 GitHub `develop` 固定为代码与评审真源、GitLab `develop` 固定为发布镜像；合入必须使用 merge commit，再经独立授权将 GitLab fast-forward 到同一 SHA，禁止 squash、rebase 和 force push。
- 后端发布 Job 固定从 `develop` 加载普通 Pipeline，并要求 GitLab webhook 使用项目级 Secret Token；Poll SCM、Multibranch 和手工构建仅可用于不发布验证。
- Phase 8 当前态收敛为 backend RC `CLOSED`，以 `88-f51677b`、manifest digest、OCI revision 和 source-manifest 作为不可变证据；供应商一致性、现场联调和业务验收继续保持 `NOT RUN`。

### Fixed
- 发布门禁改为只接受经验证的 GitLab `develop` PUSH：`gitlabBefore` 必须为非零 previous SHA，`gitlabAfter` 必须匹配检出 HEAD，且 before 必须为 HEAD 祖先。
- 经验证的 `develop` PUSH 在发布前按 previous SHA 执行 Mock 合同和 selector 选中的 HEAVY；MR 使用目标分支作为差异基线，其他分支 PUSH、MR、手工和轮询构建均不能发布镜像。
- 移除后端 CI 自动触发 TEST 部署的陈旧说明，TEST 部署改为部署人员独立触发并显式选择 immutable 前后端镜像。

### Verification
- 提交钩子 QUALITY 全门禁通过：3625 passed、4 个既有外部条件 skip；Ruff、Bandit、架构、测试拓扑和 FAST 预算检查均通过。
- Jenkins 与 selector 聚焦合同测试 17 passed；staged selector 选出的 5 个 HEAVY owner 共 85 passed、0 skipped。
- 应用代码覆盖率不适用；CI fail-closed 合同缺口为 0，预落地 Review 无剩余意见，计划完成度为 14/17，其余 3 项等待独立 merge、GitLab fast-forward 和后续重放授权。
- 本次证据只证明仓内治理与 CI 合同闭合，不替代 Jenkins/GitLab 现场 Secret Token 配置、供应商一致性、现场联调或业务验收。

## [0.26.5.0] - 2026-08-19

### Added
- 新增 `LineRunEpoch` 及设备、位置冻结绑定，为 WorkLine START 提供可重放、可审计的连续运行代际。
- 新增设备统一 HTTP Endpoint 配置，支持 RFC1918、IPv6 ULA、回环地址、内部服务名和带点完整域名，并在 Epoch 激活时冻结规范化结果。

### Changed
- WorkLine START 收敛为 replay-first 的事务入口，按请求身份串行创建 Epoch，并以 `200/404/409/503` 分别表达成功、不存在、冲突和暂不可用。
- 粗分机激活改为依赖冻结的 Epoch 配置、拓扑摘要与设备合同；运行时数据清理同步覆盖 Epoch 及其绑定。
- 直接删除旧 START 准入字段和遗留运行态路径，不保留兼容别名、shim 或双路径。

### Fixed
- 修复 START OpenAPI 将错误响应并入 `200`、未声明实际非成功状态码的问题。
- 修复 Endpoint 接受 unspecified、广播、multicast、link-local、reserved、文档网段及 legacy numeric host 的问题。

### Verification
- QUALITY 全门禁通过：FAST 为 3623 passed、4 个既有外部条件 skip；Ruff、Bandit、架构、测试拓扑和预算检查均通过。
- staged HEAVY 在隔离 PostgreSQL、Redis、Celery 与迁移环境中 32 passed、0 skipped，包含 Epoch 激活、START、DeviceCommand、真实 worker wiring 与 runtime reset。
- 行为路径覆盖审计为 100%，独立预落地 Review 经三轮反馈闭环后无剩余问题。
- 本次证据证明后端工程包与本地集成闭合，不替代前端工程包、供应商 ECS/WMS 联调或现场业务验收。

## [0.26.4.0] - 2026-08-18

### Added
- 新增 provider-local WMS Transport Mock，覆盖 `RACK_MOVE`、`BIN_MOVE`、`RACK_EXCHANGE` 和 `BIN_EXCHANGE` 四类搬运提交请求，并提供幂等快照、故障注入和 callback 转发调试面。
- 新增与 Mock 同源的离线 Swagger UI、冻结 OpenAPI 示例及验收镜像，使预构建镜像可在纯局域网环境中完成合同浏览和联调验证。

### Changed
- 将公开可用性探针收敛为搬运提交与 callback 边界检查，并同步 WMS conformance fixture、测试支持模块、Compose 入口和 HEAVY 精确映射。
- 同步 GitLab `develop` 中的 Transport 资源围栏术语与 CI 基线；RuntimeInbox 验收改为等待最终 PostgreSQL 进程就绪，并将 FAST 临时预算校准为套件 90 秒、单项 4 秒。

### Fixed
- 修复 RuntimeInbox PostgreSQL 验收在 CI 节点选择、最终进程就绪和延迟预算上的不稳定问题。
- 修复移动端 Swagger 操作标签挤压描述文本的问题，375 px 视口下不再出现横向溢出。

### Verification
- QUALITY 全门禁通过：FAST 为 3532 passed、4 个既有外部条件 skip；Ruff、Bandit、架构、测试拓扑和预算检查均通过。
- 相对 `origin/develop` 选出的 16 个 HEAVY owner 在隔离 PostgreSQL、Redis 与迁移环境中 195 passed、0 skipped。
- 行为路径覆盖审计为 90%，实施计划 25/25 完成，预落地 Review 无剩余问题。
- 真实浏览器验证离线 Swagger 资源、四类搬运提交、幂等与冲突、故障注入及三类 callback 示例；该证据仅证明 provider-local Mock，不替代 WES/WMS/供应商或现场业务验收。

## [0.26.3.0] - 2026-08-18

### Added
- 可通过独立安装的 `wes_plugin_sdk`、类型化 Fact 和封闭 Decision 开发业务插件，并保持核心执行基础能力不依赖具体工作线。
- 新增粗分机入库插件、WMS 北向合同、可靠确认、NG 处理、换架、恢复与设备回调闭环，以及对应的静态部署装配。
- 新增 `MaterialExecution`、`InboundEvidence`、`WmsConfirmation` 和换架绑定等持久化对象及直接切换 migration，为重启恢复和人工对账保留事实依据。

### Changed
- Execution worker 使用持久 defer、ACTIVE Epoch 领取、队列声明和进程启动围栏，设备未就绪、外部等待及子进程替换均可按当前事实继续处理。
- Docker 镜像冻结仓库源码快照，同时保留本地 Compose 构建入口；HEAVY selector 精确覆盖 Execution、Celery、WMS Adapter、迁移与部署装配。
- 粗分业务测试保持在插件包内，核心 QUALITY、HEAVY 与供应商 ECS/网关一致性验收继续按基础能力、业务能力和外部设备边界分离。

### Fixed
- 修复设备就绪重试永久 HOLD、重复 WMS 终态选择失败、恢复位置未按 Epoch 绑定规范化及 NG 证据重复引用的问题。
- 修复 WMS 结果与 Epoch 关联、决策摘要持久化顺序、换架并发锁序、恢复因果围栏和 Transport evidence 重放竞争问题。
- 修复 Celery prefork 替换子进程重复执行 Epoch 重启门禁，以及开发 worker 队列声明与启动命令不一致的问题。

### Verification
- QUALITY 全门禁通过：3531 passed、4 个既有外部服务条件 skip；Ruff、Bandit、架构、测试拓扑和 FAST 预算均通过。
- 以 `de034e72` 快照为发布基线选出的 19 个 HEAVY 文件全部通过：183 passed；测试所有权审计覆盖率 100%，最终预落地评审无剩余意见。
- 仓内交付已闭合；供应商 ECS/网关一致性与 WMS/WES/RCS/ECS 现场联合验收仍为外部阻塞，Phase 8 不标记为业务验收完成。

## [0.26.2.0] - 2026-08-16

### Added
- 为 Transport callback 增加持久化收据、唯一身份约束和定向 Alembic migration，使可关联的拒绝、重放与冲突在重启和并发场景下保持确定。
- 补充 WMS Transport v0.2 的 DTO、OpenAPI、生产 wiring、PostgreSQL 集成与 HEAVY 测试资产。

### Changed
- 直接以 `target_face`、`container_id` 和分族搬运最终结果替换旧 Transport wire，不保留别名、兼容层或双路径。
- 冻结出站请求原始字节及摘要，并统一 ACK、固定重试、`DELIVERY_UNKNOWN` 与 callback 身份关联语义。
- 严格区分 UTF-8、JSON 语法、重复键、数字 lexeme 和 DTO 校验边界，使预关联错误不污染幂等身份。

### Fixed
- 修复浮点数重序列化导致不同 JSON 数字共享摘要、重复键被错误持久化，以及非法 Unicode 被误判为可重试 `503` 的问题。
- 修复 rejected receipt 冲突响应沿用 `422` 数据形状、搬运最终结果数组未校验排序或成员数量，以及 runtime 缺失 fallback 与 handler 身份判定漂移的问题。
- 修复 pre-commit 嵌套 Git 测试继承父仓库环境而污染测试仓库判定的问题。

### Verification
- QUALITY 全门禁通过：FAST 为 3264 passed、4 个既有条件 skip；Ruff、Bandit、架构和测试拓扑检查均通过。
- 受影响 HEAVY 在隔离 PostgreSQL、Redis 和 Celery 环境下 28 passed；计划完成度 38/38，行为路径覆盖审计 100%，独立预落地审查无剩余意见。

## [0.26.1.0] - 2026-08-16

### Added
- 为退役插件残余增加统一的 PostgreSQL schema 验收和精确 HEAVY 影响映射，可在全新数据库上一次验证诊断、暂停、NG 原因和运行时意图的最终结构。
- 补充 Rocky Linux 基础支撑离线交付包说明，并固化实施、评审、QA 与 ship 之间的验证证据交接规则。

### Changed
- Callback 与 Runtime 诊断统一使用 `WORKFLOW_EXECUTION_FAILED`、`WORKFLOW_TRANSITION_INVALID` 和 `WORKFLOW` 域，不再暴露插件身份或插件错误域。
- RuntimeHold 与 NG 原因只接受 `DEVICE_ERROR`、`RUNTIME`、`MANUAL` 三类共享来源；查询与释放接口不再携带插件字段。
- RuntimeIntentLog 只保留当前 Capability 身份，`operation_kind` 改为显式必填且数据库不再提供插件默认值；claim 入口在访问数据库前拒绝缺失或空白值。
- HEAVY selector 的 glob 重叠校验复用缓存结果，提交门禁对纯人类阅读文档只执行文档相称检查，对机器合同和代码继续运行完整 QUALITY。
- 同步插件退役后的架构计划、阶段所有权、观测合同和 Rocky Linux 初始化记录，使当前文档不再把旧插件执行闭包当作现役能力。

### Fixed
- 修复诊断 Trace、RuntimeHold、RuntimeIntentLog 与数据库当前 head 之间仍残留插件字段、索引、CHECK 约束和默认值的问题。
- 修复 selector 精确映射缺少内容指纹、测试所有权与实施事实不一致，以及运行时意图必填 fixture 仍使用旧合同的问题。

### Removed
- 删除无生产消费者的 recorded replay 草案、Timeline repository、包导出及其专属测试，不保留兼容入口或替代 facade。
- 删除北向观测中的 `PLUGIN_EXECUTION` 阶段，以及诊断、暂停、NG 原因和运行时意图中的退役插件身份字段与错误码。

### Verification
- QUALITY 全门禁通过：304 个 selector 合同通过；FAST 为 3252 passed、4 个既有条件 skip；Ruff、Bandit、架构和测试拓扑检查均通过。
- 受影响的 8 个 HEAVY owner 在隔离 PostgreSQL、Redis 和 Celery 环境下 47 passed、0 skipped；覆盖审计为 86%，预落地评审无剩余意见。

## [0.26.0.0] - 2026-08-14

### Added
- 新增面向 WMS 团队的 Transport OpenAPI 3.0.3 外发合同，并以同一 schema builder 约束 FastAPI 路由与冻结交付产物。
- 为料箱储位补充权威 `rack_face`，支持同侧一至两对 `BIN_EXCHANGE`；为完整搬运最终结果增加 WMS 来源 `outcome_revision` 与数据库单调水位。

### Changed
- WMS Transport 请求与响应边界改为严格 UTF-8/JSON、重复键拒绝、封闭字段联合和精确 HTTP `Content-Type`/`Content-Encoding` 校验。
- 搬运最终结果按同一任务的来源版本单调收敛：高版本可将 `UNKNOWN` 收敛为确定结果，低版本仅幂等确认且不得回退投影，已确定终态的矛盾证据进入人工处置。
- 收紧搬运提交 ACK、容器中间位置事件、搬运最终结果失败码、时间戳、位置长度和 UUIDv7 的公共合同，并同步外发需求文档与 WES 内部履约合同。

### Fixed
- 修复 ACK 关联字段不匹配被误判为业务冲突、深层 JSON 物化异常逸出、重复 HTTP 头可能绕过严格边界的问题。
- 修复 `outcome_revision` 并发身份裁决、乱序旧结果覆盖新投影，以及迁移永久数据库默认值与模型声明漂移的问题。

### Verification
- FAST：3239 passed、4 个既有条件 skip；相关合同专项 368 passed；分支覆盖率 93%。
- 真实 PostgreSQL/Redis Transport 集成验证 23 passed；独立落地前复审无剩余意见。

## [0.25.0.0] - 2026-08-14

### Added
- 交付 Phase 7 `DeviceCommand`/ECS 核心生产基线：`LineRunEpoch` 设备合同绑定、单设备单未闭合命令、统一 ECS 状态/命令 wire、ACK/CALLBACK evidence 与三个有界 Celery worker。
- 新增 DeviceCommand 运维诊断 Runbook，以及真实 PostgreSQL、Redis、Celery prefork、HTTP ECS 与 callback 的无业务插件闭环验收。

### Changed
- FastAPI 与 Celery 生命周期共同装配唯一 DeviceCommand composition root；设备命令和 TransportTask 保持平行可靠对象，不复用状态机、表、重试或测试所有者。
- RuntimeInbox 收敛为通用 ingress、replay 和可靠性能力；ECS RESULT/EVENT 改由 `device_evidences` 独立持久化、幂等、冲突和 Epoch fencing。

### Fixed
- 修复 callback 流式限流、拒绝路径幂等身份固化、派发前截止时间复核、Epoch 轮换、binding 超时约束、uniform-wire Mock 与固定错误语义。
- 修复 ECS `Retry-After` 与非对象 JSON 归类、DeviceCommand 重建迁移的存活外键恢复、状态观测命令码宽度，以及运行态汇总读取退役设备字段的问题。

### Removed
- 删除旧 DeviceCommand Gateway、lease、runtime projection、SystemCapability、SystemOutbox/RuntimeIntent 设备命令分支及旧 callback route，不保留兼容 facade、双写或 fallback。
- 删除已无生产 owner 的普通 WMS event callback 分支与对应旧测试；WMS 业务回调和 ECS 设备回调继续严格分离。

### Verification
- QUALITY：3199 passed、4 个既有条件 skip；HEAVY selector 合同 278 passed；Ruff、Bandit、架构和测试拓扑门禁均通过。
- DeviceCommand PostgreSQL 约束 14 passed，真实 Redis、Celery、HTTP ECS callback 闭环 1 passed；三轮独立复审最终无 P0–P2 意见。

## [0.24.2.0] - 2026-08-13

### Added
- 将通用 Transport 从暗构建推进为可运行的生产基线，提供统一公共 Port、WMS 搬运事件入口、Celery 提交/证据/对账任务和持久化提交快照。
- 新增 Transport 运维 Runbook、真实 Redis broker、prefork Worker、PostgreSQL 与 HTTP 闭环验收，覆盖无业务插件时的最小生产接线和故障恢复。
- 新增 UUIDv7 生成与校验能力，并将搬运请求、回调和持久化 Evidence 的关联身份统一到部署级唯一标识。

### Changed
- API 与 Celery 生命周期改为共同使用唯一 Transport composition root，公开入口只暴露 Transport Port、Runtime、合同 DTO 和构建函数。
- WMS callback 收敛为以 `operation_id` 和 `timestamp` 为核心的封闭信封；ACK、OpenAPI、原始事件时间和完整请求摘要保持同一合同。
- 搬运批处理固定为 100 条，并以单并发 Worker 的领取时间预算保证提交、Evidence 和对账任务之间的公平性。
- 货架供给需求改由现役 rack-supply 领域模型和 PostgreSQL 唯一约束承接，不再借用旧 Transport 业务 operation。

### Fixed
- 修复 submit 与 callback 并发时的锁序死锁、迟到确定性拒绝无法收敛、旧 attempt 回写覆盖新 claim，以及资源绑定提前释放的问题。
- 修复 WMS runtime 未就绪时错误响应、operation 专属 OpenAPI data 过宽、非法时间戳和 `client_request_id` 未严格校验的问题。
- 修复 FastAPI/Celery 初始化失败时清理阶段互相短路、子进程和 Redis/数据库测试资源残留，以及 Docker/Uvicorn 生产启动参数不可用的问题。
- 修正 Celery QUIT 接管测试对 broker `redelivered` 内部标记的单一路径假设，同时继续验证同一 task id、接管时限和数据库幂等终态。

### Removed
- 删除已无业务 owner 的 WMS 货架搬运和料箱搬运 Effect 身份、专属端口与 capability definition，不保留兼容入口。
- 删除 `WmsRackDemand` 的旧目标货架、根 operation 和 owner 交接字段/约束，以及 `STATION_TRANSPORT` 旧 owner 类型。

### Verification
- QUALITY 全门禁通过：Ruff、Bandit、运行时/架构/拓扑、HEAVY selector 合同均通过；FAST 为 3403 passed、4 个既有条件 skip。
- 受影响 HEAVY 在隔离 PostgreSQL 与 Redis 下 288 passed、0 skipped；独立落地前复审为 0 P0、0 P1、0 P2、0 ASK。

## [0.24.1.0] - 2026-08-11

### Changed
- 将 WorkLine 收敛为静态身份、物理拓扑、通用配置与启停入口，RuntimeInbox 仅保留通用 ingress、claim、replay 和 terminal 可靠性能力。
- WMS 北向合同收敛为 31 项现役 operation，并同步诊断、可观测性、legacy 台账与 HEAVY selector 的当前真源。

### Fixed
- 修复退役迁移未删除 SMT 入站交接需求、来源明细和 WMS 输送批次成员表的问题，并以空库升级合同验证目标 schema。
- 修复无业务 owner 的人工操作、沙箱事件、沙箱结果入口仍接纳请求并最终进入合同不匹配或死信的问题。

### Removed
- 删除嵌入式工作线插件、插件 binding/dispatcher/attempt 执行闭包，以及已无 owner 的人工操作和沙箱调试公共合同。
- 删除粗分、SMT 入站交接等旧业务 carrier、孤儿依赖注入和已退役插件身份的生产写入及测试假对象。

### Verification
- QUALITY：3385 passed、4 skipped；Ruff、Bandit、架构门禁、测试拓扑、FAST 预算和 HEAVY selector 合同均通过。
- 受影响 HEAVY：隔离 PostgreSQL 与 Redis 下 10 passed；独立复审无剩余意见。覆盖审计为 73%，8 个防御分支缺口按发布决策接受。

## [0.24.0.0] - 2026-08-10

### Added
- 新增 AGV/CTU 通用 Transport 暗闭环，支持货架搬运、货架换面、1 至 4 个料箱搬运和 1 至 2 对料箱交换，并以持久化任务句柄承接异步结果。
- 新增 WMS Transport 固定统一接口（wire）、同步接纳 ACK、异步位置与结果回调、256 KiB 请求响应预算，以及明确的 `arrival_face` 权威事实。
- 新增 Transport 聚合、成员、位置投影、资源绑定和 Evidence 表及 PostgreSQL 约束、领取索引与并发验收测试。

### Changed
- 将 Phase 4 收敛为基础搬运能力：只管理搬运身份、资源互斥、可靠下发、位置事实、最终结果和最新权威 outcome 发布，不承接业务分配、设备命令或工作线插件编排。
- 统一 SRS、权威矩阵、WMS 合同、插件指南、测试所有权和架构计划中的 Transport 边界；当前实现保持暗构建，不注册生产 API、Celery 任务或消费者。
- Transport 结果采用单调版本和最新状态合并；未发布的低版本可被更高权威版本取代，未知位置允许由后续确定事实纠正。

### Fixed
- 修复多成员结果冲突时可能部分写入成员与位置投影、换面成功未命中冻结目标面，以及迟到取货里程碑覆盖未知位置的问题。
- 修复 WMS ACK 身份与字段校验、发送前确定失败、超限请求、重试预算、交付未知和迟到 ACK 的收敛边界。
- 修复协调中已确认的确定位置、成员状态和失败码可能被冲突 Evidence 改写的问题，同时保留未知结果的高版本纠正能力。

### Verification
- FAST：3647 passed、4 skipped；QUALITY 全门禁通过，Ruff、Bandit、架构约束、测试拓扑和 FAST 预算均无新增问题。
- 受影响 Transport HEAVY：空库迁移至 `a8d9b9eba49b` 后 15 passed；覆盖率业务路径 98%，剩余缺口仅为上游闭集失效后的防御分支。
- Phase 4 计划审计：60 DONE、0 PARTIAL、0 NOT DONE；3 项历史 TDD/GitNexus 顺序事实已人工确认但无法从最终树反推。

## [0.23.1.0] - 2026-08-08

### Added
- 新增 WMS/WES 自动出库交互合同，给出统一端点、JSON 信封、PickingTask 下发、启动锁定、逐盘决定、NG、来源恢复、位置事实和完成报告的联调基线。
- 新增 Transport 履约合同，冻结搬运请求、同步接纳 ACK、异步 `TransportResult`、六态生命周期和结果对账边界。
- 新增第三方设备统一接口白皮书，统一命令、状态、结果回调、设备事件、幂等、合同版本和 `LineRunEpoch` 约定。

### Changed
- 自动出库改为任务驱动：`PickingTask` 只聚合已接纳的 `CellExecution`，逐盘扫码后由 WMS 判断业务资格和目标，WES 独立决定设备动作与 NG 执行链。
- 明确同线多任务排队、不同线并行、CTU 批次运输、三段滚筒线缓存、目标架换面及清场运输的独立生命周期和背压规则。
- 统一 SRS、权威矩阵、设备命令合同、插件指南、测试所有权和十一阶段总控计划，不再把供应商私有 Adapter 或旧运行时可靠链作为目标实现路径。

### Verification
- 纯文档变更检查通过：`git diff --check`、核心 Markdown 格式、代码围栏、项目内链接和项目外归档边界均无错误。
- 覆盖率审计确认生产代码、可执行配置和测试路径变更均为零；预落地复审无剩余问题。

## [0.23.0.0] - 2026-08-07

### Added
- 新增 Phase 3 WMS 薄访问层，提供统一的 `request`、`get`、`post` 与 `aclose` 入口、不可变访问结果和固定 `system_id="wms"` 的工厂。
- 新增 WMS Adapter FAST 合同测试及精确 HEAVY NONE 映射，覆盖严格 JSON 值域、请求构造、传输失败事实、响应解码和生命周期语义。
- 新增任务驱动的 `PickingTask` 对接要求与出库操作设计，明确 WMS 冻结业务结果、WES 映射执行 Decision 的后续实施边界。

### Changed
- 收敛 Phase 3 为类似 Axios 的 HTTP/JSON 标准封装，不包含具体 WMS 业务 API、持久化、重试、熔断、动态注册或生产接线。
- 统一 SRS、权威矩阵、WMS 北向合同、插件指南和总控计划中的职责表述：WMS 负责业务决定，WES 负责执行决定，Phase 4 才承接 RCS、AGV、CTU 运输交互。
- 将已完成的 Phase 3 实施计划移出项目目录归档，项目内仅保留当前架构与开发合同真源；`docs/hardware/` 厂商原始资料继续保留。

### Fixed
- 收紧请求 Header、严格 JSON 编码与深层响应解析失败合同，完整保留响应已接收、状态码和 Header 等传输事实。
- 修正文档中的决定重放、幂等、启动拒绝、业务事件与设备事件边界，并将 WorkLine 明确为执行插件而非业务决策插件。

### Verification
- QUALITY：3439 passed、4 skipped、1 warning；Ruff、Bandit、架构门禁、测试拓扑、FAST 预算和 HEAVY selector 合同均通过。
- Outbound HTTP、WMS Adapter 与 selector 定向回归：425 passed；受影响 HEAVY 选择为空，预落地复审无剩余问题。
- Phase 3 归档计划完成度：4/4 DONE；WMS Adapter 综合覆盖率 97%，剩余 4 项为非关键防御路径。

## [0.22.1.0] - 2026-08-06

### Added
- 收录 WMS 厂商原始接口资料，并明确硬件资料、WES 当前合同与业务蓝图之间的真源边界。
- 建立 Phase 3 WMS Adapter 薄接入实施计划，冻结 35 项能力、共享端口、Evidence、Breaker 和测试所有权。

### Changed
- 将 Phase 1/2 验收结论同步到最小执行架构 SPEC、总控计划、SRS、ADR、文件索引和测试治理说明。
- 收紧 Outbound HTTP 请求与响应 Header 合同、工厂入口及响应清理语义，保持基础传输与业务能力隔离。
- HEAVY selector 将发布版本文件和已清理的 Agent 工作区配置归类为显式无重测试影响，同时继续对生产候选 fail-closed。

### Fixed
- 修复 Outbound HTTP 对非法请求 Header、非法响应 Header 和调用方控制内容编码的处理偏差。
- 修复响应清理取消路径可能遗留任务的问题，并补齐对应成功与失败路径回归覆盖。

### Removed
- 删除项目内 `.codex` 工作区配置及其跟踪入口，避免仓库持有客户端私有配置。
- 移除已被当前 Phase 3 计划取代的 Phase 2 过程计划，不在项目目录保留历史设计副本。

### Verification
- QUALITY：3371 passed、4 skipped、1 warning；Ruff、Bandit、架构门禁、测试拓扑和 HEAVY selector 合同均通过。
- GitNexus 对 `origin/develop..HEAD` 的比较结果为 low risk，未检出受影响执行流；独立复审未发现剩余问题。

## [0.22.0.0] - 2026-08-05

### Added
- 新增框架无关的 Outbound HTTP 传输合同、单次发送实现和共享 Client 生命周期，供后续 Adapter 通过唯一构造入口消费。
- 新增有界响应 Header、Chunk、Wire、解码体和压缩比预算，并以稳定 delivery state 与 failure kind 公开传输事实。

### Changed
- 出站请求采用严格的路径、Query、Header、Body、Base URL、Timeout 和内容编码输入合同；禁用 Cookie、环境代理和重定向。
- HTTPX/HTTPCore 日志仅在当前请求上下文内脱敏，基础 Transport 保持与业务、Adapter、插件和运行时编排隔离。
- 为 Outbound HTTP 生产路径增加架构门禁和 HEAVY selector 显式 NONE 映射，冻结 Phase 2 四文件基础边界。

### Fixed
- 修复不同发送阶段的失败事实映射、响应清理超时与取消传播、幂等关闭及关闭取消时的资源释放。
- 修复未知异常被误归类、底层日志可能泄露请求信息，以及字符串输入被误拆成二元 Query/Header pair 的问题。

### Verification
- QUALITY：3363 passed、4 skipped；Outbound HTTP 基础层与架构门禁：133 passed；分支覆盖率 95%。
- Phase 2 当前交付和退出门禁全部完成；预落地复审未发现剩余问题。

## [0.21.1.0] - 2026-08-04

### Added
- 建立 WES 架构收敛总控计划与 Phase 1 验收基线，明确顶层 SPEC、阶段边界、测试所有权及后续阶段承接关系。
- 新增 FAST、QUALITY、HEAVY 分层门禁、受影响 HEAVY 选择器及独立 CI 执行链路，使核心快速回归与真实服务测试保持隔离。

### Changed
- 当前架构文档、SRS、开发入口和 CI 说明统一指向现役真源；历史设计移至项目外归档，硬件厂商原始资料保持原貌。
- 默认测试治理收紧为核心通用能力验收，具体工作线、业务插件和厂商 Adapter 测试由各自独立包负责。
- FAST 单例预算放宽为 3 秒，并以机器门禁约束总时长、慢用例和重测试目录边界。

### Fixed
- 修复 HEAVY 选择器在重命名、删除、内容指纹、测试目标和纯注释 runtime 文件上的 fail-closed 行为，确保所有生产候选都有精确映射或经评审的 NONE 结论。
- 恢复 WMS Q19 阶段原子边界，并补齐 callback OpenAPI 请求体的必填、自包含 Schema 与公开示例合同。
- 清理 runtime 过渡模型中的历史真源表述，避免当前代码继续引导到已退役设计。

### Removed
- 删除项目内的历史规格、计划、报告、失效流水线入口、旧测试平台与业务专属测试资产，不保留兼容占位或转发文档。

### Verification
- QUALITY：3236 passed、4 skipped；受影响 HEAVY：38 passed；Phase 1 计划门禁 7/7 完成，预发布审查未发现问题。

## [0.21.0.0] - 2026-08-03

### Added
- 建立 WES 核心测试的 FAST、QUALITY、HEAVY 分层治理，新增受影响 HEAVY 测试选择器、显式影响映射、FAST 时间预算以及核心/插件测试所有权门禁。
- 固化 WES 最小执行架构与测试语义收敛基线，明确核心测试只覆盖 SPEC 定义的通用执行能力，具体工作线、插件和厂商业务测试随独立二次开发包交付。

### Changed
- 默认 `pytest` 与本地质量门禁收敛为快速回归集；数据库、HTTP、Celery、进程、故障注入、容量与真实部署测试改由受影响 HEAVY 集显式运行。
- 重写测试目录、CI 入口、架构账本、清理矩阵和开发指南的所有权语义，使通用投影、查询、回调、Outbox 与 Mock 合同保持在核心边界内。
- Task 5 的最终核心执行对象测试承接延后至执行架构重构，由架构收敛 Master Plan Phase 4 直接跟踪；HMAC 生产级验收作为 P3 安全加固事项，不阻塞本次收敛。

### Fixed
- HEAVY 选择器现在正确处理中文及 quoted path、Git 参数边界、无效 glob、已删除 direct 测试和失效 mapping，并在治理配置漂移时 fail-closed。
- 补齐 WMS conformance、Mock、RuntimeInbox、Repository、callback、Outbox、数据库并发和 benchmark runner 的 HEAVY 归属与生产闭包合同。
- 删除与 FAST 重复的质量 profile，统一 WMS 提交超时合同，并修复 Mock WMS 镜像导入及测试拓扑分类偏差。

### Removed
- 从核心 `tests/` 移除旧插件平台、具体工作线/插件场景、迁移验收、legacy characterization、业务 fixture 和相关数据脚本，避免 WES 系统测试继续固化待删除架构。
- 退役旧 runtime evidence、live suite、测试工作线同步和插件专用 PostgreSQL 场景资产；后续业务测试由对应 `workline_plugins/<plugin_key>/` 包自行承接。

### Documentation
- 更新测试治理说明、架构文件索引、插件开发指南、业务验收计划和 superpowers 文档索引，记录当前批次完成范围及执行架构重构后的剩余承接事项。

## [0.20.7.0] - 2026-07-31

### Added
- 新增配置化 WMS 全工厂接入能力，冻结 35 项类型化北向合同，并通过 Provider profile、参数化 endpoint 编译、启动检查和一致性门禁实现兼容合同下的直接连接。
- 建立 19 项 QUERY 与 16 项 EFFECT 的统一运行时：查询和 WMS 数据修改使用同步 REST，只有 AGV/CTU 调度任务使用异步 ACK、状态查询、回调提示、证据记录、重放与对账闭环。
- 接通 Q19 GRN 准入、E11 货架调度、E12 上料批次、E13 退料批次、满箱交换以及入站事件等业务能力，覆盖粗分机和分拣机仓内流程。
- 新增部署证明、合同一致性、运行可观测性和发布前准入门禁，支持 API、Worker、Beat 与迁移角色使用同一 Provider 配置。

### Changed
- 明确隔离 WMS 数据操作与 AGV/CTU 调度边界：QUERY 和同步修改直接返回结果，调度 EFFECT 才进入 ACK 生命周期和独立 Outbox 通道。
- 收敛 RuntimeInbox、履约状态、批次投影和状态恢复的所有权，E12 与 E13 分别维护上料对象和由 SCAN3 形成的退料候选队列。
- 生产部署统一校验 WMS Provider profile，并隔离 Celery Worker 拓扑；未发布系统仅保留目标合同，不提供旧配置、旧回调或旧数据路径兼容。

### Fixed
- 修复查询并发与预算、幂等重放、同步模糊传输、异步 ACK 恢复、超时轮询及终态投影中的边界问题，避免旧事实复用、重复推进和状态漂移。
- 修复 Q19 拒绝、E11 候选饥饿、E12/E13 批次准备与收敛、满箱交换履约及回调准入中的合同偏差。
- 发布脚本在数据库迁移前清理并验证遗留 `celery_worker`，Compose 启动清除孤儿容器；测试部署在首次启动前强制验证 Provider profile。

### Removed
- 删除旧单据端口、聚合端口、专用证据归档、终态回调、transport facade、CTU 批次预览以及 Mock/legacy 兼容路径，避免双轨实现。

### Documentation
- 更新全工厂 WMS 接入 SPEC、分阶段验收记录、部署与联调 Runbook，记录已验证任务及仍需真实 WMS 联调和工厂切换确认的外部验收项。

## [0.20.6.0] - 2026-07-29

### Changed
- Runtime 资源预占兜底现在直接使用 material-flow 域的真实默认服务，WorkLine service facade 只暴露仍受支持的配置域能力。

### Fixed
- 补齐未注入预占服务时的默认单例回归覆盖，并将该目标态测试精确排除在 legacy cleanup 矩阵之外。

### Removed
- 删除 WorkLine service facade 中已失效的 PEP 562 tombstone、延迟属性加载器和旧预占服务名称，不再保留未发布系统的兼容入口。
- 清理 44 份未被引用的过期归档文档，同时保留仍被当前文档引用的归档根及其依赖闭包。

## [0.20.5.0] - 2026-07-28

### Added
- SMT 分拣入库现在通过 generated plugin 完成 source-pick 请求、设备命令、Outbox、callback、恢复和 `PICKED` 账本闭环，并为命令与账本写入提供独立 system capability。
- 三类运行态记录新增强制 plugin binding snapshot pins 与 PostgreSQL 约束，激活、Session 创建和执行阶段均固定插件、配置、索引及 Provider 身份。
- 新增 SMT 并发恢复、完整 PostgreSQL 生命周期和运行时扩展性能预算，覆盖成功、失败、重复、回滚、迟到 callback 与 100 条恢复扫描。

### Changed
- RuntimeInbox 执行统一收敛到 generated dispatcher、route-level facts builder 和 typed effect state，rough sorter 与 SMT 使用同一套三阶段处理及写回边界。
- SMT source-pick recovery 通过持久化 command correlation、执行锚点和稳定锁顺序恢复唯一候选；歧义、证据不匹配和失败终态进入人工处置。
- 运行时架构、插件开发、SMT 业务流程、文件索引和 legacy cleanup 清单已同步到 generated-only 目标实现。

### Fixed
- `COMMAND_RESULT` 现在只信任 `RuntimeInbox.command_id` 对应的持久化 `DeviceCommand`；伪造 callback 无法覆盖命令类型、结果、数据或错误详情，非终态与矛盾证据保持零副作用。
- 修复 SMT command correlation、成功账本、恢复事务、生产路由与执行归属的边界缺口，避免重复推进、跨 Session 覆盖和失败命令被当作成功处理。
- 收紧 smoke seed、插件绑定准入、诊断来源和文档身份，防止未知插件、未绑定运行态或旧 SMT 标识重新进入活动链路。

### Removed
- 删除 legacy compatibility、未绑定 RuntimeInbox processor、备用 orchestrator delegate、旧 write-back callback 及其 legacy-only 测试，运行时不再保留双轨或 fallback。

## [0.20.4.0] - 2026-07-27

### Changed
- 运行时类型与外部调用契约进一步收紧，统一值规范化、外部 HTTP/WMS 状态查询、Outbox 派发与系统能力账本的 typed 边界，并保持失败路径 fail-closed。
- Callback result、event 与 external 入站按包络准入、权威上下文、路由校验和编排阶段拆分；RuntimeInbox processor、插件 effect intent 转换、replay 验真与 Session 复用流程同步按职责收敛，降低嵌套和重复分支。
- SMT/NG/WMS 对账策略与数据库方言解析提取为共享 helper，减少 preview/runtime 及多个 Repository 间的重复实现。
- 补齐 PostgreSQL 外部 HTTP transport attempt、插件 attempt、派发结果与系统能力账本的一致性验证，并扩展 callback 与插件运行时回归覆盖。

### Fixed
- 修复 event callback 的 provider profile admission 发生非预期异常时绕过统一失败审计的问题，确保记录 `500 / FAILED / ORCHESTRATION` 后继续传播原始异常。

## [0.20.3.0] - 2026-07-26

### Added
- WorkLine 插件迁移现在可聚合多个环境的 inventory 与批准证据，生成带稳定摘要的 migration matrix，并在缺少环境、批准过期、索引漂移或报告超时时 fail-closed；当时的操作顺序现归档于 `../archive_docs/wes_backend/docs/operations/workline-plugin-migration-inventory.md`。
- 单环境 inventory 现在包含 WorkItem/Intent 固定引用、逐 WorkLine binding、Provider admission、System Capability 和 Port 要求，为后续 cutover preflight 提供机器可验收输入。
- 新增本机 Docker、WMS Mock 与 ECS Mock 的系统设计文档，明确后续开发环境和验收能力的推进边界。

### Changed
- Superpowers 文档索引按 active、archive 和 superseded 生命周期重新整理，已完成的历史计划与规格移入归档目录。
- RuntimeInbox 历史验收文档不再承担当前机器门禁，插件迁移清单的运维流程统一到独立 inventory/matrix 指南。

### Fixed
- 修复新增 migration matrix 基础能力被 legacy cleanup generator 误判为待清理入口的问题，并同步 WorkLine service facade 的合法导出契约。

## [0.20.2.0] - 2026-07-26

### Changed
- WMS Mock 库存查询现在与生产 adapter 使用相同的签名 GET 合同，并支持按仓库和货主过滤库存，不再接受 legacy POST envelope 与 `sku` 参数别名。
- WMS 独立验收容器的健康检查改为验证公开北向合同，确保合同配置缺失时服务不会被误判为可用。

### Fixed
- 修复插件 attempt 为 `CONTINUE_NEXT` 平台生命周期动作重复预写 semantic ledger 的问题，保持 effect、device outbox 与 replay 账本一一对应。
- 修复粗分机 sandbox 库存种子的仓库和货主维度缺失，并补齐 HMAC fail-closed、并发重放、PostgreSQL attempt/outbox/replay 回归验证。

## [0.20.1.0] - 2026-07-25

### Added
- WMS Mock 现在提供带 HMAC 认证、幂等提交、状态查询和 typed result 的北向 EFFECT 合同，可直接验收入库确认、料盘绑定与满箱交换。
- 新增可控时钟、一次性故障、限流、超大响应与并发重放能力，并提供独立的真实镜像 TCP 验收环境。

### Changed
- 北向可行性探针改为验证实际 WMS Mock 的公开接口、时间安全边界、响应预算与回调提示，不再依赖测试内嵌替身。
- Compose、环境配置、镜像依赖和发布证据统一传递公开合同参数，同时隔离开发 Mock 与验收服务。

### Fixed
- 修复 nonce 重放、保留期与可见性边界、并发故障认领以及状态回放中的合同偏差。
- 修复 legacy 满箱完成回调语义、验收健康检查、镜像入口和发布摘要校验，避免验收结果与实际部署不一致。

## [0.20.0.0] - 2026-07-25

### Added
- WMS EFFECT 操作现在通过幂等提交与状态查询确认最终结果，支持超时、模糊发送、崩溃恢复、租约围栏和人工对账场景下的安全恢复。
- 新增状态查询 Port、HTTP adapter、持久化轮询状态和 PostgreSQL claim 机制，并为入库确认、料盘绑定和满箱交换提供统一的状态确认合同。
- 新增北向可行性探针、录制回放资产、联调验收模板、切换 Runbook 和 operation SLO 目录，为真实 WMS 联调与后续整体切换提供可执行材料。

### Changed
- 北向运行时收敛为单部署 Provider，callback 仅作为提前查询提示，业务终态统一由状态查询与 reducer 决定。
- 三个 WMS EFFECT operation 合并使用共享 preparation service，同时保留各 operation 的 typed 请求映射、签名和幂等身份。
- Docker、Celery、Settings 与测试部署模板统一提供状态轮询、凭据、签名和连接配置，缺失生产配置时继续 fail-closed。

### Fixed
- 修复 WMS 协议拒绝未进入开放对账、状态确认预算不完整、逐项租约丢失以及回调关联键不一致导致的账本悬挂。
- 修复 PostgreSQL 崩溃恢复测试仍期待旧式立即对账的问题，并同步 legacy 清理矩阵与闭环计数。
- 修复 Provider 轮换、请求体双签名、状态提示关联和启动门禁中的合同漂移。

### Removed
- 删除未接入生产路径的 query shadow/readiness 平台、旧 conformance trust root、terminal callback adapter 和重复的 operation preparation service。

## [0.18.1.0] - 2026-07-21

### Fixed
- 修复 WorkLine 插件 dispatcher 对显式空 handler 候选的诊断分类，统一返回稳定的缺失处理错误码。
- 收紧插件运行时的持久化身份、binding pin、迁移清单输入与同步 flush 类型边界，新增对应 fail-closed 回归测试。

## [0.18.0.0] - 2026-07-20

### Added
- 新增 WorkLine 插件与系统能力平台：以静态生成索引注册 typed Plugin、QUERY/EFFECT capability、不可变 binding 和 Session/Execution pin，支持按 provider profile 与 Port 方法最小授权运行。
- 新增粗分机类型化决策插件、WMS 库存准入能力和 13 个扫码决策场景，覆盖物料流转、设备命令、NG、Hold、超时与确定性 replay。
- 新增 RuntimeInbox 三阶段插件尝试、意图账本、effect claim 与 evidence/replay 管道，并增加两份运行时/插件 binding 数据库迁移。
- 新增 PostgreSQL 集成、E2E、性能预算和架构门禁测试，覆盖并发 pin、attempt stale snapshot、outbox result、callback、录制 replay 与会话锚点。

### Changed
- RuntimeInbox、callback、设备命令和 WorkLine 服务统一接入平台化运行时边界；运行中的 Session 固定 binding、profile、生成索引和执行关联，避免配置漂移影响既有作业。
- 粗分机 WMS 准入、系统能力副作用和插件调度改为基于声明合同、权威事实和事务性账本执行，生产路由只使用生成索引。
- 架构账本、迁移矩阵、业务合同与测试拓扑同步到扩展平台目标态，明确 legacy 入口已退出生产路径。

### Fixed
- 修复 provider profile 被硬编码、Port 方法过度授权、插件 binding 切换并发、设备命令占用和 callback 幂等关联问题。
- 修复新建平台 Session 首条 Inbox 缺失 execution/correlation 锚点，以及业务键交叉并发导致 advisory lock 死锁的问题。
- 收紧 replay、结果关联、失败终态、意图转换与 effect 授权校验，避免迟到回调、旧快照或未声明能力覆盖权威运行状态。

### Removed
- 删除旧 runtime catalog/dispatcher、手写 plugin manifest/YAML 及相关兼容生产入口，改由平台定义与生成索引承载。

## [0.17.1.0] - 2026-07-16

### Added
- 新增粗分机扫码到 WMS 准入决策的权威窄闭环合同、13 场景 trace fixture 与机器化合同测试，覆盖扫码、测量、WMS 查询、设备失败、超时、幂等冲突和迟到 callback。
- 新增 WorkLine 活动盘点基础能力实施计划，并记录超过 100 条 WorkLine 时引入批量运行引用摘要的后续优化项。

### Changed
- 收束粗分机运行流、入库验收、能力规格与跨系统事件流的文档所有权，上层文档仅保留协议和验收交接，分支判定统一引用权威合同。
- 更新 WorkLine 插件与系统能力平台设计，批准粗分机窄闭环合同，并明确后续 typed outcome、QUERY evidence、Intent identity 与 replay 输入。

### Fixed
- 对齐粗分机合同与真实领域枚举及状态所有权：Session 使用 `WAITING_DEVICE_RESULT`，普通 Hold 不再写入 MaterialUnit 或 DeviceCommand 状态。
- 将 `TIMER_TIMEOUT` 明确归属平台 reconciliation + RuntimeHold 路径，补齐 BLOCK Intent/EFFECT 身份、三阶段冲突 replay、零新写入和各能力 evidence 的闭合约束。
- 统一 WMS 超时稳定原因码为 `WMS_TIMEOUT`，删除旧业务概念别名和双原因码表达。

## [0.17.0.0] - 2026-07-16

### Added
- 新增 WorkLine 活动迁移清单模型、盘点服务与只读 CLI，可在可重复读快照中汇总插件合同、provider profile、未完成运行态引用及 foundation blocker，并生成稳定 SHA-256 摘要。
- 新增真实 PostgreSQL 合同测试，覆盖五类运行态引用、状态矩阵、样本优先级、MVCC 快照一致性、数据库拒写和 100 条安全上限。
- 新增 WorkLine 插件与系统能力平台设计，明确目标架构、能力目录、provider 合同和后续迁移边界。

### Changed
- 迁移清单 CLI 强制校验运行环境，使用 `REPEATABLE READ + READ ONLY` 事务、分层超时和原子文件替换，并以稳定退出码支持部署门禁。
- legacy matrix 与架构守卫显式登记迁移清单基础能力，确保目标态文件不会被误归入清理账本。

### Fixed
- RuntimeInbox 重放验真拒绝现在持久化稳定原因码并标记为不可重试，同时保留未知运行异常的默认重试语义。
- 迁移清单严格拒绝畸形 catalog、provider、WorkLine 和 repository summary 数据，并为规模阻断提供脱敏且可操作的错误提示。

## [0.16.0.0] - 2026-07-15

### Added
- 新增 RuntimeInbox 五态处理、人工重放、超时扫描、崩溃恢复、运行 SLI 与 PostgreSQL 正式验收链路，覆盖幂等、FIFO、租约围栏和重试耗尽合同。
- 新增 Celery prefork 子进程单一异步运行时，统一数据库与 Redis 资源代际、初始化回滚、有界关闭和任务上下文隔离。
- 新增数据库连接容量门禁、结构化 `application_name`、生产扩缩容校验及 RuntimeInbox PostgreSQL benchmark/CI 工具。

### Changed
- Callback result/event/external、人工操作、设备事件和超时消息统一写入 RuntimeInbox；非工作线回调接收即终态化，工作线消息由新 Celery 消费链路推进。
- RuntimeInbox 设备事件按真实设备身份维持 FIFO，分布式锁按处理超时自动续期，并在 Redis 连接故障时仅对锁获取阶段降级到 PostgreSQL advisory lock。
- 生产 API、Celery Worker、Beat 与 Flower 全部改为仅运行镜像内源码，部署覆盖文件不再挂载宿主机 `src`。
- 部署完成前按当前环境实际部署清单逐个验证 Celery Worker/Beat 容器状态，并对每个 Worker 执行定向 ping，避免 testing 未部署 Beat 时误失败或异步消费不可用时误报成功。
- 日志、测试输出、运行数据重置、迁移事务与发布回滚路径统一到新的运行时和部署合同。

### Fixed
- 修复 RuntimeInbox 终态时间、指数退避、关联外键、payload 上限、重放来源验真、异常重试及 processor token 围栏问题。
- 修复 RuntimeInbox Replay 失败仍返回 HTTP 200、OpenAPI 未声明错误响应，以及回调空白事件标识被误归类为配置错误的问题。
- 修复人工 HOLD/RESUME/CANCEL 使用旧 Inbox kind 导致的六类数据库约束冲突，并让生成的 DEVICE_EVENT 保留完整路由包络。
- 修复非工作线 command result 被异步 worker 再次领取、生产源码挂载覆盖镜像以及数据库容量计算偏离真实拓扑的问题。

### Removed
- 删除 WorklineInbox 模型、Repository、Service、批处理器、消费 facade、双写路径及相关兼容入口，RuntimeInbox 成为唯一入站事实源。
- 删除分散在 Celery 任务中的临时 event loop 和懒初始化逻辑，所有同步任务统一通过子进程异步运行时执行。

## [0.15.4.0] - 2026-07-10

### Changed
- 统一 runtime、handling、rack、device、WMS integration 等域的值规范化入口，减少重复转换逻辑并明确必填文本只接受真实非空字符串。
- 将 WorkLine restructuring 顶层设计拆分为概览、架构、模块、接口、数据、状态、安全、非功能和实施文档，保留可追踪的设计计划与验收边界。
- 标记延后实现的实体并清理活跃代码中的过程阶段标记，使当前架构表面保持稳定领域命名。

### Fixed
- 修复 sorter inbound 必填字段取值、plugin manifest iterable 摘要和 station claim 枚举状态判断回归，并补齐对应单元与运行时测试。
- 修复 legacy matrix 生成器把 single-layer rack 和名称含 `reconciling` 的 Bin Cell 条目误归为 NG return 的问题，同步 matrix、absence ledger 与文档统计校验。
- 收紧 legacy 审计契约，校验 matrix 与 ledger 的条目、业务语义、目标能力和 seed 分类统计一致。

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
- 将旧 `src/workline_plugins/*` 和旧 plugin template 移出可 import 路径；其历史样本当前归档于项目外 `../archive_docs/wes_backend/docs/archive/legacy-workline-plugins/`。
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
- 外部归档 `../archive_docs/wes_backend/docs/architecture/legacy-cleanup-matrix.md` 的历史统计表同步：total 668 / model 39 / repository 7 / rebuild 412 / phase2 199 / workline 455；当前机器清单以项目内 CSV 为准。
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

- 历史设计归档 `../archive_docs/wes_backend/docs/architecture/workline-and-plugin-restructuring.md` 与当前
  `docs/architecture/file_index.md` 曾同步 Phase 1 Packet D 完成状态、文件索引和验证证据；前者不属于当前架构真源。

### Fixed

- 收紧 inbound normalizer 边界修复 code review 发现：R-I3c 不再 broad allowlist 整个 orchestration 目录，import-linter 同时检查 `inbound_normalizer_registry`，并移除可被字符串前缀伪造的 caller-module runtime guard。

## [0.9.0.0] - 2026-06-25

### Added

- WorkLine 重构 **Phase 0：目标态锁定与架构护栏** 全部 7 任务交付完成。该阶段当时使用的主计划现已归档到
  `../archive_docs/wes_backend/docs/architecture/workline-and-plugin-restructuring.md`，不属于当前架构真源。
- **P0-001 Target State Contract**（历史归档：`../archive_docs/wes_backend/target-state-contract.md`）：抽取当时主计划的可执行合同，含 P0 系统能力 10 项、域边界 8 域、状态所有权矩阵 7 类对象、Authority Matrix 11 类事实权威来源、Plane 读模型边界、不做清单 14 条。
- **P0-002 Legacy Cleanup Matrix**（历史 Markdown 当前归档于
  `../archive_docs/wes_backend/docs/architecture/legacy-cleanup-matrix.md`；机器清单为
  `docs/architecture/legacy-cleanup-matrix.csv`，2191 entries / 0 pending-review）：逐入口标记
  delete/rebuild/move/keep-contract 策略，含 service module-level def + `__all__` 导出符号穷尽覆盖。生成器
  `scripts/generate_legacy_matrix.py` 可复现。
- **P0-003 Behavior Contract Baseline** (`tests/contracts/workline/`, `tests/characterization/workline_legacy/`, `tests/fixtures/workline_contract/`)：10 BC 全覆盖（强制 5 contract + 1 characterization + 4 strict xfail 壳），覆盖 start admission / runtime snapshot / handoff / resource projection / 粗分机入库 / 满箱交换 / 分拣机入库 / 缺 event_id / WMS authority cache / Event_Push 响应。28 pass + 3 strict xfail。
- **P0-004 ExecutionCorrelation Migration Matrix**（历史归档：`../archive_docs/wes_backend/session-correlation-matrix.md`）：逐文件列 39 个跨域 session FK 迁移路径（0 遗漏），按 resource/handling/rack/device/wms_integration/workline-runtime/material/sys 域分组。发现 device `session_id_int` ↔ session `awaiting_command_id` 外键环（HIGH 风险，进入 Phase 1 CEO-010）。ExecutionCorrelation schema 字段对齐当时主计划 §9.2（trace_id / source_event_id / business_owner_key），idempotency 引用当时主计划 §5.4 独立 idempotency_keys 表。
- **P0-005 Device Command Contract** (`docs/architecture/device-command-contract.md`)：当时以第三方设备白皮书为输入；该白皮书现已移至项目外归档，不是当前真源。当前合同锁定 Command-Ack-Callback 异步闭环、设备状态、Event_Push ACK、DeviceCommand 顶层边界和禁止字段（PLC/坐标/关节/安全回路）。
- **P0-006 External Contract Profile + IntegrationLab**（历史文档归档：`../archive_docs/wes_backend/external-contract-profile.md`、`../archive_docs/wes_backend/integration-lab-and-simulator.md`；当时测试资产：`tests/support/external_contract_profile.py`、`tests/fixtures/external_contracts/wms/default/`）：按 `provider_code + contract_version` 描述 WMS/ECS provider 外部合同；Pydantic schema 落 tests/support/（禁止 src/app/ import，Phase 1 CEO-013 升级到生产路径）；WMS fixture 5 个必填 case（success/reject/timeout/duplicate/missing_event_id）。归档文档不属于当前合同真源。
- **P0-007 Architecture Guardrails** (`scripts/architecture-guardrails.sh`, `scripts/architecture-guardrails.allowlist`, `tests/architecture/`, `docs/architecture/architecture-guardrails-spec.md`)：将主计划 §7.5 核心 5 条不变量（C1-C5）+ I3 capability 注入（R-I3a/R-I3b）映射为可执行扫描脚本。phase-aware 模式（phase0 warn-only / phase1 enforced / phase2 缩减 allowlist）；seed allowlist 31 条全部关联 `legacy_entry_id`（C1×5 + C2×22 + R-I3b×4 含 device 实现）。删任意 seed 行后 phase1 退出码 1（enforcement 真生效）。
- `scripts/git-quality-gate.sh --check architecture` 新增。Jenkinsfile `Quality Checks` stage 新增 `Architecture Guardrails` 并行步骤，默认 phase0，可通过 `ARCHITECTURE_PHASE` 环境变量切 phase1。
- `tests/architecture/` 6 个测试文件（C1-C5 + R-I3a/R-I3b，24 tests）；C5 使用 `tests/support/runtime_inbox_contract.py` 目标态状态机（RECEIVED/PROCESSING/PROCESSED/FAILED/DEAD_LETTER 6 转移），不 import legacy `WorklineInbox`。
- 3 个测试锁定 SPEC 验收为不变量：`test_phase0_legacy_matrix_contract.py`（service inventory + rebuild/move 必填 target/blocking + CSV 与生成器一致防漂移 + `__all__` 入矩阵 + R-I3b seed 指向具体 port）、`test_external_contract_profile_fixtures.py`（P0-006 5 fixture 真实可校验）、`test_git_quality_gate_architecture_profile.py`（quality profile 真执行 guardrails）。

### Changed

- 当时主计划（历史归档：`../archive_docs/wes_backend/docs/architecture/workline-and-plugin-restructuring.md`）§9.7
  曾将 `wms_integration` 履约请求端点从 POST 收口为 GET 只读，并约束出站 WMS 履约、库存事务和 PKG 绑定
  只能由当时的 runtime/orchestration 经 RuntimeIntentLog + EffectPort 调用；该记录只说明 0.9.0.0 版本事实，
  不属于当前架构真源。

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
- P9 WES 第三方设备接入白皮书（历史交付，现已移至项目外归档）
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
