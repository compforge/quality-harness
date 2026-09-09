"""Test case metadata primitives: @case / @spec / @rule / @link decorators,
Case dataclass, case_hash, YAML loader.

These decorators are markers — they attach metadata to handler functions so
casegen discovery and static spec extractors can enumerate it. At runtime they
have zero side effect on the handler itself.

@case / @spec drive casegen (discover compiles them into common.Case); @rule /
@link are review-side context — function-level reviewer guidance and a curated
"see also" — markers only, not consumed by casegen. All four share one grammar
so a static extractor can read them straight off the source.

Usage::

    @spec('''
    note 创建接口:
    - tenant/user header 必填
    - (tenant, user, name) 唯一,重复创建返回 ConflictError
    ''')
    @case("happy_minimal", "只传 Name 应创建成功")
    @case("duplicate_name", "重复 Name 应返回 409", expect="HTTP 409")
    @link("docs/tenancy.md")
    @rule("这个 handler 在请求热路径，盯新增的同步 DB 调用")
    @router.post("")
    async def create_note(...): ...

For cases too long to fit on a line, use a companion ``<module>_cases.yaml``
sidecar next to the handler module. discover() merges yaml + decorator cases.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml

CASE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# group：组织桶（eval-api 侧 tests/<group>/ 落盘）。同 id 规则；默认 "default"。
# 注意 group 是"文件放哪"的组织属性，不进 case_hash —— 改 group 只是挪文件，
# 不该把对应 test 标 stale（stale 只看意图 desc/input/expect/forbid/spec 变没变）。
GROUP_PATTERN = CASE_ID_PATTERN
DEFAULT_GROUP = "default"
CASES_ATTR = "__cases__"
SPEC_ATTR = "__spec__"
SPEC_ID_ATTR = "__spec_id__"
RULES_ATTR = "__rules__"
LINKS_ATTR = "__links__"

CaseSource = Literal["yaml", "decorator"]


@dataclass(frozen=True)
class CaseSpec:
    """5-field user-input shape, attached to handler via ``@case(...)``.

    Missing-but-not-empty fields use ``""`` (not ``None``) so unset behaves the
    same as an empty string for both truthiness and string ops downstream.
    """

    id: str
    desc: str
    input: str = ""
    expect: str = ""
    forbid: str = ""
    group: str = DEFAULT_GROUP  # 组织桶（tests/<group>/）；不参与 case_hash


@dataclass(frozen=True)
class Case(CaseSpec):
    """CaseSpec content + handler/source metadata filled by discover."""

    endpoint: str = ""  # handler function name (snake_case)
    source_module: str = ""  # python dotted path, e.g. "api.rest.v1.endpoints.note"
    cases_file: Path | None = None  # set when source == 'yaml'
    source: CaseSource = "decorator"

    def __post_init__(self) -> None:
        if not CASE_ID_PATTERN.match(self.id):
            origin = self.cases_file if self.cases_file else f"@case on {self.endpoint}"
            raise ValueError(
                f"invalid case id {self.id!r} in {origin}: "
                f"must match {CASE_ID_PATTERN.pattern}"
            )


F = TypeVar("F", bound=Callable)


def case(
    id: str,
    desc: str,
    *,
    input: str = "",
    expect: str = "",
    forbid: str = "",
    group: str = DEFAULT_GROUP,
) -> Callable[[F], F]:
    """Attach one test case to the handler. Stackable; preserves declaration order.

    ``group`` is the organizational bucket the generated test lands in
    (``tests/<group>/``); defaults to ``"default"``. Set it explicitly to
    disambiguate same-named endpoints across surfaces (e.g. a REST and an
    action_rpc ``create_note`` both in the default group would collide).
    """
    if not id or not isinstance(id, str):
        raise ValueError("@case requires non-empty string id")
    if not CASE_ID_PATTERN.match(id):
        raise ValueError(
            f"@case id {id!r} must match {CASE_ID_PATTERN.pattern} "
            f"(lowercase english + digits + underscore, starts with a letter)"
        )
    if not GROUP_PATTERN.match(group):
        raise ValueError(
            f"@case({id!r}) group {group!r} must match {GROUP_PATTERN.pattern}"
        )
    if not desc or not isinstance(desc, str):
        raise ValueError(f"@case({id!r}) requires non-empty string desc")

    entry = CaseSpec(
        id=id,
        desc=desc.strip(),
        input=(input or "").strip(),
        expect=(expect or "").strip(),
        forbid=(forbid or "").strip(),
        group=group,
    )

    def decorator(fn: F) -> F:
        existing = list(getattr(fn, CASES_ATTR, []))
        # Python decorator stack runs bottom-up, so the case closest to the
        # handler is registered first. Prepend so the final list mirrors
        # source order (top-to-bottom).
        existing.insert(0, entry)
        if any(e.id == entry.id for e in existing[1:]):
            raise ValueError(
                f"@case duplicate id {entry.id!r} on {getattr(fn, '__name__', '<fn>')}"
            )
        setattr(fn, CASES_ATTR, existing)
        return fn

    return decorator


def spec(text: str, *, id: str | None = None) -> Callable[[F], F]:
    """Attach one endpoint contract; ``id`` names it on a plural-spec symbol."""
    normalized = (text or "").strip()
    if not normalized:
        raise ValueError("@spec requires non-empty text")
    if id is not None and not CASE_ID_PATTERN.fullmatch(id):
        raise ValueError(f"@spec id {id!r} must match {CASE_ID_PATTERN.pattern}")

    def decorator(fn: F) -> F:
        setattr(fn, SPEC_ATTR, normalized)
        setattr(fn, SPEC_ID_ATTR, id)
        return fn

    return decorator


def _prepend_marker(fn: F, attr: str, value: str) -> F:
    """Register a 0..N marker value on ``fn`` in source order. The decorator
    stack runs bottom-up (marker nearest the fn first), so prepend to make the
    final list read top-to-bottom like the source."""
    existing = list(getattr(fn, attr, []))
    existing.insert(0, value)
    setattr(fn, attr, existing)
    return fn


def link(ref: str) -> Callable[[F], F]:
    """Attach one curated "see also" (0..N): a repo-relative md path, or a
    unit-id pointing at another symbol (distinguished by ``::``). Stackable;
    preserves declaration order."""
    normalized = (ref or "").strip()
    if not normalized:
        raise ValueError("@link requires non-empty ref")

    def decorator(fn: F) -> F:
        return _prepend_marker(fn, LINKS_ATTR, normalized)

    return decorator


def rule(text: str) -> Callable[[F], F]:
    """Attach one function-level review rule (0..N): reviewer guidance for this
    handler — what to watch when reviewing it. Unlike @spec (a contract the code
    already satisfies), a rule is an instruction to the reviewer. Stackable;
    preserves declaration order."""
    normalized = (text or "").strip()
    if not normalized:
        raise ValueError("@rule requires non-empty text")

    def decorator(fn: F) -> F:
        return _prepend_marker(fn, RULES_ATTR, normalized)

    return decorator


def get_cases(fn: Callable) -> list[CaseSpec]:
    """Return CaseSpec list attached to ``fn`` in source order."""
    return list(getattr(fn, CASES_ATTR, []))


def get_spec(fn: Callable) -> str | None:
    """Return the spec text attached to ``fn``, or None."""
    return getattr(fn, SPEC_ATTR, None)


def get_spec_id(fn: Callable) -> str | None:
    """Return the optional canonical spec id attached to ``fn``."""
    return getattr(fn, SPEC_ID_ATTR, None)


def get_links(fn: Callable) -> list[str]:
    """Return the curated links attached to ``fn`` in source order."""
    return list(getattr(fn, LINKS_ATTR, []))


def get_rules(fn: Callable) -> list[str]:
    """Return the review rules attached to ``fn`` in source order."""
    return list(getattr(fn, RULES_ATTR, []))


# --------------------------------------------------------------------------- hashing


def hash_text(text: str) -> str:
    """SHA256 first 8 hex chars over stripped text. Stable across runs."""
    normalized = (text or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:8]


def case_hash(c: Case, spec_text: str | None, spec_id: str | None = None) -> str:
    """Joint hash of case fields + enclosing @spec text.

    Bundling spec into the hash means editing @spec marks all that endpoint's
    scripts stale with a single hash field — no separate spec_hash needed.
    """
    parts = [
        c.id,
        "\n--desc--\n",
        c.desc or "",
        "\n--input--\n",
        c.input or "",
        "\n--expect--\n",
        c.expect or "",
        "\n--forbid--\n",
        c.forbid or "",
        "\n--spec--\n",
        spec_text or "",
        "\n--spec-id--\n",
        spec_id or "",
    ]
    return hash_text("".join(parts))


# --------------------------------------------------------------------------- yaml loader


def load_cases_file(path: Path, source_module: str) -> list[Case]:
    """Parse a ``*_cases.yaml`` into a flat Case list.

    Two yaml shapes supported:

    Single-endpoint::

        endpoint: create_note
        cases:
          - id: name_too_long
            desc: "..."

    Multi-endpoint::

        endpoints:
          create_note:
            cases: [...]
          delete_note:
            cases: [...]
    """
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cases: list[Case] = []
    if "endpoints" in raw:
        endpoints = raw["endpoints"] or {}
        if not isinstance(endpoints, dict):
            raise ValueError(f"{path}: 'endpoints' must be a mapping of name → section")
        for endpoint_name, section in endpoints.items():
            cases.extend(
                _parse_section(path, source_module, endpoint_name, section or {})
            )
    elif "endpoint" in raw:
        cases.extend(_parse_section(path, source_module, raw["endpoint"], raw))
    else:
        raise ValueError(f"{path}: must declare either 'endpoint' or 'endpoints'")
    return cases


def _parse_section(
    path: Path,
    source_module: str,
    endpoint: str,
    section: dict[str, Any],
) -> list[Case]:
    entries = section.get("cases") or []
    out: list[Case] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: each case must be a mapping, got {entry!r}")
        case_id = entry.get("id")
        desc = entry.get("desc")
        if not case_id or not desc:
            raise ValueError(f"{path}: case missing id or desc: {entry!r}")
        if case_id in seen_ids:
            raise ValueError(
                f"{path}: duplicate case id {case_id!r} under endpoint {endpoint}"
            )
        seen_ids.add(case_id)
        group = (entry.get("group") or DEFAULT_GROUP).strip()
        if not GROUP_PATTERN.match(group):
            raise ValueError(
                f"{path}: case {case_id!r} group {group!r} must match {GROUP_PATTERN.pattern}"
            )
        out.append(
            Case(
                id=case_id,
                desc=desc.strip(),
                input=(entry.get("input") or "").strip(),
                expect=(entry.get("expect") or "").strip(),
                forbid=(entry.get("forbid") or "").strip(),
                group=group,
                endpoint=endpoint,
                source_module=source_module,
                cases_file=path,
                source="yaml",
            )
        )
    return out
