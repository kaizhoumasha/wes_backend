# WES 最小执行架构十一阶段收敛总控实施计划

> **For agentic workers:** 本文只控制阶段顺序、职责边界、原子交接和退出门禁。每个阶段开始前必须另有经批准的详细实施计划；不得直接把本文当作代码实施脚本，也不得在阶段门禁未通过时启动下一阶段。

**Goal:** 按十一个单向依赖阶段，将当前 WES 直接收敛到 SPEC 定义的目标架构；先暗构建 WMS HTTP Client 和
AGV/CTU Transport 基础能力，再通过独立原子切换阶段替换对应生产路径。Device/ECS 与执行插件必须另有独立批准计划，
不得借 Phase 4/5 越权建设。

**Architecture:** Phase 3 `WmsClient` factory 通过 Phase 2 builder，为各运行时/事件循环 owner 装配一个明确生命周期的
`OutboundHttpTransport`。Phase 4 只复用该 Client 暗构建四个工作线搬运方法、`TransportTask`、WMS 转发 RCS/AGV/CTU Adapter、
Transport member-position/result evidence 和位置投影；Phase 5 只原子切换 Transport 消费者并删除 Transport 旧 owner。
DeviceCommand、统一设备 Adapter、设备状态、设备 CALLBACK、ECS 和插件 SDK 不属于 Phase 4/5，必须在 Phase 7/8 前由
独立 Device/ECS 基础能力计划冻结并验收，否则插件阶段保持阻塞。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL/TimescaleDB、Alembic、Celery、
Pydantic 2、HTTPX、Pytest 9、Ruff、Bandit、Import Linter、Jenkins。

**Status:** In progress — Phase 1–3 已完成；Phase 3 已交付 Axios 式 WMS HTTP Client；Phase 4 已完成暗构建和后端 QA 验收；
Phase 5–11 尚未开始。

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
- Phase 2–4 的新基础层、WMS Client 与 Transport 能力均采用暗构建：源码和新测试可以与旧实现共存，但不得注册到旧
  Composition Root、不得接收生产流量、不得 shadow write，也不得提供新旧选择开关。WMS Client 与 Transport 的直接替代
  统一发生在 Phase 5；Device/ECS 不参与本次切换。每次原子切换必须删除该次被替代的直接旧 owner。
- WMS 是业务单据、库存、主数据、业务授权和全局仓内位置权威；WES 只拥有工作线本地执行事实；
  ECS/PLC 拥有设备物理动作和安全互锁。
- 外部 HTTP 公共层只发送一次并返回传输事实，不拥有自动重试、业务拒绝、Circuit Breaker 决策、
  Outbox/`TransportTask`/`WmsConfirmation` 生命周期、工作线推进或厂商 Payload 映射。
- WMS/RCS Adapter 每次调用只发送一次，不拥有 retry/backoff 配置；`TransportTask` 只能按批准合同保留原 identity、版本和
  Payload 进行安全收敛，否则进入人工对账。Device/ECS 重试语义不在 Phase 4/5 定义。
- 每个外部系统由各运行时/事件循环 owner 持有一个明确生命周期的 Client；禁止跨事件循环共享、每次请求创建 Client，
  也禁止全局万能 Client。
- builder 只完成 base URL、Timeout、连接池和 Transport 生命周期装配；业务 Adapter 只接收该 Transport，
  不接收裸 `httpx.AsyncClient`，也不管理连接池或通用传输异常。
- 当前已批准 outbound 合同均未要求认证，Phase 2 和设备统一接口不建设认证策略、凭据解析、HMAC、Clock、Nonce 或预留接口。
  纯局域网访问控制由部署边界负责；不得因单一供应商要求在 WES 中增加私有认证分支。
- Inbound callback 认证保持独立 API 边界所有权，不得因同为 HTTP 而与 Phase 2 outbound 传输合并。
- 当前产品部署在隔离局域网，不建设 HMAC、nonce、clock、凭据、IP allowlist 或认证扩展 seam。WMS inbound API 在具体
  业务开发时由其 ingress owner 定义，不属于 Phase 3。
- 核心、供应商一致性、WMS Adapter 与插件测试所有权严格隔离：核心验证基础能力与统一公共协议，供应商验收验证 ECS/网关
  符合白皮书和设备合同附录，WMS Adapter 验证业务结果合同，插件验证执行 Decision 和对象推进；不得跨层复制或互相替代。
- 执行插件包独立构建、测试和显式装配；客户镜像只安装明确清单，不建设运行时扫描或私有包 registry。产品内唯一 WMS
  北向 Adapter 是 `src/app/wms_adapter/` 下的应用 ACL；设备供应商直接适配统一接口，不在 WES 建立私有 Adapter 包。
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
| Phase 3 | WES 最小平台能力建设 | Phase 4 | AGV/CTU Transport 基础能力建设 | 只暗构建四个搬运方法、TransportTask、WMS 转发 Adapter、成员位置/终态证据和位置投影 |
| 无 | 无 | Phase 5 | Transport 原子切换与旧所有者删除 | 只迁移 Transport 消费者和生产装配，不涉及 Device/ECS |
| Phase 4 | 核心测试承接与平台基线验收 | Phase 6 | Transport 测试承接与基线验收 | 只承接 Phase 4 Transport 最终对象 |
| Phase 5 | 粗分机参考插件优化 | Phase 7 | 粗分机参考插件优化 | Device/ECS 独立计划未批准前阻塞，不消费 Phase 4 设备能力 |
| Phase 6 | 分拣执行插件组优化 | Phase 8 | 分拣执行插件组优化 | 依赖独立 Device/ECS 能力及 Phase 7 已验收模式 |
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
| `docs/plugin_development_guide.md` | 曾要求每个供应商交付 WES Adapter 包 | 已同步为供应商实现统一接口、插件只拥有业务执行映射 |
| `docs/integration/workline_device_error_code_standardization.md` | 曾要求 WES Adapter 映射供应商原始错误 | 已同步为供应商 ECS/网关按设备合同附录输出统一错误包络 |
| `docs/integration/third_party_integration_whitepaper.md` | 1.1 曾被整体归档，导致供应商顶层合同缺位 | 恢复为长期生效的 2.3 统一接口真源；1.1 继续留在项目外历史归档 |
| `docs/devops/prod-release-deploy.md` 开头 | 仍以旧编号指向最终 Alembic 基线，并记录临时 Provider Profile 部署输入 | 本轮只读；对应新编号为 Phase 10，Phase 5 切换 WMS 配置时同步 Runbook |
| `TODOS.md` | 无阶段编号；依赖最终对象的触发条件仍成立 | 无需修改，不新增重复调度项 |

上述“本轮只读”项不是新的实施入口。SPEC 继续定义目标业务边界；指南和 TODO 不得反向改变本计划的
Transport/Adapter/核心所有权。

## 4. 当前分支与实施状态

| 证据 | 当前事实 | 裁决 |
| --- | --- | --- |
| `git status` | 当前 Phase 4 实施分支已完成 Transport 暗构建和后端 QA | 尚未合入 `develop`，也未接入生产路径 |
| `28eb99d9` / PR #100 | Phase 1 架构与测试治理已合入 | Phase 1 完成，但测试计划延后义务未整体完成 |
| `src/app/wms_integration/` | 仍为 54 个生产文件，Provider/Profile、Registry、QUERY、Effect/status/evidence 混合存在 | Phase 3 不复用其设计；旧包只是待替代的临时所有者 |
| `src/app/sys/external_http_*` 与 `canonical_dispatch.py` | 已有 typed transport fact、凭据解析、NONE/HMAC、bounded response 的部分能力，但耦合 Provider Profile/SystemOutbox/WMS operation | 仅 transport fact 作为行为证据；认证相关能力无真实 outbound 合同依据，不进入 Phase 2 |
| 多处 `httpx.AsyncClient()` | DeviceCommand、旧 Outbox、WMS runtime、旧 Gateway 等仍自行创建 Client | Phase 5 只切换 WMS/Transport；Device/ECS 裸 Client 由独立计划处理，不借 Phase 4/5 顺带清理 |
| 目标对象扫描 | 已有 `TransportTask`、Transport member-position/result evidence 和 Transport 位置投影 | Phase 4 暗构建完成；旧 Effect/Outbox 仍由 Phase 5 原子切换处置 |
| 当前规划增量 | Phase 3 已删除业务 Port、operation 矩阵和单项业务门禁，只保留 WMS HTTP Client 与开发示例 | Phase 3 已完成实施与验收 |
| 其他旧 feature 分支 | 大幅落后或已被 develop 取代，包含旧 Manifest/Runtime 语义 | 只作 Git 历史，不作为实施输入 |

阶段状态：Phase 1–3 已完成，Phase 4 已完成暗构建和后端 QA，Phase 5–11 均未开始。

## 5. 总控依赖模型

```text
Phase 1  测试治理基线（已完成）
   ↓
Phase 2  外部 Outbound HTTP 传输基础能力（已完成）
   ↓
Phase 3  WMS HTTP Client 薄封装
   ↓
Phase 4  AGV/CTU Transport 基础能力
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

| 责任 | Phase 2：Outbound HTTP | Phase 3：WMS Client 暗构建 | Phase 4：Transport 暗构建 | Phase 5：Transport 切换 |
| --- | --- | --- | --- | --- |
| 生产装配与调用切换 | 不接入真实 Adapter | 不修改旧 Composition Root | 不修改旧 Composition Root | 原子切换已明确列出且已验收的目标消费者，并删除其旧 owner |
| Client 生命周期、连接池、Timeout、base URL | 唯一拥有 Transport | 通过 factory 构造并公开长期 `WmsClient` | 复用 Client | 装配并管理 Client 生命周期；Transport 不外露 |
| HTTP method primitive | 拥有当前最小集合，不解释业务 | 提供 request/get/post 薄方法 | 为真实业务选择固定 method | 不改变 method 合同 |
| WMS 业务 method/path、wire DTO、拒绝码 | 不拥有 | 不拥有 | 只拥有 Transport submit/member-position/result wire | 只切换 Transport 消费者 |
| WMS 转发 RCS method/path、wire DTO、提交 ACK、逐箱位置与异步终态 | 不拥有 | 不拥有 | Transport Adapter 唯一目标 owner | 只接线，不改变合同 |
| WMS outbound 认证 | 不拥有、不预留 | 当前仅 `NONE`，不提供认证字段或 seam | 不可见 | 删除旧 HMAC/Profile/fallback 配置 |
| 外部调用 evidence 与 breaker | 不拥有 | 不拥有；ACL 无状态 | 只保存 Transport submit/member-position/result 事实；不建 breaker | 删除 Transport 旧 evidence/breaker 分支 |
| WMS 查询、业务决策、确认调用 | 不拥有 | 只提供 Client，不提供业务方法 | 不拥有 | 不进入 Phase 5 |
| WMS 转发搬运调用 | 不拥有 | 不拥有 | `Transport Port` + WMS 转发 RCS Adapter | 切换真实消费者到新 Transport Port |
| Device/ECS 命令、状态与回调 | 只提供通用单次发送 primitive | 不拥有 | 不拥有 | 不切换；等待独立 Device/ECS 计划 |
| `TransportTask` 生命周期 | 不拥有 | 不拥有 | 唯一目标 owner | 接管 Transport 生产流量并删除旧可靠闭包 |
| Transport evidence ingress | 不拥有 | 不修改旧入口 | 固定 member-position/result evidence 与应用端口 | 原子切换 Transport 事实入口 |
| 旧实现和旧测试 | 不修改 | 不修改、不作为新测试 oracle | 不修改、不登记后续处置 | Phase 5 自行建立 successor/NONE 后处置并删除 |

### Outbound HTTP 认证裁决

| 所有者 | 当前必须拥有 | 明确不得拥有 |
| --- | --- | --- |
| WMS Composition Root | 向 factory 传入 base URL/Timeout 并管理返回的 `WmsClient` 生命周期 | 传入 session factory、运行时扫描、Service Locator、推测性认证配置 |
| Phase 2 基础层 | 无认证职责 | `AuthStrategy`、凭据解析、HMAC、Clock、Nonce、认证枚举或未来扩展 seam |
| WMS Client 包（Phase 3） | 当前 outbound 无认证；只统一 HTTP/JSON 访问 | 业务 DTO、业务结果解释、无合同依据的 canonical/Header/签名、裸 httpx、连接池 |
| Device/ECS Adapter | Phase 4/5 不建设、不接线 | 借 Transport 阶段预建任何设备协议或认证能力 |
| 部署配置 | 当前不提供 outbound 认证键 | 原始 Secret、任意 Header/签名表达式、未获合同支持的认证选项 |
| WES 核心可靠对象 | 只观察类型化端口结果并管理可靠性生命周期 | httpx、认证方案、签名 Header、credential reference |
| 执行插件 | 无认证职责 | HTTP Client、认证配置、凭据、厂商原始协议、WMS 业务裁决 |

## 6. Phase 1：测试治理基线

**Objective:** 冻结目标 SPEC、实施基线、核心/供应商一致性/WMS Adapter/插件测试所有权与 FAST/QUALITY/HEAVY 重量边界。

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
不包含认证 seam 的架构门禁；供 Phase 3/4 消费的公开合同和旧消费者 HANDOFF 清单。

**旧所有者删除或交接清单:** Phase 2 不修改或删除 `sys/external_http_*`、WMS Transport、旧 Outbox sender、
DeviceCommand Client 及其生产调用者，只登记当前 owner。Transport 专属 Effect/status/Outbox 可靠生命周期由 Phase 4
建立目标能力，Phase 5 自行建立 successor/NONE 后原子切换并删除。DeviceCommand、设备状态、统一设备 Adapter 和 ECS 旧 owner 不进入 Phase 4/5，
必须由独立 Device/ECS 计划重新扫描、建立 successor 并切换；Phase 9 只验证无遗漏，不作为默认归宿。

**测试所有权与重量要求:** 只用 `httpx.MockTransport`、测试内 local fake 和纯单元测试；不访问真实外部系统，
不使用 sleep，不建立大规模 E2E。只验证框架无关合同、生命周期、请求装配、受限响应、传输事实分类、取消传播、
已知异常与意外异常边界及日志脱敏。厂商认证/Header/DTO、业务拒绝、重试/终态/恢复不进入本阶段测试。

**与前后阶段的 handoff:** Phase 2 向 Phase 3/4 交付同一 Transport 合同，但不激活真实业务集成。Phase 3 暗构建 WMS
Client；Phase 4 只复用该 Client 暗构建 WMS Transport Adapter；Phase 5 首次装配并删除 Transport 旧 Client/sender。
Device/ECS 后续可以复用 Phase 2 Transport，但必须由独立计划拥有，不回填 Phase 4/5。

**Exit gate:** builder 只返回可直接使用的 Transport；公开合同不暴露 httpx 类型；GET/POST 走同一条单次发送路径且无
WMS 条件分支；公共层单次发送且无重试/业务解释；
不捕获未知编程异常，不存在认证、credential、HMAC、Clock、Nonce 或生产 fake；全部轻量测试和架构门禁通过；
`src/core/outbound_http/` 之外的既有生产文件无 Phase 2 实施修改，WMS/RCS/ECS 生产模块和 Composition Root 零新包
import；`src/core/bounded_http_response.py` 内容与路径保持不变；Phase 3 可在不复制 transport 语义的前提下开始。

**详细计划归档:** GET/POST 基线已完成且当前无待实施修订；Phase 2 计划已移至项目外
`../archive_docs/wes_backend/docs/superpowers/plans/2026-08-04-wes-outbound-http-transport-convergence.md`，不再承担当前执行入口。

**风险及防止阶段越权的约束:** 最大风险是把既有 SystemOutbox/Provider Profile 整体提升为公共层、提前修改真实
Adapter/Composition Root，或建设认证/拦截器扩展平台。子计划必须以“新包单一实现、稳定 primitive 只读复用、
旧消费者精确 HANDOFF”为准；不得搬迁旧模块、复制旧源码、继承旧生命周期或提前执行 Phase 5 的切换。

## 8. Phase 3：WMS HTTP Client 薄封装

**Objective:** 在不触碰当前生产路径的前提下，消费 Phase 2 Transport，暗构建一个类似前端 Axios 的长期
`WmsClient`，统一 WMS origin、GET/POST、query、headers、JSON 编解码、传输事实和资源关闭。

**Authoritative inputs:** Phase 2 公开合同、当前 WMS Client 使用合同和 `src/app/wms_adapter/` 实现。前端 `src/api/client.ts` 与
`src/api/contract/client.ts` 只作为职责形态参考。当前 `src/app/wms_integration/` 只用于识别未来删除边界，不是新实现模板。

**Entry conditions:** Phase 2 基线已通过退出门禁，所需 Transport、request/result 和 builder 已存在。具体 WMS 业务 API、
消费者、wire、DTO 和业务尺寸预算不属于 Phase 3 入口条件；Phase 3 已完成实施。

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

**当前实施真源:** 使用 `docs/contracts/wms-northbound-interaction-contract.md`与 `src/app/wms_adapter/`。
Phase 3 子计划已完成并移至项目外 `../archive_docs/wes_backend/docs/superpowers/plans/`。

**风险及防止阶段越权的约束:** 最大风险是把未来业务 API、业务模型或通用集成平台提前塞入 Client。任何业务字段、
path 和结果解释都必须留到真实业务开发。

## 9. Phase 4：AGV/CTU Transport 基础能力建设

**Objective:** 在不触碰当前生产路径的前提下，暗构建 AGV 整架搬运与 CTU 架内料箱搬运的最小可靠履约闭环：持久化
不可变搬运请求、经 WMS 转发提交 RCS、可靠接收 CTU 逐箱位置事实和异步终态，并更新本地位置投影。

**Authoritative inputs:** Phase 3 `WmsClient`、`docs/contracts/transport-fulfillment-contract.md`、
`docs/contracts/wms-async-callback-envelope-contract.md`、WMS 北向合同和入站验证原则。
第三方设备统一接口白皮书和设备命令合同不属于 Phase 4 输入。

**Entry conditions:** Phase 3 退出门禁通过；四类搬运请求、Transport submit ACK、CTU member-position 与异步 TransportResult wire
已由 WMS/WES 双方批准，其中 `move_bins()` 单次最多 4 个成员，`exchange_bins()` 明确支持一次 1～2 个交换对的单任务协调执行。
Phase 4 只依赖自身和已完成的 Phase 3；Phase 5 的生产接线、consumer 与 successor/NONE 不构成本阶段前置条件。

**Scope:** 面向工作线插件的 `move_rack()`、`rotate_rack()`、`move_bins()`、`exchange_bins()`；按首个真实消费者需要为
Phase 3 `WmsClient` 增加逐请求请求体上限和响应预算透传，但不加入 Transport 业务语义；内部 `TransportTask`、
冻结成员、Transport evidence、位置投影、bin/rack 运输资源活动绑定、Transport claim/fencing、六态与闭集对账原因、
`Transport Port`、WMS 转发 RCS Adapter、operation-scoped Transport evidence handler、统一 `TransportOutcome` 和未注册的暗 Composition。

**Explicit out-of-scope:** DeviceCommand、设备状态、统一设备 Adapter、设备 CALLBACK、ECS、WorkLine/LineRunEpoch、
Material/Bin Execution、插件 SDK、Decision/EvidenceProcessor、WmsConfirmation、PickingTask 业务、WES 直连 RCS/AGV/CTU、
车辆/路径/交通策略、动态 registry、Service Locator、Transport 查询/取消/轮询和自动物理恢复。

**Deliverables:** `WmsClient` 最小逐请求字节预算扩展；四个简单公共方法、`TransportHandle`、统一 `TransportOutcome`；
TransportTask 六态与闭集对账原因；
AGV Rack Move/Rotate、CTU Bin Move/Exchange 四类请求、闭集 locator 及禁止混装约束；`exchange_bins()` 一次 1～2 个交换对且
不拆成普通 MOVE；WMS Transport Adapter；ACK-after-persist 的 member-position/result evidence；逐箱 pick/place 单调位置更新；
锁任务后应用的唯一 reducer；bin/rack 运输资源活动绑定；
bulk 位置投影；PostgreSQL claim/唯一约束/事务测试；未注册生产路径的暗 Composition 和四个内部后台批处理入口。

**Phase 5 后续责任（不属于 Phase 4 交付物）:** Phase 5 启动时重新扫描当前 Transport 生产引用图，自行建立并批准
successor/NONE 清单后再执行原子切换和旧所有者删除。Phase 4 不预登记、不维护该清单。Device/ECS 旧 owner 由独立
Device/ECS 计划处理。

**测试所有权与重量要求:** Phase 4 只新增四个公共搬运方法、Transport 核心、WMS Transport Adapter、Transport evidence ingress 和
PostgreSQL integration 测试。测试不得导入 PickingTask、工作线插件、设备 DTO、供应商协议或真实业务 happy path；
WMS/RCS/AGV/CTU 真实行为继续由外部联调验收拥有。

**与前后阶段的 handoff:** 接收 Phase 3 `WmsClient`；交付 TransportTask、WMS Transport Adapter、Transport evidence handler、
暗 Composition 和四个内部批处理入口。Phase 4 不执行生产切换，也不等待 Phase 5 规划完成。

**Exit gate:** 四个公共方法及 Transport submit/member-position/result 合同已批准；同一对象无重叠非终态任务；Task → submit ACK →
member position → TransportResult → task/projection 的暗闭环通过；倒序逐箱事实不能回退位置，并发结果不能覆盖已接受终态；
核心无 `httpx`、ECS、DeviceCommand、PickingTask 或旧 Runtime/Effect import；新 route/task/Adapter 未注册到生产入口；
四个内部批处理入口可由测试显式调用，且不存在 Phase 5 反向依赖。

**实施状态:** Phase 4 已按详细计划完成暗构建；尚未注册生产 route、Celery task、beat、worker hook 或工作线消费者。
当前验收与后续生产切换仍分别以详细计划和 Phase 5 独立任务为准。

**当前实施真源:** `docs/superpowers/plans/2026-08-08-wes-minimal-platform-capabilities.md`。

**风险及防止阶段越权的约束:** 禁止把 ECS/Device 或通用执行平台重新包装进 Transport；禁止直连 RCS/AGV/CTU；
禁止在 Phase 4 接线、改写旧实现、删除旧测试或增加兼容桥。

## 10. Phase 5：新旧能力原子切换与旧所有者删除

**Objective:** 在 Phase 4 Transport 能力独立验收后，原子迁移 Transport 消费者、结果入口和生产装配，并删除被替代的
Transport 旧实现、旧配置和旧测试，使生产运行态只剩唯一 Transport 路径。

**Authoritative inputs:** Phase 3 `WmsClient`；Phase 4 `TransportTask`、Transport Port、WMS Transport Adapter、
Transport evidence handler、暗装配和四个内部批处理入口；Phase 5 启动时重新扫描的当前 Transport 生产引用图。

**Entry conditions:** Phase 4 退出门禁通过；所有 Transport producer、consumer、callback、配置、任务路由、数据库对象和
测试 owner 已逐项映射到唯一 successor 或 `NONE`；不存在需要迁移的发布数据。

**Scope:** 装配 Phase 3 `WmsClient` 与 Phase 4 Transport；切换 Transport Port 消费者和 Transport evidence ingress；
删除 Transport 专属 Effect/status/Outbox、callback hint、旧 result callback、配置、任务路由、旧 schema 和对应测试。

**Explicit out-of-scope:** WMS 查询/业务决策/确认、普通 WMS Event、DeviceCommand、设备状态、设备 CALLBACK、ECS、
统一设备 Adapter、执行插件、兼容 shim、双写/双读、旧数据转换和重新定义 Phase 4 合同。

**Deliverables:** 唯一生产 `WmsClient` 使用入口、唯一 Transport Port/WMS Adapter、唯一 `TransportTask` 生命周期、
唯一 Transport evidence ingress，以及完成 successor/NONE 的 Transport 测试树。

**旧所有者删除规则:** 目标测试先通过，再在同一原子提交序列中接线并删除直接旧 owner；不得复制旧源码、保留 re-export、
alias、fallback、shadow write 或历史表读取。共享模块只删除 Transport 专属分支。

**测试所有权与重量要求:** 先运行 Phase 4 权威测试，再改写生产接线测试，最后按 successor/NONE 删除旧断言；
受影响 integration/e2e/resilience/mock 显式运行并精确更新 HEAVY mapping。

**与前后阶段的 atomic handoff:** 按“目标测试绿 → 接线 → 删除 Transport 旧 owner → 全量验证”串行完成；
向 Phase 6 交付唯一 Transport 生产路径。任何可运行双轨中间态不得合并。

**Exit gate:** 当前生产 Composition Root 只引用 Phase 3 `WmsClient` 和 Phase 4 Transport；全仓零 Transport 旧
Effect/status/Outbox/callback hint/result callback；对应测试完成 successor/NONE；FAST、QUALITY、精确 HEAVY、Ruff、
Bandit、Import Linter、GitNexus detect changes 和运行态 smoke 全部通过。Device/ECS 现状不作为本阶段验收项。

**需要单独编写的子计划:** 启动前根据 Phase 4 实际交付重新扫描 Transport 引用图，编写并批准
`docs/superpowers/plans/2026-08-06-wes-atomic-capability-cutover.md`。

**风险及防止阶段越权的约束:** 只切换 Transport。发现 Device/ECS 或普通 WMS 业务耦合时必须拆出并回到对应 owner，
不得借 Phase 5 扩大删除范围。

## 11. Phase 6：核心测试承接与平台基线验收

**Objective:** 审计 Phase 4/5 Transport 最终路径的测试所有权，完成残余 Transport 旧测试 successor/NONE 处置，
证明 Transport 基础能力不依赖 PickingTask、WorkLine 插件或 Device/ECS。

**Authoritative inputs:** Phase 4/5 Transport 最终对象、`tests/README.md` 和 HEAVY selector 真源。

**Entry conditions:** Phase 5 退出门禁通过；Transport 已成为唯一生产路径；剩余 Transport 旧测试映射明确。

**Scope:** 审计 Transport FAST、Adapter contract 和 PostgreSQL integration；处置残余混合 Transport 旧测试；
强化 Transport/业务/Device/ECS 测试所有权门禁。

**Explicit out-of-scope:** 通用执行平台、WorkLine 语义、DeviceCommand、ECS、供应商协议和执行插件验收。

**Deliverables:** Transport 基线、测试矩阵、successor/NONE 审计和跨边界缺席门禁。

**测试所有权与重量要求:** FAST 不访问真实数据库/HTTP/Celery；PostgreSQL claim/事务使用精确 integration；
不得以厂商或业务 E2E 证明 Transport 基础可靠性。

**与前后阶段的 atomic handoff:** 接收 Phase 5 唯一 Transport 路径；Device/ECS 与插件阶段必须另行满足自己的前置条件。

**Exit gate:** Transport 测试所有权唯一，核心测试无具体业务/设备/厂商行为，残余旧 Transport 测试全部处置。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-platform-baseline-acceptance.md`，同步其阶段号为 Phase 6。

**风险及防止阶段越权的约束:** 禁止为绿灯删除未承接不变量；禁止把插件测试改名搬入核心 contracts/runtime/HEAVY。

## 12. Phase 7：粗分机参考插件优化

**Objective:** 在独立 Device/ECS 基础能力已批准、实施并切换为唯一生产路径后，以粗分机交付首个真实执行插件、
设备合同附录、endpoint/device 绑定和供应商一致性验收。

**Authoritative inputs:** 顶层 SPEC §11.1、第三方设备统一接口白皮书、独立 Device/ECS 基础能力计划及验收证据、
粗分机真实拓扑、供应商原始资料、WMS 操作清单和 Phase 4/5 Transport 能力。

**Entry conditions:** Phase 6 Transport 基线通过；独立 Device/ECS 基础能力已完成，且不回填 Phase 4/5；粗分机供应商资料
完整；设备 `task_type`、`event_type`、字段闭集、错误和时限已形成可批准附录。

**Scope:** 粗分机设备合同附录、endpoint/device 绑定、供应商一致性验收、独立插件包；粗分 WMS 业务结果到插件执行动作的
映射；设备长命令、NG 执行、WMS 决策/查询/确认和 Phase 4 Transport Port 消费。设备 HTTP 只复用独立 Device/ECS 计划
交付的唯一生产 Adapter。

**Explicit out-of-scope:** 其他分拣线、第二个 WES 设备 HTTP Adapter、公共 HTTP Client、凭据、通用认证配置、WMS wire 合同重定义、通用插件模板。

**Deliverables:** 获批粗分机设备合同附录、endpoint/device 绑定、通过一致性验收的供应商 ECS/网关、可独立构建/测试的
粗分机插件、显式插件 Composition Root 绑定和真实业务验收结果；不新增 WES HTTP Adapter。

**旧所有者删除或交接清单:** 设备旧 sender 必须由独立 Device/ECS 切换计划删除；本阶段不得恢复核心/插件中的供应商私有 DTO、HTTP Client、
HMAC 工具、路径或映射副本。插件交付时删除旧粗分业务代码、配置和测试，不保留 alias/fallback。

**2.3 运行时对齐 TODO（本 Phase 交付项）:** 本阶段必须按 TDD 在独立计划交付的唯一生产设备路径上完成粗分机合同附录、
endpoint/device 绑定和供应商一致性验收：命令和状态路径
固定且不可按供应商覆盖；命令、结果、事件和状态都携带并核验 `contract_key`/`contract_version`，状态查询强制使用
`device_code`、禁止缓存并校验观察年龄，将维护态归入 `mode` 而非 `status`。每个独立命令资源 `device_code` 最多一个已接纳且未终态命令，
WES 只在 `AUTO + IDLE` 且无活动命令时发送，ECS 原子接纳并以 `429` 拒绝同一 `device_code` 竞争；不同 `device_code` 仍可对象级并行。命令 ACK
校验公共响应包络，区分明确未接纳与 delivery unknown；稳定身份始终绑定同一规范化载荷，合同修正改变摘要时仅在明确未接纳
前提下换新身份。结果和事件回调使用部署级唯一 `source_event_id`，每个 `command_code` 只接纳一个终态结果；未知命令、版本
不匹配、重复和冲突按白皮书失败关闭。响应追踪当前 HTTP 请求；发送方时间戳仅作不可变证据，不单独作为排序或丢弃权威。
设备回调不再依赖应用层 Token/权限分支；共享 MOCK 同步使用相同公共包络。旧 `current_command_id`、`priority`/`timeout`
wire 字段、`event_id`、可配置路径、私有认证、缺失事件身份和兼容字段全部删除，不建立双路径。获批设备合同附录必须具有合同
版本、设备实例、ECS/网关或固件、配置和 `LineRunEpoch` 绑定；行为变化必须重新验收并创建新 Epoch，不得在活动 Epoch 内
静默切换。该设备绑定与插件交付完成后同步更新仍标记为 implementation baseline 的 `docs/workline_diagnostics_quickstart.md` 与
`docs/contracts/observability-contract.md`；若其内容已被当前合同替代，则按项目规则移出项目目录归档，不保留转发页或重复真源。

**测试所有权与重量要求:** 独立 Device/ECS 基础能力测试拥有固定路径、公共包络、DTO 校验、错误映射、身份和
ACK/CALLBACK；供应商一致性验收拥有设备
附录字段与 ECS/网关行为；WMS Adapter 包拥有业务结果合同；插件包拥有执行 Decision 和对象推进。五者不得复制场景，
供应商验收和插件测试均不进入核心默认 pytest。

**与前后阶段的 atomic handoff:** 消费独立 Device/ECS 基础能力和 Phase 4/5 Transport；发现公共设备能力缺口时回到
Device/ECS owner 修订，否则规则留在设备合同附录或粗分插件。本阶段不得实现第二个 Adapter。

**Exit gate:** 供应商 ECS/网关通过一致性验收，endpoint/device 绑定明确，插件独立安装、构建和测试；业务闭环仅由插件拥有；
全部设备 HTTP 调用仍经独立 Device/ECS 计划交付的唯一生产 Adapter，核心无供应商特殊分支。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md`，同步其阶段号为 Phase 7。

**风险及防止阶段越权的约束:** 禁止插件访问 Transport/HTTP/认证/凭据；禁止因供应商内部协议不同而修改 WES 固定路径、
公共包络或增加兼容 Adapter。

## 13. Phase 8：分拣执行插件组优化

**Objective:** 按真实工作线和获批设备合同附录分别交付自动分拣、人工分拣、满箱交换和复杂出库，不建设通用分拣工作流。

**Authoritative inputs:** 顶层 SPEC §11.2–§12、第三方设备统一接口白皮书、每条线真实拓扑与供应商原始资料、Phase 7
复审结果、独立 Device/ECS 基础能力和 Phase 4/5 Transport。

**Entry conditions:** Phase 7 退出门禁通过；每个实际插件、设备合同附录和部署组合明确；各详细计划获批。

**Scope:** 自动/人工/满箱交换/复杂出库执行插件；所需设备合同附录、endpoint/device 绑定和供应商一致性验收；复用既有
独立 Device/ECS 计划交付的生产 Adapter 与 Phase 4 Transport Port；WMS 业务结果映射、同线进出、NG 执行、即时 PUT、
CTU 批次和 WMS 来源权威。

**Explicit out-of-scope:** `SorterCorridor`、库存权威、动态发现、统一厂商认证三选一、第二个 WES 设备 HTTP Adapter、
公共 HTTP Client、预建 BASIC、通用工作流 DSL。

**Deliverables:** 每个真实设备的获批合同附录、endpoint/device 绑定和供应商一致性验收；每个插件独立包、fixture、测试、
构建产物和显式装配；客户镜像清单；不新增 WES HTTP Adapter。

**旧所有者删除或交接清单:** 设备旧 sender 已由独立 Device/ECS 切换计划删除；本阶段不得新增供应商私有裸 Client、重复连接池/HMAC/路径或
协议映射。每个插件交付时删除旧业务代码、配置和测试；删除包时同步移除 workspace、镜像和插件 Composition Root 绑定。

**测试所有权与重量要求:** 供应商一致性验收独立拥有设备附录/集成/异常/恢复场景；插件独立拥有业务单元/集成/E2E/
韧性/并发/负载；核心只验证统一公共协议和通用机制，Phase 2 只验证 Transport。

**与前后阶段的 atomic handoff:** 消费独立 Device/ECS 基础能力、Phase 4/5 Transport 和 Phase 7 已验收模式；全部实际
交付包完成后向 Phase 9 提交零散旧所有者清单。本阶段不得直接消费 Phase 2 Transport 或实现第二个 Adapter。

**Exit gate:** 两条自动线和两条人工线使用同一插件的不同配置实例；全部 endpoint/device 绑定明确，供应商与插件分别通过；
全部设备 HTTP 调用仍经独立 Device/ECS 计划交付的唯一生产 Adapter；核心无插件 import/fixture/供应商名称分支；
无动态平台扩张。

**需要单独编写的子计划:** 分别编写并批准：
`docs/superpowers/plans/2026-08-03-automatic-sorter-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-manual-sorter-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-full-bin-exchange-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-complex-outbound-plugin-convergence.md`。若真实现场证明应合并为同一部署插件，
先修订本阶段清单，不预建空包。

**风险及防止阶段越权的约束:** 禁止从粗分计划复制改名；禁止把供应商私有认证或协议差异引入 WES，现场差异必须由
供应商 ECS/网关在统一接口边界内消化。

## 14. Phase 9：旧平台代码最终闭环清理

**Objective:** 在 Phase 2 已交付唯一新基础层、Phase 3–8 的目标替代均已完成的基础上清除跨阶段残留，证明生产态只有
最小执行架构和唯一 HTTP 基础层。

**Authoritative inputs:** 各阶段删除/交接清单、GitNexus 变更影响、全仓 import/语义扫描、当前装配与部署配置。

**Entry conditions:** 当前范围内全部 WMS/RCS Adapter、设备统一接口、供应商一致性和插件交付完成；任何仍活动的旧所有者
都有明确 successor，不存在未完成原子交接。

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

**Exit gate:** 机器门禁证明旧架构、裸 Client、重复传输和无依据认证零引用；应用/Celery/部署只装配最终对象、WMS/RCS
Adapter、设备统一接口和明确插件。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-legacy-production-path-removal.md`，同步其阶段号为 Phase 9。

**风险及防止阶段越权的约束:** 缺席扫描按语义和所有者判断，不按 `replay`/`reconciliation` 等词批量删除，避免误伤最终可靠行为。

## 15. Phase 10：旧数据模型与迁移链清理

**Objective:** 最终模型稳定后删除未发布系统的旧 schema/revision，生成唯一可从空库建立系统的 Alembic 基线。

**Authoritative inputs:** Phase 9 零旧路径结果、最终 SQLModel metadata、Alembic 规则、TimescaleDB 必要对象。

**Entry conditions:** Phase 9 退出门禁通过；最终核心、WMS/RCS Adapter、设备统一接口和插件所需持久化模型稳定；无旧表活动消费者。

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

**Objective:** 从干净环境证明核心、Phase 2 HTTP 基础层、当前范围 WMS/RCS Adapter、设备统一接口、供应商一致性、插件、
数据库基线、部署装配和缺席门禁共同满足目标架构。

**Authoritative inputs:** SRS、顶层 SPEC、Phase 1–10 退出证据、当前 ADR/合同、插件指南、TODO 与运维文档。

**Entry conditions:** Phase 10 空库基线通过；全部当前范围 WMS/RCS Adapter、供应商 ECS/网关和插件独立验收；所有阶段
文档和代码状态一致。

**Scope:** 空库 upgrade；核心 FAST/QUALITY/受影响 HEAVY；Phase 2 Transport 合同与生命周期；WMS/RCS Adapter、设备统一
接口、供应商一致性和每个插件独立入口；
部署与 composition root；旧架构、散落 httpx 和无依据认证缺席门禁；当前态文档一致性。

**Explicit out-of-scope:** 未来 SRS 需求、未确认厂商、未交付插件、推测性协议和旧版本兼容。

**Deliverables:** 可审计的最终验收报告、各包测试结果、空库基线结果、部署清单、缺席门禁结果和合并判定。

**旧所有者删除或交接清单:** 不接受新的遗留交接；发现任何旧路径、重复 HTTP 或无依据认证 owner、未验收包都必须退回其来源阶段修正。

**测试所有权与重量要求:** 核心、Phase 2、WMS/RCS Adapter、供应商一致性和插件分别运行自己的最低稳定层完整断言；
真实/验收级 E2E 不回写核心；
不以单一全链路 happy path 替代分层合同与可靠性测试。

**与前后阶段的 atomic handoff:** 接收 Phase 1–10 完整证据；只有全部通过才允许最终结果合并 `develop`，无后续兼容阶段。

**Exit gate:** SPEC §15 全部验收通过；测试计划 Task 7 完成；只有 Phase 2 基础层直接依赖 httpx；设备统一接口无
供应商私有认证或协议分支；无旧架构/迁移/兼容路径/核心插件污染；最终结果可合并。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-final-architecture-acceptance.md`，同步其阶段号为 Phase 11。

**风险及防止阶段越权的约束:** 禁止用最终验收临时实现缺失能力或放宽门禁；失败必须回到拥有该职责的阶段修正。

## 17. 发现的矛盾与最终裁决

| 矛盾或歧义 | 仓库证据 | 最终裁决 |
| --- | --- | --- |
| 旧总控把 WMS 作为 Phase 2 | Master/WMS 子计划旧编号 | 新 Phase 2 独立为公共 HTTP；WMS 顺延 Phase 3，后续顺延到 Phase 11 |
| “收敛”容易被理解为能力建设阶段必须立即迁移/删除旧代码 | 旧计划把 WMS Client、业务 API、平台建设、生产接线和旧 owner 混在 Phase 3/4 | Phase 2 暗构建基础传输；Phase 3 只暗构建 WMS Client；Phase 4 只暗构建 AGV/CTU Transport；Phase 5 只原子切换 Transport |
| 统一设备 HTTP Adapter 曾被塞入 Phase 4 | 旧摘要把 DeviceCommand、设备状态和 ECS HTTP 与 Transport 混建 | Phase 4/5 完全排除 Device/ECS；统一设备能力必须另立计划并在 Phase 7/8 前完成 |
| 旧 WMS 子计划把推测性认证放入 Phase 2 | WMS Task 5–8、`canonical_dispatch.py` 与 `sign_wms_hmac_request`，但冻结 WMS outbound 合同无认证要求 | Phase 2 不实现 `AuthStrategy`、凭据、HMAC、Clock、Nonce 或认证 seam；WMS 旧草案标记 Needs re-review，将来只有真实合同明确要求时才修订计划 |
| 现有 `external_http_*` 看似公共但耦合旧平台 | import `operation_registry`、Provider Profile、SystemOutbox/`idempotency_key` | 只复用可证明的 primitive，不把旧 Provider/Outbox 设计提升为目标公共层 |
| 当前既有长期 WMS Client，也有多个每请求 Client | WMS query/effect lane 与 DeviceCommand/旧 Gateway/Outbox | 目标为每外部系统每进程一个 Client；分阶段原子切换，Phase 9 最终零散落 Client |
| 旧 Provider 配置可表达 NONE/HMAC，容易被误读为当前需求 | `provider_profile.py` 与 `external_http_binding.py` | 它们是后续原子切换时删除的旧 owner，不进入 Phase 2 公共合同或部署配置 |
| SPEC §14 曾是九阶段编号，且与 Phase 3/4/5 交接语义冲突 | 复审前 SPEC §14.2–14.3 | 总控改为十一阶段：Phase 3/4 只暗构建新能力，Phase 5 原子切换生产路径并删除旧闭包；SPEC 在 Phase 3 实施前同步该语义 |
| 当前存在旧 `DeviceCommand`/Projection 类型 | 生产代码扫描 | 与 Phase 4 Transport 无关；既不作为实现模板，也不进入 Phase 5 Transport 删除范围 |
| 旧远端 feature 分支包含大量历史实现 | 分支差异远大于当前 develop | 不合并、不 cherry-pick，不作为需求；只有当前 develop 真实代码和权威文档作为实施输入 |
| WMS 初稿中的 method/path 与目标业务消费者尚未逐项批准 | `docs/hardware/wms_rcs_interface_requirements.md` 与当前 WMS 合同 | 保留厂商初稿原文；Phase 3 只实现共享 Client，具体业务开发再逐项确认 wire，不从旧编号或既有实现推定 |

## 18. 自审结果

| 检查项 | 结果 | 说明 |
| --- | --- | --- |
| 占位标记 | 分阶段阻断 | PickingTask 的正式 JSON Schema、枚举闭集及业务 fixture 仍未冻结，只阻断其所属后续业务阶段；Phase 4 的四类 Transport wire、WMS 异步回调统一信封和 Task 0 已批准，不受其影响 |
| 兼容设计 | 通过 | Phase 2–4 新能力不接生产流量；旧路径只保留为唯一活动 owner 到 Phase 5；Phase 5 原子切换并删除，不存在 shim、alias、re-export、fallback、双写、双读或旧数据兼容 |
| 重复职责 | 通过 | Phase 4/5 只拥有 Transport；Device/ECS、具体业务和插件均有独立边界 |
| 测试过重 | 通过 | Phase 2/3/4 各自只测试新 owner；旧测试迁移和运行态验收统一归 Phase 5，不以跨层 happy path 替代分层测试 |
| 未确认推测能力 | 通过 | 不含认证 seam、BASIC/HMAC、动态拦截器、DSL、Service Locator、动态发现、未来协议或空插件 |
| 敏感信息 | 通过 | Phase 2 无凭据与 Secret；日志合同仍禁止 headers/body/query/原始异常文本 |
| 阶段越权 | 通过 | Phase 3/4 明确禁止接线和旧 owner 处置；Phase 5 单独承担原子切换；上一阶段未退出不得启动下一阶段 |
| 当前状态准确性 | 通过 | Phase 1–3 已完成；Phase 4 已完成暗构建和后端 QA；Phase 5–11 未开始 |

## 19. 总体完成定义

只有同时满足以下条件，本计划才完成：

1. Phase 2 公共传输、Phase 4 WMS Transport Adapter、独立 Device/ECS 能力和执行插件分别拥有单一职责与独立验收。
2. 只有 Phase 2 基础层直接依赖 httpx；WMS Transport Adapter 与后续设备统一接口只消费已装配 Transport，核心和插件不可见
   httpx、认证、凭据或供应商私有协议。
3. 核心可靠性不变量由最终对象测试证明；设备公共 wire、供应商一致性、WMS 业务结果合同和执行映射分别由其唯一所有者证明。
4. 旧生产架构、旧 HTTP owner、无依据认证、旧测试所有者、旧配置、兼容路径和旧 migration chain 全部归零。
5. 最终数据库可以从空库一次建立，不需要旧数据、旧 revision 或转换脚本。
6. 当前态文档、active TODO、代码、测试、schema、部署配置和 composition root 共同指向同一个最终架构。

## 20. Implementation Tasks

Phase 3 已按 TDD 完成不含业务 API 的 WMS HTTP Client。当前行为真源为
`docs/contracts/wms-northbound-interaction-contract.md`、`src/app/wms_adapter/` 与 `tests/contracts/wms_adapter/`；完成计划只在项目外归档保留历史。

| 顺序 | 任务 | Surface area | 主要验证 |
| --- | --- | --- | --- |
| 1 | 建立 WMS Client 访问合同测试 | GET/POST、JSON、传输事实和关闭 | FAST 测试先红后绿；不含业务断言 |
| 2 | 实现 `WmsClient` | `client.py` | relative path、一次 send、严格 JSON 编解码、空响应/JSON `null`、非 2xx 和失败事实 |
| 3 | 实现 factory 与公开导出 | `factory.py`、`__init__.py` | 固定 `system_id="wms"`、长期 Client、零裸 httpx |
| 4 | Phase 3 退出验证 | 新 Client、HEAVY selector 显式 NONE 与架构边界 | Phase 2 回归、FAST、selector、Ruff、类型、Import Linter、quality |

具体 WMS 业务 API 不进入本任务表。它们在后续真实业务开发中各自定义 DTO、path、结果解释和合同测试，并复用稳定的
`WmsClient`。

## 21. 工程复审完成摘要

- **Scope：** Phase 3 只暗构建 Axios 式 WMS Client；Phase 4 只暗构建 AGV/CTU Transport；Phase 5 只切换 Transport；
  Device/ECS 必须另立计划并在 Phase 7/8 前独立验收。
- **Architecture：** Phase 3 Client 直接消费 Phase 2 Transport，只统一 HTTP/JSON；证据、业务 DTO、业务结果解释和可靠生命周期均不属于 Client。
- **Contract：** 所有业务结果由 WMS 给出；具体 method/path/DTO 随后续业务逐项批准和实现，不阻断 Phase 3。
- **Entry gate：** Phase 2 已完成，Phase 3 已实施并通过退出门禁。
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
| ENG REVIEW | COMPLETE | 0 | 0 | 业务 API 已全部移出 Phase 3；共享 Client 已实施并通过退出门禁 |
| INDEPENDENT REVIEW | SUPERSEDED | 0 | 0 | 旧“33 项完整面”评审基线已被消费者驱动边界替代，不再作为当前放行证据 |
| SERENA REVIEW | CLEAR | 0 | 0 | 参考前端 API Client 和 Phase 2 Transport 后，目标边界已收敛为单一薄封装 |
| SEQUENTIAL REVIEW | CLEAR | 0 | 0 | 具体业务合同不再作为共享 Client 的入口门禁 |
| PHASE 4 SCOPE REVIEW | CLEAR | 1 | 0 | Phase 4 已收敛为 AGV/CTU Transport 并通过最终评审；Device/ECS 移交独立计划 |
| DESIGN REVIEW | N/A | 0 | 0 | 无 UI/交互范围 |
| DX REVIEW | CLEAR | 0 | 0 | 新增 API 使用固定六步标准并复用 Client，不修改共享核心 |

**VERDICT：Phase 1–3 已完成；Phase 4 AGV/CTU Transport 已通过暗构建后端 QA；Phase 5 生产接线尚未开始。**

NO UNRESOLVED DECISIONS
