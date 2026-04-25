# WORKLINE 插件模板

这个目录是 PR8 的插件开发模板资产。模板来自 `smt_classifier` 和 `inbound_tote_qc` 的共同结构，不复制任一插件的私有复杂度。

使用方式：

1. 复制 `*.py.tmpl` 到 `src/workline_plugins/<plugin_key>/`，去掉 `.tmpl` 后缀。
2. 把 `{{PLUGIN_KEY}}`、`{{PLUGIN_CLASS}}`、`{{CONTRACT_VERSION}}` 等占位符替换成业务值。
3. 先落 `contract.py`，确保事件和结果业务字段只在 `data`，命令业务字段只在 `params`。
4. 再落 `context.py`、`state_machine.py`、`plugin.py` 和测试。
5. 用 `fixtures/` 里的 happy path、业务 NG、系统异常、timeout 和 invalid envelope 示例扩展本业务测试。
6. 开发/测试环境用 `WorkLine.run_mode=SIMULATION` 跑 sandbox 闭环；消息 payload 不增加 sandbox 标志。

模板文件：

- `plugin.py.tmpl`：插件入口和 handler 结构。
- `contract.py.tmpl`：Pydantic payload、业务键解析、结果分类、命令 params helper。
- `context.py.tmpl`：类型化业务 context。
- `state_machine.py.tmpl`：状态和 trigger。
- `tests.py.tmpl`：插件单元测试结构。
- `sandbox_happy_path.md`：WORKLINE 级 sandbox 调试步骤。
- `fixtures/`：白皮书包络示例。
