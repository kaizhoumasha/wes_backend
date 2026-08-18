# 粗分机分层与现场联合验收状态

## 总体结论

**WAREHOUSE ENGINEERING PASS；EXTERNAL ACCEPTANCE NOT RUN — BLOCKED**（2026-08-19）。

仓内工程门禁和本机 Mock 模拟联调已经通过；真实供应商一致性、真实 WMS 联调和 WMS/WES/RCS/ECS
现场联合验收尚未运行。Mock 只替代开发环境不可达的外部端点，不替代供应商实现、物理设备事实或业务验收，因此 Phase 8
总体仍不能标记完成。

## 分层状态

| 层级 | 状态 | 当前有效证据 | 证据边界 / 阻塞 |
| --- | --- | --- | --- |
| 核心与共享基础能力 | PASS | Commit `d90d0df6` hook QUALITY：`3532 passed, 4 skipped`；计划锁定的核心 FAST owner：`21 passed`；Phase 8 selector HEAVY：`362 passed, 0 skipped` | 证明当前仓库基础能力、真实 PostgreSQL/Redis/worker 与受影响 HEAVY，不替代外部系统和业务验收 |
| WMS Adapter 合同 | PASS | FAST owner、QUALITY 与 HEAVY 均通过；operation、DTO、幂等、`WAIT` 后继与投递未知由仓库合同测试拥有 | 只证明 WES 侧 ACL 与合同；真实 WMS 联调为 `NOT RUN` |
| 粗分业务能力本机 Mock 联调 | PASS | `uv run --project workline_plugins/rough_sorter pytest workline_plugins/rough_sorter/tests/e2e -q`：`10 passed, 0 skipped` | 真实 WES HTTP/PostgreSQL/Redis/Celery/Beat；WMS/ECS 为 stdlib mock，不证明供应商、PLC、RCS 或现场物理闭环 |
| 供应商一致性 | NOT RUN — BLOCKED | 见 [`rough-sorter-supplier-conformance.md`](rough-sorter-supplier-conformance.md) | 缺真实 ECS/网关、设备版本与供应商原始证据 |
| 现场联合与业务验收 | NOT RUN — BLOCKED | 无 | 缺真实 WMS、ECS、RCS、设备、料盘、现场配置、安全确认和联合执行窗口 |

## 已通过的仓内工程与本机 Mock 验收

| 项目 | 值 |
| --- | --- |
| 执行日期 | 2026-08-19 |
| WES Commit / tree | `d90d0df6f6044fbb008e3fbaaa6a92e2d3b99eb6` / `eed9e8cb2f8802a2ffee88db5eba1ef85839d585` |
| 插件 / SDK 版本 | `wes-rough-sorter-plugin 1.0.0` / `wes-plugin-sdk 0.1.0` |
| 后端镜像 | `sha256:b16303df7964945c528cdc16b26686d906405786c04a3f26b88e655de8fa45f4`；OCI revision 与 source-manifest 分别严格匹配上述 Commit/tree |
| 环境 | 随机隔离 Docker network；真实 PostgreSQL、Redis、WES API、Celery worker + embedded Beat、fulfillment worker |
| 模拟边界 | stdlib WMS mock、stdlib ECS mock；没有真实供应商或现场设备 |
| 主路径 | `SCAN_COMPLETED → ACCEPT → PICK_AND_PUT → MOVE_FORWARD → ASSIGNED → PICK_AND_PUT → RECORDED → CLOSED` |
| 本机 Mock 边界路径 | WMS `WAIT` 完成本次确认并创建新 operation，`ACCEPT` 前不创建设备命令；ECS `ACK` 后跨真实下一次 Beat 保持 `ACKNOWLEDGED:1` 且不重放，匹配 callback 后才关闭 execution |
| 插件 E2E | `10 passed, 0 skipped` |
| 核心 owner / HEAVY | `21 passed` / `362 passed, 0 skipped` |

测试启动后用 direct SQL 设置初始环境：静态 WorkLine/Epoch/设备/位置配置，以及 Phase 7 DeviceStatusObservation 和 Transport RackPlacement 两个可信运行态投影。direct SQL 不证明这两个投影的生产 owner，也不写本次 material execution、evidence、command 或 confirmation 状态；这些对象全部从 HTTP ingress 开始由真实 WES 路径形成。最终以只读 SQL 断言可靠对象状态与唯一性。该结果是可重复的本机模拟验收，不是生产路径或现场证据。

## 现场联合验收解除阻塞条件

- 供应商一致性验收先完成并冻结 ECS/网关、设备/固件版本及原始证据位置。
- 提供真实 WMS、WES、RCS、ECS 版本、镜像 digest、Endpoint、网络和非敏感活动 Epoch 配置清单。
- 准备可追踪的真实料盘、旧/新架与位置身份，以及现场安全联锁、清线和人工恢复负责人。
- 约定联合执行窗口，覆盖单主成功路径以及批准计划要求的冲突、`WAIT`、无 Cell、ACK 后重放、callback 未知、旧架 release gate、双 `RACK_MOVE` 失败和人工核验恢复。
- 每项保存操作身份、时间线、系统日志、设备原始记录、WMS/RCS 结果和执行/复核人；任何一层失败时总体保持 BLOCKED。
