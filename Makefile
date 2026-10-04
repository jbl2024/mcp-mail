.PHONY: build run test release

build:
	uv build

run:
	uv run mcp-mail

test:
	uv run python -m pytest

release:
	sh scripts/release.sh
