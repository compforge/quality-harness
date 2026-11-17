# Each Component checks only its own files; empty selections mean its full suite.
CHECK_PATHS ?= $(if $(filter %.py,$(LINT_FILES)),$(filter %.py,$(LINT_FILES)),.)
TEST_PATHS = $(if $(strip $(TEST_FILES)),$(TEST_FILES),tests)

.PHONY: test lint format fix

test:
	uv run --project .. pytest -v $(TEST_PATHS)

lint:
	uv run --project .. ruff check $(CHECK_PATHS)

format:
	uv run --project .. ruff check --fix $(CHECK_PATHS)
	uv run --project .. ruff format $(CHECK_PATHS)

fix: format
