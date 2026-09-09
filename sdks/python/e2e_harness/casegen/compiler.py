"""casegen — build-time compiler: NL markers (``@case``/``@spec``) → structured ``case.yaml``.

The build-time half of judgment-as-data (see ``docs/casegen.md``): ``discover`` extracts the NL
markers, a ``Compiler`` turns each into a structured ``common.Case`` (with ``judge.e2e.assert``),
and casegen writes a committed ``case.yaml`` that ``e2e run`` executes. An ``intent hash`` (the
marker's ``case_hash`` over NL + ``@spec``) is recorded per case in a top-level ``compiled_from``
map, so ``check`` is a deterministic, no-LLM drift gate and ``compile`` only re-compiles markers
whose intent actually drifted (preserving human/LLM-filled asserts on the rest).

This module is step ① — the deterministic skeleton (Compiler protocol + ``StubCompiler`` +
orchestration + ``check``). The real fill (``DraftCompiler`` → claude, then ``LLMCompiler`` →
``common.llm``) is a drop-in for ``Compiler``; nothing else changes.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from spec_case.model import Binding, Case, case_to_raw, load_caseset, validate
from e2e_harness.casegen.discover import DiscoverConfig, DiscoveredCase, discover


class Compiler(Protocol):
    """NL marker → structured ``common.Case``. The one seam the LLM lives behind."""

    def compile(self, nl: DiscoveredCase) -> Case: ...


class StubCompiler:
    """Deterministic, no-LLM — for testing orchestration/drift and as the abstraction's anchor.

    Projects only the identity (``id``) and leaves ``judge.e2e.assert`` empty; a real compiler
    (``DraftCompiler`` / ``LLMCompiler``) fills the asserts from the NL intent.
    """

    def compile(self, nl: DiscoveredCase) -> Case:
        return Case(
            id=nl.case.id,
            input={},
            judge={"e2e": {"assert": []}},
            binding=_binding(nl),
        )


class DraftCompiler:
    """v1, no LLM — a self-contained, fillable draft (mirrors eval-api's "hand to claude" loop).

    Projects the identity and **inlines the NL intent into ``desc`` as fill-guidance**, leaving
    ``input`` and ``judge.e2e.assert`` empty for a human / claude to complete. The guidance lives
    in ``desc`` (round-trips through yaml, excluded from ``case_hash``, human + LLM readable) so the
    structured ``input`` / ``judge`` namespaces stay clean. An unfilled draft (e2e face declared, no
    asserts) → ``error`` in ``e2e run`` — it can't be hidden green by a sibling pass; fill the
    asserts to make it judgeable. (The future ``LLMCompiler`` fills ``input`` + ``assert`` directly.)

    Why not auto-fill asserts by rule: ``input`` is NL (e.g. "req body"), can't be parsed to a real
    request deterministically — and asserts without a real ``input`` would false-fail at run. So a
    draft fills *neither*; both are completed together.
    """

    def compile(self, nl: DiscoveredCase) -> Case:
        c = nl.case
        guide = [c.desc] if c.desc else []
        guide.append(
            "—— casegen draft: fill input + judge.e2e.assert from the intent below ——"
        )
        for label, val in (
            ("input(NL)", c.input),
            ("expect", c.expect),
            ("forbid", c.forbid),
            ("spec", nl.spec_text),
        ):
            if val:
                guide.append(f"{label}: {val}")
        return Case(
            id=c.id,
            input={},
            desc="\n".join(guide),
            judge={"e2e": {"assert": []}},
            binding=_binding(nl),
        )


# --------------------------------------------------------------------------- serialization


def _case_to_dict(case: Case) -> dict:
    """Delegate canonical Case serialization to spec-case."""
    return case_to_raw(case)


def _binding(nl: DiscoveredCase) -> Binding:
    return Binding(
        symbol_id=nl.symbol_id,
        spec=nl.spec_text or "",
        spec_id=nl.spec_id,
    )


def _write_caseset(
    out: Path, caseset: str, cases: list[Case], compiled_from: dict[str, str]
) -> None:
    doc = {
        "caseset": caseset,
        # casegen-owned metadata: case_id → intent hash it was compiled from. `load_caseset`
        # ignores this top-level key; `check` reads it to detect marker drift.
        "compiled_from": dict(sorted(compiled_from.items())),
        "cases": [_case_to_dict(c) for c in cases],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _read_compiled(out: Path) -> tuple[dict[str, Case], dict[str, str]]:
    """Existing compiled file → (cases by id, ``compiled_from`` map). Empty if absent."""
    if not out.exists():
        return {}, {}
    cases = {c.id: c for c in load_caseset(out).cases}
    raw = yaml.safe_load(out.read_text()) or {}
    return cases, dict(raw.get("compiled_from") or {})


# --------------------------------------------------------------------------- orchestration


@dataclass
class CompileReport:
    compiled: list[str] = field(default_factory=list)  # new or drifted → (re)compiled
    reused: list[str] = field(
        default_factory=list
    )  # intent unchanged → existing kept (edits preserved)
    dropped: list[str] = field(
        default_factory=list
    )  # in old file but no longer a marker (orphaned)


def compile_cases(
    config: DiscoverConfig, compiler: Compiler, out: Path, *, caseset: str
) -> CompileReport:
    """(Re)compile drifted/new markers → write ``out``. Markers whose intent is unchanged keep
    their existing compiled case verbatim — so a human/LLM's filled asserts survive re-runs."""
    discovered = discover(config)
    prev_cases, prev_hashes = _read_compiled(out)
    rep = CompileReport()
    cases: list[Case] = []
    compiled_from: dict[str, str] = {}
    for dc in discovered:
        cid = dc.case.id
        compiled_from[cid] = dc.case_hash
        if cid in prev_cases and prev_hashes.get(cid) == dc.case_hash:
            cases.append(
                prev_cases[cid]
            )  # intent unchanged → keep (don't clobber filled asserts)
            rep.reused.append(cid)
        else:
            cases.append(compiler.compile(dc))  # new or drifted → recompile
            rep.compiled.append(cid)
    rep.dropped = sorted(set(prev_cases) - {dc.case.id for dc in discovered})
    _write_caseset(out, caseset, cases, compiled_from)
    return rep


@dataclass
class CheckReport:
    drifted: list[str] = field(
        default_factory=list
    )  # marker intent ≠ compiled (or never compiled)
    orphaned: list[str] = field(default_factory=list)  # compiled but marker gone

    @property
    def ok(self) -> bool:
        return not self.drifted and not self.orphaned


def check_cases(config: DiscoverConfig, out: Path) -> CheckReport:
    """Deterministic, no-LLM drift gate: compiled ``case.yaml`` must be in sync with the markers."""
    discovered = discover(config)
    _, prev_hashes = _read_compiled(out)
    marker_ids = {dc.case.id for dc in discovered}
    rep = CheckReport()
    rep.drifted = sorted(
        dc.case.id for dc in discovered if prev_hashes.get(dc.case.id) != dc.case_hash
    )
    rep.orphaned = sorted(set(prev_hashes) - marker_ids)
    return rep


def list_cases(config: DiscoverConfig, out: Path) -> list[tuple[str, str]]:
    """(case_id, status) where status ∈ in-sync / drifted / new / orphaned."""
    discovered = discover(config)
    prev_cases, prev_hashes = _read_compiled(out)
    rows: list[tuple[str, str]] = []
    for dc in discovered:
        cid = dc.case.id
        if cid not in prev_hashes:
            rows.append((cid, "new"))
        elif prev_hashes[cid] != dc.case_hash:
            rows.append((cid, "drifted"))
        else:
            rows.append((cid, "in-sync"))
    rows += [
        (cid, "orphaned")
        for cid in sorted(set(prev_cases) - {dc.case.id for dc in discovered})
    ]
    return rows


# --------------------------------------------------------------------------- CLI


def _config(args: argparse.Namespace) -> DiscoverConfig:
    src = Path(args.source)
    if not src.is_dir():
        raise SystemExit(f"casegen: --source {src} is not a directory")
    return DiscoverConfig(source_root=src, test_root=src)  # test_root unused by casegen


def _out(args: argparse.Namespace) -> Path:
    return Path(args.out)


def cmd_list(args: argparse.Namespace) -> int:
    for cid, status in list_cases(_config(args), _out(args)):
        print(f"  {status:<9} {cid}")
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    rep = compile_cases(
        _config(args), DraftCompiler(), _out(args), caseset=args.caseset
    )
    print(
        f"casegen: compiled {len(rep.compiled)}, reused {len(rep.reused)}, dropped {len(rep.dropped)} → {args.out}"
    )
    if rep.compiled:
        validate(load_caseset(_out(args)))  # the written file is a valid case set
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    rep = check_cases(_config(args), _out(args))
    if rep.ok:
        print("casegen: in sync ✓")
        return 0
    for cid in rep.drifted:
        print(f"  drifted   {cid}  (marker changed — re-run `casegen compile`)")
    for cid in rep.orphaned:
        print(f"  orphaned  {cid}  (compiled case has no marker)")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="casegen", description="Compile NL @case markers → structured case.yaml."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in [
        ("list", cmd_list, "list markers + drift status"),
        ("compile", cmd_compile, "(re)compile drifted/new markers → case.yaml"),
        ("check", cmd_check, "drift gate (no LLM): markers vs compiled case.yaml"),
    ]:
        p = sub.add_parser(name, help=help_)
        p.add_argument(
            "--source", required=True, help="dir to scan for @case/@spec markers"
        )
        p.add_argument("--out", required=True, help="compiled case.yaml path")
        if name == "compile":
            p.add_argument(
                "--caseset", default="cases", help="case set name written into the file"
            )
        p.set_defaults(func=fn)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
