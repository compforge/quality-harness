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
`Workload` locates a declared runtime carrier; a Service can reference multiple workloads.
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

from harness_common.execution.artifact import Artifact as Artifact
from harness_common.system.component import Component as Component
from harness_common.system.deployment import Deployer as Deployer
from harness_common.system.deployment import Deployment as Deployment
from harness_common.system.environment import Environment as Environment
from harness_common.system.environment import HostEnvironment as HostEnvironment
from harness_common.system.host import Host as Host
from harness_common.system.environment import EnvironmentFacts as EnvironmentFacts
from harness_common.system.environment import EnvironmentSnapshot as EnvironmentSnapshot
from harness_common.execution.execution import Execution as Execution
from harness_common.execution.experiment import Experiment as Experiment
from harness_common.execution.experiment import ExperimentRun as ExperimentRun
from harness_common.system.forge import Forge as Forge
from harness_common.identity import Identity as Identity
from harness_common.execution.operation import HttpOperation as HttpOperation
from harness_common.execution.operation import Operation as Operation
from harness_common.execution.operation import OperationRun as OperationRun
from harness_common.execution.outcome import Outcome as Outcome
from harness_common.system.product import Product as Product
from harness_common.system.repository import Repository as Repository
from harness_common.execution.reducer import Reducer as Reducer
from harness_common.system.service import Service as Service
from harness_common.system.workload import KubernetesWorkload as KubernetesWorkload
from harness_common.system.workload import Workload as Workload
from harness_common.system.workload import WorkloadInstance as WorkloadInstance
from harness_common.system.workload import (
    KubernetesWorkloadInstance as KubernetesWorkloadInstance,
)

from harness_common.access.client import Client as Client
from harness_common.access.client import ClientManager as ClientManager
from harness_common.access.client import ClientProvider as ClientProvider
from harness_common.access.datasource import DataSource as DataSource
from harness_common.access.datasource import ServiceDataSource as ServiceDataSource
from harness_common.access.client import client_key as client_key
from harness_common.access.fixture import EnvironmentFixture as EnvironmentFixture
from harness_common.access.context import EnvironmentContext as EnvironmentContext
from harness_common.access.fixture import FixtureContext as FixtureContext
from harness_common.access.fixture import EnvironmentBudgets as EnvironmentBudgets
from harness_common.access.fixture import EnvironmentRun as EnvironmentRun
from harness_common.access.fixture import run_environment as run_environment

from harness_common.system.resource import Resource as Resource
from harness_common.system.resource import ServiceResource as ServiceResource
from harness_common.system.resource import ResourceInstanceRef as ResourceInstanceRef
from harness_common.system.resource import ResourceInstance as ResourceInstance
from harness_common.system.resource import ResourceMeasurement as ResourceMeasurement
from harness_common.system.resource import ResourceCoverage as ResourceCoverage
from harness_common.system.resource import ResourceSnapshot as ResourceSnapshot
