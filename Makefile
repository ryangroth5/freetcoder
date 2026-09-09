COMPOSE ?= docker compose
.DEFAULT_GOAL := help

help:  ## Show this help
	@grep -hE '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | expand -t22

build:      ## Build the dev, web and library images (compose tags them separately)
	$(COMPOSE) build dev web library
library:    ## Run just the question library service on :8090
	$(COMPOSE) up library
dev:        ## Run library + backend (8081) + vite (5173) with hot reload
	$(COMPOSE) up library dev web
prod:       ## Build and run the shippable single-port container
	$(COMPOSE) --profile prod up --build prod
shell:      ## Interactive shell in the dev container
	$(COMPOSE) run --rm dev bash

test:       ## Backend + library tests -- MUST run in-container (rlimits differ on macOS)
	$(COMPOSE) run --rm dev sh -c 'cd /srv/app/backend && pytest -q tests'
	$(COMPOSE) run --rm dev sh -c 'cd /srv/app && PYTHONPATH=/srv/app pytest -q -c backend/pyproject.toml library/tests'
lint:       ## ruff + mypy, both services
	$(COMPOSE) run --rm dev sh -c 'cd /srv/app/backend && ruff check . && mypy freetcoder'
	$(COMPOSE) run --rm dev sh -c 'cd /srv/app && PYTHONPATH=/srv/app ruff check library && mypy library'
web-lint:   ## Frontend typecheck
	$(COMPOSE) run --rm --no-deps web sh -c 'cd /srv/app/frontend && npx tsc --noEmit'
web-test:   ## Frontend unit tests (vitest); the browser suites are e2e/
	$(COMPOSE) run --rm --no-deps web sh -c 'cd /srv/app/frontend && npx vitest run'
e2e:        ## Playwright against the dev stack (needs FREETCODER_FAKE_LLM=1)
	$(COMPOSE) exec web sh -c 'cd /srv/app/frontend && npx playwright test'
e2e-prod:   ## Playwright against the built production image
	$(COMPOSE) exec web sh -c 'cd /srv/app/frontend && E2E_BASE_URL=http://prod:8080 npx playwright test'
check: lint test web-lint web-test  ## Everything CI would run

clean:      ## Remove containers and volumes
	$(COMPOSE) down -v --remove-orphans

.PHONY: help build dev prod library shell test lint web-lint web-test e2e e2e-prod check clean
