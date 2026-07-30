# WMS / RCS 全量工厂接口要求

本文描述 WES 与 WMS/RCS 的当前生产合同。旧 transport facade、旧终态 callback、通用 Workline
External HTTP Outbox 和请求期 provider 选择均已删除，不是可恢复的兼容入口。

## 1. 合同真源

- 北向调用以 35 个 typed operation registry 为唯一真源。
- 每个 operation 冻结 identity、target code、请求/响应模型、超时预算、完成模式和 reject code。
- WES 只能使用当前部署的 provider profile 与 HMAC revision。
- EFFECT 的同步提交结果、异步 ACK 和权威 status 查询不得互相替代。
- E08–E14 异步 EFFECT 的终态只来自权威 status 查询；callback 只提供唤醒 hint。

## 2. WMS 调用 WES

普通业务事件统一进入 `POST /api/v1/callback/event`，只允许：

- `WMS_GRN_RECEIVED`
- `WMS_PALLET_ARRIVED`
- `WMS_INVENTORY_UPDATED`
- `WMS_PDA_OPERATION_RECORDED`

普通事件顶层必须包含 `source_system=WMS`、`event_type`、`source_event_id`、`source_version`、
offset-aware `occurred_at`、`request_id`、可选 `correlation_id` 和 event-specific `data`。
`WMS_GRN_RECEIVED.data` 的业务身份是 `grn_id + po_number + po_item + material_code`，不得包含
`item_count` 或 `items[]`。

异步 EFFECT 提示只进入 `POST /api/v1/callback/external`，且只允许
`WMS_EFFECT_STATUS_HINT`。Hint 顶层只允许必填 `source_system=WMS`、`callback_type`、
`source_event_id`（最长 160 字符）、offset-aware `occurred_at`、`data` 和可选 `trace_id`；
`data` 只允许已注册 E08–E14 的 `operation_identity`、`idempotency_key` 与 `dispatch_key`。
不得携带 `command_code`、`device_code`、`finish_time`、`status`、`result` 或其它额外字段。

RuntimeInbox 以 payload 自身的事件类型为权威；路由参数、内部参数和沙箱参数不能覆盖 payload
中的类型。普通事件提交到 `/external`、hint 提交到 `/event`、未在冻结允许集内的 WMS/RCS 类型均拒绝，
且不得写入 RuntimeInbox。

## 3. WES 调用 WMS

WES 根据 35-operation registry 解析 endpoint：

- QUERY operation 从部署唯一 Provider profile 编译目标 endpoint。
- EFFECT operation 以同一 registry 冻结 target、认证、预算与 canonical payload。
- E08–E14 返回 ACK 后进入权威 status 轮询，直到业务终态或预算耗尽。
- 未注册 identity、target、裸 URL 或通用 External HTTP facade 请求必须在创建 Outbox 前失败。

operation 的完整字段、35 条清单和 E01–E14 编号以
[`docs/contracts/external-contract-profile.md`](../contracts/external-contract-profile.md) 为准。

## 4. RuntimeInbox 与幂等

- WMS 为每条普通事件和 status hint 生成全局唯一 `source_event_id`。
- 四类普通事件以 `source_system + source_event_id` 判定身份；同一 ID 不能跨 event type 复用。
- status hint 以 `source_system + callback_type + source_event_id` 判定身份。
- 完全相同的重复消息只 ACK，不重复执行业务副作用。
- 同一 identity 对应不同 payload 必须记录冲突并拒绝处理。
- Callback API 只负责认证、合同校验、RuntimeInbox 落库与快速 ACK；业务处理由编排消费者执行。

## 5. 分拣机南向因果

分拣机入库的下一次北向 PICK 只能由上一条南向 PICK command 的 ACK 推进。扫码平台展示状态、
UI 预测或本地轮询不得代替 ACK，也不得自行触发下一次 PICK。

本地物理完成事实先落库，再提交对应 typed WMS operation。WMS 暂时失败时进入
`WMS_SYNC_PENDING` 或 `RECONCILING`，不得抹掉已确认的本地物理事实。

## 6. 沙箱与 Mock

- 沙箱普通事件与 status hint 仍按生产端点分流，不提供 `/external` 普通事件兼容入口。
- 沙箱不能构造旧 WMS/RCS 终态，也不能生成旧 transport Outbox。
- Mock status route 只接受 E08–E14 identity；同步 operation 或未知 identity 必须在 fault injection、
  delay 和 hook 之前合同拒绝。
- Mock、fixture 和 smoke 数据必须使用当前事件、typed operation 与南向 ACK 因果。

## 7. 发布验收

- Registry 固定为 35 个 operation。
- Callback allow-set 固定为四类普通事件加 status hint。
- 旧 target code、URL setting、provider profile、credential reference 和终态 callback 在生产源码、
  部署配置、fixture、脚本、活跃文档与正向测试中均不存在。
- 未迁移 T2/T5 入口明确领域失败，不提供兼容路径。
