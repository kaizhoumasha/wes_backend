"""convert device_commands.error_detail to json

Revision ID: 20260304_1622
Revises: 20260304_1455
Create Date: 2026-03-04 16:22:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260304_1622"
down_revision: Union[str, Sequence[str], None] = "20260304_1455"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION wes_biz._safe_text_to_json(input_text TEXT)
        RETURNS JSON
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF input_text IS NULL OR btrim(input_text) = '' THEN
                RETURN NULL;
            END IF;
            BEGIN
                RETURN input_text::json;
            EXCEPTION WHEN others THEN
                RETURN json_build_object('message', input_text);
            END;
        END;
        $$;
        """
    )

    op.execute(
        """
        ALTER TABLE wes_biz.device_commands
        ALTER COLUMN error_detail TYPE JSON
        USING wes_biz._safe_text_to_json(error_detail);
        """
    )

    op.execute("DROP FUNCTION wes_biz._safe_text_to_json(TEXT);")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        ALTER TABLE wes_biz.device_commands
        ALTER COLUMN error_detail TYPE TEXT
        USING error_detail::text;
        """
    )
