---
status: Phase 1 CEO-006
created_at: 2026-06-26
parent: docs/architecture/workline-and-plugin-restructuring.md
spec: docs/superpowers/archive/specs/2026-06-26-workline-restructuring-phase-1-spec.md
related: docs/architecture/target-state-contract.md, docs/architecture/adr/workline-restructuring/0008-authority-matrix.md
note: |
  本文档是 Phase 1 CEO-006 交付物，从 Phase 0 target-state-contract.md §4
  独立成稿，扩展案例、反例和权威边界判定规则。对齐主计划 §3.4 + ADR 0008。
  Phase 0 target-state-contract.md §4 仍作为目标态合同的一部分引用本文档。
---

# Authority Matrix（事实权威来源矩阵）

> 父设计：主计划 §3.4 Authority Matrix
> 目标态合同：`target-state-contract.md` §4
> ADR：`docs/architecture/adr/workline-restructuring/0008-authority-matrix.md`

## 1. 编写目的

WES 不是所有外部事实的唯一权威。**按事实类型拆分权威来源**——WES 只聚合 evidence 和冲突状态，不复制外部主数据、不替代外部系统做规划决策。

本文档锁定 11 类事实的权威来源、WES 角色、WES 写入边界，并提供案例和反例，使实现者在编码时能直接判断"这个数据该读哪里、该写哪里、谁是权威"。

## 2. 事实类型权威矩阵（11 类）

| # | 事实类型 | 权威系统 | WES 角色 | WES 写入 | C3 authority |
| --- | --- | --- | --- | --- | --- |
| 1 | 库存数量、批次、有效期 | WMS | 引用 + 单次执行快照 | 只读 evidence；禁止跨请求缓存 | `authority=WMS, source_version=必填` |
| 2 | 入库单 / 出库单 / 批次单 / 波次 / 业务任务 | WMS | 外部引用 + 执行上下文 | 不复制为 WES 单据主档 | `authority=WMS, source_version=必填` |
| 3 | 设备到位信号（光电、接近开关、扫码） | ECS/device | 接收 + 转换 | evidence + transition events | `authority=ECS` |
| 4 | 设备业务命令结果（机械臂取放、滚筒线动作） | ECS/device runtime | 接收 + 诊断 | RESULT + 设备诊断状态 | `authority=ECS` |
| 5 | 硬件防呆、安全回路、急停、复位、物理坐标/关节控制 | ECS/现场安全系统 | **只感知，不控制** | 只写 event/evidence/hold，不下发安全控制或坐标级指令 | `authority=ECS, scope=SAFETY` |
| 6 | 设备事件/任务结果回调 | ECS/device callback | normalize + dispatch | typed evidence + RuntimeInbox + device projection | `authority=ECS` |
| 7 | AGV/CTU 履约状态与位置 evidence | WMS E08–E14 status query | 校验 typed ACK/status/terminal result；E12/E13 使用批次级结果 | owner 校验权威结果后更新 handling 投影，不复制实时位置或 SDK 状态 | `authority=WMS` |
| 8 | 货架/料箱/库位主数据 | WMS | 引用 + 作业期投影 | 不复制主数据，只维护 active projection | `authority=WMS, source_version=必填` |
| 9 | WMS 普通事件与状态查询提示 | WMS | 四类普通事件 normalize + dispatch；`WMS_EFFECT_STATUS_HINT` 只唤醒 E08–E14 查询 | typed evidence + correlation key；不作为履约终态 | `authority=WMS` |
| 10 | 冲突、对账、RECONCILING 决议 | WES ReconciliationManager | 冲突记录 + 决议权威 | RECONCILING evidence + `resolution_decision`；恢复动作由各 owner 按 evidence 执行 | `authority=WES, scope=RECONCILING` |
| 11 | WES 作业期料盘/物料根实体 | **WES material 域（WES 自有）** | 根实体拥有者 | material_units 身份 + 作业期业务状态；位置摘要只读投影只能由 `RuntimeLocationEvent` 更新 | `authority=WES` |

## 3. 权威边界判定规则

### 3.1 数据读取：该读哪里？

| 数据需求 | 读哪里 | 不该读哪里 |
| --- | --- | --- |
| 库存可用量 | operation-specific inventory QUERY Definition + `WmsQueryExecutionPort`（每次执行读取一次） | WES active projection 或跨请求缓存冒充全局库存 |
| GRN/入库单详情 | `wms.document.*` operation-specific QUERY Definition | 复制为 WES 单据主档 |
| 物料主数据 | operation-specific master-data QUERY Definition + `WmsQueryExecutionPort` | WES 自建物料主数据 |
| 货架/料箱/库位状态 | operation-specific master-data QUERY Definition + `WmsQueryExecutionPort`（主数据）+ WES active projection（作业期占用） | 把作业期投影当主数据写回 |
| 设备到位/状态 | `device` 域 callback（ECS 推送） | WES 轮询 PLC 点位 |
| WES 料盘位置 | `RuntimeLocationEvent` 投影（事实 11） | 直接写 material_units.location_summary |
| 对账 drift | operation-specific reconciliation QUERY Definition + `WmsQueryExecutionPort`（只读拉取 WMS 权威） | 把对账查询升级为副作用 |

### 3.2 数据写入：该写哪里？

| 写入需求 | 写哪里 | 不该写哪里 |
| --- | --- | --- |
| 库存预留/释放/转移确认 | operation-specific inventory EFFECT Definition + `WmsEffectPreparationPort`（经 RuntimeIntentLog） | 直接改 WES active projection 假装库存已变 |
| 履约请求（搬运/补给/换面/满箱交换） | operation-specific WMS fulfillment contract（经 RuntimeIntentLog + EffectPort） | WES 内部域直连 RCS/AGV/CTU SDK |
| 设备命令下发 | `DeviceCommandPort`（只面向 ECS API） | 下发 PLC/坐标/关节/安全回路指令 |
| WMS callback 入站 | `WmsEventPort` normalizer → RuntimeInbox（只 ACK，不直接改 session） | callback API 直接改 ExecutionSession/投影 |
| 位置投影更新 | `RuntimeLocationEvent` evidence → projection writer 重放 | API 层直接改投影表 |
| 冲突登记 | `ReconciliationManager`（只产 evidence + resolution_decision） | ReconciliationManager 直接写跨域 owner 状态 |

### 3.3 权威不变量（主计划 §3.4）

WES 内部域（workline / runtime / handling / resource / material / device）**不能**直接依赖：
- PLC/RCS/AGV-CTU SDK
- WMS HTTP client

设备事实经 `device` 域，搬运事实经 `wms_integration` 端口。RCS/AGV/CTU 直连仅作条件触发扩展（主计划 §10.5），生产前默认不触发、不预留代码骨架。

## 4. 案例与反例

### 4.1 案例：粗分机扫码入库（事实 1/3/11 协同）

| 步骤 | 事实类型 | 权威 | WES 动作 |
| --- | --- | --- | --- |
| 扫码到位 | 事实 3（设备到位信号） | ECS/device | `device` 域接收 callback → RuntimeInbox → evidence |
| WMS 校验物料 | 事实 1（库存）/ 事实 8（物料主数据） | WMS | `wms.master_data.get_material@v1` + `wms.inventory.query_inventory@v1`（带 source_version） |
| 建料盘实体 | 事实 11（WES 作业期根实体） | WES | material 域写 material_units（WES 自有） |
| 箱格分配 | 事实 10（WES 投影） | WES | resource 域写 BinCellOccupancy（作业期投影） |
| PKG 绑定通知 WMS | 事实 2（业务任务） | WMS | `wms.fulfillment.notify_pkg_binding@v1` 同步 typed EFFECT，提交响应直接返回 typed terminal result |

### 4.2 反例 1：影子 WMS（事实 1 违规）

❌ **错误**：WES active projection 或跨请求缓存保存了库存数据，业务逻辑读取旧值做分配决策，WMS 库存实际已变。

✅ **正确**：
- 每次 execution 只查询一次 WMS，并让 policy 与 evidence 共用同一 typed authority snapshot
- evidence 必须带 `authority=WMS, source, evidence_at, source_version`（C3）；写入失败时查询 fail closed
- WMS effect 失败不允许抹掉本地物理位置事实（主计划 §5.1 物理事实与 WMS 业务确认顺序）

### 4.3 反例 2：WES 直连 RCS（事实 7 违规）

❌ **错误**：WES runtime 域直接调用 RCS SDK 查询 AGV 位置，跳过 RuntimeIntentLog 与 operation-specific fulfillment contract。

✅ **正确**：
- AGV/CTU 履约状态只能从 E08–E14 typed ACK/status/terminal result 获取；E12/E13 只消费批次级权威结果
- WES 只提交具名履约意图（例如 `wms.fulfillment.request_load_unit_transport@v1`），不调度车辆
- 直连 RCS/AGV/CTU 需满足主计划 §10.5 触发条件 + 独立 SPEC，生产前默认不触发

### 4.4 反例 3：API 层直接改投影（事实 10/11 违规）

❌ **错误**：API 层在 handoff 完成时直接 UPDATE `material_units.location_summary` 和 `BinPlacement.status`。

✅ **正确**：
- 交接必须以 External callback 或 `RuntimeIntentLog` evidence 推进（BC-03）
- 位置投影只能由 `RuntimeLocationEvent` evidence → projection writer 重放更新
- API 层只读投影，不写投影（C2 / I7 不变量）

### 4.5 反例 4：callback 直接改 session（事实 6/9 违规）

❌ **错误**：`/api/v1/callback/event` 处理器在 ACK 后直接 UPDATE `execution_sessions.state = RUNNING`。

✅ **正确**：
- callback API 只做鉴权、schema normalize、幂等校验、ACK、写 `RuntimeInbox`
- session 状态转移由 runtime/orchestration worker 消费 RuntimeInbox 后推进
- callback 不直接改 session/projection/device runtime（主计划 §5.3）

## 5. 与 C3 不变量的关系

C3（主计划 §7.5）要求查询响应强制带 `scope/authority/source/evidence_at`。本矩阵的 "C3 authority" 列给出每类事实在响应中的 `authority` 字段值：

- 外部权威事实（1/2/8/9）→ `authority=WMS`，且必须带 `source_version`（`ExternalAuthorityMetadata`）
- 设备事实（3/4/5/6）→ `authority=ECS`
- WES 自有事实（10/11）→ `authority=WES`，`scope` 标明作用域（RECONCILING / WORKLINE_LOCAL / SESSION）

详见 Phase 1 CEO-005 `src/core/authority_metadata.py` + `tests/architecture/test_c3_response_schema_inventory.py`。

## 6. 验收（CEO-006）

1. ✅ 11 类事实类型全部列出权威来源（§2）
2. ✅ 权威边界判定规则覆盖读取/写入/不变量（§3）
3. ✅ 案例与反例覆盖典型违规模式（影子 WMS / 直连 RCS / API 改投影 / callback 改 session）（§4）
4. ✅ 与 C3 不变量的关系明确（§5）
5. ✅ 与 `target-state-contract.md` §4 + 主计划 §3.4 + ADR 0008 一致
