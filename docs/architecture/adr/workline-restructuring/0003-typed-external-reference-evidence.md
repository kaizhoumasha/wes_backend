# ADR 0003: typed ExternalReference + Evidence Envelope

**状态**: Accepted
**日期**: 2026-06-23
**适用范围**: resource 域外部对象引用 + evidence_json 字段

## 背景

resource 域的 `rack_code / bin_code / location_code` 等外部引用当前是裸字符串，无 schema 无版本无对账标记，导致 WMS master-data 漂移不可检测、6 个月后 evidence 结构变化不可迁移。`evidence_json` 字段是裸 JSON dict，跨域写入方自由结构，无法支持 GIN 索引查询或 schema 演进。

## 决策

1. **typed `ExternalReference`**：所有外部对象引用必须用 typed Pydantic 模型：
   - `system: Literal["WMS", "RCS", "AGV", "CTU", "PLC"]`
   - `object_type: Literal["rack", "bin", "location", "material", "work_position"]`
   - `code / schema_version / validated_at / source_version`
2. **typed `EvidenceEnvelope`**：`evidence_json` 升级为 Pydantic envelope：
   - `schema_version / source_system / source_event_id / source_version / validated_at / request_hash / payload`
3. **GIN 索引**：`ExternalReference.code` + `EvidenceEnvelope.source_event_id` 等结构化字段加 GIN 索引。
4. **WMS master-data drift 对账**：`WmsReconciliationQueryPort.check_*_drift` 定期只读拉取 WMS 权威事实；未验证或漂移的外部引用**不得**驱动关键履约动作。
5. **evidence schema 变更日志**：`docs/contracts/evidence-catalog.md` 维护每次 schema 升级的 source/target 映射。

## 后果

- resource 投影可按结构化字段查询（GIN 索引）。
- WMS 漂移可检测、可分类处理（MISSING / RENAMED / METADATA_DRIFT）。
- evidence schema 演进有 catalog 跟踪，跨域不静默漂移。
- resource 域与 handling / device / external_wms 域使用统一 envelope 规范。

## 验收

- `docs/architecture/specs/workline-restructuring/40-resource-projection.md` 发布。
- `src/app/resource/models/` 引入 `ExternalReference` + `EvidenceEnvelope` Pydantic 模型。
- Alembic 迁移加 GIN 索引。
- `WmsReconciliationQueryPort` 只读实现 + 单元测试；任何 WMS 写入确认或补偿动作必须归入 `WmsInventoryTransactionPort` / `WmsFulfillmentPort`。
- evidence-catalog.md 初始版发布。

## 引用

- 顶层设计：[`../../workline-and-plugin-restructuring.md`](../../workline-and-plugin-restructuring.md)
- Sub-spec 40：[`../../specs/workline-restructuring/40-resource-projection.md`](../../specs/workline-restructuring/40-resource-projection.md)
- 现有 ADR：[`../2026-05-13-wes-wms-rcs-resource-boundary.md`](../2026-05-13-wes-wms-rcs-resource-boundary.md)
