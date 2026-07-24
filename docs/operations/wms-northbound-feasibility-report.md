# WMS 北向可行性报告（开发 mock 合同门禁）

- 结论：**GO（仅开发阶段 mock 合同门禁）**
- 确认时间：2026-07-24
- WES owner：WES Runtime Team
- 开发 WMS stub owner：WES Mock WMS Team
- stub/build version：`task1-dev-mock-v1`
- 依据：[WMS 北向最小交互合同](../contracts/wms-northbound-interaction-contract.md)

## 范围与限制

用户已明确开发阶段 WMS 能力由 mock 提供。本报告的 GO 仅证明开发 mock 能以 HTTP 黑盒方式满足最小合同，
不等同真实 WMS 书面确认或生产准入。真实 WMS 的 endpoint、认证、operation 清单、保留期、SLA、响应大小、
限流承诺及双方签字，必须在 Task 9 重新验收；未完成前不得把本报告解释为真实 WMS 的 GO。

## 承诺参数（开发 mock）

| 参数 | 值 | 门槛 |
| --- | ---: | --- |
| WES max confirmation age | 600 秒 | 部署参数 |
| safety margin | 300 秒 | 部署参数 |
| WMS retention | 900 秒 | 不小于前两项之和 |
| WMS visibility SLA | 2 秒 | 不大于 NOT_FOUND grace period |
| WES NOT_FOUND grace period | 3 秒 | 部署参数 |
| 最大响应体 | 8192 bytes | 非零且验收记录 |

认证在开发 stub 中由进程内隔离替代；真实 WMS 必须在 Task 9 明确 TLS/credential 方案，且探针不记录 credential。

## 黑盒探针证据

运行：`uv run pytest tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py -q`。
探针不导入 WES 生产 adapter、不读取 mock 内部状态，只通过 submit/status HTTP 响应断言。所有强制 case 通过：

| case | 结果 |
| --- | --- |
| 首次提交、处理中重放、已完成重放、同 key 冲突 | PASS |
| ACCEPTED → PROCESSING → COMPLETED、typed result、REJECTED、NOT_FOUND | PASS |
| 首次未到达后重提、已受理暂不可见后重提 | PASS |
| 保留期/可见性参数、429 Retry-After、5xx、查询超时 | PASS |

探针输出只含 HTTP status、稳定错误码、状态和 source version；不含 secret 或完整响应体。
