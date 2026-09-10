# 执行中断后的诊断与恢复

本手册处理服务重启、消息丢失和结果迟到后的执行恢复。数据库和整机恢复由[现场数据恢复计划](../superpowers/plans/2026-08-18-wes-onsite-data-recovery.md)负责；该计划尚未完成真实异机恢复验收，不能把它当作已有备份能力。

## 先确定哪一层中断

记录对象身份、最后一次提交时间、当前状态、错误原因和资源绑定，再沿“入口收据 → Evidence → 执行对象 → 结果发布 → 业务消费者”定位。保留原始 operation 与 operation_id；不要用新身份试探物理命令是否可执行。

| 观察 | 能说明什么 | 下一步 |
| --- | --- | --- |
| HTTP 202 或接收 ACK | 合同规定的接收事实已经提交 | 查询 Evidence 和执行对象，不能据此认定物理完成 |
| Evidence 为 PENDING | 事实已保存，尚待应用 | 检查对应 worker 和原有周期扫描是否运行 |
| Evidence 为 APPLIED | 该事实已应用到所属领域 | 继续核对执行结果及消费者，不推定业务完成 |
| 未发布的 outcome version | 执行结果与发布进度尚未收敛 | 检查原发布 worker/扫描，不重新创建执行对象 |
| UNKNOWN / RECONCILING | 交付、结果或关联事实仍有歧义 | 保留身份与资源，取得匹配的 WMS/ECS 权威事实 |
| 查询 404 | 此查询范围没有找到记录 | 核对完整身份与入口；不能认定外部未接纳 |
| 查询 503 | 持久化查询不可用 | 先恢复基础服务，不能据此更改业务状态 |

恢复 worker、Beat 或 broker 后，使用已有持久化扫描推进原工作。技术恢复耗时与等待外部对账耗时分别记录。重启后仍阻塞的对象不得通过改库、清理绑定或跳过队头放行。

## Transport：收据、执行结果与资源

使用现有 Transport 详情接口查看 `send_started_at`、`result_deadline_at`、`submit_attempt_count`、`pending_evidence_count`、`outcome_version`、`published_outcome_version` 和 `active_binding_count`。

使用 `GET /api/v1/transport/callback-receipts?operation=…&operation_id=…` 查询原接收结果，需要 `ops:transport-callback-receipt:read`。收据中的拒绝码和原因是首次保存的结果；拒绝收据可能没有对应 Evidence。不要把内容冲突解释成可覆盖原事实。

`send_started_at` 已保存而结果未知时，即使怀疑网络尚未送达，也不能自动认定未发送。由 WMS/ECS 核对原身份、动作和实际位置，再通过正式回调入口提供匹配的权威结果。迟到结果到达后核对 Evidence 应用、执行状态、资源释放及结果发布，最后由消费者证明自身流程推进。

`TRANSPORT_RESULT_TIMEOUT_SECONDS` 只用于新接受任务的期限。修改配置不会延长已有 `result_deadline_at`；超时进入对账，不等于动作失败。配置生效、任务期限和现场路线耗时是三个独立验收项，见[配置索引](configuration-index.md)与[Transport 合同](../contracts/transport-fulfillment-contract.md)。

## WMS：可靠义务与入站事实分别查询

两类查询都使用完整的 `(operation, operation_id)`，只展示持久化事实：

- `GET /api/v1/wms-diagnostics/confirmations`：查询 WES → WMS 可靠义务，需要 `ops:wms-confirmation:read`。
- `GET /api/v1/wms-diagnostics/evidences`：查询 WMS → WES Evidence，需要 `ops:wms-evidence:read`。

两个接口都接收 `operation`、`operation_id` 查询参数。不要把 HTTP exchange 日志、缓存命中或查询成功当成义务完成，也不要把查询页面当成通用重试入口。基础层保存与推进可靠义务；业务是否满足准入、如何解释结果，由冻结业务上下文对应的消费者决定。

## Device：关闭原阻塞后重处理原 Evidence

通过现有设备 Evidence 阻塞详情核对 `source_event_id`、最新 `block_id`、原 DeviceCommand 和设备当前状态。先使用所属合同允许的权威设备结果或既有对账入口闭合原命令；设备空闲的判断不能靠超时推断。

满足当前 blocker 的准入后，使用其返回的 `reprocess_path`，即 `POST /api/v1/device/evidences/{source_event_id}/blockers/{block_id}/reprocess`，填写真实原因。入口会复核最新阻塞、原命令闭合和设备上是否还有未闭合命令，并保留审计。它重处理原 Evidence，不允许通过旧 blocker 绕开当前阻塞。成功后仍需核对新处理结果，不能直接认定物理动作完成。

## prepare：新身份重新求值的限定入口

工作线联调现有 `/runs/{run_id}/wms/retry` 只用于 prepare WMS 对账。其语义是“确认 WMS 已作废原 prepare 且不会再发送对应 plan_delta，使用新身份重新求值”，不是一般技术重试。

取得 WMS 的上述明确确认后，由有权操作的人员提交原动作标识、当前版本和完整 prepare 数据。服务会检查 run 阶段、原 step/confirmation 身份和任务关联，保存替换历史。没有 WMS 作废确认、版本冲突或身份不符时停止；不要修改确认布尔值来试探是否能通过。常规网络重试仍保留原身份与内容。

## 数据恢复后的放行与交接

从备份恢复时先禁止新物理派发，恢复匹配应用并按数据恢复计划启动空 Redis。备份时刻之后可能已经发生真实运动，数据库位置不能覆盖现场事实。逐项核对未闭合执行、Evidence、顺序和资源，再等待 WMS/ECS 权威闭合并按业务准入放行。

交接记录至少包含：候选代码与配置、故障窗口、原执行身份、恢复前后状态与时间、未闭合资源、权威结果来源及接收身份、待处理消费者、技术恢复时间和业务放行时间。基础恢复测试、插件/调试消费者验证、供应商一致性及现场业务验收分别出结论。
