"""Eval's runtime projection of one canonical CaseSet and how eval reads a Case.

Cases are the harness-neutral ``spec_case.model.Case`` — there is no eval-private case class.
What is eval's own is the **reading** of one: ``eval_view`` projects a canonical case onto
the fields eval's solver / scorer need, encoding eval's autonomous interpretation of ``input`` and
``judge.eval`` (``common.case`` deliberately leaves both schemaless). It is a read-only view,
not a model — nothing is stored or serialized through it (the canonical ``common.Case`` is the
only input model; ``dump_cases_yaml`` writes it back via ``case_to_raw``). Do not let it grow
back into an ``EvalCase``.

eval's slice of a case:

- ``input.query`` — the stimulus (required).
- ``input.candidate_sources`` — the retrieval pool this query scopes to (empty = whole
  subject). It lives in ``input`` because it changes *how the request is made* (the solver
  narrows the call), not how the answer is judged.
- ``judge.eval.{expected_behavior, ground_truth, evidence_sources}`` — the **contract**
  fields that drive scoring (answer vs refuse, the gold answer, the gold sources for
  retrieval-recall). They live under ``judge.eval`` because they are judgment criteria.
- ``facets`` — typed classification (key=value with a per-key value domain), validated
  against the set's vocab so a constrained axis can't rot (a free ``difficulty`` column
  filling with ``domain`` / ``novel``). Reported as the case's ``dimensions``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# FacetSpec / FacetSchema are the harness-neutral facet vocabulary, shared via common.
from spec_case.facets import FacetSchema as FacetSchema
from spec_case.facets import FacetSpec as FacetSpec
from spec_case.model import Case as Case
from spec_case.model import case_to_raw


@dataclass(frozen=True)
class EvalView:
    """eval's read of one ``common.Case`` — the fields the solver / scorer consume. A
    transient projection (see module docstring), never persisted; the canonical case is the
    source of truth."""

    query: str
    expected_behavior: str
    ground_truth: str | None
    dimensions: dict[str, str]
    evidence_sources: list[str]
    candidate_sources: list[str]


def eval_view(case: Case) -> EvalView:
    """Project a canonical ``common.Case`` onto eval's fields, validating eval's contract.

    Raises ``ValueError`` if the case is not a well-formed eval case: ``input.query`` is
    required, and a ``refuse`` case must not carry a ``ground_truth`` (there is no gold answer
    to a query the system should decline).
    """
    j = case.judge.get("eval") or {}
    query = case.input.get("query")
    if not query:
        raise ValueError(f"case {case.id}: eval case needs a non-empty `input.query`")
    expected_behavior = j.get("expected_behavior", "answer")
    ground_truth = j.get("ground_truth")
    if expected_behavior == "refuse" and ground_truth is not None:
        raise ValueError(f"case {case.id}: refuse case must not set ground_truth")
    return EvalView(
        query=query,
        expected_behavior=expected_behavior,
        ground_truth=ground_truth,
        dimensions=dict(case.facets),
        evidence_sources=list(j.get("evidence_sources") or []),
        candidate_sources=list(case.input.get("candidate_sources") or []),
    )


def dump_cases_yaml(cases: list[Case]) -> str:
    """Serialize canonical cases as a reusable CaseSet fragment.

    The single write path for importers/builders — they hand canonical ``Case`` objects, not
    hand-assembled YAML (which would re-implement escaping). Round-trips through
    ``common.case.case_from_raw``.
    """
    body = {"cases": [case_to_raw(c) for c in cases]}
    return yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=4096)


class SourceRecord(BaseModel):
    """One document a corpus provides — SUT-agnostic. A consumer's provisioner turns
    these into its own source representation (upload a file, index text, ...).

    ``uri`` points at the content (a path resolved relative to the evalset file, or a
    URL); ``content`` is inline text (an alternative to ``uri`` — e.g. straight from an
    importer before it is written to disk). ``meta`` carries SUT-specific hints
    (``resource_type`` ...) without the framework needing to know them.
    """

    model_config = ConfigDict(extra="forbid")

    name: str  # display name / identity (shown in citations; used by retrieval-recall)
    uri: str | None = None
    content: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> SourceRecord:
        if (self.uri is None) == (self.content is None):
            raise ValueError(f"source {self.name!r} needs exactly one of `uri` or `content`")
        return self


class EvalSet(BaseModel):
    """Resolved runtime view of one canonical spec-case CaseSet.

    ``caseset`` is the shared identity used by Eval and Perf. Sources are resolved for the
    local runtime, while cases and facet vocabulary retain the canonical asset semantics.
    The ``corpus`` property is Eval's name for the provisioned source collection and is
    deliberately derived from the CaseSet identity rather than configured separately.
    """

    # arbitrary_types_allowed: `cases` holds the canonical spec-case Case (a dataclass, not a
    # pydantic model) — pydantic treats it as an opaque value; cases are built + validated by
    # the config layer (case_from_raw) and validate_against, not re-parsed here.
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    caseset: str
    facet_schema: FacetSchema = Field(default_factory=FacetSchema)
    sources: list[SourceRecord] = Field(default_factory=list)
    cases: list[Case] = Field(min_length=1)
    focus: str | None = None

    @property
    def corpus(self) -> str:
        return self.caseset

    def validate_against(self, schema: FacetSchema) -> None:
        seen: set[str] = set()
        for c in self.cases:
            if c.id in seen:
                raise ValueError(f"duplicate case id: {c.id}")
            seen.add(c.id)
            schema.validate_dimensions(c.id, c.facets)  # facets = the case's classification
            eval_view(c)  # eval contract (query present, refuse⇒no ground_truth) — fail loud
