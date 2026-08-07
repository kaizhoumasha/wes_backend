# Runtime Evidence Catalog

> 状态：`implementation_baseline`。本文只锁定收敛前仍在运行的 WMS evidence 表、索引与 drift job；目标架构中的外部输入统一归属 `InboundEvidence`，WMS 查询由具体业务模块通过 `WmsClient` 完成，确认和搬运证据分别由 `WmsConfirmation` 与 `TransportTask` 拥有。旧表和任务退役时本文必须同步重写或归档。

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

## Retention

`wms_call_evidence` 是供 trace、callback、breaker、drift job 和现场诊断查询的热 evidence 表。
WMS 不维护专用保留周期或清理任务，记录保留服从项目统一保留策略或经核准的运维方案。

在统一策略明确前，active、结果不明确、Hold 或 Reconciliation 关联记录不得进入普通清理集合；
容量边界通过生产容量规划和周期性复核闭环。

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
- drift job 是只读任务；任何 WMS 写入确认或补偿动作必须走 operation-specific typed EFFECT Definition
  与 `WmsEffectPreparationPort`。
- 本系统尚未发布；删除或重命名 schema 字段时直接更新最终模型并清空开发/测试数据，不提供旧数据迁移、
  replay fixture 或裸 JSON 兼容路径。本文对应的旧 evidence 实现退役后应整体移出项目归档。
