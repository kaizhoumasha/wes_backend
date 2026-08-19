# 粗分机供应商一致性验收状态

## 当前结论

**OUTSIDE BACKEND RC — SITE OWNED / NOT RUN**（2026-08-20）。

真实供应商 ECS/网关、WMS、RCS 和设备联调环境位于用户现场，开发环境按设计不连接这些系统。本项由现场部署验收人员执行，
不阻塞后端或前端各自关闭 RC。当前仍不能宣称供应商一致性通过；`workline_plugins/rough_sorter/tests/e2e/` 使用的 stdlib ECS mock
只模拟 WES 统一 wire，不能替代供应商实现。

## 当前仓库证据边界

| 项目 | 值 |
| --- | --- |
| WES Commit / tree | `c81440509f7ab1a312630a00bde3bbc877d7c35a` / `783132672d9a0ac6f5626f56368ad15a53244e0a` |
| 插件 / SDK 版本 | `wes-rough-sorter-plugin 1.0.0` / `wes-plugin-sdk 0.1.0` |
| 后端镜像 | `sha256:19852fdfb89abf8fb77ccc91036a87ccaa049a0aea39ff16d630db39b437fac5`；OCI revision/source-manifest 已与上述 WES Commit/tree 严格匹配 |
| 仓内工程验收 | PASS；QUALITY `3624 passed, 4 skipped`，本次 selector HEAVY `85 passed, 0 skipped` |
| 本机 Mock 模拟联调 | PASS；插件 E2E `11 passed, 0 skipped`；真实 WES HTTP、PostgreSQL、Redis、Celery/Beat，WMS 与 ECS 均为明确 mock 边界 |
| 供应商一致性 | NOT RUN；无真实供应商实现参与 |

本机 Mock E2E 证明固定统一 wire 可以驱动插件业务闭环，并验证 WMS `WAIT` 后继及 ECS `ACK` 跨下一次真实 Beat 不重放；它不证明供应商私有协议映射、PLC 动作、物理状态、时限或安全联锁正确。
该 E2E 启动后用 direct SQL 设置初始环境：静态 WorkLine/Epoch/设备/位置配置，以及 Phase 7 DeviceStatusObservation 和 Transport RackPlacement 两个可信运行态投影。direct SQL 不证明投影的生产 owner，也不写本次 material execution、evidence、command 或 confirmation 状态。

## 现场执行所需外部输入

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
