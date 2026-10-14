RUN := docker compose run --rm dev

.PHONY: build sync test lint typecheck check shell

build:
	docker compose build dev

sync:
	$(RUN) uv sync

test:
	$(RUN) uv run pytest $(ARGS)

lint:
	$(RUN) uv run ruff check .

typecheck:
	$(RUN) uv run mypy

check: lint typecheck test

shell:
	$(RUN) bash
