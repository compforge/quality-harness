.PHONY: test lint fix build

test:
	uv run --project .. pytest -v harness_toolbox/tests $(TEST_FILES)

lint:
	uv run --project .. ruff check harness_toolbox

fix:
	uv run --project .. ruff check --fix harness_toolbox
	uv run --project .. ruff format harness_toolbox

build:
	uv build --out-dir ../../../dist
