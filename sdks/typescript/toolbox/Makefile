.PHONY: fix lint lint-ci test build

fix:
	@true

lint:
	bun run typecheck

lint-ci: lint

test: build
	bun test $(TEST_FILES)

build:
	bun run build
