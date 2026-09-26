# ECS_TEST 来源设备默认值实施计划

**状态：已实施，待合入**

**设计合同：**[ECS_TEST 来源设备调试默认值设计](../specs/2026-09-25-workline-debug-instruction-drafts-design.md)

## 目标与变更面

选择 ECS_TEST 来源设备后，联调界面可读取、保存或清除该设备的常用目标设备、`task_type` 和固定 `params`。默认值只用于预填；运行时仍只读取用户显式应用到 WorkLine 的 `ecs_test_rules`。Transport 联调保持现有表单和默认值。

仅增加下列实现，不新建 Domain 校验模块、独立 Service、Protocol、Snapshot 或运行时装配：

| 位置 | 修改 |
| --- | --- |
| `src/app/device/models/device.py`、一条 Alembic migration | 仅在 `Device` 表类增加可空 `ecs_test_default_json`；不加入 `DeviceEditableBase`、`DeviceCreate`、`DeviceUpdate` 或通用 `DeviceResponse` |
| `src/app/device/repositories/device_repository.py` | 复用 `get_by_device_code_for_update`，定向替换默认值并推进 Device 版本 |
| `src/app/device/services/device_service.py` | 使用现有 `parse_ecs_test_rules` 校验完整单条规则；负责提交事务，提交成功后失效设备缓存 |
| `src/app/device/v1/ecs_test_default.py`、`src/app/device/__init__.py` | 专用 GET/PUT、严格请求/响应模型及现有 Device 读写权限；直接使用现有 `device_service`，不在 `register.py` 增加应用状态 |
| `tests/device/`、`tests/api/`、`tests/integration/device_command/` | 各自验证 Service、真实 ASGI 合同和 PostgreSQL 锁/持久化 |
| `docs/architecture/heavy-test-impact.toml` | 按实际修改文件补精确 HEAVY 映射 |

首个生产补丁前检查 `git status --short`、当前分支、相关 `AGENTS.md`，保留现有工作区现场；对将修改的生产符号运行 GitNexus upstream impact analysis，并列出 DeviceService、Repository、API 和测试的直接消费者。代码只在该变更面内修改。实施目标本身不要求逐任务 Commit、Push、PR 或部署。

## 1. 写失败用例并确认合同

先在 `tests/device/` 写聚焦 FAST 用例：保存完整对象、显式 `null` 清除、设备不存在、活动线仍可保存、保存不改 WorkLine 规则。测试应观察 Service 的提交/缓存调用顺序；只验证真实行为，不为每个新方法复制一套假对象矩阵。先运行新增用例确认 RED，再修改生产代码。

在 `tests/api/` 用现有 `httpx.ASGITransport` 模式写真实 ASGI 请求用例，覆盖 GET 未保存返回 `default: null`、PUT 保存/清除、无权限、设备不存在的 404，以及成功响应的 `device_version` 和统一信封。以下两种请求必须返回 422，不能被解释成清除或空参数：

```json
{}
```

```json
{"default": {"target_device_code": "STATION_SCAN2", "task_type": "MOVE_FORWARD"}}
```

`{"default": null}` 才是清除；完整对象必须显式携带 `params`，允许 `"params": {}`。请求体和嵌套默认值均拒绝多余字段，尤其拒绝客户端提供 `source_device_code`。API 测试必须经过 FastAPI 路由、依赖和 `response_model`，不能用直接调用路由函数代替。GET 与 PUT 虽同路径，测试按 HTTP method 分别断言权限。

## 2. 最小实现

1. 给 `Device` 表类增加可空 JSON 字段，使用 `uv run alembic revision -m "add device ecs_test default"` 生成迁移 revision，再填写单列 upgrade/downgrade。不要改设备通用 CRUD schema 或 WorkLine 模型。
2. Repository 在现有 `get_by_device_code_for_update` 行锁下读取设备，缺席返回 `None`；存在时整体替换 `ecs_test_default_json`、调用 `increment_version()` 并 `flush`。同一设备后写覆盖先写；不拆分或合并 `params` 子键。
3. `DeviceService` 保存前用当前设备的 `device_code` 合成 `source_device_code`，调用 `parse_ecs_test_rules({"ecs_test_rules": [rule]})` 复用结构校验。Service 通过现有 `_commit_mutation` 提交，成功后才失效缓存；提交失败按现有机制回滚且不失效缓存。GET 复用已有按设备编码查询。保存不要求客户端提供 Device `version`，但必须推进服务器版本并返回新值。
4. 专用 API 使用 `/devices/{device_code}/ecs-test-default`，GET 复用 `biz:device:detail`、PUT 复用 `biz:device:update`。请求模型将 `default: EcsTestDefaultRule | None` 声明为**必填且可为 null**，规则模型中的 `params` 也为必填；API 只负责校验外部形状、调用 Service 和组装响应，不直接提交事务或访问 Repository。将路由挂入现有 `src/app/device/__init__.py`，不改 `register.py` 的应用生命周期。

完成每个内聚切片后运行相应聚焦 FAST 用例；若已有更简单的仓库惯用实现能满足同一合同，以现有实现为准，不机械照搬步骤。

## 3. PostgreSQL 与迁移验证

只使用独占临时 PostgreSQL 实例和独立逻辑库；不设置 `ALLOW_SHARED_DEV_DB_INTEGRATION`，不对共享开发库运行 migration、并发测试或清理。先在干净逻辑库验证 base 到 head 迁移，再在独立的已迁移逻辑库运行 `tests/integration/device_command/test_ecs_test_default_postgresql.py`；环境未启用导致的 skip 不计为通过。无需为补做 RED 执行 `git stash`。

该集成文件至少验证：

- 保存后重新查询得到完整 JSON；显式清除后为 `None`；未保存的已有 Device 在迁移后仍可读取。
- 活动 WorkLine 上的设备可保存默认值，WorkLine 的 `ecs_test_rules`、版本和在途命令不变。
- 两个独立事务同时保存同一设备时，第二个写入等待第一个提交；最终对象是最后一次成功写入的**完整对象**，Device 版本精确等于初始版本 `V0 + 2`。
- 保存后，用保存前取得的旧 Device 版本走既有通用设备更新，会触发原乐观锁冲突。

清理只删除本测试创建的行；不清理其它业务数据或卷。迁移降级只在该独占临时库中验证，且不替代干净库 base→head 证据。

## 4. 最终核对

枚举 `Device.ecs_test_default_json` 的全部读取者：应只有专用读写路径；WorkLine START、Event worker 和 DeviceCommand 创建路径均不得读取它。检查通用设备 Create/Update/Response 不暴露该字段，保存默认值也不创建命令。修正生产符号的旧值/残留后，运行：

```bash
uv run pytest tests/device/test_ecs_test_default_service.py tests/api/test_device_ecs_test_default_api.py -q
uv run scripts/select_heavy_tests.py --scope unstaged
./scripts/run_selected_heavy_local.sh --scope unstaged
./scripts/git-quality-gate.sh --profile quality
git diff --check
```

上述测试文件名是目标所有者；实施时按实际新增文件名调整。HEAVY 映射须让新持久化路径和迁移命中真正验证它们的集成/迁移测试，不把无关 DeviceCommand 约束测试当作兜底。若交付目标后来包含 Commit，先固定 staged 快照，按项目规则对 staged 范围选择 HEAVY 并由正常 hook 执行 QUALITY；不要为同一未变化快照重复完整门禁。

最终报告分别说明代码/数据库验证、真实 ECS 与物理现场验收边界。默认值接口成功只证明参数已保存，不证明规则已应用或设备已执行。
