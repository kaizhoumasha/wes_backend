# WORKLINE Generated Plugin 开发指南

本文面向当前 WORKLINE 插件开发者。生产插件统一位于
`src/app/runtime/workline_plugins/`，通过 generated index、immutable Plugin Binding
和 `WorklinePluginDispatcher` 执行。

当前参考实现：

- `rough_sorter/`：多 route、独立 config/input/state 的完整插件。
- `smt_sorting_inbound/`：source-pick 最小业务切片。
- `generated_index.py`：生成产物，只能由生成器更新。

## 核心边界

插件负责：

- 固定 `plugin_key` 与 `contract_version`。
- 用 typed schema 声明设备角色、命令、route 和 System Capability。
- 为每个 route 注册 stable handler、facts model 与 facts builder。
- 以纯函数根据 input、state、config、facts 产生 `PluginDecision`。
- 只返回 intent，不直接落数据库或发送消息。

插件不负责：

- 不创建或修改 Session、Execution、RuntimeInbox、DeviceCommand、SystemOutbox、Timeline 或 Hold。
- 不访问 Repository、SQL、HTTP Client、Celery 或 provider implementation。
- 不生成或覆盖 `command_code`。
- 不实现 ACK、重试、timeout、recovery scan、事务或幂等基础设施。
- 不在 RuntimeInbox bridge、dispatcher 或 callback 中增加插件私有分支。
- 不提供未绑定执行、备用 delegate、route alias 或运行时 fallback。

## 推荐目录

最小插件使用四个文件：

```text
src/app/runtime/workline_plugins/<plugin_key>/
  __init__.py
  contracts.py
  handlers.py
  definition.py
```

复杂插件可像 `rough_sorter/` 一样拆分 `config.py`、`inputs.py`、`state.py` 和
`domain_contract.py`，但只在文件确有独立职责时拆分。

对应测试放在：

```text
tests/workline_plugins/<plugin_key>/
```

不要在 `tests/` 根目录新增测试。真实 PostgreSQL 闭环放在
`tests/integration/workline_capabilities/`。

## Definition 与 route registration

`definition.py` 是插件静态合同入口，应包含：

- fixed plugin identity；
- typed config/state；
- 设备、命令与资源 schema；
- allowed System Capabilities；
- logical route 到 `HandlerRegistration` 的完整映射。

每个 `HandlerRegistration` 必须同时提供：

- `handler`：纯业务决策函数；
- `facts_model`：该 route 接受的 typed facts；
- `facts_builder`：从通用 `PluginAttemptFactSource` 构建 facts 的顶层稳定函数。

facts builder 的 import identity 会进入 generated index digest。lambda、局部函数、
重复 registration 和不稳定 callable 会在生成阶段被拒绝。builder 输出还会在 dispatch
前由 `facts_model` 再校验一次。

## Config、State、Input 与 Facts

- Config 只表达插件业务配置，使用 Pydantic 严格校验。
- State 只表达插件 decision 的 immutable 下一状态，不直接代表数据库写入。
- Input 只表达 route 的 canonical 输入，不读取任意 callback payload fallback。
- Facts 只包含平台已验证的 immutable 事实，不携带 ORM entity、Session 或 Repository。
- 共享的 `CommandResultInput`、`CapabilityEffectResultInput` 等输入放在平台
  `contracts.py`，不在各插件复制。

## Command 与 callback authority

插件声明设备动作和业务参数；WES Runtime 统一生成 `command_code` 并持久化
`DeviceCommand + SystemOutbox`。

`COMMAND_RESULT` 必须携带有效 `RuntimeInbox.command_id`。Runtime 根据该 ID 读取
权威 `DeviceCommand`，再校验 task type、command code、correlation 和业务 evidence。
callback payload 不能覆盖这些权威字段。

校验失败必须返回稳定 diagnostic，且 effect write set 为空。

## Binding 与 activation

新执行只能来自 active immutable Plugin Binding。激活时平台校验：

- generated definition 与 contract version；
- canonical config hash；
- generated index digest；
- provider profile identity（插件声明时必填并进入 snapshot）；
- 环境、撤权、禁用和有效期。

`WorklineSession`、`ExecutionSession` 与 `ExecutionWorkItem` 创建时固定：

```text
plugin_key
contract/manifest_version
plugin_binding_id
plugin_binding_version
plugin_config_hash
plugin_index_digest
```

这些字段在 ORM 和 PostgreSQL 都是必填；业务代码不得构造缺 pin 的运行态记录。

## System Capability

插件只能调用 Definition 显式声明的 `(capability_key, version)`。平台 gateway 会拒绝：

- 未声明 capability；
- version 不匹配；
- payload 不满足 typed contract；
- effect evidence 与当前 attempt 不一致。

System Capability handler 拥有自己的 Service/Repository 边界；插件只表达调用意图，
不直接 import 实现。

## 生成与验证

修改 Definition、registration、facts builder 或 handler identity 后运行：

```bash
uv run scripts/generate_runtime_extensions.py
uv run scripts/generate_runtime_extensions.py --check
uv run pytest tests/workline_plugins/<plugin_key> -q
uv run pytest tests/workline_runtime/extensions -q
```

生成文件禁止手工编辑。提交前至少验证：

- identity 与 digest 稳定；
- config/input/state/facts 成功和失败路径；
- capability 声明隔离；
- command/result authority；
- mismatch/reject 零副作用；
- 重复 input 的幂等 decision；
- binding activation 与 digest mismatch fail closed。

涉及数据库不变量、命令/Outbox、callback 或 recovery 时，再运行对应的隔离
PostgreSQL integration suite。

## SMT source-pick 参考闭环

```text
handoff request
  → bound Session / Execution / WorkItem
  → RuntimeInbox
  → generated SMT route
  → DeviceCommand + SystemOutbox
  → COMMAND_RESULT(command_id)
  → authoritative command validation
  → unique recovery correlation
  → source item PICKED
```

零候选、多候选、evidence mismatch、设备失败、重复 callback 和重复 recovery scan
都必须 fail closed 或进入受控 Hold，不得猜测关联，也不得重复落 effect。
