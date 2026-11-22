.PHONY: install lint format test check verify-data ref-build lookup demo

install:
	uv sync

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy src tests

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

test:
	uv run pytest

check: lint test

verify-data:
	uv run billauditor verify-data

# Build data/processed/reference.duckdb from the checksummed CMS files in data/raw.
ref-build:
	uv run billauditor ref-build

# Example: make lookup CODE=99213 ARGS="--locality MA-01"
lookup:
	uv run billauditor lookup $(CODE) $(ARGS)

# Run the placeholder pipeline on any file and print where its trace was written.
demo:
	uv run billauditor run $(or $(BILL),tests/fixtures/placeholder.pdf)
