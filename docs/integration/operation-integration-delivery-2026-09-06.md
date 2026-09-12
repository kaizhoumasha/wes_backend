# Operation 联调交付（2026-09-06）

> 本页下述部署记录对应先前独立实例的 issued/plan_delta 等验收范围，不代表当前 SHIP 基线已经部署。
> 当前基线新增 Operation、WMS `bin_code` 字段和 NG 出口上报清理尚待统一联调更新；不得要求 WMS 按旧实例推断新字段已生效。
> BinExecution 退役与三类整线插件业务不包含在本次 Operation 基线完成声明中。

状态：**已部署至独立联调实例，HTTP、持久化、worker 和进程重启验收通过**。WMS 联合验收尚未完成。

## WMS 调用入口

- 服务地址：`http://10.24.199.219:8003`。
- Swagger：`http://10.24.199.219:8003/api/docs`；机器合同：`http://10.24.199.219:8003/api/openapi.json`。
- WMS → WES：`POST /api/v1/wms/events`，`Content-Type: application/json`；隔离联调网内按现有固定 NONE 认证策略接收。
- WES → WMS prepare：`POST {WMS_BASE_URL}/api/v1/wes/decisions`。`prepare@v1` 由 WES 发起，不是 WMS 对 Event 入口发送的 operation。
- 本实例启用基础 Operation route、Service 和可靠 worker，`ENABLED_WORKLINE_PLUGINS=[]`；未启动人工拣选工作线或设备。
  WMS 可调用 issued 和 plan_delta；合法计划应用仍要求匹配的任务绑定及已持久化 PREPARE_ACCEPTED，不能对刚接收的 QUEUED 任务直接应用计划。

### issued 请求

```json
{
  "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
  "operation": "outbound.picking_task.issued@v1",
  "timestamp": 1786060800123,
  "data": {
    "task_id": "WMS-PICKING-001",
    "task_type": "MANUAL",
    "queue_revision": 1,
    "dispatch_sequence": 1
  }
}
```

每个新业务请求使用新的 canonical UUIDv7。技术重试保持原 operation_id、timestamp 和完整正文。

### plan_delta revision 1 请求

```json
{
  "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4473",
  "operation": "outbound.picking_task.plan_delta@v1",
  "timestamp": 1786060800124,
  "data": {
    "task_id": "WMS-PICKING-001",
    "plan_revision": 1,
    "target_rack": {"rack_id": "TARGET-001", "rack_face": "A"},
    "added_bin_source_racks": [{"rack_id": "SOURCE-001", "rack_face": "B"}]
  }
}
```

revision 1 必须包含 target_rack；后续 revision 禁止 target_rack，且至少新增一个来源。无变化字段省略，不发送 null 或空列表。
`rack_face`、`target_face`、`arrival_face` 及相关 face 字段为 1–10 个 Unicode 字符的非空 opaque 值；不做 trim、截断或数值转换。持久化字段使用 `VARCHAR(10)`，超过 10 个字符返回 422 INVALID_DATA。

### ACK 与重试

统一响应包含 `operation_id`、`code`、Unix 毫秒 `timestamp`、`data`。

| HTTP / code | 含义与 WMS 动作 |
| --- | --- |
| 202 / RECEIVED | 该 operation 要求的 Evidence 与业务事实已提交；plan_delta 成员和版本已原子应用。不是物理完成。 |
| 200 / DUPLICATE | 原规范化请求成功重放，不重复应用，保留首次接收时间。 |
| 409 / CONFLICT | `data.reason_code` 区分 IDEMPOTENCY_CONFLICT、STATE_CONFLICT、REVISION_CONFLICT、REFERENCE_CONFLICT；禁止修改原身份正文或跳 revision。 |
| 422 / REJECTED | 严格 DTO、operation 或业务数据不合法；已支持 issued／plan_delta 的 INVALID_DATA 在身份可识别时可靠保存，未知 operation 直接拒绝。修正内容使用新 identity。 |
| 503 / UNAVAILABLE | 保留原身份与正文重试；prepare 结果未定且符合暂存条件的计划会保存 PENDING Evidence，不能发布下一 revision。 |

计划冲突由 WMS 使用新的 `plan_delta` identity 和期望 revision 重新提交；WES 按普通 Event 路径可靠记录、校验并自动应用，
不提供管理员人工纠正入口。WMS 已成功 Event 的永久重放语义独立保留。

## 制品与验证

- 源码树：`72c7c62e564d49209a005a798dd82df4b0361775`；base HEAD：`bf98b4fee7824ff9da073d962bacf663a1e82a91`；明确标记未提交工作树。
- 镜像：`wes-backend:r2b2-72c7c62e564d`。
- 镜像 digest：`sha256:5c438e98fe376b284e3ee062e3c0fc3d2d820e6c7ca1f4bdb026a4e2b5eb8afd`。
- 压缩归档 SHA-256：`925e79706b11fba6a3cb00f6f4ca0e8e26998a32d82f83fed6dd55bc6d267a88`。
- 迁移 head：`3d040b37c049`；六个 face 字段均为 `VARCHAR(10)`，来源成员使用原文字段普通唯一索引。历史迁移安装的共享 pgcrypto extension 保留，当前来源索引与查询不再计算摘要。
- 本轮完整 QUALITY：2759 passed、5 skipped；生命周期切片精确选中 HEAVY：15 passed、0 skipped。此前 face10 切片 HEAVY 323 passed，聚焦 PostgreSQL／迁移验证 34 passed；本轮未修改 schema。真实 PostgreSQL、ASGI 和 prepare worker 在本地隔离环境通过。
- 远端发布目录：`/home/CANTAISYS/wes-operation-integration/releases/20260906-r2b2-72c7c62e564d`；Compose 项目 `wes_operation_integration`，环境文件 `.env.opint` 权限 0600。
- 远端保留原数据并完成备份，从 `864351b8d0c6` 升级至 `3d040b37c049`；四个应用进程已重建，数据库和 Redis 容器保持原 ID；pgcrypto 1.3、TimescaleDB 2.27.1；基础授权初始化和 `operation_admin` 登录通过。密码仅存放在远端 `.env.opint`，未写入文档或聊天。
- 六个容器 running/healthy，四个应用进程配置均为 `ENABLED_WORKLINE_PLUGINS=[]`；DB、Redis 不发布主机端口。现有实例 10 个容器 ID 均保持不变。
- 实际 LAN `/api/docs` 与 tailnet `/api/openapi.json`、Swagger CSS/JS 均 HTTP 200；发布五个 Event operation 和计划对账 API，未登录管理调用 HTTP 401。
- 实际 issued 请求结果为 202 RECEIVED → 200 DUPLICATE → 正文漂移 409 IDEMPOTENCY_CONFLICT；非法 plan_delta 首次及重放均 422 REJECTED。
  API 与 WMS worker 重启后，原请求依旧 200／422，完整响应及首次接收时间保持一致。
- 数据库核对：issued Evidence 为 APPLIED，非法 plan_delta Evidence 为 IGNORED；测试任务为 QUEUED、计划版本 0，Epoch 数量为 0。
  测试任务 `WMS-OPERATION-SMOKE-01a075d1-a17e-724b-b72f-8e40662a270e` 明确用于接口验收，无真实业务或物理完成含义。
- 真实 worker introspection 确认 `wms-fulfillment` 队列消费。远端尚未触发对真实 WMS 的 prepare 调用或成功计划应用；这两条正向事务路径已在隔离 PostgreSQL／真实 worker 自测中验证，后续由联合用例验收。
- 原始证据：`reports/operation-smoke-results.json`、`reports/operation-restart-replay.json`、`reports/operation-live-database.txt`、
  `reports/operation-live-runtime.json`、`reports/operation-live-docs-check.json`、`reports/operation-live-worker-queues.json`。

### face 长度升级验收

- 真实接口分别发送 10／11 个英文及中文字符：10 个字符通过 wire 校验，因测试任务不存在返回 409 REFERENCE_CONFLICT；11 个字符返回 422 INVALID_DATA。未创建任务或触发设备。
- 实际 OpenAPI 中 14 处 rack_face／target_face／arrival_face 属性均声明 maxLength 10；Swagger CSS／JS 仍返回 200。
- 升级后重放原 issued 和非法 plan_delta，完整响应与原始时间戳分别保持 200／422；原现场实例 10 个容器 ID 未变化。
- 本轮证据：`reports/face10-quality.log`、`reports/face10-heavy-clean.log`、`reports/face10-postgresql-fixed.log`、
  `reports/face10-live-database.txt`、`reports/face10-live-replay.json`、`reports/face10-live-openapi.json`、`reports/face10-live-runtime.json`、`reports/face10-face10-smoke-results.json`。

### R2-B2 生命周期围栏升级

- 已部署宿主 PickingTask 生命周期围栏。PREPARING／EXECUTING、计划 blocker 或 picking-owned 未闭合 WmsConfirmation 会阻止停用与新准入；确认完成本身不会解除仍在等待计划的任务围栏。
- 真实 PostgreSQL 回归证明完成 prepare 后停用仍拒绝，以及并发停用等待任务绑定提交后读取新状态并保留 Epoch；四文件 HEAVY 共 15 passed。
- 仅重建四个应用进程，无数据库迁移；六容器 healthy，plugins=[]。原数据库／Redis 和旧现场实例容器 ID 均未变化。
- 线上原 issued／非法 plan_delta 完整重放响应与时间戳不变，Transport 任务／调试运行查询 HTTP 200；新增八类负载摘要 SQL 实际执行通过。线上空摘要只证明数据库路径可用，拒绝行为由 PostgreSQL／并发回归证明。
- 原始证据：`reports/r2b2-quality.log`、`reports/r2b2-heavy.log`、`reports/r2b2-live-replay.json`、`reports/r2b2-live-transport.jsonl`、`reports/r2b2-live-workload.json`、`reports/r2b2-live-runtime.json`。
- R2-B2 整体尚未完成：人工插件仍缺 START builder、固定 handler 和后继装配。实例配置的 WMS 地址为 `http://10.24.199.217:8284`；对 `/api/v1/wes/decisions` 的 OPTIONS 探测返回 404，不能据此证明 POST 合同可用或不可用。真实 prepare 联调还需核实实际路径、认证及可用任务／工作线身份。

## 联合验收

主合同为 [WMS Outbound Picking](../contracts/wms-outbound-picking-task-integration-requirements.md)，
联合用例见 [J01–J11](outbound-picking-plan-delta-joint-freeze.md)。按真实联调反馈收敛合同；未向 WMS 发送消息，未声称双方已接受。
