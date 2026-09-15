.PHONY: test lint fix build

CHECK_PATHS ?= $(if $(filter %.py,$(LINT_FILES)),$(filter %.py,$(LINT_FILES)),harness_toolbox)

test:
	uv run --project .. pytest -v harness_toolbox/tests $(TEST_FILES)

lint:
	uv run --project .. ruff check $(CHECK_PATHS)

fix:
	uv run --project .. ruff check --fix $(CHECK_PATHS)
	uv run --project .. ruff format $(CHECK_PATHS)

build:
	uv build --out-dir ../../../dist
