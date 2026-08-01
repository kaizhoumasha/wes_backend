import json
import subprocess
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from scripts.select_heavy_tests import (
    SelectorError,
    build_parser,
    expand_braces,
    get_changed_files,
    load_config,
    main,
    matches_glob,
    select_heavy_tests,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HEAVY_TEST = "tests/integration/test_authoritative_runtime.py"


def _write_mapping(
    tmp_path: Path,
    mappings: list[tuple[str, list[str]]] | None = None,
    *,
    ignore_globs: list[str] | None = None,
) -> Path:
    lines = [f"ignore_globs = {json.dumps(ignore_globs or ['docs/**', '*.md', 'tests/**', 'Jenkinsfile'])}"]
    for source_glob, heavy_tests in mappings or []:
        lines.extend(
            [
                "",
                "[[mapping]]",
                f"source_glob = {json.dumps(source_glob)}",
                f"heavy_tests = {json.dumps(heavy_tests)}",
            ]
        )
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mapping_path


def test_selector_module_exists() -> None:
    assert (REPO_ROOT / "scripts/select_heavy_tests.py").is_file()


def test_parser_enforces_scope_base_protocol() -> None:
    parser = build_parser()

    assert parser.parse_args([]).scope == "unstaged"
    assert parser.parse_args(["--scope", "staged"]).scope == "staged"
    assert parser.parse_args(["--base", "origin/develop"]).base == "origin/develop"
    with pytest.raises(SystemExit):
        parser.parse_args(["--scope", "unstaged", "--base", "origin/develop"])


@pytest.mark.parametrize(
    ("scope", "base", "stdouts", "expected_commands", "expected_files"),
    [
        (
            "unstaged",
            None,
            ["src/app.py\n", "src/new.py\n"],
            [
                ["git", "diff", "--name-only"],
                ["git", "ls-files", "--others", "--exclude-standard"],
            ],
            ["src/app.py", "src/new.py"],
        ),
        (
            "staged",
            None,
            ["main.py\nmigrations/env.py\n"],
            [["git", "diff", "--cached", "--name-only"]],
            ["main.py", "migrations/env.py"],
        ),
        (
            None,
            "origin/develop",
            ["tests/integration/test_foo.py\n"],
            [["git", "diff", "--name-only", "origin/develop...HEAD"]],
            ["tests/integration/test_foo.py"],
        ),
        (
            "unstaged",
            None,
            ["", ""],
            [
                ["git", "diff", "--name-only"],
                ["git", "ls-files", "--others", "--exclude-standard"],
            ],
            [],
        ),
    ],
)
def test_get_changed_files_uses_expected_git_diff(
    tmp_path: Path,
    scope: str | None,
    base: str | None,
    stdouts: list[str],
    expected_commands: list[list[str]],
    expected_files: list[str],
) -> None:
    runner = Mock(
        side_effect=[
            subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
            for command, stdout in zip(expected_commands, stdouts, strict=True)
        ]
    )

    changed_files = get_changed_files(scope=scope, base=base, repo_root=tmp_path, runner=runner)

    assert changed_files == expected_files
    assert runner.call_args_list == [
        call(command, cwd=tmp_path, check=True, capture_output=True, text=True) for command in expected_commands
    ]


def test_git_changed_paths_preserve_noncanonical_input_for_validation(tmp_path: Path) -> None:
    unsafe_path = "tests/integration/test_x.py\x1f"
    runner = Mock(
        side_effect=[
            subprocess.CompletedProcess(["git", "diff"], 0, stdout=f"{unsafe_path}\n", stderr=""),
            subprocess.CompletedProcess(["git", "ls-files"], 0, stdout="", stderr=""),
        ]
    )

    changed_files = get_changed_files(scope="unstaged", base=None, repo_root=tmp_path, runner=runner)

    assert changed_files == [unsafe_path]
    with pytest.raises(SelectorError, match=r"changed path.*仓库相对路径"):
        select_heavy_tests(changed_files, load_config(_write_mapping(tmp_path)))


def test_direct_heavy_test_selects_itself(tmp_path: Path) -> None:
    config = load_config(_write_mapping(tmp_path))

    assert select_heavy_tests(["tests/integration/test_foo.py"], config) == ["tests/integration/test_foo.py"]


@pytest.mark.parametrize(
    "changed_path",
    [
        "tests/integration/conftest.py",
        "tests/mock/ecs_mock_catalog.py",
        "tests/fixtures/orders.json",
        "tests/conftest.py",
        "tests/runtime/conftest.py",
        "tests/support/runtime_factory.py",
        "main.py",
        "migrations/env.py",
        "alembic.ini",
    ],
)
def test_candidate_assets_use_mapping(tmp_path: Path, changed_path: str) -> None:
    config = load_config(_write_mapping(tmp_path, [(changed_path, [HEAVY_TEST])]))

    assert select_heavy_tests([changed_path], config) == [HEAVY_TEST]


@pytest.mark.parametrize(
    "changed_path",
    [
        "docs/architecture/selector.md",
        "Jenkinsfile",
        ".githooks/pre-commit",
        "tests/runtime/test_service.py",
        "tests/contracts/test_contract.py",
        "tests/api/test_route.py",
    ],
)
def test_ignored_paths_select_nothing(tmp_path: Path, changed_path: str) -> None:
    config = load_config(
        _write_mapping(
            tmp_path,
            ignore_globs=["docs/**", "*.md", "tests/**", "Jenkinsfile", ".githooks/**"],
        )
    )

    assert select_heavy_tests([changed_path], config) == []


def test_unknown_path_fails_closed(tmp_path: Path) -> None:
    config = load_config(_write_mapping(tmp_path))

    with pytest.raises(SelectorError, match="未分类"):
        select_heavy_tests(["tools/release.py"], config)


@pytest.mark.parametrize(
    "changed_path",
    [
        "./tests/integration/test_x.py",
        "tests//integration/test_x.py",
        "tests/integration/./test_x.py",
        "tests/integration/../e2e/test_x.py",
        "tests/integration/test_x.py/",
        "tests/integration/test_\x01.py",
        "tests\\integration\\test_x.py",
        "/tests/integration/test_x.py",
    ],
)
def test_changed_path_validation_rejects_noncanonical_or_unsafe_paths(tmp_path: Path, changed_path: str) -> None:
    config = load_config(_write_mapping(tmp_path))

    with pytest.raises(SelectorError, match=r"changed path.*仓库相对路径"):
        select_heavy_tests([changed_path], config)


@pytest.mark.parametrize(
    "source_glob",
    [
        "./src/**",
        "src//**",
        "src/./**",
        "src/../tests/**",
        "src/\x01/**",
        "src\\**",
        "/src/**",
    ],
)
def test_schema_rejects_noncanonical_source_globs(tmp_path: Path, source_glob: str) -> None:
    mapping_path = _write_mapping(tmp_path, [(source_glob, [HEAVY_TEST])])

    with pytest.raises(SelectorError, match=r"mapping\.source_glob.*仓库相对路径"):
        load_config(mapping_path)


@pytest.mark.parametrize(
    "heavy_test",
    [
        "./tests/integration/test_x.py",
        "tests//integration/test_x.py",
        "tests/integration/./test_x.py",
        "tests/integration/../e2e/test_x.py",
        "tests/integration/test_x.py/",
        "tests/integration/test_\x01.py",
        "tests\\integration\\test_x.py",
        "/tests/integration/test_x.py",
        "tests/integration/test_*.py",
    ],
)
def test_schema_rejects_noncanonical_heavy_test_paths(tmp_path: Path, heavy_test: str) -> None:
    mapping_path = _write_mapping(tmp_path, [("src/**", [heavy_test])])

    with pytest.raises(SelectorError, match=r"mapping\.heavy_tests.*仓库相对路径"):
        load_config(mapping_path)


def test_mapping_union_and_explicit_none(tmp_path: Path) -> None:
    second_heavy_test = "tests/e2e/test_authoritative_runtime.py"
    duplicate_mapping = load_config(
        _write_mapping(
            tmp_path,
            [
                ("src/{app,worker}/**", [HEAVY_TEST, second_heavy_test]),
                ("src/app/**", [second_heavy_test, HEAVY_TEST, HEAVY_TEST]),
            ],
        )
    )
    none_mapping = load_config(_write_mapping(tmp_path, [("pyproject.toml", [])]))

    assert select_heavy_tests(["src/app/service.py"], duplicate_mapping) == [
        second_heavy_test,
        HEAVY_TEST,
    ]
    assert select_heavy_tests(["pyproject.toml"], none_mapping) == []


def test_invalid_heavy_output_fails_closed(tmp_path: Path) -> None:
    mapping_path = _write_mapping(tmp_path, [("src/**", ["tests/unit/test_runtime.py"])])

    with pytest.raises(SelectorError, match="HEAVY 测试路径"):
        load_config(mapping_path)


def test_braces_and_double_star_zero_depth() -> None:
    assert expand_braces("tests/{integration,e2e}/**/test_*.py") == [
        "tests/integration/**/test_*.py",
        "tests/e2e/**/test_*.py",
    ]
    assert matches_glob("tests/integration/test_foo.py", "tests/{integration,e2e}/**/test_*.py")
    assert matches_glob("tests/integration/runtime/test_foo.py", "tests/{integration,e2e}/**/test_*.py")


def test_conflicting_overlapping_mappings_are_rejected(tmp_path: Path) -> None:
    mapping_path = _write_mapping(
        tmp_path,
        [
            ("src/**", ["tests/integration/test_all.py"]),
            ("src/app/**", ["tests/integration/test_app.py"]),
        ],
    )

    with pytest.raises(SelectorError, match="歧义"):
        load_config(mapping_path)

    wildcard_intersection = _write_mapping(
        tmp_path,
        [
            ("src/a*", ["tests/integration/test_prefix.py"]),
            ("src/*b", ["tests/integration/test_suffix.py"]),
        ],
    )
    with pytest.raises(SelectorError, match="歧义"):
        load_config(wildcard_intersection)


def test_caret_character_class_overlap_matches_purepath_semantics(tmp_path: Path) -> None:
    assert matches_glob("src/a", "src/[^a]")
    mapping_path = _write_mapping(
        tmp_path,
        [
            ("src/[^a]", ["tests/integration/test_caret_class.py"]),
            ("src/a", ["tests/integration/test_literal.py"]),
        ],
    )

    with pytest.raises(SelectorError, match="歧义"):
        load_config(mapping_path)


def test_negated_character_class_with_unicode_overlap_fails_closed(tmp_path: Path) -> None:
    assert matches_glob("src/α", "src/[! -~]")
    assert matches_glob("src/α", "src/?")
    mapping_path = _write_mapping(
        tmp_path,
        [
            ("src/[! -~]", ["tests/integration/test_non_ascii.py"]),
            ("src/?", ["tests/integration/test_any_character.py"]),
        ],
    )

    with pytest.raises(SelectorError, match="不支持的 glob 字符类"):
        load_config(mapping_path)


def test_unsupported_character_class_schema_fails_closed(tmp_path: Path) -> None:
    mapping_path = _write_mapping(tmp_path, [("src/[]]", [HEAVY_TEST])])

    with pytest.raises(SelectorError, match="不支持的 glob 字符类"):
        load_config(mapping_path)


def test_git_diff_failure_fails_closed(tmp_path: Path) -> None:
    runner = Mock(side_effect=subprocess.CalledProcessError(128, ["git", "diff"], stderr="bad ref"))

    with pytest.raises(SelectorError, match="git diff 失败"):
        get_changed_files(scope=None, base="origin/missing", repo_root=tmp_path, runner=runner)


def test_main_prints_one_test_per_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mapping_path = _write_mapping(
        tmp_path,
        [("src/**", ["tests/integration/test_z.py", "tests/e2e/test_a.py"])],
    )
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout="src/runtime.py\n",
            stderr="",
        )
    )

    exit_code = main(
        ["--scope", "unstaged"],
        repo_root=tmp_path,
        mapping_path=mapping_path,
        runner=runner,
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "tests/e2e/test_a.py\ntests/integration/test_z.py\n"


def test_repository_mapping_keeps_unaccepted_candidates_unmapped() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    for candidate in (
        "src/app/runtime.py",
        "main.py",
        "migrations/env.py",
        "alembic.ini",
        "docker-compose.yml",
        "pyproject.toml",
        ".env.test",
        "tests/integration/conftest.py",
        "tests/fixtures/orders.json",
        "tests/conftest.py",
        "tests/runtime/conftest.py",
        "tests/support/runtime_factory.py",
    ):
        with pytest.raises(SelectorError, match="未配置 mapping/NONE"):
            select_heavy_tests([candidate], config)


def test_repository_mapping_declares_required_ignore_globs() -> None:
    ignore_globs, mappings = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert ignore_globs == (
        "docs/**",
        "*.md",
        "tests/**",
        "Jenkinsfile",
        ".githooks/**",
        ".github/**",
        ".gitlab-ci.yml",
        "README*",
        "LICENSE*",
    )
    assert mappings == ()


def test_repository_ci_and_quality_gate_run_selector_contracts() -> None:
    jenkinsfile = (REPO_ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    quality_gate = (REPO_ROOT / "scripts/git-quality-gate.sh").read_text(encoding="utf-8")

    assert "stage('HEAVY Selector Smoke')" in jenkinsfile
    assert "uv run pytest tests/scripts -q" in jenkinsfile
    assert "stage('HEAVY Required')" not in jenkinsfile
    assert "run_script_contract_tests" in quality_gate
    assert "run_tool pytest tests/scripts -q" in quality_gate
