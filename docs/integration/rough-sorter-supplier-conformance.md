# 粗分机供应商一致性验收状态

## 当前结论

**NOT RUN — BLOCKED**（2026-08-18）。

仓库内没有可访问的真实供应商 ECS/网关、设备/固件版本、设备序列号、供应商执行人签字或供应商侧原始证据，因此未运行、也不能宣称通过供应商一致性验收。`workline_plugins/rough_sorter/tests/e2e/` 使用的 stdlib ECS mock 只模拟 WES 统一 wire，不能替代供应商实现。

## 当前仓库证据边界

| 项目 | 值 |
| --- | --- |
| WES Commit | `cfa9b89b5e70087b53abc3420b3fbb8e5f59af68` |
| 插件 / SDK 版本 | `wes-rough-sorter-plugin 1.0.0` / `wes-plugin-sdk 0.1.0` |
| 后端镜像 | `sha256:96b0a43c5421a979c959932bea618b74145d8fea9789aa1e77208403e88fa23b`；OCI revision/source-manifest 已与当前 Commit/tree 严格匹配 |
| 仓库部署 E2E | PASS；`8 passed, 0 skipped`；真实 WES HTTP、PostgreSQL、Redis、Celery/Beat，WMS 与 ECS 均为明确 mock 边界 |
| 供应商一致性 | NOT RUN；无真实供应商实现参与 |

仓库部署 E2E 证明固定统一 wire 可以驱动插件业务闭环，不证明供应商私有协议映射、PLC 动作、物理状态、时限或安全联锁正确。
该 E2E 启动后用 direct SQL 设置初始环境：静态 WorkLine/Epoch/设备/位置配置，以及 Phase 7 DeviceStatusObservation 和 Transport RackPlacement 两个可信运行态投影。direct SQL 不证明投影的生产 owner，也不写本次 material execution、evidence、command 或 confirmation 状态。

## 解除阻塞所需外部输入

- 获批的 [`rough-sorter-device-contract.md`](../contracts/device-annexes/rough-sorter-device-contract.md) 对应供应商 ECS/网关版本、设备/固件版本和设备身份清单。
- 可访问的真实供应商测试环境、网络与执行窗口；凭据只在受控环境提供，不写入仓库或报告。
- 三个角色设备的真实 Endpoint、`device_code`、`contract_key`、`contract_version` 与活动 Epoch 绑定证据。
- 附录要求的 task/event、字段闭集、身份、ACK、CALLBACK、错误、超时、投递未知和不可逆点用例结果。
- 供应商执行人、WES 复核人、执行时间、环境版本与原始日志/抓包/设备记录的受控证据位置。

## 结果记录格式

真实验收后逐项追加记录，不覆盖失败证据：

| 用例 | WES 请求/回调身份 | ECS/设备版本 | 时间窗口 | 结果 | 原始证据位置 | 执行/复核人 |
| --- | --- | --- | --- | --- | --- | --- |
| 待运行 | 待提供 | 待提供 | 待安排 | NOT RUN | 未提供 | 未安排 |
