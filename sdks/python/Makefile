# Validation belongs to the leaf Components; see AGENTS.md.
.PHONY: build clean bump report-assets

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

report-assets:
	python3 common/hatch_build.py
