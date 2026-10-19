import pytest

from deployment.plugin_definitions import load_plugin_definitions
from deployment.plugin_models import include_plugin_table


@pytest.mark.parametrize(
    "name",
    [
        "automatic_picking_works",
        "automatic_picking_feeds",
        "automatic_picking_feed_scans",
        "automatic_picking_pickups",
        "automatic_picking_returns",
        "automatic_picking_return_allocations",
        "automatic_picking_return_motions",
        "automatic_picking_drains",
        "automatic_picking_drain_racks",
    ],
)
def test_automatic_tables_belong_only_to_explicit_installation(name):
    assert not include_plugin_table(name, ())
    assert include_plugin_table(name, ("automatic-picking",))


def test_unknown_and_duplicate_automatic_keys_remain_closed():
    for keys in [("automatic-picking", "automatic-picking"), ("automatic-picking-extra",)]:
        with pytest.raises(ValueError):
            load_plugin_definitions(keys)
