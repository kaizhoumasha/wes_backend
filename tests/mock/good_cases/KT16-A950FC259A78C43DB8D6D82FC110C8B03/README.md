# KT16 完整执行 GOOD CASE：A950FC259A78C43DB8D6D82FC110C8B03

## 1. 定位与证据边界

这是现场完整闭环的验收基准，包含正常执行主线、恢复成功分支和保留异常。不能称为“零异常、无人干预已证明”的样本。所有时间为 **2026-09-25 UTC**；北京时间为 **2026-09-26，加 8 小时**。毫秒取持久化/接收时间，不将字段 `updated_at` 当作物理动作发生时间。

任务数据库 ID `353500396663360`，WorkLine `KT16 / 348950323769920`，插件 `manual-picking / 0.1.0`。任务创建 `21:16:47.779`，状态记录 `EXECUTION_COMPLETED` 于 `21:37:02.457`；最后关联货架回库结果收到于 `21:46:22.099`。任务业务完成和退箱/搬运收尾是不同生命周期。

证据：[evidence.json](evidence.json)，包含 32 个 WMS confirmation、118 条 InboundEvidence、30 个 Transport 及冻结提交正文、42 条 Transport 回调、24 个 DeviceCommand、6 个 Passage、6 个 Return、30 个业务关联以及 48 条 ECS 原始入口日志。数据库主快照采集于 2026-09-25T21:52:01.970071+00:00；补充上下文采集于 2026-09-25T21:53:00.800666+00:00。筛选窗口及来源见 `capture_scope`；相同窗口未发现其他新任务。

WES 对 RCS 的观察来自 WMS 转交的 Transport 接纳/结果。没有取得 WMS↔RCS 内部 HTTP、RCS request_id、AGV/PLC 动作日志；不能把 WES→WMS 请求当作直接发给 RCS。PDA 内部 Cell 拣选也是 WMS 黑盒。48 条 ECS 入口日志均 HTTP 200；WMS 信封按持久化规范化载荷保存，不臆造未采集的 HTTP 响应头。

## 2. 初始条件与拓扑

目标架 `610017 / 90` → `OUT65`；入口 `CNV0301`、出口 `CNV0302`；来源/退箱工作位 `KT16`。SCAN1～4 对应 `STATION_SCAN9`～`STATION_SCAN12`。计划只有 revision 1，来源为 `510001/90、510002/270、510017/270、510024/90、510027/90、510034/90`。另外两次 drain 补架分别用于 `510035/270` 和 `510001/270`。

初始 F01 与前三个来源架 CTU01 于 21:17:24 创建；实际首个来源到位是 `510017/270`，并非计划数组首项。验收依赖真实到位身份/面，不按提交顺序假定物理到位。后续来源架通过既有进场窗口在离场接纳后补入。

完整跨系统顺序见 [timeline.csv](timeline.csv)（303 行，按 WES 接收/持久化时间排序；创建与 ACK 不是物理完成）。

## 3. 六箱完整路线

全部 6 个 Passage 为 CLOSED、WMS 准入 WORK_REQUIRED、完成决定 NORMAL；6 个 Return 为 EXITED。每箱恰好 4 条 MOVE_FORWARD，均取得 SUCCESS 回调并记为 SUCCEEDED。

| 箱号 | 入站来源 rack/face/slot | 入站成功收到 | SCAN1 收到 | SCAN2 收到 | WMS 完成收到 | SCAN3 收到 | SCAN4 收到 | 退箱目标 rack/face/slot | 退箱成功收到 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A000002231 | 510017/270/510017B3F2C101 | 21:22:15.027 | 21:22:24.781 | 21:22:38.793 | 21:22:59.942 | 21:23:13.605 | 21:23:19.198 | 510001/90/510001A2F1C101 | 21:24:45.182 |
| A000002165 | 510001/90/510001A2F1C101 | 21:23:45.086 | 21:23:55.177 | 21:24:09.196 | 21:24:30.918 | 21:24:44.502 | 21:24:49.848 | 510002/270/510002B3F1C101 | 21:27:23.246 |
| A000000628 | 510002/270/510002B3F1C101 | 21:26:24.873 | 21:26:34.861 | 21:26:49.359 | 21:27:02.323 | 21:27:15.779 | 21:27:21.304 | 510024/90/510024A4F2C101 | 21:29:55.538 |
| A000002456 | 510024/90/510024A4F2C101 | 21:28:51.388 | 21:29:01.250 | 21:29:16.373 | 21:32:04.324 | 21:32:17.898 | 21:32:23.407 | 510035/270/510035B2F1C101 | 21:33:21.667 |
| A000002701 | 510027/90/510027A3F2C101 | 21:31:34.195 | 21:31:44.251 | 21:32:20.063 | 21:32:34.959 | 21:32:48.628 | 21:32:54.152 | 510035/270/510035B2F2C101 | 21:34:12.577 |
| A000000341 | 510034/90/510034A2F1C101 | 21:35:43.803 | 21:35:53.864 | 21:36:08.216 | 21:36:46.642 | 21:37:00.207 | 21:37:06.171 | 510001/270/510001B4F2C101 | 21:42:50.154 |

各扫码原始后缀保留在附录 ECS 事件表和原始入口日志中。不得以说明文档常见示例后缀覆盖实际扫码。Return EXITED 只表示搬运执行权交接，物理放置以 TARGET_PLACED/Transport 成功为准。

## 4. 全部 WMS 决策请求及结果

方向均为 WES→WMS；`operation_id` 是请求/响应关联和技术重试身份。完整 request_payload 和 normalized_payload 在证据文件中，按 response_evidence_id 连接。任务完成后的 drain/return 保持 WorkLine 所有权。

| operation | 对象/数量 | 创建 | 派发 | 完成持久化 | 结果 | Evidence 应用 | operation_id | Evidence ID |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| outbound.picking_task.prepare@v1 | A950FC259A78C43DB8D6D82FC110C8B03 | 21:16:47.808 | 21:16:47.855 | 21:16:48.966 | PREPARE_ACCEPTED | APPLIED | 01a0da6d-8d50-7744-85a2-dfe1b75b13e9 | 353500401586752 |
| outbound.bin.inbound_batch@v1 | 510017 | 21:21:38.032 | 21:21:41.362 | 21:21:42.246 | READY | APPLIED | 01a0da71-faf8-7d63-bac1-9cd45de1318c | 353501602861632 |
| outbound.rack.departure_decide@v1 | 510017 | 21:22:15.242 | 21:22:15.280 | 21:22:16.154 | READY | APPLIED | 01a0da72-8c50-7664-85e0-5b47c2416903 | 353501741744704 |
| outbound.manual_bin.work_admission_decide@v1 | A000002231 | 21:22:41.355 | 21:22:41.403 | 21:22:42.381 | WORK_REQUIRED | APPLIED | 01a0da72-f24b-7122-99aa-d719d7a3a93b | 353501849158208 |
| outbound.bin.inbound_batch@v1 | 510001 | 21:23:11.165 | 21:23:11.356 | 21:23:12.178 | READY | APPLIED | 01a0da73-66cc-73bf-bba1-394a9e48d63a | 353501971206720 |
| outbound.bin.return_batch@v1 | 510001 | 21:23:45.288 | 21:23:51.363 | 21:23:52.177 | READY | APPLIED | 01a0da73-ec2a-798c-b948-ca2dfdfff33d | 353502135034432 |
| outbound.manual_bin.work_admission_decide@v1 | A000002165 | 21:24:11.353 | 21:24:11.401 | 21:24:12.316 | WORK_REQUIRED | APPLIED | 01a0da74-51d9-735a-8155-de76503d1358 | 353502217531968 |
| outbound.rack.departure_decide@v1 | 510001 | 21:24:45.011 | 21:24:45.048 | 21:24:45.925 | READY | APPLIED | 01a0da74-d556-73dc-bd5c-8724e214792c | 353502355190336 |
| workline.return_buffer.drain_rack_decide@v1 | 1 | 21:24:56.463 | 21:25:01.355 | 21:25:02.282 | READY | APPLIED | 01a0da75-021a-7152-8aaa-4bbed3f60eac | 353502422209088 |
| outbound.bin.inbound_batch@v1 | 510002 | 21:25:41.478 | 21:25:51.359 | 21:25:52.354 | READY | APPLIED | 01a0da75-b1ed-73c7-b373-da907afc7c9d | 353502627287616 |
| outbound.bin.return_batch@v1 | 510002 | 21:26:25.092 | 21:26:31.360 | 21:26:32.304 | READY | APPLIED | 01a0da76-5c64-76ab-ba10-75e0a714df4f | 353502790914624 |
| outbound.manual_bin.work_admission_decide@v1 | A000000628 | 21:26:51.352 | 21:26:51.399 | 21:26:52.333 | WORK_REQUIRED | APPLIED | 01a0da76-c2d8-777f-a7ad-0763e787ef41 | 353502872957504 |
| outbound.rack.departure_decide@v1 | 510002 | 21:27:23.079 | 21:27:23.123 | 21:27:23.921 | READY | APPLIED | 01a0da77-3ec9-7c71-b4bc-5c34563ba0a1 | 353503002341952 |
| outbound.bin.inbound_batch@v1 | 510024 | 21:28:18.441 | 21:28:21.359 | 21:28:22.245 | READY | APPLIED | 01a0da78-1710-7885-87b3-776c34191388 | 353503241241152 |
| outbound.bin.return_batch@v1 | 510024 | 21:28:51.588 | 21:29:01.419 | 21:29:02.240 | READY | APPLIED | 01a0da78-989f-714e-b282-2ebb34bc6bca | 353503405060672 |
| outbound.manual_bin.work_admission_decide@v1 | A000002456 | 21:29:18.022 | 21:29:21.359 | 21:29:22.210 | WORK_REQUIRED | APPLIED | 01a0da78-ffc6-78c2-8c84-0444708892db | 353503486853696 |
| outbound.rack.departure_decide@v1 | 510024 | 21:29:55.347 | 21:29:55.365 | 21:29:56.175 | READY | APPLIED | 01a0da79-9195-716b-b775-544eddaa389f | 353503625974336 |
| outbound.bin.inbound_batch@v1 | 510027 | 21:30:49.610 | 21:30:51.358 | 21:30:52.210 | READY | APPLIED | 01a0da7a-6591-7842-b591-b49c81944c25 | 353503855493696 |
| outbound.rack.departure_decide@v1 | 510027 | 21:31:34.396 | 21:31:34.423 | 21:31:35.241 | READY | APPLIED | 01a0da7b-147f-7184-871d-64aab501b919 | 353504031744576 |
| outbound.manual_bin.work_admission_decide@v1 | A000002701 | 21:32:21.351 | 21:32:21.391 | 21:32:22.282 | WORK_REQUIRED | APPLIED | 01a0da7b-cbe7-7689-a50f-a7873987f821 | 353504224420416 |
| outbound.bin.return_batch@v1 | 510035 | 21:32:27.912 | 21:32:31.355 | 21:32:32.242 | READY | APPLIED | 01a0da7b-e592-72b9-8799-486c647f2fb7 | 353504265212480 |
| outbound.rack.departure_decide@v1 | 510035 | 21:32:32.394 | 21:32:32.430 | 21:32:33.310 | READY | APPLIED | 01a0da7b-f70d-7d55-9ac0-3fb9a9a62634 | 353504269595200 |
| outbound.bin.return_batch@v1 | 510035 | 21:33:06.478 | 21:33:11.356 | 21:33:12.196 | READY | RECONCILING | 01a0da7c-7c38-76c6-9cb9-e1998aa7c90e | 353504428864064 |
| outbound.bin.return_batch@v1 | 510035 | 21:33:12.312 | 21:33:21.360 | 21:33:22.250 | READY | APPLIED | 01a0da7c-9303-7fde-b2a9-d6fafcb1d5a0 | 353504470053440 |
| outbound.bin.inbound_batch@v1 | 510034 | 21:35:06.077 | 21:35:11.352 | 21:35:12.257 | READY | APPLIED | 01a0da7e-4f63-7cdc-b33a-7ee4791e20c5 | 353504920633920 |
| outbound.rack.departure_decide@v1 | 510034 | 21:35:43.981 | 21:35:44.032 | 21:35:44.853 | READY | APPLIED | 01a0da7e-e36f-7939-9408-7b7a038bebb0 | 353505054155328 |
| outbound.manual_bin.work_admission_decide@v1 | A000000341 | 21:36:11.347 | 21:36:13.257 | 21:36:14.288 | WORK_REQUIRED | APPLIED | 01a0da7f-4e53-78a2-abf1-fd78c492533f | 353505174770240 |
| outbound.picking_task.completion_confirm@v1 | A950FC259A78C43DB8D6D82FC110C8B03 | 21:36:56.497 | 21:37:01.360 | 21:37:02.393 | COMPLETED | APPLIED | 01a0da7f-fef5-7587-8b33-8a767c27625d | 353505371771456 |
| outbound.rack.departure_decide@v1 | 610017 | 21:37:06.473 | 21:37:06.518 | 21:37:07.370 | READY | APPLIED | 01a0da80-25b5-7cc6-aa20-cc4a7cbb1279 | 353505392149056 |
| workline.return_buffer.drain_rack_decide@v1 | 1 | 21:37:16.459 | 21:37:21.355 | 21:37:22.282 | READY | APPLIED | 01a0da80-4cb5-78c4-a671-1b3e6af10d0d | 353505453228608 |
| outbound.bin.return_batch@v1 | 510001 | 21:41:42.471 | 21:41:51.376 | 21:41:52.271 | READY | APPLIED | 01a0da84-5bd3-71b9-8d6c-28d4be3a9c55 | 353506559111744 |
| outbound.rack.departure_decide@v1 | 510001 | 21:41:52.443 | 21:41:52.473 | 21:41:53.368 | READY | APPLIED | 01a0da84-82c0-7473-872b-227f4108d2a4 | 353506563600960 |

## 5. WMS→WES 业务事件

 issued → prepare → plan_delta → 六次 work_completed → completion_confirm 构成业务主线。取消事件是附带异常，不作为完成依据。

| 接收时间 | operation | 箱/作用域 | 应用 | operation_id | Evidence ID |
| --- | --- | --- | --- | --- | --- |
| 21:16:47.757 | outbound.picking_task.issued@v1 | 任务 | APPLIED | 01a0da6d-8cb3-71bd-9966-f810ca25a76c | 353500396606016 |
| 21:17:24.707 | outbound.picking_task.plan_delta@v1 | 任务 | APPLIED | 01a0da6e-1d0f-7761-98ea-681663532ddc | 353500547936832 |
| 21:22:59.942 | outbound.manual_bin.work_completed@v1 | A000002231 | APPLIED | 01a0da73-3a8f-7232-b157-e8774d99a2b4 | 353501921071680 |
| 21:24:30.918 | outbound.manual_bin.work_completed@v1 | A000002165 | APPLIED | 01a0da74-9df0-75d0-8391-c3b07ca07c64 | 353502293701184 |
| 21:27:02.323 | outbound.manual_bin.work_completed@v1 | A000000628 | APPLIED | 01a0da76-ed5d-7370-8297-e8e242e6eb0c | 353502913856064 |
| 21:32:04.324 | outbound.manual_bin.work_completed@v1 | A000002456 | APPLIED | 01a0da7b-890d-7e84-84df-f25362e22ffa | 353504150852160 |
| 21:32:34.959 | outbound.manual_bin.work_completed@v1 | A000002701 | APPLIED | 01a0da7c-00b9-73e3-9492-200d48a086ae | 353504276345408 |
| 21:36:46.642 | outbound.manual_bin.work_completed@v1 | A000000341 | APPLIED | 01a0da7f-d7db-77b9-9574-b600b0567d0b | 353505307230784 |
| 21:37:17.376 | outbound.picking_task.cancel@v1 | PLAN_MEMBERS | RECONCILING | 01a0da80-4fed-70a1-8abc-5b7e8449cf38 | 353505433109056 |

## 6. 全部 Transport（经 WMS 交给 RCS 执行）

共 30 个，全部 SUCCEEDED：12 个 BIN_MOVE（6 入站+6 退箱），8 个 CTU01，8 个 CTU03，2 个 F01。本例没有 CTU02。每条 Transport 有唯一冻结的 submit_operation_id/正文；成功回调使用独立 operation_id、outcome_revision 1。42 条回调为 30 条 resulted 和 12 条 TARGET_PLACED，没有 SOURCE_PICKED，不能伪造该物理事件。

| 类型 | 对象 | 来源 | 目标 | 目标面 | 创建 | 成功回调收到 | 业务 step | Transport ID |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F01 | 610017 | 610017 | OUT65 | 90 | 21:17:24.814 | 21:21:02.415 | PICKING_TASK_TARGET_RACK_IN | transport-a0c37b6e-87ef-4246-812e-c3b0d420da3f |
| CTU01 | 510001 | 510001 | KT16 | 90 | 21:17:24.844 | 21:23:10.940 | PICKING_TASK_BIN_SOURCE_RACK_IN | transport-4151c1b4-3fdf-441e-862f-e83079a1d53e |
| CTU01 | 510002 | 510002 | KT16 | 270 | 21:17:24.857 | 21:25:41.242 | PICKING_TASK_BIN_SOURCE_RACK_IN | transport-b13f7d14-f2ca-418d-9d67-01050ed60163 |
| CTU01 | 510017 | 510017 | KT16 | 270 | 21:17:24.869 | 21:21:37.794 | PICKING_TASK_BIN_SOURCE_RACK_IN | transport-9268bf86-4ef5-4aae-9628-be532cb6f2d0 |
| BIN_MOVE | A000002231 | 510017/270/510017B3F2C101 | CNV0301 | — | 21:21:42.353 | 21:22:15.027 | MANUAL_PICKING_INBOUND_BATCH | transport-0bd3a5cb-9516-49e1-9bed-32203e2464e9 |
| CTU03 | 510017 | 510017 | WH01 | — | 21:22:16.324 | 21:25:59.831 | MANUAL_PICKING_SOURCE_RACK_OUT | transport-ec1d41a2-1003-4c77-a622-74720dea648c |
| CTU01 | 510024 | 510024 | KT16 | 90 | 21:22:17.823 | 21:28:18.232 | PICKING_TASK_BIN_SOURCE_RACK_IN | transport-b4d51f41-3a49-41d3-aaad-c7e1945d5abd |
| BIN_MOVE | A000002165 | 510001/90/510001A2F1C101 | CNV0301 | — | 21:23:12.253 | 21:23:45.086 | MANUAL_PICKING_INBOUND_BATCH | transport-46aa7a40-25c1-4711-b6ab-184ff1b73516 |
| BIN_MOVE | A000002231 | CNV0302 | 510001/90/510001A2F1C101 | — | 21:23:52.251 | 21:24:45.182 | MANUAL_PICKING_RETURN_BATCH | transport-72162d28-07c9-4c7c-ab86-51eab311607b |
| CTU03 | 510001 | 510001 | WH01 | — | 21:24:46.085 | 21:28:25.906 | MANUAL_PICKING_SOURCE_RACK_OUT | transport-1838ce1c-6c40-44fc-940d-d45886eee220 |
| CTU01 | 510027 | 510027 | KT16 | 90 | 21:24:47.599 | 21:30:49.409 | PICKING_TASK_BIN_SOURCE_RACK_IN | transport-16b99869-3e98-43f3-85fb-812cc47e0293 |
| BIN_MOVE | A000000628 | 510002/270/510002B3F1C101 | CNV0301 | — | 21:25:52.438 | 21:26:24.873 | MANUAL_PICKING_INBOUND_BATCH | transport-dd4e7adc-8fee-4941-aa5a-95c8797184c1 |
| BIN_MOVE | A000002165 | CNV0302 | 510002/270/510002B3F1C101 | — | 21:26:32.387 | 21:27:23.246 | MANUAL_PICKING_RETURN_BATCH | transport-32059d22-f5dc-436f-8a45-15eb6f313f5d |
| CTU03 | 510002 | 510002 | WH01 | — | 21:27:24.087 | 21:29:17.856 | MANUAL_PICKING_SOURCE_RACK_OUT | transport-e7f7fba3-57e9-478f-993d-4f92d70ce203 |
| CTU01 | 510035 | 510035 | KT16 | 270 | 21:27:25.634 | 21:32:27.715 | MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN | transport-e327bb4b-e1bc-4022-aabb-61eb1c622d6a |
| BIN_MOVE | A000002456 | 510024/90/510024A4F2C101 | CNV0301 | — | 21:28:22.301 | 21:28:51.388 | MANUAL_PICKING_INBOUND_BATCH | transport-a4a57e5f-2459-41a1-a909-fa947fbba131 |
| BIN_MOVE | A000000628 | CNV0302 | 510024/90/510024A4F2C101 | — | 21:29:02.491 | 21:29:55.538 | MANUAL_PICKING_RETURN_BATCH | transport-137568f6-eba6-4aae-b47b-c375ed1eef44 |
| CTU03 | 510024 | 510024 | WH01 | — | 21:29:56.333 | 21:33:44.560 | MANUAL_PICKING_SOURCE_RACK_OUT | transport-9167379d-f507-476d-b0fb-56680419b01a |
| CTU01 | 510034 | 510034 | KT16 | 90 | 21:29:57.801 | 21:35:05.884 | PICKING_TASK_BIN_SOURCE_RACK_IN | transport-f53e31b3-f930-411b-91cf-53b147d6088c |
| BIN_MOVE | A000002701 | 510027/90/510027A3F2C101 | CNV0301 | — | 21:30:52.288 | 21:31:34.195 | MANUAL_PICKING_INBOUND_BATCH | transport-f2bcef20-989b-49e1-af47-48ca8f231be0 |
| CTU03 | 510027 | 510027 | WH01 | — | 21:31:35.376 | 21:33:47.248 | MANUAL_PICKING_SOURCE_RACK_OUT | transport-bad8d733-d70c-4af8-95bf-af8a38e43a91 |
| BIN_MOVE | A000002456 | CNV0302 | 510035/270/510035B2F1C101 | — | 21:32:32.318 | 21:33:21.667 | MANUAL_PICKING_RETURN_BATCH | transport-c3699422-b39a-4c91-9b15-a67fe09b8dba |
| BIN_MOVE | A000002701 | CNV0302 | 510035/270/510035B2F2C101 | — | 21:33:22.343 | 21:34:12.577 | MANUAL_PICKING_RETURN_BATCH | transport-7b714b34-7930-4785-b616-4825f73eb5d9 |
| CTU03 | 510035 | 510035 | WH01 | — | 21:34:12.416 | 21:36:04.991 | MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT | transport-59abc0ec-cb83-4a8b-b357-d14d75fcb0c6 |
| BIN_MOVE | A000000341 | 510034/90/510034A2F1C101 | CNV0301 | — | 21:35:12.302 | 21:35:43.803 | MANUAL_PICKING_INBOUND_BATCH | transport-5e3c50b6-b14c-4749-8e32-5427781718ee |
| CTU03 | 510034 | 510034 | WH01 | — | 21:35:44.970 | 21:38:28.144 | MANUAL_PICKING_SOURCE_RACK_OUT | transport-2d011e5b-8d27-49ad-b391-4f82131780e5 |
| F01 | 610017 | 610017 | T_OUT | — | 21:37:07.446 | 21:41:18.964 | MANUAL_PICKING_TRANSFER_RACK_OUT | transport-628b85d9-bce8-4eb9-8a25-0a4ddbd6942f |
| CTU01 | 510001 | 510001 | KT16 | 270 | 21:37:22.384 | 21:41:42.222 | MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN | transport-112d47c1-8300-429f-ad97-81a02e775d2b |
| BIN_MOVE | A000000341 | CNV0302 | 510001/270/510001B4F2C101 | — | 21:41:52.361 | 21:42:50.154 | MANUAL_PICKING_RETURN_BATCH | transport-adfb818c-da96-471e-8fb8-ecbed2389e5d |
| CTU03 | 510001 | 510001 | WH01 | — | 21:42:49.870 | 21:46:22.099 | MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT | transport-e2f1e84c-0c66-43ea-8e46-e254d8de32f8 |

### 离场事件优先的实测基线

下表分开计算请求排队、WMS 收发和 READY 后现场依赖等待；毫秒为本次样本，不直接设为所有现场的硬 SLA。

| 货架 | 请求落库→派发 ms | 派发→READY 落库 ms | READY→创建离场 ms | 创建离场 |
| --- | --- | --- | --- | --- |
| 510017 | 38.1 | 874.5 | 169.8 | 21:22:16.324 |
| 510001 | 37.4 | 876.9 | 160.6 | 21:24:46.085 |
| 510002 | 44.1 | 798.4 | 166.2 | 21:27:24.087 |
| 510024 | 18.5 | 809.7 | 158.6 | 21:29:56.333 |
| 510027 | 26.8 | 817.5 | 135.4 | 21:31:35.376 |
| 510035 | 35.4 | 880.7 | 99105.3 | 21:34:12.416 |
| 510034 | 50.8 | 820.8 | 117.4 | 21:35:44.970 |
| 610017 | 45.1 | 851.8 | 75.8 | 21:37:07.446 |
| 510001 | 30.4 | 894.7 | 56502.1 | 21:42:49.870 |

`510035` 在 READY 后等待约 99 秒，最后一箱 A000002701 的 TARGET_PLACED 于 21:34:12.283 收到，CTU03 于 21:34:12.416 创建。末轮 `510001/270` 等待约 56.5 秒，A000000341 的 TARGET_PLACED 于 21:42:49.757 收到，CTU03 于 21:42:49.870 创建。两者分别约 133 ms、114 ms 后推进；这是当前现场依赖解除，不是等待旧历史或下一轮轮询。Transport 最终 SUCCEEDED 稍后到达不反向否定已经收到的放置事实。

## 7. 全部 ECS 扫码与方向命令

每个 SCAN 触发一次本轮方向命令；ACK 是接纳，SUCCESS 才是完成。本表由真实 Evidence/Command 关联，不根据箱号猜测命令。

| 箱号 | 点位 | 实收条码 | 扫码接收 | MOVE_FORWARD 创建 | ACK 收到 | 成功处理 | command_code | 扫码 Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A000002231 | SCAN1 | A000002231-B | 21:22:24.781 | 21:22:24.899 | 21:22:25.032 | 21:22:27.658 | 01a0da72-b203-7baa-896b-73f477989f8a | 353501777031744 |
| A000002231 | SCAN2 | A000002231-C | 21:22:38.793 | 21:23:00.011 | 21:23:00.128 | 21:23:04.054 | 01a0da73-3b2b-7de9-b7be-85d841e59835 | 353501834424896 |
| A000002231 | SCAN3 | A000002231-B | 21:23:13.605 | 21:23:13.693 | 21:23:13.802 | 21:23:17.267 | 01a0da73-709d-79a1-bbfe-09e248883227 | 353501977014848 |
| A000002231 | SCAN4 | A000002231-B | 21:23:19.198 | 21:23:19.280 | 21:23:19.516 | 21:23:22.776 | 01a0da73-8670-79b6-85b0-4cf7a688f0c7 | 353501999923776 |
| A000002165 | SCAN1 | A000002165-B | 21:23:55.177 | 21:23:55.280 | 21:23:55.383 | 21:23:57.887 | 01a0da74-1310-78c1-9935-9b4c863e519f | 353502147293760 |
| A000002165 | SCAN2 | A000002165-C | 21:24:09.196 | 21:24:30.974 | 21:24:31.096 | 21:24:35.175 | 01a0da74-9e7e-79c2-865b-c5add9b1b81d | 353502204715584 |
| A000002165 | SCAN3 | A000002165-B | 21:24:44.502 | 21:24:44.591 | 21:24:44.698 | 21:24:48.151 | 01a0da74-d3af-7f39-bd76-83260c4229d9 | 353502349328960 |
| A000002165 | SCAN4 | A000002165-B | 21:24:49.848 | 21:24:49.935 | 21:24:50.021 | 21:24:53.598 | 01a0da74-e88f-79af-ac7c-bb27baec1505 | 353502371222080 |
| A000000628 | SCAN1 | A000000628-B | 21:26:34.861 | 21:26:34.970 | 21:26:35.074 | 21:26:37.705 | 01a0da76-82da-79e4-ba1e-4ecc9674fc7e | 353502801355328 |
| A000000628 | SCAN2 | A000000628-C | 21:26:49.359 | 21:27:02.379 | 21:27:02.474 | 21:27:06.309 | 01a0da76-edeb-776f-9ae8-b321b46ba7ca | 353502860739136 |
| A000000628 | SCAN3 | A000000628-B | 21:27:15.779 | 21:27:15.858 | 21:27:15.967 | 21:27:19.650 | 01a0da77-2292-7bf8-8211-1dfd21cc76be | 353502968959552 |
| A000000628 | SCAN4 | A000000628-B | 21:27:21.304 | 21:27:21.435 | 21:27:21.529 | 21:27:24.886 | 01a0da77-385b-7fef-a8aa-b2865050685b | 353502991589952 |
| A000002456 | SCAN1 | A000002456-B | 21:29:01.250 | 21:29:01.354 | 21:29:02.361 | 21:29:04.726 | 01a0da78-beaa-79de-bbe2-fef77d09923f | 353503400964672 |
| A000002456 | SCAN2 | A000002456-C | 21:29:16.373 | 21:32:04.383 | 21:32:04.512 | 21:32:08.427 | 01a0da7b-899f-74b1-8625-e4ac5104852d | 353503462912576 |
| A000002456 | SCAN3 | A000002456-B | 21:32:17.898 | 21:32:17.980 | 21:32:18.272 | 21:32:22.316 | 01a0da7b-bebc-78c8-9f9f-d222341bb76c | 353504206438976 |
| A000002456 | SCAN4 | A000002456-B | 21:32:23.407 | 21:32:23.486 | 21:32:23.582 | 21:32:26.980 | 01a0da7b-d43e-7aa6-94ab-399969c87a5f | 353504229003840 |
| A000002701 | SCAN1 | A000002701-B | 21:31:44.251 | 21:31:44.351 | 21:31:44.439 | 21:31:47.126 | 01a0da7b-3b60-79b3-83c4-1630ba041f6f | 353504068620864 |
| A000002701 | SCAN2 | A000002701-C | 21:32:20.063 | 21:32:35.020 | 21:32:35.140 | 21:32:39.101 | 01a0da7c-014c-71fd-8981-be30a6a7d9f1 | 353504215302720 |
| A000002701 | SCAN3 | A000002701-B | 21:32:48.628 | 21:32:48.700 | 21:32:48.778 | 21:32:52.286 | 01a0da7c-36bc-738b-b7e5-5369a0de1f97 | 353504332313152 |
| A000002701 | SCAN4 | A000002701-B | 21:32:54.152 | 21:32:54.229 | 21:32:54.335 | 21:32:57.718 | 01a0da7c-4c55-734e-8130-94e8e51ff227 | 353504354935360 |
| A000000341 | SCAN1 | A000000341-B | 21:35:53.864 | 21:35:53.955 | 21:35:54.053 | 21:35:56.563 | 01a0da7f-0a63-74e8-9874-c0540fbffbd6 | 353505091031616 |
| A000000341 | SCAN2 | A000000341-C | 21:36:08.216 | 21:36:46.711 | 21:36:46.840 | 21:36:50.634 | 01a0da7f-d877-76c7-a8d1-1724c3714629 | 353505149825600 |
| A000000341 | SCAN3 | A000000341-B | 21:37:00.207 | 21:37:00.295 | 21:37:00.802 | 21:37:04.282 | 01a0da80-0d87-78b8-8eee-73985bdc95ec | 353505362776640 |
| A000000341 | SCAN4 | A000000341-B | 21:37:06.171 | 21:37:06.257 | 21:37:06.370 | 21:37:09.734 | 01a0da80-24d1-7391-9bee-afd27eb35114 | 353505387205184 |

## 8. 异常与恢复分支：不能省略

1. 21:33:06.479 创建的 return_batch `01a0da7c-7c38-76c6-9cb9-e1998aa7c90e` 将 A000002701 指向 `510035B2F1C101`，与 A000002456 的目标相同；响应 Evidence `353504428864064` 为 RECONCILING，未形成该冲突目标的第二条 Transport。随后新 operation `01a0da7c-9303-7fde-b2a9-d6fafcb1d5a0` 分配 `510035B2F2C101` 并成功执行。验收应保留“冲突分配不执行，正确重新分配可继续”的分支。仅凭这份数据库快照不能证明重新求值完全无外部干预；审计窗口未发现结构化 action 记录也不是无人干预证明。
2. 21:37:17.377 收到 PLAN_MEMBERS 取消 `510034/90/rev1`，Evidence `353505433109056` 保持 RECONCILING；当时任务已业务完成，源成员 cancelled_evidence_id 仍为空。它不是本任务成功完成的必要事件，不能纳入正常验收期望“取消已应用”。应作为单独待解释项保留。
3. A000000341 在任务业务完成后仍等待退箱：21:37:16 请求 drain，21:41:42 新架到位，随后退箱成功，最后 CTU03 成功于 21:46:22 收到。验收必须覆盖父任务完成不截断既有 WorkLine 退箱义务。

## 9. 后续验收步骤与断言

在隔离 Mock/验收环境回放，不向现场发送此快照。生成新 task_id、operation_id、Transport/command/event 身份；维护原有引用对应关系，时间使用单调的新时间。业务重复测试复用本轮相同身份及相同正文，漂移测试才改变正文；不得把现场历史身份直接用于新一轮。固定请求 JSON 在 evidence.json 中，不通过猜测重建。

| 编号 | 场景 | 验收断言 | 证据级别 |
| --- | --- | --- | --- |
| G01 | issued/prepare/plan_delta | 一个任务、revision 1、精确六个来源面及一个目标面；相同 issued/plan 重投不重复建任务/搬运。 | 现场主线；重复重投须补验 |
| G02 | 来源架并发进场 | 按配置窗口提交；实际先到 510017 时可先投料，不按数组首项阻塞；离场 ACCEPTED 后释放原进场名额。 | 现场主线 |
| G03 | 六个入站 BIN_MOVE | 箱/来源槽/目标 CNV0301 与 WMS 决定一致；保存 TARGET_PLACED 和成功事实。 | 现场主线 |
| G04 | SCAN1 独立前进 | 有效本箱入站依据满足即前进，不新增目标架门禁；另注入目标架仍 ACCEPTED 的场景验收独立性。 | 六次正常前进已观察；目标架未到分支需补验 |
| G05 | SCAN2 与 PDA | 六次 WORK_REQUIRED；完成通知 NORMAL 前不正常放行，完成后每箱一条 MOVE_FORWARD；保留实收扫码后缀。 | 现场主线 |
| G06 | SCAN3/SCAN4 | 有效现场扫码建立/推进本轮 Return；首次 SCAN4 顺序冻结，SCAN4 命令成功后 READY。 | 现场主线 |
| G07 | 机会式退箱 | 来源面完成投料后，可将前箱放回当前空槽；精确沿用 WMS 的 rack/face/slot。 | 现场主线 |
| G08 | 独立 drain | 需要时请求 drain_rack_decide，按返回架/面入场；不要求 PickingTask 仍 EXECUTING。 | 两次补架及末箱收尾 |
| G09 | 冲突分配恢复 | A000002701 不执行冲突 B2F1 槽目标，重新分配 B2F2 后仅一个实际退箱 Transport。 | 恢复成功；无人干预需另验 |
| G10 | 离场事件优先 | 创建确认提交后唤醒派发；READY 提交后唤醒 Plan；无现场依赖即创建/提交 CTU03，不人为等 10 秒。 | 9 次离场时序 |
| G11 | 离场真实依赖 | 510035 和末轮 510001 必须等最后一箱 TARGET_PLACED，再推进；禁止用 ACK 当放置完成。 | 现场主线 |
| G12 | 任务完成与收尾 | completion_confirm COMPLETED 后任务 EXECUTION_COMPLETED；目标 F01 出场、末箱退箱与 drain CTU03 继续。 | 现场主线 |
| G13 | 缺少 SOURCE_PICKED | 本例 0 条该事件，仍能闭合交接并让后续运行；不得补造物理取走时间。 | 现场主线 |
| G14 | 重复/漏唤醒/重启 | 重复唤醒不重复 HTTP 义务或 CTU03；事务回滚不唤醒；丢唤醒后 10 秒扫描恢复原身份。 | 由现有事务/worker 测试及故障注入另验 |
| G15 | 整体闭环 | 6 CLOSED Passage、6 EXITED Return、24 SUCCEEDED DeviceCommand、30 SUCCEEDED Transport；异常两条 Evidence 仍单列。 | 现场计数核对 |

自动化所有权：插件流程放 `workline_plugins/manual-picking/tests/`；WMS wire/可靠机制放 `tests/contracts/wms_adapter/`、`tests/integration/wms_adapter/`；事务唤醒放 `tests/core/test_transaction_wakeup.py`。本次交付是验收说明和真实证据归档，未新增可执行 replay runner，也不替代未来故障注入测试。

## 10. 证据检索与完整性

`confirmations[].response_evidence_id → evidences[].id`；`bindings[].client_request_id → transports[].client_request_id`；`transports[].transport_task_id → transport_evidences[].transport_task_id`；`passages/returns.*_command_code → commands[].command_code`；`ecs_ingress[].request_body` 保留原 ECS 请求正文。每个 Transport 的 submit_request_body_digest 可以直接对冻结 UTF-8 正文作 SHA-256 校验。

证据文件 SHA-256：`9e9a860f889ef5f45493ed4fc5cf262f6401f386ed3e601c9bc6b4e5b5f3cc50`。本文件与证据文件是观察记录；后续规范变化应显式修订验收规则，不覆盖原现场快照。
