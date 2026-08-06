# WMS 全工厂 Operation 顶层蓝图

## 目标与边界

本文是 35 项 WMS wire operation 的业务视图。精确 wire 合同以
`docs/contracts/wms-northbound-interaction-contract.md` 为准；WES 所有权与执行架构以
`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` 为准。

目标 `wms_adapter` 只拥有类型化 DTO、显式窄端口、固定 method/path、错误映射、HTTP 调用证据和无状态 Adapter；
当前 WMS outbound 认证固定为 `NONE`，不提供认证 seam；
不负责 WorkLine 业务编排、可靠任务生命周期、工厂字段映射、RCS 调度或设备防呆。单个 WES 部署只有一个
明确配置的目标 WMS 连接。

## 唯一数据流

```text
WorkLine Plugin → WmsCapabilities ───────────────────────┐
WmsConfirmation → WmsConfirmationSender ────────────────┼→ HttpWmsGateway → WMS
TransportTask → Transport Port → WmsForwardedTransportClient ┘
```

所有 QUERY 由调用进程直接执行：

- Q01–Q19 由 `WmsCapabilities` 返回同步类型化结果。
- E01–E07/E15 由可靠对象 `WmsConfirmation` 持久化义务，再调用无状态 sender 获取同步结果。
- E08–E14 由 `TransportTask` 拥有领取、重试、轮询、批次状态和终态成员最终事实；不建立独立成员进度生命周期，WMS Client 只做单次 submit/status。
- E16 由 `TransportTask` 判定何时可取消，并通过同一无状态搬运 Client 执行单次 cancel。

每项 operation 使用一个垂直 capability 模块，内聚 request/result、固定 method/path、拒绝码和
`WmsCallSpec`。公共 Protocol/Gateway 只提供显式方法；生产运行时不存在 Provider/Catalog registry、动态发现、
conformance 平台或 WMS codegen。

## 业务目标

- Decision A 已采用 WMS 初稿覆盖 16 项的 path/业务字段和当前批准 method，E02 为 `POST`；完整 DTO 字段矩阵、其余
  19 项，以及 E08–E14 status/状态闭集/关联键/幂等承诺仍是实施前置门禁。
- Q08 按 Decision A 返回 GRN header 与 PO/物料 `items[]` 明细；已绑定料盘及汇总通过 Q09 查询。
- Q19 是无副作用粗分准入，首次有效结论先落本地 admission fact。
- E05 只做库存账务转移，不隐式调度搬运。
- E08 同工作位需求合并。
- E11 位于粗分移出固定交换位与 STATION A/B 之间，WMS 选择空箱和目标储位。
- E12 是冻结成员批次；E13 是有界 FIFO 候选窗口和 ACK 有序接纳前缀。
- 已发生本地物理事实永久保留；外部同步失败形成待确认义务或依赖暂停，不创建通用 Hold/Reconciliation 生命周期。

## 南向扫码流水

南向取料 result/CALLBACK 确认共享交接位置已经释放，并完成对应位置投影后，才允许下一北向取料；南向取料
result 后执行扫码，SCAN result 由 WES 做 typed 决策并下发投放，PUT result 提交最终位置事实。ACK 只表示设备
接纳命令，不得用于释放物理位置。厂商命令类型、wire DTO 与映射只存在对应 Adapter 版本；插件只消费标准化
角色事件与逻辑动作，业务与插件配置不得复制厂商映射。

## 可靠性与验收

WMS 正常 outcome 必须携带真实非空 `evidence_key`。发送前 evidence 失败则不发送；发送后 evidence 失败标记
远端结果未知。只有逐 operation 合同明确批准安全重提且对象显式 `retryable=true` 时，可靠对象才保留内部原
`dispatch_key`，映射为唯一获批 wire 字段后重提；否则保持暂停并进入人工对账。每个公开调用复用进程级 Phase 2
Transport，只执行一次有界请求/响应、申请一次 breaker permit、完成一条 evidence 和一次最终 breaker 更新；列表能力
不自动续页。

上线前必须证明：35 个垂直模块无缺失/重复，Q19 无副作用，8 个同步确认、7 个异步搬运与 1 个取消操作的
所有权精确，测试态 conformance harness/Mock fixture 完整，生产无 registry/动态发现，旧 transport、
Provider/Catalog、RuntimeIntent/Effect 和 System Capability 均不进入最终运行态。
