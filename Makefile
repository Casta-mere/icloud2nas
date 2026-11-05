# Extra `docker compose run` flags. CI has no TTY and passes DC_FLAGS=-T; empty locally.
DC_FLAGS ?=
DC_RUN := docker compose run --rm$(if $(DC_FLAGS), $(DC_FLAGS))
RUN := $(DC_RUN) dev
WEB := $(DC_RUN) --no-deps web

.PHONY: build sync test lint typecheck check bump shell web-install web-check web-test web-build web-dev image

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

# Set every version field and regenerate uv.lock. PART=patch|minor|major, or an exact X.Y.Z.
bump:
	$(if $(PART),,$(error usage: make bump PART=patch|minor|major|X.Y.Z))
	$(RUN) uv run python scripts/bump_version.py set '$(PART)'
	$(RUN) uv lock
	$(RUN) uv run python scripts/bump_version.py check

shell:
	$(RUN) bash

# The named node_modules volume is created root-owned; hand it to the web user first.
web-install:
	$(DC_RUN) --no-deps --user root web chown -R "$${I2N_UID:-1000}:$${I2N_GID:-1000}" /web/node_modules
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
