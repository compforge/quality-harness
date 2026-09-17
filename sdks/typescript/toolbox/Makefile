.PHONY: fix lint lint-ci test build common-build

fix:
	@true

common-build:
	$(MAKE) -C ../common build

lint: common-build
	bun run typecheck

lint-ci: lint

test: build
	bun test $(TEST_FILES)

build: common-build
	bun run build
