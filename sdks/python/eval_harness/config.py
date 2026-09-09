"""Load a single ``experiment.yaml`` → ``Experiment``.

One entry file describes the whole run: ``evalset`` (a canonical CaseSet reference),
``service`` (base SUT config), ``arms`` / ``matrix`` (comparison arms),
``metrics`` and ``weights``. Relative CaseSet paths resolve from ``materials_root``;
the CaseSet owns its identity, sources, facet vocabulary, cases and per-face judgment.

String values support ``${VAR}`` / ``${VAR:-default}`` interpolation from the
environment, so secrets (e.g. ``service.llm.api_key``) stay out of the yaml /
git — an unset var with no default fails loud rather than sending an empty value.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from spec_case.model import CaseSet, Source, load_caseset, validate

from eval_harness.model.evalset import EvalSet, SourceRecord
from eval_harness.model.experiment import Arm, Experiment, Service

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate(obj: Any) -> Any:
    """Recursively resolve ``${VAR}`` / ``${VAR:-default}`` from the environment."""
    if isinstance(obj, str):

        def _repl(m: re.Match) -> str:
            var, default = m.group(1), m.group(2)
            if var in os.environ:
                return os.environ[var]
            if default is not None:
                return default
            raise ValueError(f"experiment config references unset env var ${{{var}}} (no default)")

        return _ENV_PATTERN.sub(_repl, obj)
    if isinstance(obj, dict):
        return {k: _interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate(x) for x in obj]
    return obj


def _source(s: Source, base: Path) -> SourceRecord:
    """Resolve a source's ``uri`` to an absolute path (relative to the evalset file's dir);
    URLs and inline content pass through unchanged."""
    uri = s.uri
    if uri and "://" not in uri and not Path(uri).is_absolute():
        uri = str((base / uri).resolve())
    return SourceRecord(
        name=s.name,
        uri=uri,
        content=s.content,
        meta=dict(s.meta),
    )


def _load_evalset(spec: Any, root: Path) -> EvalSet:
    """Load and resolve one canonical CaseSet for Eval.

    Source paths belong to the CaseSet file, never to the process cwd or experiment.
    The canonical loader and integrity rules remain owned by spec-case.
    """
    if not isinstance(spec, str) or not spec:
        raise ValueError("evalset must be a non-empty canonical CaseSet path")
    es_path = Path(spec).expanduser()
    if not es_path.is_absolute():
        es_path = root / es_path
    case_set: CaseSet = load_caseset(es_path)
    validate(case_set)
    return EvalSet(
        caseset=case_set.caseset,
        facet_schema=case_set.facet_schema,
        sources=[_source(s, es_path.parent) for s in case_set.sources],
        cases=list(case_set.cases),
        focus=case_set.focus or None,
    )


def _service(raw: dict[str, Any]) -> Service:
    """Normalize the concise YAML identity into common value objects."""
    data = dict(raw)
    if data.get("component"):
        component = dict(data["component"])
        if component.get("repository"):
            repository = dict(component["repository"])
            forge = repository.get("forge", "")
            if not isinstance(forge, dict):
                repository["forge"] = {"name": str(forge)}
            component["repository"] = repository
        data["component"] = component
    return Service.model_validate(data)


def load_experiment(path: str | Path, materials_root: str | Path | None = None) -> Experiment:
    path = Path(path)
    raw = _interpolate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    # experiments/<x>.yaml lives under materials/, so materials root = parent of experiments/
    root = Path(materials_root) if materials_root else path.parents[1]

    # `evalset` is single-CaseSet sugar; `evalsets` runs several canonical sets together.
    raw_evalsets = raw.get("evalsets") or ([raw["evalset"]] if "evalset" in raw else [])
    if not raw_evalsets:
        raise ValueError("experiment config needs `evalset` or `evalsets`")
    evalsets = [_load_evalset(e, root) for e in raw_evalsets]
    if "facets" in raw:
        raise ValueError("experiment facets cannot override canonical CaseSet facets")

    exp = Experiment(
        name=raw["name"],
        description=raw.get("description") or "",
        service=_service(raw["service"]),
        evalsets=evalsets,
        arms=[Arm(**e) for e in (raw.get("arms") or [])],
        matrix=raw.get("matrix") or {},
        metrics=raw.get("metrics") or [],
        weights=raw.get("weights") or {},
        heavy_fields=raw.get("heavy_fields") or [],
    )
    schema = exp.facet_schema()
    for es in exp.evalsets:
        es.validate_against(schema)  # fail loud on bad facet values
    return exp
