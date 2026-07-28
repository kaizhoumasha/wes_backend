# WMS 全工厂 Operation 顶层蓝图

## 目标与边界

`wms_integration` 是 WES 内部 WMS Gateway。它拥有 typed Port、静态 Definition、Provider transport/evidence 和
同步/异步结果归约；不负责 Workline 业务编排、工厂字段映射、RCS 调度或设备防呆。

单个 WES 部署只有一个 active WMS Provider。新工厂按 35 项合同一次性启用；中间开发阶段不可作为生产接入形态。

## 唯一数据流

```text
Workline Plugin / System Capability
        → WMS Gateway typed Port
        → Provider binding / transport / evidence
        → WMS
```

QUERY 由调用进程直接执行。EFFECT 先写 RuntimeIntent/Outbox：

- E01–E07/E15 在 `wms-data` 收敛同步 typed result；
- E08–E14 在 `wms-fulfillment` 通过 ACK/status 收敛；
- E16 在 `wms-fulfillment` 同步返回取消裁决。

## 业务冻结

- GRN 就是一条 PO 行到货记录；料盘通过 Q09 查询。
- Q19 是无副作用粗分准入，首次有效结论先落本地 admission fact。
- E05 只做库存账务转移，不隐式调度搬运。
- E08 同工作位需求合并。
- E11 位于粗分移出固定交换位与 STATION A/B 之间，WMS 选择空箱和目标储位。
- E12 是冻结成员批次；E13 是有界 FIFO 候选窗口和 ACK 有序接纳前缀。
- 已发生本地物理事实永久保留；外部同步失败以 Hold、重试和对账处理。

## 南向扫码流水

南向取料 ACK 后立即释放下一北向取料；南向取料 result 后执行扫码；SCAN result 由 WES 做 typed 决策并下发
投放；PUT result 提交最终位置事实。厂商命令类型只存在版本化 plugin binding，业务和 manifest 不保存第二份映射。

## 迁移与验收

旧 transport 生产者必须按 `provider_manifest.LEGACY_TRANSPORT_MIGRATION_MANIFEST` 迁移到 E08–E14。迁移是替换，
不提供 facade、alias、双写或 fallback。

上线前必须证明：35 项无缺失/重复、业务场景全覆盖、Q19 无副作用、9/7 完成模式精确、Mock fixture 完整、旧
transport identity 不进入目标 Provider manifest。
