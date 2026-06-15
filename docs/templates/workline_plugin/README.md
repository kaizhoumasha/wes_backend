# WORKLINE 插件模板

这个目录是 WORKLINE 插件开发模板资产。模板沉淀通用插件结构，不复制任一业务插件的私有复杂度。

## 使用方式

1. 复制 `contract.py.tmpl`、`context.py.tmpl`、`plugin.py.tmpl` 到 `src/workline_plugins/<plugin_key>/`，去掉 `.tmpl` 后缀。
2. 把 `{{PLUGIN_KEY}}`、`{{PLUGIN_CLASS}}`、`{{CONTRACT_VERSION}}` 等占位符替换成业务值。
3. 先落 `contract.py`，确保事件和结果业务字段只在 `data`，命令业务字段只在 `params`。
4. 再落 `context.py`、`plugin.py` 和测试。
5. 在现有 registry 中显式新增/合并 `WorklinePluginDefinition` 条目，位置是 `src/workline_plugin_registry.py`。
6. manifest 只写八类可序列化静态事实：`plugin_key`、`contract_version`、`devices`、`rack_positions`、`topology`、`commands`、`events`、`resource_boundaries`。
7. registry helper 只负责读取运行时行为；运行时能力写在插件类成员或方法上：`resolve_business_key`、`classify_result`、必须实现 `get_context_model()`、`resolve_material_identity`、`list_ng_reasons`。
8. 用 `fixtures/` 里的 happy path、业务 NG、已建模异常流、系统错误、timeout 和 invalid envelope 示例扩展本业务测试。
9. 开发/测试环境用 `WorkLine.run_mode=SIMULATION` 跑 sandbox 闭环；消息 payload 不增加 sandbox 标志。

## 模板文件

- `plugin.py.tmpl`：插件入口、pure-data manifest 和 handler 结构。
- `contract.py.tmpl`：Pydantic payload、业务键解析、结果分类、物料身份解析、NG 原因目录、命令 params helper。
- `context.py.tmpl`：类型化业务 context。
- `registry_entry.py.tmpl`：注册到 `src/workline_plugin_registry.py` 的片段。
- `tests.py.tmpl`：插件单元测试和 registry/manifest 合同测试结构。
- `sandbox_happy_path.md`：WORKLINE 级 sandbox 调试步骤。
- `fixtures/`：白皮书包络示例。

## Manifest 合同

- manifest 是 pure data，不保存 callable、Pydantic 类型或运行态对象。
- `devices` 使用 `DeviceRequirement`，只声明设备角色、数量和硬件能力。
- `rack_positions` 只声明 WES-managed rack docking positions / inventory-fact anchors，不枚举所有物理点位。
- `topology` 中 MATERIAL_FLOW 只表达 rack position 到 rack position；设备动作边使用 `FlowEdgeType.OPERATION`。
- `events` 使用 `EventBinding`，声明事件名、来源设备角色、事件分类和 payload schema 引用。
- `commands` 使用 `CommandBinding`，声明命令名、目标设备角色、货架位参数和结果事件绑定。
- `resource_boundaries` 使用 `ResourceBoundary`，声明 rack/WMS/snapshot/lease 等资源编排边界。
- `RackPositionArg` 静态货架位使用 `rack_position_ref`，`rack_position_ref` 与 `source` 互斥；`RackPositionArgSource` 不支持 `STATIC`。

## 错误定义硬规则

- NG 是物料的业务结果，不是系统错误。
- 已建模异常流只要能自动分流、返工、继续或完成，就不写成 `FAILED`。
- 只有流程无法自动推进、需要人工/维修/对账/外部介入时，才进入错误或阻断。
- 设备动作成功但检测结果 NG 时，设备 result 应为 `SUCCESS`，业务 NG 原因放在 `data`，插件再返回 `RuntimeIntent.mark_ng(...)` 和后续分流意图。

## Runtime helper 约定

- `resolve_business_key` 只处理本插件声明的业务事件；不属于本插件或无法判断的 payload 返回 `None`，让通用 resolver 继续尝试平台通用字段。
- 明确属于本插件但违反合同的 payload 才抛出 `ValueError`，运行时会把它记录为插件解析失败。
- `classify_result` 只补充插件拥有的结果分类；普通成功/失败可交给通用分类器。
- `resolve_material_identity` 必须返回稳定 `idempotency_key`，用于 NG return / runtime hold 幂等和冲突判断。
- `list_ng_reasons` 暴露插件 NG 原因，供 `/runtime-holds/ng-reasons` 和 NG return 记录复用。
