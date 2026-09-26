# ECS_TEST 来源设备调试默认值设计

**状态：已批准（替换同日 WorkLine 双模式草稿设计）**

**日期：2026-09-25**

## 1. 目标与边界

用户在联调界面选择一台 ECS_TEST 来源设备时，自动带出该设备常用的目标设备、`task_type` 和固定 `params`，便于检查、调整后快速应用。默认值只负责预填；保存默认值、应用到 WorkLine 和执行是三个独立动作。用户临时修改表单并执行，不会自动覆盖设备默认值。

本设计只交付 ECS_TEST 来源设备的默认值读写。Transport 自动联调继续使用现有 `CreateTransportDebugRun` 表单和合同默认值；`rack_id`、`face_groups`、料箱编码与槽位是每次运行的具体对象，由用户当次填写。不新增 Transport 草稿、工作线级默认值或统一的跨模式草稿模型。

ECS_TEST 的生效合同仍是 `WorkLine.runtime_config_json.ecs_test_rules`。Device 默认值不参与事件处理、START、命令派发或结果闭合；必须由用户通过现有 WorkLine 配置入口应用，并按现有要求停线后 START。本设计不修改 `_process_event()`、`_start_ecs_test()`、`parse_ecs_test_rules()` 和 Transport `create_run()` 的执行语义。

**执行不变量：**运行时、WorkLine START 和 ECS 命令创建都不得读取 `Device.ecs_test_default_json`。只有用户显式应用后形成的 WorkLine `ecs_test_rules` 参与运行；修改或清除设备默认值不会改变已生效的规则和在途命令。

## 2. 归属与字段必要性

一条 ECS_TEST 规则以 `source_device_code` 匹配真实事件，再向 `target_device_code` 发送固定动作。同一来源在一条 WorkLine 上最多有一条生效规则。用户按来源设备选择常用参数，因此默认值归属于这台来源 Device；目标设备只是默认值的一部分，不是该默认值的所有者。

现有 `Device.diagnostic_profile` 用于诊断分类偏好和展示策略，不能承载设备命令参数；`WorkLine.runtime_config_json` 是正式运行配置，保存编辑态默认值会与整体替换及 WorkLine 版本发生耦合。因此仅给 Device 增加一个可空 JSON 字段 `ecs_test_default_json`，保存 `target_device_code`、`task_type`、`params`。不保存可从设备身份得到的 `source_device_code`，不新增表、状态或通用 `debug_defaults` 容器。该字段需要一条 schema migration。

`ecs_test_default_json` 不加入通用 `DeviceCreate`、`DeviceUpdate`；只有专用端点可写。普通设备更新不会替换它。设备即使处于活动线，仍允许编辑默认值，因为编辑不会改变当前生效规则。设备被改挂到另一条 WorkLine 后，默认值仍跟随设备；其目标设备可能不再属于同一条线，应用时须按当前拓扑校验，不自动改写目标。

## 3. API 与数据合同

以下为本设计拟定的 API 合同；GET 沿用现有 `biz:device:detail` 权限，PUT 沿用 `biz:device:update` 权限。

```text
GET /devices/{device_code}/ecs-test-default
PUT /devices/{device_code}/ecs-test-default
```

路径用 `device_code`（联调界面选设备时天然拿到的标识），不用内部数字 `id`，避免前端多一次 id 查找。`device_code` 未命中任何设备时返回 404。

`GET` 在设备存在但未保存默认值时返回 `default: null`；设备不存在时返回 404。`PUT` 的 `default` 为完整对象时替换该设备默认值，为 `null` 时清除。请求不得携带 `source_device_code` 或额外字段。保存时从当前设备生成完整单条规则，交给现有 `parse_ecs_test_rules({"ecs_test_rules": [rule]})` 做结构校验；不在保存阶段检查目标设备当前归属、在线状态或能力，这些仍由应用与 START 检查。外部结构错误返回 4xx，不写入部分对象。

`PUT` 必须显式提供 `default`：`{}` 不能表示清除；非空默认值必须显式提供 `params`，空对象 `{}` 可以是合法参数。缺少这两个必填字段时返回 422，不自动填入 `null` 或空参数。

请求示例（`params` 的具体二级字段是**讨论示例，非设备合同**，须以获批设备附录为准）：

```http
PUT /devices/STATION_SCAN1/ecs-test-default
Content-Type: application/json

{
  "default": {
    "target_device_code": "STATION_SCAN1",
    "task_type": "MOVE_FORWARD",
    "params": {"source": {"location_id": "STATION_SCAN1"}}
  }
}
```

成功响应示例：

```json
{
  "code": "1000",
  "message": "操作成功",
  "data": {
    "device_id": 7,
    "device_code": "STATION_SCAN1",
    "device_version": 18,
    "default": {
      "target_device_code": "STATION_SCAN1",
      "task_type": "MOVE_FORWARD",
      "params": {"source": {"location_id": "STATION_SCAN1"}}
    }
  },
  "timestamp": "2026-09-25T14:00:00Z"
}
```

未保存时，`GET` 的 `data.default` 固定为 `null`；清除请求为 `{"default": null}`，成功响应也返回 `default: null`。`PUT` 不要求客户端提交 Device `version`：同一设备默认值后保存者覆盖先保存者。服务端仍在设备行锁下写入、推进已有 Device 版本并返回 `device_version`，避免与普通设备更新的版本机制脱节。事务提交后失效该设备的相关缓存。若普通设备配置页面随后提交旧版本，应沿用既有冲突提示并刷新设备数据，不绕过乐观锁。

未保存时的读取示例：

```http
GET /devices/STATION_SCAN1/ecs-test-default
```

```json
{
  "code": "1000",
  "message": "操作成功",
  "data": {
    "device_id": 7,
    "device_code": "STATION_SCAN1",
    "device_version": 18,
    "default": null
  },
  "timestamp": "2026-09-25T14:00:00Z"
}
```

显式清除示例：

```http
PUT /devices/STATION_SCAN1/ecs-test-default
Content-Type: application/json

{"default": null}
```

```json
{
  "code": "1000",
  "message": "操作成功",
  "data": {
    "device_id": 7,
    "device_code": "STATION_SCAN1",
    "device_version": 19,
    "default": null
  },
  "timestamp": "2026-09-25T14:00:00Z"
}
```

## 4. 预填与应用

联调界面选择来源设备后读取默认值。无默认值时显示空表单；有默认值时预填目标设备、`task_type` 和 `params`。用户编辑后可以：

- **保存为该设备默认值**：调用专用 `PUT`，不触发命令，也不修改 WorkLine 生效配置。
- **应用并启动 ECS_TEST**：读取当前 WorkLine 的完整生效规则集，以所选来源设备的表单规则替换同来源规则，向用户展示最终规则集；用户确认后按既有 `PUT /work_lines/{id}` 更新 `run_mode` 与 `runtime_config_json.ecs_test_rules`，停线后调用现有 START。未选来源的既有规则不得因这次编辑意外丢失。保存默认值不是应用的前提，应用也不反写默认值。

联调界面用所选设备当前的 `device_code` 填充 `source_device_code`；现有 WorkLine 配置入口仍接收完整规则，并在 START 时校验其来源与目标，不新增一个声称能验证前端选择身份的接口。应用仍需满足现有 WorkLine 版本、停线、设备归属及 START 校验。目标设备搬走、停用或合同不匹配时，现有流程拒绝 START；默认值仍保留供用户修订，不绕过校验。当前已运行的 ECS_TEST 不因默认值变化而改变固定规则。

Transport 联调界面继续按现有 `CreateTransportDebugRunRequest` 提交 `{API_PATH}/v1/transport/debug-runs`。本设计不提供 Transport 默认值端点，不持久化本次运行的货架和料箱选择。

## 5. 实施范围与验收

实施范围限定为 Device 字段及 migration、专用 Repository/Service/API 读写、缓存失效和对应测试；WorkLine 与设备执行路径无需修改。API 权限按现有设备配置权限核实，并验证无权限用户不能读写默认值。

FAST 验证：设备存在/不存在、无默认值、保存与清除、拒绝多余字段和非法规则、按设备隔离、活动线仍可保存、保存不调用 START 或命令服务。真实 PostgreSQL 集成验证：同一设备并发保存最终只有完整的一份默认值、版本正确推进；保存默认值不改变 WorkLine 的 `ecs_test_rules` 或在途命令。应用路径复用现有 ECS_TEST 聚焦测试；只在实际修改其代码或合同后刷新相应回归。新增生产模块及 migration 按 `heavy-test-impact.toml` 建立精确映射，并在干净临时库验证迁移链。

代码验证、真实 ECS Result 与现场物理验收分别记录；默认值 API 的成功响应只证明参数已保存，不证明配置已应用或设备已执行。
