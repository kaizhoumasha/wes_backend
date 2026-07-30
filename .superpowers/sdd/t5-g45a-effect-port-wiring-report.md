# T5 G4.5a EFFECT preparation Port 接线报告

## 状态

DONE_WITH_CONCERNS

## 范围与实现

- 新增 deployment-owned `effect_preparation_runtime`：仅持有 immutable provider catalog 与既有
  `wms_fulfillment_domain_projector`，不持有 DB session、不执行 HTTP、也不拥有外部资源。
- `WorklineRuntimeServices` 从当前 owner 取得 WMS EFFECT preparation Port；未绑定 owner 时保持 `None`。
- RuntimeInbox attempt 分别注册 QUERY 和 EFFECT Port；不存在其中一个不会阻断另一个。
- Stage 3 为每个 attempt 临时创建带 `RuntimeCapabilityContext.get_effect_port` resolver 的
  `RuntimeIntentEffectApplier`，使实际 writeback 可以解析 `WmsEffectPreparationPort`；未缓存 resolver。
- API/Celery 使用已经验证过的 `startup.catalog` 发布 runtime；关闭时只校验并撤销 owner binding。
- 未修改 WMS 状态机、QUERY、外部 HTTP transport、模型、migration、operation registry 或旧 Rack/Handling producer。

## TDD

- RED：EFFECT-only 和 QUERY+EFFECT attempt 注册测试分别报未注册的
  `WmsEffectPreparationPort`；Stage 3 resolver 测试报
  `RuntimeInboxWriteBackService` 缺少 `effect_applier_for_attempt`。
- GREEN：对应定向 runtime 注册/Stage 3 resolver 测试 `6 passed`。

## 验证

- `uv run pytest tests/deployment/test_wms_effect_lane_dispatch.py tests/deployment/test_celery_async_runtime.py -q`
  → `60 passed`。
- `uv run pytest tests/deployment/test_wms_transport_startup.py -q` → `4 passed`。
- `uv run pytest tests/runtime/orchestration/test_runtime_inbox_attempt_profile.py -q -k 'attempt_runtime_registers or stage_three_effect_applier or real_runtime_services_leave'`
  → `6 passed`。
- `uv run ruff format --check <touched files>`、`uv run ruff check <touched files>`、`git diff --check` → 通过。

## GitNexus

- 依要求发起 `WorklineRuntimeServices`、`build_workline_runtime_services`、
  `_configure_attempt_runtime_ports`、`RuntimeInboxWriteBackService` 与
  `RuntimeIntentEffectApplier` 的 upstream impact；MCP 因本机 LadybugDB 文件版本 42 与工具版本 40
  不兼容而未能返回图谱结果。brief 给出的 HIGH/LOW 风险和用户的继续授权已执行。

## Concern

- 全文件 `tests/runtime/orchestration/test_runtime_inbox_attempt_profile.py -q` 仍有 1 项既有失败：fixture
  返回旧 `WriteDisposition.COMMITTED`，production path 现在要求 `RuntimeInboxWriteBackResult.disposition`。
  此项已列为 T5 final blocker，本切片没有扩展到该旧 fixture 迁移。
