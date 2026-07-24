# WMS 北向可行性报告（开发 mock 合同门禁）

- 结论：**GO（仅开发阶段 mock 合同门禁）**
- 确认时间：2026-07-24
- WES owner：WES Runtime Team
- 开发 WMS stub owner：WES Mock WMS Team
- stub/build version：`task1-dev-mock-v1`
- WES 确认状态：已确认开发 mock 合同门禁
- 开发 Mock WMS 确认状态：已确认公开 HTTP stub 行为
- 依据：[WMS 北向最小交互合同](../contracts/wms-northbound-interaction-contract.md)

## 范围与限制

用户已明确开发阶段 WMS 能力由 mock 提供。本报告的 GO 仅证明开发 mock 能以 HTTP 黑盒方式满足最小合同，
不等同真实 WMS 书面确认或生产准入。真实 WMS 的 endpoint、认证、operation 清单、保留期、SLA、响应大小、
限流承诺及双方签字，必须在 Task 9 重新验收；未完成前不得把本报告解释为真实 WMS 的 GO。

## 承诺参数（开发 mock）

| 参数 | 值 | 门槛 |
| --- | ---: | --- |
| WES max confirmation age | 6 秒 | 开发 stub 可控时钟参数 |
| safety margin | 3 秒 | 开发 stub 可控时钟参数 |
| WMS retention | 9 秒 | 不小于前两项之和 |
| WMS visibility SLA | 2 秒 | 不大于 NOT_FOUND grace period |
| WES NOT_FOUND grace period | 3 秒 | 部署参数 |
| 最大响应体 | 4096 bytes | 以超限 wire body 负测验证 |

认证在开发 stub 中由进程内隔离替代；真实 WMS 必须在 Task 9 明确 TLS/credential 方案，且探针不记录 credential。

## 开发 mock 公开面与 operation 清单

| 项目 | 值 |
| --- | --- |
| submit endpoint | `POST /northbound/operations` |
| status endpoint | `GET /northbound/operations/status` |
| 公开效果观察面 | `GET /northbound/operations/effects`，仅返回 effect count |
| 可控时钟（开发 stub） | `POST /northbound/test-clock/advance` |
| operation | `wms.fulfillment.notify_pkg_binding@v1` |

`/northbound/test-*` 只供开发 mock 探针使用，绝不属于真实 WMS 生产接口。

## 黑盒探针证据

运行：`uv run pytest tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py -q`。
探针不导入 WES 生产 adapter、不读取 mock 内部状态，只通过 submit/status HTTP 响应断言。所有强制 case 通过：

| case | 结果 |
| --- | --- |
| 首次提交、处理中重放、已完成重放、同 key 冲突 | PASS |
| ACCEPTED → PROCESSING → COMPLETED（任意非负递增版本）、typed result、REJECTED 稳定查询、NOT_FOUND | PASS |
| 首次未到达后重提、受控恢复、已受理暂不可见唯一效果、已见状态后对账 | PASS |
| 可控时钟下的最小保留期/可见性边界、流式最大响应体、429 两种 Retry-After、5xx、真实客户端提交/查询超时 | PASS |

探针输出只含本地 case 枚举和布尔结果；恶意远端 body 的负测已验证 stdout、stderr 和报告均不含 secret/PII/body。
