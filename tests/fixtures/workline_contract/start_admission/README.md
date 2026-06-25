# WorkLine Contract Fixtures（P0-003）

Phase 0 BC fixture 集。Phase 1+ 实施时补充完整 JSON fixture，当前提供 case 索引。

## 目录与 BC 映射

| 目录 | BC | 说明 |
| --- | --- | --- |
| `start_admission/` | BC-01 | manifest、device roles、active projection、WMS/ECS 可用性 |
| `runtime_snapshot/` | BC-02 | session_with_inbox_hold_intent（Phase 1 解锁） |
| `handoff/` | BC-03 | callback_evidence、no_evidence |
| `resource_projection/` | BC-04 | duplicate_active_owner |
| `rough_sorter_inbound/` | BC-05 | happy_path（Phase 4 解锁） |
| `full_box_exchange/` | BC-06 | pre_diversion（Phase 3/4 解锁） |
| `sorter_inbound/` | BC-07 | object_pipeline（Phase 1 解锁） |
| `device_event/` | BC-08 | missing_event_id |
| `wms_cache/` | BC-09 | query_cache_hit |
| `event_push/` | BC-10 | command_like_response |

## fixture schema

每个 fixture JSON 必须声明 `case_id`、`provider_code`（如适用）、`expected_port`。
Phase 0 强制 pass 项（BC-01/03/04/08/09/10）的断言内联在 tests/support/workline_contracts.py，
fixture 在 Phase 1+ 接入真实 runtime 后补全为数据驱动测试。
