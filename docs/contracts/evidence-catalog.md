# Runtime Evidence Catalog

本文档锁定 WMS evidence envelope 的版本、结构化索引和 drift 分类口径。跨域 evidence 字段变更必须先更新本 catalog，再更新 Pydantic 合同、迁移和测试。

## Schema Versions

| Schema | Owner | Source systems | Required fields |
| --- | --- | --- | --- |
| `evidence.v1` | `wms_integration` | `WMS`, `RCS`, `ECS`, `DEVICE` | `schema_version`, `source_system`, `source_event_id`, `source_version`, `evidence_type`, `occurred_at`, `external_refs`, `request_hash`, `payload_hash`, `payload` |
| `external_ref.v1` | `wms_integration` | `WMS`, `RCS`, `AGV`, `CTU`, `ECS`, `DEVICE` | `system`, `object_type`, `code`, `schema_version`, `validated_at`, `source_version` |

## Indexed Fields

`wms_call_evidence.request_snapshot` 和 `wms_call_evidence.response_snapshot` 使用 JSONB 存储，并声明 PostgreSQL GIN 索引：

| Index | Column | Purpose |
| --- | --- | --- |
| `ix_wms_call_evidence_request_snapshot_gin` | `request_snapshot` | 查询出站 request envelope 内的 `external_refs`、`source_event_id`、`payload_hash` |
| `ix_wms_call_evidence_response_snapshot_gin` | `response_snapshot` | 查询入站/响应 envelope 内的 `external_refs`、`source_event_id`、`payload_hash` |

## Retention And Archive

`WMS_CALL_EVIDENCE_RETENTION_DAYS` 默认 180 天。`WmsCallEvidenceService.archive_expired_evidence()` 只迁移超过保留期且非 `STARTED` 的 evidence：

| Table | Purpose | Notes |
| --- | --- | --- |
| `wms_call_evidence` | 热 evidence 表 | 供 trace、callback、breaker、drift job 和现场诊断查询 |
| `wms_call_evidence_archive` | 归档 evidence 表 | 保留原 `evidence_key`、hash、snapshot、状态、时间和 `original_evidence_id` |

- `STARTED` 表示仍可能在途，不能被 retention job 归档或删除。
- archive 写入成功后才允许删除热表记录。
- archive 表只保存脱敏快照，不恢复或复制原始 provider payload。

## Drift Classification

`ExternalReferenceCatalog` 按 `(system, object_type, schema_version)` 查找当前 provider catalog。`WmsCallEvidenceService.run_external_reference_drift_job()` 只读扫描 evidence envelope，不写 WMS、不修改本地投影。

| Kind | Meaning | Required action |
| --- | --- | --- |
| `NONE` | reference 类型和 `source_version` 与 catalog 一致 | 可继续作为履约 evidence 使用 |
| `UNKNOWN_REFERENCE_TYPE` | catalog 中没有对应 `(system, object_type, schema_version)` | 进入 reconciliation 诊断，不得驱动关键履约动作 |
| `SOURCE_VERSION_MISMATCH` | reference 的 `source_version` 与 catalog 当前版本不一致 | 进入 drift 诊断；由 reconciliation 决定重查、冻结或人工恢复 |

## Change Rules

- 新增 external reference 类型必须先新增 catalog entry，再允许 adapter / callback 写入 envelope。
- source version 漂移只能追加 evidence 和诊断结果，不能覆盖当前投影。
- drift job 是只读任务；任何 WMS 写入确认或补偿动作必须走 `WmsInventoryTransactionPort` 或 `WmsFulfillmentPort`。
- 删除或重命名 schema 字段必须提供迁移路径和 replay fixture，不能静默兼容裸 JSON。
