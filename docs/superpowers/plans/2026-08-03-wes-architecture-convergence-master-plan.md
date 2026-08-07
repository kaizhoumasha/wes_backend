# WES 最小执行架构十一阶段收敛总控实施计划

> **For agentic workers:** 本文只控制阶段顺序、职责边界、原子交接和退出门禁。每个阶段开始前必须另有经批准的详细实施计划；不得直接把本文当作代码实施脚本，也不得在阶段门禁未通过时启动下一阶段。

**Goal:** 按十一个单向依赖阶段，将当前 WES 直接收敛到 SPEC 定义的最小执行架构；先分别暗构建 WMS HTTP Client
与最小平台能力，再通过独立原子切换阶段替换生产路径并删除旧能力，最后以独立 Adapter/执行插件、单一数据库基线和
完整系统验收结束收敛。

**Architecture:** Phase 3 `WmsClient` factory 通过 Phase 2 builder，为各运行时/事件循环 owner 装配一个明确生命周期的
`OutboundHttpTransport`；Phase 4 的 WMS 转发 RCS/AGV/CTU 业务模块复用该 Client，并通过 `Transport Port` 与核心隔离。
ECS 等真实设备 Adapter 的 Transport 构造所有权由 Phase 7/8 各自合同冻结。各 Adapter 拥有其
外部系统的 method/path、wire DTO、真实合同要求的认证协议和结果解释；
WES 核心只依赖类型化业务端口，可靠性生命周期分别由 `DeviceCommand`、`TransportTask` 和
`WmsConfirmation` 拥有。测试治理、直接旧所有者随替代随删除和最终数据库基线是贯穿主线。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL/TimescaleDB、Alembic、Celery、
Pydantic 2、HTTPX、Pytest 9、Ruff、Bandit、Import Linter、Jenkins。

**Status:** In progress — Phase 1–2 已完成；Phase 3 已收敛为 Axios 式 WMS HTTP Client，依赖已满足，可以进入实施；
Phase 4–11 尚未开始。

**Requirements baseline:** `docs/architecture/SRS.md`

**Design baseline:** `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`

**Implementation baseline:** `v0.22.1.0`（2026-08-06 Phase 1/2 验收与 Phase 3 边界收敛发布基线）

---

## 1. 全局硬约束

- 系统尚未发布；开发和测试数据可以清空，不保留旧版本、旧 API、旧字段、旧配置、旧数据或历史 revision 的迁移能力。
- SRS 定义产品需求，顶层 SPEC 定义目标架构，本文只编排实施顺序；历史实现、旧分支和未确认设想不得提升为需求。
- 严格遵守 DRY、KISS、SOLID、YAGNI；不建设通用工作流、动态插件发现、Manifest、Service Locator、
  运行时 registry、任意签名 DSL 或推测性集成平台。
- 最终运行态只能存在一条执行路径；禁止兼容 shim、alias、re-export、deprecated wrapper、双写、双读、
  旧路径 fallback 和按 WorkLine 切分的新旧双轨。
- 替代能力通过验收并进入生产切换时，必须在同一原子切换中删除直接旧所有者；Phase 9 只处理跨阶段残留，不能成为
  保留旧路径的理由。
- Phase 2–4 的新基础层、WMS Client 与最小平台/Transport 能力均采用暗构建：源码和新测试可以与旧实现共存，但不得注册到旧
  Composition Root、不得接收生产流量、不得 shadow write，也不得提供新旧选择开关。因此这种静态共存不构成运行时双轨。
  WMS Client、WMS 转发 RCS Adapter 与核心执行对象的直接替代统一发生在 Phase 5；真实 ECS/设备厂商 Adapter 的
  直接替代分别发生在 Phase 7/8。每次原子切换都
  必须删除该次已被替代的直接旧 owner，不保留 shim、fallback 或旧数据迁移。
- WMS 是业务单据、库存、主数据、业务授权和全局仓内位置权威；WES 只拥有工作线本地执行事实；
  ECS/PLC 拥有设备物理动作和安全互锁。
- 外部 HTTP 公共层只发送一次并返回传输事实，不拥有自动重试、业务拒绝、Circuit Breaker 决策、
  Outbox/`TransportTask`/`WmsConfirmation` 生命周期、工作线推进或厂商 Payload 映射。
- WMS/ECS/RCS Adapter 每次调用都只发送一次，不拥有 retry/backoff 配置；只有逐 operation 外部合同明确批准安全重提且
  对象显式 `retryable=true` 时，`WmsConfirmation`、`TransportTask`、`DeviceCommand` 等可靠对象才可保留原 identity/内部
  `dispatch_key`，映射为唯一获批 wire 字段后重新提交；否则保持暂停并进入人工对账。
- 每个外部系统由各运行时/事件循环 owner 持有一个明确生命周期的 Client；禁止跨事件循环共享、每次请求创建 Client，
  也禁止全局万能 Client。
- builder 只完成 base URL、Timeout、连接池和 Transport 生命周期装配；业务 Adapter 只接收该 Transport，
  不接收裸 `httpx.AsyncClient`，也不管理连接池或通用传输异常。
- 当前已批准 outbound 合同均未要求认证，Phase 2 不建设认证策略、凭据解析、HMAC、Clock、Nonce 或预留接口。
  将来只有真实厂商 outbound 合同明确要求时，才先修订对应 Adapter 与总控计划，再在最窄所有者中实现。
- Inbound callback 认证保持独立 API 边界所有权，不得因同为 HTTP 而与 Phase 2 outbound 传输合并。
- 当前产品部署在隔离局域网，不建设 HMAC、nonce、clock、凭据、IP allowlist 或认证扩展 seam。WMS inbound API 在具体
  业务开发时由其 ingress owner 定义，不属于 Phase 3。
- 核心、Adapter、插件测试所有权严格隔离：核心验证基础能力与可靠性，Adapter 验证厂商合同与标准化映射，
  WMS Adapter 验证业务结果合同，插件验证执行 Decision 和对象推进；不得跨层复制或互相替代。
- 设备厂商 Adapter/执行插件包独立构建、测试和显式装配；客户镜像只安装明确清单，不建设运行时扫描或私有包 registry。
  产品内唯一 WMS 北向 Adapter 是 `src/app/wms_adapter/` 下的应用 ACL，不属于客户可选的设备厂商二次开发包。
- Phase 3–4 可以合并“新能力已完成但尚未接线”的稳定暗构建态；任何生产双轨态、未完成 Phase 5 原子交接或未通过
  当前阶段退出门禁的状态不得合并回 `develop`。
- 每个阶段开始前必须有经批准的详细实施计划；计划必须冻结准确文件、接口、测试层级、验证命令和提交边界。

### 当前交付范围

本十一阶段计划只交付顶层 SPEC §3.1 已确认的粗分机、自动分拣、人工分拣、满箱交换和复杂出库能力。
SRS §3.5 特殊物料、机构件/SFC 协同及 §3.6 生产退料属于未来需求，不进入 Phase 7/8 或 Phase 11 验收，
不得据此预建空插件、Adapter 或扩展平台。

## 2. 九阶段到十一阶段的编号映射

| 原编号 | 原名称 | 新编号 | 新名称 | 裁决 |
| --- | --- | --- | --- | --- |
| Phase 1 | 测试治理基线 | Phase 1 | 测试治理基线 | 保持，已完成 |
| 无 | 无 | Phase 2 | 外部 Outbound HTTP 传输基础能力收敛 | 新增独立基础阶段 |
| Phase 2 | WMS 薄接入边界收敛 | Phase 3 | WMS HTTP Client 薄封装 | 消费 Phase 2，只统一 WMS HTTP/JSON 访问标准 |
| Phase 3 | WES 最小平台能力建设 | Phase 4 | WES 最小平台与 Transport 能力建设 | 复用 Phase 3 Client 实现实际 RCS/AGV/CTU 业务交互 |
| 无 | 无 | Phase 5 | 新旧能力原子切换与旧所有者删除 | 新增；统一迁移消费者和生产装配，不迁移旧实现或旧数据 |
| Phase 4 | 核心测试承接与平台基线验收 | Phase 6 | 核心测试承接与平台基线验收 | 承接对象改为 Phase 4 最终对象 |
| Phase 5 | 粗分机参考插件优化 | Phase 7 | 粗分机参考插件优化 | ECS Adapter 消费 Phase 2 传输能力 |
| Phase 6 | 分拣执行插件组优化 | Phase 8 | 分拣执行插件组优化 | ECS/RCS Adapter 消费 Phase 2 传输能力 |
| Phase 7 | 旧平台代码最终闭环清理 | Phase 9 | 旧平台代码最终闭环清理 | 增加裸 httpx、重复传输和无依据认证缺席门禁 |
| Phase 8 | 旧数据模型与迁移链清理 | Phase 10 | 旧数据模型与迁移链清理 | 依赖 Phase 9 零旧路径 |
| Phase 9 | 最终基线与系统验收 | Phase 11 | 最终基线与系统验收 | 验收新增 Phase 2 基础层及职责所有权 |

## 3. 受编号与职责调整影响的文档和引用

| 文档或引用 | 影响 | 本轮处理 |
| --- | --- | --- |
| `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md` | 总控由九阶段改为十一阶段 | 已重写并复审 |
| 旧 `2026-08-03-wes-wms-thin-access-convergence.md` | Phase 2 顺延为 Phase 3；旧稿含无合同依据的认证设计 | 完整移至项目外归档；Phase 3 在 Phase 2 完成后重新编写当前计划 |
| `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md` | 原 Phase 3–9 引用整体顺延 | 同步延后义务与测试承接阶段 |
| `docs/superpowers/README.md` | 当前文档索引仍称九阶段、WMS Phase 2 | 同步索引名称和状态 |
| `docs/architecture/file_index.md` | 文档索引仍称九阶段 | 同步为十一阶段并标注 WMS Phase 3 |
| `README.md` | 当前架构入口仍称九阶段，WMS 计划未标注 Phase 3 | 作为必要索引同步为十一阶段和 Phase 3 |
| `docs/architecture/SRS.md` §1.1、§3.1、§3.5、§3.6 | 四处以“九阶段”描述当前实现或验收范围 | 已机械同步为十一阶段；业务范围不变 |
| `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` §14.2–14.3 | 含旧阶段编号与 Phase 3/4 原子交接语义 | Phase 3/4 实施前同步为暗构建、Phase 5 原子切换语义；不改变业务架构 |
| `docs/plugin_development_guide.md` §2.2–2.3、§2.6 | Adapter 曾被概括为直接处理 HTTP/认证 | 已同步为消费已装配 Transport、只拥有真实厂商协议映射 |
| `docs/integration/workline_device_error_code_standardization.md` 开头、§3.1 | 仍以旧编号指向最小平台和旧代码清理 | 本轮只读；实施对应阶段前同步引用，不改变设备错误语义 |
| `docs/devops/prod-release-deploy.md` 开头 | 仍以旧编号指向最终 Alembic 基线，并记录临时 Provider Profile 部署输入 | 本轮只读；对应新编号为 Phase 10，Phase 5 切换 WMS 配置时同步 Runbook |
| `TODOS.md` | 无阶段编号；依赖最终对象的触发条件仍成立 | 无需修改，不新增重复调度项 |

上述“本轮只读”项不是新的实施入口。SPEC 继续定义目标业务边界；指南和 TODO 不得反向改变本计划的
Transport/Adapter/核心所有权。

## 4. 当前分支与实施状态

| 证据 | 当前事实 | 裁决 |
| --- | --- | --- |
| `git status` | 当前 `develop` 工作树执行 Phase 3 纯文档复评；生产代码未修改 | 只继承当前合同与计划，不继承生产实施 |
| `28eb99d9` / PR #100 | Phase 1 架构与测试治理已合入 | Phase 1 完成，但测试计划延后义务未整体完成 |
| `src/app/wms_integration/` | 仍为 54 个生产文件，Provider/Profile、Registry、QUERY、Effect/status/evidence 混合存在 | Phase 3 不复用其设计；旧包只是待替代的临时所有者 |
| `src/app/sys/external_http_*` 与 `canonical_dispatch.py` | 已有 typed transport fact、凭据解析、NONE/HMAC、bounded response 的部分能力，但耦合 Provider Profile/SystemOutbox/WMS operation | 仅 transport fact 作为行为证据；认证相关能力无真实 outbound 合同依据，不进入 Phase 2 |
| 多处 `httpx.AsyncClient()` | DeviceCommand、旧 Outbox、WMS runtime、旧 Gateway 等仍自行创建 Client | Phase 2–4 只暗构建 successor；Phase 5 统一切换 WMS 与核心执行路径，Phase 7/8 接入真实 ECS/RCS Adapter，Phase 9 建立最终缺席门禁 |
| 目标对象扫描 | 尚无最终 `InboundEvidence`、`TransportTask`、`WmsConfirmation`、`LineRunEpoch` 等完整生产闭环 | Phase 4 未开始；旧 `DeviceCommand`/RuntimeProjection 不能等同于目标平台完成 |
| 当前规划增量 | Phase 3 已删除业务 Port、operation 矩阵和单项业务门禁，只保留 WMS HTTP Client 与开发示例 | 仅文档变更；Phase 3 已可进入实施 |
| 其他旧 feature 分支 | 大幅落后或已被 develop 取代，包含旧 Manifest/Runtime 语义 | 只作 Git 历史，不作为实施输入 |

阶段状态：Phase 1–2 已完成；Phase 3 可进入实施，Phase 4–11 均未开始。

## 5. 总控依赖模型

```text
Phase 1  测试治理基线（已完成）
   ↓
Phase 2  外部 Outbound HTTP 传输基础能力（已完成）
   ↓
Phase 3  WMS HTTP Client 薄封装
   ↓
Phase 4  WES 最小平台能力
   ↓
Phase 5  新旧能力原子切换与旧所有者删除
   ↓
Phase 6  核心测试承接与平台基线
   ↓
Phase 7  粗分机参考插件
   ↓
Phase 8  分拣执行插件组
   ↓
Phase 9  旧平台代码最终闭环
   ↓
Phase 10  旧数据模型与迁移链
   ↓
Phase 11 最终基线与系统验收
```

允许提前编写和评审下一阶段详细计划，但上一阶段退出门禁未通过时不得启动下一阶段生产代码实施。

### Phase 2–5 职责边界

| 责任 | Phase 2：Outbound HTTP | Phase 3：WMS Client 暗构建 | Phase 4：最小平台/Transport 暗构建 | Phase 5：生产切换 |
| --- | --- | --- | --- | --- |
| 生产装配与调用切换 | 不接入真实 Adapter | 不修改旧 Composition Root | 不修改旧 Composition Root | 原子切换全部目标消费者并删除旧 owner |
| Client 生命周期、连接池、Timeout、base URL | 唯一拥有 Transport | 通过 factory 构造并公开长期 `WmsClient` | 复用 Client | 装配并管理 Client 生命周期；Transport 不外露 |
| HTTP method primitive | 拥有当前最小集合，不解释业务 | 提供 request/get/post 薄方法 | 为真实业务选择固定 method | 不改变 method 合同 |
| WMS 业务 method/path、wire DTO、拒绝码 | 不拥有 | 不拥有 | 由具体业务模块拥有 | 只接线，不改变合同 |
| WMS 转发 RCS method/path、wire DTO、状态/取消 | 不拥有 | 不拥有 | Transport Adapter 唯一目标 owner | 只接线，不改变合同 |
| WMS outbound 认证 | 不拥有、不预留 | 当前仅 `NONE`，不提供认证字段或 seam | 不可见 | 删除旧 HMAC/Profile/fallback 配置 |
| 外部调用 evidence 与 breaker | 不拥有 | 不拥有；ACL 无状态 | 由可靠对象按实际需要持有证据；不预建通用 breaker | 删除旧 evidence/breaker 及其表 |
| WMS 查询、业务决策、确认调用 | 不拥有 | 只提供 Client，不提供业务方法 | 随具体业务逐项实现 | 切换真实消费者到新业务模块 |
| WMS 转发搬运调用 | 不拥有 | 不拥有 | `Transport Port` + WMS 转发 RCS Adapter | 切换真实消费者到新 Transport Port |
| `TransportTask`/`WmsConfirmation` 生命周期 | 不拥有 | 不拥有 | 新最终对象唯一目标 owner | 接管生产流量并删除旧可靠闭包 |
| callback ingress | 不拥有 | 不修改旧入口 | 新建最终 `InboundEvidence` 与 application port | 原子切换入口并删除旧 WMS RuntimeInbox 路径 |
| 旧实现和旧测试 | 不修改 | 不修改、不作为新测试 oracle | 不修改，只登记 successor | 按 successor/NONE 处置并删除 |

### Outbound HTTP 认证裁决

| 所有者 | 当前必须拥有 | 明确不得拥有 |
| --- | --- | --- |
| WMS Composition Root | 向 factory 传入 base URL/Timeout 并管理返回的 `WmsClient` 生命周期 | 传入 session factory、运行时扫描、Service Locator、推测性认证配置 |
| Phase 2 基础层 | 无认证职责 | `AuthStrategy`、凭据解析、HMAC、Clock、Nonce、认证枚举或未来扩展 seam |
| WMS Client 包（Phase 3） | 当前 outbound 无认证；只统一 HTTP/JSON 访问 | 业务 DTO、业务结果解释、无合同依据的 canonical/Header/签名、裸 httpx、连接池 |
| ECS/RCS Adapter（Phase 7/8） | 只实现厂商原始资料已明确要求的认证；首次需求必须先修订计划 | 提前预建 BASIC/HMAC、从插件读取凭据、通用认证平台 |
| 部署配置 | 当前不提供 outbound 认证键 | 原始 Secret、任意 Header/签名表达式、未获合同支持的认证选项 |
| WES 核心可靠对象 | 只观察类型化端口结果并管理可靠性生命周期 | httpx、认证方案、签名 Header、credential reference |
| 执行插件 | 无认证职责 | HTTP Client、认证配置、凭据、厂商原始协议、WMS 业务裁决 |

## 6. Phase 1：测试治理基线

**Objective:** 冻结目标 SPEC、实施基线、核心/Adapter/插件测试所有权与 FAST/QUALITY/HEAVY 重量边界。

**Authoritative inputs:** 顶层 SPEC、`docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`、
`tests/README.md`、`docs/architecture/heavy-test-impact.toml`。

**Entry conditions:** 已满足；基线工作从当时最新 `develop` 执行，未启动生产内核重构。

**Scope:** 已完成测试拓扑、预算、核心/插件所有权门禁和显式旧插件测试清理；冻结 Task 4/5/7 的延后承接义务。

**Explicit out-of-scope:** 不实现 Outbound HTTP、WMS 薄边界、最终执行对象、真实 Adapter 或执行插件。

**Deliverables:** PR #100 / `28eb99d9`；核心 FAST/QUALITY/HEAVY 治理；测试处置四分类；延后义务清单。

**旧所有者删除或交接清单:** 已删除 `tests/workline_plugins/` 和可独立判定的插件/旧平台测试；五个混合 WMS/入站资产
保留到 Phase 6，在最终对象权威测试通过后处置。

**测试所有权与重量要求:** Phase 1 的治理门禁继续生效；“阶段完成”不代表测试收敛计划整体完成。

**与前后阶段的 atomic handoff:** 向 Phase 2 交付轻量测试规则；向 Phase 6 交付 Task 4/5/7 延后义务。

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
和脱敏日志；method primitive 只包含已批准消费者合同需要的 `GET/POST`。只复用现有
`src/core/bounded_http_response.py`，不新增认证或测试替身生产模块。

**Explicit out-of-scope:** 自动重试、业务拒绝映射、Circuit Breaker 决策、WMS/RCS/ECS method/path/wire DTO、
任何 outbound 认证/凭据/签名/Clock/Nonce、Outbox/`TransportTask`/`WmsConfirmation` 生命周期、工作线推进、
动态拦截器注册表、运行时排序、Service Locator 和通用集成框架；既有 WMS/RCS/ECS Adapter、Composition Root、
旧 Outbox/Effect/status/DeviceCommand 调用链的修改、接入或删除同样不属于本阶段。

**Deliverables:** 固定请求/响应流水线；`GET/POST` 最小 method 合同；Transport/builder/配置与生命周期合同；脱敏日志；
`tests/core/outbound_http/` 下只依赖 `httpx.MockTransport` 或测试内 local fake 的轻量测试；证明生产包不导出测试替身、
不包含认证 seam 的架构门禁；供 Phase 3/4/7/8 消费的公开合同和旧消费者 HANDOFF 清单。

**旧所有者删除或交接清单:** Phase 2 不修改或删除 `sys/external_http_*`、WMS transport、旧 Outbox sender、
DeviceCommand Client 及其生产调用者，只登记当前 owner、直接 importer 和目标切换阶段。WMS
QUERY/Provider/Transport owner 由 Phase 5 切换并删除；WMS Effect/status/Outbox 持久化、claim、重试、fencing 和终态
可靠生命周期闭包由 Phase 4 建立 successor、Phase 5 原子切换并删除；
`src/app/device/services/device_command_service.py`、`src/app/runtime/orchestration/services/device_command_gateway.py`、
`src/app/runtime/orchestration/services/inbox/outbox_dispatch_service.py`、
`src/app/runtime/capabilities/material_flow/start_admission_service.py` 和
`src/app/runtime/capabilities/material_flow/smt_inbound_handoff_route_service.py` 的核心 DeviceCommand HTTP 发送、实时状态及
准入探测分支由 Phase 4 建立 typed Device command/status/admission port successor，Phase 5 改接并删除裸 Client；SMT handoff
的本地业务判定不得保留为目标规则，Phase 7 插件只消费 WMS 业务结果并映射执行，且不得继续 import gateway 私有 helper。真实 ECS/RCS Adapter 由 Phase 7/8 首次
接入 Phase 2 Transport，同时删除对应厂商重复 Client/传输实现。Phase 5 还必须删除旧
`SystemOutboxDispatchType.DEVICE_COMMAND` 可靠性 owner：`src/app/sys/models/outbox.py`、
`src/app/sys/repositories/outbox_repository.py`、`src/app/sys/services/outbox_engine.py`、
`src/app/runtime/orchestration/enums.py`、`src/app/runtime/orchestration/services/intent/operation_service.py`、
`src/app/workline/services/write_back_service.py` 及 `outbox_dispatch_service.py` 中仅服务该 dispatch type 的模型约束、索引、
创建、claim/队首互斥、wait/retry/fencing、dispatch、ACK 和投影分支，改由最终 `DeviceCommand` 对象唯一拥有。跨多个消费者
的共享旧 helper 由最后一个生产 importer 所在阶段删除，Phase 9 只验证无遗漏，不作为延迟删除的默认归宿。

**测试所有权与重量要求:** 只用 `httpx.MockTransport`、测试内 local fake 和纯单元测试；不访问真实外部系统，
不使用 sleep，不建立大规模 E2E。只验证框架无关合同、生命周期、请求装配、受限响应、传输事实分类、取消传播、
已知异常与意外异常边界及日志脱敏。厂商认证/Header/DTO、业务拒绝、重试/终态/恢复不进入本阶段测试。

**与前后阶段的 handoff:** Phase 2 向 Phase 3/4/7/8 交付同一 Transport 合同，但不激活任何真实 Adapter 或生产
Composition Root；新包与尚未切换的旧消费者共存不构成运行时双轨，因为生产请求仍只有原路径。Phase 3 暗构建 WMS
Client；Phase 4 复用该 Client 暗构建 WMS 转发 RCS Adapter；Phase 5 才首次由 Composition Root 装配两者并删除
对应旧 Client；Phase 7/8 按各自批准的设备合同冻结并执行真实 ECS/设备厂商 Transport 构造边界。

**Exit gate:** builder 只返回可直接使用的 Transport；公开合同不暴露 httpx 类型；GET/POST 走同一条单次发送路径且无
WMS 条件分支；公共层单次发送且无重试/业务解释；
不捕获未知编程异常，不存在认证、credential、HMAC、Clock、Nonce 或生产 fake；全部轻量测试和架构门禁通过；
`src/core/outbound_http/` 之外的既有生产文件无 Phase 2 实施修改，WMS/RCS/ECS 生产模块和 Composition Root 零新包
import；`src/core/bounded_http_response.py` 内容与路径保持不变；Phase 3 可在不复制 transport 语义的前提下开始。

**详细计划归档:** GET/POST 基线已完成且当前无待实施修订；Phase 2 计划已移至项目外
`../archive_docs/wes_backend/docs/superpowers/plans/2026-08-04-wes-outbound-http-transport-convergence.md`，不再承担当前执行入口。

**风险及防止阶段越权的约束:** 最大风险是把既有 SystemOutbox/Provider Profile 整体提升为公共层、提前修改真实
Adapter/Composition Root，或建设认证/拦截器扩展平台。子计划必须以“新包单一实现、稳定 primitive 只读复用、
旧消费者精确 HANDOFF”为准；不得搬迁旧模块、复制旧源码、继承旧生命周期或提前执行 Phase 5/7/8 的切换。

## 8. Phase 3：WMS HTTP Client 薄封装

**Objective:** 在不触碰当前生产路径的前提下，消费 Phase 2 Transport，暗构建一个类似前端 Axios 的长期
`WmsClient`，统一 WMS origin、GET/POST、query、headers、JSON 编解码、传输事实和资源关闭。

**Authoritative inputs:** Phase 2 公开合同、WMS Client 使用合同和 Phase 3 详细计划。前端 `src/api/client.ts` 与
`src/api/contract/client.ts` 只作为职责形态参考。当前 `src/app/wms_integration/` 只用于识别未来删除边界，不是新实现模板。

**Entry conditions:** Phase 2 基线已通过退出门禁，所需 Transport、request/result 和 builder 已存在。具体 WMS 业务 API、
消费者、wire、DTO 和业务尺寸预算不属于 Phase 3 入口条件，因此当前可以进入实施。

**Scope:** 独立 `src/app/wms_adapter/` 包，只包含 `client.py`、`factory.py` 和 `__init__.py`；公开
`request/get/post/aclose`，每次调用最多一次 Phase 2 send。访问层只处理 HTTP/JSON，不知道 operation 或业务含义。

**Explicit out-of-scope:** 所有具体 WMS 业务 API、业务 Port、业务 DTO、业务 Outcome、消费者矩阵、inbound API、
RCS/AGV/CTU 业务、数据库、Migration、Repository、Service、evidence、retry、breaker、分页、缓存、动态 registry、
认证扩展、生产接线和旧 owner 修改。

**Deliverables:** 最小 `WmsClient`、factory、公开导出、访问层 FAST 测试，以及一份不接生产的新增业务 API 指导示例。
示例不得演化为演示业务模块、公共业务基类、代码生成器或 registry。

**旧所有者处置:** `NONE`。Phase 3 不迁移、不改写、不删除旧生产 owner 或旧测试；Phase 5 才原子切换并直接删除旧实现。

**测试所有权与重量要求:** FAST 只验证 GET query、POST JSON、relative path、一次 send、status/headers、Phase 2
delivery facts、取消和关闭；不得验证库存、PickingTask、业务拒绝、NG 或 WorkLine 执行。

**与前后阶段的 handoff:** 从 Phase 2 接收 Transport/Builder；向后续所有真实 WMS 业务模块交付同一个访问 Client。
Phase 4 可以复用该 Client 实现 WMS 转发的 RCS/AGV/CTU API，但 Phase 3 不拥有这些业务语义。

**Exit gate:** 三个目标生产文件和访问层测试完整；新包只依赖 Phase 2，无业务 API、业务 Port、数据库、旧包、认证、
retry、registry 或生产接线；FAST、Ruff、类型检查、Import Linter、架构门禁和 quality profile 通过。

**需要单独编写的子计划:** 使用
`docs/superpowers/plans/2026-08-05-wes-wms-thin-access-convergence.md`。

**风险及防止阶段越权的约束:** 最大风险是把未来业务 API、业务模型或通用集成平台提前塞入 Client。任何业务字段、
path 和结果解释都必须留到真实业务开发。

## 9. Phase 4：WES 最小平台能力建设

**Objective:** 在不触碰当前生产路径的前提下，暗构建最终执行对象、三类可靠性生命周期、通用 WorkLine、投影、
最小插件 SPI/SDK、`Transport Port` 及 WMS 转发 RCS Adapter，并仅用新核心/Transport 测试验证目标能力。

**Authoritative inputs:** 顶层 SPEC §4.4/§6–§10、Phase 2 Transport、Phase 3 `WmsClient`、设备命令合同、
Transport 业务需求和入站验证原则。

**Entry conditions:** Phase 3 退出门禁通过；WMS 转发 RCS/AGV/CTU 的真实 submit/status/cancel wire 已单独获批；
Phase 5 旧可靠链删除闭包和 consumer 清单已冻结，但本阶段不执行。

**Scope:** `WorkLine`、`LineRunEpoch`、`MaterialExecution`、`BinExecution`、位置/设备投影、`InboundEvidence`、
`DeviceCommand`、`TransportTask`、`WmsConfirmation`、封闭 Decision、显式依赖注入、可靠领取/轮询/重试/终态/恢复、
`Transport Port`、WMS 转发 RCS Adapter、Phase 4 测试树内最小 typed-port fake 和 uv workspace 显式装配边界。

**Explicit out-of-scope:** 裸 httpx、WMS 业务 method/path/DTO、具体 ECS 厂商命令、WES 本地业务规则、动态插件发现、
Manifest、Service Locator 和自动物理恢复。WMS 转发 RCS wire 只存在 Phase 4 Transport Adapter，不进入核心对象或插件。

**Deliverables:** Fake 执行插件可运行的独立最小闭环；插件只把 WMS 封闭业务结果映射为执行 Decision；三类可靠对象及新持久化约束；类型化 WMS/Device/Transport 端口；
WMS 转发 RCS Adapter；投影与人工清线语义；供 Phase 5 接线的目标路径。当前生产 Composition Root、旧表、旧实现和旧测试保持不变。

**Phase 5 旧所有者切换清单（Phase 4 只登记 successor，不修改旧 owner）:** `TransportTask`/`WmsConfirmation` 权威测试通过后，Phase 5 原子删除旧 WMS
Effect/status/Outbox/fulfillment/operation-registry 生命周期闭包；Provider Profile、旧认证和旧 Transport 同样由 Phase 5
删除。Phase 4 只记录最终对象对 RuntimeInbox、Intent/Effect、Hold/Recovery/Reservation 旧 owner 的 successor 关系；
Phase 5 原子切换时删除，不推迟到 Phase 9。DeviceCommand 闭包在 Phase 5 必须把 `device_command_service.py` 的直接发送、
`device_command_gateway.py` 的派发/实时状态探测、`outbox_dispatch_service.py` 的 blocked-resource ECS probe、
`start_admission_service.py` 的设备状态拉取和 `smt_inbound_handoff_route_service.py` 的 source-pick 状态探测改接同一组 typed
Device command/status/admission port，删除五个核心路径中的 `httpx` import、每请求 Client、厂商 URL/Header/ACK 解释、
直接网络异常处理，以及 SMT 路径对 gateway 私有实时探测 helper 的 import。SMT handoff 的业务判断可留至 Phase 7 由
粗分机插件替代，但 Phase 5 结束后只允许消费 typed port，不再拥有网络或厂商协议职责。
Phase 4 交付 DeviceCommand 可靠生命周期、typed Device port、核心 fake 和当前产品的 WMS 转发 RCS wire；不实现 ECS
厂商 wire。需要真实 ECS 设备通信的部署必须等待 Phase 7/8 Adapter 接入；Phase 5 切换时不得保留旧 HTTP sender 作为 fallback。WMS Outbox 闭包必须
包含两个 `tasks/sys.py` WMS dispatcher、Celery beat/route、
`outbox_dispatch_composition.py` 的 WMS scopes、`task_queue_gateway.py` 的 WMS targets/status enqueue，以及
`outbox_engine.py`、`models/outbox.py`、`canonical_dispatch.py`、旧 effect reducer 和 WMS capability effect runtime 的
WMS 专属分支。Phase 5 始终删除旧 `wms_typed_effect_callback_router` 到 status service 的调用，以及
`process_external`、callback writer 和 OpenAPI/sandbox 中的旧 WMS hint 支持；successor 已明确为 `NONE`，只删除旧分支，
不建立新 hint 入口。
普通 WMS event 仍由 `InboundEvidence` application port 接管；完整旧入站链 `runtime_inbox_orchestrator_bridge` →
`wms_runtime_inbox_handler` → router 及其 `services/inbox/__init__.py` re-export、callback writer 的普通 WMS event 分支同步
接管并删除。`callback_ingress_service.py` 的普通 WMS event 只按已批准 event 合同映射封闭 typed outcome；hint 分支按
`NONE` 删除。两类分支都不得继续捕获旧 RuntimeInbox 异常或增加兼容异常转换；非 WMS callback 的旧异常合同留给其对应阶段。
WorkLine sandbox external-callback 的 WMS 分支不得绕过该入口写 RuntimeInbox：删除其 WMS source/callback 支持，
并从 `SandboxExternalCallbackRequest`/OpenAPI 删除 WMS 默认值和允许项；Phase 4 验收使用 Phase 3 `WmsClient`、
本阶段定义的具体业务 Port、核心测试树自有 fake 与最终可靠对象 fixture。
同一 Phase 5 原子切换必须删除 `SystemOutboxDispatchType.DEVICE_COMMAND` 及其全部旧可靠性分支：
`models/outbox.py` 的枚举、resource-wait 判定和专属索引，`outbox_repository.py` 的 DeviceCommand claim、物理设备队首互斥、
blocked-resource、wait/retry/fencing 与恢复查询，`outbox_engine.py` 的 DeviceCommand dispatcher，
`runtime/orchestration/enums.py` 的旧 Outbox dispatch type，`operation_service.py` 的 Outbox 命令状态/ACK 校验，
`write_back_service.py` 和 `device_command_service.py` 的 DeviceCommand SystemOutbox 创建，
`device_command_gateway.py` 的旧 dispatch type 校验，以及 `outbox_dispatch_service.py` 的 dispatch/sandbox/观测分支。最终
`DeviceCommand` 对象直接拥有持久化、claim、设备互斥、重试、ACK/CALLBACK、终态、恢复和投影，禁止用 typed port 包裹
旧 SystemOutbox 生命周期。只有确实不属于 WMS 或 DeviceCommand 的通用 SystemOutbox/RuntimeInbox 才可留给
Phase 7/8/9。

**测试所有权与重量要求:** Phase 4 只新增最终核心对象测试，验证持久化、幂等、ACK/CALLBACK、claim/fencing、重试、
终态、恢复和投影；使用本阶段核心测试树内最小 typed-port fake，不导入 Phase 3 Adapter 测试资产，不复制 WMS/ECS
厂商合同和具体工作线 happy path，不修改或删除旧测试。

**Phase 5 旧测试 successor/NONE 清单（Phase 4 只登记）:** `test_device_command_service_contract.py`、
`test_device_command_gateway.py`、`test_outbox_dispatch_async_guard.py` 以及 Phase 4 详细计划冻结的 start-admission/SMT
handoff 核心承接测试，将绑定裸 `httpx.AsyncClient`、gateway 私有 helper、厂商 URL/ACK 或实时 HTTP probe 的断言改为
typed Device port 调用、错误传播和零核心网络副作用。`tests/api/test_workline_runtime_sse.py` 只保留 typed Device port ACK
后提交 `command.status.changed` 状态事件及 SSE API 行为，并改用 typed Device port fake；HTTP GET/POST、URL、状态
payload 和 ACK wire 断言只在 Phase 7/8 Adapter 包重建。先在最终 `DeviceCommand` 上建立持久化、claim、设备互斥、
resource wait、重试、fencing、ACK/CALLBACK 和终态的权威测试，再删除
`tests/sys/test_system_outbox_engine.py`、`tests/sys/test_system_outbox_dispatch_concurrency_contract.py`、
`tests/workline_runtime/test_system_outbox_resource_wait_contract.py`、
`tests/workline_runtime/test_dispatch_attempt_lease_fencing.py` 和
`tests/contracts/system_capabilities/test_canonical_external_http_dispatch.py` 中仅验证
`SystemOutboxDispatchType.DEVICE_COMMAND` 的旧 owner 用例；仍适用于其他 dispatch type 的用例不得随之误删。每项删除都
必须在 Phase 4 详细计划登记最终 DeviceCommand 目标测试路径；实际旧测试修改/删除由 Phase 5 执行，无最终语义的旧 DispatchEnvelope/schema 断言标记 `NONE`。
`tests/api/test_qa_regression_002.py`、`tests/api/test_workline_runtime_sse.py`、
`tests/integration/test_system_outbox_repository.py`、`tests/integration/test_system_outbox_dispatch_concurrency.py` 和
`tests/resilience/test_runtime_scenario_replay.py` 中直接构造或断言 DeviceCommand SystemOutbox 的用例由 Phase 5 同步
完成 successor/NONE 处置；integration/resilience 变更同时精确更新
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
SystemOutbox helper 的直接 patch，也必须在 Phase 5 改接最终 `DeviceCommand` successor 或按 `NONE` 删除旧耦合。

**与前后阶段的 handoff:** 接收 Phase 3 `WmsClient`，在本阶段按真实合同构建 WMS 转发 RCS/AGV/CTU 业务模块与
Transport Port；在自身测试树定义 test-local fake 并完成闭环；向 Phase 5 交付最终对象、业务模块与 Adapter 的
Composition Root 目标接线图和 successor/NONE 清单；同时冻结 typed Device port，供 Phase 7/8 真实 ECS Adapter 接入。
Phase 4 不执行生产切换。

**Exit gate:** Phase 4 test-local typed-port fake 可在新核心测试中驱动 Input → Evidence → Decision → Command/Transport/Confirmation → Result →
Projection；最终对象及新表约束完整；核心新代码无 httpx/认证/厂商协议；新 Composition 模块未注册到当前生产入口；
旧生产 owner 和旧测试未被修改；Phase 5 所需 consumer、配置、数据清理及 successor/NONE 清单完整且无未决所有权。

**Phase 5 未来删除门禁清单（不是 Phase 4 退出条件）:** Phase 4 test-local typed-port fake 可驱动 Input → Evidence → Decision → Command/Transport/Confirmation → Result → Projection；
核心对象无 httpx/认证/厂商协议；旧 WMS 可靠闭包以及被最终对象直接替代的 RuntimeInbox、Intent/Effect、
Hold/Recovery/Reservation 等生产所有者全部缺席；`dispatch_wms_*_outbox_batch`、WMS Outbox claim scope/target、
`enqueue_wms_effect_status`、旧 callback router/status service 调用和 SystemOutbox WMS 专属校验/发送/恢复分支零命中；
`device_command_service.py`、`device_command_gateway.py`、`outbox_dispatch_service.py`、`start_admission_service.py` 和
`smt_inbound_handoff_route_service.py` 的核心 DeviceCommand/设备状态/准入路径零 `httpx`、裸 Client、gateway 私有网络
helper 或厂商 wire 解释，只调用 typed Device port，且生产 Composition Root 不绑定 fake 或旧 sender；
全仓生产代码零 `SystemOutboxDispatchType.DEVICE_COMMAND`、零字符串 `DEVICE_COMMAND` Outbox dispatch type、零 DeviceCommand
SystemOutbox 专属索引、claim/wait/retry/fencing/dispatch/ACK/投影分支；对应核心可靠性只由最终 `DeviceCommand` 对象及其
权威测试拥有；精确 owner 扫描必须覆盖 `device_command_service.py` 的旧 Outbox 创建和
`device_command_gateway.py` 的旧 dispatch type 校验；全部直接引用被删除枚举/模型/分支的 FAST/HEAVY 测试已在 Phase 5
完成 successor/NONE 处置并可收集；测试 import/语义缺席扫描同时覆盖 `_dispatch_device_command`、
`SystemOutboxDispatchType.DEVICE_COMMAND`、`device-command:` dispatch key、DeviceCommand SystemOutbox 双创建，以及
ACK-exhausted Reconciliation/SystemOutbox 路径，还必须覆盖旧 `prepare_runtime_effect()` 三对象写入合同和
`_mark_device_command_failed_if_dispatch_exhausted`、`_dispatch_blocked_resource_heads`、
`_repair_orphaned_device_busy_dispatches`、`_repair_self_blocked_device_busy_dispatches` helper，不能只扫描
`DEVICE_COMMAND` 字面量；
API callback 到 writer/consumer 的普通 WMS event 路径只持久化 `InboundEvidence`，零 WMS RuntimeInbox 写入或无人消费 ACK，
并按已批准 event 合同验证 typed outcome，零旧 RuntimeInbox 异常 import、catch 或兼容转换；hint 的生产路由、payload、
OpenAPI 和测试全部按 `NONE` 删除，不得保留旧分支；
WorkLine sandbox route 同样零 WMS RuntimeInbox/SystemOutbox fallback 或无条件 WMS enqueue；
Phase 6 只接收最终生产路径和待承接的旧测试资产。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-minimal-platform-capabilities.md`，同步其阶段号为 Phase 4。

**风险及防止阶段越权的约束:** 禁止重建通用 Runtime/Effect 平台；禁止核心读取 Secret 或 wire 协议；禁止在 Phase 4
接线、改写旧实现或删除旧测试，也禁止为了未来切换增加兼容桥。

## 10. Phase 5：新旧能力原子切换与旧所有者删除

**Objective:** 在 Phase 3 WMS Client 与 Phase 4 最小平台/Transport Adapter 都已独立验收后，一次性迁移生产消费者和 Composition Root，
清理开发/测试数据，并删除被替代的旧实现、旧配置和旧测试，使生产运行态只剩目标路径。

**Authoritative inputs:** Phase 3 WMS Client 合同与暗构建门禁；Phase 4 具体业务 method/path/DTO/result、最终对象、接线图、旧 owner
闭包和 successor/NONE 清单；当前生产 Composition Root、Settings、Compose/Jenkins/Celery/API callback 实际引用图。

**Entry conditions:** Phase 3–4 退出门禁均通过；WMS Client、Transport Port、WMS 转发 RCS Adapter 与最终
可靠对象均稳定；所有旧生产 importer、配置键、任务路由、数据库对象和测试 owner 已逐项归类；WMS 普通 callback 的
successor/NONE 裁决已完成，不得带未决语义进入本阶段；不存在
需要兼容或迁移的发布数据。

**Scope:** 生产 Composition Root 装配 Phase 3 WMS Client 与 Phase 4 WMS 转发 RCS Adapter；后续具体 WMS 业务模块统一
复用该 Client。所有 WMS 业务查询、业务决策、业务确认、WMS inbound 业务命令、
Transport Port 消费者、普通 event callback 及核心可靠对象消费者原子切换；按 successor/NONE 清单删除旧分支；删除旧
`src/app/wms_integration/`、Provider/Profile/Registry、旧裸 HTTP/HMAC/
credential/fallback、WMS System Capability 和旧可靠分支；切换 Settings/Compose/Jenkins/Celery/Runbook；按 successor/NONE
处置旧测试及 HEAVY mapping；清空开发/测试旧表数据并删除被替代 schema。

**Explicit out-of-scope:** 搬运或包装旧实现、兼容 shim、双写/双读、按 WorkLine 灰度、旧数据转换、重新定义 Phase 3
wire 合同、重新实现 Phase 4 生命周期、真实 ECS/RCS Adapter 和执行插件优化。

**Deliverables:** 唯一生产 WMS Client、唯一 WMS 转发 RCS Adapter/Transport Port；唯一
`TransportTask`/`WmsConfirmation`/`DeviceCommand` 生命周期；独立
普通 event callback ingress；hint successor 为 `NONE`；无旧 Provider/Profile/HMAC/裸 Client/旧
WMS Outbox 分支的生产树；完成 successor/NONE 的测试树。

**旧所有者删除规则:** 本阶段不是“迁移旧能力代码”，而是迁移消费者和生产装配。每个新 owner 的目标测试先通过，
随后在同一原子提交序列中删除直接旧 owner；不得复制旧源码、保留 re-export、alias、fallback、shadow write 或历史表读取。
共享模块只删除 WMS/DeviceCommand 专属分支，仍有非目标真实消费者的通用分支不得误删。

**测试所有权与重量要求:** 先运行 Phase 3/4 新能力权威测试，再改写生产接线测试，最后按逐文件 successor/NONE 清单删除
旧断言；FAST 验证唯一 Composition 与类型化结果，受影响 integration/e2e/resilience/mock 显式运行并精确更新 HEAVY mapping。
禁止以关键词批量删除测试，也禁止恢复旧 owner 只为保持旧测试通过。

**与前后阶段的 atomic handoff:** 接收两个暗构建 successor，在一个 PR 内按“目标测试绿 → 接线 → 删除旧 owner →
全量验证”串行完成；向 Phase 6 交付唯一生产路径和仅剩的跨插件混合测试承接项。任何可运行双轨中间态不得合并。

**Exit gate:** 当前生产 Composition Root 只引用新 WMS Client、Phase 4 WMS 转发 RCS Adapter 和最终对象；全仓生产代码零旧 Provider/Profile、
WMS HMAC/credential/fallback、旧 query/effect/status runtime、重复 WMS Transport、WMS 专属 SystemOutbox/RuntimeInbox 路径及
`SystemOutboxDispatchType.DEVICE_COMMAND`；旧配置和任务路由缺席；可靠对象证据有明确 owner 和清理边界；对应旧测试完成
successor/NONE；FAST、QUALITY、精确 HEAVY、Ruff、Bandit、Import Linter、GitNexus detect changes 和运行态 smoke 全部通过。

**需要单独编写的子计划:** 启动前根据 Phase 3/4 实际交付重新扫描引用图，编写并批准
`docs/superpowers/plans/2026-08-06-wes-atomic-capability-cutover.md`；不得提前把当前静态文件矩阵视为执行真源。

**风险及防止阶段越权的约束:** 最大风险是切换范围过大或漏掉隐藏 producer/consumer。必须按真实引用图逐项切换，
不得拆成可运行的双轨 PR；若新能力缺口暴露，应回到对应新 owner 修正并重新验收，不能临时调用旧路径兜底。

## 11. Phase 6：核心测试承接与平台基线验收

**Objective:** 将跨插件通用可靠性和 WorkLine 语义完全承接到 Phase 4 最终对象，并证明无真实插件也可独立验收平台。

**Authoritative inputs:** 测试收敛计划 Task 4/5/7、Phase 4 最终对象、`tests/README.md` 和 HEAVY selector 真源。

**Entry conditions:** Phase 5 原子切换退出门禁通过；Phase 4 最终对象已成为唯一生产路径；剩余旧测试 successor 映射明确。

**Scope:** 完成五个混合资产及其他不直接阻塞 Phase 4 测试收集的剩余旧测试处置；审计 Phase 4 已建立的最终核心唯一测试；
扩展核心/Adapter/插件所有权门禁；运行 FAST/QUALITY/受影响 HEAVY。

**Explicit out-of-scope:** 具体工作线执行闭环、厂商 canonical/Header/DTO 和执行插件验收。

**Deliverables:** 平台核心基线、最终对象测试矩阵、旧测试 successor/NONE 审计、核心测试缺席门禁。

**旧所有者删除或交接清单:** 直接引用 Phase 5 被删除生产符号、枚举或数据库约束的测试，以及全部 DeviceCommand
SystemOutbox 测试 owner，必须已随 Phase 5 原子切换完成 successor/NONE 处置，本阶段不得接收。Phase 6 只处置仍可在
最终生产对象上收集、但继续混合验证 RuntimeInbox、Intent/Effect、Capability、Manifest、Hold、Recovery、Reservation
语义的旧测试及五个混合测试资产；最终对象 successor 测试先通过再删除旧断言。不得为测试承接恢复 Phase 5 已删除的生产 owner；
插件/Adapter 专属行为分别交给 Phase 7/8 包内重建。

**测试所有权与重量要求:** 核心 FAST 不访问真实数据库/HTTP/Celery；必要持久化与进程测试进入精确 HEAVY；
不以厂商或工作线 E2E 证明核心。

**与前后阶段的 atomic handoff:** 接收 Phase 5 唯一生产对象；向 Phase 7/8 交付稳定 SDK/门禁和明确的插件/Adapter 测试所有权。

**Exit gate:** 测试计划 Task 4/5 核心承接完成；核心测试无具体插件/厂商行为；最小 fake 通过平台基线；结果只称“平台核心基线”。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-platform-baseline-acceptance.md`，同步其阶段号为 Phase 6。

**风险及防止阶段越权的约束:** 禁止为绿灯删除未承接不变量；禁止把插件测试改名搬入核心 contracts/runtime/HEAVY。

## 12. Phase 7：粗分机参考插件优化

**Objective:** 以粗分机交付首个真实执行插件和现场所需 ECS Adapter，验证 Phase 2/4 平台边界足以支持真实闭环。

**Authoritative inputs:** 顶层 SPEC §11.1、粗分机真实拓扑、厂家合同、WMS 操作清单、Phase 2 Transport、Phase 4 SDK。

**Entry conditions:** Phase 6 平台核心基线通过；粗分机厂商资料完整；Adapter 允许的认证闭集有真实合同依据。

**Scope:** 独立 Adapter/插件包；事件/命令标准化；粗分 WMS 业务结果到执行 Decision 的映射；设备长命令、NG 执行、
WMS 决策/查询/确认和搬运端口消费。

**Explicit out-of-scope:** 其他分拣线、公共 HTTP Client、凭据、通用认证配置、WMS wire 合同重定义、通用插件模板。

**Deliverables:** 可独立构建/测试的粗分机 Adapter 与插件；显式 composition root 绑定；真实业务验收结果。

**旧所有者删除或交接清单:** Adapter 交付时删除核心/插件中的对应厂商 DTO、HTTP Client、HMAC 工具和映射副本；
插件交付时删除旧粗分业务代码、配置和测试，不保留 alias/fallback。

**测试所有权与重量要求:** Adapter 包拥有厂商 canonical/Header/DTO/错误映射/合同；WMS Adapter 包拥有业务结果合同；
插件包拥有执行 Decision 和对象推进；
Phase 2/核心测试不得复制这些场景，两个包测试均不进入核心默认 pytest。

**与前后阶段的 atomic handoff:** 消费 Phase 2 Transport 与 Phase 4 SDK；发现公共能力缺口时只有两个以上已确认消费者成立
才可修订平台，否则规则留在粗分插件。

**Exit gate:** 两个包独立安装、构建、测试；业务闭环仅由插件拥有；厂商协议仅由 Adapter 拥有；核心无特殊分支。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md`，同步其阶段号为 Phase 7。

**风险及防止阶段越权的约束:** 禁止插件访问 Transport/HTTP/认证/凭据；禁止首次真实 BASIC 需求未经计划修订直接实现。

## 13. Phase 8：分拣执行插件组优化

**Objective:** 按真实工作线和厂家合同分别交付自动分拣、人工分拣、满箱交换和复杂出库，不建设通用分拣工作流。

**Authoritative inputs:** 顶层 SPEC §11.2–§12、每条线真实拓扑与厂家合同、Phase 7 复审结果、Phase 2/4 稳定接口。

**Entry conditions:** Phase 7 退出门禁通过；每个实际插件/Adapter 的范围、厂家合同和部署组合明确；各详细计划获批。

**Scope:** 自动/人工/满箱交换/复杂出库执行插件；所需 ECS/RCS Adapter；WMS 业务结果映射、同线进出、NG 执行、即时
PUT、CTU 批次和 WMS 来源权威。

**Explicit out-of-scope:** `SorterCorridor`、库存权威、动态发现、统一厂商认证三选一、公共 HTTP Client、预建 BASIC、通用工作流 DSL。

**Deliverables:** 每个真实 Adapter/插件独立包、fixture、测试、构建产物和显式装配；客户镜像清单。

**旧所有者删除或交接清单:** 每个 Adapter 交付时删除对应裸 Client、重复连接池/HMAC/协议映射；每个插件交付时删除旧业务代码、
配置和测试；删除包时同步移除 workspace、镜像和 composition root 绑定。

**测试所有权与重量要求:** Adapter 独立拥有厂商合同/集成/E2E/韧性；插件独立拥有业务单元/集成/E2E/韧性/并发/负载；
核心只验证通用机制，Phase 2 只验证 transport。

**与前后阶段的 atomic handoff:** 消费已稳定的 Phase 2 Transport 和 Phase 4 SDK；全部实际交付包完成后向 Phase 9 提交零散旧所有者清单。

**Exit gate:** 两条自动线和两条人工线使用同一插件的不同配置实例；全部包独立通过；核心无插件 import/fixture/名称分支；
无动态平台扩张。

**需要单独编写的子计划:** 分别编写并批准：
`docs/superpowers/plans/2026-08-03-automatic-sorter-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-manual-sorter-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-full-bin-exchange-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-complex-outbound-plugin-convergence.md`。若真实现场证明应合并为同一部署插件，
先修订本阶段清单，不预建空包。

**风险及防止阶段越权的约束:** 禁止从粗分计划复制改名；禁止把首个 BASIC 需求当作全局通用选项，必须先修订对应 Adapter 计划和认证闭集。

## 14. Phase 9：旧平台代码最终闭环清理

**Objective:** 在 Phase 2 已交付唯一新基础层、Phase 3–8 的目标替代均已完成的基础上清除跨阶段残留，证明生产态只有
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

**与前后阶段的 atomic handoff:** 接收 Phase 3–8 的删除余量并核验 Phase 2 新基础层的唯一性；全部零命中后才允许
Phase 10 固化最终 metadata。

**Exit gate:** 机器门禁证明旧架构、裸 Client、重复传输和无依据认证零引用；应用/Celery/部署只装配最终对象及明确 Adapter/插件。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-legacy-production-path-removal.md`，同步其阶段号为 Phase 9。

**风险及防止阶段越权的约束:** 缺席扫描按语义和所有者判断，不按 `replay`/`reconciliation` 等词批量删除，避免误伤最终可靠行为。

## 15. Phase 10：旧数据模型与迁移链清理

**Objective:** 最终模型稳定后删除未发布系统的旧 schema/revision，生成唯一可从空库建立系统的 Alembic 基线。

**Authoritative inputs:** Phase 9 零旧路径结果、最终 SQLModel metadata、Alembic 规则、TimescaleDB 必要对象。

**Entry conditions:** Phase 9 退出门禁通过；最终核心、Adapter/插件所需持久化模型稳定；无旧表活动消费者。

**Scope:** 删除旧表/字段/约束/索引和 revision chain；清空开发/测试数据库；使用 Alembic generator 生成随机 revision ID；空库验收。

**Explicit out-of-scope:** 旧数据转换、桥接表、临时回填、downgrade、兼容 schema 和生产历史数据迁移。

**Deliverables:** 单一干净初始基线；metadata/schema/约束/索引/扩展对象一致性结果。

**旧所有者删除或交接清单:** 删除 Runtime/Manifest/Capability/Intent/Effect/Hold/Recovery/Reservation 及旧认证/Provider 持久字段；
删除只验证旧 revision 的测试，标注 successor 或 NONE 理由。

**测试所有权与重量要求:** 只验证 migration 生成物、空库 upgrade 和 metadata 一致性；不保留旧 upgrade/downgrade/回填测试。

**与前后阶段的 atomic handoff:** 只接受 Phase 9 已证明无消费者的模型删除集；向 Phase 11 交付唯一空库基线。

**Exit gate:** `migrations/versions/` 只含最终初始基线及其后真实 revision；空库一次 upgrade head 成功；无旧迁移/兼容断言。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-schema-and-migration-baseline-reset.md`，同步其阶段号为 Phase 10。

**风险及防止阶段越权的约束:** 禁止在模型未稳定前生成基线；禁止因保留开发数据引入兼容迁移。

## 16. Phase 11：最终基线与系统验收

**Objective:** 从干净环境证明核心、Phase 2 HTTP 基础层、当前范围 Adapter/插件、数据库基线、部署装配和缺席门禁共同满足目标架构。

**Authoritative inputs:** SRS、顶层 SPEC、Phase 1–10 退出证据、当前 ADR/合同、插件指南、TODO 与运维文档。

**Entry conditions:** Phase 10 空库基线通过；全部当前范围 Adapter/插件独立验收；所有阶段文档和代码状态一致。

**Scope:** 空库 upgrade；核心 FAST/QUALITY/受影响 HEAVY；Phase 2 transport 合同与生命周期；每个 Adapter/插件独立入口；
部署与 composition root；旧架构、散落 httpx 和无依据认证缺席门禁；当前态文档一致性。

**Explicit out-of-scope:** 未来 SRS 需求、未确认厂商、未交付插件、推测性协议和旧版本兼容。

**Deliverables:** 可审计的最终验收报告、各包测试结果、空库基线结果、部署清单、缺席门禁结果和合并判定。

**旧所有者删除或交接清单:** 不接受新的遗留交接；发现任何旧路径、重复 HTTP 或无依据认证 owner、未验收包都必须退回其来源阶段修正。

**测试所有权与重量要求:** 核心、Phase 2、Adapter、插件分别运行自己的最低稳定层完整断言；真实/验收级 E2E 不回写核心；
不以单一全链路 happy path 替代分层合同与可靠性测试。

**与前后阶段的 atomic handoff:** 接收 Phase 1–10 完整证据；只有全部通过才允许最终结果合并 `develop`，无后续兼容阶段。

**Exit gate:** SPEC §15 全部验收通过；测试计划 Task 7 完成；只有 Phase 2 基础层直接依赖 httpx；当前 Adapter 不含
无真实合同依据的认证；无旧架构/迁移/兼容路径/核心插件污染；最终结果可合并。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-final-architecture-acceptance.md`，同步其阶段号为 Phase 11。

**风险及防止阶段越权的约束:** 禁止用最终验收临时实现缺失能力或放宽门禁；失败必须回到拥有该职责的阶段修正。

## 17. 发现的矛盾与最终裁决

| 矛盾或歧义 | 仓库证据 | 最终裁决 |
| --- | --- | --- |
| 旧总控把 WMS 作为 Phase 2 | Master/WMS 子计划旧编号 | 新 Phase 2 独立为公共 HTTP；WMS 顺延 Phase 3，后续顺延到 Phase 11 |
| “收敛”容易被理解为能力建设阶段必须立即迁移/删除旧代码 | 旧计划把 WMS Client、业务 API、最小平台建设、生产接线和旧 owner 删除混在 Phase 3/4 | Phase 2 暗构建基础传输；Phase 3 只暗构建 WMS Client；Phase 4 暗构建实际平台、Transport Port 与 WMS 转发 RCS Adapter；Phase 5 原子切换 |
| 旧 WMS 子计划把推测性认证放入 Phase 2 | WMS Task 5–8、`canonical_dispatch.py` 与 `sign_wms_hmac_request`，但冻结 WMS outbound 合同无认证要求 | Phase 2 不实现 `AuthStrategy`、凭据、HMAC、Clock、Nonce 或认证 seam；WMS 旧草案标记 Needs re-review，将来只有真实合同明确要求时才修订计划 |
| 现有 `external_http_*` 看似公共但耦合旧平台 | import `operation_registry`、Provider Profile、SystemOutbox/`idempotency_key` | 只复用可证明的 primitive，不把旧 Provider/Outbox 设计提升为目标公共层 |
| 当前既有长期 WMS Client，也有多个每请求 Client | WMS query/effect lane 与 DeviceCommand/旧 Gateway/Outbox | 目标为每外部系统每进程一个 Client；分阶段原子切换，Phase 9 最终零散落 Client |
| 旧 Provider 配置可表达 NONE/HMAC，容易被误读为当前需求 | `provider_profile.py` 与 `external_http_binding.py` | 它们是后续原子切换时删除的旧 owner，不进入 Phase 2 公共合同或部署配置 |
| SPEC §14 曾是九阶段编号，且与 Phase 3/4/5 交接语义冲突 | 复审前 SPEC §14.2–14.3 | 总控改为十一阶段：Phase 3/4 只暗构建新能力，Phase 5 原子切换生产路径并删除旧闭包；SPEC 在 Phase 3 实施前同步该语义 |
| 当前存在旧 `DeviceCommand`/Projection 类型 | 生产代码扫描 | 不等于 Phase 4 新平台能力已完成；Phase 4 只以完整目标对象和新测试验收，生产闭环与旧 owner 删除由 Phase 5 验收 |
| 旧远端 feature 分支包含大量历史实现 | 分支差异远大于当前 develop | 不合并、不 cherry-pick，不作为需求；只有当前 develop 真实代码和权威文档作为实施输入 |
| WMS 初稿中的 method/path 与目标业务消费者尚未逐项批准 | `docs/hardware/wms_rcs_interface_requirements.md` 与当前 WMS 合同 | 保留厂商初稿原文；Phase 3 只实现共享 Client，具体业务开发再逐项确认 wire，不从旧编号或既有实现推定 |

## 18. 自审结果

| 检查项 | 结果 | 说明 |
| --- | --- | --- |
| 占位标记 | 阻断 | PickingTask 业务语义已冻结，但条件候选消费者、来源绑定/事实通知和 inbound command wire 的批准尚未关闭 |
| 兼容设计 | 通过 | Phase 2–4 新能力不接生产流量；旧路径只保留为唯一活动 owner 到 Phase 5；Phase 5 原子切换并删除，不存在 shim、alias、re-export、fallback、双写、双读或旧数据兼容 |
| 重复职责 | 通过 | Transport、WMS Client、具体业务模块、核心可靠对象和生产切换所有权互斥；Phase 2–5 边界单独列明 |
| 测试过重 | 通过 | Phase 2/3/4 各自只测试新 owner；旧测试迁移和运行态验收统一归 Phase 5，不以跨层 happy path 替代分层测试 |
| 未确认推测能力 | 通过 | 不含认证 seam、BASIC/HMAC、动态拦截器、DSL、Service Locator、动态发现、未来协议或空插件 |
| 敏感信息 | 通过 | Phase 2 无凭据与 Secret；日志合同仍禁止 headers/body/query/原始异常文本 |
| 阶段越权 | 通过 | Phase 3/4 明确禁止接线和旧 owner 处置；Phase 5 单独承担原子切换；上一阶段未退出不得启动下一阶段 |
| 当前状态准确性 | 通过 | Phase 1–2 已完成；Phase 3 可进入实施，Phase 4–11 未开始 |

## 19. 总体完成定义

只有同时满足以下条件，本计划才完成：

1. Phase 2 公共传输、WMS/ECS/RCS Adapter、WES 核心和执行插件分别拥有单一职责与独立验收。
2. 只有 Phase 2 基础层直接依赖 httpx；Adapter 只消费已装配 Transport，核心和插件不可见 httpx/认证/凭据/wire 协议。
3. 核心可靠性不变量全部由最终对象测试证明，厂商合同、WMS 业务结果合同和执行映射只由各自包测试证明。
4. 旧生产架构、旧 HTTP owner、无依据认证、旧测试所有者、旧配置、兼容路径和旧 migration chain 全部归零。
5. 最终数据库可以从空库一次建立，不需要旧数据、旧 revision 或转换脚本。
6. 当前态文档、active TODO、代码、测试、schema、部署配置和 composition root 共同指向同一个最终架构。

## 20. Implementation Tasks

Phase 3 已收敛为不含业务 API 的 WMS HTTP Client。Phase 2 依赖已满足，可以直接按 TDD 实施。阶段唯一子计划真源为
`docs/superpowers/plans/2026-08-05-wes-wms-thin-access-convergence.md`。

| 顺序 | 任务 | Surface area | 主要验证 |
| --- | --- | --- | --- |
| 1 | 建立 WMS Client 访问合同测试 | GET/POST、JSON、传输事实和关闭 | FAST 测试先红后绿；不含业务断言 |
| 2 | 实现 `WmsClient` | `client.py` | relative path、一次 send、严格 JSON 编解码、空响应/JSON `null`、非 2xx 和失败事实 |
| 3 | 实现 factory 与公开导出 | `factory.py`、`__init__.py` | 固定 `system_id="wms"`、长期 Client、零裸 httpx |
| 4 | Phase 3 退出验证 | 新 Client、HEAVY selector 显式 NONE 与架构边界 | Phase 2 回归、FAST、selector、Ruff、类型、Import Linter、quality |

具体 WMS 业务 API 不进入本任务表。它们在后续真实业务开发中各自定义 DTO、path、结果解释和合同测试，并复用稳定的
`WmsClient`。

## 21. 工程复审完成摘要

- **Scope：** Phase 3 只暗构建 Axios 式 WMS Client；Phase 4 暗构建新核心、Transport Port 和 WMS 转发 RCS Adapter；Phase 5 原子接线并删除旧 owner。
- **Architecture：** Phase 3 Client 直接消费 Phase 2 Transport，只统一 HTTP/JSON；证据、业务 DTO、业务结果解释和可靠生命周期均不属于 Client。
- **Contract：** 所有业务结果由 WMS 给出；具体 method/path/DTO 随后续业务逐项批准和实现，不阻断 Phase 3。
- **Entry gate：** Phase 2 已完成，Phase 3 当前可进入实施。
- **Code Quality：** 只包含三个生产文件；不引入业务 Port、Gateway 层、生成器或动态 registry。
- **Failure modes：** Client 每次调用只发送一次并保留 Phase 2 传输事实；业务模块决定如何解释响应。
- **Test Review：** FAST 只证明 WMS Client 访问合同，不验证任何业务能力，也不创建 HEAVY 持久化测试。
- **Performance：** 每次调用一次 send、每进程一个长期 Transport；无 retry、cache、分页聚合或数据库事务。
- **Existing / ADD-REUSE-HANDOFF：** REUSE Phase 2；ADD WMS Client；HANDOFF 具体业务 API 到对应后续业务阶段；旧包不迁移。
- **Delivery：** Phase 3 使用一个小 PR 完成 Client、测试、factory 和导出；具体业务 API 不捆绑进入。
- **TODO：** 不为未来分页、认证、运输或对账新增 seam；真实消费者和合同出现时再修订。

## GSTACK REVIEW REPORT

| Review | 本轮状态 | 发现 | 未解决 | 说明 |
| --- | --- | ---: | ---: | --- |
| ENG REVIEW | READY | 0 | 0 | 业务 API 已全部移出 Phase 3；Phase 2 依赖满足 |
| INDEPENDENT REVIEW | SUPERSEDED | 0 | 0 | 旧“33 项完整面”评审基线已被消费者驱动边界替代，不再作为当前放行证据 |
| SERENA REVIEW | CLEAR | 0 | 0 | 参考前端 API Client 和 Phase 2 Transport 后，目标边界已收敛为单一薄封装 |
| SEQUENTIAL REVIEW | READY | 0 | 0 | 具体业务合同不再作为共享 Client 的入口门禁 |
| DESIGN REVIEW | N/A | 0 | 0 | 无 UI/交互范围 |
| DX REVIEW | CLEAR | 0 | 0 | 新增 API 使用固定六步标准并复用 Client，不修改共享核心 |

**VERDICT：Phase 1–2 已完成；Phase 3 已收敛为 Axios 式 WMS HTTP Client，可以进入代码实施。**
