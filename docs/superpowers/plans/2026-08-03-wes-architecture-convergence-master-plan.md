# WES 最小执行架构十二阶段收敛总控实施计划

> **For agentic workers:** 本文只控制阶段顺序、职责边界、原子交接和退出门禁。每个阶段开始前必须另有经批准的详细实施计划；不得直接把本文当作代码实施脚本，也不得在阶段门禁未通过时启动下一阶段。

**Goal:** 按十二个单向依赖阶段，将当前 WES 直接收敛到 SPEC 定义的目标架构；Phase 4 Transport 暗构建验收后，
先完整退役旧工作线插件执行闭包，允许核心进入“全绿但零业务插件”的中间态，再分别收敛 Transport、Device/ECS 和
重新实现的独立业务插件。

**Architecture:** Phase 3 `WmsClient` factory 通过 Phase 2 builder，为各运行时/事件循环 owner 装配一个明确生命周期的
`OutboundHttpTransport`。Phase 4 只复用该 Client 暗构建四个工作线搬运方法、`TransportTask`、WMS 转发 RCS/AGV/CTU Adapter、
Transport member-position/result evidence 和位置投影。Phase 5 退役旧工作线插件及其专属 Runtime/Registry/Intent/Effect
执行闭包，不把新 Transport 接到旧插件；Phase 6 在零业务插件基线上完成 Transport 正式基础基线和旧 owner 收敛，
不得用 no-op consumer 假装业务接线；Phase 7 独立交付 DeviceCommand、设备状态、统一设备接口、CALLBACK、
`LineRunEpoch` 绑定和唯一生产装配。Phase 8/9 才以真实插件驱动最小 SPI 并完整重写业务插件。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL/TimescaleDB、Alembic、Celery、
Pydantic 2、HTTPX、Pytest 9、Ruff、Bandit、Import Linter、Jenkins。

**Status:** In progress — Phase 1–3 已完成；Phase 3 已交付 Axios 式 WMS HTTP Client；Phase 4 已完成暗构建和后端 QA 验收；
Phase 5 已完成零插件基线；Phase 6 Transport 与 Phase 7 DeviceCommand/ECS 核心生产基线已完成；退役插件活动残留收敛已合入，
合入后 tombstone 已清理且完成计划已移出项目归档。Phase 8 仓内实现、质量门禁和插件部署 E2E 已完成，但仍为
`IN_PROGRESS — EXTERNAL BLOCKED`；供应商一致性、现场联调和业务验收尚未完成。

**Requirements baseline:** `docs/architecture/SRS.md`

**Design baseline:** `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`

**Phase 8 rough-sorter contract baseline:** `docs/contracts/wms-rough-sorter-inbound-integration-requirements.md`（`Approved`）

**Phase 9 putaway contract baseline:** `docs/contracts/wms-inbound-putaway-integration-requirements.md`（`ReviewRequired`）

**Implementation baseline:** `develop@bda2079d523984f25265c113b2fb213429da40f0`（Phase 8 仓内实现与 Epoch 激活已合入；
外部验收仍阻塞，Phase 9 尚未开始）

---

## 1. 全局硬约束

- 系统尚未发布；开发和测试数据可以清空，不保留旧版本、旧 API、旧字段、旧配置、旧数据或历史 revision 的迁移能力。
- SRS 定义产品需求，顶层 SPEC 定义目标架构，本文只编排实施顺序；历史实现、旧分支和未确认设想不得提升为需求。
- 严格遵守 DRY、KISS、SOLID、YAGNI；不建设通用工作流、动态插件发现、Manifest、Service Locator、
  运行时 registry、任意签名 DSL 或推测性集成平台。
- 最终运行态只能存在一条执行路径；禁止兼容 shim、alias、re-export、deprecated wrapper、双写、双读、
  旧路径 fallback 和按 WorkLine 切分的新旧双轨。
- 替代能力通过验收并进入生产切换时，必须在同一原子切换中删除直接旧所有者；Phase 10 只处理跨阶段残留，不能成为
  保留旧路径的理由。
- Phase 2–4 的新基础层、WMS Client 与 Transport 能力均采用暗构建：源码和新测试可以与旧实现共存，但不得注册到旧
  Composition Root、不得接收生产流量、不得 shadow write，也不得提供新旧选择开关。Phase 5 先删除旧插件执行闭包；
  Phase 6 才收敛 Transport 正式基础基线；Phase 7 独立收敛 Device/ECS。每次原子切换必须删除该次被替代的直接旧 owner。
- WMS 是业务单据、库存、主数据、业务授权和全局仓内位置权威；WES 只拥有工作线本地执行事实；
  ECS/PLC 拥有设备物理动作和安全互锁。
- 外部 HTTP 公共层只发送一次并返回传输事实，不拥有自动重试、业务拒绝、Circuit Breaker 决策、
  Outbox/`TransportTask`/`WmsConfirmation` 生命周期、工作线推进或厂商 Payload 映射。
- WMS/RCS Adapter 每次调用只发送一次，不拥有 retry/backoff 配置；`TransportTask` 只能按批准合同保留原 identity、版本和
  Payload 进行安全收敛，否则进入人工对账。Device/ECS 重试语义只由 Phase 7 定义。
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
- Phase 3–4 可以合并“新能力已完成但尚未接线”的稳定暗构建态；Phase 5 允许合并“核心全绿且零业务插件”的稳定态；
  任何生产双轨态、空插件/no-op consumer、未完成当前阶段交接或未通过
  当前阶段退出门禁的状态不得合并回 `develop`。
- 每个阶段开始前必须有经批准的详细实施计划；计划必须冻结准确文件、接口、测试层级、验证命令和提交边界。

### 当前交付范围

本十二阶段计划只交付顶层 SPEC §3.1 已确认的粗分机、自动分拣、人工分拣、满箱交换和复杂出库能力。
SRS §3.5 特殊物料、机构件/SFC 协同及 §3.6 生产退料属于未来需求，不进入 Phase 8/9 或 Phase 12 验收，
不得据此预建空插件、Adapter 或扩展平台。

## 2. 九阶段到十二阶段的编号映射

| 原编号 | 原名称 | 新编号 | 新名称 | 裁决 |
| --- | --- | --- | --- | --- |
| Phase 1 | 测试治理基线 | Phase 1 | 测试治理基线 | 保持，已完成 |
| 无 | 无 | Phase 2 | 外部 Outbound HTTP 传输基础能力收敛 | 新增独立基础阶段 |
| Phase 2 | WMS 薄接入边界收敛 | Phase 3 | WMS HTTP Client 薄封装 | 消费 Phase 2，只统一 WMS HTTP/JSON 访问标准 |
| Phase 3 | WES 最小平台能力建设 | Phase 4 | AGV/CTU Transport 基础能力建设 | 只暗构建四个搬运方法、TransportTask、WMS 转发 Adapter、成员位置/终态证据和位置投影 |
| 无 | 无 | Phase 5 | 旧工作线插件执行闭包退役 | 删除旧插件和其专属平台，允许零业务插件中间态 |
| 无 | 无 | Phase 6 | Transport 正式基础基线与旧 owner 收敛 | 合并原 Transport 切换和测试承接；零插件时不造虚假消费者 |
| 无 | 无 | Phase 7 | DeviceCommand/ECS 通用能力生产收敛 | 新增独立基础阶段，不包含供应商私有 DTO 和业务插件 |
| Phase 5 | 粗分机参考插件优化 | Phase 8 | 粗分机参考插件优化 | 首个真实插件驱动最小 SPI，不复用旧插件源码 |
| Phase 6 | 分拣执行插件组优化 | Phase 9 | 分拣执行插件组优化 | 依赖 Phase 7 及 Phase 8 已验收模式 |
| Phase 7 | 旧平台代码最终闭环清理 | Phase 10 | 旧平台代码最终闭环清理 | 只处理 Phase 5–9 跨阶段残留 |
| Phase 8 | 旧数据模型与迁移链清理 | Phase 11 | 旧数据模型与迁移链清理 | 依赖 Phase 10 零旧路径 |
| Phase 9 | 最终基线与系统验收 | Phase 12 | 最终基线与系统验收 | 验收基础能力、Adapter、供应商和插件所有权 |

## 3. 受编号与职责调整影响的文档和引用

| 文档或引用 | 影响 | 本轮处理 |
| --- | --- | --- |
| `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md` | 总控由十一阶段调整为十二阶段 | 本轮同步 Phase 5–12 |
| `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-10-wes-legacy-workline-plugin-execution-retirement.md` | Phase 5 详细退役计划 | Phase 5 完成后保留完整历史执行证据，不再作为当前实施入口 |
| 旧 `2026-08-03-wes-wms-thin-access-convergence.md` | Phase 2 顺延为 Phase 3；旧稿含无合同依据的认证设计 | 完整移至项目外归档；Phase 3 在 Phase 2 完成后重新编写当前计划 |
| `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md` | 旧插件与 DeviceCommand 测试处置仍归旧 Phase 5 | 同步为 Phase 5 插件退役与 Phase 7 Device/ECS 双 owner |
| `docs/superpowers/README.md` | 当前文档索引仍称九阶段、WMS Phase 2 | 同步索引名称和状态 |
| `docs/architecture/file_index.md` | 当前索引仍称十一阶段 | 同步为十二阶段并新增 Phase 5 计划入口 |
| `README.md` / `docs/architecture/SRS.md` | 若仅写“十一阶段”则编号过期 | 本轮扫描并机械同步；业务范围不变 |
| `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` §14.2–14.3 | 仍要求 Phase 5 直接切换 Transport | 同步为插件退役、Transport、Device/ECS、插件重写的新顺序 |
| `docs/plugin_development_guide.md` | 曾要求每个供应商交付 WES Adapter 包 | 已同步为供应商实现统一接口、插件只拥有业务执行映射 |
| `docs/integration/workline_device_error_code_standardization.md` | 曾要求 WES Adapter 映射供应商原始错误 | 已同步为供应商 ECS/网关按设备合同附录输出统一错误包络 |
| `docs/integration/third_party_integration_whitepaper.md` | 1.1 曾被整体归档，导致供应商顶层合同缺位 | 恢复为长期生效的 2.3 统一接口真源；1.1 继续留在项目外历史归档 |
| `docs/devops/prod-release-deploy.md` 开头 | 仍以旧 Phase 8 指向最终 Alembic 基线 | 已机械同步为 Phase 11；运行职责和临时 Provider 输入不在本轮修改 |
| `TODOS.md` | Device/ECS 已由 Phase 7 正式承接，不再属于未排期工作 | 删除重复 active TODO；真实设备附录和绑定仍归 Phase 8 |

上述同步项不是新的实施入口。SPEC 继续定义目标业务边界；指南和 TODO 不得反向改变本计划的
Transport/Adapter/核心所有权。

## 4. 当前分支与实施状态

| 证据 | 当前事实 | 裁决 |
| --- | --- | --- |
| `da8c1073` / PR #109 | Phase 4 Transport 暗构建和后端 QA 已合入 `develop` | `ACCEPTED_DARK`；尚未接入生产路径 |
| `28eb99d9` / PR #100 | Phase 1 架构与测试治理已合入 | Phase 1 完成，但测试计划延后义务未整体完成 |
| `src/app/wms_integration/` | 仍为 54 个生产文件，Provider/Profile、Registry、QUERY、Effect/status/evidence 混合存在 | Phase 3 不复用其设计；旧包只是待替代的临时所有者 |
| `src/app/sys/external_http_*` 与 `canonical_dispatch.py` | 已有 typed transport fact、凭据解析、NONE/HMAC、bounded response 的部分能力，但耦合 Provider Profile/SystemOutbox/WMS operation | 仅 transport fact 作为行为证据；认证相关能力无真实 outbound 合同依据，不进入 Phase 2 |
| 多处 `httpx.AsyncClient()` | DeviceCommand、旧 Outbox、WMS runtime、旧 Gateway 等仍自行创建 Client | Phase 5 只解除旧插件闭包；Device/ECS 裸 Client 由 Phase 7 处理 |
| 目标对象扫描 | 已有 `TransportTask`、Transport member-position/result evidence 和 Transport 位置投影 | Phase 4 暗构建完成；Phase 6 只收敛 Transport 直接旧 owner |
| `34837439` / `src/app/runtime/workline_plugins/` 缺席 | 旧工作线插件、binding、dispatcher、attempt 及目录外活动 owner 已原子退役 | Phase 5 零插件基线完成；后续插件按 Phase 8/9 目标合同重新实现 |
| `5fe59968` / 项目外归档 `2026-08-15-wes-retired-plugin-residual-convergence.md` | 退役插件活动残留收敛已合入 `develop`，两个 deletion tombstone 已完成合入后清理 | Completed；归档只作历史证据，不再是项目内实施入口 |
| 当前规划增量 | Phase 3 已删除业务 Port、operation 矩阵和单项业务门禁，只保留 WMS HTTP Client 与开发示例 | Phase 3 已完成实施与验收 |
| 其他旧 feature 分支 | 大幅落后或已被 develop 取代，包含旧 Manifest/Runtime 语义 | 只作 Git 历史，不作为实施输入 |

阶段状态：Phase 1–3 已完成，Phase 4 已完成暗构建和后端 QA；Phase 5 已完成零插件基线；
Phase 6 与 Phase 7 核心生产基线、退役插件活动残留收敛及其合入后清理均已完成；Phase 8 仓内实现、质量门禁和插件部署 E2E
已完成，但因供应商一致性与现场联合验收尚未运行，仍为 `IN_PROGRESS — EXTERNAL BLOCKED`；Phase 9–12 尚未开始。

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
Phase 5  旧工作线插件执行闭包退役（允许零业务插件）
   ↓
Phase 6  Transport 正式基础基线与旧 owner 收敛
   ↓
Phase 7  DeviceCommand/ECS 通用能力生产收敛
   ↓
Phase 8  粗分机参考插件与最小 SPI
   ↓
Phase 9  分拣执行插件组
   ↓
Phase 10 旧平台代码最终闭环
   ↓
Phase 11 旧数据模型与迁移链
   ↓
Phase 12 最终基线与系统验收
```

允许提前编写和评审下一阶段详细计划，但上一阶段退出门禁未通过时不得启动下一阶段生产代码实施。

### Phase 2–7 职责边界

| 责任 | Phase 2：Outbound HTTP | Phase 3：WMS Client 暗构建 | Phase 4：Transport 暗构建 | Phase 5：旧插件退役 | Phase 6/7：基础能力收敛 |
| --- | --- | --- | --- | --- | --- |
| 生产装配与调用切换 | 不接入真实 Adapter | 不修改旧 Composition Root | 不修改旧 Composition Root | 删除旧插件装配，不接新 Transport | Phase 6 装配 Transport 基础能力；Phase 7 装配 Device/ECS |
| Client 生命周期、连接池、Timeout、base URL | 唯一拥有 Transport | 通过 factory 构造并公开长期 `WmsClient` | 复用 Client | 不修改目标 Client | Phase 6/7 各自显式装配，不暴露裸 Client |
| HTTP method primitive | 拥有当前最小集合，不解释业务 | 提供 request/get/post 薄方法 | 为真实业务选择固定 method | 不改变合同 | 不改变 Phase 2/3 合同 |
| WMS 业务 method/path、wire DTO、拒绝码 | 不拥有 | 不拥有 | 只拥有 Transport wire | 删除旧插件专属调用 | 真实业务纵切片另行拥有 |
| WMS 转发 RCS Transport | 不拥有 | 不拥有 | Transport Adapter 唯一目标 owner | 不接旧插件 | Phase 6 验收唯一基础能力，Phase 8 起由真实插件消费 |
| WMS outbound 认证 | 不拥有、不预留 | 当前仅 `NONE` | 不可见 | 删除旧插件专属配置 | Phase 6/7 不新增认证 seam |
| 外部调用 evidence 与 breaker | 不拥有 | 不拥有；ACL 无状态 | 只保存 Transport 事实 | 删除旧插件专属分支 | Phase 6/7 分别拥有自己的可靠证据 |
| Device/ECS 命令、状态与回调 | 只提供通用单次发送 primitive | 不拥有 | 不拥有 | 解除旧插件调用，不提升旧实现 | Phase 7 唯一拥有 |
| `TransportTask` 生命周期 | 不拥有 | 不拥有 | 唯一目标 owner | 保持暗构建 | Phase 6 正式基线，零插件时没有任务生产者 |
| Transport evidence ingress | 不拥有 | 不修改旧入口 | 固定 evidence 与应用端口 | 不接旧插件 | Phase 6 完成唯一入口；无任务时无业务流量 |
| 旧实现和旧测试 | 不修改 | 不修改 | 不修改 | 处置插件执行闭包 | Phase 6 处置 Transport；Phase 7 处置 Device/ECS |

### Outbound HTTP 认证裁决

| 所有者 | 当前必须拥有 | 明确不得拥有 |
| --- | --- | --- |
| WMS Composition Root | 向 factory 传入 base URL/Timeout 并管理返回的 `WmsClient` 生命周期 | 传入 session factory、运行时扫描、Service Locator、推测性认证配置 |
| Phase 2 基础层 | 无认证职责 | `AuthStrategy`、凭据解析、HMAC、Clock、Nonce、认证枚举或未来扩展 seam |
| WMS Client 包（Phase 3） | 当前 outbound 无认证；只统一 HTTP/JSON 访问 | 业务 DTO、业务结果解释、无合同依据的 canonical/Header/签名、裸 httpx、连接池 |
| Device/ECS Adapter | Phase 4–6 不建设、不接线；Phase 7 按统一接口交付 | 借 Transport 或插件阶段预建供应商私有协议或认证能力 |
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
按 owner 分别保留到 Phase 5–7，在对应最终对象权威测试通过后处置。

**测试所有权与重量要求:** Phase 1 的治理门禁继续生效；“阶段完成”不代表测试收敛计划整体完成。

**与前后阶段的 atomic handoff:** 向 Phase 2 交付轻量测试规则；向 Phase 5–7 分别交付插件、Transport、Device/ECS 的
Task 4/5/7 延后义务。

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
建立目标能力，Phase 6 建立 successor/`NONE` 后收敛并删除。DeviceCommand、设备状态、统一设备 Adapter 和 ECS 旧 owner
不进入 Phase 4/6，必须由 Phase 7 重新扫描、建立 successor 并切换；Phase 10 只验证无遗漏，不作为默认归宿。

**测试所有权与重量要求:** 只用 `httpx.MockTransport`、测试内 local fake 和纯单元测试；不访问真实外部系统，
不使用 sleep，不建立大规模 E2E。只验证框架无关合同、生命周期、请求装配、受限响应、传输事实分类、取消传播、
已知异常与意外异常边界及日志脱敏。厂商认证/Header/DTO、业务拒绝、重试/终态/恢复不进入本阶段测试。

**与前后阶段的 handoff:** Phase 2 向 Phase 3/4 交付同一 Transport 合同，但不激活真实业务集成。Phase 3 暗构建 WMS
Client；Phase 4 只复用该 Client 暗构建 WMS Transport Adapter；Phase 6 冻结唯一 Transport 安装装配并删除直接旧
Client/sender。Device/ECS 在 Phase 7 复用 Phase 2 Transport，但必须由自己拥有，不回填 Phase 4/6。

**Exit gate:** builder 只返回可直接使用的 Transport；公开合同不暴露 httpx 类型；GET/POST 走同一条单次发送路径且无
WMS 条件分支；公共层单次发送且无重试/业务解释；
不捕获未知编程异常，不存在认证、credential、HMAC、Clock、Nonce 或生产 fake；全部轻量测试和架构门禁通过；
`src/core/outbound_http/` 之外的既有生产文件无 Phase 2 实施修改，WMS/RCS/ECS 生产模块和 Composition Root 零新包
import；`src/core/bounded_http_response.py` 内容与路径保持不变；Phase 3 可在不复制 transport 语义的前提下开始。

**详细计划归档:** GET/POST 基线已完成且当前无待实施修订；Phase 2 计划已移至项目外
`../archive_docs/wes_backend/docs/superpowers/plans/2026-08-04-wes-outbound-http-transport-convergence.md`，不再承担当前执行入口。

**风险及防止阶段越权的约束:** 最大风险是把既有 SystemOutbox/Provider Profile 整体提升为公共层、提前修改真实
Adapter/Composition Root，或建设认证/拦截器扩展平台。子计划必须以“新包单一实现、稳定 primitive 只读复用、
旧消费者精确 HANDOFF”为准；不得搬迁旧模块、复制旧源码、继承旧生命周期或提前执行 Phase 6 的 Transport 收敛。

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

**旧所有者处置:** `NONE`。Phase 3 不迁移、不改写、不删除旧生产 owner 或旧测试；后续按 owner 分别由 Phase 5 插件退役、
Phase 6 Transport 或真实 WMS 业务纵切片直接删除旧实现。

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
Phase 4 只依赖自身和已完成的 Phase 3；Phase 5 插件退役和 Phase 6 Transport 安装装配、consumer、successor/`NONE`
均不构成本阶段前置条件。

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

**Phase 5/6 后续责任（不属于 Phase 4 交付物）:** Phase 5 只退役旧插件执行闭包且不得接线 Transport；Phase 6 启动时
重新扫描当前 Transport 引用图，自行建立并批准 successor/`NONE` 清单后再收敛安装装配和删除直接旧 owner。Phase 4
不预登记、不维护该清单。Device/ECS 旧 owner 由 Phase 7 处理。

**测试所有权与重量要求:** Phase 4 只新增四个公共搬运方法、Transport 核心、WMS Transport Adapter、Transport evidence ingress 和
PostgreSQL integration 测试。测试不得导入 PickingTask、工作线插件、设备 DTO、供应商协议或真实业务 happy path；
WMS/RCS/AGV/CTU 真实行为继续由外部联调验收拥有。

**与前后阶段的 handoff:** 接收 Phase 3 `WmsClient`；交付 TransportTask、WMS Transport Adapter、Transport evidence handler、
暗 Composition 和四个内部批处理入口。Phase 4 不执行生产切换，也不等待 Phase 5/6 规划完成。

**Exit gate:** 四个公共方法及 Transport submit/member-position/result 合同已批准；同一对象无重叠非终态任务；Task → submit ACK →
member position → TransportResult → task/projection 的暗闭环通过；倒序逐箱事实不能回退位置，并发结果不能覆盖已接受终态；
核心无 `httpx`、ECS、DeviceCommand、PickingTask 或旧 Runtime/Effect import；新 route/task/Adapter 未注册到生产入口；
四个内部批处理入口可由测试显式调用，且不存在 Phase 5/6 反向依赖。

**实施状态:** Phase 4 已按原批准合同完成暗构建和 QA 验收，尚未注册生产 route、Celery task、beat、worker hook 或工作线
消费者。Phase 5 退役计划已经完成并归档。2026-08-11 当前 Transport 合同与公共 WMS 异步回调信封将 WMS/WES wire
和回调身份统一为 `operation_id`；该后续 wire 差异不回退 Phase 4 的 `ACCEPTED_DARK` 历史结论，而是作为 Phase 6
生产基线入口义务，在任何 Transport
生产安装前按 TDD 闭合。

**当前合同真源:** `docs/contracts/transport-fulfillment-contract.md`。完成的 Phase 4 过程计划归档于
`../archive_docs/wes_backend/docs/superpowers/plans/2026-08-08-wes-minimal-platform-capabilities.md`，不再作为当前实施入口。

**风险及防止阶段越权的约束:** 禁止把 ECS/Device 或通用执行平台重新包装进 Transport；禁止直连 RCS/AGV/CTU；
禁止在 Phase 4 接线、改写旧实现、删除旧测试或增加兼容桥。

## 10. Phase 5：旧工作线插件执行闭包退役

**Objective:** 完整退役嵌入核心的 `rough_sorter`、`smt_sorting_inbound` 及其专属 plugin registry、generated index、
dispatcher、Runtime/Intent/Effect/SystemCapability/SystemOutbox 执行闭包，形成核心全绿但暂时没有业务插件的受控中间态。

**Authoritative inputs:** 顶层 SPEC §7/§14；当前 `src/app/runtime/workline_plugins/`；所有目录外 producer、consumer、
callback、API、Celery、配置、数据库和测试 owner；本阶段详细计划。

**Entry conditions:** Phase 4 退出门禁通过；代码实施分支上的完整插件执行闭包已逐项映射为
`DELETE → successor`、`DELETE → NONE` 或 `RETAIN`；Phase 7 需要新建的能力另记 `Phase 7 ADD` 义务，不作为 Phase 5
删除对象的 successor 或保留理由；不存在需要迁移的发布数据。

**Scope:** 删除具体旧插件、generated index、registry、dispatcher 和只为旧插件存在的业务执行入口；解除 WorkLine、
RuntimeInbox、Intent/Effect、SystemCapability、SystemOutbox、API、Celery、配置和测试对旧插件平台的依赖；保留或先承接
WorkLine 静态身份、物理拓扑、入站幂等和 fencing 等通用不变量；通过一条无数据迁移的 schema cleanup revision 同步删除
旧 binding 表、plugin identity/manifest/pin/state 列及其 FK、索引和约束。

**Explicit out-of-scope:** 新插件重写、插件 SDK/模板、Phase 4 Transport 接线、最终 DeviceCommand/ECS、供应商私有协议、
兼容 shim、空插件、默认插件、no-op consumer、双路径和旧数据迁移。

**Deliverables:** 零业务插件核心装配；具体插件和旧 plugin platform 缺席门禁；完成 successor/`NONE` 的旧插件测试处置；
ORM 与空库 `alembic upgrade head` 形成同一零插件 schema；当前态文档、部署清单和任务路由不再指向旧插件入口。

**旧所有者删除规则:** 删除对象按活动执行闭包而非目录或关键词判断；通用不变量必须先有最终核心 owner；只证明旧插件、
旧 Runtime/Manifest/Capability/Intent/Effect 的对象可标记 `NONE`；不得把旧实现搬到新包或保留 facade。

**测试所有权与重量要求:** 具体业务测试按 `DELETE → NONE（PLUGIN_OWNED，未来插件按新代码重建）` 删除；供应商私有协议标记
`SUPPLIER_OWNED`；通用核心不变量先由最终核心测试承接。不得用粗分或 SMT happy path 证明核心能力。

**与前后阶段的 atomic handoff:** 接收 Phase 4 暗 Transport，但不把它接到旧插件；按“承接通用不变量 → 删除具体插件 →
删除专属平台 → 全量验证”完成，向 Phase 6 交付零业务插件、零旧 plugin platform 的干净核心。

**Exit gate:** 核心可在插件安装清单为空时启动和验收；全仓生产代码、测试和机器可读配置对具体旧插件、generated plugin
index 和旧 registry 零依赖；空库升级后的 PostgreSQL 与 ORM 同时缺少 binding 表、plugin identity/manifest/pin/state
列和相关约束；不存在空插件、no-op consumer、alias、fallback 或双路径；Phase 4 Transport 未接旧插件。

**完成证据:** 详细计划已归档于
`../archive_docs/wes_backend/docs/superpowers/plans/2026-08-10-wes-legacy-workline-plugin-execution-retirement.md`。
`3199af5d` 承接零插件通用可靠性不变量，`34837439` 原子退役旧插件执行闭包；`8eceacea` 记录
`v0.24.1.0` 变更日志与验证结果。该归档只用于追溯，不再作为当前实施入口。

**风险及防止阶段越权的约束:** 只删除旧业务插件及其专属执行闭包。通用 WorkLine、证据、幂等和 fencing 必须按 owner
逐项判断；Device/ECS 最终能力留给 Phase 7，Transport 最终基线留给 Phase 6。

## 11. Phase 6：Transport 正式基础基线与旧 owner 收敛

**Objective:** 在零业务插件基线上，将 Phase 3 `WmsClient` 和 Phase 4 Transport 收敛为唯一可安装的 Transport 基础能力，
删除 Transport 直接旧 owner，并完成核心、WMS Adapter 和 PostgreSQL 可靠性测试所有权。

**Authoritative inputs:** Phase 3 `WmsClient`；Phase 4 `TransportTask`、Transport Port、WMS Transport Adapter、
Transport evidence handler 和四个批处理入口；`docs/contracts/wms-async-callback-envelope-contract.md`；
`docs/contracts/transport-fulfillment-contract.md`；Phase 5 零插件基线；`tests/README.md` 和 HEAVY selector 真源。

**Entry conditions:** Phase 5 退出门禁通过；公共 WMS 异步回调的 `operation_id` wire 差异、失败测试清单和唯一改造清单已由
Phase 6 详细计划冻结，Task 1 必须先形成可归因的失败测试，生产代码修改只能在红灯证据之后开始；所有 Transport producer、
consumer、callback、配置、任务路由、数据库对象和测试 owner 已映射到唯一 successor 或 `NONE`；没有旧插件消费者需要迁移。

**Scope:** 冻结唯一 Transport Composition Root、生命周期和生产安装方式；删除 Transport 专属旧
Effect/status/Outbox/callback hint/result callback、配置、任务路由、旧 schema 和对应测试；完成 Transport FAST、
WMS Adapter contract、PostgreSQL integration 和跨边界缺席门禁。

**Explicit out-of-scope:** 创建业务 Transport producer、把结果接到空插件、`NoOpTransportOutcomePublisher`、普通 WMS
业务、DeviceCommand、设备状态、ECS、供应商协议和插件业务验收。

**Deliverables:** 唯一可安装的 `WmsClient`/Transport bundle；唯一 Transport Port、WMS Adapter、`TransportTask` 生命周期
和 evidence ingress；Transport successor/`NONE` 测试矩阵。没有业务插件时 bundle 保持未绑定、无任务、无结果发布。

**旧所有者删除规则:** 目标测试先通过，再删除 Transport 直接旧 owner；不得复制旧源码、保留 re-export、alias、fallback、
shadow write 或历史表读取。共享模块只删除 Transport 专属分支。

**测试所有权与重量要求:** FAST 不访问真实数据库/HTTP/Celery；PostgreSQL claim/事务使用精确 integration；不得以厂商、
插件 E2E 或空 consumer 证明 Transport 基础可靠性。

**与前后阶段的 atomic handoff:** 接收 Phase 5 零插件核心；按“权威测试绿 → 冻结唯一安装装配 → 删除 Transport 旧 owner →
全量验证”完成；向 Phase 7 交付与 Device/ECS 平行、互不依赖的 Transport 基线。

**Exit gate:** Transport 最终对象和测试 owner 唯一；回调 DTO、持久化、Handler、迁移和合同测试已统一使用
`operation + operation_id`，旧 `event_id` 回调身份零引用；Transport evidence 的 `operation_id` 由 WMS 生成并只通过
`transport_task_id` 关联；Transport submit 同样改用稳定 `operation_id`，由 WES Transport 在首次提交前与不可变 Payload 原子
持久化，安全重提不换号，业务 JSON 中旧 `request_id` 零引用。Phase 4 暗构建中的旧 submit 信封在本阶段直接替换，不保留
双信封、兼容解析或迁移路径；旧 Transport
Effect/status/Outbox/callback 路径零引用；合同测试覆盖 `400 | 413` 预关联空响应体、Transport evidence 在 `422` 后换新 ID且
不保存拒绝摘要；PickingTask 等普通 WMS 业务的原因果 ID 端到端场景由对应业务合同和插件阶段验证，
不进入 Phase 6。核心测试无具体业务/设备/厂商行为；零插件态没有虚假 publisher 或 consumer。第一个真实插件只能在 Phase 8
显式绑定 Transport。

**完成状态:** Phase 6 已按冻结的最终 wire、唯一生产装配与生命周期、旧 owner/test successor 闭包完成实现和验收；详细过程计划
已移出项目目录归档，不再作为当前架构真源。旧计划名 `2026-08-06-wes-atomic-capability-cutover.md` 继续保持停用。

**风险及防止阶段越权的约束:** “正式基础基线”不等于“已有业务生产流量”。禁止为制造切换证据而创建空插件、默认插件、
no-op publisher 或测试专用生产装配。

## 12. Phase 7：DeviceCommand/ECS 通用能力生产收敛

**Objective:** 独立交付并原子收敛 WES `DeviceCommand` 可靠生命周期、设备状态与事件证据、统一 ECS 接口、
ACK/CALLBACK 关联、`LineRunEpoch` fencing 和唯一生产装配，为后续真实插件提供与业务无关的设备执行基础能力。

**Authoritative inputs:** 顶层 SPEC §4.2–§5.2、`docs/architecture/device-command-contract.md`、
`docs/integration/third_party_integration_whitepaper.md`、Phase 2 Outbound HTTP、Phase 5 零插件基线和当前旧 Device owner 引用图。

**Entry conditions:** 已满足。Phase 6 退出门禁已通过；命令、状态、事件和结果固定路径及公共包络已批准；旧 DeviceCommand、
RuntimeIntentLog、SystemOutbox 设备分支、gateway、callback、配置、任务和测试已在独立详细计划中映射到唯一 successor、
`NONE` 或 `RETAIN`。实施前必须按详细计划重新核对冻结摘要，发生漂移时只复审受影响边界。

**Scope:** 稳定命令 identity 和不可变 payload digest；每 `device_code` 最多一个已接纳未终态命令；发送前
`AUTO + IDLE` 和活动 Epoch 合同身份校验；同步 ACK 与异步终态 CALLBACK 分离；delivery unknown、deadline、安全重提和人工
对账；状态新鲜度；事件/结果 ACK-after-persist、部署级唯一 `source_event_id`、重复/冲突/迟到 fencing；显式 Composition Root
和有界 worker；删除全部直接旧 Device owner。

**Explicit out-of-scope:** 供应商原始 DTO、私有路径/认证、PLC/机械安全、具体 `task_type`/`event_type` 业务全集、
插件 Decision、WMS 业务流程、Transport、动态 registry、Service Locator、通用工作流和旧数据迁移。

**Deliverables:** 唯一 `DeviceCommand` 聚合与 Repository/Service；统一 ECS Adapter 和 ingress handler；设备状态/事件/结果证据；
Epoch 绑定与 fencing；固定 worker/路由装配；Device/ECS 核心合同和可靠性测试；旧 gateway/SystemOutbox 设备分支缺席门禁。

**旧所有者删除规则:** 最终 DeviceCommand 权威测试先建立，随后在同一阶段切换唯一生产装配并删除旧 DeviceCommand、
RuntimeIntentLog 和 SystemOutbox 的设备命令职责、可配置路径、旧 `event_id`、`priority`/`timeout` wire 和私有认证分支；
非设备共享职责按详细计划标记为 `RETAIN`，不得扩大删除范围；不保留兼容字段或双路径。

**测试所有权与重量要求:** 核心只验证固定路径、公共包络、身份、幂等、状态新鲜度、ACK/CALLBACK、fencing 和可靠生命周期；
供应商一致性验收拥有真实设备附录和 ECS 行为；插件测试拥有业务推进。三者不得互相代测。

**与前后阶段的 atomic handoff:** 接收 Phase 5 零插件核心和 Phase 2 HTTP primitive，技术上不消费 Phase 6 Transport；
实施调度仍按本总控等待 Phase 6 退出门禁。Phase 7 向 Phase 8 交付唯一 Device/ECS 基础能力。发现供应商差异时留在
设备附录或供应商网关，不扩张核心合同。

**Exit gate:** 所有设备 HTTP 调用只经唯一 ECS Adapter；同一 `device_code` 无并发活动命令；ACK 不推进物理终态；只有匹配
CALLBACK 可推进投影；未知、冲突、迟到或合同不匹配证据失败关闭；旧 Device owner 和裸 Client 分支零引用。零设备绑定是
合法退出态：未绑定设备返回 `DEVICE_NOT_FOUND`，不发送 outbound 请求，也不接纳设备事件。

**完成状态:** Phase 7 已按批准合同完成唯一 `DeviceCommand`/ECS 生产基线、三个有界 worker、固定 callback、
`LineRunEpoch` fencing、schema cutover 和旧 Device owner 收敛。完整受影响 HEAVY 已在真实 PostgreSQL、Redis、Celery
prefork 与 HTTP 闭环中 426 passed、0 skipped；完成的过程计划已移至项目外
`../archive_docs/wes_backend/docs/superpowers/plans/2026-08-10-wes-device-ecs-production-convergence.md`。
这只证明核心基础能力，不代表供应商一致性、设备合同附录、现场联调或 Phase 8/9 业务插件已经交付。

**风险及防止阶段越权的约束:** 不把旧 `DeviceCommandService` 当目标模板；不在本阶段定义具体插件业务、供应商私有协议或
Transport；`LineRunEpoch` 只拥有设备合同/拓扑/配置的连续可信运行代际，不吸收 PickingTask 生命周期。

## 13. Phase 8：粗分机参考插件优化

**Objective:** 在独立 Device/ECS 基础能力已批准、实施并切换为唯一生产路径后，以粗分机交付首个真实执行插件、
设备合同附录、endpoint/device 绑定和供应商一致性验收。

**Authoritative inputs:** 顶层 SPEC §7/§11.1、`docs/contracts/wms-rough-sorter-inbound-integration-requirements.md`、
第三方设备统一接口白皮书、Phase 7 Device/ECS 验收证据、粗分机真实拓扑、供应商原始资料和 Phase 6 Transport 基线。

**Entry conditions:** Phase 7 Device/ECS 退出门禁通过；入库合同已由 WMS、WES、RCS 和 ECS 联合批准；粗分机供应商资料
完整；设备 `task_type`、`event_type`、字段闭集、错误和时限已形成可批准附录。

**Scope:** 粗分机设备合同附录、endpoint/device 绑定、供应商一致性验收和独立插件包；身份与测量证据、WMS 原子 GRN
绑定、目标 Cell 晚绑定、placement/NG Fact、旧架 release gate 与快照、人工对账，以及两个既有 `RACK_MOVE` 的 Phase 6
Transport Port 消费；以第一
个真实插件为依据冻结最小、静态、显式注入的插件 SPI。设备 HTTP 只复用 Phase 7 唯一生产 Adapter。

**Explicit out-of-scope:** 其他分拣线、第二个 WES 设备 HTTP Adapter、公共 HTTP Client、凭据、通用认证配置、WMS wire 合同重定义、通用插件模板。

**Deliverables:** 独立获批 Phase 8 粗分入库合同和粗分机设备合同附录、endpoint/device 绑定、通过一致性验收的供应商 ECS/网关、
可独立构建/测试的粗分机插件、由真实使用驱动的最小 SPI、显式插件 Composition Root 绑定和真实业务验收结果；不新增
顶层 `InboundTask`、兼容 operation 或 WES HTTP Adapter。

**旧所有者删除或交接清单:** 旧粗分业务代码和测试已在 Phase 5 删除，本阶段只按当前合同重新实现，不从 Git 历史搬运；
设备旧 sender 已由 Phase 7 删除。本阶段不得恢复供应商私有 DTO、HTTP Client、HMAC 工具、路径或映射副本。

Phase 8 最终扫描确认 `plugin_state`、`src.app.runtime.workline_plugins` 和旧粗分实现没有生产 owner。下列跨阶段通用对象不是
粗分插件的第二路径，Phase 8 不越权删除；它们按当前 owner 与 successor 精确交给 Phase 9/10：

| 残余对象 | 当前 owner / 主要消费者 | 已批准 successor / 交接 |
| --- | --- | --- |
| `RuntimeInbox` | `runtime/orchestration/runtime_inbox.py`、repository/service、callback writer、WMS handler、Celery scanner | 具体执行证据已由 `InboundEvidence` 承接；剩余通用 callback/inbox 消费闭包由 Phase 10 原子切换 |
| `ExecutionSession` | session model/repository、WorkLine runtime/query/safety | `LineRunEpoch` 加具体对象 Execution；Phase 10 按消费者删除通用 session 路径 |
| `RuntimeIntent` / Effect / `SystemCapability` / `SystemOutbox` | intent/effect service、system capability definitions、WMS sync 与 outbox dispatch | `DeviceCommand`、`TransportTask`、`WmsConfirmation` 和类型化领域 Service；Phase 10 删除通用热路径，不建立兼容桥 |
| `RuntimeHold` | hold model/repository/service、safety/resource/query | 业务 NG、设备故障、依赖暂停和人工清线的具体状态；Phase 10 逐消费者归属，不以粗分业务测试代证 |
| `confirm_inbound` / `notify_pkg_binding` 通用 WMS operation | WMS operation definitions、generated capability index、sync obligation 与 observability | 不是 Phase 8 粗分 operation；分别由既有 Phase 9/10 cutover guardrail 管理，Phase 8 不删除或改写 |

**设备合同附录责任:** 本阶段只冻结粗分机真实支持的 `task_type`、`event_type`、Payload、错误和时限，并完成 endpoint/device/
ECS 版本与 `LineRunEpoch` 绑定。固定路径、公共包络、identity、ACK/CALLBACK、状态新鲜度和冲突处理全部复用 Phase 7，
不得在插件中复制或覆盖。相关诊断文档若已被当前合同取代，按项目规则移出项目目录归档，不保留转发页或重复真源。

**测试所有权与重量要求:** Phase 7 Device/ECS 基础能力测试拥有固定路径、公共包络、DTO 校验、错误映射、身份和
ACK/CALLBACK；供应商一致性验收拥有设备
附录字段与 ECS/网关行为；WMS Adapter 包拥有业务结果合同；插件包拥有执行 Decision 和对象推进。五者不得复制场景，
供应商验收和插件测试均不进入核心默认 pytest。

**与前后阶段的 atomic handoff:** 消费 Phase 7 Device/ECS 和 Phase 6 Transport；发现公共设备能力缺口时回到
Device/ECS owner 修订，否则规则留在设备合同附录或粗分插件。本阶段不得实现第二个 Adapter。

**Exit gate:** 供应商 ECS/网关通过一致性验收，endpoint/device 绑定明确，插件独立安装、构建和测试；业务闭环仅由插件拥有；
全部设备 HTTP 调用仍经 Phase 7 唯一生产 Adapter，核心无供应商特殊分支；最小 SPI 只包含本插件实际使用的稳定接口。

**需要单独编写的子计划:** 唯一详细计划已建立为
`docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md`。其当前状态为 `IN_PROGRESS`；仓内实现、插件部署 E2E、
QUALITY、迁移链和所选 HEAVY 已完成，供应商一致性和现场联合验收仍为 `NOT RUN — BLOCKED`。

**风险及防止阶段越权的约束:** 插件只可访问 Transport Port 和 DeviceCommand 应用端口，不得访问其内部状态机、HTTP、
认证或凭据；禁止因供应商内部协议不同而修改 WES 固定路径、公共包络或增加兼容 Adapter。

## 14. Phase 9：分拣执行插件组优化

**Objective:** 按真实工作线和获批设备合同附录分别交付自动分拣、人工分拣、满箱交换和复杂出库，不建设通用分拣工作流。

**Authoritative inputs:** 顶层 SPEC §11.2–§12、`docs/contracts/wms-inbound-putaway-integration-requirements.md`、
`docs/contracts/wms-outbound-picking-task-integration-requirements.md`、第三方设备统一接口白皮书、每条线真实拓扑与供应商原始资料、
Phase 8 复审结果、Phase 7 Device/ECS 和 Phase 6 Transport。

**Entry conditions:** Phase 8 退出门禁通过；入库上架与自动出库合同均已联合批准；每个实际插件、设备合同附录和部署组合明确；
各详细计划获批。

**Scope:** 自动上架、人工分拣、满箱交换和复杂出库执行插件；所需设备合同附录、endpoint/device 绑定和供应商一致性验收；
复用既有 Phase 7 生产 Adapter 与 Phase 6 Transport Port；完整上架计划、获批交换成员、目标 Bin 供退、SCAN1—SCAN4、
逐盘晚绑定 PUT、业务专属 NG、同线进出和 WMS 库存权威。

**Explicit out-of-scope:** `SorterCorridor`、库存权威、动态发现、统一厂商认证三选一、第二个 WES 设备 HTTP Adapter、
公共 HTTP Client、预建 BASIC、通用工作流 DSL。

**Deliverables:** 获批入库上架和出库业务合同、每个真实设备的获批合同附录、endpoint/device 绑定和供应商一致性验收；
每个插件独立包、fixture、测试、构建产物和显式装配；目标 Bin 供退、四扫描点、NG 出口和人工恢复的现场验收；客户镜像
清单；不新增 WES HTTP Adapter。

**旧所有者删除或交接清单:** 旧业务代码和测试已由 Phase 5 删除；设备旧 sender 已由 Phase 7 删除。本阶段不得新增供应商
私有裸 Client、重复连接池/HMAC/路径或协议映射；插件包下线时同步移除 workspace、镜像和 Composition Root 绑定。

**测试所有权与重量要求:** 供应商一致性验收独立拥有设备附录/集成/异常/恢复场景；插件独立拥有业务单元/集成/E2E/
韧性/并发/负载；核心只验证统一公共协议和通用机制，Phase 2 只验证 Transport。

**与前后阶段的 atomic handoff:** 消费 Phase 7 Device/ECS、Phase 6 Transport 和 Phase 8 已验收的最小 SPI；全部实际
交付包完成后向 Phase 10 提交零散旧所有者清单。本阶段不得直接消费 Phase 2 HTTP Transport 或实现第二个 Adapter。

**Exit gate:** 两条自动线和两条人工线使用同一插件的不同配置实例；全部 endpoint/device 绑定明确，供应商与插件分别通过；
全部设备 HTTP 调用仍经 Phase 7 唯一生产 Adapter；核心无插件 import/fixture/供应商名称分支；
无动态平台扩张。

**需要单独编写的子计划:** 分别编写并批准：
`docs/superpowers/plans/2026-08-03-automatic-sorter-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-manual-sorter-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-full-bin-exchange-plugin-convergence.md`、
`docs/superpowers/plans/2026-08-03-complex-outbound-plugin-convergence.md`。若真实现场证明应合并为同一部署插件，
先修订本阶段清单，不预建空包。自动分拣插件计划必须覆盖人工切换的 `INBOUND` / `OUTBOUND` 两种模式，不为自动上架
另建重复插件或第二套基础能力。

**风险及防止阶段越权的约束:** 禁止从粗分计划复制改名；禁止把供应商私有认证或协议差异引入 WES，现场差异必须由
供应商 ECS/网关在统一接口边界内消化。

## 15. Phase 10：旧平台代码最终闭环清理

**Objective:** 在 Phase 2 已交付唯一新基础层、Phase 3–9 的目标替代均已完成的基础上清除跨阶段残留，证明生产态只有
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

**与前后阶段的 atomic handoff:** 接收 Phase 3–9 的删除余量并核验 Phase 2 新基础层的唯一性；全部零命中后才允许
Phase 11 固化最终 metadata。

**Exit gate:** 机器门禁证明旧架构、裸 Client、重复传输和无依据认证零引用；应用/Celery/部署只装配最终对象、WMS/RCS
Adapter、设备统一接口和明确插件。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-legacy-production-path-removal.md`，同步其阶段号为 Phase 10。

**风险及防止阶段越权的约束:** 缺席扫描按语义和所有者判断，不按 `replay`/`reconciliation` 等词批量删除，避免误伤最终可靠行为。

## 16. Phase 11：旧数据模型与迁移链清理

**Objective:** 最终模型稳定后删除未发布系统的旧 schema/revision，生成唯一可从空库建立系统的 Alembic 基线。

**Authoritative inputs:** Phase 10 零旧路径结果、最终 SQLModel metadata、Alembic 规则、TimescaleDB 必要对象。

**Entry conditions:** Phase 10 退出门禁通过；最终核心、WMS/RCS Adapter、设备统一接口和插件所需持久化模型稳定；无旧表活动消费者。

**Scope:** 删除旧表/字段/约束/索引和 revision chain；清空开发/测试数据库；使用 Alembic generator 生成随机 revision ID；空库验收。

**Explicit out-of-scope:** 旧数据转换、桥接表、临时回填、downgrade、兼容 schema 和生产历史数据迁移。

**Deliverables:** 单一干净初始基线；metadata/schema/约束/索引/扩展对象一致性结果。

**旧所有者删除或交接清单:** 删除 Runtime/Manifest/Capability/Intent/Effect/Hold/Recovery/Reservation 及旧认证/Provider 持久字段；
删除只验证旧 revision 的测试，标注 successor 或 NONE 理由。

**测试所有权与重量要求:** 只验证 migration 生成物、空库 upgrade 和 metadata 一致性；不保留旧 upgrade/downgrade/回填测试。

**与前后阶段的 atomic handoff:** 只接受 Phase 10 已证明无消费者的模型删除集；向 Phase 12 交付唯一空库基线。

**Exit gate:** `migrations/versions/` 只含最终初始基线及其后真实 revision；空库一次 upgrade head 成功；无旧迁移/兼容断言。

**需要单独编写的子计划:** Phase 11 详细计划已建立为
`docs/superpowers/plans/2026-08-15-wes-schema-and-migration-baseline-reset.md`；Phase 10 退出后必须基于当前实测结果重新冻结实施清单并取得批准，才可开始基线重置。

**风险及防止阶段越权的约束:** 禁止在模型未稳定前生成基线；禁止因保留开发数据引入兼容迁移。

## 17. Phase 12：最终基线与系统验收

**Objective:** 从干净环境证明核心、Phase 2 HTTP 基础层、当前范围 WMS/RCS Adapter、设备统一接口、供应商一致性、插件、
数据库基线、部署装配和缺席门禁共同满足目标架构。

**Authoritative inputs:** SRS、顶层 SPEC、Phase 1–11 退出证据、当前 ADR/合同、插件指南、TODO 与运维文档。

**Entry conditions:** Phase 11 空库基线通过；全部当前范围 WMS/RCS Adapter、供应商 ECS/网关和插件独立验收；所有阶段
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

**与前后阶段的 atomic handoff:** 接收 Phase 1–11 完整证据；只有全部通过才允许最终结果合并 `develop`，无后续兼容阶段。

**Exit gate:** SPEC §15 全部验收通过；测试计划 Task 7 完成；只有 Phase 2 基础层直接依赖 httpx；设备统一接口无
供应商私有认证或协议分支；无旧架构/迁移/兼容路径/核心插件污染；最终结果可合并。

**需要单独编写的子计划:** 启动前编写并批准
`docs/superpowers/plans/2026-08-03-wes-final-architecture-acceptance.md`，同步其阶段号为 Phase 12。

**风险及防止阶段越权的约束:** 禁止用最终验收临时实现缺失能力或放宽门禁；失败必须回到拥有该职责的阶段修正。

## 18. 发现的矛盾与最终裁决

| 矛盾或歧义 | 仓库证据 | 最终裁决 |
| --- | --- | --- |
| 旧总控把 WMS 作为 Phase 2 | Master/WMS 子计划旧编号 | Phase 2 独立为公共 HTTP；WMS 为 Phase 3，最终验收顺延到 Phase 12 |
| 旧 Phase 5 要把新 Transport 接到即将删除的旧插件 | Phase 4 publisher 必须有真实 consumer；用户已接受零插件中间态 | Phase 5 先退役旧插件闭包；Phase 6 只交付可安装 Transport 基线；Phase 8 首个真实插件再绑定 consumer |
| 只删除 `src/app/runtime/workline_plugins/` 容易被误认为完成 | 目录外仍有 WorkLine、Runtime、API、Celery、配置和测试直接引用 | Phase 5 按完整活动执行闭包和 successor/`NONE` 矩阵退役，不按目录批量删除 |
| 统一设备 HTTP Adapter 曾被塞入 Phase 4 或插件阶段 | 旧摘要把 DeviceCommand、设备状态和 ECS HTTP 与 Transport/粗分混建 | Phase 7 成为正式独立 Device/ECS 阶段；Phase 8/9 只消费，不修补核心能力 |
| 旧 WMS 子计划把推测性认证放入 Phase 2 | WMS Task 5–8、`canonical_dispatch.py` 与 `sign_wms_hmac_request`，但冻结 WMS outbound 合同无认证要求 | Phase 2 不实现 `AuthStrategy`、凭据、HMAC、Clock、Nonce 或认证 seam；WMS 旧草案标记 Needs re-review，将来只有真实合同明确要求时才修订计划 |
| 现有 `external_http_*` 看似公共但耦合旧平台 | import `operation_registry`、Provider Profile、SystemOutbox/`idempotency_key` | 只复用可证明的 primitive，不把旧 Provider/Outbox 设计提升为目标公共层 |
| 当前既有长期 WMS Client，也有多个每请求 Client | WMS query/effect lane 与 DeviceCommand/旧 Gateway/Outbox | 目标为每外部系统每进程一个 Client；分阶段原子切换，Phase 10 最终零散落 Client |
| 旧 Provider 配置可表达 NONE/HMAC，容易被误读为当前需求 | `provider_profile.py` 与 `external_http_binding.py` | 它们是后续原子切换时删除的旧 owner，不进入 Phase 2 公共合同或部署配置 |
| SPEC §14 仍是十一阶段且要求 Phase 5 切换 Transport | SPEC §14.2–14.3 | 同步为十二阶段：Phase 5 插件退役、Phase 6 Transport、Phase 7 Device/ECS、Phase 8/9 插件重写 |
| 当前存在旧 `DeviceCommand`/Projection 类型 | 生产代码扫描 | Phase 5 只解除其旧插件调用；Phase 7 从合同和最终不变量出发重建，不把旧类型当目标模板 |
| 旧远端 feature 分支包含大量历史实现 | 分支差异远大于当前 develop | 不合并、不 cherry-pick，不作为需求；只有当前 develop 真实代码和权威文档作为实施输入 |
| WMS 初稿中的 method/path 与目标业务消费者尚未逐项批准 | `docs/hardware/wms_rcs_interface_requirements.md` 与当前 WMS 合同 | 保留厂商初稿原文；Phase 3 只实现共享 Client，具体业务开发再逐项确认 wire，不从旧编号或既有实现推定 |

## 19. 自审结果

| 检查项 | 结果 | 说明 |
| --- | --- | --- |
| 占位标记 | 分阶段阻断 | PickingTask 的正式 JSON Schema、枚举闭集及业务 fixture 仍未冻结，只阻断其所属后续业务阶段；Phase 4 的四类 Transport wire、WMS 异步回调统一信封和 Task 0 已批准，不受其影响 |
| 兼容设计 | 通过 | Phase 5 明确允许零业务插件，不提供空插件、no-op consumer、shim、alias、fallback、双写、双读或旧数据兼容 |
| 重复职责 | 通过 | Phase 5 只退役旧插件闭包；Phase 6 只拥有 Transport；Phase 7 只拥有 Device/ECS；Phase 8/9 拥有业务插件 |
| 测试过重 | 通过 | Phase 5 按 owner 处置旧测试；Phase 6/7 分别验证基础能力；Phase 8/9 插件测试不进入核心 |
| 未确认推测能力 | 通过 | 不含认证 seam、BASIC/HMAC、动态拦截器、DSL、Service Locator、动态发现、未来协议或空插件 |
| 敏感信息 | 通过 | Phase 2 无凭据与 Secret；日志合同仍禁止 headers/body/query/原始异常文本 |
| 阶段越权 | 通过 | Phase 5 不接 Transport、不实现 Device/ECS、不重写插件；上一阶段未退出不得启动下一阶段 |
| 当前状态准确性 | 通过 | Phase 1 至 7 核心基线已完成；Phase 8 仓内实现已完成但外部验收阻塞，仍为 `IN_PROGRESS`；Phase 9 至 12 未开始 |

## 20. 总体完成定义

只有同时满足以下条件，本计划才完成：

1. Phase 2 公共传输、Phase 4 WMS Transport Adapter、独立 Device/ECS 能力和执行插件分别拥有单一职责与独立验收。
2. 只有 Phase 2 基础层直接依赖 httpx；WMS Transport Adapter 与后续设备统一接口只消费已装配 Transport，核心和插件不可见
   httpx、认证、凭据或供应商私有协议。
3. 核心可靠性不变量由最终对象测试证明；设备公共 wire、供应商一致性、WMS 业务结果合同和执行映射分别由其唯一所有者证明。
4. 旧生产架构、旧 HTTP owner、无依据认证、旧测试所有者、旧配置、兼容路径和旧 migration chain 全部归零。
5. 最终数据库可以从空库一次建立，不需要旧数据、旧 revision 或转换脚本。
6. 当前态文档、active TODO、代码、测试、schema、部署配置和 composition root 共同指向同一个最终架构。

## 21. Implementation Tasks

Phase 6 Transport 与 Phase 7 Device/ECS 核心生产基线均已完成。两阶段分别拥有独立可靠对象、生产装配和测试证据；
已完成的过程计划已归档，不再保留为项目内当前真源。退役插件活动残留收敛及合入后 tombstone 清理已完成；Phase 8 合同、
SDK、可靠对象、粗分插件、静态装配、迁移链、质量门禁和插件部署 E2E 已完成。供应商一致性和现场闭环仍须独立验收；不得把
核心测试、mock 边界或插件部署 E2E 当成真实供应商或现场验收。

| 顺序 | 任务 | 状态 | 主要验收 |
| --- | --- | --- | --- |
| 1 | Phase 6 Transport 正式基础基线 | Completed | Transport 可安装但无业务 producer；旧 owner 已收敛 |
| 2 | Phase 7 DeviceCommand/ECS 核心生产基线 | Completed | 唯一生产装配、schema、旧 owner、FAST、PostgreSQL、broker E2E 与精确 HEAVY 已闭环 |
| 3 | Phase 5 后退役插件活动残留收敛 | Completed | `5fe59968` 已合入；deletion tombstone 已清理，完成计划已移出项目归档 |
| 4 | Phase 8 粗分机参考插件实施 | In progress — external blocked | 仓内实现与插件部署 E2E 已完成；等待真实供应商一致性和现场联合验收 |

Phase 8 仓内实施与插件部署 E2E 已完成；这不表示供应商一致性、真实 RCS 顺序能力或现场业务闭环已通过。

## 22. 工程复审完成摘要

- **Scope：** Phase 5 只退役旧插件执行闭包；Phase 6 只收敛 Transport；Phase 7 只收敛 Device/ECS；Phase 8/9 才交付业务插件。
- **Architecture：** TransportTask 与 DeviceCommand 是平行可靠对象；零插件态不创建 no-op consumer，也不制造虚假生产闭环。
- **Code Quality：** 删除按 owner 和引用闭包判断，不按目录或关键词；不搬迁旧源码，不保留兼容 facade。
- **Test Review：** 核心、WMS Adapter、供应商一致性和插件测试所有权严格分离；通用不变量先有 successor，旧业务测试后删除。
- **Performance：** 本轮阶段调整不引入新轮询、缓存、registry 或运行时扫描；后续 worker 必须有界。
- **Failure modes：** 已覆盖只删目录、短命 Transport 接线、空插件、旧 DeviceCommand 升格、批量误删测试和历史文档残留。
- **Parallelization：** Phase 5 至 7 核心基线和 Phase 8 仓内实施已完成；后续供应商一致性与现场联合验收按详细计划推进，
  核心公共 wire 不并行改造。
- **NOT in scope：** Phase 7 不实现 Phase 8/9 的业务插件；任何阶段都不得删除 `docs/hardware/`。

## GSTACK REVIEW REPORT

| Review | 本轮状态 | 发现 | 未解决 | 说明 |
| --- | --- | ---: | ---: | --- |
| ENG REVIEW | CLEAR | 3 | 0 | 已补齐测试处置、schema cutover 和批准状态三项问题；Phase 5 详细计划可实施、可审计 |
| ADVERSARIAL REVIEW | CLEAR | 4 | 0 | 否决短命 Transport-to-legacy 接线、只删目录、空插件和旧 DeviceCommand 升格 |
| SEQUENTIAL REVIEW | CLEAR | 1 | 0 | 十二阶段顺序通过依赖、YAGNI 和测试所有权复核 |
| PHASE 4 SCOPE REVIEW | CLEAR | 1 | 0 | Phase 4 保持 Transport 暗构建，不反向扩张 Device/ECS 或插件能力 |
| PHASE 6 PRE-IMPLEMENTATION REVIEW | CLEAR | 4 | 0 | 已修正 Port/wire 身份分层、当前实施基线、红灯测试时序和唯一生产式 smoke |
| DESIGN REVIEW | N/A | 0 | 0 | 无 UI/交互范围 |
| DX REVIEW | CLEAR | 0 | 0 | 新阶段使用显式 owner、静态装配和独立包，避免动态平台和 Service Locator |

**VERDICT：十二阶段顺序已冻结；Phase 1 至 7 核心基线均已完成；Phase 8 仓内实现与插件部署 E2E 已完成，但仍为
`IN_PROGRESS — EXTERNAL BLOCKED`；供应商一致性和现场闭环尚未通过；Phase 9 至 12 未开始。**

NO UNRESOLVED DECISIONS
