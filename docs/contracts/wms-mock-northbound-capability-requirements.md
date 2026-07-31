# Mock WMS 北向能力要求

Mock WMS 必须读取与生产相同的静态 operation registry 和 operation-specific Pydantic model，不维护 identity、
request 字段或拒绝码的第二份手写白名单。

## 必须能力

- 35 项 request/result fixture 键集合与 registry 完全一致；缺少、多出或模型校验失败均阻断测试。
- Q01–Q18 为 GET，Q19 为无副作用 POST。
- 16 项 EFFECT 均为 POST；同步 9 项直接返回 typed terminal result，异步 7 项 submit 只返回 ACK。
- 异步 status 只接受 E08–E14 identity；同步项查询 status 必须 fail closed。
- 幂等作用域为 `operation_identity + idempotency_key`；同键不同 fingerprint 返回
  `IDEMPOTENCY_CONFLICT`。
- 业务拒绝只使用 Definition 冻结的 reason code。
- callback hint 只携带关联键并唤醒 status query，不携带终态 payload。
- reset、延迟、超时、429、5xx、畸形响应、部分失败、ACK 丢失等故障控制按精确 identity 隔离。

fixture 位于 `tests/mock/wms_operation_fixtures.py`，不得包含 credential、签名 header 或生产数据。覆盖测试逐项调用
request/result model 校验，不允许 skip/xfail。
