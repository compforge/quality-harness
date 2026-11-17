.PHONY: lint test

lint:
	@for file in graph/*.js tabs/*.js tests/*.cjs; do node --check "$$file" || exit; done

test:
	node --test $(if $(TEST_FILES),$(TEST_FILES),tests/*.test.cjs)
