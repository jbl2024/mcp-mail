.PHONY: install build run dev test release

install:
	sh scripts/install.sh

build:
	uv build

run:
	sh scripts/run.sh

dev:
	uv run mcp-mail

test:
	uv run python -m pytest

release:
	sh scripts/release.sh
