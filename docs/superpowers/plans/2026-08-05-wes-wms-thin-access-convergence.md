# WES Phase 3 无状态 WMS ACL 收敛计划

> **Status:** `BLOCKED_AT_TASK_1`。
> 当前只允许继续完成 Task 1 的跨能力合同门禁；门禁关闭后，仅已单独获批 wire 的能力可以进入 TDD，未获批能力保持不存在。

**Goal:** 消费 Phase 2 `OutboundHttpTransport`，为目标态 SRS 业务消费者提供无状态、类型化、最小化的 WMS
Anti-Corruption Layer；不实现或承载 WMS 转发的 RCS/AGV/CTU Transport 能力。

**Requirements baseline:** `docs/architecture/SRS.md`。

**Business wire owner:** `docs/contracts/wms-northbound-interaction-contract.md`。

**Architecture baseline:**
`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`。

## 1. 阶段裁决

### 1.1 Phase 3 是什么

Phase 3 只暗构建 WMS 业务 ACL：

- WMS 权威业务事实查询。
- WES → WMS `PickingTask` 来源绑定请求与业务事实通知。
- WMS → WES 业务命令的 DTO 与无状态校验/标准化。
- 固定 method/path、严格 DTO、请求编码、响应解析和单次结果翻译。
- 消费 Phase 2 Transport，不复制连接池、超时、响应上限或传输事实分类。

每项公开能力必须有已批准的目标态消费者；“消费者”不要求已接入当前生产源码，但必须能定位到目标阶段、组件和调用时机。
厂商初稿有接口但目标态业务无消费者时，不实施。

### 1.2 Phase 3 不是什么

Phase 3 不包含：

- RCS/AGV/CTU 搬运、补给、交换、旋转、status 或 cancel。
- `Transport Port`、`TransportTask` 或 WMS 转发 RCS Client。
- `WmsConfirmation`、`InboundEvidence` 的持久化和业务生命周期。
- 数据库表、Repository、Service、Alembic Migration、调用 evidence 或 Circuit Breaker。
- retry、scanner、claim、fencing、reconciliation 生命周期或投影推进。
- WorkLine Decision、设备命令、厂商 Payload 或生产 Composition Root 接线。
- 旧 Provider/Profile/Registry、兼容 alias、fallback、双读、双写或 shadow request。

即使搬运 HTTP 最终发给 WMS，只要语义是 WMS 转发 RCS，仍属于 Phase 4 Transport，而不是 Phase 3 WMS ACL。

## 2. 与前后阶段的唯一接缝

| 阶段 | 向 Phase 3 提供 / 从 Phase 3 接收 |
| --- | --- |
| Phase 2 | 提供单次发送、长期 Client、有界响应和传输事实；不提供 WMS 业务解释 |
| Phase 3 | 提供类型化 WMS Query、Picking Source Binding、Confirmation Port、inbound DTO/normalizer 和封闭单次调用结果 |
| Phase 4 | 直接消费 Phase 2 构建 WMS 转发 RCS Adapter；消费 Phase 3 三条业务端口构建 `WmsConfirmation` 与 `InboundEvidence` 可靠 owner |
| Phase 5 | 原子切换生产消费者和 Composition Root，删除旧 WMS/Transport owner；不保留双轨 |

Phase 4 可以直接依赖 Phase 2 的 Transport Protocol 构建 Transport Adapter，但不得绕过 Phase 3 直接调用 WMS
业务查询或确认接口。

目标调用与资源生命周期固定为：

```text
Composition Root
  ├─ Phase 3 outbound factory（每个进程构造一次，system_id 固定为 "wms"）
  │    └─ HttpWmsGateway（拥有并私有持有 Transport）
  │         ├─ WmsBusinessQueryPort
  │         ├─ WmsPickingSourceBindingPort
  │         ├─ WmsBusinessConfirmationPort
  │         └─ aclose() → OutboundHttpTransport.aclose()
  └─ WmsInboundNormalizer（独立纯解析对象；无 origin、Transport 或 aclose）
```

Outbound 业务消费者只依赖窄端口；Phase 4 inbound ingress 只依赖独立 normalizer。只有 Composition Root 持有 factory
返回的具体 Gateway，并在进程关闭时调用幂等 `aclose()`。关闭失败原样传播，不由 Adapter 吞掉。Phase 3 不把 Transport
或裸 `httpx.AsyncClient` 暴露给业务消费者，也不让 outbound 配置或生命周期影响 inbound 解析。

## 3. 消费者驱动准入

Phase 3 不再维护固定“33 项 operation”目标。能力清单只来自
`docs/contracts/wms-northbound-interaction-contract.md` 的消费者矩阵，并遵守：

1. 一项能力对应至少一个目标态业务消费者，并登记目标阶段、具体组件和调用时机；只写“SRS 场景”不算消费者。
2. 同一业务事实只允许一个主要 wire owner。
3. 查询只返回 WMS 权威事实，不用于为 `PickingTask` 拼装、锁定或重新分配来源，也不建立本地主数据同步。
4. `request_picking_source_bindings` 只请求 WMS 原子生成完整来源绑定；WMS 拥有库存资格、库存锁、料格冻结和跨任务分配。
5. 确认只提交已发生物理事实或明确业务义务，不隐式触发 Transport；逐项位置事实与整单完成事实不得合并或双写。
6. 未获 WMS 批准的 wire 保持缺席，不用占位 DTO、generic payload 或旧实现补全。

当前保留候选分为四类：

- 权威事实查询：物料、货架、料箱、GRN/Package、库存和预留。
- 来源绑定请求：`STARTING` 的 PickingTask 请求 WMS 返回完整原子来源绑定或明确无完整方案。
- 业务确认：预留/释放、入库、非 PickingTask 库存转移、PKG 绑定、来源 NG、逐项位置完成、PickingTask 完成、人工工作位就绪。
- 业务输入：PickingTask 目标项创建/更新、原子替代来源批次或明确无完整替代方案、恢复/取消，以及人工作业完成。

其中条件能力和未批准 wire 以合同 §5 为准；Task 1 关闭前不得生成代码文件清单。

## 4. 目标代码边界

Task 1 退出前只冻结责任、公开边界和禁止依赖，不预先冻结文件树。Task 1 退出后再根据获批 wire 生成最小文件清单，且只允许
包含以下责任：

- 公开边界：三条 outbound 业务端口、独立 `WmsInboundNormalizer`、最小 outbound 配置、封闭 outcome 和 outbound factory。
- 私有实现：固定 method/path 的 DTO 编解码、无状态 Gateway，以及仅获批能力需要的值对象。
- 共享值对象只有在至少三个获批能力出现相同语义和相同约束时才提取；不预建 `_shared.py`、通用请求基类或扩展框架。
- factory 将 Phase 2 builder 的 `system_id` 固定为 `wms`，不把它变成配置项或调用方参数。
- outbound factory 每个进程只构造一个 Gateway/Transport；Gateway 拥有 Transport 并提供幂等关闭，单次业务调用不得
  创建 Client。Normalizer 直接构造，不进入 factory 或 Gateway 生命周期。

明确不得出现：

```text
models/
repositories/
services/
call_control.py
circuit_breaker.py
wms_outbound_call_evidence
wms_outbound_circuit_breaker
transport/
reconciliation/
```

`HttpWmsGateway` 行为无状态，只持有 Phase 2 Transport。每个公开调用恰好形成一次 `send`；无自动 retry、轮询、
分页聚合或 callback 等待。

### 4.1 后续新增 WMS API 的最小实施路径

后续业务需要新增 WMS API 时，不扩建通用平台。开发者按同一条显式纵切片完成：

1. 在北向合同登记目标阶段、具体消费者、调用时机、WMS 权威事实或业务义务；无法定位真实消费者时停止。
2. 使用单项能力批准模板取得 WMS 对 method/path、闭集 DTO、错误码、幂等、权威元数据、数量/字节预算和版本化
   machine-readable fixture/schema 的批准。
3. 将能力放入职责匹配的既有窄端口；只有职责确实不同才增加新端口，不按接口数量拆端口。
4. 增加操作专属 request/result/outcome、一个显式 Gateway/Normalizer 方法和一次 `OutboundHttpRequest` 映射；获批 method
   不在 Phase 2 枚举内时，先以独立 TDD 变更扩展 Phase 2。
5. 在 `tests/contracts/wms_adapter/` 先以获批 fixture/schema 写失败合同测试，再实现至通过；不得用 Phase 2 Transport
   测试、核心业务测试或人类 Markdown 正文替代 wire 验收。
6. 同步公开导出、消费者矩阵和实现状态。首个获批能力作为结构参考纵切片；复制其职责结构，不复制业务字段或抽象层次。

正常单项扩展只触及该 operation 的 DTO、一个端口方法、Gateway/Normalizer 私有绑定、获批 fixture 和合同测试。相同语义
与约束未在至少三个获批能力中重复前，不提取共享 helper；禁止生成器、配置驱动 API、生产 registry、generic `call`、
动态发现或公共 `WmsCallSpec`。

## 5. 公开端口

Phase 3 提供三条职责互斥的 outbound 业务端口：

- `WmsBusinessQueryPort`：读取 WMS 权威事实，不产生副作用。
- `WmsPickingSourceBindingPort`：只暴露语义 operation `request_picking_source_bindings`，请求 WMS 为 `STARTING`
  PickingTask 原子生成完整来源绑定。
- `WmsBusinessConfirmationPort`：提交同步业务确认，不拥有可靠义务生命周期。

`request_picking_source_bindings` 的成功结果必须精确覆盖全部目标任务项，每项携带完整 SixInOne 和唯一 `PkgID`；
另一合法结果是明确“无完整方案”。部分绑定不得生效，也不得提前申请目标架。method/path/wire 仍须由 WMS 批准。

来源绑定校验按唯一 owner 拆分：

| 校验 | Owner | Phase 3 行为 |
| --- | --- | --- |
| 字段闭集、联合 DTO 完整、请求参数与同步响应关联、任务项集合精确相等、SixInOne 完整、响应内 `PkgID` 唯一 | Phase 3 WMS ACL | 失败映射为合同无效，不返回部分绑定 |
| 当前有效请求、持久化请求身份、绑定代际、迟到响应和幂等义务 | Phase 4 可靠对象 | Phase 3 不访问或推断持久化状态 |
| 未完成资格、无在途/unknown、现场物理结构、LIFO 连续前缀、目标面顺序和状态推进 | Phase 8 自动出库插件 | Phase 3 不接收这些业务上下文，也不以 ACL 测试证明这些规则 |

Phase 3 只比较本次方法实参和本次同步响应能够证明的关联，不判断该请求在持久化业务对象中是否仍然有效。

WMS inbound 只提供 DTO 和 normalizer，不在 Phase 3 暴露绕过 `InboundEvidence` 的业务处理端口。Phase 4 API/应用层
负责先持久化证据再 ACK，并调用对应业务对象。Inbound callback 的认证保持 API ingress 独立所有权；outbound
`NONE` 不能代替入站调用方认证合同。

禁止提供：

- `WmsForwardedTransportClient`
- `WmsCallControl`
- 字符串 operation selector
- generic `call`
- 生产 registry 或动态发现

## 6. Outcome 边界

Phase 3 的 outcome 只表达本次调用事实，并完整保留 Phase 2 `delivery_state` 与 `failure_kind`：

- WMS 成功并通过 DTO 校验。
- WMS 明确业务拒绝。
- 请求未发送。
- 交付状态未知。
- 已收到响应但响应阶段读取、协议、大小或清理失败。
- 已完整收到响应，但状态码、正文或 DTO 不符合获批合同。

Outcome 不决定 retry、dependency pause、terminal 或投影变化。Phase 4 可靠对象根据 Phase 2 交付事实、Phase 3
业务结果和自身持久化状态作出裁决。

单次调用分支必须保持封闭：

```text
显式端口方法
  ├─ 请求 DTO / 编码失败 ──────────────→ 合同无效；0 次 send
  └─ OutboundHttpTransport.send（恰好一次）
       ├─ CancelledError ──────────────→ 原样传播
       ├─ NOT_SENT ────────────────────→ 请求未发送
       ├─ DELIVERY_UNKNOWN ────────────→ 交付状态未知
       └─ RESPONSE_RECEIVED
            ├─ failure_kind 非空 ──────→ 响应阶段传输失败；原样保留 failure_kind，不推导暂停策略
            ├─ 获批成功 DTO ──────────→ operation-specific success
            ├─ 获批业务拒绝 DTO ──────→ business reject
            └─ 状态码/正文/DTO 不合法 ─→ 合同无效
```

每个 operation 保留自己的成功结果类型；只有传输失败、合同无效和业务拒绝等跨 operation 且语义完全相同的分支共享
值对象，避免 generic payload 或一个宽泛的成功结果类型。

## 7. Implementation Tasks

以下是唯一实施任务体系；所有评审 finding 均已并入 Task 1–7，不再维护第二套 T1–T4 摘要清单。

### Task 1：冻结跨能力合同门禁

**范围：纯文档。**

- [x] 撤销“33 项固定 surface”和 Phase 3 READY 结论。
- [x] 将 WMS 转发 RCS 的 submit/status/cancel 全部移交 Phase 4。
- [x] 删除 reconciliation、通用 load-unit transport、复合粗分 admission 和错误方向的 manual task 候选。
- [x] 为保留、条件保留和排除能力登记候选消费者及 successor/`NONE`。
- [x] 冻结 PickingTask 只含目标项、STARTING 原子来源绑定、WMS 库存权威、逐项位置事实与整单完成独立、替代批次原子语义。
- [ ] 将每个候选消费者补全为目标阶段、具体组件和调用时机；由业务方确认库存查询、reserve/release、transfer 的真实
  目标态消费者及原子边界，无法定位者删除候选能力。
- [ ] 为每个需要组合多项 WMS 事实的消费者冻结一致性合同：优先使用 WMS 提供的共同 snapshot/version；若 WMS 不提供，
  必须明确可接受的读取顺序、有效窗口和 fail-closed 条件。不得以复合 WMS 准入决策接口替代 WES 本地规则。
- [ ] 为每个列表或批量结果冻结最大项数、最大 wire/decoded 字节数和超限语义，并证明不超过 Phase 2 的硬上限；无法满足
  且确需分页时，先单独修订基础传输/业务合同，本阶段不预建分页 seam。
- [ ] 冻结 API ingress 所需的调用方认证合同或明确的局域网 `NONE` 条件；各 inbound operation 的 wire 仍按单项能力
  批准，不进入此跨能力门禁。认证决定不进入 Phase 2 Transport，也不从 outbound `NONE` 推导。
- [x] 冻结单项能力批准模板：目标消费者、method/path、闭集 request/result、错误码、幂等语义、权威元数据来源、数量与
  字节预算，以及 WMS 批准的版本化机器可读 fixture/schema。人类阅读文档不得被测试解析为合同真源。

**验证：** Markdown 格式、项目内引用闭包、硬件原文 hash、`git diff --check`。不新增或修改测试代码。

### 单项能力批准门禁（适用于 Task 2–5）

每项能力独立通过以下门禁后才能进入 TDD；一个能力未获批不阻塞其他能力，但它必须保持代码、DTO 和测试均不存在：

- 目标消费者、调用时机和原子边界已经确认。
- WMS 已批准 method/path、闭集 request/result、错误码、幂等语义以及数量/字节预算。
- 对照 Phase 2 `OutboundHttpMethod` 检查获批 method；若未支持，先以独立 TDD 小变更扩展 Phase 2 通用枚举和传输测试，
  不得在 Gateway 中直用 HTTPX 或歪曲 WMS method。
- 权威查询的 version/time/provenance 有真实来源并符合 `authority-matrix.md`；WMS 未提供的 `source_version` 或权威时间不得
  由 Phase 3 伪造。若 `ExternalAuthorityMetadata` 无法由获批 wire 满足，先修订目标元数据合同，不保留旧字段兼容。
- WMS 批准的版本化机器可读 fixture/schema 已可作为独立验收输入；实现方自行编写的 MockTransport 只负责驱动该合同，
  不能同时充当 wire 真源。

Phase 3 最终退出仍要求所有保留能力均已通过门禁并实施；无法获批或无法定位消费者的候选能力直接删除，不保留占位。

### Task 2：建立最小无状态合同

**前置：Task 1 全部完成，且至少一项 outbound 能力通过单项批准门禁。**

- 使用 TDD 建立最小配置、封闭 outcome、concrete Gateway/factory 生命周期骨架，以及第一项获批能力需要的严格 DTO；
  不预建通用 DTO 基类，共享类型只在三个以上获批能力出现相同语义时提取。
- 配置只包含 WMS origin 与 timeout；当前认证固定为 `NONE`。
- 不接受 Session、Repository、Provider、credential、retry 或 breaker 参数。
- 先证明 0/1 次 send、三种 delivery state、响应阶段 failure、取消传播和幂等关闭，使 Task 3/4 能在同一 Gateway 上
  逐项完成可执行的红绿重构。

### Task 3：实现获批查询

- 每项查询先独立通过单项能力批准门禁，再建立语义模块和显式端口方法；不等待其他无关能力获批。
- 每个查询在 Task 2 Gateway 上完成固定 wire、一次请求和一次有界响应；没有缓存、分页 seam 或自动续页。
- 查询不得用于 PickingTask 来源选择、库存锁、料格冻结或跨任务分配。
- 测试以 WMS 批准的版本化 fixture/schema 证明 DTO/wire，不测试 WorkLine 决策或库存算法。

### Task 4：实现获批来源绑定与业务确认

- 以独立 `WmsPickingSourceBindingPort` 实现 `request_picking_source_bindings`；不并入 Query 或 Confirmation Port。
- 只翻译完整原子来源绑定或无完整方案，不决定 `WAITING_STOCK`、目标架调度或业务推进。
- 每项来源绑定或同步业务确认先独立通过单项能力批准门禁；不等待其他无关能力获批。
- 每个方法在 Task 2 Gateway 上只负责 DTO、固定 wire 和结果翻译。
- 逐项位置通知与 `confirm_picking_completed` 分别建模；不复用 `transfer_inventory` 双写同一事实。
- 不持久化来源绑定或确认义务，不自动 retry，不把成功直接解释为 WorkLine 推进。

### Task 5：实现 WMS inbound DTO 与 normalizer

- 每项 inbound operation 先独立通过单项能力批准门禁，再实现 PickingTask 或人工作业输入 DTO。
- 只验证封闭 wire 和规范化结果，不创建任务、不访问数据库、不返回业务完成。
- 将 `WmsInboundNormalizer` 作为 Phase 4 可消费的公开边界导出；不导出 FastAPI handler、registry 或通用 command bus。
- Normalizer 独立构造，不消费 WMS origin、outbound factory、Transport 或 `aclose()`。
- Phase 4 负责 `InboundEvidence`、幂等、ACK 和业务对象推进。

### Task 6：收口 outbound Gateway、factory 与公开导出

- 收口 Task 2 已建立的 outbound Gateway/factory，不在此时才引入发送或 lifecycle 行为，也不接入 inbound normalizer。
- Gateway 只依赖 Phase 2 `OutboundHttpTransport`。
- factory 只消费 Phase 2 builder，不接收 `session_factory`，并固定 `system_id="wms"`。
- factory 返回的具体 Gateway 拥有 Transport，提供幂等 `aclose()`；关闭错误和取消原样传播。
- 每个方法固定映射一个获批 method/path 和 request/result 类型。
- 取消原样传播；不实现 retry、breaker 或持久化 evidence。

### Task 7：边界门禁与退出验证

- 新包零数据库模型、Migration、Repository、Service、breaker、transport 和旧 WMS import。
- Phase 4 暗构建代码可以依赖 Phase 3 的公开端口、DTO/normalizer 和 outcome；Phase 5 原子切换前，旧生产消费者与
  Composition Root 不得 import 或接线新包，也不得建立 feature flag 或双轨。
- WMS ACL 的唯一 FAST 所有者是 `tests/contracts/wms_adapter/`；不得复用 Phase 2、旧 `wms_integration`、插件或
  `tests/mock/` 断言证明新 ACL。
- 运行新 WMS ACL FAST 合同、Phase 2 回归、测试拓扑门禁、Ruff、类型检查、Import Linter 和 quality profile。
- 为 `src/app/wms_adapter/**` 增加精确 HEAVY selector mapping。Phase 3 暗构建期无数据库、无生产消费者且全部行为由
  MockTransport FAST 合同承接，经 selector 评审后显式 `NONE`；不是因为“新增测试路径”而更新 mapping。

### 跨任务 TDD 覆盖矩阵（在 Task 2–6 内执行）

**前置：Task 1 全部完成，并且目标 operation 已通过单项能力批准门禁。纯文档 Task 1 不新增或修改测试代码。**

| FAST 行为所有者 | 必须先失败再实现的行为 |
| --- | --- |
| 最小配置与 factory | 非法 origin/timeout 在创建 Transport 前失败；builder 只接收获批配置并固定 `system_id="wms"`；构造失败原样传播；同一 Gateway 的多次业务调用复用同一 Transport，不按调用创建 Client |
| WMS 合同与 Gateway | 严格字段闭集、联合 DTO、每个 operation 的成功/业务拒绝/合同无效 outcome；请求编码失败 0 次 send；NOT_SENT/UNKNOWN/响应阶段 failure；取消传播；幂等关闭与关闭失败传播 |
| 获批 outbound 垂直能力 | 以 WMS 批准的版本化 fixture/schema 验证每项 query、来源绑定和业务确认的固定 method/path/request/result、权威元数据真实来源、合同边界值及数量/字节预算；单次有界结果和 0/1 次 send；来源绑定覆盖精确任务项集合、SixInOne、`PkgID` 唯一以及部分/缺项/重复整批拒绝，不测试 LIFO 或任务状态 |
| WMS inbound 规范化 | 以 WMS 批准的版本化 fixture/schema 验证 inbound body 字段闭集、联合类型、规范化结果和无业务副作用；normalizer 不接收认证 header、principal、认证策略、origin 或 Transport |
| 架构边界门禁 | 零数据库/旧 WMS/httpx import；Phase 4 只依赖公开合同；旧生产消费者与 Composition Root 在 Phase 5 前不引用或接线；公开 surface 无 generic `call` 或 registry |

以上行为统一落在 `tests/contracts/wms_adapter/`；WMS 批准的机器可读验收输入与测试同包版本化，仅架构边界门禁落在
`tests/architecture/`。测试文件按获批 operation 和现有测试规模生成最小清单，不预设一行为一文件，也不为未获批能力
创建占位测试。

真实 WMS、数据库、Celery、Phase 4 可靠对象和 Phase 8 出库场景均不属于本阶段测试。Phase 2 回归只证明基础传输未被破坏，
不计入 WMS ACL 行为覆盖率。

## 8. What already exists

- `src/core/outbound_http/` 已提供单次发送、长期 Client、有界响应、传输事实和显式关闭；Phase 3 直接消费，不复制实现。
- `src/core/authority_metadata.py` 是现有权威元数据 primitive，但只有在 Task 1 证明其字段可由目标合同真实满足后才复用；
  它不是旧 wire 的兼容要求。
- 当前 `src/app/wms_integration/` 的 Provider/Registry/evidence/breaker/生命周期只用于 Phase 5 删除闭包分析，不复用源码、
  类型、fixture 或测试作为新 ACL 模板。

## 9. NOT in scope

- Phase 2 未支持 HTTP method 的实际代码扩展：只有 Task 1 批准出超集 method 时，才以独立基础层变更执行。
- Phase 4 的 WMS API ingress 认证（含获批的局域网 `NONE`）、`WmsConfirmation`、`InboundEvidence`、Transport 与可靠
  生命周期：必须在 Phase 3 端口稳定后实现。
- Phase 5 的生产 Composition Root 切换和旧 owner 删除：Phase 3 只暗构建，不建立双轨或 feature flag。
- Phase 8 的自动出库状态、LIFO、双面顺序、NG、补料与恢复：由插件独立实现和测试。
- 真实 WMS 联调、MOCK 环境、观测看板和 Runbook：等待获批 wire 与后续阶段产生真实信号。
- `docs/hardware/` 厂商原始资料：只读保留，不改写、不归档。

## 10. 当前阻断

Task 1 的跨能力门禁仍有以下阻断，因此当前不得启动代码或测试：

- 库存 query/reserve/release/transfer 的目标阶段、具体消费者、调用时机和原子边界尚未全部确认。
- 多项 WMS 权威事实的共同 snapshot/version 或 fail-closed 一致性合同尚未冻结。
- 列表/批量结果的最大项数、wire/decoded 字节预算和超限语义尚未冻结。
- Inbound callback 的调用方认证合同或局域网 `NONE` 适用条件尚未获 WMS 批准。

Task 1 关闭后，下列未决事项只阻断各自能力及 Phase 3 最终退出，不阻断其他已获批能力进入 TDD：

- `release_reservation` 的 method 尚未获 WMS 确认。
- `request_picking_source_bindings` 业务语义已冻结，但完整 wire 尚未获 WMS 批准。
- NG、逐项位置完成、PickingTask 完成、原子替代来源/无完整替代方案、人工工作位和各 inbound 命令缺少获批 wire。

不得以厂商初稿样例、旧实现字段、实现方自行编写的 MockTransport 或历史测试结果代替 WMS 合同批准。

## 11. 退出标准

- Phase 3 只有目标态消费者驱动的 WMS 业务 ACL，无固定 operation 数量目标；每个消费者可定位到阶段、组件和调用时机。
- WMS 转发 RCS 的全部能力和可靠 Transport 生命周期只存在 Phase 4 范围。
- Adapter 无状态，零数据库、零 Migration、零 evidence/breaker owner。
- 每项能力都有唯一消费者、固定获批 wire、封闭 DTO 和业务拒绝码。
- 全部获批 HTTP method 均由 Phase 2 支持；Transport 创建、唯一持有者和关闭路径明确。
- Phase 2 三种 delivery state、响应阶段 failure、业务拒绝与合同无效保持可区分，不丢失传输事实。
- Inbound callback 认证由 API ingress 独立拥有，未被 outbound 配置或 WMS ACL 混入。
- Inbound normalizer 独立于 outbound origin、Gateway、Transport 和关闭生命周期。
- 来源绑定的 wire、可靠状态和插件业务校验各有唯一 owner，测试互不替代。
- WMS 权威查询的 version/time/provenance 字段有真实来源，不伪造外部版本或时间。
- 多事实消费者有获批的一致性合同；列表和批量结果有不超过 Phase 2 硬上限的数量/字节预算与超限语义。
- 每项实现均由 WMS 批准的版本化机器可读 fixture/schema 提供独立 wire 真源；MockTransport 只驱动测试。
- 所有保留能力均已独立获批并实施；未获批或无可定位消费者的候选能力已经删除，不保留占位。
- SRS 保持用户需求真源，不含阶段实现参数、operation 编号或“已冻结”自证语句。
- 项目内已过期的 33 项蓝图和旧 WMS 辅助域 ADR 已移至项目外归档。

## 12. 实施顺序与并行策略

本阶段顺序实施，无独立 worktree 并行机会：Task 1 冻结跨能力门禁；首个获批 outbound 能力通过 Task 2 建立最小
Gateway/outcome 骨架；其余获批 outbound 与 inbound operation 分别在 Task 3–5 内逐项 TDD。虽然能力可以独立批准，
但共同修改同一 `wms_adapter` 公开 surface 与 fixture 目录，当前规模下串行变更更简单；随后 Task 6/7 收口公开导出和门禁。

**VERDICT：Phase 3 当前尚不可实施；单项批准模板与开发路径已收敛，只允许继续关闭 Task 1 的四项跨能力外部门禁。**

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | ---: | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 本轮未要求 |
| Codex Review | `/codex review` | Independent 2nd opinion | 2 | CLEAR AFTER REPAIR | 本轮 10 项 findings 已全部裁决并回写 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | ISSUES OPEN (EXTERNAL) | 计划内结构问题已修复；4 项跨能力外部批准未关闭 |
| Serena Review | 仓库真实符号与引用扫描 | Code-shape evidence | 1 | CLEAR | Phase 2 显式 Transport 可复用；旧 registry/provider 包不作新实现模板 |
| Sequential Review | 假设、反证与门禁复核 | Architecture consistency | 1 | BLOCKED (EXTERNAL) | 方案满足最小设计；4 项跨能力外部决定仍阻断实施 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 非 UI 规划 |
| DX Review | 架构复评内置检查 | Developer experience gaps | 1 | CLEAR | 新增 API 采用批准模板、显式纵切片和固定六步清单，不建设生成器或 registry |

- **CODEX:** 补充并促成 Phase 4 暗构建依赖、逐能力批准、一致性/响应预算、独立 normalizer、独立 wire fixture 和单一任务体系；
  “响应阶段 failure 会直接全局停摆”与“没有当前生产消费者即违反 YAGNI”的严重度被降级为措辞澄清。
- **VERDICT:** ENG + CODEX + SERENA + SEQUENTIAL REVIEW COMPLETED — 计划结构已收敛，但 Phase 3 仍为
  `BLOCKED_AT_TASK_1`，尚不可实施。

**UNRESOLVED DECISIONS:**

- 库存 query/reserve/release/transfer 的目标消费者、调用时机与原子边界。
- 多项 WMS 权威事实的共同 snapshot/version 或获批 fail-closed 一致性合同。
- 列表/批量结果的最大项数、wire/decoded 字节预算与超限语义。
- Inbound callback 的认证合同或局域网 `NONE` 适用条件。
- 各保留 operation 的完整 wire、幂等语义及 WMS 批准的版本化机器可读 fixture/schema。
