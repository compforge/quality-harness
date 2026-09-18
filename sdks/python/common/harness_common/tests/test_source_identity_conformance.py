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
        )
        for item in fixture["components"]
    ]

    assert len(set(components)) == 3
    assert replace(components[0], description="Different description") == components[0]
    assert {
        "products": [asdict(item) for item in products],
        "repositories": [asdict(item) for item in repositories],
        "components": [
            {key: value for key, value in asdict(item).items() if value is not None}
            for item in components
        ],
    } == fixture
