-- 初始化单成功路径前置环境：静态 WorkLine/Epoch 配置，以及 DeviceStatusObservation、RackPlacement 两个可信运行态投影。
-- 字段来源：获批 rough-sorter 三设备/四位置 Epoch 合同、当前架投影和 ECS status 公共合同。
-- direct SQL 只设置前置条件，不证明投影 owner；不写本次 material execution、evidence、command 或 confirmation 状态。
BEGIN;

INSERT INTO wes_biz.work_lines (
    id, version, created_at, is_deleted, line_code, line_name, line_type, is_active,
    config, runtime_config_json, diagnostic_profile, run_mode
) VALUES (
    9001, 0, '__NOW__', false, 'RS-E2E-LINE', 'Rough sorter E2E', 'AUTO', true,
    '{}', '{}', '{}', 'AUTO'
);

INSERT INTO wes_biz.devices (
    id, version, created_at, is_deleted, device_code, device_name, work_line_id,
    is_active, sort_order, device_role, role_index, diagnostic_profile
) VALUES
    (9101, 0, '__NOW__', false, 'RS-E2E-MEASUREMENT', 'Measurement', 9001, true, 1, 'MEASUREMENT_DEVICE', 1, '{}'),
    (9102, 0, '__NOW__', false, 'RS-E2E-TRANSFER', 'Transfer', 9001, true, 2, 'TRANSFER_DEVICE', 1, '{}'),
    (9103, 0, '__NOW__', false, 'RS-E2E-PLACEMENT', 'Placement', 9001, true, 3, 'PLACEMENT_DEVICE', 1, '{}');

INSERT INTO wes_biz.line_run_epochs (
    id, version, created_at, epoch_code, workline_id, topology_digest,
    configuration_digest, status, started_at, plugin_key, plugin_version, flow_mode
) VALUES (
    9201, 0, '__NOW__', 'RS-E2E-EPOCH', 9001, '__TOPOLOGY_DIGEST__',
    '__CONFIGURATION_DIGEST__', 'ACTIVE', '__NOW__', 'rough_sorter', '1.0.0', 'ROUGH_SORT_INBOUND'
);

INSERT INTO wes_biz.line_run_epoch_device_bindings (
    id, version, created_at, line_run_epoch_id, device_id, device_code, contract_key,
    contract_version, status_max_age_ms, command_timeout_ms, device_role
) VALUES
    (9301, 0, '__NOW__', 9201, 9101, 'RS-E2E-MEASUREMENT', 'rough_sorter.measurement_device', '1.0', 600000, 30000, 'MEASUREMENT_DEVICE'),
    (9302, 0, '__NOW__', 9201, 9102, 'RS-E2E-TRANSFER', 'rough_sorter.transfer_device', '1.0', 600000, 30000, 'TRANSFER_DEVICE'),
    (9303, 0, '__NOW__', 9201, 9103, 'RS-E2E-PLACEMENT', 'rough_sorter.placement_device', '1.0', 600000, 30000, 'PLACEMENT_DEVICE');

INSERT INTO wes_biz.line_run_epoch_position_bindings (
    id, version, created_at, line_run_epoch_id, position_role, location_id, location_type
) VALUES
    (9401, 0, '__NOW__', 9201, 'MEASUREMENT_POSITION', 'MEASUREMENT-1', 'MEASUREMENT_POSITION'),
    (9402, 0, '__NOW__', 9201, 'PIPELINE_INLET', 'INLET-1', 'PIPELINE_INLET'),
    (9403, 0, '__NOW__', 9201, 'PIPELINE_OUTLET', 'OUTLET-1', 'PIPELINE_OUTLET'),
    (9404, 0, '__NOW__', 9201, 'NG_POSITION', 'NG-1', 'NG_POSITION');

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
