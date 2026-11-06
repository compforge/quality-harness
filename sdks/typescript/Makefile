PACKAGES := common toolbox perf-harness trace-harness

.PHONY: deps fix lint lint-ci test build

deps:
	bun install --frozen-lockfile
	$(MAKE) -C common build
	cd toolbox && bun install --frozen-lockfile
	cd trace-harness && bun install --frozen-lockfile

# Workspace checks always cover every package; file paths are package-relative below.
fix lint test build:
	@for package in $(PACKAGES); do \
		$(MAKE) -C "$$package" $@ LINT_FILES= TEST_FILES= || exit $$?; \
	done

lint-ci: lint
