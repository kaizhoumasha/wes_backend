"""wms_integration 7 ports。

按主计划 §3.5.1 + §5.1 拆分:
1. WmsMasterDataPort (物料主数据, 包括 area/warehouse/storage_location/equipment)
2. WmsDocumentPort (单据: GRN/拣货单/出库单/波次/任务快照)
3. WmsInventoryQueryPort (库存只读查询)
4. WmsInventoryTransactionPort (库存事务: reserve/release/confirm/transfer)
5. WmsFulfillmentPort (履约: 搬运/补给/换面/满箱交换/notify pkg binding)
6. WmsEventPort (入站事件 normalizer: WMS_GRN_RECEIVED / WMS_PALLET_ARRIVED 等)
7. WmsReconciliationQueryPort (对账 drift 查询)

所有 Protocol 已落地，capability 可独立通过 port contract 注入。

端口方法命名: ClassName.method (Port.method 合同), 与
src/app/contracts/external_contract_profile.py runtime_capabilities_query
字段格式一致。
"""
