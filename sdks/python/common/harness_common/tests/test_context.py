import time
from dataclasses import dataclass
from unittest.mock import patch

from harness_common import (
    ClientProvider,
    ClientManager,
    DataSource,
    Environment,
    EnvironmentContext,
    EnvironmentBudgets,
    FixtureContext,
    run_environment,
)


class Probe:
    def __init__(self):
        self.ready = False
        self.closed = False

    async def initialize(self):
        self.ready = True

    async def dispose(self):
        self.closed = True


@dataclass
class ProbeSource:
    client_key: str

    def create_client(self, clients):
        return Probe()


@dataclass(frozen=True)
class ProbeEnvironment(Environment, ClientProvider[Probe]):
    @property
    def client_key(self):
        return "environment:" + self.name

    def create_client(self, clients):
        return Probe()


async def test_environment_and_data_providers_share_root_lifecycle_without_fixture():
    environment = ProbeEnvironment("test")
    data_source: DataSource[Probe] = ProbeSource("database")
    async with ClientManager() as clients:
        ctx = EnvironmentContext(environment, clients, time.monotonic() + 5)
        env_client = await ctx.clients.get(ctx.environment)
        db_client = await ctx.clients.get(data_source)
        assert env_client is await ctx.clients.get(ProbeEnvironment("test"))
        assert env_client is not db_client
        assert env_client.ready and db_client.ready
        assert not env_client.closed and not db_client.closed
        assert "phase" not in vars(ctx)
    assert env_client.closed and db_client.closed


def test_context_budget_uses_monotonic_clock_and_clamps_expiration():
    ctx = EnvironmentContext(Environment("test"), ClientManager(), 100)
    with patch("harness_common.context.time.monotonic", return_value=98):
        assert ctx.remaining_s == 2
    with patch("harness_common.context.time.monotonic", return_value=101):
        assert ctx.remaining_s == 0


async def test_fixture_phase_is_an_extension_not_a_context_requirement():
    phases = []

    class Fixture:
        async def prepare(self, ctx: FixtureContext, state):
            assert isinstance(ctx, EnvironmentContext)
            phases.append(ctx.phase)

        async def cleanup(self, ctx: FixtureContext, state):
            phases.append(ctx.phase)

    async def execute(ctx: EnvironmentContext, state):
        return ctx.environment.name

    result = await run_environment(
        Environment("test"),
        None,
        Fixture(),
        execute,
        budgets=EnvironmentBudgets(1, 1, 1, 1),
    )
    assert result.healthy
    assert result.result == "test"
    assert phases == ["prepare", "cleanup"]
