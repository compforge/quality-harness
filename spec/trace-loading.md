# Trace loading contract

This optional runtime capability is implemented by Python and TypeScript. Their Source adapters and
storage layouts may differ; the loading and analysis semantics below are language-neutral.

## Ownership and execution

A Source selects trace IDs, fetches complete projected span skeletons and reads evidence by stable
references. It owns backend pagination, field projection and completeness checks. Environment discovery,
credentials and business query compilation remain host responsibilities. A Session owns its Source,
reading budget, active trace leases and evidence caches. Closing the Session MUST stop admission,
cancel outstanding reads, settle work, close the Source and remove any temporary workspace it owns.
Sources MUST bound external I/O and honor their runtime's cancellation mechanism. A Source instance MUST
NOT be owned by multiple open Sessions.

Selection fixes a Dataset's unique trace members without constructing trees. The first skeleton read
fixes that trace's observed span identities. Structural projection MUST include the metadata required by
business classification and correlation, not just parent IDs and timestamps. Later projection extensions
MUST read the frozen references rather than fetch a replacement span set. Evidence loading MUST NOT
reclassify nodes or change their parents. This is an observed membership guarantee, not a globally
consistent backend snapshot. A fresh selection may observe later spans.

Each active trace has a lease. A lease owns mutable analysis state, fact computation and measurements.
Concurrent leases MUST NOT share mutable trees or computed facts. Access through the analysis runtime
MUST fail after release. Dataset evidence may persist after leases and Sessions close and may be reused
by a later run with the same Source identity. Re-evaluation MUST create fresh computation state.

## Policy and dependencies

`lazy=true` reads evidence only when requested. `lazy=false` preloads the configured fields when the
current trace becomes active; an unspecified field selection means full evidence, an empty selection
means no additional fields. The policy MUST NOT eagerly load an entire Dataset, run every computation,
or depend on whether the caller will render HTML. Later requests outside the preload selection use
the same loader. Equal consumed evidence MUST produce equal analysis results in both modes.

A consumer declares dependencies on span evidence fields or named node facts. Producers and transforms
perform pure computation after those dependencies are ready. Measurers may also declare dependencies;
async detectors may request facts and measurements from their analysis context. The runtime MUST share
in-flight computation and reject dependency cycles, including cycles between concurrent requests.
FactTransform remains a computation over modeled facts, with no direct backend access.

Assembly can defer brief projection. Explicit view preparation resolves projection dependencies and,
when requested, full details before rendering. Renderers MUST NOT read evidence or execute analysis.
Offline HTML detail decompression is a separate presentation mechanism.

## Evidence reuse, errors and resource bounds

Evidence references contain trace/span identities and, where available, backend record identity.
Caches MUST isolate Source namespaces and distinguish unloaded fields, present values, present empty
values, absent fields and failed reads. Missing or expired evidence MUST fail explicitly rather than
becoming an empty field. Failed reads MUST NOT be recorded as successful field cache entries.

Concurrent requests for one trace SHOULD be coalesced and MUST reuse completed evidence reads.
Reading concurrency is bounded across Datasets in the same Session. Active trace count and decoded
trace evidence size are bounded separately. Preload and demand reads MUST enforce the same budgets;
budget exhaustion MUST fail even if the requested evidence is not used by a computation. A failed
unused preload field may leave unrelated analysis available; consuming failed evidence must retry
successfully or report failure.

A byte budget is a working-evidence limit, not an exact process RSS guarantee: backend response decoding,
JSON parsing, user computations and HTML serialization may allocate additional memory. Implementations
MUST document their storage and import limits rather than implying these allocations are eliminated.

## Conformance

Both SDK test suites consume [`conformance/trace/loading.json`](../conformance/trace/loading.json).
It fixes the evidence and expectations for lazy, full preload, selected preload, unrelated preload and
empty preload: no trees during selection, initial field state/read count, concurrent fact reuse,
measurement equality, empty versus absent fields, and lease invalidation.

Language-specific tests additionally cover Source projection, stored evidence identity, persisted cache
reuse, namespace isolation, concurrent cycles, resource budgets, cancellation and offline report equality.
This capability does not require a Dataset statistics report or the separate detector dependency-graph
execution capability.
