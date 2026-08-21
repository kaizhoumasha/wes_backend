"""Tree parent IDs must support production Snowflake primary keys."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from textwrap import dedent


def test_permission_and_menu_parent_ids_match_snowflake_primary_key_type() -> None:
    """A production Snowflake ID must fit both tree parent columns."""

    script = dedent(
        """
        import json

        from sqlalchemy.dialects import postgresql

        from src.app.admin.models.menu import Menu
        from src.app.admin.models.perm import Permission
        from src.core.conf import settings
        from src.utils.snowflake import generate_snowflake_id

        dialect = postgresql.dialect()
        print(json.dumps({
            "use_snowflake_id": settings.USE_SNOWFLAKE_ID,
            "snowflake_id": generate_snowflake_id(),
            "permission_id_type": Permission.__table__.c.id.type.compile(dialect=dialect).upper(),
            "permission_parent_id_type": Permission.__table__.c.parent_id.type.compile(dialect=dialect).upper(),
            "menu_id_type": Menu.__table__.c.id.type.compile(dialect=dialect).upper(),
            "menu_parent_id_type": Menu.__table__.c.parent_id.type.compile(dialect=dialect).upper(),
        }))
        """
    )
    env = os.environ.copy()
    env["USE_SNOWFLAKE_ID"] = "true"

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    contract = json.loads(result.stdout)

    assert contract["use_snowflake_id"] is True
    assert contract["snowflake_id"] > 2_147_483_647
    assert contract["permission_id_type"] == "BIGINT"
    assert contract["permission_parent_id_type"] == "BIGINT"
    assert contract["menu_id_type"] == "BIGINT"
    assert contract["menu_parent_id_type"] == "BIGINT"
