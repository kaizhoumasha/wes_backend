"""wms_integration ports — 当前里程碑活跃 4 个 + @deferred 3 个。

按主计划 §3.5.1 + §5.1 拆分:

活跃 (当前里程碑):
1. WmsMasterDataPort (物料主数据, 包括 area/warehouse/storage_location/equipment)
2. InventoryQueryOperationPort (库存只读 typed operation 查询)
3. WmsInventoryTransactionPort (库存事务: reserve/release/confirm/transfer)
4. WmsFulfillmentPort (履约: 搬运/补给/换面/满箱交换/notify pkg binding)

@deferred (全量联调前):
5. WmsDocumentPort (单据: GRN/拣货单/出库单/波次/任务快照)
6. WmsEventPort (入站事件 normalizer)
7. WmsReconciliationQueryPort (对账 drift 查询)

所有 Protocol 已落地，capability 可独立通过 typed contract 注入。
"""
