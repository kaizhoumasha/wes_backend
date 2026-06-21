# Workline 插件 Manifest 合同指南

本指南说明插件作者在 `manifest.yaml` 中声明物料单元根域合同时需要维护的字段。

## 会话主体

`session_subject` 必填，用于声明插件会话承载的业务主体。旧 manifest 为兼容可加载；新插件和模板必须声明该字段。第一阶段只支持卷盘物料单元：

- `type`: `MATERIAL_UNIT`
- `physical_form`: `REEL`
- `identity_sources`: 至少声明一个身份来源，建议包含 `PkgID` 和 `material_identity_key`

## 状态机

`state_machines` 必填。旧 manifest 为兼容可加载；新插件和模板必须声明该字段。第一阶段只支持一个状态 owner：

- `state_owner.model`: `MaterialUnit`
- `state_owner.field`: `status`

状态机 `subject` 必须与 `session_subject` 保持一致，`category/type` 使用 `MATERIAL_UNIT`，`physical_form` 使用 `REEL`。

`MaterialUnit.status` 的 transition 需要显式声明 `NG` 和 `RECONCILING` 出口。当前 loader 对缺失出口先记录 warning，不阻断加载；插件作者仍应按合同补全，避免运行期状态归属不清。

标准 transition：

- `IN_TRANSIT -> STORED, COMPLETED, NG, RECONCILING`
- `STORED -> IN_TRANSIT, NG, RECONCILING`
- `RECONCILING -> IN_TRANSIT, STORED, COMPLETED, NG`
- `NG -> []`
- `COMPLETED -> []`

## 管线队列

`pipeline_queues` 用于声明插件内部关键队列或工作位。旧 manifest 为兼容可缺省；新插件和模板必须声明该字段，确无队列时声明空数组或在插件文档中说明无队列：

- `code`: 队列编码，插件内唯一
- `role`: 队列角色，例如 `BUFFER`、`SCAN`、`WORKSTATION`
- `capacity`: 正整数或 `MANY`
- `order_policy`: 可省略，默认 `FIFO`；允许值为 `FIFO`、`LIFO`、`PRIORITY`
