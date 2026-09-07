"""retire bin execution and line run epoch

Revision ID: 93deacda8c9c
Revises: 5098dc1b2b63
Create Date: 2026-09-07 11:18:33.082879+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "93deacda8c9c"
down_revision: Union[str, Sequence[str], None] = "5098dc1b2b63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Replace the unpublished schema; never discard execution evidence implicitly."""
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM wes_biz.bin_executions) OR EXISTS (SELECT 1 FROM wes_biz.device_commands) OR EXISTS (SELECT 1 FROM wes_biz.inbound_evidences) OR EXISTS (SELECT 1 FROM wes_biz.line_run_epoch_device_bindings) OR EXISTS (SELECT 1 FROM wes_biz.line_run_epoch_position_bindings) OR EXISTS (SELECT 1 FROM wes_biz.line_run_epochs) OR EXISTS (SELECT 1 FROM wes_biz.material_executions) OR EXISTS (SELECT 1 FROM wes_biz.picking_tasks) OR EXISTS (SELECT 1 FROM wes_biz.position_projections) OR EXISTS (SELECT 1 FROM wes_biz.transport_decision_bindings) OR EXISTS (SELECT 1 FROM wes_biz.wms_confirmations) OR EXISTS (SELECT 1 FROM wes_runtime.transport_tasks) OR EXISTS (SELECT 1 FROM wes_biz.work_lines WHERE is_active) THEN RAISE EXCEPTION 'WorkLine retirement requires an empty execution baseline and inactive work lines; rebuild the unpublished database before migration'; END IF; END $$"
    )
    op.execute(
        "ALTER TABLE wes_biz.device_commands DROP CONSTRAINT ck_device_commands_device_command_execution_context_complete"
    )
    op.execute(
        "ALTER TABLE wes_biz.device_commands DROP CONSTRAINT fk_device_commands_device_binding_id_line_run_epoch_dev_d412"
    )
    op.execute(
        "ALTER TABLE wes_biz.device_commands DROP CONSTRAINT fk_device_commands_line_run_epoch_id_line_run_epochs"
    )
    op.execute("ALTER TABLE wes_biz.device_commands DROP CONSTRAINT ux_device_commands_execution_identity")
    op.execute("DROP INDEX wes_biz.ix_wes_biz_device_commands_device_binding_id")
    op.execute(
        "ALTER TABLE wes_biz.inbound_evidences DROP CONSTRAINT fk_inbound_evidences_line_run_epoch_id_line_run_epochs"
    )
    op.execute("DROP INDEX wes_biz.ix_wes_biz_inbound_evidences_line_run_epoch_id")
    op.execute(
        "ALTER TABLE wes_biz.material_executions DROP CONSTRAINT fk_material_executions_line_run_epoch_id_line_run_epochs"
    )
    op.execute("DROP INDEX wes_biz.ix_material_executions_active_fifo")
    op.execute("DROP INDEX wes_biz.ix_material_executions_epoch_status")
    op.execute("DROP INDEX wes_biz.ix_wes_biz_material_executions_line_run_epoch_id")
    op.execute("ALTER TABLE wes_biz.picking_tasks DROP CONSTRAINT ck_picking_tasks_picking_task_binding_matches_status")
    op.execute("ALTER TABLE wes_biz.picking_tasks DROP CONSTRAINT fk_picking_tasks_line_run_epoch_id_line_run_epochs")
    op.execute("DROP INDEX wes_biz.ix_wes_biz_picking_tasks_line_run_epoch_id")
    op.execute(
        "ALTER TABLE wes_biz.position_projections DROP CONSTRAINT ck_position_projections_position_projection_bin_authority_valid"
    )
    op.execute(
        "ALTER TABLE wes_biz.position_projections DROP CONSTRAINT fk_position_projections_bin_execution_id_bin_executions"
    )
    op.execute(
        "ALTER TABLE wes_biz.position_projections DROP CONSTRAINT fk_position_projections_line_run_epoch_id_line_run_epochs"
    )
    op.execute("DROP INDEX wes_biz.ix_position_projection_epoch")
    op.execute("DROP INDEX wes_biz.ix_wes_biz_position_projections_bin_execution_id")
    op.execute("DROP INDEX wes_biz.ix_wes_biz_position_projections_line_run_epoch_id")
    op.execute("ALTER TABLE wes_biz.transport_decision_bindings DROP CONSTRAINT fk_transport_decision_bindings_epoch")
    op.execute(
        "ALTER TABLE wes_biz.transport_decision_bindings DROP CONSTRAINT ux_transport_decision_bindings_decision_identity"
    )
    op.execute("DROP INDEX wes_biz.ix_wes_biz_transport_decision_bindings_epoch_resource")
    op.execute(
        "ALTER TABLE wes_biz.wms_confirmations DROP CONSTRAINT ck_wms_confirmations_wms_confirmation_exactly_one_owner"
    )
    op.execute(
        "ALTER TABLE wes_biz.wms_confirmations DROP CONSTRAINT fk_wms_confirmations_bin_execution_id_bin_executions"
    )
    op.execute(
        "ALTER TABLE wes_biz.wms_confirmations DROP CONSTRAINT fk_wms_confirmations_line_run_epoch_id_line_run_epochs"
    )
    op.execute("DROP INDEX wes_biz.ix_wes_biz_wms_confirmations_bin_execution_id")
    op.execute("DROP INDEX wes_biz.ix_wes_biz_wms_confirmations_line_run_epoch_id")
    op.execute(
        "ALTER TABLE wes_runtime.transport_tasks DROP CONSTRAINT ck_transport_tasks_transport_execution_authority_all_or_none"
    )
    op.execute(
        "ALTER TABLE wes_runtime.transport_tasks DROP CONSTRAINT fk_transport_tasks_authority_bin_execution_id_bin_executions"
    )
    op.execute(
        "ALTER TABLE wes_runtime.transport_tasks DROP CONSTRAINT fk_transport_tasks_authority_line_run_epoch_id_line_run_epochs"
    )
    op.execute("DROP TABLE wes_biz.bin_executions")
    op.execute("DROP TABLE wes_biz.line_run_epoch_device_bindings")
    op.execute("DROP TABLE wes_biz.line_run_epoch_position_bindings")
    op.execute("DROP TABLE wes_biz.line_run_epochs")
    op.execute("ALTER TABLE wes_biz.device_commands DROP COLUMN device_binding_id")
    op.execute("ALTER TABLE wes_biz.device_commands DROP COLUMN line_run_epoch_id")
    op.execute("ALTER TABLE wes_biz.device_commands ADD COLUMN status_max_age_ms INTEGER")
    op.execute("ALTER TABLE wes_biz.device_commands ADD COLUMN workline_id INTEGER")
    op.execute("ALTER TABLE wes_biz.inbound_evidences DROP COLUMN line_run_epoch_id")
    op.execute("ALTER TABLE wes_biz.inbound_evidences ADD COLUMN workline_id INTEGER")
    op.execute("ALTER TABLE wes_biz.material_executions DROP COLUMN line_run_epoch_id")
    op.execute("ALTER TABLE wes_biz.picking_tasks DROP COLUMN line_run_epoch_id")
    op.execute("ALTER TABLE wes_biz.position_projections DROP COLUMN bin_execution_id")
    op.execute("ALTER TABLE wes_biz.position_projections DROP COLUMN line_run_epoch_id")
    op.execute("ALTER TABLE wes_biz.transport_decision_bindings DROP COLUMN line_run_epoch_id")
    op.execute("ALTER TABLE wes_biz.transport_decision_bindings ADD COLUMN workline_id BIGINT NOT NULL")
    op.execute("ALTER TABLE wes_biz.wms_confirmations DROP COLUMN bin_execution_id")
    op.execute("ALTER TABLE wes_biz.wms_confirmations DROP COLUMN line_run_epoch_id")
    op.execute("ALTER TABLE wes_biz.wms_confirmations ADD COLUMN workline_id BIGINT")
    op.execute("ALTER TABLE wes_biz.work_lines ADD COLUMN device_contracts JSON DEFAULT '{}' NOT NULL")
    op.execute("ALTER TABLE wes_biz.work_lines ALTER COLUMN device_contracts DROP DEFAULT")
    op.execute("ALTER TABLE wes_biz.work_lines ADD COLUMN flow_mode VARCHAR(100)")
    op.execute("ALTER TABLE wes_biz.work_lines ADD COLUMN plugin_version VARCHAR(50)")
    op.execute("ALTER TABLE wes_biz.work_lines ADD COLUMN position_bindings JSON DEFAULT '{}' NOT NULL")
    op.execute("ALTER TABLE wes_biz.work_lines ALTER COLUMN position_bindings DROP DEFAULT")
    op.execute("ALTER TABLE wes_runtime.transport_tasks DROP COLUMN authority_bin_execution_id")
    op.execute("ALTER TABLE wes_runtime.transport_tasks DROP COLUMN authority_line_run_epoch_id")
    op.execute(
        "ALTER TABLE wes_biz.device_commands ADD CONSTRAINT ck_device_commands_device_command_execution_context_complete CHECK (endpoint_base_url IS NOT NULL AND command_timeout_ms IS NOT NULL AND ((execution_ref_type IN ('MANUAL_DEBUG', 'EVENT_DEBUG') AND workline_id IS NULL AND material_execution_id IS NULL) OR (execution_ref_type NOT IN ('MANUAL_DEBUG', 'EVENT_DEBUG') AND workline_id IS NOT NULL AND status_max_age_ms IS NOT NULL AND status_max_age_ms > 0)))"
    )
    op.execute(
        "ALTER TABLE wes_biz.device_commands ADD CONSTRAINT fk_device_commands_workline_id_work_lines FOREIGN KEY(workline_id) REFERENCES wes_biz.work_lines (id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.device_commands ADD CONSTRAINT ux_device_commands_execution_identity UNIQUE (workline_id, device_code, execution_ref_type, execution_ref_id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.inbound_evidences ADD CONSTRAINT fk_inbound_evidences_workline_id_work_lines FOREIGN KEY(workline_id) REFERENCES wes_biz.work_lines (id)"
    )
    op.execute("CREATE INDEX ix_wes_biz_inbound_evidences_workline_id ON wes_biz.inbound_evidences (workline_id)")
    op.execute(
        "CREATE INDEX ix_material_executions_active_fifo ON wes_biz.material_executions (workline_id, admission_received_at, admission_evidence_id, id) WHERE status <> 'CLOSED'"
    )
    op.execute(
        "CREATE INDEX ix_material_executions_workline_status ON wes_biz.material_executions (workline_id, status, id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.picking_tasks ADD CONSTRAINT ck_picking_tasks_picking_task_binding_matches_status CHECK ((status = 'QUEUED' AND workline_id IS NULL) OR (status IN ('PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED') AND workline_id IS NOT NULL))"
    )
    op.execute("CREATE INDEX ix_position_projection_workline ON wes_biz.position_projections (workline_id, id)")
    op.execute(
        "ALTER TABLE wes_biz.transport_decision_bindings ADD CONSTRAINT fk_transport_decision_bindings_workline FOREIGN KEY(workline_id) REFERENCES wes_biz.work_lines (id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.transport_decision_bindings ADD CONSTRAINT ux_transport_decision_bindings_decision_identity UNIQUE (workline_id, correlation_id, step)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_transport_decision_bindings_workline_resource ON wes_biz.transport_decision_bindings (workline_id, resource_fence_id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.wms_confirmations ADD CONSTRAINT ck_wms_confirmations_wms_confirmation_exactly_one_owner CHECK ((CASE WHEN material_execution_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN picking_task_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN workline_id IS NOT NULL THEN 1 ELSE 0 END) = 1)"
    )
    op.execute(
        "ALTER TABLE wes_biz.wms_confirmations ADD CONSTRAINT fk_wms_confirmations_workline_id_work_lines FOREIGN KEY(workline_id) REFERENCES wes_biz.work_lines (id)"
    )
    op.execute("CREATE INDEX ix_wes_biz_wms_confirmations_workline_id ON wes_biz.wms_confirmations (workline_id)")


def downgrade() -> None:
    """Replace the unpublished schema; never discard execution evidence implicitly."""
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM wes_biz.device_commands) OR EXISTS (SELECT 1 FROM wes_biz.inbound_evidences) OR EXISTS (SELECT 1 FROM wes_biz.material_executions) OR EXISTS (SELECT 1 FROM wes_biz.picking_tasks) OR EXISTS (SELECT 1 FROM wes_biz.position_projections) OR EXISTS (SELECT 1 FROM wes_biz.transport_decision_bindings) OR EXISTS (SELECT 1 FROM wes_biz.wms_confirmations) OR EXISTS (SELECT 1 FROM wes_runtime.transport_tasks) OR EXISTS (SELECT 1 FROM wes_biz.work_lines WHERE is_active) THEN RAISE EXCEPTION 'WorkLine retirement requires an empty execution baseline and inactive work lines; rebuild the unpublished database before migration'; END IF; END $$"
    )
    op.execute(
        "ALTER TABLE wes_biz.device_commands DROP CONSTRAINT ck_device_commands_device_command_execution_context_complete"
    )
    op.execute("ALTER TABLE wes_biz.device_commands DROP CONSTRAINT fk_device_commands_workline_id_work_lines")
    op.execute("ALTER TABLE wes_biz.device_commands DROP CONSTRAINT ux_device_commands_execution_identity")
    op.execute("ALTER TABLE wes_biz.inbound_evidences DROP CONSTRAINT fk_inbound_evidences_workline_id_work_lines")
    op.execute("DROP INDEX wes_biz.ix_wes_biz_inbound_evidences_workline_id")
    op.execute("DROP INDEX wes_biz.ix_material_executions_active_fifo")
    op.execute("DROP INDEX wes_biz.ix_material_executions_workline_status")
    op.execute("ALTER TABLE wes_biz.picking_tasks DROP CONSTRAINT ck_picking_tasks_picking_task_binding_matches_status")
    op.execute("DROP INDEX wes_biz.ix_position_projection_workline")
    op.execute(
        "ALTER TABLE wes_biz.transport_decision_bindings DROP CONSTRAINT fk_transport_decision_bindings_workline"
    )
    op.execute(
        "ALTER TABLE wes_biz.transport_decision_bindings DROP CONSTRAINT ux_transport_decision_bindings_decision_identity"
    )
    op.execute("DROP INDEX wes_biz.ix_wes_biz_transport_decision_bindings_workline_resource")
    op.execute(
        "ALTER TABLE wes_biz.wms_confirmations DROP CONSTRAINT ck_wms_confirmations_wms_confirmation_exactly_one_owner"
    )
    op.execute("ALTER TABLE wes_biz.wms_confirmations DROP CONSTRAINT fk_wms_confirmations_workline_id_work_lines")
    op.execute("DROP INDEX wes_biz.ix_wes_biz_wms_confirmations_workline_id")
    op.execute("ALTER TABLE wes_biz.device_commands DROP COLUMN status_max_age_ms")
    op.execute("ALTER TABLE wes_biz.device_commands DROP COLUMN workline_id")
    op.execute("ALTER TABLE wes_biz.device_commands ADD COLUMN device_binding_id INTEGER")
    op.execute("ALTER TABLE wes_biz.device_commands ADD COLUMN line_run_epoch_id INTEGER")
    op.execute("ALTER TABLE wes_biz.inbound_evidences DROP COLUMN workline_id")
    op.execute("ALTER TABLE wes_biz.inbound_evidences ADD COLUMN line_run_epoch_id INTEGER")
    op.execute("ALTER TABLE wes_biz.material_executions ADD COLUMN line_run_epoch_id INTEGER NOT NULL")
    op.execute("ALTER TABLE wes_biz.picking_tasks ADD COLUMN line_run_epoch_id INTEGER")
    op.execute("ALTER TABLE wes_biz.position_projections ADD COLUMN bin_execution_id BIGINT")
    op.execute("ALTER TABLE wes_biz.position_projections ADD COLUMN line_run_epoch_id INTEGER NOT NULL")
    op.execute("ALTER TABLE wes_biz.transport_decision_bindings DROP COLUMN workline_id")
    op.execute("ALTER TABLE wes_biz.transport_decision_bindings ADD COLUMN line_run_epoch_id BIGINT NOT NULL")
    op.execute("ALTER TABLE wes_biz.wms_confirmations DROP COLUMN workline_id")
    op.execute("ALTER TABLE wes_biz.wms_confirmations ADD COLUMN bin_execution_id BIGINT")
    op.execute("ALTER TABLE wes_biz.wms_confirmations ADD COLUMN line_run_epoch_id BIGINT")
    op.execute("ALTER TABLE wes_biz.work_lines DROP COLUMN device_contracts")
    op.execute("ALTER TABLE wes_biz.work_lines DROP COLUMN flow_mode")
    op.execute("ALTER TABLE wes_biz.work_lines DROP COLUMN plugin_version")
    op.execute("ALTER TABLE wes_biz.work_lines DROP COLUMN position_bindings")
    op.execute("ALTER TABLE wes_runtime.transport_tasks ADD COLUMN authority_bin_execution_id BIGINT")
    op.execute("ALTER TABLE wes_runtime.transport_tasks ADD COLUMN authority_line_run_epoch_id INTEGER")
    op.execute(
        "CREATE TABLE wes_biz.line_run_epochs (\n\tversion INTEGER DEFAULT '0' NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITHOUT TIME ZONE, \n\tid BIGSERIAL NOT NULL, \n\tcreated_by BIGINT, \n\tupdated_by BIGINT, \n\tepoch_code VARCHAR(100) NOT NULL, \n\tworkline_id INTEGER NOT NULL, \n\tplugin_key VARCHAR(100) NOT NULL, \n\tplugin_version VARCHAR(50) NOT NULL, \n\tflow_mode VARCHAR(100) NOT NULL, \n\ttopology_digest VARCHAR(64) NOT NULL, \n\tconfiguration_digest VARCHAR(64) NOT NULL, \n\tconfiguration_snapshot_json JSON NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tstarted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tclosed_at TIMESTAMP WITHOUT TIME ZONE, \n\tCONSTRAINT pk_line_run_epochs PRIMARY KEY (id), \n\tCONSTRAINT ck_line_run_epochs_line_run_epoch_status_valid CHECK (status IN ('ACTIVE', 'CLOSED')), \n\tCONSTRAINT ux_line_run_epochs_epoch_code UNIQUE (epoch_code), \n\tCONSTRAINT fk_line_run_epochs_workline_id_work_lines FOREIGN KEY(workline_id) REFERENCES wes_biz.work_lines (id)\n)"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_line_run_epochs_active_workline ON wes_biz.line_run_epochs (workline_id) WHERE status = 'ACTIVE'"
    )
    op.execute("CREATE INDEX ix_wes_biz_line_run_epochs_workline_id ON wes_biz.line_run_epochs (workline_id)")
    op.execute(
        "CREATE TABLE wes_biz.line_run_epoch_device_bindings (\n\tversion INTEGER DEFAULT '0' NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITHOUT TIME ZONE, \n\tid BIGSERIAL NOT NULL, \n\tcreated_by BIGINT, \n\tupdated_by BIGINT, \n\tline_run_epoch_id BIGINT NOT NULL, \n\tdevice_id INTEGER NOT NULL, \n\tdevice_code VARCHAR(100) NOT NULL, \n\tdevice_role VARCHAR(50) NOT NULL, \n\tendpoint_base_url VARCHAR(255) NOT NULL, \n\tcontract_key VARCHAR(100) NOT NULL, \n\tcontract_version VARCHAR(50) NOT NULL, \n\tstatus_max_age_ms INTEGER NOT NULL, \n\tcommand_timeout_ms INTEGER NOT NULL, \n\tCONSTRAINT pk_line_run_epoch_device_bindings PRIMARY KEY (id), \n\tCONSTRAINT ux_line_run_epoch_device_bindings_epoch_device_code UNIQUE (line_run_epoch_id, device_code), \n\tCONSTRAINT ux_line_run_epoch_device_bindings_epoch_device_id UNIQUE (line_run_epoch_id, device_id), \n\tCONSTRAINT ck_line_run_epoch_device_bindings_line_run_epoch_bindin_a07c CHECK (status_max_age_ms > 0), \n\tCONSTRAINT ck_line_run_epoch_device_bindings_line_run_epoch_bindin_f00e CHECK (command_timeout_ms > 0), \n\tCONSTRAINT ck_line_run_epoch_device_bindings_line_run_epoch_bindin_9e72 CHECK (length(endpoint_base_url) > 0), \n\tCONSTRAINT fk_line_run_epoch_device_bindings_line_run_epoch_id_lin_46b5 FOREIGN KEY(line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id), \n\tCONSTRAINT fk_line_run_epoch_device_bindings_device_id_devices FOREIGN KEY(device_id) REFERENCES wes_biz.devices (id)\n)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_line_run_epoch_device_bindings_device_id ON wes_biz.line_run_epoch_device_bindings (device_id)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_line_run_epoch_device_bindings_line_run_epoch_id ON wes_biz.line_run_epoch_device_bindings (line_run_epoch_id)"
    )
    op.execute(
        "CREATE TABLE wes_biz.line_run_epoch_position_bindings (\n\tversion INTEGER DEFAULT '0' NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITHOUT TIME ZONE, \n\tid BIGSERIAL NOT NULL, \n\tcreated_by BIGINT, \n\tupdated_by BIGINT, \n\tline_run_epoch_id BIGINT NOT NULL, \n\tposition_role VARCHAR(50) NOT NULL, \n\tlocation_id VARCHAR(120) NOT NULL, \n\tlocation_type VARCHAR(50) NOT NULL, \n\tCONSTRAINT pk_line_run_epoch_position_bindings PRIMARY KEY (id), \n\tCONSTRAINT ux_line_run_epoch_position_bindings_epoch_role UNIQUE (line_run_epoch_id, position_role), \n\tCONSTRAINT ux_line_run_epoch_position_bindings_epoch_location UNIQUE (line_run_epoch_id, location_id), \n\tCONSTRAINT fk_line_run_epoch_position_bindings_line_run_epoch_id_l_ca09 FOREIGN KEY(line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id)\n)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_line_run_epoch_position_bindings_line_run_epoch_id ON wes_biz.line_run_epoch_position_bindings (line_run_epoch_id)"
    )
    op.execute(
        "CREATE TABLE wes_biz.bin_executions (\n\tversion INTEGER DEFAULT '0' NOT NULL, \n\tcreated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITHOUT TIME ZONE, \n\tid BIGSERIAL NOT NULL, \n\tcreated_by BIGINT, \n\tupdated_by BIGINT, \n\texecution_code VARCHAR(120) NOT NULL, \n\tbin_id VARCHAR(100) NOT NULL, \n\tworkline_id INTEGER NOT NULL, \n\tline_run_epoch_id INTEGER NOT NULL, \n\tstatus VARCHAR(10) NOT NULL, \n\tstarted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, \n\tclosed_at TIMESTAMP WITHOUT TIME ZONE, \n\tCONSTRAINT pk_bin_executions PRIMARY KEY (id), \n\tCONSTRAINT ck_bin_executions_bin_execution_status_valid CHECK (status IN ('ACTIVE', 'CLOSED')), \n\tCONSTRAINT ux_bin_executions_execution_code UNIQUE (execution_code), \n\tCONSTRAINT fk_bin_executions_workline_id_work_lines FOREIGN KEY(workline_id) REFERENCES wes_biz.work_lines (id), \n\tCONSTRAINT fk_bin_executions_line_run_epoch_id_line_run_epochs FOREIGN KEY(line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id)\n)"
    )
    op.execute("CREATE INDEX ix_wes_biz_bin_executions_bin_id ON wes_biz.bin_executions (bin_id)")
    op.execute("CREATE INDEX ix_wes_biz_bin_executions_status ON wes_biz.bin_executions (status)")
    op.execute(
        "CREATE UNIQUE INDEX ux_bin_executions_active_bin ON wes_biz.bin_executions (bin_id) WHERE status = 'ACTIVE'"
    )
    op.execute("CREATE INDEX ix_wes_biz_bin_executions_workline_id ON wes_biz.bin_executions (workline_id)")
    op.execute("CREATE INDEX ix_bin_executions_epoch_status ON wes_biz.bin_executions (line_run_epoch_id, status, id)")
    op.execute("CREATE INDEX ix_wes_biz_bin_executions_line_run_epoch_id ON wes_biz.bin_executions (line_run_epoch_id)")
    op.execute(
        "ALTER TABLE wes_biz.device_commands ADD CONSTRAINT ck_device_commands_device_command_execution_context_complete CHECK (((execution_ref_type IN ('MANUAL_DEBUG', 'EVENT_DEBUG') AND line_run_epoch_id IS NULL AND device_binding_id IS NULL AND material_execution_id IS NULL AND endpoint_base_url IS NOT NULL AND command_timeout_ms IS NOT NULL) OR (execution_ref_type NOT IN ('MANUAL_DEBUG', 'EVENT_DEBUG') AND line_run_epoch_id IS NOT NULL AND device_binding_id IS NOT NULL AND endpoint_base_url IS NULL AND command_timeout_ms IS NULL)))"
    )
    op.execute(
        "ALTER TABLE wes_biz.device_commands ADD CONSTRAINT fk_device_commands_device_binding_id_line_run_epoch_dev_d412 FOREIGN KEY(device_binding_id) REFERENCES wes_biz.line_run_epoch_device_bindings (id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.device_commands ADD CONSTRAINT fk_device_commands_line_run_epoch_id_line_run_epochs FOREIGN KEY(line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.device_commands ADD CONSTRAINT ux_device_commands_execution_identity UNIQUE (line_run_epoch_id, device_code, execution_ref_type, execution_ref_id)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_device_commands_device_binding_id ON wes_biz.device_commands (device_binding_id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.inbound_evidences ADD CONSTRAINT fk_inbound_evidences_line_run_epoch_id_line_run_epochs FOREIGN KEY(line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_inbound_evidences_line_run_epoch_id ON wes_biz.inbound_evidences (line_run_epoch_id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.material_executions ADD CONSTRAINT fk_material_executions_line_run_epoch_id_line_run_epochs FOREIGN KEY(line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id)"
    )
    op.execute(
        "CREATE INDEX ix_material_executions_active_fifo ON wes_biz.material_executions (workline_id, line_run_epoch_id, admission_received_at, admission_evidence_id, id) WHERE status <> 'CLOSED'"
    )
    op.execute(
        "CREATE INDEX ix_material_executions_epoch_status ON wes_biz.material_executions (line_run_epoch_id, status, id)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_material_executions_line_run_epoch_id ON wes_biz.material_executions (line_run_epoch_id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.picking_tasks ADD CONSTRAINT ck_picking_tasks_picking_task_binding_matches_status CHECK ((status = 'QUEUED' AND workline_id IS NULL AND line_run_epoch_id IS NULL) OR (status IN ('PREPARING', 'EXECUTING', 'EXECUTION_COMPLETED') AND workline_id IS NOT NULL AND line_run_epoch_id IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE wes_biz.picking_tasks ADD CONSTRAINT fk_picking_tasks_line_run_epoch_id_line_run_epochs FOREIGN KEY(line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id)"
    )
    op.execute("CREATE INDEX ix_wes_biz_picking_tasks_line_run_epoch_id ON wes_biz.picking_tasks (line_run_epoch_id)")
    op.execute(
        "ALTER TABLE wes_biz.position_projections ADD CONSTRAINT ck_position_projections_position_projection_bin_authority_valid CHECK ((object_type = 'RACK' AND bin_execution_id IS NULL) OR (object_type = 'BIN' AND bin_execution_id IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE wes_biz.position_projections ADD CONSTRAINT fk_position_projections_bin_execution_id_bin_executions FOREIGN KEY(bin_execution_id) REFERENCES wes_biz.bin_executions (id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.position_projections ADD CONSTRAINT fk_position_projections_line_run_epoch_id_line_run_epochs FOREIGN KEY(line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id)"
    )
    op.execute("CREATE INDEX ix_position_projection_epoch ON wes_biz.position_projections (line_run_epoch_id, id)")
    op.execute(
        "CREATE INDEX ix_wes_biz_position_projections_bin_execution_id ON wes_biz.position_projections (bin_execution_id)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_position_projections_line_run_epoch_id ON wes_biz.position_projections (line_run_epoch_id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.transport_decision_bindings ADD CONSTRAINT fk_transport_decision_bindings_epoch FOREIGN KEY(line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.transport_decision_bindings ADD CONSTRAINT ux_transport_decision_bindings_decision_identity UNIQUE (line_run_epoch_id, correlation_id, step)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_transport_decision_bindings_epoch_resource ON wes_biz.transport_decision_bindings (line_run_epoch_id, resource_fence_id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.wms_confirmations ADD CONSTRAINT ck_wms_confirmations_wms_confirmation_exactly_one_owner CHECK ((CASE WHEN material_execution_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN bin_execution_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN picking_task_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN line_run_epoch_id IS NOT NULL THEN 1 ELSE 0 END) = 1)"
    )
    op.execute(
        "ALTER TABLE wes_biz.wms_confirmations ADD CONSTRAINT fk_wms_confirmations_bin_execution_id_bin_executions FOREIGN KEY(bin_execution_id) REFERENCES wes_biz.bin_executions (id)"
    )
    op.execute(
        "ALTER TABLE wes_biz.wms_confirmations ADD CONSTRAINT fk_wms_confirmations_line_run_epoch_id_line_run_epochs FOREIGN KEY(line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_wms_confirmations_bin_execution_id ON wes_biz.wms_confirmations (bin_execution_id)"
    )
    op.execute(
        "CREATE INDEX ix_wes_biz_wms_confirmations_line_run_epoch_id ON wes_biz.wms_confirmations (line_run_epoch_id)"
    )
    op.execute(
        "ALTER TABLE wes_runtime.transport_tasks ADD CONSTRAINT ck_transport_tasks_transport_execution_authority_all_or_none CHECK ((authority_workline_id IS NULL AND authority_line_run_epoch_id IS NULL AND authority_bin_execution_id IS NULL) OR (authority_workline_id IS NOT NULL AND authority_line_run_epoch_id IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE wes_runtime.transport_tasks ADD CONSTRAINT fk_transport_tasks_authority_bin_execution_id_bin_executions FOREIGN KEY(authority_bin_execution_id) REFERENCES wes_biz.bin_executions (id)"
    )
    op.execute(
        "ALTER TABLE wes_runtime.transport_tasks ADD CONSTRAINT fk_transport_tasks_authority_line_run_epoch_id_line_run_epochs FOREIGN KEY(authority_line_run_epoch_id) REFERENCES wes_biz.line_run_epochs (id)"
    )
