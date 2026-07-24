# WMS 北向联调验收与整体切换记录

本文是单工厂、单 WMS Provider 的发布证据模板。WES 只验收双方可观察的 submit、status query 和可选
callback hint 交互，不记录或推断 WMS 内部工作流。开发阶段使用仓库 mock/replay 验证 WES 合同实现；真实 WMS
尚未上线，因此真实联调、数据清理和整体切换必须保持未完成状态。

## 当前结论

- 开发 mock 验证：`PASS`
- 真实 WMS 联调验收：`PENDING`
- 联调测试数据清理：`BLOCKED`
- 整体切换：`BLOCKED`

`PASS` 仅表示当前代码能通过确定性 mock/replay 合同测试。不得用 mock 结果替代真实 WMS 联调验收，也不得预填确认人、验收时间、WMS 构建版本。只有下述真实证据完整、双方确认且清理目标经过单独审查后，才能改变
`PENDING/BLOCKED` 状态。

## WMS 团队必须提供的能力

| 能力 | WMS 交付要求 | WES 验收方式 |
| --- | --- | --- |
| EFFECT submit | 接收 `operation_identity + idempotency_key + canonical_payload + frozen_binding`；同键同 fingerprint 幂等重放，同键异 fingerprint 返回稳定冲突码 | 按合同矩阵验证 202、200、409、422、真实 deadline 超时及重复提交 |
| EFFECT status query | 按 `operation_identity + idempotency_key` 返回五态、单调 `source_version`、稳定拒绝码和 operation-specific typed result | 对三类 EFFECT 逐项回放可见状态、终态、`NOT_FOUND`、429、5xx、超时和响应体上限 |
| QUERY | 实现 `wms.inventory.query_inventory@v1` 的预算、分页、数值精度、业务拒绝、429 和错误形状 | 执行 QUERY 合同题库并保存规范化结果 |
| callback hint（可选） | 仅携带关联键，允许 WES 提前发起 status query；不提供终态权威 | 验证接收、拒绝、重复、触发查询以及 enqueue 失败后的 scanner 接管 |
| 保留与可见性 | 承诺幂等/状态记录保留期、状态可见性 SLA、最大响应体和 submit/status deadline | 验证 `retention >= WES max confirmation age + safety margin` 且 `visibility SLA <= NOT_FOUND grace` |
| 安全 | 提供 TLS 保护和部署凭据交付流程 | 仅记录认证方式与凭据引用版本；证据中不得出现密钥、完整 header 或未脱敏 body |

必须覆盖的 operation：

- `wms.inventory.query_inventory@v1`
- `wms.inventory.confirm_inbound@v1`
- `wms.fulfillment.full_box_exchange@v1`
- `wms.fulfillment.notify_pkg_binding@v1`

## 真实联调验收记录模板

以下字段只能从目标联调环境和发布构建采集，空白表示未验收。

| 发布事实 | 真实值 |
| --- | --- |
| Provider identity |  |
| Contract version |  |
| WES 构建版本 |  |
| WMS 构建版本 |  |
| 联调环境标识 |  |
| WES max confirmation age |  |
| WES `NOT_FOUND` grace |  |
| Safety margin |  |
| WMS 幂等记录保留期 |  |
| WMS 状态记录保留期 |  |
| WMS 状态可见性 SLA |  |
| Submit/status deadline |  |
| 最大响应体 |  |

每个验收 case 都必须记录下列字段；失败证据只能保留有界、脱敏后的协议事实。

| Operation | Case ID | Request canonical hash | HTTP/status result | 规范化结果 | 耗时 | 脱敏 evidence ref | 结论 |
| --- | --- | --- | --- | --- | ---: | --- | --- |
|  |  |  |  |  |  |  |  |

最低 case 集合为：首次受理、处理中同键重放、已完成同键重放、同键异 fingerprint、提交真实 deadline
超时、可见性 SLA 边界、单调状态序列、拒绝、`NOT_FOUND` 宽限期、受控同键重提、已见状态后丢失、
保留期边界、响应体上限、429 + `Retry-After`、5xx 和状态查询超时。三类 EFFECT 的 `COMPLETED` 必须分别通过
对应 typed result 校验。

双方确认字段：

| 角色 | 姓名 | 构建/合同确认 | 验收时间 | 结论 |
| --- | --- | --- | --- | --- |
| WES 确认人 |  |  |  |  |
| WMS 确认人 |  |  |  |  |

在上述表格为空时，真实 WMS 联调验收只能是 `PENDING`。

## 清理前置条件与记录

联调测试数据清理不是仓库测试。执行前必须具备精确环境、数据库/租户范围、双方数据 owner、备份位置和经过
单独评审的清理清单/SQL；本仓库不提供可直接执行的破坏性脚本。

全部前置条件：

1. 真实联调验收全部 case 通过，三个 EFFECT 均已终结或对账关闭。
2. 停止联调任务及对应 Celery worker，并证明双方没有新的 EFFECT receipt。
3. 备份必要的脱敏诊断日志，解析 Intent、Outbox、inbox、reconciliation 及历史表的依赖顺序。
4. WES 与 WMS 数据 owner 共同确认精确删除目标、恢复方法和执行窗口。
5. 清理后运行空环境 migration、健康检查、配置检查、backlog/worker 检查和一个 QUERY smoke case。

| 清理事实 | 真实值 |
| --- | --- |
| 环境与数据范围 |  |
| 备份 evidence ref |  |
| 审批后的清理清单/SQL ref |  |
| 执行人与复核人 |  |
| 开始/完成时间 |  |
| 清理后残留检查 |  |

当前没有这些前置条件，因此联调测试数据清理保持 `BLOCKED`。

## 整体切换与回退边界

1. 使用唯一 active WMS Provider 配置启动，先保持真实 EFFECT admission 关闭。
2. 通过健康检查、配置/SLA 不变量、status query backlog/worker 和 QUERY smoke preflight 后记录 GO。
3. GO 后才开放真实 EFFECT admission。首个真实 EFFECT 离开 WES 本地边界或结果不明确即越过不可逆点。
4. 首个真实 EFFECT 前，只有在双方零 receipt 有证据时才允许回退部署和 migration。
5. 首个真实 EFFECT 后发生故障时，立即关闭新 EFFECT admission；保持 status query、callback hint、租约恢复和
   reconciliation worker 运行，保留当前 schema/账本并 forward-fix。禁止 downgrade、清空在途数据或恢复
   callback 终态权威。

| Cutover 事实 | 真实值 |
| --- | --- |
| Preflight evidence ref |  |
| EFFECT admission 初始状态 |  |
| 双方零 receipt evidence ref |  |
| GO 确认人与时间 |  |
| 首个真实 EFFECT dispatch/receipt evidence ref |  |
| 不可逆点时间 |  |
| 失败时 admission 关闭及 forward-fix 记录 |  |

当前真实联调和清理均未完成，因此整体切换保持 `BLOCKED`，不得记录 GO。

## 仓内可重复证据

- QUERY：`tests/fixtures/wms_provider_conformance/query_inventory_replay.v1.json`
- 入库确认状态：`tests/fixtures/wms_provider_conformance/confirm_inbound_status_replay.v1.json`
- 满箱交换状态：`tests/fixtures/wms_provider_conformance/full_box_exchange_status_replay.v1.json`
- 料盘绑定状态：`tests/fixtures/wms_provider_conformance/notify_pkg_binding_status_replay.v1.json`

这些 fixture 只包含合成事实、规范化结果和 digest，可验证 WES parser/typed contract 的确定性；它们不包含生产
密钥、真实业务数据或真实 WMS 验收结论。
