RUN := docker compose run --rm dev
WEB := docker compose run --rm --no-deps web

.PHONY: build sync test lint typecheck check shell web-install web-check web-test web-build web-dev image

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

# The named node_modules volume is created root-owned; hand it to the web user first.
web-install:
	docker compose run --rm --no-deps --user root web chown -R "$${I2N_UID:-1000}:$${I2N_GID:-1000}" /web/node_modules
	$(WEB) npm ci

web-check:
	$(WEB) npm run check

web-test:
	$(WEB) npx vitest run $(ARGS)

web-build:
	$(WEB) npm run build

web-dev:
	docker compose up web serve

image:
	docker build --target runtime -t icloud2nas:local .
