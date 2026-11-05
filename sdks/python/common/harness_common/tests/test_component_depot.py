"""Cross-language declarations preserve identity without inspecting a checkout."""

import json
from dataclasses import asdict, replace
from pathlib import Path

from harness_common import Bin, Component, Depot, Forge, Repository, Tool


def test_component_depot_conformance() -> None:
    """@case Tools and Depot preserve Component equality/hash and shared locations."""
    root = Path(__file__).resolve().parents[5]
    cases = json.loads((root / "conformance/common/component-depots.json").read_text())
    components = []
    for case in cases:
        value = case["component"]
        repository = value["repository"]
        component = Component(
            repository=Repository(Forge(**repository["forge"]), repository["path"]),
            name=value["name"],
            language=value.get("language"),
            tools=tuple(Tool(**tool) for tool in value["tools"])
            if "tools" in value
            else None,
            depot=Depot(tuple(Bin(**item) for item in value["depot"]["bins"]))
            if "depot" in value
            else None,
        )
        encoded = asdict(
            component,
            dict_factory=lambda pairs: {
                key: item for key, item in pairs if item is not None
            },
        )
        assert json.loads(json.dumps(encoded)) == value
        components.append(component)

    bare, node, python, shared, empty = components
    assert bare.tools is None and bare.depot is None
    assert node == bare and hash(node) == hash(bare)
    assert {bare: "present"}[node] == "present"
    assert replace(node, name="different") != node
    assert (
        replace(node, repository=Repository(Forge("gitlab"), node.repository.path))
        != node
    )
    assert empty.tools == () and empty.depot == Depot(())
    assert node.tools == (Tool("pnpm", ">=9 <10"), Tool("vite"))
    assert node.depot is not None and shared.depot is not None
    assert node != shared
    assert shared.depot.bins[0] == node.depot.bins[0]
    assert tuple(item.kind for item in node.depot.bins) == (
        "dependencies",
        "output",
        "cache",
    )
    assert python.depot == Depot((Bin(".venv", "dependencies"),))
