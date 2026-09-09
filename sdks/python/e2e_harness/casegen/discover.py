"""Discover test cases from handler source code (AST) + companion yaml files.

Walks ``source_root`` for handler modules carrying ``@case`` / ``@spec``
decorators and merges them with companion ``*_cases.yaml`` sidecars. For each
case, computes the target test script path under ``test_root`` keyed by the
case's ``group`` (not the handler's source path):

    <test_root>/<group>/test_<endpoint>__<case_id>.py
    → tests/note/test_create_note__happy_minimal.py   (group="note")

This intentionally **decouples the test layout from the service's source-dir
structure** — the case's ``group`` (declared on ``@case``, default "default")
decides where the test lives, so the service can reorganize its handler files
without moving tests. ``endpoint`` (the handler function name) keeps short,
per-handler case ids unique within a group; same-named endpoints across
surfaces must use distinct groups (discover raises on collision).

AST static analysis (not import) is used, so this module has no runtime
dependency on the service code being scanned.
"""

from __future__ import annotations

import ast
import warnings
from dataclasses import dataclass
from pathlib import Path

from e2e_harness.casegen.contract import (
    CASE_ID_PATTERN,
    DEFAULT_GROUP,
    GROUP_PATTERN,
    Case,
    case_hash,
    load_cases_file,
)


@dataclass(frozen=True)
class DiscoverConfig:
    source_root: Path  # dir to scan for @case/@spec, e.g. <repo>/server/api
    test_root: (
        Path  # where tests land, grouped: <test_root>/<group>/ (e.g. eval-api/tests)
    )


@dataclass(frozen=True)
class DiscoveredCase:
    case: Case
    handler_qualname: str  # for traceability ("api.rest.v1.endpoints.note.create_note")
    spec_text: str | None
    spec_id: str | None
    symbol_id: str
    case_hash: str  # joint hash of case content + @spec text
    target_script_path: Path


@dataclass(frozen=True)
class HandlerInfo:
    """AST-extracted view of one top-level handler."""

    name: str
    spec_text: str | None
    spec_id: str | None
    cases: list[Case]  # @case-derived cases, in source order


def discover(config: DiscoverConfig) -> list[DiscoveredCase]:
    """Scan ``config.source_root`` for handlers + yaml.

    Yaml-sourced cases win on id collision; the dropped decorator case is
    reported via warning.
    """
    source_scan_root = config.source_root
    out: list[DiscoveredCase] = []
    seen_handler_pys: set[Path] = set()

    # Pass 1: every cases.yaml + its companion handler.
    for cases_file in sorted(source_scan_root.rglob("*_cases.yaml")):
        dotted, handler_py = _module_from_cases_path(cases_file, config.source_root)
        yaml_cases = load_cases_file(cases_file, source_module=dotted)
        handlers = _extract_handlers(handler_py, source_module=dotted)
        seen_handler_pys.add(handler_py.resolve())

        merged = _merge_yaml_and_decorator_cases(
            yaml_cases=yaml_cases,
            handlers=handlers,
            cases_file=cases_file,
            handler_py=handler_py,
        )
        for c in merged:
            info = _single_handler(handlers, c.endpoint, cases_file)
            spec_text = info.spec_text if info else None
            spec_id = info.spec_id if info else None
            out.append(
                DiscoveredCase(
                    case=c,
                    handler_qualname=f"{dotted}.{c.endpoint}",
                    spec_text=spec_text,
                    spec_id=spec_id,
                    symbol_id=_symbol_id(handler_py, config.source_root, c.endpoint),
                    case_hash=case_hash(c, spec_text, spec_id),
                    target_script_path=_target_path(config.test_root, c),
                )
            )

    # Pass 2: handlers with @case but no companion yaml.
    for handler_py in sorted(source_scan_root.rglob("*.py")):
        if handler_py.name.startswith("_") or handler_py.resolve() in seen_handler_pys:
            continue
        if handler_py.name.endswith("_test.py") or handler_py.name.startswith("test_"):
            continue
        dotted = _module_dotted(handler_py, config.source_root)
        try:
            handlers = _extract_handlers(handler_py, source_module=dotted)
        except SyntaxError:
            continue
        if not any(info.cases for infos in handlers.values() for info in infos):
            continue
        for infos in handlers.values():
            for info in infos:
                for c in info.cases:
                    out.append(
                        DiscoveredCase(
                            case=c,
                            handler_qualname=f"{dotted}.{c.endpoint}",
                            spec_text=info.spec_text,
                            spec_id=info.spec_id,
                            symbol_id=_symbol_id(
                                handler_py, config.source_root, c.endpoint
                            ),
                            case_hash=case_hash(c, info.spec_text, info.spec_id),
                            target_script_path=_target_path(config.test_root, c),
                        )
                    )
    _check_unique_targets(out)
    return out


def _target_path(test_root: Path, c: Case) -> Path:
    """Test script path keyed by ``group`` (not source path): ``<test_root>/<group>/test_<endpoint>__<id>.py``."""
    return test_root / c.group / f"test_{c.endpoint}__{c.id}.py"


def _check_unique_targets(cases: list[DiscoveredCase]) -> None:
    """Two cases mapping to the same path == a (group, endpoint, id) collision —
    typically the same endpoint name under two surfaces sharing a group. The fix
    is to give one an explicit ``group=`` on its ``@case``."""
    seen: dict[Path, str] = {}
    for dc in cases:
        prev = seen.get(dc.target_script_path)
        if prev is not None:
            raise ValueError(
                f"case path collision: {dc.target_script_path} claimed by both "
                f"{prev} and {dc.handler_qualname} (case {dc.case.id!r}, group "
                f"{dc.case.group!r}). Set a distinct group= on one @case."
            )
        seen[dc.target_script_path] = dc.handler_qualname


# --------------------------------------------------------------------------- AST helpers


def _module_dotted(handler_py: Path, source_root: Path) -> str:
    rel = handler_py.relative_to(source_root)
    return ".".join(rel.with_suffix("").parts)


def _module_from_cases_path(cases_path: Path, source_root: Path) -> tuple[str, Path]:
    """Derive (dotted module, handler.py) from ``<...>/<module>_cases.yaml``."""
    handler_py = cases_path.with_name(cases_path.name.replace("_cases.yaml", ".py"))
    if not handler_py.exists():
        raise FileNotFoundError(
            f"cases file {cases_path} has no companion handler module {handler_py}"
        )
    return _module_dotted(handler_py, source_root), handler_py


def _extract_handlers(
    handler_py: Path, source_module: str
) -> dict[str, list[HandlerInfo]]:
    """Return every top-level declaration, preserving plural specs per symbol."""
    tree = ast.parse(handler_py.read_text(encoding="utf-8"), filename=str(handler_py))
    out: dict[str, list[HandlerInfo]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        spec_text, spec_id = _read_spec_decorator(node, handler_py)
        cases = _read_case_decorators(node, handler_py, source_module)
        out.setdefault(node.name, []).append(
            HandlerInfo(
                name=node.name,
                spec_text=spec_text,
                spec_id=spec_id,
                cases=cases,
            )
        )
    return out


def _read_spec_decorator(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    source_path: Path,
) -> tuple[str | None, str | None]:
    found: tuple[str, str | None] | None = None
    for deco in fn.decorator_list:
        if not isinstance(deco, ast.Call) or _decorator_name(deco.func) != "spec":
            continue
        if len(deco.args) != 1 or any(kw.arg != "id" for kw in deco.keywords):
            raise ValueError(
                f"{source_path}:{deco.lineno}: @spec takes text and optional id="
            )
        arg = deco.args[0]
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            raise ValueError(
                f"{source_path}:{deco.lineno}: @spec argument must be a string literal "
                f"(got {type(arg).__name__}). f-strings / variable refs aren't supported."
            )
        text = arg.value.strip()
        if not text:
            raise ValueError(
                f"{source_path}:{deco.lineno}: @spec text must be non-empty"
            )
        spec_id: str | None = None
        for keyword in deco.keywords:
            spec_id = _require_string_literal(
                keyword.value, source_path, deco.lineno, "spec id"
            )
            if not CASE_ID_PATTERN.fullmatch(spec_id):
                raise ValueError(
                    f"{source_path}:{deco.lineno}: @spec id {spec_id!r} must match "
                    f"{CASE_ID_PATTERN.pattern}"
                )
        if found is not None:
            raise ValueError(
                f"{source_path}:{deco.lineno}: one declaration may carry only one @spec"
            )
        found = (text, spec_id)
    return found or (None, None)


_CASE_KW_FIELDS = ("input", "expect", "forbid", "group")


def _read_case_decorators(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    source_path: Path,
    source_module: str,
) -> list[Case]:
    cases: list[Case] = []
    seen_ids: set[str] = set()
    for deco in fn.decorator_list:
        if not isinstance(deco, ast.Call) or _decorator_name(deco.func) != "case":
            continue
        c = _parse_case_call(deco, source_path, fn.name, source_module)
        if c.id in seen_ids:
            raise ValueError(
                f"{source_path}:{deco.lineno}: duplicate @case id {c.id!r} on handler {fn.name!r}"
            )
        seen_ids.add(c.id)
        cases.append(c)
    return cases


def _parse_case_call(
    deco: ast.Call,
    source_path: Path,
    endpoint: str,
    source_module: str,
) -> Case:
    if len(deco.args) != 2:
        raise ValueError(
            f"{source_path}:{deco.lineno}: @case takes exactly two positional arguments "
            f"(id, desc); got {len(deco.args)}"
        )
    id_node, desc_node = deco.args
    case_id = _require_string_literal(id_node, source_path, deco.lineno, "id")
    desc = _require_string_literal(desc_node, source_path, deco.lineno, "desc")
    if not case_id:
        raise ValueError(f"{source_path}:{deco.lineno}: @case id must be non-empty")
    if not CASE_ID_PATTERN.match(case_id):
        raise ValueError(
            f"{source_path}:{deco.lineno}: @case id {case_id!r} must match {CASE_ID_PATTERN.pattern}"
        )
    if not desc.strip():
        raise ValueError(f"{source_path}:{deco.lineno}: @case desc must be non-empty")

    kwargs: dict[str, str] = {}
    for kw in deco.keywords:
        if kw.arg not in _CASE_KW_FIELDS:
            raise ValueError(
                f"{source_path}:{deco.lineno}: @case unknown keyword {kw.arg!r} "
                f"(allowed: {', '.join(_CASE_KW_FIELDS)})"
            )
        value = _require_string_literal(kw.value, source_path, deco.lineno, kw.arg)
        kwargs[kw.arg] = value.strip()

    group = kwargs.get("group") or DEFAULT_GROUP
    if not GROUP_PATTERN.match(group):
        raise ValueError(
            f"{source_path}:{deco.lineno}: @case group {group!r} must match {GROUP_PATTERN.pattern}"
        )

    return Case(
        id=case_id,
        desc=desc.strip(),
        input=kwargs.get("input", ""),
        expect=kwargs.get("expect", ""),
        forbid=kwargs.get("forbid", ""),
        group=group,
        endpoint=endpoint,
        source_module=source_module,
        cases_file=None,
        source="decorator",
    )


def _require_string_literal(
    node: ast.expr,
    source_path: Path,
    lineno: int,
    field: str,
) -> str:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        raise ValueError(
            f"{source_path}:{lineno}: @case {field} must be a string literal "
            f"(got {type(node).__name__}). f-strings / variable refs aren't supported."
        )
    return node.value


def _decorator_name(node: ast.expr) -> str | None:
    """Extract trailing identifier for ``foo`` / ``mod.foo`` / ``a.b.foo``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _merge_yaml_and_decorator_cases(
    *,
    yaml_cases: list[Case],
    handlers: dict[str, list[HandlerInfo]],
    cases_file: Path,
    handler_py: Path,
) -> list[Case]:
    """Merge yaml + decorator cases. Yaml wins on id collision."""
    merged: list[Case] = []
    seen_per_endpoint: dict[str, set[str]] = {}

    for c in yaml_cases:
        if c.endpoint not in handlers:
            raise ValueError(
                f"{cases_file}: endpoint '{c.endpoint}' not found in module "
                f"{handler_py} (cases.yaml referenced a handler that doesn't exist)"
            )
        merged.append(c)
        seen_per_endpoint.setdefault(c.endpoint, set()).add(c.id)

    for endpoint, infos in handlers.items():
        seen = seen_per_endpoint.setdefault(endpoint, set())
        for info in infos:
            for c in info.cases:
                if c.id in seen:
                    warnings.warn(
                        f"{handler_py}: @case id {c.id!r} on {endpoint} conflicts with "
                        f"the same id in {cases_file}; yaml wins, decorator case ignored.",
                        stacklevel=2,
                    )
                    continue
                merged.append(c)
                seen.add(c.id)
    return merged


def _single_handler(
    handlers: dict[str, list[HandlerInfo]], endpoint: str, cases_file: Path
) -> HandlerInfo | None:
    infos = handlers.get(endpoint, [])
    if len(infos) > 1:
        raise ValueError(
            f"{cases_file}: endpoint {endpoint!r} has plural named specs; "
            "bind cases with decorators so each spec remains unambiguous"
        )
    return infos[0] if infos else None


def _symbol_id(handler_py: Path, source_root: Path, endpoint: str) -> str:
    return f"{handler_py.relative_to(source_root).as_posix()}::{endpoint}"
