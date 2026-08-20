# TODOS

> 2026-07-31 清理说明：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
> 是 WES 执行架构唯一目标基线。系统尚未发布，不保留旧版本兼容、旧数据迁移、双路径或旧架构后续事项。
> active TODO 只记录与最终架构一致、已有真实触发条件且尚未排期的独立工作；已完成事项由 Git 历史记录，
> 不继续保留在本文件。

## WorkLine

### WorkLine 角色与拓扑设备绑定向导

**What:** 为 WorkLine 配置台补充按标准设备角色、实际拓扑、现场容量和故障隔离范围组织的设备绑定向导。

**Why:** 最终架构只配置现场无法推导的设备实例、Endpoint、位置容量、角色绑定和物理拓扑；前端需要一个低噪声入口帮助运维补齐这些真实配置。

**Context:** 后端以 `WorkLine` 静态身份、标准角色约定和显式配置校验为真源，不使用 WorkLine/Vendor Manifest、动态能力 Catalog 或运行时发现。

具体工作线插件位于仓库根目录 `workline_plugins/<plugin_key>/` 独立二次开发包；核心 `tests/` 不保存
具体插件 fixture 或业务场景。绑定向导只面向 WES 通用 WorkLine 配置能力，不读取插件包测试资产。

**Scope:**

- 按工作线模式和标准角色展示待绑定设备、位置容量及拓扑缺口
- 支持从未绑定设备或当前 WorkLine 设备中选择/调整角色
- 活动 `LineRunEpoch` 存在时只读展示，提示清线并结束当前 Epoch 后调整
- 复用后端显式配置校验的 blocker、warning 和修复入口，不在前端复制业务规则

**Depends on:** 最终 WorkLine、设备角色、位置拓扑和配置校验合同稳定。

**Effort:** M

**Priority:** P2

---

## Operations

### 统一运营看板、告警与 Runbook

**What:** 在最终执行对象、设备和 WMS 集成指标稳定后，建设统一运营看板、告警阈值和现场 Runbook。

**Why:** Transport、独立 Device/ECS、硬件故障和基础依赖需要统一运营入口，避免每条工作线重复建设看板和告警口径；
PickingTask、WMS 业务确认等业务指标继续由各业务 owner 定义，不能混入基础能力口径。

**Context:** Celery Worker 单异步运行时改造会新增按 role/PID/run-id 结构化的 PostgreSQL `application_name`、连接预算门禁和 pool timeout 配置；这些信号应并入同一运营面，而不是再建一套数据库专用看板。

北向 WMS 已定义同步调用、breaker、超时和业务拒绝指标口径。本 TODO 不重建领域口径，只在最终信号稳定后
聚合到跨执行对象、Device、WMS 和 Database 的统一运营面。

**Scope:**

- InboundEvidence 接收/处理延迟、幂等冲突和失败数
- DeviceCommand ACK age、CALLBACK age、dispatch deadline、设备 ERROR/OFFLINE/MAINTENANCE
- TransportTask 批次状态、终态成员最终事实、批次完成延迟和失败数
- 硬件故障、依赖暂停、人工清线和迟到 CALLBACK 证据
- 聚合 WMS 同步调用、breaker、timeout/5xx、429/Retry-After 和业务拒绝信号
- Database pool checkout wait/timeout、按 `application_name` 的连接预算占用、`idle in transaction` 数量
- 数据库告警与 Runbook：例如 `idle in transaction > 0` 持续 2 分钟、pool timeout 或预算占用接近上限
- 现场 Runbook：WMS/RCS 拒绝、TransportResult 缺失、command evidence 缺失、设备状态不一致和人工清线

**Depends on:** Phase 4 Transport 指标、独立 Device/ECS 指标、Celery 单异步运行时、连接预算与结构化
`application_name` 落地，并产生真实或接近真实的试运行数据。业务指标由对应业务计划另行交付。

**Effort:** M-L

**Priority:** P2

---

### 分拣机/粗分机供应商联调操作手册

**What:** 在最终 DeviceCommand、CALLBACK、InboundEvidence、WMS 同步能力和入库流程稳定后，编写分拣机/粗分机供应商联调手册。

**Why:** 顶层设计只定义 WES/ECS/WMS 边界和业务合同；供应商联调还需要可执行的 payload 样例、回调样例、异常码、测试步骤和恢复流程。

**Context:** 手册应在第三方设备统一接口和该设备合同附录稳定后，从真实接口与回调样例生成，不引用旧 Runtime、
WorkLineInbox、自动 replay、供应商私有路径或兼容 Payload。

**Scope:**

- 设备角色、统一命令 payload、callback result/event 样例
- 正常入库、NG、满箱/换架、设备失败、WMS/RCS 拒绝五类联调场景
- 稳定 `command_code`、部署级唯一 `source_event_id`、`trace_id` 与核心关联键的使用约定
- ECS 同步 ACK 只表示请求接纳；后续动作由 WorkLine 插件产生封闭 Decision，经 `DeviceCommand` 和统一设备接口下发；
  供应商内部协议由 ECS/网关收敛，不进入 WES 私有 Adapter
- 进程重启后的证据核对、人工清线和新 LineRunEpoch 恢复步骤

**Depends on:** 最终 DeviceCommand、第三方设备统一接口、粗分机设备合同附录和入库业务合同稳定。

**Effort:** M

**Priority:** P2

---

## Reliability

### API/Worker 跨进程就绪快照

**What:** 为 `/ready` 建立 API/Worker 共享的跨进程就绪快照，并验证缺失、损坏和 stale 状态均 fail closed。

**Why:** `/ready` 仍读取 API 进程内单例，而 Celery 只更新 Worker 进程副本；多进程部署下无法形成可验证的同一就绪事实。

**Resolved (2026-08-20):** 树形 `parent_id` 已与 Snowflake BIGINT 主键对齐；权限目录、五个内置角色、内置角色授权和首个管理员已由
`AuthorizationBootstrapService` 与 `bootstrap_foundation` 统一拥有，静态生产 SQL 已删除。

**Scope:**

- 为 `/ready` 建立跨进程真实测试，明确共享快照、缺失/损坏/stale 的 fail-closed 语义；不直接复用历史 Redis 实现而跳过当前 Redis 降级评审
- 每个行为先建立 current-develop RED，再做最小实现；禁止恢复历史 release 元数据或复制未评审的 Redis 实现

**Depends on:** Redis 共享快照的 fail-closed 所有权批准；历史 bundle 与补丁审计保留。

**Effort:** M

**Priority:** P1

---

### FAST 测试执行时间优化与 60 秒预算恢复

**What:** 定位并消除 Transport、OpenAPI 和 callback 路由 FAST 测试中的重复应用初始化与重复装配，使 Jenkins
参考环境中的完整 FAST 套件重新稳定在 60 秒预算内。

**Why:** Jenkins `wes_backend-ci #78` 的功能断言全部通过，但 FAST 套件在 `2 CPU / 4 GB` 限制下耗时
`74.741s`，超过原 60 秒预算并阻断 TEST 环境部署。为优先恢复可联调环境，套件总预算临时调整为 90 秒；
后续 `#79` 中一个数据库模型测试因 CI 节点抖动从 `2.82s` 升至 `3.229s`，因此单测试预算临时从 3 秒调整为
4 秒；`tests/unit/` p95 门禁保持不变。

**Scope:**

- 在 Jenkins 等价 `2 CPU / 4 GB` 容器限制下分析 Transport、OpenAPI 和 callback 路由测试耗时
- 复用不会改变测试隔离性的 FastAPI/OpenAPI 装配结果，消除重复初始化
- 保留现有 FAST 断言和测试所有权，不以迁移到 HEAVY 或删除覆盖作为提速手段
- 完整 FAST 套件稳定不超过 60 秒、单测试稳定不超过 3 秒后，将临时 90 秒/4 秒预算恢复为 60 秒/3 秒

**Depends on:** TEST 联调环境成功部署后安排，不阻塞当前 WMS/WES 非业务联调。

**Effort:** S

**Priority:** P1

---

### Mock 镜像依赖锁定与纯局域网分发

**What:** 为 tests/mock/Dockerfile 建立可复现的 Python 依赖锁定和预构建镜像离线分发流程。

**Why:** 当前 Mock 镜像通过 pip 安装未锁版本，并在构建时依赖 Debian/PyPI 镜像；预构建镜像可以在局域网离线运行，但无法保证现场离线构建或未来重建得到相同结果。

**Pros:** 固定 FastAPI、Swagger UI 兼容性和 Mock 运行环境，便于现场保存、校验和重建同一镜像。

**Cons:** 会影响 ECS/WMS 共用 Mock 镜像的构建方式，需要独立评估 uv.lock、离线 wheel、基础镜像归档和 CI 发布，不应混入单次 Swagger 调试面改动。

**Context:** WMS Transport Mock Swagger 计划只承诺预构建镜像离线运行，并通过 image ID 绑定验收对象。本 TODO 应优先复用仓库现有 uv.lock、镜像构建和 SHA-256 交付约定，不另建包管理器或私有依赖体系。

**Depends on:** 当前 WMS/ECS Mock 重构和镜像职责稳定，并明确现场是否要求离线重建而不只是离线运行。

**Effort:** M

**Priority:** P2

---

### 全仓 Redis fail-open/fail-closed/fallback 审计

**What:** 审计整个仓库所有 Redis 调用点，明确每个调用是 fail-open、fail-closed 还是带 fallback，并补齐缺失的降级或错误处理。

**Why:** Celery Worker 和入站异步处理只覆盖了部分 Redis 降级边界；其余模块（缓存、锁、SSE、IP 定位等）可能仍存在无明确语义的 Redis 失败路径，需要在独立后续项中统一。

**Context:** `src/database/redis_client.py` 已提供原子初始化和降级模式，但尚未覆盖全仓调用点。本 TODO 应输出一份调用点清单与每点的失败语义。

**Scope:**

- 列出所有 `RedisManager` / `redis_client` 调用点
- 标注 fail-open（继续服务）/ fail-closed（报错拒绝）/ fallback（PostgreSQL advisory lock 等）
- 对未标注或语义不一致的调用点补齐处理与测试

**Depends on:** 最终 Worker 与入站处理路径稳定，Redis 原子初始化稳定。

**Effort:** M (human: ~1 day / CC: ~30 min)

**Priority:** P3

---

### 空库 Checksums 与最小数据库角色

**What:** 在 Phase 10 退出门禁通过、Phase 11 冻结最终 schema 后，为新建 PostgreSQL 数据目录评审 data checksums，并拆分 bootstrap/migrator/runtime 的最小权限角色。

**Why:** 当前生产 Compose 仍使用同一 `POSTGRES_USER` 启动数据库、迁移和应用。直接在现场韧性计划中同时改角色、schema 和恢复流程会扩大故障面，并与 Phase 11 空库基线重置的所有权重叠。

**Scope:**

- 在独立空数据目录验证 checksums 初始化、检测和失败回滚，不转换旧数据目录
- 定义 bootstrap、migrator、runtime 的 owner、连接变量和最小数据库/schema权限
- 先建立空库角色与 Alembic successor 测试，再替换单一运行账号
- 与 `docs/superpowers/plans/2026-08-15-wes-schema-and-migration-baseline-reset.md` 的最终 schema manifest、空库迁移链和实施窗口联合评审
- 不提供旧角色别名、兼容 wrapper、双账号回退或旧数据迁移路径

**Depends on:** Phase 10 零旧生产路径退出；Phase 11 Task 1 冻结最终 schema 与专有对象；独立数据库安全 wrapper 可用。

**Effort:** M

**Priority:** P2

---

## Completed

当前没有已完成待办；完成记录以版本号和日期随发布移动到本节。
