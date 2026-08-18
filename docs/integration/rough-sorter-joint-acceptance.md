# 粗分机分层与现场联合验收状态

## 总体结论

**NOT RUN — BLOCKED**（2026-08-18）。

插件部署级单主成功路径已经在仓库隔离环境通过；真实供应商一致性和 WMS/WES/RCS/ECS 现场联合验收尚未运行。各层证据不可互相替代，因此当前不能标记业务验收完成。

## 分层状态

| 层级 | 状态 | 当前有效证据 | 证据边界 / 阻塞 |
| --- | --- | --- | --- |
| 核心基线 | PASS | Commit `a3a801d4` normal hook QUALITY：`3526 passed, 4 skipped`；最终 Dockerfile selector HEAVY：`24 passed, 0 skipped` | 只证明仓库核心与所选真实基础设施测试，不替代供应商或现场业务验收 |
| WMS Adapter | PARTIAL | 同一 Commit 的 FAST/QUALITY 与最终 selector HEAVY；WMS operation 与 DTO 以仓库合同测试为 owner | 未连接真实 WMS，mock 不替代 WMS 现场验收 |
| 粗分插件部署 E2E | PASS | `uv run --project workline_plugins/rough_sorter pytest workline_plugins/rough_sorter/tests/e2e -q`：`8 passed, 0 skipped` | 只证明单主成功路径；WMS/ECS 为 stdlib mock；Celery `solo` 证明业务闭环，不证明 prefork 并发 |
| 供应商一致性 | NOT RUN — BLOCKED | 见 [`rough-sorter-supplier-conformance.md`](rough-sorter-supplier-conformance.md) | 缺真实 ECS/网关、设备版本与供应商原始证据 |
| 现场联合验收 | NOT RUN — BLOCKED | 无 | 缺真实 WMS、ECS、RCS、设备、料盘、现场配置、安全确认和联合执行窗口 |

## 已通过的插件部署 E2E

| 项目 | 值 |
| --- | --- |
| 执行日期 | 2026-08-18 |
| WES Commit | `a3a801d4e93d56334b38ab084851e91cf456e8a2` |
| 插件 / SDK 版本 | `wes-rough-sorter-plugin 1.0.0` / `wes-plugin-sdk 0.1.0` |
| 后端镜像 | `sha256:471305f490c2abe1c29baeaa064c20361fb9f34c1b5ce8b2cfb7268e25b41b1f`；OCI revision=`a3a801d4e93d56334b38ab084851e91cf456e8a2`，source-manifest=`f46e2c43e5aac3bf18cc17a05bea8d7dbbc1b6c6` |
| 环境 | 随机隔离 Docker network；真实 PostgreSQL、Redis、WES API、Celery worker + embedded Beat、fulfillment worker |
| 模拟边界 | stdlib WMS mock、stdlib ECS mock；没有真实供应商或现场设备 |
| 主路径 | `SCAN_COMPLETED → ACCEPT → PICK_AND_PUT → MOVE_FORWARD → ASSIGNED → PICK_AND_PUT → RECORDED → CLOSED` |
| 结果 | `8 passed, 0 skipped` |
| 临时运行日志 | 执行机 `$TMPDIR/rs9-e2e-{api,worker,fulfillment}-5e337d1959.log`；不作为长期现场证据 |

测试启动后用 direct SQL 设置初始环境：静态 WorkLine/Epoch/设备/位置配置，以及 Phase 7 DeviceStatusObservation 和 Transport RackPlacement 两个可信运行态投影。direct SQL 不证明这两个投影的生产 owner，也不写本次 material execution、evidence、command 或 confirmation 状态；这些对象全部从 HTTP ingress 开始由真实 WES 路径形成。最终以只读 SQL 断言 execution `CLOSED`、3 个命令和 3 个 confirmation 均唯一且完成。

## 现场联合验收解除阻塞条件

- 供应商一致性验收先完成并冻结 ECS/网关、设备/固件版本及原始证据位置。
- 提供真实 WMS、WES、RCS、ECS 版本、镜像 digest、Endpoint、网络和非敏感活动 Epoch 配置清单。
- 准备可追踪的真实料盘、旧/新架与位置身份，以及现场安全联锁、清线和人工恢复负责人。
- 约定联合执行窗口，覆盖单主成功路径以及批准计划要求的冲突、`WAIT`、无 Cell、ACK 后重放、callback 未知、旧架 release gate、双 `RACK_MOVE` 失败和人工核验恢复。
- 每项保存操作身份、时间线、系统日志、设备原始记录、WMS/RCS 结果和执行/复核人；任何一层失败时总体保持 BLOCKED。
