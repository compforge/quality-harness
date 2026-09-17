from harness_common import ClientManager, DataSource
from harness_common.client import _ClientBorrower


class ExampleClient:
    async def initialize(self) -> None:
        pass

    async def dispose(self) -> None:
        pass


class ExampleSource(DataSource[ExampleClient]):
    @property
    def client_key(self) -> str:
        return "shared-db"

    def create_client(self, clients: _ClientBorrower) -> ExampleClient:
        return ExampleClient()


async def test_description_is_optional_and_does_not_change_client_identity() -> None:
    source = ExampleSource()
    assert source.description is None
    described = ExampleSource()
    described.description = "Read-only conversation history"
    async with ClientManager() as clients:
        assert await clients.get(source) is await clients.get(described)
    assert described.description == "Read-only conversation history"
