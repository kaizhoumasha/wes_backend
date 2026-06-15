"""旧 Workline manifest 合同清理门禁。"""

import ast
import re
from pathlib import Path
from typing import Any

ACTIVE_PATHS = (
    Path("src"),
    Path("tests"),
    Path("docs/templates"),
    Path("docs/plugin_development_guide.md"),
)

IGNORED_ACTIVE_PATH_PREFIXES = (Path("tests/mock"),)

TEXT_SUFFIXES = {".json", ".md", ".py", ".tmpl", ".txt", ".yaml", ".yml"}
ALLOWLIST_MARKER = "manifest-cleanup-allowlist"


def _legacy_symbol(*parts: str) -> str:
    return "".join(parts)


LEGACY_IDENTIFIER_FRAGMENTS = (
    _legacy_symbol("Device", "RoleRequirement"),
    _legacy_symbol("Single", "LayerRackBoundary"),
)

LEGACY_API_EXPORTS = (
    _legacy_symbol("Device", "RoleRequirement", "Option"),
    _legacy_symbol("WorkLine", "Single", "LayerRackBoundary", "Summary"),
    _legacy_symbol("Pos", "ition"),
    _legacy_symbol("Pos", "ition", "Carrier", "Capability"),
    _legacy_symbol("Pos", "ition", "Arg"),
    _legacy_symbol("Pos", "ition", "Arg", "Role"),
    _legacy_symbol("Pos", "ition", "Arg", "Source"),
    _legacy_symbol("Pos", "ition", "Arg", "Source", "Kind"),
)

LEGACY_EXACT_SYMBOLS = (
    _legacy_symbol("Business", "KeyResolver"),
    _legacy_symbol("Result", "Classifier"),
    _legacy_symbol("_looks", "_like_manifest"),
    _legacy_symbol("_ALLOWED", "_SINGLE_LAYER_"),
    _legacy_symbol("_requires", "_single", "_layer_boundaries"),
)

LEGACY_MANIFEST_FIELDS = (
    _legacy_symbol("required", "_device_roles"),
    _legacy_symbol("event", "_source_roles"),
    _legacy_symbol("command", "_target_roles"),
    _legacy_symbol("supported", "_events"),
    _legacy_symbol("supported", "_commands"),
    _legacy_symbol("resource", "_kinds"),
    _legacy_symbol("requires", "_single_layer_boundary"),
    _legacy_symbol("single", "_layer_boundaries"),
    _legacy_symbol("runtime", "_source"),
    _legacy_symbol("pos", "itions"),
    "capabilities",
)

LEGACY_MANIFEST_CALLABLE_PARAMS = (
    _legacy_symbol("business", "_key_resolver"),
    _legacy_symbol("result", "_classifier"),
    _legacy_symbol("context", "_model"),
    _legacy_symbol("material", "_identity_resolver"),
    _legacy_symbol("ng", "_reason_catalog"),
)

LEGACY_MANIFEST_NAMES = LEGACY_MANIFEST_FIELDS + LEGACY_MANIFEST_CALLABLE_PARAMS

MANIFEST_CONSTRUCTOR_PATTERN = re.compile(r"\bWorklinePluginManifest\s*\(", re.DOTALL)
MANIFEST_NAMESPACE_PATTERN = re.compile(r"\bmanifest\s*=\s*SimpleNamespace\s*\(", re.DOTALL)
MANIFEST_ATTRIBUTE_PATTERNS = tuple(
    (name, re.compile(rf"\bmanifest\s*\.\s*{re.escape(name)}\b")) for name in LEGACY_MANIFEST_NAMES
)

LEGACY_REMOVED_CONTRACT_MEMBER_FIELDS = {
    _legacy_symbol("DeviceRequirement"): ("capabilities",),
    _legacy_symbol("Pos", "ition"): ("capabilities",),
    _legacy_symbol("Pos", "ition", "Carrier", "Capability"): ("capacity",),
    _legacy_symbol("CommandBinding"): (
        _legacy_symbol("position", "_args"),
        _legacy_symbol("position", "_ref"),
        _legacy_symbol("result", "_event"),
    ),
    _legacy_symbol("Pos", "ition", "Arg"): (
        _legacy_symbol("position", "_ref"),
        _legacy_symbol("runtime", "_source"),
    ),
    _legacy_symbol("Rack", "Pos", "ition", "Arg"): (_legacy_symbol("position", "_ref"),),
    _legacy_symbol("Pos", "ition", "Arg", "Source"): (_legacy_symbol("fallback", "_position", "_ref"),),
    _legacy_symbol("Rack", "Pos", "ition", "Arg", "Source"): (_legacy_symbol("fallback", "_position", "_ref"),),
    _legacy_symbol("ResourceBoundary"): (_legacy_symbol("position", "_code"),),
}

LEGACY_REMOVED_CONTRACT_MEMBER_SYMBOLS = (
    *(
        _legacy_symbol(owner, ".", field)
        for owner, fields in LEGACY_REMOVED_CONTRACT_MEMBER_FIELDS.items()
        for field in fields
    ),
    _legacy_symbol("NodeRefKind", ".", "POS", "ITION"),
)

LEGACY_REMOVED_CONTRACT_MEMBER_PATTERNS = tuple(
    (symbol, re.compile(rf"\b{re.escape(symbol)}\b")) for symbol in LEGACY_REMOVED_CONTRACT_MEMBER_SYMBOLS
)

LEGACY_REMOVED_TYPE_SIGNATURE_PATTERNS = (
    (
        _legacy_symbol("TopologySpec", " | ", "None"),
        re.compile(r"\bTopologySpec\s*\|\s*None\b"),
    ),
)

LEGACY_TEXT_SYMBOLS = (
    *(field_name for field_name in LEGACY_MANIFEST_FIELDS if field_name not in {"capabilities", "positions"}),
    *LEGACY_IDENTIFIER_FRAGMENTS,
    *LEGACY_API_EXPORTS,
    *LEGACY_EXACT_SYMBOLS,
)

LEGACY_TEXT_SYMBOL_PATTERNS = tuple((symbol, re.compile(rf"\b{re.escape(symbol)}\b")) for symbol in LEGACY_TEXT_SYMBOLS)

LEGACY_CAPABILITIES_FIELD_PATTERNS = (
    (
        _legacy_symbol("manifest", ".", "capabilities"),
        re.compile(r"\bmanifest\s*\.\s*capabilities\b"),
    ),
)


def _iter_active_files() -> list[Path]:
    files: list[Path] = []
    for path in ACTIVE_PATHS:
        if path.is_file():
            files.append(path)
            continue
        files.extend(
            item
            for item in path.rglob("*")
            if item.is_file()
            and item.suffix in TEXT_SUFFIXES
            and not any(item.is_relative_to(prefix) for prefix in IGNORED_ACTIVE_PATH_PREFIXES)
        )
    return sorted(files)


def _line_is_allowlisted(line: str) -> bool:
    return ALLOWLIST_MARKER in line


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _line_at(text: str, line_number: int) -> str:
    return text.splitlines()[line_number - 1]


def _find_matching_call_end(text: str, open_paren_offset: int) -> int | None:
    depth = 0
    for offset in range(open_paren_offset, len(text)):
        char = text[offset]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return offset
    return None


def _depth_one_keyword_line(text: str, start_offset: int, end_offset: int, keyword: str) -> int | None:
    depth = 1
    offset = start_offset
    keyword_pattern = re.compile(rf"\b{re.escape(keyword)}\s*=")
    while offset < end_offset:
        char = text[offset]
        if char == "(":
            depth += 1
            offset += 1
            continue
        if char == ")":
            depth -= 1
            offset += 1
            continue
        if depth == 1 and (match := keyword_pattern.match(text, offset)) is not None:
            return _line_number(text, match.start())
        offset += 1
    return None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_manifest_like_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id in {"manifest", "summary", "option"}


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _removed_type_signature(annotation: ast.AST | None) -> str | None:
    if annotation is None:
        return None
    annotation_text = ast.unparse(annotation)
    for signature, pattern in LEGACY_REMOVED_TYPE_SIGNATURE_PATTERNS:
        if pattern.search(annotation_text):
            return signature
    return None


def _is_legacy_type_or_export(name: str | None) -> bool:
    if name is None:
        return False
    return (
        name in LEGACY_API_EXPORTS
        or name in LEGACY_EXACT_SYMBOLS
        or any(fragment in name for fragment in LEGACY_IDENTIFIER_FRAGMENTS)
    )


def _is_assigned_manifest(tree: ast.AST, call: ast.Call) -> bool:
    for parent in ast.walk(tree):
        value = getattr(parent, "value", None)
        if value is not call:
            continue
        targets = getattr(parent, "targets", ())
        return any(isinstance(target, ast.Name) and target.id == "manifest" for target in targets)
    return False


def _python_legacy_manifest_hits(path: Path, text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _text_legacy_manifest_hits(path, text)

    hits: list[str] = []
    legacy_manifest_names = set(LEGACY_MANIFEST_NAMES)
    legacy_field_names = set(LEGACY_MANIFEST_FIELDS)
    legacy_removed_contract_member_fields = LEGACY_REMOVED_CONTRACT_MEMBER_FIELDS

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> Any:
            call_name = _call_name(node.func)
            if call_name == "WorklinePluginManifest":
                for keyword in node.keywords:
                    if keyword.arg in legacy_manifest_names:
                        hits.append(f"{path}:{keyword.lineno}: WorklinePluginManifest.{keyword.arg}")
            if call_name == "SimpleNamespace":
                for keyword in node.keywords:
                    if keyword.arg in legacy_manifest_names and _is_assigned_manifest(tree, node):
                        hits.append(f"{path}:{keyword.lineno}: manifest namespace field {keyword.arg}")
            if call_name in legacy_removed_contract_member_fields:
                for keyword in node.keywords:
                    if keyword.arg in legacy_removed_contract_member_fields[call_name]:
                        hits.append(f"{path}:{keyword.lineno}: {call_name}.{keyword.arg}")
            if (
                call_name == "hasattr"
                and len(node.args) >= 2
                and _is_manifest_like_name(node.args[0])
                and (field_name := _literal_string(node.args[1])) in legacy_field_names
            ):
                hits.append(f"{path}:{node.lineno}: public assertion for old manifest field {field_name}")
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
            if signature := _removed_type_signature(node.annotation):
                hits.append(f"{path}:{node.lineno}: removed type signature {signature}")
            self.generic_visit(node)

        def visit_arg(self, node: ast.arg) -> Any:
            if signature := _removed_type_signature(node.annotation):
                hits.append(f"{path}:{node.lineno}: removed type signature {signature}")
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
            if signature := _removed_type_signature(node.returns):
                hits.append(f"{path}:{node.lineno}: removed type signature {signature}")
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> Any:
            if _is_manifest_like_name(node.value) and node.attr in legacy_manifest_names:
                hits.append(f"{path}:{node.lineno}: manifest.{node.attr}")
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> Any:
            if _is_legacy_type_or_export(node.name):
                hits.append(f"{path}:{node.lineno}: legacy manifest type definition {node.name}")
            if any(token in node.name for token in ("Manifest", "Summary", "Option")):
                for statement in node.body:
                    target = getattr(statement, "target", None)
                    if isinstance(target, ast.Name) and target.id in legacy_manifest_names:
                        hits.append(f"{path}:{statement.lineno}: public model field {target.id}")
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
            for alias in node.names:
                if _is_legacy_type_or_export(alias.name):
                    hits.append(f"{path}:{node.lineno}: legacy manifest type import {alias.name}")
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> Any:
            for alias in node.names:
                imported_name = alias.name.rsplit(".", maxsplit=1)[-1]
                if _is_legacy_type_or_export(imported_name):
                    hits.append(f"{path}:{node.lineno}: legacy manifest type import {imported_name}")
            self.generic_visit(node)

        def visit_Assign(self, node: ast.Assign) -> Any:
            for target in node.targets:
                if isinstance(target, ast.Name) and _is_legacy_type_or_export(target.id):
                    hits.append(f"{path}:{node.lineno}: legacy manifest alias {target.id}")
                if isinstance(target, ast.Name) and target.id == "__all__":
                    for item in ast.walk(node.value):
                        exported_name = _literal_string(item)
                        if _is_legacy_type_or_export(exported_name):
                            hits.append(f"{path}:{item.lineno}: legacy manifest export {exported_name}")
            self.generic_visit(node)

    Visitor().visit(tree)
    return hits


def _window_legacy_name_hits(path: Path, text: str, pattern: re.Pattern[str], reason: str) -> list[str]:
    hits: list[str] = []
    for match in pattern.finditer(text):
        window = text[match.start() : match.start() + 5000]
        for name in LEGACY_MANIFEST_NAMES:
            name_match = re.search(rf"\b{re.escape(name)}\s*=", window)
            if name_match is None:
                continue
            line_number = _line_number(text, match.start() + name_match.start())
            if not _line_is_allowlisted(_line_at(text, line_number)):
                hits.append(f"{path}:{line_number}: {reason} {name}")
    return hits


def _text_legacy_manifest_hits(path: Path, text: str) -> list[str]:
    hits: list[str] = []
    hits.extend(_window_legacy_name_hits(path, text, MANIFEST_CONSTRUCTOR_PATTERN, "WorklinePluginManifest param"))
    hits.extend(_window_legacy_name_hits(path, text, MANIFEST_NAMESPACE_PATTERN, "manifest namespace field"))

    for name, pattern in MANIFEST_ATTRIBUTE_PATTERNS:
        for match in pattern.finditer(text):
            line_number = _line_number(text, match.start())
            if not _line_is_allowlisted(_line_at(text, line_number)):
                hits.append(f"{path}:{line_number}: manifest.{name}")

    for symbol, pattern in LEGACY_REMOVED_CONTRACT_MEMBER_PATTERNS:
        for match in pattern.finditer(text):
            line_number = _line_number(text, match.start())
            if not _line_is_allowlisted(_line_at(text, line_number)):
                hits.append(f"{path}:{line_number}: removed contract member {symbol}")

    for signature, pattern in LEGACY_REMOVED_TYPE_SIGNATURE_PATTERNS:
        for match in pattern.finditer(text):
            line_number = _line_number(text, match.start())
            if not _line_is_allowlisted(_line_at(text, line_number)):
                hits.append(f"{path}:{line_number}: removed type signature {signature}")

    if path.suffix == ".json":
        manifest_like_json = re.search(r'"plugin_key"\s*:', text) and re.search(r'"contract_version"\s*:', text)
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _line_is_allowlisted(line):
                continue
            for name in LEGACY_MANIFEST_NAMES:
                if name == "capabilities" and not manifest_like_json:
                    continue
                if re.search(rf'"{re.escape(name)}"\s*:', line):
                    hits.append(f"{path}:{line_number}: json manifest key {name}")

    if path.suffix == ".md":
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _line_is_allowlisted(line):
                continue
            for name in (field_name for field_name in LEGACY_MANIFEST_FIELDS if field_name != "capabilities"):
                if name == "positions":
                    has_legacy_field_context = bool(re.search(r"(^|[`,\s])positions(`|\s+只声明|,|，|$)", line))
                else:
                    has_legacy_field_context = bool(re.search(rf"\b{re.escape(name)}\b", line))
                if has_legacy_field_context:
                    hits.append(f"{path}:{line_number}: documented old manifest field {name}")

    return hits


def _text_legacy_type_hits(path: Path, text: str) -> list[str]:
    hits: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _line_is_allowlisted(line):
            continue
        for symbol in LEGACY_API_EXPORTS + LEGACY_IDENTIFIER_FRAGMENTS:
            if not re.search(rf"\b{re.escape(symbol)}\b", line):
                continue
            if re.search(r"\b(from|import|class|def)\b", line) or re.search(rf"\b{re.escape(symbol)}\s*[=(]", line):
                hits.append(f"{path}:{line_number}: legacy manifest type/export {symbol}")
        for symbol in LEGACY_EXACT_SYMBOLS:
            if re.search(rf"\b(def|class)\s+{re.escape(symbol)}\b|^\s*{re.escape(symbol)}\s*=", line):
                hits.append(f"{path}:{line_number}: legacy helper {symbol}")
    return hits


def _legacy_contract_member_keyword_hits(path: Path, text: str) -> list[str]:
    hits: list[str] = []
    for owner, fields in LEGACY_REMOVED_CONTRACT_MEMBER_FIELDS.items():
        call_pattern = re.compile(rf"\b{re.escape(owner)}\s*\(")
        for call_match in call_pattern.finditer(text):
            open_paren_offset = call_match.end() - 1
            end_offset = _find_matching_call_end(text, open_paren_offset)
            if end_offset is None:
                continue
            for field_name in fields:
                line_number = _depth_one_keyword_line(text, open_paren_offset + 1, end_offset, field_name)
                if line_number is None or _line_is_allowlisted(_line_at(text, line_number)):
                    continue
                hits.append(f"{path}:{line_number}: removed contract member keyword {owner}.{field_name}")
    return hits


def _text_removed_token_hits(path: Path, text: str) -> list[str]:
    hits: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _line_is_allowlisted(line):
            continue
        for symbol, pattern in LEGACY_TEXT_SYMBOL_PATTERNS:
            if pattern.search(line):
                hits.append(f"{path}:{line_number}: removed manifest symbol {symbol}")
        for symbol, pattern in LEGACY_REMOVED_CONTRACT_MEMBER_PATTERNS:
            if pattern.search(line):
                hits.append(f"{path}:{line_number}: removed contract member {symbol}")
        for signature, pattern in LEGACY_REMOVED_TYPE_SIGNATURE_PATTERNS:
            if pattern.search(line):
                hits.append(f"{path}:{line_number}: removed type signature {signature}")
        for reason, pattern in LEGACY_CAPABILITIES_FIELD_PATTERNS:
            if pattern.search(line):
                hits.append(f"{path}:{line_number}: removed {reason}")

    hits.extend(_legacy_contract_member_keyword_hits(path, text))
    return hits


def _legacy_symbol_hits(path: Path, text: str) -> list[str]:
    hits = _text_removed_token_hits(path, text)
    if path.suffix == ".py":
        hits.extend(_python_legacy_manifest_hits(path, text))
    else:
        hits.extend(_text_legacy_manifest_hits(path, text))
        hits.extend(_text_legacy_type_hits(path, text))
    return hits


def test_cleanup_gate_allows_hardware_capabilities_json_fixture() -> None:
    text = '{"' + "cap" + 'abilities": ["scan_item"]}'

    hits = _legacy_symbol_hits(Path("fixture.json"), text)

    assert hits == []


def test_cleanup_gate_detects_multiline_command_binding_position_ref_in_text() -> None:
    text = "\n".join(
        (
            _legacy_symbol("Command", "Binding", "("),
            '    command="MOVE",',
            _legacy_symbol("    position", '_ref="BIN",'),
            ")",
        )
    )

    hits = _text_removed_token_hits(Path("sample.tmpl"), text)

    assert any(_legacy_symbol("Command", "Binding.position", "_ref") in hit for hit in hits)


def test_cleanup_gate_detects_multiline_device_requirement_capabilities_in_text() -> None:
    text = "\n".join(
        (
            _legacy_symbol("Device", "Requirement", "("),
            '    role="SCANNER",',
            _legacy_symbol("    cap", 'abilities=("scan_item",),'),
            ")",
        )
    )

    hits = _text_removed_token_hits(Path("sample.md"), text)

    assert any(_legacy_symbol("Device", "Requirement.cap", "abilities") in hit for hit in hits)


def test_cleanup_gate_detects_rack_position_arg_legacy_position_ref() -> None:
    text = _legacy_symbol("Rack", "Pos", "ition", "Arg", '(name="target", position', '_ref="BIN")')

    hits = _text_removed_token_hits(Path("sample.tmpl"), text)

    assert any(_legacy_symbol("Rack", "Pos", "ition", "Arg.position", "_ref") in hit for hit in hits)


def test_cleanup_gate_detects_manifest_positions_field_without_flagging_rack_positions() -> None:
    text = "\n".join(
        (
            "WorklinePluginManifest(",
            _legacy_symbol("    pos", "itions=(),"),
            "    rack_positions=(),",
            ")",
        )
    )

    hits = _text_legacy_manifest_hits(Path("sample.tmpl"), text)

    assert any(_legacy_symbol("pos", "itions") in hit for hit in hits)
    assert not any("rack_positions" in hit for hit in hits)


def test_cleanup_gate_allows_runtime_position_code_payload_key() -> None:
    text = '{"position_code": "WORK_POSITION"}'

    hits = _text_removed_token_hits(Path("runtime_payload.json"), text)

    assert hits == []


def test_old_manifest_contract_symbols_are_removed_from_active_paths() -> None:
    hits: list[str] = []
    for path in _iter_active_files():
        text = path.read_text(encoding="utf-8")
        hits.extend(_legacy_symbol_hits(path, text))

    assert not hits, "旧 manifest 合同残留:\n" + "\n".join(hits[:200])
