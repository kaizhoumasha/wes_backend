# ADR 0008: Authority Matrix（外部事实权威来源）

**状态**: Accepted
**日期**: 2026-06-23
**适用范围**: 所有外部事实的权威归属 + WES 内部域的 evidence 写入规则

## 背景

WES 不是所有外部事实的唯一权威。plan 早期把所有外部事实归 WMS，但实际现场：实时到位、扫码、PLC 传感器、RCS 任务状态往往比 WMS 更接近物理真相。如果 WES 用本地 projection 冒充 WMS 全局库存，构成"影子 WMS"风险。

## 决策

按事实类型拆分权威来源：

| 事实类型 | 权威系统 | WES 角色 | WES 写入 |
| --- | --- | --- | --- |
| 库存数量、批次、有效期 | WMS | 引用 + 作业期快照 | 只读 evidence + 短暂快照缓存 |
| 入库单 / 出库单 / 批次单 / 波次 / 业务任务 | WMS | 外部引用 + 执行上下文 | 不复制为 WES 单据主档 |
| 设备到位信号（光电、接近开关、扫码） | PLC / device | 接收 + 转换 | evidence + transition events |
| 设备命令结果（机械臂取放、滚筒线动作） | device runtime | 接收 + 诊断 | RESULT + 设备诊断状态 |
| AGV/CTU 实时位置与状态 | RCS / AGV-CTU SDK | 引用 fulfillment 回调 | 触发 handling 状态机，不复制为本地状态 |
| 货架/料箱/库位主数据 | WMS | 引用 + 作业期投影 | 不复制主数据，只维护 active projection |
| WMS 回调事件（WMS 主动推送） | WMS callback | normalize + dispatch | typed evidence + correlation key |
| 冲突、对账、RECONCILING 状态 | WES ReconciliationManager | 决定权威 | RECONCILING evidence + 恢复动作 |
| WES 作业期料盘/物料根实体 | WES material 域 | **WES 自有** | `material_units` 状态 + 位置 + current_session correlation |

**不变量**：

- WES 内部域（workline / runtime / handling / resource / material / device）**不能**直接依赖 PLC/RCS/AGV-CTU SDK 或 WMS HTTP client。
- 设备事实经 `device` 域、搬运事实经 `wms_integration` 端口或未来 `external/rcs` 端口。
- 所有查询响应强制带 `scope/authority/source/evidence_at` 字段；不允许本地 active projection 冒充 WMS 全局库存。

## 后果

- 外部事实的权威归属清晰，避免影子 WMS 风险。
- 未来直连 AGV/CTU 通过 `external/rcs` 端口扩展，不破坏现有结构。
- 查询响应 schema 强制带 scope/authority 字段，前端可识别数据来源。
- WES material 域是唯一自有根实体域，料盘/物料身份不受外部影响。

## 验收

- `docs/architecture/specs/workline-restructuring/00-overview.md` Authority Matrix 段落发布。
- `docs/architecture/authority-matrix.md` 单独文档发布（CEO-006）。
- 所有查询响应 schema 加 `scope/authority/source/evidence_at` 字段。
- `src/app/*/schemas/` 加 schema 校验。

## 引用

- 顶层设计：[`../../workline-and-plugin-restructuring.md`](../../workline-and-plugin-restructuring.md)
- 现有 ADR：[`../2026-05-13-wes-wms-rcs-resource-boundary.md`](../2026-05-13-wes-wms-rcs-resource-boundary.md)
