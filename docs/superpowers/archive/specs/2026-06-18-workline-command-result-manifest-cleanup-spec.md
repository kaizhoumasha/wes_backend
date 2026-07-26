# 清理 WorkLine Manifest 中误建模的 COMMAND_RESULT 事件

日期：2026-06-18
状态：Ready for implementation with preconditions

## 背景

WorkLine 第三方集成白皮书把设备回调分成两条入站协议：

- `/api/v1/callback/event`：设备主动事件，字段包含 `device_code`、`event_type`、`timestamp`、`data`。
- `/api/v1/callback/result`：设备命令执行结果，字段包含 `command_code`、`device_code`、`result`、`finish_time`、`data`、`error_detail`。

当前部分插件 manifest 把命令结果也写进 `events`，并标记为 `category: COMMAND_RESULT`。这会把 Result Callback 误表达成 Event Push 合同，影响供应商联调、前端沙箱模板和插件作者认知。

已验证运行时不依赖 manifest 中的 `_RESULT` event 路由命令结果：

- `plugin_base.py` 通过 `command_type` / `task_type` 与 `result` 路由 `@on_command(..., result=...)`。
- `inbox_service.py` 仍创建 `InboxKind.COMMAND_RESULT`。
- `operation_service.py` 会从 manifest events 生成沙箱 Event 模板，因此错误 event 会误导调试 UI。

## 目标

清理真实插件、模板、开发指南和测试中把命令结果建模为 manifest Event 的错误定义，并加防回归测试。

完成后应满足：

- 真实插件 manifest 不再包含 `category: COMMAND_RESULT` 的 event。
- 插件模板和开发指南不再教作者把命令结果写入 `events`。
- 沙箱 Event 模板不会生成 `COMMAND_RESULT`。
- Result Callback / `COMMAND_RESULT` Inbox / `@on_command(..., result=...)` 运行链路保持不变。

## 非目标

- 不改 `/api/v1/callback/result` 入站协议。
- 不改 `rough_sorter/plugin.py` 或 `smt_sorting_inbound/plugin.py` 的状态机。
- 不迁移历史数据。
- 不在 manifest loader 层硬禁 `EventCategory.COMMAND_RESULT`。
- 不新增沙箱 Result 模板。
- 不改 `docs/archive/**`、`docs/superpowers/archive/**`。
- 不改描述 Inbox / Result Callback 合法 `COMMAND_RESULT` 的业务文档。

系统尚未发布，本次不保留旧语义兼容债；测试夹具也应清理，不继续保留错误示例。

## 实施前置条件

- 不要直接在 `develop` 上实施。先从 `develop` 创建 `feature/workline-command-result-manifest-cleanup` 分支。
- 本 SPEC 预计不需要修改运行时函数、类或方法体；如果实施中确实需要改 Python 函数、类或方法，必须先按项目规则运行 GitNexus impact analysis，并在 HIGH / CRITICAL 风险时先停下来确认。
- 实施流程应使用 `executing-plans` 或等价的逐任务执行方式：按任务改动、按任务验证，不把 manifest、文档、测试和运行时行为混在一次无验证的批量修改里。

## 影响范围

必须覆盖：

- `src/workline_plugins/rough_sorter/manifest.yaml`
- `src/workline_plugins/smt_sorting_inbound/manifest.yaml`
- `src/workline_plugins/smt_sorting_inbound/constants.py`
- `docs/plugin_development_guide.md`
- `docs/templates/workline_plugin/manifest.yaml.tmpl`
- `docs/templates/workline_plugin/README.md`
- WorkLine runtime / plugin manifest / template 相关测试

不要求修改：

- `docs/integration/third_party_integration_whitepaper.md`
- `src/workline_plugins/rough_sorter/plugin.py`
- `src/workline_plugins/smt_sorting_inbound/plugin.py`
- `src/workline_runtime/plugin_manifest.py`

## 设计约定

`manifest.yaml` 的 `events` 声明 manifest 可识别的事件名、来源设备角色和事件分类，可包含设备通过 `/callback/event` 上报的主动事件以及 `INTERNAL` / `OPERATOR` / `SAFETY` 等运行时可见事件。

命令结果不属于 manifest Event：

```text
设备执行命令完成
  -> POST /api/v1/callback/result
  -> WorklineInbox(kind=COMMAND_RESULT)
  -> plugin @on_command(COMMAND, result=...)
```

测试和夹具中表达命令结果时，应使用 Result Callback 语义字段，而不是再构造 `_RESULT` event 名称：

```text
payload.command_code = 已下发命令编号
payload.command_type = SORTING_SOURCE_PICK | SORTING_TARGET_PLACE | SORTING_NG_PLACE
payload.result = SUCCESS | FAILED
```

如果测试只需要证明已有 `command_id` / `awaiting_command_id` 能驱动后续资源事实处理，不应再补一个 `canonical_event_type: SORTING_*_RESULT` 作为证据。

沙箱操作员可见 Event 模板来自 manifest.events 中的 `ENTRY_DEVICE` / `OPERATOR` / `SAFETY`：

```text
manifest.events
  -> filter out INTERNAL / COMMAND_RESULT
  -> sandbox event templates
```

## 实施任务

1. 清理真实插件 manifest

   - 删除 `rough_sorter/manifest.yaml` 中：
     - `ROUGH_SORTER_PICK_AND_PUT_RESULT`
     - `ROUGH_SORTER_MOVE_TO_NG_RESULT`
     - `ROUGH_SORTER_MOVE_FORWARD_RESULT`
     - `ROUGH_SORTER_PUT_TO_BIN_RESULT`
   - 删除 `smt_sorting_inbound/manifest.yaml` 中：
     - `SORTING_SOURCE_PICK_RESULT`
     - `SORTING_TARGET_PLACE_RESULT`
     - `SORTING_NG_PLACE_RESULT`

2. 清理 SMT 常量

   - 删除 `EVENT_SOURCE_PICK_RESULT`
   - 删除 `EVENT_TARGET_PLACE_RESULT`
   - 删除 `EVENT_NG_PLACE_RESULT`
   - 同步清理 `__all__` 和测试 import。

3. 更新文档和模板

   - `docs/plugin_development_guide.md`：说明 `events` 可包含设备上报事件和 `INTERNAL` / `OPERATOR` / `SAFETY` 等运行时可见事件，命令结果走 Result Callback。
   - `docs/templates/workline_plugin/manifest.yaml.tmpl`：移除 `MEASURE_ITEM_RESULT/category: COMMAND_RESULT` 示例。
   - `docs/templates/workline_plugin/README.md`：移除“命令结果事件写入 events”的说法。

4. 更新测试

   - 真实插件 manifest 测试从“断言 `_RESULT` 是 `COMMAND_RESULT`”改为“断言真实插件 manifest 不包含 `EventCategory.COMMAND_RESULT` event”。
   - `test_workline_operation_service.py` 明确断言 `COMMAND_RESULT` 不生成沙箱 Event 模板。
   - `test_runtime_intent_effects.py` 中 `canonical_event_type: SORTING_*_RESULT` 不得替换成另一个事件名；应改为 Result Callback 语义字段，例如 `command_type: COMMAND_SOURCE_PICK|COMMAND_TARGET_PLACE|COMMAND_NG_PLACE`、`result: SUCCESS`，必要时保留 `command_code`。
   - `test_plugin_manifest_yaml_loader.py` 清理 `COMMAND_RESULT` event 夹具，loader 仍不硬禁 enum。
   - `test_plugin_template_assets.py` 验证模板不再包含 `COMMAND_RESULT` event 示例。

## 验收标准

- `rg "category: COMMAND_RESULT" src/workline_plugins docs/templates/workline_plugin docs/plugin_development_guide.md` 不再命中错误 Event 示例。
- `rough_sorter` 和 `smt_sorting_inbound` manifest 加载成功。
- 真实插件 manifest 中没有 `EventCategory.COMMAND_RESULT` event。
- 沙箱 Event 模板不会包含 `COMMAND_RESULT`。
- 测试夹具中不再用 `canonical_event_type: SORTING_*_RESULT` 表达命令结果。
- Result Callback / `COMMAND_RESULT` Inbox 相关测试仍通过。

## 验证命令

```bash
uv run pytest tests/workline_runtime/test_plugin_manifest_and_topology.py
uv run pytest tests/workline_runtime/test_smt_sorting_inbound_plugin.py
uv run pytest tests/workline_runtime/test_workline_operation_service.py
uv run pytest tests/workline_runtime/test_runtime_intent_effects.py
uv run pytest tests/workline_runtime/test_plugin_manifest_yaml_loader.py
uv run pytest tests/workline_plugins/test_plugin_template_assets.py
```

建议实现完成后额外执行：

```bash
uv run ruff check .
```

## 风险和回滚

- 风险：测试或模板仍残留 `COMMAND_RESULT` event 示例，后续插件作者继续照抄错误合同。
  - 防护：增加真实插件 manifest 通用断言和模板断言。
- 风险：误删 Result Callback 运行链路相关 `COMMAND_RESULT`。
  - 防护：只清理 manifest event 建模，不清理 Inbox、callback/result、session wait type 和业务流程文档中的合法 `COMMAND_RESULT`。
- 回滚：如果发现某个插件确实需要设备主动事件，应该新增真实 Event Push 名称和 `category`，而不是恢复 `_RESULT/category: COMMAND_RESULT`。

## 并行策略

建议顺序实现。manifest、模板、文档和测试都在同一合同边界内，拆成多个 worktree 容易产生重复修正和测试冲突。

## 参考结论

`_RESULT` event 定义不会影响当前 `plugin.py` 命令结果处理路由，但会影响 manifest 的外部合同表达和沙箱 Event 模板。因此本次清理应聚焦 manifest / 模板 / 测试 / 指南，而不是改运行时状态机。
