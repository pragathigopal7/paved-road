PYTHON    ?= python3
TERRAFORM ?= terraform
PROMTOOL  ?= promtool
TF_DIRS   := bootstrap modules/network modules/eks modules/github-oidc platform

.PHONY: help install lint test test-python test-pulumi test-terraform promtool rules demo local-up local-down clean

help: ## Show targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: ## Install the CLI, dev tools and Pulumi test deps
	$(PYTHON) -m pip install -e ".[dev]" -r infra/pulumi/requirements.txt

lint: ## Ruff lint and format check
	ruff check . && ruff format --check .

test: lint test-python test-pulumi test-terraform promtool ## Everything CI runs

test-python: ## CLI, policy engine, scaffolder, MCP plugin, generated service
	$(PYTHON) -m pytest

test-pulumi: ## Pulumi program against the mock engine
	cd infra/pulumi && $(PYTHON) -m pytest -o addopts="" -q tests

test-terraform: ## fmt, validate and offline tests for every module
	$(TERRAFORM) -chdir=infra/terraform fmt -check -recursive
	@set -e; for d in $(TF_DIRS); do \
	  echo "== $$d"; \
	  $(TERRAFORM) -chdir=infra/terraform/$$d init -backend=false -input=false >/dev/null; \
	  $(TERRAFORM) -chdir=infra/terraform/$$d validate; \
	  if [ -d infra/terraform/$$d/tests ]; then $(TERRAFORM) -chdir=infra/terraform/$$d test; fi; \
	done

rules: ## Extract alert rule groups for promtool and local Prometheus
	$(PYTHON) scripts/extract_rules.py platform/prometheus/alerts.yaml build/prometheus-rules.yaml

promtool: rules ## Check alert rules and run their unit tests
	$(PROMTOOL) check rules build/prometheus-rules.yaml
	$(PROMTOOL) test rules platform/prometheus/tests/alerts_test.yaml

demo: ## Scaffold a service and run the policy gate on it and on a bad manifest
	rm -rf build/demo && paved new refunds-consumer --team payments --topic refunds --out build/demo
	paved check build/demo/deploy
	-paved check tests/fixtures/bad-deployment.yaml

local-up: rules ## Run Kafka, the example consumer, OTel, Jaeger, Prometheus
	docker compose -f local/docker-compose.yml up --build -d

local-down: ## Stop the local stack
	docker compose -f local/docker-compose.yml down -v

clean:
	rm -rf build .pytest_cache **/__pycache__
