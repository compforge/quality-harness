CHECK_PATHS ?= harness_common/ toolbox/harness_toolbox/ e2e_harness/ eval_harness/ perf_harness/ trace_harness/ trajectory_harness/

FIX_PATHS ?= $(CHECK_PATHS)

.PHONY: test lint fix build clean bump

test:
	uv run pytest -v $(TEST_FILES)

lint:
	uv run ruff check $(CHECK_PATHS)

fix:
	uv run ruff check --fix $(FIX_PATHS)
	uv run ruff format $(FIX_PATHS)

build:
	uv build --all-packages --out-dir ../../dist

clean:
	rm -rf dist build *.egg-info

bump:
	@current=$$(grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)"/\1/'); \
	major=$$(echo $$current | cut -d. -f1); \
	minor=$$(echo $$current | cut -d. -f2); \
	patch=$$(echo $$current | cut -d. -f3); \
	next="$$major.$$minor.$$((patch + 1))"; \
	sed -i '' "s/^version = \"$$current\"/version = \"$$next\"/" pyproject.toml; \
	uv lock; \
	echo "bumped $$current → $$next"
