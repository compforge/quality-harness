from harness_common import (
    Artifact,
    Component,
    Deployer,
    Deployment,
    Environment,
    Execution,
    Experiment,
    ExperimentRun,
    Forge,
    HttpOperation,
    KubernetesEnvironment,
    Operation,
    OperationRun,
    Outcome,
    Product,
    Repository,
    Service,
)


def test_service_identity_is_component_within_environment() -> None:
    dev = Environment(name="dev")
    poc = Environment(name="poc")
    chat = Component(
        repository=Repository(
            forge=Forge(name="codebase"),
            path="example/chat-server",
        ),
        name="chat-server",
    )

    assert Service(name="chat-server", component=chat, environment=dev) == Service(
        name="chat-server",
        component=chat,
        environment=dev,
    )
    assert Service(name="chat-server", component=chat, environment=dev) != Service(
        name="chat-server",
        component=chat,
        environment=poc,
    )


def test_service_name_is_part_of_runtime_identity() -> None:
    environment = Environment(name="dev")
    component = Component(
        repository=Repository(forge=Forge(name="github"), path="org/monorepo"),
        name="gateway",
    )

    assert Service(
        name="public-gateway", component=component, environment=environment
    ) != Service(name="internal-gateway", component=component, environment=environment)


def test_components_are_scoped_by_repository() -> None:
    assert Component(
        repository=Repository(forge=Forge(name="github"), path="org/api"),
        name="server",
    ) != Component(
        repository=Repository(forge=Forge(name="github"), path="org/worker"),
        name="server",
    )


def test_repository_is_scoped_by_forge() -> None:
    assert Repository(forge=Forge(name="github"), path="org/repo") != Repository(
        forge=Forge(name="gitlab"),
        path="org/repo",
    )


def test_product_is_an_independent_business_identity() -> None:
    assert Product(name="example-product") == Product(name="example-product")


def test_kubernetes_environment_extends_environment_with_cluster_access() -> None:
    environment = KubernetesEnvironment(
        name="dev",
        kubeconfig="~/.kube/config",
        context="dev-cluster",
    )

    assert isinstance(environment, Environment)
    assert environment.kubeconfig == "~/.kube/config"


def test_http_operation_extends_operation_with_http_contract() -> None:
    operation = HttpOperation(name="create_widget", method="POST", path="/v1/widgets")

    assert isinstance(operation, Operation)
    assert operation.name == "create_widget"
    assert operation.method == "POST"
    assert operation.path == "/v1/widgets"


def test_deployment_targets_one_service() -> None:
    service = Service(
        name="widget-server",
        component=Component(
            repository=Repository(
                forge=Forge(name="github"),
                path="example/widget",
            ),
            name="server",
        ),
        environment=Environment(name="dev"),
    )

    deployment = Deployment(service=service)

    assert deployment.service is service
    assert hasattr(Deployer, "deploy")


def test_experiment_run_owns_typed_artifact_references() -> None:
    experiment = Experiment(name="widget-contract")
    run = ExperimentRun(
        experiment=experiment.name, run_id="run-1", created_at="2026-09-03"
    )

    run.add_artifact("verdict", "verdict.json")
    run.add_artifact("raw", "outcomes/requests.jsonl")
    run.add_artifact("raw", "outcomes.jsonl")

    assert run.artifacts == [
        Artifact(name="verdict", path="verdict.json"),
        Artifact(name="raw", path="outcomes.jsonl"),
    ]
    assert run.artifact_paths() == {
        "verdict": "verdict.json",
        "raw": "outcomes.jsonl",
    }


def test_experiment_run_retains_execution_operation_and_outcome_hierarchy() -> None:
    service = Service(
        name="widget-server",
        component=Component(
            repository=Repository(
                forge=Forge(name="github"), path="example/widget-server"
            ),
            name="server",
        ),
        environment=Environment(name="dev"),
    )
    outcome = Outcome()
    operation_run = OperationRun(
        id="case-1:0",
        service=service,
        operation=HttpOperation(name="create", method="POST", path="/widgets"),
        outcome=outcome,
    )
    execution = Execution(id="case-1", operation_runs=[operation_run])
    run = ExperimentRun(
        run_id="run-1",
        experiment="widget-contract",
        created_at="2026-09-03",
        executions=[execution],
    )

    assert list(run.operation_runs()) == [operation_run]
    assert list(run.outcomes()) == [outcome]
