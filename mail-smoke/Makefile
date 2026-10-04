.PHONY: build test test-real run
build:
	uv sync
test:
	uv run python -m pytest -q
test-real: build
	@test -f .env || (echo "Create .env from .env.example first"; exit 1)
	@LIVE_MAIL=1 uv run --env-file .env python -m pytest -q
run: test-real
