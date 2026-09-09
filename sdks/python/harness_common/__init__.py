"""common — harness-neutral shared code and runtime identities.

The harnesses (e2e / eval / perf / trace / trajectory) depend on this package but never on each
other; only genuinely neutral code lives here (no harness domain concept leaks in).

The canonical case model — `Case` / `CaseSet` / `FacetSchema` and the `Face` / `FACES`
judgment-face enum — lives upstream in `spec_case.model` / `spec_case.facets`
(the spec-case package, `[model]` extra): the case format is the asset layer's contract,
and this repo is one of its runners. Import those names from `spec_case` directly.

`Forge` and `Repository` identify where source lives; `Product` names a business product;
`Component` identifies a buildable unit within a Repository. `Environment` identifies where
execution happens. `Service` identifies a Component's runtime presence in one Environment.
`Operation` identifies a capability exposed by a Service; `HttpOperation` adds its HTTP
transport contract. An `Execution` groups domain-defined work such as an e2e CaseRun or
a perf Trial; each `OperationRun` owns one raw `Outcome`. Domain engines own scheduling
details. A domain `Reducer` projects the recorded Outcomes into one or more
`Artifact` objects without calling the tested Service again. `Deployment` records an
attempt applied through a `Deployer`.
`Experiment` names reproducible verification intent; `ExperimentRun` records one execution
and references its `Artifact` outputs.
"""

from __future__ import annotations

from harness_common.artifact import Artifact as Artifact
from harness_common.component import Component as Component
from harness_common.deployment import Deployer as Deployer
from harness_common.deployment import Deployment as Deployment
from harness_common.environment import Environment as Environment
from harness_common.environment import KubernetesEnvironment as KubernetesEnvironment
from harness_common.execution import Execution as Execution
from harness_common.experiment import Experiment as Experiment
from harness_common.experiment import ExperimentRun as ExperimentRun
from harness_common.forge import Forge as Forge
from harness_common.operation import HttpOperation as HttpOperation
from harness_common.operation import Operation as Operation
from harness_common.operation import OperationRun as OperationRun
from harness_common.outcome import Outcome as Outcome
from harness_common.product import Product as Product
from harness_common.repository import Repository as Repository
from harness_common.reducer import Reducer as Reducer
from harness_common.service import Service as Service
