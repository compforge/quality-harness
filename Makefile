PART ?= patch

.PHONY: help bump

help:
	@echo "make bump [PART=patch|minor|major]"

bump:
	python3 scripts/bump_version.py --part "$(PART)"
