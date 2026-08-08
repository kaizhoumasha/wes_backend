---
status: Current
created_at: 2026-06-26
updated_at: 2026-08-07
parent: docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
spec: docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
related: docs/contracts/wms-northbound-interaction-contract.md, docs/contracts/transport-fulfillment-contract.md, docs/integration/third_party_integration_whitepaper.md, docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
note: |
  本文最初由 Phase 1 CEO-006 交付，现已按 WES 最小执行架构收敛设计更新。
  历史 Runtime/Intent/Effect 名称不再构成当前权威边界。
---

# Authority Matrix（事实权威来源矩阵）

> 父设计：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` §4
> WMS Client 访问标准：`docs/contracts/wms-northbound-interaction-contract.md`
> 设备 wire：`docs/integration/third_party_integration_whitepaper.md`
> ADR：`docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`

## 1. 编写目的

WES 不是所有外部事实的唯一权威。**按事实类型拆分权威来源**——业务资格、来源、目标、优先级、业务异常分类、
替代来源、取消和业务终态由 WMS 给出封闭结果；WES 聚合 evidence、维护作业期投影，并决定安全的物理执行、NG 隔离、
等待、暂停和对账动作。WES 不复制外部主数据，也不把本地规则、投影或人工操作升级为业务裁决；WMS 也不下发机械动作
或改写 WES 执行对象状态。

本文档锁定 11 类事实的权威来源、WES 角色、WES 写入边界，并提供案例和反例，使实现者在编码时能直接判断"这个数据该读哪里、该写哪里、谁是权威"。

## 2. 事实类型权威矩阵（11 类）

| # | 事实类型 | 权威系统 | WES 角色 | WES 写入 | 权威元数据规则 |
| --- | --- | --- | --- | --- | --- |
| 1 | 库存数量、批次、有效期 | WMS | 引用 + 单次执行快照 | 只读 evidence；禁止跨请求缓存 | `authority=WMS, source_version=必填` |
| 2 | 入库单 / 出库单 / 批次单 / 波次 / 业务任务 | WMS | 外部引用 + 执行上下文 | 不复制为 WES 单据主档 | `authority=WMS, source_version=必填` |
| 3 | 设备到位信号（光电、接近开关、扫码） | ECS/device | 统一接口校验 + 接收 | evidence + transition events | `authority=ECS` |
| 4 | 设备业务命令结果（机械臂取放、滚筒线动作） | ECS/device runtime | 接收 + 诊断 | RESULT + 设备诊断状态 | `authority=ECS` |
| 5 | 硬件防呆、安全回路、急停、复位、物理坐标/关节控制 | ECS/现场安全系统 | **只感知，不控制** | 只写 event/evidence/hold，不下发安全控制或坐标级指令 | `authority=ECS, scope=SAFETY` |
| 6 | 设备事件/任务结果回调 | ECS/device callback | 公共包络与设备附录校验 + dispatch | `InboundEvidence` + device projection | `authority=ECS` |
| 7 | AGV/CTU 履约状态与位置 evidence | RCS；当前网络入口由 WMS 转发 | Phase 4 `TransportTask` 提交需求，并通过 Transport evidence 应用端口校验标准化成员位置事实和可靠异步终态 | owner 只按 `SOURCE_PICKED`、`TARGET_PLACED` 及终态更新活动投影，不复制 RCS 实时轨迹或 SDK 状态 | `authority=RCS, transport_via=WMS` |
| 8 | 货架/料箱/库位主数据 | WMS | 引用 + 作业期投影 | 不复制主数据，只维护 active projection | `authority=WMS, source_version=必填` |
| 9 | WMS 业务决策、业务命令与普通事件 | WMS | 决策/命令 DTO normalize 后交给具体执行消费者；普通事件按批准合同 dispatch | 保存 WMS 业务资格、异常分类、来源/目标和终态结果；不得在本地重算、替换或扩大业务结果 | `authority=WMS` |
| 10 | 执行冲突、NG 隔离与人工清线 | WES | 冲突证据 + 作业期执行处置 | 暂停、隔离、物理 NG 路由、清线 evidence；涉及业务资格、来源、目标、库存或业务终态的处置仍须 WMS 决定 | `authority=WES, scope=EXECUTION_LOCAL` |
| 11 | WES 作业期料盘/物料执行对象 | WES | 根执行对象拥有者 | `MaterialExecution` 身份与作业期状态；位置只由证据驱动投影更新 | `authority=WES` |

## 3. 权威边界判定规则

### 3.1 数据读取：该读哪里？

| 数据需求 | 读哪里 | 不该读哪里 |
| --- | --- | --- |
| 库存可用量 | 对应业务模块通过 Phase 3 `WmsClient` 调用 WMS（每次执行读取一次） | WES active projection 或跨请求缓存冒充全局库存 |
| GRN/入库单详情 | 对应业务模块通过 `WmsClient` 调用 WMS | 复制为 WES 单据主档 |
| 物料主数据 | 对应业务模块通过 `WmsClient` 调用 WMS | WES 自建物料主数据 |
| 货架/料箱/库位状态 | WMS 业务 API（主数据）+ WES active projection（作业期占用） | 把作业期投影当主数据写回 |
| 设备到位/状态 | 设备统一接口 callback 或状态查询 | WES 轮询 PLC 点位 |
| WES 料盘位置 | `PositionProjection`（事实 11） | 绕过证据直接写位置摘要 |
| 对账 drift | 仅在出现真实消费者并定义对应业务 API 后查询 | 预建无消费者的通用 reconciliation 能力 |

需要多项 WMS 事实才能得出业务结论时，由 WMS 在 operation 内部完成组合并返回一个封闭业务结果，WES 不得通过多次查询
自行拼装业务决策。只有纯执行校验确实需要组合多项权威事实时，才允许使用 WMS 提供的共同 snapshot/version；若 WMS
不提供，先批准读取顺序、有效窗口和 fail-closed 条件。具体业务模块只返回 wire 可证明的结果与元数据，不伪造外部版本，
也不把多次读取包装成 WMS 原子决策；Phase 3 Client 不解释这些业务事实。

### 3.2 数据写入：该写哪里？

| 写入需求 | 写哪里 | 不该写哪里 |
| --- | --- | --- |
| 库存预留/释放/转移确认 | 具体业务义务 owner → 具体 WMS 业务模块 → `WmsClient` | Client 持久化生命周期，或直接改 active projection 假装库存已变 |
| 履约请求（搬运/补给/换面/满箱交换） | Phase 4 `TransportTask` → `Transport Port` → WMS 转发 RCS Adapter | 把业务语义放入 Phase 3 Client，或由 WES 内部域直连 RCS/AGV/CTU SDK |
| 设备命令下发 | `DeviceCommandPort`（只面向 ECS API） | 下发 PLC/坐标/关节/安全回路指令 |
| WMS 业务决策/命令入站 | 对应业务 ingress → 具体执行消费者 | 访问层直接执行编排，或消费者重算/改写 WMS 业务结果 |
| 位置投影更新 | 终态 evidence → projection writer | API 层直接改投影表 |
| 冲突登记 | 对应执行对象写 conflict evidence，等待人工裁决 | 通用管理器直接写跨域 owner 状态 |

### 3.3 权威不变量

WES 内部域（workline / runtime / handling / resource / material / device）**不能**直接依赖：

- PLC/RCS/AGV-CTU SDK
- WMS HTTP client

设备事实经 `device` 域；搬运事实经 Phase 4 `Transport Port` 和 WMS 转发 RCS Adapter。Phase 3 `WmsClient` 只提供
HTTP/JSON 访问，不拥有 Transport 业务语义。RCS/AGV/CTU 直连不属于当前目标，不预留 SDK 依赖或配置骨架。

## 4. 通用示例与反例

### 4.1 通用对象推进示例（事实 1/3/4/11 协同）

| 步骤 | 事实类型 | 权威 | WES 动作 |
| --- | --- | --- | --- |
| 设备输入到达 | 事实 3（设备到位或扫描信号） | ECS/device | ECS 按统一接口上报，核心持久化 `InboundEvidence` |
| 取得业务结果 | 事实 1/2/8/9 | WMS | 对应业务模块复用 `WmsClient`，取得 WMS 封闭业务结果 |
| 建立作业期对象 | 事实 11（WES 作业期执行对象） | WES | 插件校验结果关联并映射为封闭执行 Decision，核心建立具体执行对象 |
| 创建设备动作 | 事实 11（WES 作业期目标） | WES | 持久化逻辑 `DeviceCommand`；成功终态前不更新 `PositionProjection` |
| 设备完成动作 | 事实 4（设备命令结果） | ECS/device runtime | ECS 按统一接口上报终态，核心持久化 evidence 后更新 `PositionProjection` |
| 提交外部义务 | 事实 2/7 | WMS 或 RCS | 业务确认走对应业务模块并复用 `WmsClient`；运输意图走 Phase 4 `TransportTask`/`Transport Port`，由 Transport owner 保存成员位置/终态证据 |

### 4.2 反例 1：影子 WMS（事实 1 违规）

❌ **错误**：WES active projection 或跨请求缓存保存了库存数据，业务逻辑读取旧值做分配决策，WMS 库存实际已变。

✅ **正确**：

- 每次 execution 只查询一次 WMS，并让 policy 与 evidence 共用同一 typed authority snapshot
- evidence 必须带 `authority=WMS, source, evidence_at, source_version`；写入失败时查询 fail closed
- WMS 确认失败不允许抹掉已经由 terminal evidence 建立的本地物理位置事实

### 4.3 反例 2：WES 直连 RCS（事实 7 违规）

❌ **错误**：WES 执行域直接调用 RCS SDK 查询 AGV 位置，跳过 `TransportTask` 与 Transport Port。

✅ **正确**：

- AGV/CTU 履约状态只能从 Phase 4 Transport 合同定义的终态结果获取；ACK、已受理或已下发不表示完成
- WES 只通过 `Transport Port` 提交具名履约意图，不调度车辆；Phase 3 Client 只作为 HTTP 访问基础
- 当前目标禁止直连 RCS/AGV/CTU；真实需求出现时另立 SPEC，不预留代码骨架

### 4.4 反例 3：API 层直接改投影（事实 10/11 违规）

❌ **错误**：API 层在 handoff 完成时直接 UPDATE `material_units.location_summary` 和 `BinPlacement.status`。

✅ **正确**：

- 交接必须以 External callback 或可靠执行记录的 terminal evidence 推进
- 位置投影只能由 terminal evidence → projection writer 更新
- API 层只读投影，不写投影

### 4.5 反例 4：inbound normalizer 直接改执行对象（事实 6/9 违规）

❌ **错误**：WMS inbound normalizer 在解析业务命令后直接修改执行对象状态。

✅ **正确**：

- 对应业务 ingress 只做 schema normalize 和封闭拒绝，随后把 typed command 交给明确的业务消费者
- 是否持久化、幂等和推进由对应执行 owner 定义，不由访问层预建通用生命周期；执行 owner 不得改变 WMS 业务语义

### 4.6 反例 5：WES 根据 WMS 原始事实重算业务结果（事实 9 违规）

❌ **错误**：插件查询物料、GRN、库存和本地投影后，自行选择来源、目标格、业务路线、优先级或业务异常分类。

✅ **正确**：

- WMS 通过具体业务合同返回封闭业务结果及版本/关联元数据
- 对应业务模块翻译结果；插件只校验关联、时效和物理可执行性，并映射为设备等待、发送、暂停、NG 隔离或对账等执行 Decision
- WMS 结果缺失、过期、矛盾或物理不可执行时 fail closed；WES 不选择另一个业务方案
- ingress 不直接改执行对象、projection 或 device runtime

## 5. 与权威元数据合同的关系

外部权威查询响应必须带 `scope/authority/source/evidence_at`。本矩阵的“权威元数据规则”列给出每类事实在
响应中的 `authority` 字段值：

- 外部权威事实（1/2/8/9）→ `authority=WMS`，且必须带 `source_version`（`ExternalAuthorityMetadata`）
- 设备事实（3/4/5/6）→ `authority=ECS`
- WES 自有事实（10/11）→ `authority=WES`，冲突 evidence 的 `scope=WORKLINE_LOCAL`

详见 `src/core/authority_metadata.py`、
`tests/architecture/test_authority_metadata_boundary_guardrail.py` 和
`tests/architecture/test_authority_response_schema_inventory.py`。

## 6. 验收（CEO-006）

1. ✅ 11 类事实类型全部列出权威来源（§2）
2. ✅ 权威边界判定规则覆盖读取/写入/不变量（§3）
3. ✅ 案例与反例覆盖典型违规模式（影子 WMS / 直连 RCS / API 改投影 / callback 改执行对象）（§4）
4. ✅ 与权威元数据合同的关系明确（§5）
5. ✅ 与顶层 SPEC、WMS 北向合同和 [`WES / WMS / RCS 资源边界 ADR`](adr/2026-05-13-wes-wms-rcs-resource-boundary.md) 一致
