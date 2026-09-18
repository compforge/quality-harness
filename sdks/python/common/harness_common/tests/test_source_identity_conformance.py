"""Source identity wire values shared with the Go common package."""

import json
from dataclasses import asdict, replace
from pathlib import Path

from harness_common import Component, Forge, Product, Repository


def test_source_identity_conformance() -> None:
    root = Path(__file__).resolve().parents[5]
    fixture = json.loads(
        (root / "conformance/common/source-identities.json").read_text()
    )

    def repository(value: dict) -> Repository:
        return Repository(forge=Forge(**value["forge"]), path=value["path"])

    products = [Product(**item) for item in fixture["products"]]
    repositories = [repository(item) for item in fixture["repositories"]]
    components = [
        Component(
            repository=repository(item["repository"]),
            name=item["name"],
            description=item.get("description"),
            language=item.get("language"),
        )
        for item in fixture["components"]
    ]

    assert len(set(components)) == 3
    assert replace(components[0], description="Different description") == components[0]
    relabeled = replace(components[0], language="typescript")
    assert relabeled == components[0]
    assert hash(relabeled) == hash(components[0])
    assert components[2].language is None
    assert components[2].ecosystem is None
    assert {
        "products": [asdict(item) for item in products],
        "repositories": [asdict(item) for item in repositories],
        "components": [
            {key: value for key, value in asdict(item).items() if value is not None}
            for item in components
        ],
    } == fixture


def test_component_ecosystem_conformance() -> None:
    root = Path(__file__).resolve().parents[5]
    cases = json.loads(
        (root / "conformance/common/component-ecosystems.json").read_text()
    )
    repo = Repository(forge=Forge(name="github"), path="example/api")
    for case in cases:
        component = Component(repository=repo, name="api", language=case["language"])
        assert component.ecosystem == case["ecosystem"]
        assert "ecosystem" not in asdict(component)
