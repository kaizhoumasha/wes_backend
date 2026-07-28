# 粗分与分拣入库运行时流程

<!-- ownership: end-to-end-device-protocol-examples -->

扫码决策的字段归属与状态机真源见
[粗分扫码决策合同](./rough_sorter_scan_decision_contract.md)；本文只描述跨系统运行因果。

## 1. 粗分机

粗分机依次执行扫码测量、GRN/绑定校验、源机械臂上料、输送、格位预约、出料机械臂投格、本地物理事实和 WMS
同步。本地物理事实与 WMS 同步状态分离：WMS 同步失败进入 reconciliation，但不能抹掉已发生的本地事实。

## 2. 分拣机

南向投料 join gate 同时要求：

- 授权料箱已解析；
- 目标料箱在工作位；
- 目标格位可预约；
- 格位预约已成功；
- 等待截止时间已声明。

WES 不读取或推断扫码平台是否空闲，也不计算源机械臂预取容量。唯一冻结的跨臂因果是：收到南向 PICK ACK 后，
触发下一次北向取料。扫码平台空闲、南北臂互锁和防呆由机器人/PLC 保证。

## 3. WMS 入站

普通 WMS 业务事实只允许：

- `WMS_GRN_RECEIVED`
- `WMS_PALLET_ARRIVED`
- `WMS_INVENTORY_UPDATED`
- `WMS_PDA_OPERATION_RECORDED`

E08–E14 的异步执行只接受 `WMS_EFFECT_STATUS_HINT`。hint 只唤醒统一 status query；任务终态由 typed status result
收敛，不存在平行 terminal callback 路径。

## 4. 状态与恢复

RuntimeInbox 是唯一入站 evidence/trace inbox。重复同 hash 幂等 ACK，同键异 hash 冲突。Callback 层不得直接
修改 Session、资源投影或库存结论。status hint 丢失时，周期 scanner 仍需通过同一 claim 路径取回结果。

## 5. 验收

- join gate 成功与失败路径都有测试；
- 南向 PICK ACK 是下一次北向取料的唯一 WES 触发条件；
- 本地物理完成后 WMS 同步失败保留本地事实并进入 reconciliation；
- 入站允许集之外的 WMS/RCS callback fail closed。
