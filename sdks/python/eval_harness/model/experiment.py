"""Orchestration spec: ``Service`` (base SUT config), ``Arm`` (comparison arm),
``Experiment`` (the whole thing).

An **Arm** is the unit that varies across the comparison ("变量"): it is the
base ``service`` config patched by ``overrides``, and it spans two layers —

- **heavy** (provisioned): the provisioned resource + ingested sources a solve queries
  against. Expensive, has a ``prepare()`` / ``clean()`` lifecycle (impl in
  ``produce/provision``), and a reuse **key**. Arms whose key matches share one
  provisioned resource (prepare once, clean once).
- **light** (config): model / request params, applied per call, free to switch.

``Arm.key`` hashes only the **heavy-affecting** fields (corpus + the overrides
that touch ``HEAVY_FIELDS``, default ``tenant_id``). Light overrides
(``params.*`` ...) are excluded — that is precisely why two Arms differing only
in light config share the same provisioned resource.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any

from harness_common import Component, Forge, KubernetesEnvironment, Repository
from harness_common import Experiment as BaseExperiment
from pydantic import BaseModel, ConfigDict, Field, model_validator
from spec_case.model import Case

from eval_harness.model.evalset import EvalSet, FacetSchema, FacetSpec


def _deep_set(data: dict[str, Any], path: list[str], value: Any) -> None:
    cur = data
    for part in path[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[path[-1]] = value


def _deep_get(data: Any, path: list[str]) -> Any:
    cur = data
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


class LLMSpec(BaseModel):
    """Optional structured LLM config — the common comparison dimension.

    eval_harness only **carries + resolves** this (secrets via ``${ENV}``
    interpolation in the config loader); a consumer's Solver maps it to its SUT's
    per-request model config (e.g. the ``llm`` block), and the SUT
    decides whether to honor it. Lives on ``Service`` so Arm overrides patch it
    (``llm.model`` / ``llm.temperature`` ...); unless an experiment lists it in
    ``heavy_fields`` it is light, so swapping model shares the prepared resource.
    """

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    base_url: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    api_key: str | None = None  # prefer ${ENV}; never hardcode a secret in yaml
    extra: dict[str, Any] = Field(default_factory=dict)  # consumer-specific passthrough


class Service(BaseModel):
    """Base config of the system under test; all Arms derive from it (Arm = service ⊕ overrides).

    ``component`` and ``environment`` carry the shared runtime identity. The open ``config``
    bag remains the consumer's request shape (host, ids, and service-specific parameters),
    while ``llm`` is the one typed comparison dimension. Dotted overrides patch either
    without schema churn. Which paths are *heavy* (change ⇒ re-provision) is declared per
    experiment via ``Experiment.heavy_fields``, not hardcoded.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    component: Component = Field(
        default_factory=lambda: Component(
            repository=Repository(forge=Forge(name=""), path=""),
            name="",
        )
    )
    environment: KubernetesEnvironment = Field(
        default_factory=lambda: KubernetesEnvironment(name="")
    )
    config: dict[str, Any] = Field(default_factory=dict)
    llm: LLMSpec | None = None


class Arm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    overrides: dict[str, Any] = Field(default_factory=dict)

    def resolve(self, service: Service) -> Service:
        """Apply overrides onto the base service → the concrete config for this Arm."""
        data = service.model_dump()
        for dotted, val in self.overrides.items():
            _deep_set(data, dotted.split("."), val)
        return Service.model_validate(data)

    def key(self, corpus: str, service: Service, heavy_fields: list[str]) -> str:
        """Reuse identity of this Arm's heavy (provisioned) layer.

        Hash of (corpus + the heavy-affecting resolved fields, by dotted path). Two Arms
        with the same key share one provisioned resource — so a light-only difference
        (e.g. ``llm.model``) reuses it; an empty ``heavy_fields`` ⇒ corpus alone is the key.
        """
        dump = self.resolve(service).model_dump()
        heavy = {p: _deep_get(dump, p.split(".")) for p in heavy_fields}
        blob = json.dumps({"corpus": corpus, "heavy": heavy}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def expand_matrix(matrix: dict[str, list[Any]]) -> list[Arm]:
    """Cartesian product of axes → list[Arm] (sugar over an explicit arm list).

    ``{"params.target_searches": [3, 6], "tenant_id": ["a", "b"]}`` →
    4 Arms named like ``params.target_searches=3__tenant_id=a``.
    """
    if not matrix:
        return []
    keys = list(matrix)
    arms: list[Arm] = []
    for combo in itertools.product(*(matrix[k] for k in keys)):
        overrides = dict(zip(keys, combo, strict=True))
        name = "__".join(f"{k.split('.')[-1]}={v}" for k, v in overrides.items())
        arms.append(Arm(id=name, overrides=overrides))
    return arms


class Experiment(BaseModel, BaseExperiment):
    model_config = ConfigDict(extra="forbid")

    name: str
    # Free-text purpose of this eval ("这次评测在测什么"), surfaced in the report header and
    # CLI output. Cosmetic only — deliberately excluded from experiment_hash, so editing it
    # never invalidates a resumable checkpoint.
    description: str = ""
    service: Service
    # An experiment spans one or more CaseSets (each becomes one provisioned corpus): one
    # experiment, one worksheet, one report, with corpus as Eval's report dimension.
    evalsets: list[EvalSet] = Field(min_length=1)
    arms: list[Arm] = Field(default_factory=list)
    matrix: dict[str, list[Any]] = Field(default_factory=dict)
    metrics: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    # dotted service paths whose change re-provisions (e.g. "config.tenant_id"); [] ⇒ corpus
    # alone keys provisioning, so all arms share one resource per corpus.
    heavy_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_corpora(self) -> Experiment:
        case_sets = [es.caseset for es in self.evalsets]
        if len(set(case_sets)) != len(case_sets):
            raise ValueError(f"duplicate CaseSet across evalsets: {case_sets}")
        arm_ids = [arm.id for arm in self.resolved_arms()]
        if len(set(arm_ids)) != len(arm_ids):
            raise ValueError(f"duplicate arm id: {arm_ids}")
        return self

    @property
    def corpora(self) -> list[str]:
        return [es.corpus for es in self.evalsets]

    def cases(self) -> list[tuple[str, Case]]:
        """(corpus, case) over all evalsets — the experiment's full row source."""
        return [(es.corpus, c) for es in self.evalsets for c in es.cases]

    def facet_schema(self) -> FacetSchema:
        merged: dict[str, FacetSpec] = {}
        for evalset in self.evalsets:
            for name, spec in evalset.facet_schema.facets.items():
                previous = merged.get(name)
                if previous is not None and previous != spec:
                    raise ValueError(f"conflicting facet {name!r} across CaseSets")
                merged[name] = spec
        return FacetSchema(merged)

    def resolved_arms(self) -> list[Arm]:
        """Explicit ``arms`` plus matrix expansion; default to one identity Arm
        (a single run = a 1-Arm experiment, no special-casing)."""
        out = list(self.arms) + expand_matrix(self.matrix)
        if not out:
            out = [Arm(id="default", overrides={})]
        return out

    def experiment_hash(self) -> str:
        """Stable hash over the comparison-defining inputs; guards a resumed run
        against an edited yaml (arm_id labels must not silently drift)."""
        arms = [{"id": arm.id, "overrides": arm.overrides} for arm in self.resolved_arms()]
        payload = {
            "arms": sorted(arms, key=lambda arm: arm["id"]),
            "weights": dict(sorted(self.weights.items())),
            "metrics": sorted(self.metrics),
            # corpus-scoped case ids: spans all evalsets, collision-safe across corpora
            "cases": sorted(f"{corpus}/{c.id}" for corpus, c in self.cases()),
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]
