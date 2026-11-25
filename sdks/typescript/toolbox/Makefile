UNIT_TEST_FILES := $(filter %.test.ts,$(TEST_FILES))

.PHONY: fix lint lint-ci test build test-package

fix:
	@true

lint:
	bun run typecheck

lint-ci: lint

test:
	bun test $(UNIT_TEST_FILES)

build:
	bun run build

test-package: build
	bun test ./tests/*.check.ts
