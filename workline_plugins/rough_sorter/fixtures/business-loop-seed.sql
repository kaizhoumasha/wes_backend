-- 初始化单成功路径前置环境：静态 WorkLine/Device 主数据，以及 STOPPED 投影。
-- direct SQL 只设置前置条件；当前插件执行合同必须由受保护公开 START 发布。
BEGIN;

INSERT INTO wes_biz.work_lines (
    id, version, created_at, is_deleted, line_code, line_name, line_type, is_active, plugin_key,
    config, runtime_config_json, diagnostic_profile, run_mode, device_contracts, position_bindings
) VALUES (
    9001, 0, '__NOW__', false, 'RS-E2E-LINE', 'Rough sorter E2E', 'AUTO', false, 'rough_sorter',
    '__ROUGH_SORTER_CONFIG__'::json, '{}', '{}', 'AUTO', '{}', '{}'
);

INSERT INTO wes_biz.devices (
    id, version, created_at, is_deleted, device_code, device_name, work_line_id,
    is_active, sort_order, diagnostic_profile, endpoint_base_url
) VALUES
    (9101, 0, '__NOW__', false, 'RS-E2E-MEASUREMENT', 'Measurement', 9001, true, 1, '{}', '__ECS_ENDPOINT__'),
    (9102, 0, '__NOW__', false, 'RS-E2E-TRANSFER', 'Transfer', 9001, true, 2, '{}', '__ECS_ENDPOINT__'),
    (9103, 0, '__NOW__', false, 'RS-E2E-PLACEMENT', 'Placement', 9001, true, 3, '{}', '__ECS_ENDPOINT__');

INSERT INTO wes_runtime.workline_runtime_status_projections (
    id, workline_id, runtime_status, source, stopped_at, stopped_reason, evidence_json
) VALUES (
    9201, 9001, 'STOPPED', 'rough-sorter-e2e-fixture', '__NOW__', 'E2E_PUBLIC_START_REQUIRED', '{}'
);

INSERT INTO wes_biz.workline_rack_positions (
    id, created_at, workline_id, workline_code, position_code, position_name, position_role,
    allowed_rack_kind, capacity, logic_location_code, priority, enabled, metadata_json
) VALUES (
    9501, '__NOW__', 9001, 'RS-E2E-LINE', 'RACK-WORK', 'Rack work position',
    'SMT_CLASSIFIER_SINGLE_RACK_WORK', 'SINGLE_LAYER', 1, 'PIPELINE_OUTLET', 100, true, '{}'
);

COMMIT;
