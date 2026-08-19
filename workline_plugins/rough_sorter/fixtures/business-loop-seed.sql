-- 初始化单成功路径前置环境：静态 WorkLine/Device 主数据，以及 STOPPED、DeviceStatusObservation、RackPlacement 可信投影。
-- direct SQL 只设置前置条件；Epoch 与 bindings 必须由受保护公开 START 创建。
BEGIN;

INSERT INTO wes_biz.work_lines (
    id, version, created_at, is_deleted, line_code, line_name, line_type, is_active,
    config, runtime_config_json, diagnostic_profile, run_mode
) VALUES (
    9001, 0, '__NOW__', false, 'RS-E2E-LINE', 'Rough sorter E2E', 'AUTO', true,
    '__ROUGH_SORTER_CONFIG__'::json, '{}', '{}', 'AUTO'
);

INSERT INTO wes_biz.devices (
    id, version, created_at, is_deleted, device_code, device_name, work_line_id,
    is_active, sort_order, device_role, role_index, diagnostic_profile, endpoint_base_url
) VALUES
    (9101, 0, '__NOW__', false, 'RS-E2E-MEASUREMENT', 'Measurement', 9001, true, 1, 'MEASUREMENT_DEVICE', 1, '{}', '__ECS_ENDPOINT__'),
    (9102, 0, '__NOW__', false, 'RS-E2E-TRANSFER', 'Transfer', 9001, true, 2, 'TRANSFER_DEVICE', 1, '{}', '__ECS_ENDPOINT__'),
    (9103, 0, '__NOW__', false, 'RS-E2E-PLACEMENT', 'Placement', 9001, true, 3, 'PLACEMENT_DEVICE', 1, '{}', '__ECS_ENDPOINT__');

INSERT INTO wes_runtime.workline_runtime_status_projections (
    id, workline_id, runtime_status, source, stopped_at, stopped_reason, evidence_json
) VALUES (
    9201, 9001, 'STOPPED', 'rough-sorter-e2e-fixture', '__NOW__', 'E2E_PUBLIC_START_REQUIRED', '{}'
);

INSERT INTO wes_biz.device_status_observations (
    version, created_at, device_code, contract_key, contract_version, mode, status,
    device_timestamp, received_at, payload_digest, raw_payload
) VALUES
    (0, '__NOW__', 'RS-E2E-MEASUREMENT', 'rough_sorter.measurement_device', '1.0', 'AUTO', 'IDLE', __NOW_MS__, '__NOW__', repeat('1', 64), '{}'),
    (0, '__NOW__', 'RS-E2E-TRANSFER', 'rough_sorter.transfer_device', '1.0', 'AUTO', 'IDLE', __NOW_MS__, '__NOW__', repeat('2', 64), '{}'),
    (0, '__NOW__', 'RS-E2E-PLACEMENT', 'rough_sorter.placement_device', '1.0', 'AUTO', 'IDLE', __NOW_MS__, '__NOW__', repeat('3', 64), '{}');

INSERT INTO wes_biz.workline_rack_positions (
    id, created_at, workline_id, workline_code, position_code, position_name, position_role,
    allowed_rack_kind, capacity, logic_location_code, priority, enabled, metadata_json
) VALUES (
    9501, '__NOW__', 9001, 'RS-E2E-LINE', 'RACK-WORK', 'Rack work position',
    'SMT_CLASSIFIER_SINGLE_RACK_WORK', 'SINGLE_LAYER', 1, 'OUTLET-1', 100, true, '{}'
);

INSERT INTO wes_biz.resource_rack_placements (
    id, created_at, rack_code, placement_status, source_system, source_event_id, started_at,
    rack_kind, workline_id, workline_code, position_code, position_role, logic_location_code
) VALUES (
    9601, '__NOW__', 'RACK-1', 'ARRIVED', 'WMS', 'RS-E2E-RACK-ARRIVED', '__NOW__',
    'SINGLE_LAYER', 9001, 'RS-E2E-LINE', 'RACK-WORK', 'SMT_CLASSIFIER_SINGLE_RACK_WORK', 'OUTLET-1'
);

COMMIT;
