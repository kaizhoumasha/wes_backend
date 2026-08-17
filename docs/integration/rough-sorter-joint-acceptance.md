# 粗分机分层与现场联合验收状态

## 总体结论

**NOT RUN — BLOCKED**（2026-08-18）。

插件部署级单主成功路径已经在仓库隔离环境通过；真实供应商一致性和 WMS/WES/RCS/ECS 现场联合验收尚未运行。各层证据不可互相替代，因此当前不能标记业务验收完成。

## 分层状态

| 层级 | 状态 | 当前有效证据 | 证据边界 / 阻塞 |
| --- | --- | --- | --- |
| 核心基线 | PARTIAL | Commit `cfa9b89b` normal hook QUALITY：`3525 passed, 4 skipped` | 当前镜像/CI 变更选择的真实 HEAVY 仍由主验证入口刷新；QUALITY 不替代它们 |
| WMS Adapter | PARTIAL | 同一 Commit 的 FAST/QUALITY；WMS operation 与 DTO 以仓库合同测试为 owner | 未连接真实 WMS；最终 selector HEAVY 尚待主验证入口刷新 |
| 粗分插件部署 E2E | PASS | `uv run --project workline_plugins/rough_sorter pytest workline_plugins/rough_sorter/tests/e2e -q`：`8 passed, 0 skipped` | 只证明单主成功路径；WMS/ECS 为 stdlib mock；Celery `solo` 证明业务闭环，不证明 prefork 并发 |
| 供应商一致性 | NOT RUN — BLOCKED | 见 [`rough-sorter-supplier-conformance.md`](rough-sorter-supplier-conformance.md) | 缺真实 ECS/网关、设备版本与供应商原始证据 |
| 现场联合验收 | NOT RUN — BLOCKED | 无 | 缺真实 WMS、ECS、RCS、设备、料盘、现场配置、安全确认和联合执行窗口 |

## 已通过的插件部署 E2E

| 项目 | 值 |
| --- | --- |
| 执行日期 | 2026-08-18 |
| WES Commit | `cfa9b89b5e70087b53abc3420b3fbb8e5f59af68` |
| 插件 / SDK 版本 | `wes-rough-sorter-plugin 1.0.0` / `wes-plugin-sdk 0.1.0` |
| 后端镜像 | `sha256:96b0a43c5421a979c959932bea618b74145d8fea9789aa1e77208403e88fa23b`；OCI revision=`cfa9b89b5e70087b53abc3420b3fbb8e5f59af68`，source-manifest=`ced3e282b6f15695b7a57c69a71a6728ff8612b6` |
| 环境 | 随机隔离 Docker network；真实 PostgreSQL、Redis、WES API、Celery worker + embedded Beat、fulfillment worker |
| 模拟边界 | stdlib WMS mock、stdlib ECS mock；没有真实供应商或现场设备 |
| 主路径 | `SCAN_COMPLETED → ACCEPT → PICK_AND_PUT → MOVE_FORWARD → ASSIGNED → PICK_AND_PUT → RECORDED → CLOSED` |
| 结果 | `8 passed, 0 skipped` |
| 临时运行日志 | 执行机 `$TMPDIR/rs9-e2e-{api,worker,fulfillment}-b8325c5a13.log`；不作为长期现场证据 |

测试启动后用 direct SQL 设置初始环境：静态 WorkLine/Epoch/设备/位置配置，以及 Phase 7 DeviceStatusObservation 和 Transport RackPlacement 两个可信运行态投影。direct SQL 不证明这两个投影的生产 owner，也不写本次 material execution、evidence、command 或 confirmation 状态；这些对象全部从 HTTP ingress 开始由真实 WES 路径形成。最终以只读 SQL 断言 execution `CLOSED`、3 个命令和 3 个 confirmation 均唯一且完成。

## 现场联合验收解除阻塞条件

- 供应商一致性验收先完成并冻结 ECS/网关、设备/固件版本及原始证据位置。
- 提供真实 WMS、WES、RCS、ECS 版本、镜像 digest、Endpoint、网络和非敏感活动 Epoch 配置清单。
- 准备可追踪的真实料盘、旧/新架与位置身份，以及现场安全联锁、清线和人工恢复负责人。
- 约定联合执行窗口，覆盖单主成功路径以及批准计划要求的冲突、`WAIT`、无 Cell、ACK 后重放、callback 未知、旧架 release gate、双 `RACK_MOVE` 失败和人工核验恢复。
- 每项保存操作身份、时间线、系统日志、设备原始记录、WMS/RCS 结果和执行/复核人；任何一层失败时总体保持 BLOCKED。
