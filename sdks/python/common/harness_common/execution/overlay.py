"""Experiment-level ``Overlay`` — a per-run, non-shared layer that, keyed by a declared
catalog's ids, overrides each item's default parameter. The middle of the symmetry::

    catalog (shareable · declares items + their defaults)
        → overlay (THIS experiment's keyed overrides of those defaults)
            → verdict (output · keyed by the same ids)

Two instances in this repo, structurally identical, semantically distinct — and that second
instance is the whole reason this concept earns a type (one instance is not enough to name a
pattern; two is)::

    perf : experiment ``mix``     key = case.id       default 1.0          (load distribution)
    eval : experiment ``weights`` key = metric.NAME   default WEIGHT / 0   (scoring weight)

Industry calls this same shape an *overlay* (kustomize: base + overlay) or *overrides*
(Hydra); the per-task ``weight`` in Locust / k6 and ``aggregate_metric_list`` weighting in
lm-evaluation-harness are the domain-specific cousins. No single field owns it, which is why
it never grew a home here on its own — hence this module.

DISCIPLINE — read before extending (this is why we wrote a type instead of a loose ``dict``;
the type exists to make these four rules survive a new session / a Codex hand-off):

1. An overlay value is NEVER an identity attribute of the catalog item. It is "how THIS
   experiment USES the item", not "what the item IS". Do not migrate case-identity fields
   (``input`` / ``facets``) or metric-identity fields into it; and do not let ``timeout`` /
   SLO-override / repeat accrete onto the ``Case`` / metric to dodge this — per-experiment
   knobs go in an overlay, not on the catalog item. (Naming guard: it is the "experiment
   workload mix", never a "perf case field". ``common.case`` already states this: *case ⟂
   experiment params*.)
2. Unknown key → ERROR, never silent. A key that hits no catalog id is a typo or a stale
   config; swallowing it (the historical ``dict.get`` behaviour of eval's ``resolve_weights``)
   hides drift. ``resolve`` validates against the *post-filter* catalog — if the caller
   already narrowed the items (eval drops ``measure`` metrics; perf may facet-filter cases),
   validate against what actually remains.
3. Missing key → the catalog's declared default, not 0 — except where the item itself
   declares 0 as its default (an eval ``DIAGNOSTIC`` metric), which is the item's call to
   make, expressed through ``default_of``, not the overlay's.
4. An overlay is per-experiment and NOT shared / catalogued. It does not belong in the
   ``case.yaml`` or the metric registry; it lives on the ``Experiment``.

WHY a thin parametric helper and not a shared base class for perf/eval: the two instances
share *behaviour* (resolve-with-default + unknown-key validation), not *type* — their keys
(case.id vs metric NAME) and value semantics differ. So each harness *calls* this; neither
*inherits* it. The value type is ``float`` because both instances are scalar weights today;
if a third instance needs a richer per-id value (e.g. ``dict[id, MixParams]``), generalise
the value type then — not pre-emptively.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Overlay:
    """A per-experiment keyed override of a declared catalog's per-item defaults.

    ``overrides`` maps catalog-id → value. Build it from an experiment field (``exp.mix`` /
    ``exp.weights``); resolve it against the live catalog at use time. See the module
    docstring for the discipline this type exists to keep.
    """

    overrides: Mapping[str, float] = field(default_factory=dict)

    def resolve(
        self, catalog_ids: Iterable[str], default_of: Callable[[str], float]
    ) -> dict[str, float]:
        """Effective value for every id in the catalog: override else ``default_of(id)``.

        ``catalog_ids`` is the *post-filter* set of ids the experiment may legitimately key
        (rule 2). An override referencing an id outside it raises ``ValueError`` rather than
        being silently dropped.
        """
        ids = list(catalog_ids)
        known = set(ids)
        unknown = sorted(k for k in self.overrides if k not in known)
        if unknown:  # rule 2 — an off-catalog key is drift, not a no-op
            raise ValueError(
                f"overlay references unknown catalog id(s): {unknown}; known: {sorted(known)}"
            )
        return {i: self.overrides.get(i, default_of(i)) for i in ids}  # rule 3
