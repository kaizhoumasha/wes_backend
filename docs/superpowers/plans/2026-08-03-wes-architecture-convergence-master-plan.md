# WES 最小执行架构九阶段收敛总控实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按九个有明确入口、交付物和退出门禁的阶段，将当前 WES 直接收敛到 SPEC
定义的最小执行架构，并以独立业务插件、单一数据库基线和全量验收结束收敛。

**Architecture:** 测试治理、增量删除和最终基线是三条贯穿主线。测试先冻结所有权和重量，
在最终核心对象及插件交付后分段承接；生产旧路径在替代能力交付时立即删除；目标模型随平台能力
建立，历史 migration 只在模型稳定后一次性重建。本文控制阶段依赖和验收，不替代子系统详细计划。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL/TimescaleDB、
Alembic、Celery、Pydantic、Pytest 9、Ruff、Bandit、Import Linter、Jenkins。

**Status:** Approved

**Requirements baseline:** `docs/architecture/SRS.md`

**Design baseline:** `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`

**Implementation baseline:** `origin/develop@cf2f1f91`

## Global Constraints

- 系统尚未发布；开发和测试数据可以清空，不保留旧版本、旧 API、旧字段、旧配置、旧数据或历史 revision 的迁移能力。
- SRS 定义产品需求，顶层 SPEC 定义目标架构，本文只编排实施顺序；实施任务不得反向扩张业务需求或把历史实现提升为需求。
- 严格遵守 DRY、KISS、SOLID、YAGNI；只有出现三次且语义稳定的重复才允许抽取小型技术库，不建设通用工作流、动态能力平台或推测性扩展点。
- 最终运行态只能存在一条执行路径；禁止兼容 shim、alias、re-export、deprecated wrapper、
  双写、双读、旧路径 fallback 和按 WorkLine 切分的新旧双轨。
- WMS 是业务单据、库存、主数据、业务授权和全局仓内位置权威；WES 只拥有工作线本地执行事实；ECS/PLC 拥有设备物理动作和安全互锁。
- WMS 薄接入层只提供类型化同步查询、DTO、认证、地址、错误映射和确认发送适配；WES 编排、
  执行对象、可靠确认生命周期和业务 Decision 不得进入 WMS 层。
- WES 核心 `tests/` 只验证最小执行内核、通用 WorkLine、共享外部合同、架构边界和可靠性不变量；
  具体厂商合同测试由 `device_adapters/<adapter_key>/` 独立拥有，具体工作线和业务插件测试由
  `workline_plugins/<plugin_key>/` 独立拥有。
- 测试所有权禁止相互替代：核心只验证基础能力，Adapter 只验证厂商合同与标准化映射，插件只验证业务 Decision
  和对象推进；不得用任一层的 E2E 代替另一层的测试，也不得以具体业务 happy path 证明核心基础能力。
- 插件不得直接访问 Repository、数据库 Session、HTTP Client、Service Locator 或自行启动后台任务；
  只能读取注入投影、调用类型化 `WmsCapabilities` 并返回封闭 Decision。
- 根项目以 uv workspace 管理核心与 Adapter/插件包；二次开发包必须独立构建和测试，客户镜像在构建期显式选择安装并由
  composition root 显式绑定。当前不建设运行时动态发现或私有包 registry。
- 每项 WMS 能力使用垂直模块内聚 DTO、固定 method/path、拒绝码和 `WmsCallSpec`；公共端口与 Gateway 使用
  显式窄方法，不建立生产 runtime registry、generic `call` 或 WMS codegen。
- 每项替代能力与其旧所有者必须在同一阶段完成测试承接和删除；不得以“阶段 7 统一清理”为理由继续保留已经失去目标职责的生产路径。
- 每个阶段开始前必须有一份经批准的详细实施计划，包含准确文件、接口、TDD 步骤、验证命令和提交边界；本文不得直接作为代码实施脚本。
- 阶段实施在同一架构收敛集成分支上连续进行；除已于基线前合入的阶段 1 外，不把中间双轨态或未完成收敛态合并回 `develop`。
- 每个阶段的提交必须保持对应测试和质量门禁为绿色；禁止提交 intentional failing test、长期 `xfail`、测试别名或为暂存旧实现而新增的兼容资产。

---

## 1. 总控模型

九个阶段采用单向依赖：

```text
Phase 1  测试治理基线
   ↓
Phase 2  WMS 薄接入边界
   ↓
Phase 3  WES 最小平台能力
   ↓
Phase 4  核心测试承接与平台验收
   ↓
Phase 5  粗分机参考插件
   ↓
Phase 6  分拣业务插件组
   ↓
Phase 7  旧生产路径最终闭环
   ↓
Phase 8  旧数据模型与 migration 清理
   ↓
Phase 9  最终基线与系统验收
```

允许提前编写和评审下一阶段的详细计划，但不得在上一阶段退出门禁未通过时启动下一阶段生产代码实施。

三条横向主线：

| 主线 | 阶段归属 | 规则 |
| --- | --- | --- |
| 测试治理 | 1 → 4 → 5/6 → 9 | 冻结治理、承接核心、独立验收、关闭门禁 |
| 旧代码删除 | 2–6 → 7 | 替代能力交付时立即删除直接旧所有者；阶段 7 只处理跨阶段残留和全仓零命中 |
| 数据基线 | 3 → 8 → 9 | 阶段 3 建立目标模型；阶段 8 删除历史 schema/revision 并生成唯一基线；阶段 9 从空库验收 |

## 2. 阶段总览

| 阶段 | 名称 | 当前状态 | 核心结果 |
| --- | --- | --- | --- |
| 1 | 总控基线冻结与测试治理确认 | 基线已合入 | 测试所有权、重量及延后边界明确 |
| 2 | WMS 薄接入边界收敛 | 下一阶段 | 新 WMS 公共边界与旧 Runtime/Effect/Capability 编排语义完全解耦 |
| 3 | WES 最小平台能力建设 | 未开始 | 最终核心对象、可靠性记录、WorkLine SPI/SDK 和投影形成可运行闭环 |
| 4 | 核心测试承接与平台基线验收 | 未开始 | 核心可靠性在最终对象上有唯一权威测试，平台不依赖真实插件即可验收 |
| 5 | 粗分机参考插件优化 | 未开始 | 首个真实插件独立交付并验证平台扩展边界 |
| 6 | 分拣业务插件优化 | 未开始 | 自动、人工、满箱交换和复杂出库按真实工作线分别交付 |
| 7 | 旧平台代码最终闭环清理 | 未开始 | 生产代码、测试、配置和当前态文档对旧架构零引用 |
| 8 | 旧数据模型与迁移链清理 | 未开始 | 最终 metadata 和单一 Alembic 初始基线 |
| 9 | 最终基线与系统验收 | 未开始 | 空库、核心、插件、质量和缺席门禁共同通过 |

## 3. Phase 1：总控基线冻结与测试治理确认

**Objective:** 接受 SPEC、冻结实施基线，并把已完成的测试语义/重量治理与依赖最终对象的延后义务明确分开。

**Authoritative plan:** `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`

**Completed deliverables:**

- [x] 核心与插件测试所有权边界已经写入长期规则。
- [x] `tests/workline_plugins/` 和显式旧插件 characterization 测试已经移出核心。
- [x] 核心 FAST、QUALITY、HEAVY 拓扑和预算门禁已经建立。
- [x] 测试计划 Task 1、2、3、6 已完成，Task 4、7 的可独立部分已完成。
- [x] `origin/develop@cf2f1f91` 被确认为后续实施基线。

**Carried obligations:**

- 测试计划 Task 4 的五个混合资产继续保留到 Phase 4；不得在最终核心权威测试建立前直接删除。
- 测试计划 Task 5 在 Phase 3 交付最终生产对象后，于 Phase 4 执行。
- 测试计划 Task 7 分三段关闭：核心测试缺席门禁归 Phase 4，插件同包交付归 Phase 5/6，
  旧 revision 和最终质量门禁归 Phase 8/9。
- Phase 1 的“治理基线完成”不等于测试收敛计划整体完成。

**Exit gate:** 上述完成项、延后项及阶段归属在 SPEC、测试计划、`TODOS.md` 和本文中一致；不存在要求 Phase 1 越权实现生产执行内核的任务。

## 4. Phase 2：WMS 薄接入边界收敛

**Objective:** 把现有北向 WMS 能力收敛为 WES 可依赖的薄端口和适配器，不让 WMS 层继续承载旧执行平台或未来 WES 编排。

**Required child plan:** `docs/superpowers/plans/2026-08-03-wes-wms-thin-access-convergence.md`

**Inputs:**

- SPEC §4.1、§5.3、§6.3 定义的 WMS 权威边界、同步调用和 Decision 依赖。
- `origin/develop@18799144` 已交付的类型化 WMS 仓内能力及 `cf2f1f91` 测试治理基线。

**Scope:**

- [ ] 对现有 `wms_integration` 54 个生产文件建立 `KEEP/MOVE/DELETE/PHASE3_HANDOFF` 处置矩阵，并通过
  import closure 门禁证明 Phase 2 删除集不被保留资产静态引用。
- [ ] 冻结插件可调用的类型化 `WmsCapabilities` 查询接口；禁止返回 HTTP、旧 Runtime 或通用 Effect 类型。
- [ ] 每项能力建立独立垂直模块，内聚 wire DTO、固定 method/path、拒绝码和 `WmsCallSpec`；测试态
  conformance harness 核对模块、Protocol、Gateway 和共享错误映射，生产运行时不得动态发现。
- [ ] 所有 WMS 写操作与转发搬运只使用 `dispatch_key` 作为 submit、ACK、status、terminal、cancel、hint
  全链路唯一 wire 幂等键，并以 `operation_identity + dispatch_key` 原子去重；不得定义
  `idempotency_key` 别名或双键映射。
- [ ] 冻结供 `WmsConfirmation` 调用的确认发送端口；本阶段不拥有确认义务的持久化、领取、重试或终态生命周期。
- [ ] 把 E08–E14/E16 收敛为无状态的 WMS 转发搬运 Client；TransportTask、轮询、重试和终态推进仍归 Phase 3 的 Transport Port。
- [ ] 原子切换 19 项 QUERY 和 Q19 机械依赖注入，删除 QUERY System Capability definitions 并收缩生成索引；
  fulfillment definitions 与 Provider/Catalog 旧语义继续随旧 Effect 链冻结到 Phase 3，不扩展、不临时重写。
- [ ] 以 `WMS_CONFIG_FILE` 作为目标 Gateway 的唯一配置输入，装配 API/Celery 各一个进程级 Gateway 和
  `httpx.AsyncClient`，验证 Compose/Jenkins、fail-fast 启动和资源关闭；只修改 tracked `.env.*` profile，
  worktree-local `.env` 仅由 `init-env.sh` 生成且不暂存，也不让新 loader fallback 到冻结旧 Effect 链的
  Provider 配置。
- [ ] 冻结 evidence fail-closed 与远端结果未知语义，以及逻辑分页的一次 breaker permit、累计预算、单条
  evidence 和一次最终 breaker 更新。
- [ ] 用共享 WMS 合同测试验证 DTO、认证、错误分类、超时和业务拒绝；具体操作流程和具体插件 Payload 不进入核心测试。
- [ ] 形成 Phase 3 可直接消费的窄端口，不增加 bridge、alias、双轨 dispatcher 或第二套活动可靠性生命周期。
- [ ] 为 `.env.*`、Compose/Jenkins、`src/core/conf.py`、`src/register.py`、Gateway factory/configuration 和
  WMS 进程装配补充精确 HEAVY selector mapping；`src/celery_app/async_runtime.py` 变更必须运行其现有三项
  Celery HEAVY 测试，不能仅以 FAST 部署测试代替真实进程生命周期验证。

**Atomic handoff rule:** 旧 Runtime/Effect/status 链当前仍是 WMS 确认和转发搬运可靠性的唯一活动所有者。
Phase 2 不改写该链，只交付无状态 sender/client 并切换 QUERY；该链及其静态依赖必须保留到 Phase 3 的
`WmsConfirmation` 与 `TransportTask` 生产路径和权威测试建立后，在同一切换任务中删除。这是一条单向实现
依赖，不是兼容层或运行时双轨。

**Out of scope:** `InboundEvidence`、`DeviceCommand`、`TransportTask`、`WmsConfirmation`
生命周期、WorkLine 编排、具体业务 Decision 和插件规则。

**Exit gate:**

- WMS 目标公共模块只表达北向类型化业务能力、同步确认发送和无状态 transport adapter；旧可靠性所有者有明确的 Phase 3 删除清单。
- 核心及插件只能通过类型化端口访问 WMS，不直接访问 HTTP。
- WMS 目标公共模块不 import 旧 Runtime、Manifest、通用 Intent/Effect、Outbox 或 System Capability 所有者。
- Phase 3 所需查询端口、确认发送端口和 WMS 转发搬运 Client 均有稳定合同测试。
- API/Celery 装配、`WMS_CONFIG_FILE`、进程级 HTTP Client 生命周期和 QUERY 切换已通过部署测试。
- QUERY definitions 已从 WMS System Capability 与 generated index 删除；fulfillment definitions 及其旧链依赖
  有明确 Phase 3 handoff，生成索引无悬空 import。
- 旧可靠性链未被临时重写且仍是唯一活动实现，没有新增能力、兼容入口或第二份状态；其删除门槛由 Phase 3
  原子交接任务锁定。
- HEAVY selector 能从每个受影响生产/部署路径精确选中 WMS PostgreSQL 进程装配测试及既有 Celery runtime
  三项测试，所有命中测试均已实际通过。

## 5. Phase 3：WES 最小平台能力建设

**Objective:** 在不实现任何真实工作线业务规则的前提下，交付 SPEC 定义的最小执行对象、可靠性闭环、通用 WorkLine 和插件 SPI/SDK。

**Required child plan:** `docs/superpowers/plans/2026-08-03-wes-minimal-platform-capabilities.md`

**Produced interfaces:**

- `WorkLine` 与 `LineRunEpoch`。
- `MaterialExecution`、`BinExecution`。
- `PositionProjection`、`DeviceRuntimeProjection`。
- `InboundEvidence`、`DeviceCommand`、`TransportTask`、`WmsConfirmation`。
- `WmsCapabilities`、`ProjectionReader`、Transport Port 和封闭 Decision Factory。
- 最小插件 SPI/SDK、显式依赖注入、部署时显式插件绑定和无业务语义 fake。

**Required Phase 2 handoff:** E08–E14/E16 的 WMS 转发 AGV/CTU 交接包必须已经通过独立验收：
request/ACK/status/terminal/cancel DTO、七组 submit/status 方法、一个 cancel 方法、无状态 fake、认证、
错误映射和调用证据全部可用；包内不得包含 TransportTask、轮询、重试或投影推进。

**Scope:**

- [ ] 以 TDD 建立最终领域对象、状态机、Repository 和数据库约束。
- [ ] 实现 ECS Event 持久化后 ACK、同键同 Payload 幂等和冲突证据。
- [ ] 实现 DeviceCommand 下发前持久化、ACK/CALLBACK 分离、未知物理结果不自动重放。
- [ ] 实现 `InboundEvidence` 领取幂等和跨 `LineRunEpoch` CALLBACK fencing。
- [ ] 实现 `TransportTask` 批次状态、终态成员最终事实及可替换 Transport Port；不建立独立成员进度生命周期。
- [ ] 实现 `WmsConfirmation` 持久化义务、受控领取、重试和依赖恢复；调用 Phase 2 的确认发送端口。
- [ ] 写操作在 evidence 发送后持久化失败时保留“远端结果未知”，`WmsConfirmation`/`TransportTask` 使用原
  `dispatch_key` 恢复，禁止生成新键或按普通依赖失败自动重放。
- [ ] 将 WMS 转发搬运 Client 接到 Transport Port；在 `TransportTask` 与 `WmsConfirmation` 权威测试通过后，
  原子删除旧 WMS Effect/status/Outbox 生命周期、fulfillment System Capability definitions、剩余 generated
  index 条目及 Provider/Catalog 静态依赖闭包。
- [ ] 实现对象、位置、队列和单设备忙闲投影；不建设设备间软件互锁或工作线级全局锁。
- [ ] 用最小 fake 验证插件 SPI/SDK、封闭 Decision、显式依赖注入及禁止数据库/HTTP 访问。
- [ ] 建立 uv workspace、Adapter/插件独立 build/test 入口和构建期显式装配合同；不实现运行时发现或私有 registry。
- [ ] 每交付一个最终所有者，立即删除其直接替代的旧生产路径和旧测试；保留通用不变量必须先改写到最终对象。

**Out of scope:** 粗分机、自动分拣、人工分拣、满箱交换、复杂出库、具体厂商命令和现场拓扑。

**Exit gate:**

- Fake 插件可以驱动 Event/Input → Evidence → Decision → Command/Transport/Confirmation
  → Result → Projection 的最小闭环。
- 核心对象和端口不包含具体工作线、客户、厂商或物料业务规则。
- 处理幂等、ACK/CALLBACK、迟到证据、人工清线和依赖暂停拥有最终生产路径。
- Phase 4 的测试承接入口条件全部满足。

## 6. Phase 4：核心测试承接与平台基线验收

**Objective:** 把跨插件通用的可靠性和 WorkLine 语义完全迁移到 Phase 3 最终对象，并证明平台在没有真实业务插件时可以独立验收。

**Required child plan:** `docs/superpowers/plans/2026-08-03-wes-platform-baseline-acceptance.md`

**Scope:**

- [ ] 执行测试收敛计划 Task 5，在最终对象上建立唯一权威核心测试。
- [ ] 完成 Task 4 五个混合资产的 `CORE_REWRITE`、`PLUGIN_OWNED` 或 `LEGACY_DELETE` 处置。
- [ ] 扩展核心/插件所有权门禁，禁止核心测试导入任何具体工作线插件或具体插件源码。
- [ ] 验证 WorkLine/Epoch、入站幂等、命令证据、CALLBACK fencing、位置容量、设备忙闲、TransportTask 和 WmsConfirmation。
- [ ] 删除已完成测试承接的旧 RuntimeInbox、Intent/Effect、Capability、Manifest、Hold、
  Recovery 和 Reservation 测试。
- [ ] 运行核心 FAST、QUALITY 和受影响 HEAVY；记录平台基线结果。

**Out of scope:** 任何具体工作线业务闭环和插件验收；这些只在 Phase 5/6 的独立插件包中完成。

**Exit gate:**

- 测试计划 Task 4、5 的核心承接部分全部完成。
- 核心 `tests/` 不包含粗分机、自动分拣、人工分拣、满箱交换或复杂出库业务断言。
- 平台使用最小 fake 即可通过核心执行、可靠性、架构和重量门禁。
- 本阶段结果称为“平台核心基线”，不得称为最终系统基线。

## 7. Phase 5：粗分机参考插件优化

**Objective:** 以粗分机作为首个真实业务插件，验证平台端口足以支持现场命令、局部业务 Decision、可靠结果和独立测试交付。

**Required child plan:** `docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md`

**Inputs:** 仓库物理工作线现状、粗分机实际拓扑、设备角色、厂家事件/命令清单、Payload 样例和 WMS 操作清单。

**Scope:**

- [ ] 在独立 `workline_plugins/<plugin_key>/` 包中同步交付代码、测试和 fixture。
- [ ] 在独立 `device_adapters/<adapter_key>/` 包中同步交付该现场需要的厂商 Adapter 代码、测试和 fixture。
- [ ] 实现粗分机事件解释、WMS 查询、本地测量判定、设备长命令、即时目标料格选择、NG 和 WMS 确认 Decision。
- [ ] 厂商 DTO、认证差异、事件/命令 Payload、原始码映射和合同测试只由
  `device_adapters/<adapter_key>/` 拥有；插件包只消费标准化角色事件与逻辑动作并验证业务推进。两类包可以在
  同一次客户交付中出现，但不得混淆所有权，均不写入核心 `tests/`。
- [ ] 独立验证入料、流水、出料、无可用料格、补空箱货架、设备失败、WMS 拒绝和依赖暂停。
- [ ] 若发现 SPI 缺口，只允许扩展跨至少两个已确认消费者仍成立的最小技术能力；粗分机专属规则不得进入核心。
- [ ] 删除被粗分机最终插件替代的旧粗分业务代码、配置和测试。

**Exit gate:**

- 插件包可独立安装、配置和测试，核心默认 pytest、覆盖率和 HEAVY selector 不收集其测试。
- Adapter/插件包作为 uv workspace member 独立构建；客户镜像只安装明确选择的包，composition root 只绑定该清单。
- 粗分机业务闭环仅由该插件包拥有，核心没有镜像测试或特殊分支。
- Phase 3 平台接口无需依赖粗分机命名、厂商类型或现场拓扑。

## 8. Phase 6：分拣业务插件优化

**Objective:** 按实际工作线和厂家合同分别交付自动分拣、人工分拣、满箱交换和复杂出库能力，不建设包揽全部设备的通用分拣工作流。

**Required child plans:**

- `docs/superpowers/plans/2026-08-03-automatic-sorter-plugin-convergence.md`
- `docs/superpowers/plans/2026-08-03-manual-sorter-plugin-convergence.md`
- `docs/superpowers/plans/2026-08-03-full-bin-exchange-plugin-convergence.md`
- `docs/superpowers/plans/2026-08-03-complex-outbound-plugin-convergence.md`

每个独立业务插件必须拥有一份经批准的实施计划。计划依据对应工作线物理拓扑和厂家指令清单编写，不得复制粗分机计划后只替换名称；如果现场事实证明两个逻辑能力属于同一部署插件，应先修订本阶段计划清单再实施，不预建空插件包。

**Scope:**

- [ ] 自动分拣插件覆盖入库、出库、SCAN1/2/3、本线队列、即时 PUT 目标、CTU 批次和同线进出不变量。
- [ ] 人工分拣插件只编排 WES 位置与 WMS 人工作业接纳，不把 PDA 或操作员建模为虚拟 ECS 设备。
- [ ] 满箱交换按货架整体移出后的冻结快照执行，不与粗分机持续装料或自动线货架位混用。
- [ ] 复杂出库严格消费 WMS 来源分配，按料盘数量和 LIFO/单储位约束执行，不建立 WES 库存权威。
- [ ] 四条串联线通过各自 WorkLine、不可变 owner、NG 透传和下一 SCAN1 Event 自然接续；不建立 `SorterCorridor`。
- [ ] 每个 Adapter 包独立拥有厂商 DTO/映射、fixture、合同测试、集成、E2E 和韧性验收。
- [ ] 每个插件包独立拥有业务 fixture、单元、集成、E2E、韧性、并发和负载验收，不包含厂商 wire 合同。
- [ ] 每个 Adapter/插件包加入 uv workspace，独立 build/test，并由客户镜像构建清单显式选择；删除能力时同步移除 member、
  安装项、装配绑定和对应包，不保留 tombstone。
- [ ] 每交付一个 Adapter，立即删除核心或插件中对应的厂商协议、映射、fixture 和测试副本。
- [ ] 每交付一个插件，立即删除其对应旧业务代码、配置和测试。

**Exit gate:**

- 两条自动线可以使用同一自动插件的两个配置实例，两条人工线可以使用同一人工插件的两个配置实例。
- 所有已交付插件均代码、测试、fixture 同包存在并独立通过。
- 核心没有具体插件 import、fixture、业务测试或按插件名称分支。
- 未出现通用工作流 DSL、动态插件发现、Manifest、Service Locator 或推测性平台扩张。

## 9. Phase 7：旧平台代码最终闭环清理

**Objective:** 在阶段 2–6 已完成随替代随删除的基础上，清除跨阶段残留，证明最终生产运行态只有一套最小执行架构。

**Required child plan:** `docs/superpowers/plans/2026-08-03-wes-legacy-production-path-removal.md`

**Scope:**

- [ ] 全仓逐文件扫描旧 Runtime、System Capability、Manifest、Generic Intent/Effect、Hold、
  Recovery、Reconciliation、CellReservation、提前目标格预约和自动 replay。
- [ ] 删除残留生产代码、Repository、模型、任务、API、配置、脚本、生成索引、测试 fixture 和当前态文档引用。
- [ ] 删除兼容 shim、旧名称 alias、re-export、deprecated wrapper、双写、双读和 fallback。
- [ ] 建立语义缺席门禁；扫描目标是旧所有者和旧路径，不误伤合同样例回放、可靠确认重试等最终行为。
- [ ] 验证应用装配、Celery 注册、API 路由和部署配置只引用最终对象及独立插件。

**Exit gate:** 生产代码、核心测试、配置和脚本由机器缺席门禁证明对旧架构零引用；当前态人类阅读文档通过
引用审查、原路径缺席和外部归档检查证明零误导。文档正文不得成为 pytest 或质量门禁输入；任何剩余历史名称
只允许存在于 Git 历史、项目外 `../archive_docs/wes_backend/` 归档或解释删除目标的架构门禁中。
项目内不得保留 superseded 文档、副本、占位文件、软链接或转发文档。

## 10. Phase 8：旧数据模型与迁移链清理

**Objective:** 在最终模型和插件需求稳定后，删除未发布系统的旧 schema 与 migration 历史，
生成唯一、可从空库建立系统的 Alembic 初始基线。

**Required child plan:** `docs/superpowers/plans/2026-08-03-wes-schema-and-migration-baseline-reset.md`

**Scope:**

- [ ] 确认最终 SQLModel metadata 只包含目标核心、共享外部合同、运维和安全所需模型。
- [ ] 删除旧 Runtime、Manifest、Capability、Intent/Effect、Hold、Recovery、Reservation 及其他已失去目标职责的表、字段、约束和索引。
- [ ] 删除未发布 migration revision chain 及其 upgrade/downgrade、数据回填和兼容测试。
- [ ] 清空开发/测试数据库，不编写旧数据转换、桥接表、临时回填或 downgrade。
- [ ] 使用以下命令生成随机 revision ID 的单一干净基线：

  ```bash
  rtk uv run alembic revision --autogenerate -m "建立 WES 收敛基线"
  ```

- [ ] 从空库运行 `rtk uv run alembic upgrade head`，验证 schema、约束、索引、
  TimescaleDB 扩展对象和 metadata 一致性。

**Exit gate:**

- `migrations/versions/` 只包含最终初始基线及其后真实新增 revision。
- 空数据库可以一次 `upgrade head` 建立完整目标 schema。
- 仓库不存在旧 revision chain、旧数据迁移、schema downgrade 或兼容断言。

## 11. Phase 9：最终基线与系统验收

**Objective:** 从干净环境证明核心平台、每个厂商 Adapter、每个业务插件、数据库基线、部署装配和全部缺席门禁共同满足 SPEC。

**Required child plan:** `docs/superpowers/plans/2026-08-03-wes-final-architecture-acceptance.md`

**Acceptance sequence:**

- [ ] 从空数据库执行最终 Alembic baseline，并验证 metadata、schema、约束、索引和扩展对象。
- [ ] 运行核心架构、测试所有权和旧架构缺席门禁。
- [ ] 运行核心 FAST，并强制 60 秒总预算、1 秒单例预算和 `tests/unit/` p95 预算。
- [ ] 运行核心 QUALITY、受影响 HEAVY 和完整质量门禁。
- [ ] 在每个 Adapter 包目录运行厂商合同、集成、E2E 和韧性入口；在每个插件包目录运行单元、集成、E2E、韧性、并发和负载入口。
- [ ] 以真实或验收级 WMS/ECS/RCS Adapter 验证共享合同，不把具体插件验收回写核心。
- [ ] 验证部署配置只能装配最终平台和明确选择的 Adapter/插件，不存在运行时动态发现或旧 fallback。
- [ ] 验证 uv workspace 锁定、各 Adapter/插件独立构建产物、客户镜像安装清单和 composition root 绑定完全一致。
- [ ] 核对 SRS、SPEC、插件开发指南、当前 ADR、TODO 和运维文档的当前态一致性；SRS 继续作为需求基线保留，只有被取代的历史设计移出项目归档。

**Core verification floor:**

```bash
rtk uv run pytest tests/architecture/test_suite_topology_guardrail.py \
  tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
rtk uv run pytest tests/scripts -q
rtk uv run pytest --collect-only -q -o addopts=''
rtk uv run pytest -q --junitxml=reports/fast-tests.xml
rtk uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml
rtk ./scripts/git-quality-gate.sh --profile quality
rtk uv run alembic upgrade head
```

插件的准确验证命令由对应插件计划和包内 `pyproject.toml` 固定；核心验收不得代替任何插件验收。

**Exit gate:** SPEC §15 全部验收标准通过；测试收敛计划 Task 7 全部完成；不存在旧架构、
旧迁移、兼容路径、核心插件测试污染或未验收插件；最终结果可以合并回 `develop`。

## 12. 阶段变更规则

出现以下情况时必须先修改并重新批准 SPEC 或本文，不能由阶段实施者自行扩张：

- 新增 WES 权威业务数据、库存或单据职责。
- 新增协议类型、通用工作流、动态插件发现、Manifest 或 Service Locator。
- 为保留开发数据提出兼容 schema、迁移、双写或旧路径 fallback。
- 把具体工作线行为移入核心，或让核心测试运行具体插件测试。
- 改变九阶段依赖顺序、跨过阶段退出门禁或把中间态合并回 `develop`。

以下发现只需在对应子计划内处理，不改变总控结构：

- 最终对象的准确文件拆分、Repository 方法和数据库约束。
- WMS 垂直能力模块的准确文件名，但不得退回中心 registry 或 generic `call`。
- 厂家实际命令、事件、Payload 和错误码。
- 现场设备实例、Endpoint、角色绑定、位置容量和物理拓扑。
- 每个插件包的准确 `plugin_key`、配置和独立测试入口。

## 13. 总体完成定义

只有同时满足以下条件，本计划才完成：

1. WMS 薄接入层、WES 最小平台和所有已纳入范围的真实业务插件分别拥有单一职责和独立验收。
2. 核心可靠性不变量全部由最终对象测试证明，具体业务只由对应插件包测试证明。
3. 旧生产架构、旧测试所有者、旧配置、兼容路径和旧 migration chain 全部归零。
4. 最终数据库可以从空库一次建立，不需要旧数据、旧 revision 或转换脚本。
5. 核心与插件测试入口彼此独立，FAST、QUALITY、HEAVY 和插件验收均通过。
6. 当前态文档、active TODO、代码、测试、schema 和部署配置共同指向同一个最终架构。
