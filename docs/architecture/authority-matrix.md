---
status: Current
created_at: 2026-06-26
updated_at: 2026-08-03
parent: docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
spec: docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
related: docs/contracts/wms-northbound-interaction-contract.md, docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
note: |
  本文最初由 Phase 1 CEO-006 交付，现已按 WES 最小执行架构收敛设计更新。
  历史 Runtime/Intent/Effect 名称不再构成当前权威边界。
---

# Authority Matrix（事实权威来源矩阵）

> 父设计：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` §4
> WMS wire：`docs/contracts/wms-northbound-interaction-contract.md`
> ADR：`docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`

## 1. 编写目的

WES 不是所有外部事实的唯一权威。**按事实类型拆分权威来源**——WES 只聚合 evidence 和冲突状态，不复制外部主数据、不替代外部系统做规划决策。

本文档锁定 11 类事实的权威来源、WES 角色、WES 写入边界，并提供案例和反例，使实现者在编码时能直接判断"这个数据该读哪里、该写哪里、谁是权威"。

## 2. 事实类型权威矩阵（11 类）

| # | 事实类型 | 权威系统 | WES 角色 | WES 写入 | 权威元数据规则 |
| --- | --- | --- | --- | --- | --- |
| 1 | 库存数量、批次、有效期 | WMS | 引用 + 单次执行快照 | 只读 evidence；禁止跨请求缓存 | `authority=WMS, source_version=必填` |
| 2 | 入库单 / 出库单 / 批次单 / 波次 / 业务任务 | WMS | 外部引用 + 执行上下文 | 不复制为 WES 单据主档 | `authority=WMS, source_version=必填` |
| 3 | 设备到位信号（光电、接近开关、扫码） | ECS/device | 接收 + 转换 | evidence + transition events | `authority=ECS` |
| 4 | 设备业务命令结果（机械臂取放、滚筒线动作） | ECS/device runtime | 接收 + 诊断 | RESULT + 设备诊断状态 | `authority=ECS` |
| 5 | 硬件防呆、安全回路、急停、复位、物理坐标/关节控制 | ECS/现场安全系统 | **只感知，不控制** | 只写 event/evidence/hold，不下发安全控制或坐标级指令 | `authority=ECS, scope=SAFETY` |
| 6 | 设备事件/任务结果回调 | ECS/device callback | normalize + dispatch | `InboundEvidence` + device projection | `authority=ECS` |
| 7 | AGV/CTU 履约状态与位置 evidence | WMS E08–E14 status query | `TransportTask` 校验 typed ACK/status/terminal result；E12/E13 使用批次级结果 | owner 校验权威结果后更新活动投影，不复制实时位置或 SDK 状态 | `authority=WMS` |
| 8 | 货架/料箱/库位主数据 | WMS | 引用 + 作业期投影 | 不复制主数据，只维护 active projection | `authority=WMS, source_version=必填` |
| 9 | WMS 普通事件与状态查询提示 | WMS | 四类普通事件 normalize + dispatch；`WMS_EFFECT_STATUS_HINT` 只唤醒对应 `TransportTask` | `InboundEvidence` + correlation key；不作为履约终态 | `authority=WMS` |
| 10 | 冲突与人工裁决 | WES | 冲突证据 + 人工裁决权威 | 具体执行对象上的 conflict evidence；不建立通用 Reconciliation 生命周期 | `authority=WES, scope=WORKLINE_LOCAL` |
| 11 | WES 作业期料盘/物料执行对象 | WES | 根执行对象拥有者 | `MaterialExecution` 身份与作业期状态；位置只由证据驱动投影更新 | `authority=WES` |

## 3. 权威边界判定规则

### 3.1 数据读取：该读哪里？

| 数据需求 | 读哪里 | 不该读哪里 |
| --- | --- | --- |
| 库存可用量 | `WmsInventoryCapabilities` 对应显式方法（每次执行读取一次） | WES active projection 或跨请求缓存冒充全局库存 |
| GRN/入库单详情 | `WmsDocumentCapabilities` 对应显式方法 | 复制为 WES 单据主档 |
| 物料主数据 | `WmsMasterDataCapabilities` 对应显式方法 | WES 自建物料主数据 |
| 货架/料箱/库位状态 | `WmsMasterDataCapabilities`（主数据）+ WES active projection（作业期占用） | 把作业期投影当主数据写回 |
| 设备到位/状态 | `device` 域 callback（ECS 推送） | WES 轮询 PLC 点位 |
| WES 料盘位置 | `PositionProjection`（事实 11） | 绕过证据直接写位置摘要 |
| 对账 drift | `WmsReconciliationCapabilities` 对应显式方法（只读拉取 WMS 权威） | 把对账查询升级为副作用 |

### 3.2 数据写入：该写哪里？

| 写入需求 | 写哪里 | 不该写哪里 |
| --- | --- | --- |
| 库存预留/释放/转移确认 | `WmsConfirmation` → `WmsConfirmationSender` | 直接改 WES active projection 假装库存已变 |
| 履约请求（搬运/补给/换面/满箱交换） | `TransportTask` → Transport Port → 无状态 WMS 转发 Client | WES 内部域直连 RCS/AGV/CTU SDK |
| 设备命令下发 | `DeviceCommandPort`（只面向 ECS API） | 下发 PLC/坐标/关节/安全回路指令 |
| WMS callback 入站 | callback normalizer → `InboundEvidence`（只 ACK，不直接改执行对象） | callback API 直接改执行对象/投影 |
| 位置投影更新 | 终态 evidence → projection writer | API 层直接改投影表 |
| 冲突登记 | 对应执行对象写 conflict evidence，等待人工裁决 | 通用管理器直接写跨域 owner 状态 |

### 3.3 权威不变量

WES 内部域（workline / runtime / handling / resource / material / device）**不能**直接依赖：

- PLC/RCS/AGV-CTU SDK
- WMS HTTP client

设备事实经 `device` 域，搬运事实经 `wms_integration` 的 Transport Port。RCS/AGV/CTU 直连不属于当前目标，
不预留 Adapter、SDK 依赖或配置骨架；真实需求出现时必须另立 SPEC。

## 4. 通用示例与反例

### 4.1 通用对象推进示例（事实 1/3/4/11 协同）

| 步骤 | 事实类型 | 权威 | WES 动作 |
| --- | --- | --- | --- |
| 设备输入到达 | 事实 3（设备到位或扫描信号） | ECS/device | Adapter 转换厂商 DTO，核心持久化 `InboundEvidence` |
| 查询外部权威 | 事实 1/2/8 | WMS | 通过明确的 `WmsCapabilities` 方法读取带 `source_version` 的单次执行快照 |
| 建立作业期对象 | 事实 11（WES 作业期执行对象） | WES | 对应插件返回封闭 Decision，核心建立具体执行对象 |
| 创建设备动作 | 事实 11（WES 作业期目标） | WES | 持久化逻辑 `DeviceCommand`；成功终态前不更新 `PositionProjection` |
| 设备完成动作 | 事实 4（设备命令结果） | ECS/device runtime | Adapter 输出规范终态，核心持久化 evidence 后更新 `PositionProjection` |
| 提交外部义务 | 事实 2（业务任务） | WMS | 创建 `TransportTask` 或 `WmsConfirmation`，由对应 sender/port 提交类型化请求并保存结果证据 |

### 4.2 反例 1：影子 WMS（事实 1 违规）

❌ **错误**：WES active projection 或跨请求缓存保存了库存数据，业务逻辑读取旧值做分配决策，WMS 库存实际已变。

✅ **正确**：

- 每次 execution 只查询一次 WMS，并让 policy 与 evidence 共用同一 typed authority snapshot
- evidence 必须带 `authority=WMS, source, evidence_at, source_version`；写入失败时查询 fail closed
- WMS 确认失败不允许抹掉已经由 terminal evidence 建立的本地物理位置事实

### 4.3 反例 2：WES 直连 RCS（事实 7 违规）

❌ **错误**：WES 执行域直接调用 RCS SDK 查询 AGV 位置，跳过 `TransportTask` 与 Transport Port。

✅ **正确**：

- AGV/CTU 履约状态只能从 E08–E14 typed ACK/status/terminal result 获取；E12/E13 只消费批次级权威结果
- WES 只提交具名履约意图（例如 `wms.fulfillment.request_load_unit_transport@v1`），不调度车辆
- 当前目标禁止直连 RCS/AGV/CTU；真实需求出现时另立 SPEC，不预留代码骨架

### 4.4 反例 3：API 层直接改投影（事实 10/11 违规）

❌ **错误**：API 层在 handoff 完成时直接 UPDATE `material_units.location_summary` 和 `BinPlacement.status`。

✅ **正确**：

- 交接必须以 External callback 或可靠执行记录的 terminal evidence 推进
- 位置投影只能由 terminal evidence → projection writer 更新
- API 层只读投影，不写投影

### 4.5 反例 4：callback 直接改执行对象（事实 6/9 违规）

❌ **错误**：`/api/v1/callback/event` 处理器在 ACK 后直接修改执行对象状态。

✅ **正确**：

- callback API 只做鉴权、schema normalize、幂等校验、ACK、写 `InboundEvidence`
- 执行对象状态转移由 worker 领取 `InboundEvidence` 后推进
- callback 不直接改执行对象、projection 或 device runtime

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
