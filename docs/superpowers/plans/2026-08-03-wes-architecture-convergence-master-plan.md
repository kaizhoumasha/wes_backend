# WES 最小执行架构十阶段收敛总控实施计划

> **For agentic workers:** 本文只控制阶段顺序、职责边界、原子交接和退出门禁。每个阶段开始前必须另有经批准的详细实施计划；不得直接把本文当作代码实施脚本，也不得在阶段门禁未通过时启动下一阶段。

**Goal:** 按十个单向依赖阶段，将当前 WES 直接收敛到 SPEC 定义的最小执行架构；新增独立的 Outbound HTTP
传输基础能力阶段，并以独立 Adapter/业务插件、单一数据库基线和完整系统验收结束收敛。

**Architecture:** Composition Root 为每个外部系统装配一个进程内明确生命周期的
`OutboundHttpTransport`；WMS/RCS/ECS Adapter 拥有厂商 method/path、wire DTO、真实合同要求的认证协议和业务结果解释；
WES 核心只依赖类型化业务端口，可靠性生命周期分别由 `DeviceCommand`、`TransportTask` 和
`WmsConfirmation` 拥有。测试治理、直接旧所有者随替代随删除和最终数据库基线是贯穿主线。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL/TimescaleDB、Alembic、Celery、
Pydantic 2、HTTPX、Pytest 9、Ruff、Bandit、Import Linter、Jenkins。

**Status:** Reviewed — 十阶段结构、顶层 SPEC §14.2–14.3 和 Phase 2 详细计划已同步复审；Phase 2 已具备实施条件，
但生产代码尚未开始。Phase 3–10 仍须逐阶段满足各自入口条件和退出门禁。

**Requirements baseline:** `docs/architecture/SRS.md`

**Design baseline:** `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`

**Implementation baseline:** `develop@1a8bfcac`（2026-08-05 文档复审开始时的代码基线）

---

## 1. 全局硬约束

- 系统尚未发布；开发和测试数据可以清空，不保留旧版本、旧 API、旧字段、旧配置、旧数据或历史 revision 的迁移能力。
- SRS 定义产品需求，顶层 SPEC 定义目标架构，本文只编排实施顺序；历史实现、旧分支和未确认设想不得提升为需求。
- 严格遵守 DRY、KISS、SOLID、YAGNI；不建设通用工作流、动态插件发现、Manifest、Service Locator、
  运行时 registry、任意签名 DSL 或推测性集成平台。
- 最终运行态只能存在一条执行路径；禁止兼容 shim、alias、re-export、deprecated wrapper、双写、双读、
  旧路径 fallback 和按 WorkLine 切分的新旧双轨。
- 替代能力通过验收时必须在同一原子切换中删除直接旧所有者；阶段 8 只处理跨阶段残留，不能成为保留旧路径的理由。
- Phase 2 新基础层未接入生产调用时不构成对旧消费者的直接替代或同一调用双轨。直接替代分别发生在 Phase 3
  WMS QUERY/Provider/Transport owner 切换，Phase 4 旧 WMS 可靠生命周期闭包删除及核心 DeviceCommand/设备状态网络
  owner 改接 typed Device port，以及 Phase 6/7 真实 ECS/RCS Adapter 首次接入时；每次原子切换都必须删除该次已被替代的
  直接旧 owner。
- WMS 是业务单据、库存、主数据、业务授权和全局仓内位置权威；WES 只拥有工作线本地执行事实；
  ECS/PLC 拥有设备物理动作和安全互锁。
- 外部 HTTP 公共层只发送一次并返回传输事实，不拥有自动重试、业务拒绝、Circuit Breaker 决策、
  Outbox/`TransportTask`/`WmsConfirmation` 生命周期、工作线推进或厂商 Payload 映射。
- 每个外部系统在每个进程内只有一个明确生命周期的 Client；禁止每次请求创建 Client，也禁止全局万能 Client。
- builder 只完成 base URL、Timeout、连接池和 Transport 生命周期装配；业务 Adapter 只接收该 Transport，
  不接收裸 `httpx.AsyncClient`，也不管理连接池或通用传输异常。
- 当前已批准 outbound 合同均未要求认证，Phase 2 不建设认证策略、凭据解析、HMAC、Clock、Nonce 或预留接口。
  将来只有真实厂商 outbound 合同明确要求时，才先修订对应 Adapter 与总控计划，再在最窄所有者中实现。
- Inbound callback 认证保持独立 API 边界所有权，不得因同为 HTTP 而与 Phase 2 outbound 传输合并。
- 核心、Adapter、插件测试所有权严格隔离：核心验证基础能力与可靠性，Adapter 验证厂商合同与标准化映射，
  插件验证业务 Decision 和对象推进；不得跨层复制或互相替代。
- 具体 Adapter/插件包独立构建、测试和显式装配；客户镜像只安装明确清单，不建设运行时扫描或私有包 registry。
- 除已合入的 Phase 1 外，中间双轨态、未完成原子交接或未通过退出门禁的阶段不得合并回 `develop`。
- 每个阶段开始前必须有经批准的详细实施计划；计划必须冻结准确文件、接口、测试层级、验证命令和提交边界。

### 当前交付范围

本十阶段计划只交付顶层 SPEC §3.1 已确认的粗分机、自动分拣、人工分拣、满箱交换和复杂出库能力。
SRS §3.5 特殊物料、机构件/SFC 协同及 §3.6 生产退料属于未来需求，不进入 Phase 6/7 或 Phase 10 验收，
不得据此预建空插件、Adapter 或扩展平台。

## 2. 九阶段到十阶段的编号映射

| 原编号 | 原名称 | 新编号 | 新名称 | 裁决 |
| --- | --- | --- | --- | --- |
| Phase 1 | 测试治理基线 | Phase 1 | 测试治理基线 | 保持，已完成 |
| 无 | 无 | Phase 2 | 外部 Outbound HTTP 传输基础能力收敛 | 新增独立基础阶段 |
| Phase 2 | WMS 薄接入边界收敛 | Phase 3 | WMS 薄接入边界收敛 | 消费 Phase 2，不再拥有通用 HTTP Client 基础设施 |
| Phase 3 | WES 最小平台能力建设 | Phase 4 | WES 最小平台能力建设 | 接收 Phase 3 类型化端口，不重定义 WMS wire 合同 |
| Phase 4 | 核心测试承接与平台基线验收 | Phase 5 | 核心测试承接与平台基线验收 | 承接对象改为 Phase 4 最终对象 |
| Phase 5 | 粗分机参考插件优化 | Phase 6 | 粗分机参考插件优化 | ECS Adapter 消费 Phase 2 传输能力 |
| Phase 6 | 分拣业务插件组优化 | Phase 7 | 分拣业务插件组优化 | ECS/RCS Adapter 消费 Phase 2 传输能力 |
| Phase 7 | 旧平台代码最终闭环清理 | Phase 8 | 旧平台代码最终闭环清理 | 增加裸 httpx、重复传输和无依据认证缺席门禁 |
| Phase 8 | 旧数据模型与迁移链清理 | Phase 9 | 旧数据模型与迁移链清理 | 依赖 Phase 8 零旧路径 |
| Phase 9 | 最终基线与系统验收 | Phase 10 | 最终基线与系统验收 | 验收新增 Phase 2 基础层及职责所有权 |

## 3. 受编号与职责调整影响的文档和引用

| 文档或引用 | 影响 | 本轮处理 |
| --- | --- | --- |
| `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md` | 总控由九阶段改为十阶段 | 已重写并复审 |
| 旧 `2026-08-03-wes-wms-thin-access-convergence.md` | Phase 2 顺延为 Phase 3；旧稿含无合同依据的认证设计 | 完整移至项目外归档；Phase 3 在 Phase 2 完成后重新编写当前计划 |
| `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md` | 原 Phase 3–9 引用整体顺延 | 同步延后义务与测试承接阶段 |
| `docs/superpowers/README.md` | 当前文档索引仍称九阶段、WMS Phase 2 | 同步索引名称和状态 |
| `docs/architecture/file_index.md` | 文档索引仍称九阶段 | 同步为十阶段并标注 WMS Phase 3 |
| `README.md` | 当前架构入口仍称九阶段，WMS 计划未标注 Phase 3 | 作为必要索引同步为十阶段和 Phase 3 |
| `docs/architecture/SRS.md` §1.1、§3.1、§3.5、§3.6 | 四处以“九阶段”描述当前实现或验收范围 | 已机械同步为十阶段；业务范围不变 |
| `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` §14.2–14.3 | 含旧九阶段编号，且仍要求旧 Effect/status 不临时改写并把 Provider/Catalog 冻结到下一阶段 | 本轮已同步为十阶段及 Phase 3/4 原子交接语义；不改变业务架构 |
| `docs/plugin_development_guide.md` §2.2–2.3、§2.6 | Adapter 曾被概括为直接处理 HTTP/认证 | 已同步为消费已装配 Transport、只拥有真实厂商协议映射 |
| `docs/integration/workline_device_error_code_standardization.md` 开头、§3.1 | 仍以 Phase 3/7 指向最小平台和旧代码清理 | 本轮只读；对应新编号为 Phase 4/8，实施前同步引用，不改变设备错误语义 |
| `docs/devops/prod-release-deploy.md` 开头 | 仍以 Phase 8 指向最终 Alembic 基线，并记录临时 Provider Profile 部署输入 | 本轮只读；对应新编号为 Phase 9，Phase 3 切换 WMS 配置时同步 Runbook |
| `TODOS.md` | 无阶段编号；依赖最终对象的触发条件仍成立 | 无需修改，不新增重复调度项 |

上述“本轮只读”项不是新的实施入口。SPEC 继续定义目标业务边界；指南和 TODO 不得反向改变本计划的
Transport/Adapter/核心所有权。

## 4. 当前分支与实施状态

| 证据 | 当前事实 | 裁决 |
| --- | --- | --- |
| `git status` | `develop@1a8bfcac`，领先 `origin/develop@28eb99d9` 两个已提交文档变更；本轮开始时工作区干净 | 无未提交生产实施可继承 |
| `28eb99d9` / PR #100 | Phase 1 架构与测试治理已合入 | Phase 1 完成，但测试计划延后义务未整体完成 |
| `src/app/wms_integration/` | 仍为 54 个生产文件，Provider/Profile、Registry、QUERY、Effect/status/evidence 混合存在 | Phase 3 未开始；这是临时旧所有者，不是目标架构 |
| `src/app/sys/external_http_*` 与 `canonical_dispatch.py` | 已有 typed transport fact、凭据解析、NONE/HMAC、bounded response 的部分能力，但耦合 Provider Profile/SystemOutbox/WMS operation | 仅 transport fact 作为行为证据；认证相关能力无真实 outbound 合同依据，不进入 Phase 2 |
| 多处 `httpx.AsyncClient()` | DeviceCommand、旧 Outbox、WMS runtime、旧 Gateway 等仍自行创建 Client | Phase 2 只新增基础层；Phase 3 切换 WMS，Phase 4 将核心 DeviceCommand 发送/探测改接 typed Device port，Phase 6/7 接入真实 ECS/RCS Adapter，Phase 8 建立最终缺席门禁 |
| 目标对象扫描 | 尚无最终 `InboundEvidence`、`TransportTask`、`WmsConfirmation`、`LineRunEpoch` 等完整生产闭环 | Phase 4 未开始；旧 `DeviceCommand`/RuntimeProjection 不能等同于目标平台完成 |
| 远端文档分支 | `origin/codex/docs-wes-architecture-convergence-master-plan` 与当前 `develop` 树一致 | 无未合入规划增量 |
| 其他旧 feature 分支 | 大幅落后或已被 develop 取代，包含旧 Manifest/Runtime 语义 | 只作 Git 历史，不作为实施输入 |

阶段状态：Phase 1 已完成；Phase 2 计划已批准、生产实施尚未开始；Phase 3–10 均未开始。

## 5. 总控依赖模型

```text
Phase 1  测试治理基线（已完成）
   ↓
Phase 2  外部 Outbound HTTP 传输基础能力
   ↓
Phase 3  WMS 薄接入边界
   ↓
Phase 4  WES 最小平台能力
   ↓
Phase 5  核心测试承接与平台基线
   ↓
Phase 6  粗分机参考插件
   ↓
Phase 7  分拣业务插件组
   ↓
Phase 8  旧平台代码最终闭环
   ↓
Phase 9  旧数据模型与迁移链
   ↓
Phase 10 最终基线与系统验收
```

允许提前编写和评审下一阶段详细计划，但上一阶段退出门禁未通过时不得启动下一阶段生产代码实施。

### Phase 2 与 Phase 3/4 职责边界

| 责任 | Phase 2：Outbound HTTP | Phase 3：WMS 薄接入 | Phase 4：最小平台 |
| --- | --- | --- | --- |
| 生产装配与调用切换 | 不接入真实 WMS/RCS/ECS Adapter；只交付可独立验收的 builder/Transport | 首次为 WMS 创建专属 Transport 并原子切换 WMS 调用 | 不直接装配 Phase 2；只消费 Phase 3 类型化端口 |
| Client 生命周期、连接池、Timeout、base URL | 拥有；每外部系统/每进程一个明确 Client | 只消费已装配 Transport | 不可见 |
| 单次请求发送、受限响应读取、传输异常分类、安全日志 | 拥有并返回传输事实 | 不复制，只解释 WMS 业务结果 | 不可见 |
| 自动重试、依赖暂停与恢复 | 不拥有 | 不拥有；只返回类型化结果 | 由具体可靠对象按端口结果决定 |
| WMS Circuit Breaker permit 与状态更新 | 不拥有 | 围绕一次公开 WMS 调用唯一拥有；分页共享一次 permit | 不重做 breaker 决策，只消费依赖结果并管理可靠生命周期 |
| WMS method/path、wire DTO、拒绝码 | 不拥有 | 唯一拥有 | 不得重定义 |
| WMS canonical string、Header、签名版本 | 不拥有、不预留 | 只有真实 outbound 合同要求时才由 WMS Adapter 拥有 | 不可见 |
| `WMS → WES` callback ingress 认证 | 不拥有；Phase 2 仅服务 outbound | callback API 消费独立 inbound policy；可信隔离内网例外有明确合同，其他请求沿用既有 API Application/HMAC fail closed | 只消费已认证、归一化的 inbound fact |
| E08–E14/E16 单次 submit/status/cancel | 不拥有业务方法 | 类型化、无状态 Client 唯一拥有 | 通过 Transport Port 消费 |
| `TransportTask` 持久化、领取、轮询、重试、终态、恢复 | 不拥有 | 不拥有 | 唯一拥有 |
| `WmsConfirmation` 生命周期 | 不拥有 | 只提供无状态 sender | 唯一拥有 |
| 对象/位置投影和插件推进 | 不拥有 | 不拥有 | 唯一拥有通用机制 |

### Outbound HTTP 认证裁决

| 所有者 | 当前必须拥有 | 明确不得拥有 |
| --- | --- | --- |
| Composition Root | 选择具体 Adapter、装配 base URL/Timeout 并管理 Transport 生命周期 | 运行时扫描、Service Locator、推测性认证配置 |
| Phase 2 基础层 | 无认证职责 | `AuthStrategy`、凭据解析、HMAC、Clock、Nonce、认证枚举或未来扩展 seam |
| WMS Adapter 包（Phase 3） | 当前 outbound 无认证；只实现已冻结 method/path/DTO/结果映射 | 无合同依据的 canonical/Header/签名、裸 Client、连接池、通用传输异常 |
| ECS/RCS Adapter（Phase 6/7） | 只实现厂商原始资料已明确要求的认证；首次需求必须先修订计划 | 提前预建 BASIC/HMAC、从插件读取凭据、通用认证平台 |
| 部署配置 | 当前不提供 outbound 认证键 | 原始 Secret、任意 Header/签名表达式、未获合同支持的认证选项 |
| WES 核心可靠对象 | 只观察类型化端口结果并管理可靠性生命周期 | httpx、认证方案、签名 Header、credential reference |
| 业务插件 | 无认证职责 | HTTP Client、认证配置、凭据、厂商原始协议 |

## 6. Phase 1：测试治理基线

**Objective:** 冻结目标 SPEC、实施基线、核心/Adapter/插件测试所有权与 FAST/QUALITY/HEAVY 重量边界。

**Authoritative inputs:** 顶层 SPEC、`docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`、
`tests/README.md`、`docs/architecture/heavy-test-impact.toml`。

**Entry conditions:** 已满足；基线工作从当时最新 `develop` 执行，未启动生产内核重构。

**Scope:** 已完成测试拓扑、预算、核心/插件所有权门禁和显式旧插件测试清理；冻结 Task 4/5/7 的延后承接义务。

**Explicit out-of-scope:** 不实现 Outbound HTTP、WMS 薄边界、最终执行对象、真实 Adapter 或业务插件。

**Deliverables:** PR #100 / `28eb99d9`；核心 FAST/QUALITY/HEAVY 治理；测试处置四分类；延后义务清单。

**旧所有者删除或交接清单:** 已删除 `tests/workline_plugins/` 和可独立判定的插件/旧平台测试；五个混合 WMS/入站资产
保留到 Phase 5，在最终对象权威测试通过后处置。

**测试所有权与重量要求:** Phase 1 的治理门禁继续生效；“阶段完成”不代表测试收敛计划整体完成。

**与前后阶段的 atomic handoff:** 向 Phase 2 交付轻量测试规则；向 Phase 5 交付 Task 4/5/7 延后义务。

**Exit gate:** 已通过；完成项和延后项在测试计划、本文及当前 Git 历史中一致。

**需要单独编写的子计划:** 已存在并继续有效：`2026-07-31-wes-test-semantics-and-weight-convergence.md`。

**风险及防止阶段越权的约束:** 不把 Phase 1 测试清理解释为生产架构已收敛；不得提前删除仍承载通用可靠性的混合测试。

## 7. Phase 2：外部 Outbound HTTP 传输基础能力收敛

**Objective:** 在 `src/core/outbound_http/` 全新增量交付 WES 调用 WMS、RCS、ECS 等外部系统共用的 HTTP 传输能力，
形成可独立验收、可由后续具体 Adapter 消费且不含厂商业务语义的单次发送基础层；本阶段不切换任何既有生产调用。

**Authoritative inputs:** 顶层 SPEC 的外部系统边界、本文 §1/§5 的传输与认证裁决、当前
`src/app/sys/external_http_*`、`src/app/wms_integration/services/http_transport.py`、
`src/core/bounded_http_response.py` 及散落 Client 调用点的真实代码。除已稳定的公共 primitive 可被直接 import 外，
这些旧模块在 Phase 2 只作为行为证据和后续交接输入，不是本阶段修改或删除对象。

**Entry conditions:** Phase 1 已完成；Phase 2 详细计划经评审批准；先形成 `ADD/REUSE/HANDOFF` 矩阵，冻结新包、
轻量测试、架构门禁和机器可读 HEAVY mapping 的精确文件集。`src/core/bounded_http_response.py` 以不修改、不搬迁、
不 re-export 的直接 import 方式复用；耦合旧平台的异常分类和安全日志只作为语义证据，不复制源码或旧业务身份。

**Scope:** 新建 `src/core/outbound_http/`，生产文件严格限定为不做兼容 re-export 的 `__init__.py`、`contracts.py`、
`transport.py` 和 `factory.py`；交付框架无关的 request/result/错误合同、`OutboundHttpTransport`、接收 `system_id`、
base URL 与 Timeout 的简单 builder、单次
request/response 传输事实、每系统每进程 Client 生命周期、连接池与 Timeout/base URL、受限响应读取、通用传输异常分类
和脱敏日志。只复用现有 `src/core/bounded_http_response.py`，不新增认证或测试替身生产模块。

**Explicit out-of-scope:** 自动重试、业务拒绝映射、Circuit Breaker 决策、WMS/RCS/ECS method/path/wire DTO、
任何 outbound 认证/凭据/签名/Clock/Nonce、Outbox/`TransportTask`/`WmsConfirmation` 生命周期、工作线推进、
动态拦截器注册表、运行时排序、Service Locator 和通用集成框架；既有 WMS/RCS/ECS Adapter、Composition Root、
旧 Outbox/Effect/status/DeviceCommand 调用链的修改、接入或删除同样不属于本阶段。

**Deliverables:** 固定请求/响应流水线；Transport/builder/配置与生命周期合同；脱敏日志；
`tests/core/outbound_http/` 下只依赖 `httpx.MockTransport` 或测试内 local fake 的轻量测试；证明生产包不导出测试替身、
不包含认证 seam 的架构门禁；供 Phase 3/6/7 消费的公开合同和旧消费者 HANDOFF 清单。

**旧所有者删除或交接清单:** Phase 2 不修改或删除 `sys/external_http_*`、WMS transport、旧 Outbox sender、
DeviceCommand Client 及其生产调用者，只登记当前 owner、直接 importer 和目标切换阶段。WMS
QUERY/Provider/Transport owner 由 Phase 3 切换并删除；Phase 3 临时保留的 WMS Effect/status/Outbox 持久化、claim、
重试、fencing 和终态可靠生命周期闭包由 Phase 4 最终对象切换并删除；
`src/app/device/services/device_command_service.py`、`src/app/runtime/orchestration/services/device_command_gateway.py`、
`src/app/runtime/orchestration/services/inbox/outbox_dispatch_service.py`、
`src/app/runtime/capabilities/material_flow/start_admission_service.py` 和
`src/app/runtime/capabilities/material_flow/smt_inbound_handoff_route_service.py` 的核心 DeviceCommand HTTP 发送、实时状态及
准入探测分支由 Phase 4 改接 typed Device command/status/admission port 并删除裸 Client；SMT handoff 业务规则可在无网络
职责的前提下保留至 Phase 6 插件替代，但不得继续 import gateway 私有 helper。真实 ECS/RCS Adapter 由 Phase 6/7 首次
接入 Phase 2 Transport，同时删除对应厂商重复 Client/传输实现。Phase 4 还必须删除旧
`SystemOutboxDispatchType.DEVICE_COMMAND` 可靠性 owner：`src/app/sys/models/outbox.py`、
`src/app/sys/repositories/outbox_repository.py`、`src/app/sys/services/outbox_engine.py`、
`src/app/runtime/orchestration/enums.py`、`src/app/runtime/orchestration/services/intent/operation_service.py`、
`src/app/workline/services/write_back_service.py` 及 `outbox_dispatch_service.py` 中仅服务该 dispatch type 的模型约束、索引、
创建、claim/队首互斥、wait/retry/fencing、dispatch、ACK 和投影分支，改由最终 `DeviceCommand` 对象唯一拥有。跨多个消费者
的共享旧 helper 由最后一个生产 importer 所在阶段删除，Phase 8 只验证无遗漏，不作为延迟删除的默认归宿。

**测试所有权与重量要求:** 只用 `httpx.MockTransport`、测试内 local fake 和纯单元测试；不访问真实外部系统，
不使用 sleep，不建立大规模 E2E。只验证框架无关合同、生命周期、请求装配、受限响应、传输事实分类、取消传播、
已知异常与意外异常边界及日志脱敏。厂商认证/Header/DTO、业务拒绝、重试/终态/恢复不进入本阶段测试。

**与前后阶段的 atomic handoff:** Phase 2 向 Phase 3/6/7 交付同一 Transport 合同，但不激活任何真实 Adapter 或生产
Composition Root；新包与尚未切换的旧消费者共存不构成同一调用双轨，因为生产请求仍只有原路径。Phase 3/6/7
分别在自身原子切换中首次装配专属 Transport，并同时删除对应裸 Client、重复连接池和通用异常处理。Phase 4 只接收
Phase 3 类型化 WMS/Transport 端口，不 import、配置或装配 Phase 2。

**Exit gate:** builder 只返回可直接使用的 Transport；公开合同不暴露 httpx 类型；公共层单次发送且无重试/业务解释；
不捕获未知编程异常，不存在认证、credential、HMAC、Clock、Nonce 或生产 fake；全部轻量测试和架构门禁通过；
`src/core/outbound_http/` 之外的既有生产文件无 Phase 2 实施修改，WMS/RCS/ECS 生产模块和 Composition Root 零新包
import；`src/core/bounded_http_response.py` 内容与路径保持不变；Phase 3 可在不复制 transport 语义的前提下开始。

**需要单独编写的子计划:** 已新建并批准
`docs/superpowers/plans/2026-08-04-wes-outbound-http-transport-convergence.md`；实施必须逐项遵循该计划。

**风险及防止阶段越权的约束:** 最大风险是把既有 SystemOutbox/Provider Profile 整体提升为公共层、提前修改真实
Adapter/Composition Root，或建设认证/拦截器扩展平台。子计划必须以“新包单一实现、稳定 primitive 只读复用、
旧消费者精确 HANDOFF”为准；不得搬迁旧模块、复制旧源码、继承旧生命周期或提前执行 Phase 3/6/7 的切换。

## 8. Phase 3：WMS 薄接入边界收敛

**Objective:** 消费 Phase 2 Transport，把 35 项 WMS 合同收敛为类型化查询、无状态确认发送和无状态 WMS 转发
搬运 Client，同时保持 Phase 4 接管前旧可靠链的唯一活动所有权。

**Authoritative inputs:** 顶层 SPEC §4.1/§5.3/§6.3、WMS 北向合同、Phase 2 交付合同、
待新建的 Phase 3 详细计划及当前 54 文件代码矩阵。

**Entry conditions:** Phase 2 退出门禁通过；顶层 SPEC §14.2–14.3 已同步为十阶段及 Phase 3/4 原子交接语义；
WMS 详细计划已按 Phase 3、无 outbound 认证现状和 Transport 输入重新批准。

**Scope:** Q01–Q19 垂直模块和 `WmsCapabilities`；E01–E07/E15 无状态 sender；E08–E14/E16 类型化 submit/status/cancel
Client；WMS method/path/wire DTO/稳定拒绝码和业务结果解释；
一次公开 WMS 调用的 breaker permit/状态更新；QUERY 原子切换、Provider Profile 删除和 Registry 收缩；调用证据
fail-closed 与远端结果未知语义；从 Provider Profile 解耦且不复用 outbound credential 的 callback ingress policy。

**Explicit out-of-scope:** 裸 `httpx.AsyncClient`、连接池/Client 生命周期、outbound 认证/凭据/签名、通用传输异常、
搬运任务持久化、轮询、重试、终态、恢复、对象投影和业务插件 Decision。

**Deliverables:** 三条类型化窄端口；35 个垂直合同模块；消费 Phase 2 Transport 的 WMS Adapter；无状态 fake；
独立 callback ingress policy；QUERY 生产路径；Phase 4 可靠所有者精确删除闭包。

**旧所有者删除或交接清单:** 删除 QUERY System Capability、旧 query runtime/registry、Provider Profile、
Provider readiness/startup/endpoint compiler、重复 WMS transport、旧 outbound 认证配置和 fallback。旧 Effect/status/Outbox
只保留持久化、claim、重试、fencing 和终态等可靠生命周期，并在 Phase 3 内原地改接 Phase 2 Transport 与三条
类型化 WMS 端口；fulfillment definitions 只保留 Phase 4 接管所需的冻结业务身份。Phase 4 在最终可靠对象通过后
原子删除该生命周期闭包。Phase 3 退出时不得存在旧 Provider Profile、第二条认证/传输路径或双轨发送。
`WmsInboundAuthPolicy` 必须在同一次切换中改接独立 inbound policy，不能成为 Provider Profile 的悬空 importer。
共享 `external_contract_profile.py`/catalog 必须同步剥离 WMS-specific profile、全局 WMS catalog 项和全部 WMS
生产调用；generic ECS/Device/AGV 入站合同只能作为后续阶段的临时旧所有者，不能继续承载 WMS 配置或准入。

**测试所有权与重量要求:** WMS Adapter 合同测试拥有 WMS DTO、method/path、业务结果映射、breaker permit 和无状态调用；
该测试在 WMS 自身测试树定义实现公开 `OutboundHttpTransport` 合同的最小 local fake，不得 import
`tests/core/outbound_http/` 的 Phase 2 测试内部资产，也不重复
测试 Client 生命周期和通用异常矩阵。`TransportTask` 重试/终态测试归 Phase 4。

**与前后阶段的 atomic handoff:** 从 Phase 2 只接收已装配 Transport；向 Phase 4 交付 E08–E14/E16 及确认 sender。
Phase 3 不保存第二份搬运生命周期；Phase 4 不重定义 WMS method/path/DTO/HTTP 错误映射。

**Exit gate:** 所有目标 WMS Adapter 均无裸 Client/outbound 认证/凭据解析/通用 transport 逻辑；QUERY 唯一路径已切换；
无状态 sender/client 有稳定合同；旧可靠链只保留生命周期并已原地改接唯一 Phase 2 Transport，Provider Profile、
旧 outbound 认证和旧传输路径全部缺席；callback ingress 独立且 fail closed；该唯一生命周期仍有 Phase 4 删除
门禁；`WmsExternalContractProfile`、全局 WMS External Contract Catalog 及其 WMS 生产调用全部缺席，generic
ECS/Device/AGV profile 不进入 WMS 路径；FAST/QUALITY/受影响 HEAVY 通过。

**需要单独编写的子计划:** Phase 2 完成后新建并批准
`docs/superpowers/plans/2026-08-05-wes-wms-thin-access-convergence.md`；不得复制项目外旧稿的失效认证设计。

**风险及防止阶段越权的约束:** 禁止 WMS 层承载 `TransportTask`/`WmsConfirmation`；禁止以 WMS happy path 证明 Phase 2；
禁止在无真实 outbound 合同依据时实现或预留 WMS 认证。

## 9. Phase 4：WES 最小平台能力建设

**Objective:** 在不实现真实工作线业务规则的前提下，交付最终执行对象、三类可靠性生命周期、通用 WorkLine、
投影及最小插件 SPI/SDK。

**Authoritative inputs:** 顶层 SPEC §6–§10、Phase 3 类型化 WMS/Transport 端口、设备命令合同、typed Device port 边界和
入站验证原则。

**Entry conditions:** Phase 3 退出门禁通过；所有需要核心消费的 WMS 查询、确认和搬运端口稳定；旧可靠链删除闭包冻结。

**Scope:** `WorkLine`、`LineRunEpoch`、`MaterialExecution`、`BinExecution`、位置/设备投影、`InboundEvidence`、
`DeviceCommand`、`TransportTask`、`WmsConfirmation`、封闭 Decision、显式依赖注入、可靠领取/轮询/重试/终态/恢复、
最小 fake 和 uv workspace 显式装配边界。

**Explicit out-of-scope:** httpx、认证方案、厂商签名、WMS method/path/DTO、具体厂商命令、真实工作线业务规则、
动态插件发现、Manifest、Service Locator 和自动物理恢复。

**Deliverables:** Fake 插件可运行的最小闭环；三类可靠对象及持久化约束；类型化 WMS/Device/Transport 端口消费；
投影与人工清线语义；最终核心生产路径。

**旧所有者删除或交接清单:** `TransportTask`/`WmsConfirmation` 权威测试通过后，原子删除 Phase 3 仅保留可靠职责的旧 WMS
Effect/status/Outbox/fulfillment/operation-registry 生命周期闭包；Provider Profile、旧认证和旧 Transport 已在 Phase 3
删除，不属于本阶段 handoff。每个最终对象交付时删除直接替代的 RuntimeInbox、Intent/Effect、Hold/Recovery/Reservation
旧所有者，不推迟到 Phase 8。DeviceCommand 闭包必须把 `device_command_service.py` 的直接发送、
`device_command_gateway.py` 的派发/实时状态探测、`outbox_dispatch_service.py` 的 blocked-resource ECS probe、
`start_admission_service.py` 的设备状态拉取和 `smt_inbound_handoff_route_service.py` 的 source-pick 状态探测改接同一组 typed
Device command/status/admission port，删除五个核心路径中的 `httpx` import、每请求 Client、厂商 URL/Header/ACK 解释、
直接网络异常处理，以及 SMT 路径对 gateway 私有实时探测 helper 的 import。SMT handoff 的业务判断可留至 Phase 6 由
粗分机插件替代，但 Phase 4 结束后只允许消费 typed port，不再拥有网络或厂商协议职责。
Phase 4 只交付 DeviceCommand 可靠生命周期、typed Device port 及核心 fake，不装配生产 fake，也不实现 ECS/RCS wire；
需要真实设备通信的部署必须等待 Phase 6/7 Adapter 接入，不得保留旧 HTTP sender 作为 fallback。WMS Outbox 闭包必须
包含两个 `tasks/sys.py` WMS dispatcher、Celery beat/route、
`outbox_dispatch_composition.py` 的 WMS scopes、`task_queue_gateway.py` 的 WMS targets/status enqueue，以及
`outbox_engine.py`、`models/outbox.py`、`canonical_dispatch.py`、旧 effect reducer 和 WMS capability effect runtime 的
WMS 专属分支；Phase 3 typed status hint 同时改接 `TransportTask` 唯一的 hint application port，并删除旧
`wms_typed_effect_callback_router` 到 status service 的调用。完整入站链 `runtime_inbox_orchestrator_bridge` →
`wms_runtime_inbox_handler` → router 及其 `services/inbox/__init__.py` re-export、callback writer 的 WMS 分支同步由
`InboundEvidence`/`TransportTask` successor 接管并删除；上游 `callback_orchestration_service.process_wms_event`、
`process_external` 的 WMS hint 分支和 writer 的两项 WMS 写入也必须在同一原子切换中改为只写 `InboundEvidence`。
同一切换必须把 `callback_ingress_service.py` 的 WMS event/hint 错误处理改接最终 application port 的封闭 typed outcome：
duplicate 保持成功 ACK，conflict/payload-too-large/correlation-unavailable/input-rejected 分别映射既定的
409/413/503/400；WMS 分支不得继续捕获旧 RuntimeInbox 异常或增加兼容异常转换。非 WMS callback 的旧异常合同留给
其对应阶段。
WorkLine sandbox external-callback 的 WMS 分支不得绕过该入口写 RuntimeInbox：删除其 WMS source/callback 支持，
并从 `SandboxExternalCallbackRequest`/OpenAPI 删除 WMS 默认值和允许项；Phase 4 验收只使用 Phase 3 typed fake 与
最终可靠对象 fixture。
同一 Phase 4 原子切换必须删除 `SystemOutboxDispatchType.DEVICE_COMMAND` 及其全部旧可靠性分支：
`models/outbox.py` 的枚举、resource-wait 判定和专属索引，`outbox_repository.py` 的 DeviceCommand claim、物理设备队首互斥、
blocked-resource、wait/retry/fencing 与恢复查询，`outbox_engine.py` 的 DeviceCommand dispatcher，
`runtime/orchestration/enums.py` 的旧 Outbox dispatch type，`operation_service.py` 的 Outbox 命令状态/ACK 校验，
`write_back_service.py` 和 `device_command_service.py` 的 DeviceCommand SystemOutbox 创建，
`device_command_gateway.py` 的旧 dispatch type 校验，以及 `outbox_dispatch_service.py` 的 dispatch/sandbox/观测分支。最终
`DeviceCommand` 对象直接拥有持久化、claim、设备互斥、重试、ACK/CALLBACK、终态、恢复和投影，禁止用 typed port 包裹
旧 SystemOutbox 生命周期。只有确实不属于 WMS 或 DeviceCommand 的通用 SystemOutbox/RuntimeInbox 才可留给
Phase 6/7/8。

**测试所有权与重量要求:** 核心测试验证持久化、幂等、ACK/CALLBACK、claim/fencing、重试、终态、恢复和投影；
使用最小 fake，不复制 WMS/ECS 厂商合同和具体工作线 happy path。`test_device_command_service_contract.py`、
`test_device_command_gateway.py`、`test_outbox_dispatch_async_guard.py` 以及 Phase 4 详细计划冻结的 start-admission/SMT
handoff 核心承接测试，将绑定裸 `httpx.AsyncClient`、gateway 私有 helper、厂商 URL/ACK 或实时 HTTP probe 的断言改为
typed Device port 调用、错误传播和零核心网络副作用。`tests/api/test_workline_runtime_sse.py` 只保留 typed Device port ACK
后提交 `command.status.changed` 状态事件及 SSE API 行为，并改用 typed Device port fake；HTTP GET/POST、URL、状态
payload 和 ACK wire 断言只在 Phase 6/7 Adapter 包重建。先在最终 `DeviceCommand` 上建立持久化、claim、设备互斥、
resource wait、重试、fencing、ACK/CALLBACK 和终态的权威测试，再删除
`tests/sys/test_system_outbox_engine.py`、`tests/sys/test_system_outbox_dispatch_concurrency_contract.py`、
`tests/workline_runtime/test_system_outbox_resource_wait_contract.py`、
`tests/workline_runtime/test_dispatch_attempt_lease_fencing.py` 和
`tests/contracts/system_capabilities/test_canonical_external_http_dispatch.py` 中仅验证
`SystemOutboxDispatchType.DEVICE_COMMAND` 的旧 owner 用例；仍适用于其他 dispatch type 的用例不得随之误删。每项删除都
必须在 Phase 4 详细计划记录最终 DeviceCommand 目标测试路径；无最终语义的旧 DispatchEnvelope/schema 断言标记 `NONE`。
`tests/api/test_qa_regression_002.py`、`tests/api/test_workline_runtime_sse.py`、
`tests/integration/test_system_outbox_repository.py`、`tests/integration/test_system_outbox_dispatch_concurrency.py` 和
`tests/resilience/test_runtime_scenario_replay.py` 中直接构造或断言 DeviceCommand SystemOutbox 的用例也必须在 Phase 4 同步
完成 successor/NONE 处置，不能等待 Phase 5；integration/resilience 变更同时精确更新
`docs/architecture/heavy-test-impact.toml` 并显式运行受影响 HEAVY。相同义务还覆盖
`tests/sys/test_system_outbox_engine_boundaries.py` 对 `_dispatch_device_command` 的直接 import，
`tests/workline_runtime/test_runtime_reconciliation_idempotency.py` 和
`tests/workline_runtime/test_workline_runtime_status_projection_service.py` 的 ACK-exhausted
Reconciliation/SystemOutbox 路径，`tests/workline_runtime/system_capabilities/test_system_capability_effect_service.py` 的
`DeviceCommand + SystemOutbox` 双创建断言，以及 `tests/workline_runtime/test_runtime_intent_effect_applier.py` 的
`device-command:` Intent/Effect/Outbox 路径。
`tests/workline_runtime/system_capabilities/test_device_command_authoritative_precondition.py` 的
`prepare_runtime_effect()` 三对象写入合同、
`tests/runtime/orchestration/test_command_result_correlation_authority.py` 对同一旧入口的 stub，以及
`tests/workline_runtime/test_external_http_workline_dispatcher.py` 对
`_mark_device_command_failed_if_dispatch_exhausted`、`_dispatch_blocked_resource_heads`、
`_repair_orphaned_device_busy_dispatches` 和 `_repair_self_blocked_device_busy_dispatches` 四个旧 DeviceCommand
SystemOutbox helper 的直接 patch，也必须在 Phase 4 改接最终 `DeviceCommand` successor 或按 `NONE` 删除旧耦合。

**与前后阶段的 atomic handoff:** 原子接收 Phase 3 无状态端口并删除旧可靠链；向 Phase 5 交付最终对象与唯一生产路径，
使混合旧测试可以安全处置；同时冻结 typed Device port，供 Phase 6/7 真实 ECS/RCS Adapter 接入，Phase 4 不直接依赖
Phase 2 Transport。

**Exit gate:** 最小 fake 可驱动 Input → Evidence → Decision → Command/Transport/Confirmation → Result → Projection；
核心对象无 httpx/认证/厂商协议；旧 WMS 可靠闭包以及被最终对象直接替代的 RuntimeInbox、Intent/Effect、
Hold/Recovery/Reservation 等生产所有者全部缺席；`dispatch_wms_*_outbox_batch`、WMS Outbox claim scope/target、
`enqueue_wms_effect_status`、旧 callback router/status service 调用和 SystemOutbox WMS 专属校验/发送/恢复分支零命中；
`device_command_service.py`、`device_command_gateway.py`、`outbox_dispatch_service.py`、`start_admission_service.py` 和
`smt_inbound_handoff_route_service.py` 的核心 DeviceCommand/设备状态/准入路径零 `httpx`、裸 Client、gateway 私有网络
helper 或厂商 wire 解释，只调用 typed Device port，且生产 Composition Root 不绑定 fake 或旧 sender；
全仓生产代码零 `SystemOutboxDispatchType.DEVICE_COMMAND`、零字符串 `DEVICE_COMMAND` Outbox dispatch type、零 DeviceCommand
SystemOutbox 专属索引、claim/wait/retry/fencing/dispatch/ACK/投影分支；对应核心可靠性只由最终 `DeviceCommand` 对象及其
权威测试拥有；精确 owner 扫描必须覆盖 `device_command_service.py` 的旧 Outbox 创建和
`device_command_gateway.py` 的旧 dispatch type 校验；全部直接引用被删除枚举/模型/分支的 FAST/HEAVY 测试已在 Phase 4
完成 successor/NONE 处置并可收集；测试 import/语义缺席扫描同时覆盖 `_dispatch_device_command`、
`SystemOutboxDispatchType.DEVICE_COMMAND`、`device-command:` dispatch key、DeviceCommand SystemOutbox 双创建，以及
ACK-exhausted Reconciliation/SystemOutbox 路径，还必须覆盖旧 `prepare_runtime_effect()` 三对象写入合同和
`_mark_device_command_failed_if_dispatch_exhausted`、`_dispatch_blocked_resource_heads`、
`_repair_orphaned_device_busy_dispatches`、`_repair_self_blocked_device_busy_dispatches` helper，不能只扫描
`DEVICE_COMMAND` 字面量；
API callback 到 writer/consumer 的 WMS 路径只持久化 `InboundEvidence`，零 WMS RuntimeInbox 写入或无人消费 ACK；
WMS callback ingress 只映射最终 typed outcome，API FAST 覆盖 duplicate/400/409/413/503，零旧 RuntimeInbox 异常 import、
catch 或兼容转换；
WorkLine sandbox route 同样零 WMS RuntimeInbox/SystemOutbox fallback 或无条件 WMS enqueue；
Phase 5 只接收最终生产路径和待承接的旧测试资产。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-minimal-platform-capabilities.md`，同步其阶段号为 Phase 4。

**风险及防止阶段越权的约束:** 禁止重建通用 Runtime/Effect 平台；禁止核心读取 Secret 或 wire 协议；
禁止用兼容桥延长旧所有者寿命。

## 10. Phase 5：核心测试承接与平台基线验收

**Objective:** 将跨插件通用可靠性和 WorkLine 语义完全承接到 Phase 4 最终对象，并证明无真实插件也可独立验收平台。

**Authoritative inputs:** 测试收敛计划 Task 4/5/7、Phase 4 最终对象、`tests/README.md` 和 HEAVY selector 真源。

**Entry conditions:** Phase 4 全部最终生产路径和权威基础测试通过；旧对象 successor 映射明确。

**Scope:** 完成五个混合资产及其他不直接阻塞 Phase 4 测试收集的剩余旧测试处置；审计 Phase 4 已建立的最终核心唯一测试；
扩展核心/Adapter/插件所有权门禁；运行 FAST/QUALITY/受影响 HEAVY。

**Explicit out-of-scope:** 具体工作线业务闭环、厂商 canonical/Header/DTO 和业务插件验收。

**Deliverables:** 平台核心基线、最终对象测试矩阵、旧测试 successor/NONE 审计、核心测试缺席门禁。

**旧所有者删除或交接清单:** 直接引用 Phase 4 被删除生产符号、枚举或数据库约束的测试，以及全部 DeviceCommand
SystemOutbox 测试 owner，必须已随 Phase 4 原子切换完成 successor/NONE 处置，本阶段不得接收。Phase 5 只处置仍可在
最终生产对象上收集、但继续混合验证 RuntimeInbox、Intent/Effect、Capability、Manifest、Hold、Recovery、Reservation
语义的旧测试及五个混合测试资产；最终对象 successor 测试先通过再删除旧断言。不得为测试承接恢复 Phase 4 生产 owner；
插件/Adapter 专属行为分别交给 Phase 6/7 包内重建。

**测试所有权与重量要求:** 核心 FAST 不访问真实数据库/HTTP/Celery；必要持久化与进程测试进入精确 HEAVY；
不以厂商或工作线 E2E 证明核心。

**与前后阶段的 atomic handoff:** 接收 Phase 4 唯一生产对象；向 Phase 6/7 交付稳定 SDK/门禁和明确的插件/Adapter 测试所有权。

**Exit gate:** 测试计划 Task 4/5 核心承接完成；核心测试无具体插件/厂商行为；最小 fake 通过平台基线；结果只称“平台核心基线”。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-platform-baseline-acceptance.md`，同步其阶段号为 Phase 5。

**风险及防止阶段越权的约束:** 禁止为绿灯删除未承接不变量；禁止把插件测试改名搬入核心 contracts/runtime/HEAVY。

## 11. Phase 6：粗分机参考插件优化

**Objective:** 以粗分机交付首个真实业务插件和现场所需 ECS Adapter，验证 Phase 2/4 平台边界足以支持真实闭环。

**Authoritative inputs:** 顶层 SPEC §11.1、粗分机真实拓扑、厂家合同、WMS 操作清单、Phase 2 Transport、Phase 4 SDK。

**Entry conditions:** Phase 5 平台核心基线通过；粗分机厂商资料完整；Adapter 允许的认证闭集有真实合同依据。

**Scope:** 独立 Adapter/插件包；事件/命令标准化；粗分业务 Decision；设备长命令、NG、WMS 查询/确认和搬运端口消费。

**Explicit out-of-scope:** 其他分拣线、公共 HTTP Client、凭据、通用认证配置、WMS wire 合同重定义、通用插件模板。

**Deliverables:** 可独立构建/测试的粗分机 Adapter 与插件；显式 composition root 绑定；真实业务验收结果。

**旧所有者删除或交接清单:** Adapter 交付时删除核心/插件中的对应厂商 DTO、HTTP Client、HMAC 工具和映射副本；
插件交付时删除旧粗分业务代码、配置和测试，不保留 alias/fallback。

**测试所有权与重量要求:** Adapter 包拥有厂商 canonical/Header/DTO/错误映射/合同；插件包拥有业务 Decision 和对象推进；
Phase 2/核心测试不得复制这些场景，两个包测试均不进入核心默认 pytest。

**与前后阶段的 atomic handoff:** 消费 Phase 2 Transport 与 Phase 4 SDK；发现公共能力缺口时只有两个以上已确认消费者成立
才可修订平台，否则规则留在粗分插件。

**Exit gate:** 两个包独立安装、构建、测试；业务闭环仅由插件拥有；厂商协议仅由 Adapter 拥有；核心无特殊分支。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md`，同步其阶段号为 Phase 6。

**风险及防止阶段越权的约束:** 禁止插件访问 Transport/HTTP/认证/凭据；禁止首次真实 BASIC 需求未经计划修订直接实现。

## 12. Phase 7：分拣业务插件组优化

**Objective:** 按真实工作线和厂家合同分别交付自动分拣、人工分拣、满箱交换和复杂出库，不建设通用分拣工作流。

**Authoritative inputs:** 顶层 SPEC §11.2–§12、每条线真实拓扑与厂家合同、Phase 6 复审结果、Phase 2/4 稳定接口。

**Entry conditions:** Phase 6 退出门禁通过；每个实际插件/Adapter 的范围、厂家合同和部署组合明确；各详细计划获批。

**Scope:** 自动/人工/满箱交换/复杂出库业务插件；所需 ECS/RCS Adapter；同线进出、NG 透传、即时 PUT、CTU 批次和 WMS 来源权威。

**Explicit out-of-scope:** `SorterCorridor`、库存权威、动态发现、统一厂商认证三选一、公共 HTTP Client、预建 BASIC、通用工作流 DSL。

**Deliverables:** 每个真实 Adapter/插件独立包、fixture、测试、构建产物和显式装配；客户镜像清单。

**旧所有者删除或交接清单:** 每个 Adapter 交付时删除对应裸 Client、重复连接池/HMAC/协议映射；每个插件交付时删除旧业务代码、
配置和测试；删除包时同步移除 workspace、镜像和 composition root 绑定。

**测试所有权与重量要求:** Adapter 独立拥有厂商合同/集成/E2E/韧性；插件独立拥有业务单元/集成/E2E/韧性/并发/负载；
核心只验证通用机制，Phase 2 只验证 transport。

**与前后阶段的 atomic handoff:** 消费已稳定的 Phase 2 Transport 和 Phase 4 SDK；全部实际交付包完成后向 Phase 8 提交零散旧所有者清单。

**Exit gate:** 两条自动线和两条人工线使用同一插件的不同配置实例；全部包独立通过；核心无插件 import/fixture/名称分支；
无动态平台扩张。

**需要单独编写的子计划:** 分别编写并批准：
`docs/superpowers/plans/2026-08-03-automatic-sorter-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-manual-sorter-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-full-bin-exchange-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-complex-outbound-plugin-convergence.md`。若真实现场证明应合并为同一部署插件，
先修订本阶段清单，不预建空包。

**风险及防止阶段越权的约束:** 禁止从粗分计划复制改名；禁止把首个 BASIC 需求当作全局通用选项，必须先修订对应 Adapter 计划和认证闭集。

## 13. Phase 8：旧平台代码最终闭环清理

**Objective:** 在 Phase 2 已交付唯一新基础层、Phase 3–7 已随替代随删除的基础上清除跨阶段残留，证明生产态只有
最小执行架构和唯一 HTTP 基础层。

**Authoritative inputs:** 各阶段删除/交接清单、GitNexus 变更影响、全仓 import/语义扫描、当前装配与部署配置。

**Entry conditions:** 当前范围内全部最终 Adapter/插件交付；任何仍活动的旧所有者都有明确 successor，不存在未完成原子交接。

**Scope:** 清除 Runtime/System Capability/Manifest/Intent/Effect/Hold/Recovery/Reconciliation/Reservation；清除裸
httpx Client、重复连接池、无真实合同依据的认证配置和 transport fallback；建立 import/语义缺席门禁。

**Explicit out-of-scope:** 重新实现业务能力、保留 tombstone/转发文档、删除厂商原始资料、数据库 migration 基线重建。

**Deliverables:** 生产代码、配置、脚本、装配和当前态文档的零旧引用；证明只有 Phase 2 基础层可直接依赖 httpx，
必要测试适配除外。

**旧所有者删除或交接清单:** 删除所有跨阶段残留、旧配置键、Provider Profile、无依据认证 fallback/签名 helper、旧 Celery task/index；
项目内历史文档按归档规则移出，不保留副本或转发。

**测试所有权与重量要求:** 以架构/语义缺席门禁为主；不新增读取人类文档正文的 pytest；只运行受影响核心测试和精确 HEAVY。

**与前后阶段的 atomic handoff:** 接收 Phase 3–7 的删除余量并核验 Phase 2 新基础层的唯一性；全部零命中后才允许
Phase 9 固化最终 metadata。

**Exit gate:** 机器门禁证明旧架构、裸 Client、重复传输和无依据认证零引用；应用/Celery/部署只装配最终对象及明确 Adapter/插件。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-legacy-production-path-removal.md`，同步其阶段号为 Phase 8。

**风险及防止阶段越权的约束:** 缺席扫描按语义和所有者判断，不按 `replay`/`reconciliation` 等词批量删除，避免误伤最终可靠行为。

## 14. Phase 9：旧数据模型与迁移链清理

**Objective:** 最终模型稳定后删除未发布系统的旧 schema/revision，生成唯一可从空库建立系统的 Alembic 基线。

**Authoritative inputs:** Phase 8 零旧路径结果、最终 SQLModel metadata、Alembic 规则、TimescaleDB 必要对象。

**Entry conditions:** Phase 8 退出门禁通过；最终核心、Adapter/插件所需持久化模型稳定；无旧表活动消费者。

**Scope:** 删除旧表/字段/约束/索引和 revision chain；清空开发/测试数据库；使用 Alembic generator 生成随机 revision ID；空库验收。

**Explicit out-of-scope:** 旧数据转换、桥接表、临时回填、downgrade、兼容 schema 和生产历史数据迁移。

**Deliverables:** 单一干净初始基线；metadata/schema/约束/索引/扩展对象一致性结果。

**旧所有者删除或交接清单:** 删除 Runtime/Manifest/Capability/Intent/Effect/Hold/Recovery/Reservation 及旧认证/Provider 持久字段；
删除只验证旧 revision 的测试，标注 successor 或 NONE 理由。

**测试所有权与重量要求:** 只验证 migration 生成物、空库 upgrade 和 metadata 一致性；不保留旧 upgrade/downgrade/回填测试。

**与前后阶段的 atomic handoff:** 只接受 Phase 8 已证明无消费者的模型删除集；向 Phase 10 交付唯一空库基线。

**Exit gate:** `migrations/versions/` 只含最终初始基线及其后真实 revision；空库一次 upgrade head 成功；无旧迁移/兼容断言。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-schema-and-migration-baseline-reset.md`，同步其阶段号为 Phase 9。

**风险及防止阶段越权的约束:** 禁止在模型未稳定前生成基线；禁止因保留开发数据引入兼容迁移。

## 15. Phase 10：最终基线与系统验收

**Objective:** 从干净环境证明核心、Phase 2 HTTP 基础层、当前范围 Adapter/插件、数据库基线、部署装配和缺席门禁共同满足目标架构。

**Authoritative inputs:** SRS、顶层 SPEC、Phase 1–9 退出证据、当前 ADR/合同、插件指南、TODO 与运维文档。

**Entry conditions:** Phase 9 空库基线通过；全部当前范围 Adapter/插件独立验收；所有阶段文档和代码状态一致。

**Scope:** 空库 upgrade；核心 FAST/QUALITY/受影响 HEAVY；Phase 2 transport 合同与生命周期；每个 Adapter/插件独立入口；
部署与 composition root；旧架构、散落 httpx 和无依据认证缺席门禁；当前态文档一致性。

**Explicit out-of-scope:** 未来 SRS 需求、未确认厂商、未交付插件、推测性协议和旧版本兼容。

**Deliverables:** 可审计的最终验收报告、各包测试结果、空库基线结果、部署清单、缺席门禁结果和合并判定。

**旧所有者删除或交接清单:** 不接受新的遗留交接；发现任何旧路径、重复 HTTP 或无依据认证 owner、未验收包都必须退回其来源阶段修正。

**测试所有权与重量要求:** 核心、Phase 2、Adapter、插件分别运行自己的最低稳定层完整断言；真实/验收级 E2E 不回写核心；
不以单一全链路 happy path 替代分层合同与可靠性测试。

**与前后阶段的 atomic handoff:** 接收 Phase 1–9 完整证据；只有全部通过才允许最终结果合并 `develop`，无后续兼容阶段。

**Exit gate:** SPEC §15 全部验收通过；测试计划 Task 7 完成；只有 Phase 2 基础层直接依赖 httpx；当前 Adapter 不含
无真实合同依据的认证；无旧架构/迁移/兼容路径/核心插件污染；最终结果可合并。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-final-architecture-acceptance.md`，同步其阶段号为 Phase 10。

**风险及防止阶段越权的约束:** 禁止用最终验收临时实现缺失能力或放宽门禁；失败必须回到拥有该职责的阶段修正。

## 16. 发现的矛盾与最终裁决

| 矛盾或歧义 | 仓库证据 | 最终裁决 |
| --- | --- | --- |
| 旧总控把 WMS 作为 Phase 2 | Master/WMS 子计划旧编号 | 新 Phase 2 独立为公共 HTTP；WMS 顺延 Phase 3，后续顺延到 Phase 10 |
| “收敛”容易被理解为 Phase 2 直接搬迁或修改旧 HTTP 代码 | 旧计划曾要求 `KEEP/MOVE/DELETE/HANDOFF`、移动 WMS transport primitive 并同阶段删除通用 helper | Phase 2 固定在 `src/core/outbound_http/` 全新增量开发，只读复用稳定 primitive；Phase 3 删除 WMS QUERY/Provider/Transport 旧 owner；Phase 4 删除临时 WMS 可靠生命周期闭包，并将核心 DeviceCommand/状态/准入网络 owner 原子改接 typed Device port，但不 import 或装配 Phase 2；Phase 6/7 接入真实 ECS/RCS Adapter 并删除厂商重复实现 |
| 旧 WMS 子计划把推测性认证放入 Phase 2 | WMS Task 5–8、`canonical_dispatch.py` 与 `sign_wms_hmac_request`，但冻结 WMS outbound 合同无认证要求 | Phase 2 不实现 `AuthStrategy`、凭据、HMAC、Clock、Nonce 或认证 seam；WMS 旧草案标记 Needs re-review，将来只有真实合同明确要求时才修订计划 |
| 现有 `external_http_*` 看似公共但耦合旧平台 | import `operation_registry`、Provider Profile、SystemOutbox/`idempotency_key` | 只复用可证明的 primitive，不把旧 Provider/Outbox 设计提升为目标公共层 |
| 当前既有长期 WMS Client，也有多个每请求 Client | WMS query/effect lane 与 DeviceCommand/旧 Gateway/Outbox | 目标为每外部系统每进程一个 Client；分阶段原子切换，Phase 8 最终零散落 Client |
| 旧 Provider 配置可表达 NONE/HMAC，容易被误读为当前需求 | `provider_profile.py` 与 `external_http_binding.py` | 它们是后续原子切换时删除的旧 owner，不进入 Phase 2 公共合同或部署配置 |
| SPEC §14 曾是九阶段编号，且与 Phase 3/4 交接语义冲突 | 复审前 SPEC §14.2–14.3 | 已同步为十阶段：Phase 3 只机械替换旧链的传输和配置依赖，不改写其可靠生命周期；Phase 4 建立最终对象后删除旧闭包 |
| 当前存在旧 `DeviceCommand`/Projection 类型 | 生产代码扫描 | 不等于 Phase 4 最终平台已完成；只有完整对象、端口、生产闭环和删除门禁通过才算交付 |
| 旧远端 feature 分支包含大量历史实现 | 分支差异远大于当前 develop | 不合并、不 cherry-pick，不作为需求；只有当前 develop 真实代码和权威文档作为实施输入 |

## 17. 自审结果

| 检查项 | 结果 | 说明 |
| --- | --- | --- |
| 占位标记 | 通过 | 无未决类名、未定职责或“以后补”式任务；未来子计划均给出明确路径、前置批准条件和阶段 owner |
| 兼容设计 | 通过 | Phase 2 新包不接生产流量；WMS QUERY/Provider/Transport 旧路径只保留到 Phase 3，临时 WMS Effect/status/Outbox 可靠生命周期闭包及设备 HTTP/SystemOutbox 旧路径只保留到 Phase 4 最终对象/typed-port 切换；Phase 4 至 Phase 6/7 真实 Adapter 接入前无生产设备 Adapter，且不以旧 sender 或 fake 补位；全程无 shim、alias、re-export、fallback、双写、双读或旧配置兼容 |
| 重复职责 | 通过 | Transport、WMS breaker、核心可靠对象、Adapter、插件所有权互斥；Phase 3/4 边界单独列明 |
| 测试过重 | 通过 | Phase 2 仅 MockTransport/local fake 的核心轻量测试；厂商合同、可靠性生命周期和业务 E2E 分别归属其 owner |
| 未确认推测能力 | 通过 | 不含认证 seam、BASIC/HMAC、动态拦截器、DSL、Service Locator、动态发现、未来协议或空插件 |
| 敏感信息 | 通过 | Phase 2 无凭据与 Secret；日志合同仍禁止 headers/body/query/原始异常文本 |
| 阶段越权 | 通过 | 每阶段均有 entry/out-of-scope/atomic handoff/exit gate；上一阶段未退出不得启动下一阶段 |
| 当前状态准确性 | 通过 | Phase 1 完成，Phase 2 计划已批准但生产实施未开始，Phase 3–10 未开始；未把旧类型或远端分支误报为目标交付 |

## 18. 总体完成定义

只有同时满足以下条件，本计划才完成：

1. Phase 2 公共传输、WMS/ECS/RCS Adapter、WES 核心和业务插件分别拥有单一职责与独立验收。
2. 只有 Phase 2 基础层直接依赖 httpx；Adapter 只消费已装配 Transport，核心和插件不可见 httpx/认证/凭据/wire 协议。
3. 核心可靠性不变量全部由最终对象测试证明，厂商合同和业务规则只由各自包测试证明。
4. 旧生产架构、旧 HTTP owner、无依据认证、旧测试所有者、旧配置、兼容路径和旧 migration chain 全部归零。
5. 最终数据库可以从空库一次建立，不需要旧数据、旧 revision 或转换脚本。
6. 当前态文档、active TODO、代码、测试、schema、部署配置和 composition root 共同指向同一个最终架构。

## 19. Implementation Tasks

Phase 2 实施只执行以下五个顺序任务；详细接口、失败矩阵和测试场景以
`docs/superpowers/plans/2026-08-04-wes-outbound-http-transport-convergence.md` 为唯一子计划真源。

| 顺序 | 任务 | Surface area | 主要文件 | 验证 |
| --- | --- | --- | --- | --- |
| 1 | 冻结 framework-neutral 合同并先写失败测试 | method/request/limits/result/protocol/errors | `src/core/outbound_http/contracts.py`、`tests/core/outbound_http/test_contracts.py` | 合同测试先红后绿；不暴露 httpx 类型 |
| 2 | 实现一次发送与完整传输事实矩阵 | HTTPX 映射、总截止时间、响应预算、cleanup | `src/core/outbound_http/transport.py`、`tests/core/outbound_http/test_transport.py` | 成功、失败、取消、限额与清理场景通过；无自动重试 |
| 3 | 实现最小构造与生命周期合同 | `build_outbound_http_transport`、单系统长期 Client、幂等 close | `src/core/outbound_http/factory.py`、`src/core/outbound_http/__init__.py`、`tests/core/outbound_http/test_factory.py` | 生命周期和关闭后调用测试通过；无 registry/credentials/auth seam |
| 4 | 固化架构和 HEAVY 门禁 | httpx 唯一 owner、配置边界、精确影响映射 | `tests/architecture/`、`docs/architecture/heavy-test-impact.toml`、`tests/scripts/` | 架构门禁与 selector 合同通过；核心测试不含厂商或业务语义 |
| 5 | 运行 Phase 2 验证并原子交接 Phase 3 | 四文件生产面、测试证据、文档状态 | Phase 2 子计划、Master、SPEC 和索引 | 定向测试、默认收集、quality profile、ruff、diff check 全部通过 |

五个任务共享同一包和逐层收敛合同，固定为单泳道顺序实施；并行任务数为 0。

## 20. 工程复审完成摘要

- **Step 0 / Scope：** 采用推荐的 `SCOPE_REDUCED`；Phase 2 从“WMS 薄接入 + 推测认证”缩减为四个生产文件的公共出站 HTTP 基础层。
- **Architecture：** 关闭公共层夹带业务成功语义、旧 Provider/Outbox owner 上浮、Phase 2/3/4 原子交接冲突三类问题。
- **Code Quality：** 删除 auth/credentials/HMAC/Clock/Nonce seam、生产 fake/testing helper 和 registry/factory 层级三类 YAGNI 设计。
- **Test Review：** 补齐合同、传输失败矩阵、取消/cleanup、响应预算四组缺口；核心测试只验证基础能力，不验证 WMS、厂商 Adapter 或业务插件。
- **Performance：** 固定每外部系统每进程一个长期 Client、单次发送、无重试、有界 header/body/decode/cleanup；无剩余性能阻断项。
- **Existing / ADD-REUSE-HANDOFF：** 复用 `src/core/bounded_http_response.py`，新增 `src/core/outbound_http/` 四文件；真实 Adapter、composition root 和旧 owner 删除均交给后续阶段。
- **NOT in scope：** 真实系统接入、认证、重试/熔断、业务成功判断、插件语义、旧数据/旧版本兼容均已明确排除。
- **Failure modes：** 传输状态、HTTPX/有界响应异常映射、总截止时间、取消传播和 cleanup 优先级已冻结；critical gap 为 0。
- **Outside voice：** Codex 首轮提出 3 个阻断项和 1 个 header 硬上限缺口，全部回写；第二轮结论为 `BLOCKER NONE`。
- **Parallelization：** 1 条实施泳道，5 个顺序任务，0 个可安全并行任务。
- **TODO：** 不新增推测性 TODO；后续能力只在对应阶段拥有真实合同后进入计划。
- **Lake Score：** 6/6 推荐项已采用；未决选择 0。

## GSTACK REVIEW REPORT

| Review | 本轮状态 | 发现 | 未解决 | 说明 |
| --- | --- | ---: | ---: | --- |
| ENG REVIEW | CLEAR | 10 | 0 | Scope reduced；所有发现已折入 Master、SPEC、合同和 Phase 2 子计划 |
| CODEX REVIEW | CLEAR | 4 | 0 | 3 个初始 blocker 与 1 个硬上限缺口已修正；复审 `BLOCKER NONE` |
| DESIGN REVIEW | N/A | 0 | 0 | 无 UI/交互范围 |
| DX REVIEW | N/A | 0 | 0 | 本轮目标是内部架构实施计划，不新增开发者入口 |

**VERDICT：ENG + CODEX CLEARED — Phase 2 可以开始实施。**

NO UNRESOLVED DECISIONS
