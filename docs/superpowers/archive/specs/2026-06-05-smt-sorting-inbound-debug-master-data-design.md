# SMT 分拣入库 dev/test 调试主数据设计

## 背景

仓库现有 `scripts/data/sync_test_workline_devices.py` 已为 `rough_sorter` 粗分机准备 dev/test 调试主数据，包含 `WL-ROUGH-SORTER-TEST` 工作线、三台设备和一个货架停靠位。该脚本不仅用于本地开发，也会在 Jenkins testing 部署中执行，因此本设计的目标环境明确为 `APP_ENV=dev` 和 `APP_ENV=test`。

`SMT_SORTING_INBOUND` 分拣入库插件已注册，并在插件 manifest 中声明 P0 必需设备角色，但当前没有同等级的脚本入口生成可直接用于 dev/test 联调的工作线与设备。

这会让分拣入库开发调试依赖测试 fixture 或人工建表数据，容易出现角色缺失、能力声明不一致、Mock ECS 地址不一致和配置预检失败。

## 目标

1. 为 `SMT_SORTING_INBOUND` 生成一套幂等的 dev/test 调试主数据。
2. 复用现有 WorkLine/Device 同步入口，保持粗分机 seed 行为不变。
3. 让生成后的分拣入库工作线能通过插件角色、命令能力、事件能力和通信配置预检。
4. 保护已有现场调试设备的通信配置和运行态，不因重复同步被默认值覆盖。
5. 保持现有粗分机返回契约兼容，同时新增按工作线分组的同步摘要。

## 非目标

- 不初始化完整资源域事实、货架实例、目标料箱、料格占用或 active rack 投影。
- 不补齐 WMS/RCS/CTU 全链路主数据。
- 不修改 `rough_sorter` 现有 line_code、device_code、运行模式或 Jenkins 依赖行为。
- 不新增生产初始化 SQL，不在 `APP_ENV=prod` 下启用 `SIMULATION` 调试主数据。
- 不为 SMT 分拣入库生成 `WorklineRackPosition`；完整资源与货架投影链路已由 `TODOS.md` 的 SMT CTU/WMS/NG 对账后续项追踪。

## 主数据约定

新增 dev/test 调试工作线：

| 字段 | 值 |
| --- | --- |
| line_code | `WL-SMT-SORTING-INBOUND-TEST` |
| line_name | `测试 SMT 分拣入库作业线` |
| line_type | `AUTO` |
| plugin_key | `SMT_SORTING_INBOUND` |
| contract_version | `2026-06-01.p0` |
| run_mode | `SIMULATION` |
| runtime_status | `STOPPED` |
| is_active | `True` |

新增 dev/test 调试设备：

| device_code | device_role | 能力声明 |
| --- | --- | --- |
| `SORT-SOURCE-ARM-01` | `SORTING_SOURCE_ARM` | command: `SORTING_SOURCE_PICK` |
| `SORT-TARGET-ARM-01` | `SORTING_TARGET_ARM` | command: `SORTING_TARGET_PLACE`, `SORTING_NG_PLACE` |
| `SORT-SCAN-PLATFORM-01` | `SORTING_SCAN_PLATFORM` | event: `WORKING_BIN_SCAN` |
| `SORT-NG-STATION-01` | `SORTING_NG_STATION` | 无命令/事件能力要求 |
| `SORT-WORKSTATION-01` | `SORTING_WORKSTATION` | event: `SORTING_SESSION_COMPLETE_REQUESTED` |

默认通信配置：

- `host`: 来自 `MOCK_ECS_URL`、`MOCK_ECS_HOST` 或默认 `mock_ecs`。
- `port`: 来自 `MOCK_ECS_URL`、`MOCK_ECS_PORT` 或默认 `8010`。
- `protocol`: `HTTP`。
- `callback_path`: `/api/v1/device/command`。
- `capabilities_json.status_path`: `/api/v1/device/status`。
- `timeout`: `300000` 毫秒。
- `vendor_type`: `SANDBOX`。

## 同步行为

同步入口继续使用 `scripts/data/sync_test_workline_devices.py`，在现有粗分机同步后追加 SMT 分拣入库同步。

同步策略：

- 在脚本内抽取轻量 `TestWorklineSeed` 和通用 upsert helper，粗分机与 SMT 复用工作线和设备同步逻辑；不引入新的 Service、Repository、迁移或独立脚本。
- 按 `line_code` 幂等 upsert 工作线。
- 按 `device_code` 幂等 upsert 设备。
- 新建设备时写入默认 Mock ECS 通信配置和初始运行态。
- 已存在设备时刷新主数据字段、角色、能力声明、排序、所属工作线和诊断配置。
- 已存在设备时不覆盖 `host`、`port`、`protocol`、`callback_path`、`device_status`、`current_command_id`、`error_code`、`maintenance_mode` 等调试现场可能变更的字段。
- 粗分机继续同步既有货架停靠位；SMT 本次不生成货架停靠位，SMT 的 rack position 同步结果为空。
- 同步结果摘要需要区分粗分机与 SMT 分拣入库，便于 Jenkins 和开发者判断实际变更。

返回契约：

- 保留旧 top-level 字段兼容性：`workline`、`devices`、`rack_positions`、`summary` 继续代表粗分机兼容视图。
- 新增按工作线分组字段，建议命名为 `worklines_by_code`、`devices_by_workline`、`rack_positions_by_workline`、`summary_by_workline`。
- `summary.total_worklines` 和 `summary.total_devices` 继续表示当前库内总量；测试需要显式覆盖粗分机 + SMT 的总量变化。

## 验收标准

1. 空测试库运行同步脚本后，同时生成粗分机既有主数据和 1 条 SMT 分拣入库 WorkLine、6 台 SMT 设备。
2. 生成的 SMT WorkLine 使用 `SMT_SORTING_INBOUND` 和 `2026-06-01.p0`，`run_mode=SIMULATION`，`runtime_status=STOPPED`，`is_active=True`。
3. 6 台 SMT 设备满足插件 manifest 的全部必需角色。
4. 命令目标角色设备具备对应 `supports_command_types`。
5. 事件来源角色设备具备对应 `supports_event_types`。
6. SMT 配置预检不因角色、命令能力、事件能力或通信配置失败。
7. 重复运行同步脚本不重复插入工作线、设备或粗分机货架停靠位。
8. 已有设备通信配置或运行态被人工改成现场联调值后，重复同步不会覆盖这些字段。
9. 旧 top-level 返回字段继续代表粗分机兼容视图；新增分组字段能区分粗分机与 SMT 分拣入库。
10. 粗分机拓扑、通信配置和货架停靠位测试继续通过，且测试断言不再依赖“全库只有粗分机 seed”。

## 测试计划

重点扩展 `tests/scripts/test_sync_test_workline_devices.py`：

- 新增测试：创建 SMT 分拣入库工作线和 6 台设备。
- 新增测试：SMT 分拣入库配置预检通过。
- 新增测试：重复同步保持幂等。
- 新增测试：已存在 SMT 设备时保留通信配置和运行态。
- 新增测试：返回结果保留粗分机 top-level 兼容字段，并新增按工作线分组的 SMT 明细。
- 改造现有粗分机 seed 测试：粗分机拓扑断言按 `TEST_ROUGH_SORTER_LINE_CODE` 对应的 `workline_id` 过滤，不再直接读取全库设备并假设只有 3 台。
- 改造现有全局数量断言：空库同步后的 WorkLine/Device 总数应包含粗分机和 SMT；已有人工 WorkLine 场景应额外计入预置人工线。

覆盖图：

```text
sync_test_workline_devices()
  ├── rough sorter 工作线/设备/货架 upsert
  │   ├── 既有拓扑按 workline_id 过滤验证
  │   └── 旧 top-level 返回契约验证
  ├── SMT 工作线 upsert
  │   ├── create/update/unchanged 状态
  │   └── SIMULATION + dev/test 配置预检
  ├── SMT 6 设备 upsert
  │   ├── 角色、命令能力、事件能力
  │   └── Mock ECS 默认通信配置
  ├── 已有设备重复同步
  │   ├── 刷新主数据字段
  │   └── 保留通信配置与运行态
  └── summary
      ├── 旧粗分机兼容视图
      └── 新增按工作线分组明细
```

建议验证命令：

```bash
uv run pytest tests/scripts/test_sync_test_workline_devices.py tests/test_workline_service_plugin_validation.py -q
uv run ruff check scripts/data/sync_test_workline_devices.py tests/scripts/test_sync_test_workline_devices.py
```

## 风险与处理

| 风险 | 处理 |
| --- | --- |
| `SIMULATION` 在非 dev/test 环境被配置预检拦截 | 本脚本定位为 dev/test 调试 seed；Jenkins testing 环境必须保持 `APP_ENV=test` |
| 分拣入库 P0 还依赖资源投影才能跑完整业务流 | 本设计只解决工作线与设备主数据，不承诺完整资源闭环 |
| 与粗分机同步摘要结构兼容性变化 | 保留旧 top-level 粗分机视图，并新增按工作线分组明细而不是删除旧字段 |
| 现有粗分机测试因全库数量变化失败 | 粗分机拓扑断言按工作线过滤，全局总量断言显式包含 SMT |
| 未来设备编码改成现场编码 | 已存在设备的通信配置不覆盖，编码变更另走主数据迁移或配置导入 |

## 影响范围

预计修改范围：

- `scripts/data/sync_test_workline_devices.py`
- `tests/scripts/test_sync_test_workline_devices.py`

不需要修改 API 层、Service 层、Repository 层或数据库迁移。
