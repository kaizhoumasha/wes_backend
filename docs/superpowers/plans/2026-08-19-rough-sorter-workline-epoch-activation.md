# 粗分机 WorkLine Epoch 激活与多 Endpoint 派发实施计划

**状态：** 工程评审通过 — 等待实施授权与执行基线冻结

**目标：** 提供唯一生产 START 入口，原子冻结 WorkLine 运行代际，并让每个可派发物理 Device 按其 Epoch Endpoint 派发。

**范围：** 后端四个顺序工程包和前端一个独立工程包；每包必须形成可独立评审、可独立验证的绿色快照。

**直接替换：** 系统未发布，不保留旧 START、旧 admission/probe 字段、别名、wrapper、fallback 或旧数据迁移。

**真源定位：** 本计划是 Phase 8 已有仓内闭环之上的增量实施真源，只负责 WorkLine Epoch 激活、多 Endpoint 派发及其前端入口。
`2026-08-03-rough-sorter-plugin-convergence.md` 继续保存 Phase 8 初始收敛交付、仓内验收和外部阻塞状态，不再指导本计划范围内的新增实现；
本计划不新增 Phase 8A/8B 等正式阶段，也不改写旧计划的历史交付事实。

## 1. 架构裁决

### 1.1 基础能力与业务能力

基础层负责：

- `Device.endpoint_base_url` 主数据及 canonical 局域网 HTTP origin 校验；
- 按冻结 Endpoint 选择并复用 ECS Adapter；
- 通用 WorkLine 运行门禁、START 协调、Epoch 原子写入和 READY 投影；
- 保存并摘要不可变配置快照，但不解释快照中的业务字段。

粗分机业务层负责：

- 解析 `WorkLine.config["rough_sorter"]`；
- 校验三个业务角色、四类位置和部署合同证据；
- 把业务配置翻译为通用激活计划；
- 通过公开 START 建立 Epoch 后执行粗分机业务闭环。

禁止把粗分机角色、供应商版本、时钟、重传、证据策略或位置规则写入通用基础模型。禁止基础测试使用粗分机业务规则证明自身正确。

### 1.2 Endpoint 所有权

- 每个可派发物理 Device 必须对应且仅对应一个 `endpoint_base_url`。
- 纯上报、人工或逻辑 Device 允许 Endpoint 为空。
- 多个 Device 可以共享一个 Endpoint，也可以分别使用不同 Endpoint。
- Endpoint 属于 Device 静态主数据，不属于插件配置，不新增 `DeviceEndpoint` 实体、Endpoint 注册表、picker 或 host/port 拆分字段。
- 同一插件可以被多条同规格 WorkLine 使用；每条 WorkLine 读取自己的 Device 与业务配置，不共享可变业务状态。

### 1.3 Epoch 冻结规则

`LineRunEpochDeviceBinding` 只保存派发不变量：

| 类别 | 字段 |
| --- | --- |
| 父代际 | `line_run_epoch_id`；WorkLine 归属由父 Epoch 确定 |
| 设备身份 | `device_id`、`device_code`、`device_role` |
| 目标 | canonical `endpoint_base_url` |
| 合同 | `contract_key`、`contract_version` |
| 派发策略 | `status_max_age_ms`、`command_timeout_ms` |

完整规范化业务配置保存为 `LineRunEpoch.configuration_snapshot_json`。其中可以包含 ECS/网关/设备/固件版本、时间来源、允许时钟偏差、
重传窗口、证据保留期、角色合同和位置绑定。`configuration_digest` 由插件身份、运行模式和 canonical JSON 快照共同生成；
`topology_digest` 由创建前即可形成的稳定 topology input 生成：Device 部分只包含 `device_code`、`device_role`、canonical
`endpoint_base_url`、合同身份和派发策略，Position 部分只包含 `position_role`、`location_id`、`location_type`，两部分分别按完整字段排序。
数据库生成的 `line_run_epoch_id`、`device_id`、binding 主键、审计字段和时间戳不进入摘要；父 Epoch 已拥有该摘要，不能再作为摘要输入形成
“先有 Epoch ID 才能算 digest、先有 digest 才能插入 Epoch”的构造闭环。相同稳定 topology input 在不同 Epoch 中必须产生相同摘要，
`device_code`、`device_role`、Endpoint、合同、派发策略或位置任一变化必须改变摘要。

基础层只验证快照是可 canonicalize 的 JSON 对象并负责持久化、摘要和不可变性，不读取 `rough_sorter` 字段。

### 1.4 START 与 replay

唯一生产入口：

`POST /api/v1/workline/operations/worklines/{workline_id}/start`

请求只包含调用方稳定幂等键 `request_id`，去空白后长度为 `1..100`，并直接作为全局唯一 `epoch_code` 持久化；不新增
`start_request_id` 字段或幂等表。首次请求按以下锁序执行：

```text
transaction-scoped request identity advisory lock
  -> existing Epoch SELECT ... FOR UPDATE
  -> if absent: WorkLine row lock excluding soft-deleted rows
  -> generic runtime gates
  -> one Device SELECT ... FOR UPDATE
  -> business plan translation in memory
  -> Epoch + snapshot + bindings + READY + eligible parked Outbox release
  -> API Unit of Work commits once
  -> if rows released: post-commit payloadless Outbox wakeup
```

基础 repository 必须先按规范化 `request_id` 获取 PostgreSQL 事务级 advisory lock，再按全局 `epoch_code` 查询并锁定历史 Epoch。命中后
直接通过 `epoch.workline_id` 分类，不读取或锁定 WorkLine，因此 WorkLine 后续软删除不破坏 replay。只有不存在该 Epoch 的首次请求才使用
现有排除软删除行的 WorkLine 锁查询；已软删除 WorkLine 对首次请求返回 not found。hash 碰撞只允许增加串行，不能改变结果；锁随事务自动
释放。事务内禁止 HTTP/ECS、Adapter 构造、队列发送、插件发现、文件读取和逐角色 Device 查询。

首次 START 只复用并收窄现有 `SystemOutboxRepository.release_parked_after_workline_start()` 作为唯一 repository owner：查询必须同时限定
`status=RETRY_WAIT`、`workline_id`/`blocked_workline_id` 属于当前 WorkLine、`blocked_reason=WORKLINE_STOPPED_WAITING_START`，并要求
`blocked_by_runtime_hold_id`、`blocked_by_reconciliation_session_id` 均为空。不得调用更宽的 `release_blocked_by_workline()`，不得释放仍受
active runtime hold、pending reconciliation 或其他 blocked reason 约束的记录。service 返回
`released_outbox_count`，API 在 Unit of Work 成功 commit 后，仅当计数大于零时通过 app-state queue port 发送一次不携带业务 payload 的
SYSTEM Outbox wakeup。wakeup 失败只记录告警并依赖既有 Beat 扫描，不回滚已提交的 Epoch/READY/Outbox；commit 失败时不得发送 wakeup。

replay 必须在业务 builder 和当前运行门禁之前完成：

- 同 WorkLine、同 `request_id`：直接返回已持久化 Epoch，`created=false`；不查询或锁定 Device，不读取当前配置，不再次 READY，
  不再次释放 outbox。即使主数据已变化或 Epoch 已关闭，也只返回历史结果，不重新打开。
- 不同 WorkLine 使用同一全局 `request_id`：返回 `IDEMPOTENCY_CONFLICT`，不调用业务 builder。
- 新配置需要生效：调用方必须使用新的 `request_id`。

START 成功只表示通用门禁通过、Epoch 已冻结且 WorkLine 投影为 READY；不表示设备 `AUTO/IDLE`、PLC 互锁、货架到位或已开始分拣。
设备可派发性仍由 `DeviceCommand` 派发前的持久 readiness owner 校验。

成功响应沿用现有 `code="1000"` 包络，`data` 字段固定为：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `line_run_epoch_id` | integer | 本次创建或 replay 的历史 Epoch ID |
| `epoch_code` | string | 规范化后的 `request_id` |
| `workline_id` | integer | Epoch 所属 WorkLine |
| `plugin_key`、`plugin_version`、`flow_mode` | string | Epoch 冻结的插件身份 |
| `epoch_status` | `ACTIVE \| CLOSED` | replay Epoch 当前持久状态 |
| `epoch_started_at`、`epoch_closed_at` | ISO 8601 datetime / null | replay Epoch 的历史时间字段 |
| `current_workline_runtime_status` | string / null | 响应时 WorkLine 当前投影；可能属于更新 Epoch，不解释为 replay Epoch 状态 |
| `created` | boolean | 首次创建为 `true`，replay 为 `false` |

首次创建的 `current_workline_runtime_status` 必须是本事务投影的 `READY`。replay 只读当前投影；投影缺失时返回 `null`，不补建、不改写，
并始终返回原 Epoch 的身份、状态和时间字段。

### 1.5 错误合同

| 场景 | HTTP | 稳定 reason |
| --- | --- | --- |
| WorkLine 不存在 | 404 | 现有 not-found 包络 |
| 当前状态不允许首次 START | 409 | `INVALID_STATE` |
| 配置或必需 Device 无效 | 409 | `CONFIGURATION_INVALID` |
| `request_id` 已属于其他 WorkLine | 409 | `IDEMPOTENCY_CONFLICT` |
| START service 未安装 | 503 | 现有 service-unavailable 包络 |

未知数据库或基础设施异常继续进入统一 500，不伪装成业务拒绝。

## 2. 配置合同

### 2.1 Device Endpoint

唯一纯函数 `src/app/device/endpoint.py::validate_device_endpoint_base_url()` 同时负责校验和 canonicalization：

- 只接受无凭据、无业务路径、无 query/fragment 的局域网 `http` origin；
- 主机名小写，IP 使用规范形式，IPv6 正确保留方括号；
- 显式端口必须可解析且处于 `1..65535`；默认端口 `80` 归一为省略；
- 去除末尾 `/`，返回重建后的 canonical origin。

Device API、Epoch 冻结和 runtime provider 全部复用该函数，不保留旧 `validate_ecs_base_url()` 或第二套 URL 解析。环境变量只继续提供
DeviceCommand timeout/queue，不再提供命令目标 Endpoint。

### 2.2 WorkLine 粗分机配置

`WorkLine.config` 继续作为现有扩展字段，不新增配置表、动态 schema registry 或配置 DSL。业务子树固定为：

```json
{
  "rough_sorter": {
    "device_contracts": {
      "MEASUREMENT_DEVICE": {},
      "TRANSFER_DEVICE": {},
      "PLACEMENT_DEVICE": {}
    },
    "position_bindings": {
      "MEASUREMENT_POSITION": "...",
      "PIPELINE_INLET": "...",
      "PIPELINE_OUTLET": "...",
      "NG_POSITION": "..."
    }
  }
}
```

真实字段闭集以 `docs/contracts/device-annexes/rough-sorter-device-contract.md` 为准。业务 parser 拒绝缺失键、额外键、空白部署证据、重复
Device/位置和不完整角色。Endpoint 不进入此配置。

## 3. 前端产品入口

不新增 Endpoint 页面或 START 专用页面，复用现有管理入口：

- Device 页面：增加可空 `endpoint_base_url` 文本字段；空字符串在生成 schema 校验前规范化为 `null`。
- WorkLine 页面：按现有 Users/API Applications 页面模式，由 `WorkLineListPage.vue` 通过 `extra-dialogs` 注入资源局部结构化配置对话框，
  `pageConfig` 的“粗分机配置”row action 只负责打开该对话框。对话框复用 WorkLine update API 和乐观锁 `version`，只修改
  `config.rough_sorter` 并保留其他 config sibling；不提供原始 JSON 编辑器。
- WorkLine 行操作：静态启用且当前用户拥有 `biz:workline:start` 时显示 START。

START 操作每次明确的新意图只生成一个 `request_id`。超时、断网或响应丢失属于 delivery unknown，必须由 WorkLine-local raw
`sessionStorage` wrapper 按 WorkLine 保存；不得复用带 TTL 的通用缓存，也不得因时间经过而推断请求未接纳。刷新或点击“重试”继续复用；
只有收到明确成功、确定性业务拒绝或 tab session 结束时才清理。同一 WorkLine 请求进行中禁止重复点击。成功结果展示历史 Epoch、
`created` 和明确标注的 `current_workline_runtime_status`。

## 4. 五个顺序工程包

每包遵循：聚焦实现 → 相称验证 → 只读 Review → 接收并验证意见 → 修复后 fresh full Review。Critical/Important 清零前不得进入下一包。
Commit、Push 仍是独立授权；未获 Commit 授权时只形成绿色工作树快照，不扩大权限。

### 工程包 1：通用 Epoch 原子激活

**目标：** 基础层可独立创建包含 canonical 配置快照的完整 Epoch，不依赖粗分机。

**生产边界：**

- `LineRunEpoch` 增加非空不可变 `configuration_snapshot_json`；
- `configuration_digest` 使用唯一 canonical JSON owner；
- repository/service 在外部已锁 WorkLine 的前提下原子写入 Epoch、Device bindings 和 Position bindings；
- 删除分步生产写 API，保留查询能力；不导出第二个事务 owner。

**迁移：** 使用仓库命令生成随机 Alembic revision。系统未发布，不迁移旧数据；执行前若目标库存在 Epoch 数据，停止并要求清理。

**测试所有权：**

- FAST 使用无业务含义 JSON 验证键顺序稳定、值变化改变 digest、输入对象后续变化不影响持久快照；
- FAST 验证 topology input 不含数据库 ID，相同 topology 跨 Epoch 摘要相同，`device_code`、`device_role`、合同、策略或位置任一变化均改变摘要；
- FAST 证明通用 binding 不出现粗分机供应商字段；
- PostgreSQL owner 验证完整写入、约束、rollback 和并发不变量；
- HEAVY mapping 精确覆盖 Epoch model/repository/service 和 migration。

**退出：** 基础能力可独立测试，未引入粗分机 import，Review 为零意见。

### 工程包 2：唯一 START 与旧 admission 直接替换

**目标：** 用通用 `WorkLineStartService` 直接替换 sandbox START 和 ECS 即时探测路径。

**生产边界：**

- API 只解析 schema、权限和 app-state port，通过现有 Unit of Work commit 一次；
- service 负责 request identity advisory lock、existing request 分类、通用门禁、调用一个最小 plan-builder port、READY 投影和
  eligible parked Outbox 释放，并把 `released_outbox_count` 返回给 API；
- API 只在 Unit of Work commit 成功后按计数发送一次 payloadless Outbox wakeup；触发失败依赖 Beat，不改变 START 成功结果；
- 同线 replay 直接返回持久 Epoch；跨线冲突在 builder 前失败；
- 删除旧 admission service、sandbox schema/route、六个 probe/request/trace 字段及其 runtime/trace/diagnosis/reset 消费者；
- 在生产基础数据 seed 增加 `biz:workline:start`，继续保留静态 `/activate` 使用的 `biz:workline:activate`；
- 不保留 wrapper、别名、fallback 或失败审计新表。

**迁移：** 本包使用新的随机 Alembic revision 直接删除旧六列，不保留数据、回填或兼容读取。从工程包 1 的 migration head 升级到本包
head，并在干净临时 PostgreSQL 中验证仓库 base→本包 head；migration 与删除字段的生产路径映射到本包 PostgreSQL owner。

**测试所有权：**

- FAST 断言 replay 在 Device/config 改变、当前状态改变或 Epoch 关闭后仍不调用 builder、门禁、READY、outbox；
- FAST 断言首次 START 只释放 START-waiting Outbox，active hold/pending reconciliation 不释放；commit 失败不 wakeup，commit 成功按计数
  wakeup 一次，wakeup 失败保持已提交结果并依赖 Beat；
- FAST 断言 WorkLine 已软删除时仍只按历史 Epoch replay，不调用 WorkLine 查询/锁；
- ASGI 合同测试覆盖认证、权限、成功包络、精确响应字段、历史 CLOSED Epoch replay、软删除 WorkLine replay、当前投影为更新代际或缺失，
  以及稳定 reason；
- production seed 合同验证 START 权限存在且静态 activate 权限未被删除；刷新生产 SQL 的精确 `NONE` hash 和 selector 断言；
- PostgreSQL owner 覆盖 START→READY、整体 rollback、同线串行、软删除后的历史 replay，以及相同 `request_id` 不同 WorkLine 并发时在
  builder 前串行分类；
- 旧符号 absence、legacy matrix/ledger 和 HEAVY selector 同步闭合。

**退出：** 只有一个生产 START 合同，旧即时 ECS probe 无残留，Review 为零意见。

### 工程包 3：Device Endpoint 与多 Endpoint 派发

**目标：** 每条命令按 Epoch 冻结 Endpoint 派发，并复用进程内 transport。

**生产边界：**

- `Device` 增加可空 `endpoint_base_url`，Create/Update/Response 使用同一 validator；
- Epoch Device binding 增加非空 canonical Endpoint；
- DeviceCommand 首次 claim 事务同时取得不可变 binding，事务外选择 Adapter 并访问 ECS；
- provider 按 canonical origin 惰性复用一个 Adapter/transport；不同端口隔离；进程关闭时全部且只关闭一次；
- 当前低基数现场不实现 LRU、TTL、引用计数或动态淘汰。

**迁移：** 本包使用新的随机 Alembic revision 增加可空 Device Endpoint 和非空 binding Endpoint，不回改前两包 revision，不设置默认值或
回填。若目标库已有 Epoch/binding 数据，停止并由用户清理；从工程包 2 的 migration head 升级到本包 head，并在干净临时 PostgreSQL
验证仓库 base→本包 head。migration 必须与 Device/dispatch 生产路径共享精确 HEAVY owner。

**测试所有权：**

- FAST 覆盖空值、空白、公网、路径、凭据、query/fragment、非数字端口、`0`、`65536`、canonical 等价地址和 IPv6；
- Epoch digest FAST 增补 Endpoint 变化会改变摘要，并继续证明摘要不依赖数据库生成 ID；
- dispatch FAST 覆盖同 Endpoint 复用、不同 Endpoint 隔离、binding 缺失/非法时无 HTTP；
- `tests/runtime/device_command/test_composition.py` 主测 provider pool 的 Endpoint 复用与隔离、部分初始化失败清理和 shutdown 幂等；
- `tests/integration/test_celery_async_runtime.py` 只承接 provider pool 的 Celery child-local wiring 与初始化失败 rollback；
- `tests/integration/test_celery_async_runtime_postgresql.py` 使用真实 PostgreSQL、Redis、broker 和 prefork worker，证明父进程对象不进入 child、
  每个 child 内同 Endpoint 只复用一个 transport、不同 Endpoint/端口隔离，并在 `worker_process_shutdown` 全部且只关闭一次；
- `tests/e2e/device_command/test_device_command_production_wiring.py` 承接从冻结 binding 到实际 Endpoint 的生产派发路径；上述 owner 与 schema
  constraint 一起进入精确 HEAVY mapping，不新建第二套 worker harness；
- `src/app/device/endpoint.py` 必须有精确 HEAVY mapping 和 selector 合同，不使用宽泛 `device/**`。

**退出：** 基础 runtime 不再依赖单一 `ECS_BASE_URL` 选择目标，插件和命令 payload 均不携带 Endpoint，Review 为零意见。

### 工程包 4：粗分机配置翻译与业务 E2E

**目标：** 业务层把每条 WorkLine 的配置和 Device 翻译为通用激活计划，并通过公开 START 建立业务 Epoch。

**生产边界：**

- builder 使用现有一次性 `get_by_work_line_id_for_update()` 取得全部 Device，在内存中按角色分类；不新增逐角色 repository API；
- parser 生成完整规范化业务 snapshot，Endpoint 只从 required-role Device 取得；
- `deployment/` 只负责组合，基础包不 import 粗分机；
- `business-loop-seed.sql` 只建立静态主数据和可信 STOPPED 投影，不再 INSERT Epoch 或 bindings；
- 三个业务场景在首次 SCAN 前调用受保护公开 START。

**测试所有权：**

- builder FAST 断言 Device repository 恰好调用一次、网络调用为零、角色和位置闭集、配置 snapshot 完整；
- 两条同规格 WorkLine 使用不同或共享 Endpoint 均保持配置和 Device 隔离；
- 无关纯上报、人工或逻辑 Device 的空 Endpoint 不阻塞；required-role 空 Endpoint fail closed；
- 插件 E2E 证明公开 START、真实认证权限、三 Device/四 Position bindings 和业务闭环，不替代基础原子性测试；
- Mock 结果不等于供应商、现场或业务验收。

**退出：** 后端合同、QUALITY、selector 选中的 HEAVY、迁移链和粗分机 E2E 均绑定同一可执行快照并通过；Review 为零意见。

### 工程包 5：前端 Device、WorkLine 配置与 START 操作

**前置门禁：** 后端四包完成且 Review/门禁通过；获得后端 Commit 授权；包含新 OpenAPI 和权限合同的后端 Commit 位于合同冻结脚本认可的
干净 `develop`。前端在独立仓库重新冻结 base/head/index/fingerprint 和 manifest，不复用后端集合。

**生产边界：**

- 合同冻结 manifest 包含 OpenAPI 生成物、`.contract-sync-record.json` 和实际权限生成物；
- Device 资源局部表单承接可空 Endpoint，不修改共享 CRUD；
- `WorkLineListPage.vue` 通过 `extra-dialogs` 组合资源局部粗分机配置对话框，`pageConfig` row action 注入打开回调；对话框复用
  WorkLine update/version，结构化编辑 `config.rough_sorter`、保留 sibling，并隔离多 WorkLine 表单状态；
- 不修改共享 `CrudFormDialog`、CRUD renderer 或动态字段注册机制；
- WorkLine row action 调用 START，通过资源局部、无 TTL 的 `sessionStorage` wrapper 按 WorkLine 保存 delivery-unknown `request_id`；
- 复用现有权限系统，不增加页面硬编码旁路。

**测试与验证：**

- 真实 `CrudFormDialog` 提交流程覆盖非空 Endpoint 清空后提交 `null`；
- 真实 WorkLine 配置对话框覆盖配置回显、update/version 提交、sibling 保留、重复/缺失绑定、多线隔离和启用后只读；
- START 覆盖超时后刷新/重试复用 ID、超过普通缓存 TTL 后仍复用、双击抑制、成功后新意图生成新 ID、`created=false` 和稳定错误展示；
- 权限覆盖有/无 `biz:workline:start` 和未静态启用场景；
- 运行 `pnpm test`、`pnpm lint`、`pnpm contract:test`、`pnpm contract:verify`、`pnpm generate:permissions`、
  `pnpm permission:verify`、同输入再次生成无差异和 `pnpm build`；
- 浏览器验证 Device、两条 WorkLine 配置、START、console 和 network。浏览器结果不证明 ECS 或业务验收。

**退出：** 前端独立 manifest 与验证闭合，Review 为零意见；Commit、Push 继续分别等待用户授权。

## 5. 测试所有权矩阵

| 行为 | 主要 owner | 不能替代它的证据 |
| --- | --- | --- |
| Endpoint canonicalization | Device foundation FAST | 浏览器输入成功 |
| Epoch snapshot/digest/atomicity | WorkLine foundation FAST + PostgreSQL | 粗分机 E2E |
| START replay/门禁/READY | WorkLine START FAST + PostgreSQL | API 200 或 Mock |
| Outbox START 释放与提交后 wakeup | WorkLine START FAST + PostgreSQL | READY 投影或下一次 Beat 恰好执行 |
| Endpoint provider 生命周期 | Device dispatch FAST + 真实 prefork HEAVY + 生产 wiring E2E | 单进程 mock 或插件 E2E |
| 粗分机配置翻译 | deployment/business FAST | 基础 Epoch 测试 |
| 粗分业务闭环 | 插件 E2E | DeviceCommand 基础测试 |
| 前端配置与 retry 交互 | 前端 unit + browser | 后端 ASGI 测试 |
| 供应商协议一致性 | ECS/网关供应商验收 | WES Mock |
| 现场物理闭环 | 联合验收 | 本地容器健康检查 |

## 6. 最终残留与验收

最终快照必须证明：

- 不存在 `DeviceEndpoint`、Endpoint registry、plugin Endpoint 配置、host/port 拆分或旧 validator；
- 通用 binding 不存在 `ecs_version`、`gateway_version`、`device_model`、`firmware_version`、时钟、重传或证据保留字段；
- 同线 replay 路径不调用 builder、Device 查询/锁、当前门禁、READY 或 outbox；
- 旧 sandbox START、旧 admission service 和旧六字段无残留；sandbox START 不再借用 update/activate 权限，静态 `/activate` 与
  `biz:workline:activate` 必须保留；
- `src/app/device/endpoint.py`、Epoch/START、migration、deployment 和生产 wiring 的 HEAVY mapping fail closed；
- 粗分机 fixture 不直接写 Epoch；多个同规格 WorkLine 配置互不污染；
- SRS、设备附录和联合验收文档统一区分静态启用、START→READY、设备 readiness、Mock、供应商和现场验收。

## 7. 实施门禁与非范围

### 7.1 实施门禁

工程评审通过只说明本计划没有未决设计问题，不自动授权生产代码、migration、Commit 或 Push。工程包 1 开始前必须同时满足：

- 用户明确授权实施，并决定如何处置当前 `develop` 上已有 staged/unstaged/untracked 现场；不得把既有变更混入本计划的代码快照；
- 从用户确认的 backend Commit 建立独立 feature branch；只有并行隔离或保留当前脏现场确有需要时才建立 worktree；
- 冻结 branch、完整 HEAD、index/worktree 指纹、单一 Alembic head、相关合同版本和无关 dirty 指纹；
- `npx gitnexus status` 后，对首包全部生产符号批量完成 upstream impact；HIGH/CRITICAL 影响链在首个生产补丁前取得范围确认；
- 为当前工程包生成生产符号、调用点、共享 helper 消费者、直接/间接测试、HEAVY mapping、migration 和验证命令的授权 manifest；
- migration 目标使用独占干净临时 PostgreSQL；任何目标库存在 Epoch/binding 数据时 fail closed，待用户明确清理后再继续。

工程包 5 继续受其独立前置门禁约束：后端四包完成、获得后端 Commit 授权、合同 Commit 进入前端冻结脚本认可的干净 `develop` 后，
才能在前端仓库冻结新的 base/head/index/fingerprint；不得复用本轮后端或历史前端证据。

### 7.2 NOT in scope

- 供应商私有路径、字段或 Adapter：继续由 ECS/网关拥有，WES 只实现批准的统一 wire；
- 真实供应商一致性、真实 WMS 联调和现场业务验收：它们不阻塞仓内实施，但继续阻塞最终业务验收结论；
- `DeviceEndpoint` 实体、Endpoint registry、picker、LRU/TTL、引用计数和动态淘汰：当前低基数现场没有触发条件；
- 通用 WorkLine 角色/拓扑绑定向导：继续保留在 `TODOS.md`，本计划前端只实现粗分机结构化配置；
- 旧数据迁移、兼容读写、别名、wrapper、fallback 或旧 START 保留：系统未发布，直接替换并允许清理开发/测试数据；
- 为计划正文、状态、路径或文案编写自动化测试：纯文档只做 `git diff --check`、引用、真源和一致性扫描。

本计划不保存完整实现代码、测试代码、逐文件 staging 脚本或固定 migration 文件名。每个工程包开始时根据真实现场生成授权 manifest。
