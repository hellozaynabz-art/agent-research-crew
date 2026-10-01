.PHONY: setup brief test lint clean

setup:  ## install dependencies into .venv
	uv sync

brief:  ## research a topic: make brief T="The market for AI coding assistants"
	uv run research-crew "$(T)"

test:  ## offline tests (no API key needed)
	uv run pytest -q

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

clean:
	rm -rf .pytest_cache .ruff_cache
