# plan_delta 联合合同冻结材料（T1-A）

状态：**WES 当前合同已更新，等待 WMS 联合验收**。原材料日期：2026-09-06；当前修订：2026-09-12。
本文原“管理对账应用修正”语义已由正常 `plan_delta` record/replay 自动校验和应用取代；WES 不再提供管理员
`apply-correction` 入口。本文保留 WMS 联合验收事项，不作为已部署或双方验收完成的证明。

依据：[主合同](../contracts/wms-outbound-picking-task-integration-requirements.md) §4.3、§8.2；
[实施设计](../superpowers/specs/2026-09-04-outbound-picking-task-plan-delta-design.md) §5、T1-A，以及
[无阻塞执行设计](../superpowers/specs/2026-09-11-wes-nonblocking-execution-design.md) ENG-D5。

## 1. 待共同冻结的合同差异

| 项目 | 原合同差异 | WES 已实现语义 |
| --- | --- | --- |
| prepare 结果未确定时计划先到 | §4.3 将已保存但条件未满足归为 DECIDED/WAIT 或 NO_BATCH；plan_delta 未定义这类业务响应 | 对符合 §5 暂存条件的 revision 1，保存 PENDING Evidence 后返回 503/UNAVAILABLE；WMS 原身份、原 timestamp、原正文重试 |
| 计划冲突修正后重放 | §8.2 未声明合法修正可经正常 record/replay 重新校验 | 新 identity 的严格下一 revision 经正常入口校验；合法时原子应用并清精确 blocker，已保存 RECONCILING 的同 identity 同正文重报也走同一路径；历史拒绝保留 |

WES 主合同 §4.3、§8.2.1 已包含 plan_delta 专属例外、前置条件、同事务应用与重放顺序。
其它 operation 的 WAIT/NO_BATCH、错误码和重试约定保持其原合同。
如果 WMS 不接受，先修订设计并评审，不能在实现中自行选另一种 ACK。

## 2. 请求身份与响应示例

WMS 调用 `POST {{WES_BASE_URL}}/api/v1/wms/events`，operation 为 `outbound.picking_task.plan_delta@v1`。
请求完整 data 以主合同 §8.1 为准；以下响应假设该请求的 operation_id 为
`019f12d0-58d7-7b4d-a23a-1b90aa5d4472`。示例时间为示意值。

prepare 结果未确定且满足暂存条件，HTTP 503：

```json
{
  "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
  "code": "UNAVAILABLE",
  "timestamp": 1786060800123,
  "data": {}
}
```

该响应不承诺计划已应用；WMS 不发布下一 revision，也不创建新 operation_id。
匹配的 PREPARE_ACCEPTED 已可靠保存后，WMS 原请求重试，WES 在同一事务应用计划、成员和版本，提交后返回 HTTP 202/RECEIVED，
其 data 仍为 `{}`；成功原样重放返回 HTTP 200/DUPLICATE。不以回调到达推定 prepare 成功。

任务阻塞期间，新 identity 的严格下一 revision 修正计划仍走正常 Event record。若当前尚不合法则留存并返回 HTTP 409：

```json
{
  "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
  "code": "CONFLICT",
  "timestamp": 1786060800123,
  "data": {"reason_code": "STATE_CONFLICT"}
}
```

同 identity 同正文在当前事实已满足后重报，WES 重新校验并原子应用，首次成功返回 HTTP 202/RECEIVED；随后重放返回 HTTP 200：

```json
{
  "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
  "code": "DUPLICATE",
  "timestamp": 1786060800123,
  "data": {}
}
```

正常入口应用成功后，成功重放沿用修正 Evidence 首次接收时间；后来 revision 推进、任务再次阻塞或结束不改变已成功请求的重放结果。
同 identity 修改正文仍为 409/CONFLICT，reason_code 为 IDEMPOTENCY_CONFLICT。

## 3. 双方可执行 fixture 待落实矩阵

以下是联合用例要求，尚非执行结果。双方冻结后，每条必须绑定完整请求、响应、前置状态、顺序及双方运行产物，
不能用只校验静态 JSON 的自测替代协议行为验证。

| 编号 | 前置状态与动作 | 预期 |
| --- | --- | --- |
| J01 | revision 1 先到；任务与冻结绑定匹配，prepare 未超期且结果未定 | PENDING；503/UNAVAILABLE；计划成员与版本不变 |
| J02 | J01 请求按原 identity、timestamp、正文重试，prepare 仍未定 | 复用原 Evidence；503；不创建重复义务或计划 |
| J03 | 匹配 PREPARE_ACCEPTED 已落库后重试 J01 | 同事务应用后 202/RECEIVED；再次重放 200/DUPLICATE |
| J04 | J01 后 prepare 超期、进入 RECONCILING 或确定冲突 | 转入确定冲突；409/STATE_CONFLICT；无计划应用和后继动作 |
| J05 | 已存在匹配 blocker；以新 identity 提交当前合法的严格下一 revision 修正计划 | 正常 record 原子应用；202/RECEIVED；版本推进一次、精确 blocker 清除、历史拒绝保留 |
| J06 | 已保存为 RECONCILING 的同 identity 同正文修正，在当前事实满足后重报 | 正常 record 重新校验并原子应用；202/RECEIVED；随后重放 200/DUPLICATE |
| J07 | 正常 record 应用事务失败或回滚，再原正文重报 | 不发生成员、版本或 blocker 的部分提交；重报按当前事实重新校验 |
| J08 | J06 后用原 identity 修改正文 | 409/IDEMPOTENCY_CONFLICT；保留已应用事实 |
| J09 | J06 后任务推进或结束，再原样重放 J05 | 仍为 200/DUPLICATE；不重复应用 |
| J10 | WMS 未取得修正版本成功 ACK / 取得成功 ACK 后 | 前者禁止发下一 revision；后者才按连续 revision 发布 |
| J11 | blocker 引用原本已 APPLIED 的 Evidence，重放其原成功正文 | 仍为 200/DUPLICATE；实际拒绝的冲突正文不随其它修正成功而变为成功 |

合法修正只由正常 plan_delta record/replay 自动校验和应用；不通过管理员 API、改库、修改同 identity 正文或跳 revision 续行。
ACK 与数据库验证均不代表物理动作或业务验收完成。

## 4. 退出证据记录

| 必需证据 | 当前状态 |
| --- | --- |
| WMS 确认人、日期及可追溯书面记录，明确接受两项语义 | 未提供 |
| 双方一致的主合同 revision / 内容指纹 | 未冻结 |
| 双方可执行 fixture 路径、版本及运行结果（覆盖 J01–J11） | 未提供 |

三项齐全后关闭 T1-A 联合验收。T1-B–T5 的实施和联调发布按用户授权独立推进；WES 自测通过不能代替 WMS 接受。
