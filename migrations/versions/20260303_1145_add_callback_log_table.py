"""Add callback log table

Revision ID: 17dae3be98cf
Revises: 44d25b8a2459
Create Date: 2026-03-03 11:45:23.514733+08:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "17dae3be98cf"
down_revision: Union[str, Sequence[str], None] = "44d25b8a2459"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create callback_logs table in wes_biz schema
    op.execute("""
        CREATE TABLE IF NOT EXISTS wes_biz.callback_logs (
            id SERIAL PRIMARY KEY,
            deleted_by INTEGER,
            deleted_at TIMESTAMP WITHOUT TIME ZONE,
            is_deleted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),

            callback_type VARCHAR NOT NULL,
            device_id VARCHAR(50) NOT NULL,
            request_body JSONB,
            client_ip VARCHAR(50),
            user_agent TEXT,
            request_id VARCHAR(100),
            correlation_id VARCHAR(100),
            response_status INTEGER NOT NULL,
            response_time_ms INTEGER NOT NULL,
            error_message TEXT
        );

        CREATE INDEX ix_callback_logs_callback_type ON wes_biz.callback_logs(callback_type);
        CREATE INDEX ix_callback_logs_device_id ON wes_biz.callback_logs(device_id);
        CREATE INDEX ix_callback_logs_request_id ON wes_biz.callback_logs(request_id);
        CREATE INDEX ix_callback_logs_correlation_id ON wes_biz.callback_logs(correlation_id);

        COMMENT ON TABLE wes_biz.callback_logs IS '回调接收日志';
        COMMENT ON COLUMN wes_biz.callback_logs.callback_type IS '回调类型: event/result';
        COMMENT ON COLUMN wes_biz.callback_logs.device_id IS '设备 ID';
        COMMENT ON COLUMN wes_biz.callback_logs.request_body IS '原始请求体（JSON 格式）';
        COMMENT ON COLUMN wes_biz.callback_logs.client_ip IS '客户端 IP 地址';
        COMMENT ON COLUMN wes_biz.callback_logs.user_agent IS '客户端 User-Agent';
        COMMENT ON COLUMN wes_biz.callback_logs.request_id IS '请求 ID（用于链路追踪）';
        COMMENT ON COLUMN wes_biz.callback_logs.correlation_id IS '关联 ID（串联整个流程）';
        COMMENT ON COLUMN wes_biz.callback_logs.response_status IS 'HTTP 响应状态码';
        COMMENT ON COLUMN wes_biz.callback_logs.response_time_ms IS '响应时间（毫秒）';
        COMMENT ON COLUMN wes_biz.callback_logs.error_message IS '错误消息（如果处理失败）';
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS wes_biz.callback_logs;")
