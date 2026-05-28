# WORKLINE 插件模板

这个目录是 PR8 的插件开发模板资产。模板沉淀通用插件结构，不复制任一业务插件的私有复杂度。

使用方式：

1. 复制 `*.py.tmpl` 到 `src/workline_plugins/<plugin_key>/`，去掉 `.tmpl` 后缀。
2. 把 `{{PLUGIN_KEY}}`、`{{PLUGIN_CLASS}}`、`{{CONTRACT_VERSION}}` 等占位符替换成业务值。
3. 先落 `contract.py`，确保事件和结果业务字段只在 `data`，命令业务字段只在 `params`。
4. 再落 `context.py`、`plugin.py` 和测试。
5. 用 `fixtures/` 里的 happy path、业务 NG、已建模异常流、系统错误、timeout 和 invalid envelope 示例扩展本业务测试。
6. 开发/测试环境用 `WorkLine.run_mode=SIMULATION` 跑 sandbox 闭环；消息 payload 不增加 sandbox 标志。

模板文件：

- `plugin.py.tmpl`：插件入口和 handler 结构。
- `contract.py.tmpl`：Pydantic payload、业务键解析、结果分类、命令 params helper。
- `context.py.tmpl`：类型化业务 context。
- `tests.py.tmpl`：插件单元测试结构。
- `sandbox_happy_path.md`：WORKLINE 级 sandbox 调试步骤。
- `fixtures/`：白皮书包络示例。

错误定义硬规则：

- NG 是物料的业务结果，不是系统错误。
- 已建模异常流只要能自动分流、返工、继续或完成，就不写成 `FAILED`。
- 只有流程无法自动推进、需要人工/维修/对账/外部介入时，才进入错误或阻断。
- 设备动作成功但检测结果 NG 时，设备 result 应为 `SUCCESS`，业务 NG 原因放在 `data`，插件再返回 `RuntimeIntent.mark_ng(...)` 和后续分流意图。
