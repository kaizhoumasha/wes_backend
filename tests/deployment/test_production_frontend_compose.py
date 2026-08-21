from pathlib import Path

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class _ComposeSafeLoader(yaml.SafeLoader):
    """解析仓库 Compose overlay 使用的 ``!override`` 标签。"""


def _construct_compose_sequence(loader: _ComposeSafeLoader, node: yaml.nodes.SequenceNode) -> list[object]:
    return loader.construct_sequence(node)


_ComposeSafeLoader.add_constructor("!override", _construct_compose_sequence)


def test_production_frontend_uses_required_image_and_is_a_healthy_nginx_dependency() -> None:
    compose = yaml.load(
        (BACKEND_ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8"),
        Loader=_ComposeSafeLoader,  # noqa: S506 - 仓库内受控 Compose，需解析 !override 标签
    )

    frontend = compose["services"]["frontend"]
    assert frontend["image"] == "${FRONTEND_IMAGE:?FRONTEND_IMAGE is required}"
    assert frontend["expose"] == ["5173"]
    assert "ports" not in frontend
    assert frontend["networks"] == ["wesp9-network"]
    assert frontend["healthcheck"]["test"]

    nginx_dependencies = compose["services"]["nginx"]["depends_on"]
    assert nginx_dependencies["api"]["condition"] == "service_healthy"
    assert nginx_dependencies["frontend"]["condition"] == "service_healthy"
