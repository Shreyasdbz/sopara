UV_BIN ?= uv
BUN_BIN ?= bun
TERRAFORM_BIN ?= terraform
PYTHON_BIN ?= python3
DOCKER_BIN ?= docker
FOUNDATION_IMAGE ?= sopara-foundation:wp4-local

.PHONY: help toolchain format format-check lint typecheck test-unit test-property test-integration test-contract openapi-check architecture-check web-check terraform-check license-check sbom container-check check

help:
	@$(PYTHON_BIN) -c 'print("make toolchain | format | format-check | lint | typecheck | test-unit | test-property | test-integration | test-contract | openapi-check | architecture-check | web-check | terraform-check | license-check | sbom | container-check | check")'

toolchain:
	@$(PYTHON_BIN) scripts/check_toolchain.py --uv "$(UV_BIN)" --bun "$(BUN_BIN)" --terraform "$(TERRAFORM_BIN)"

format:
	@$(UV_BIN) run --locked ruff format src tests scripts
	@cd web && $(BUN_BIN) run format
	@$(TERRAFORM_BIN) -chdir=infra/terraform fmt -recursive

format-check:
	@$(UV_BIN) run --locked ruff format --check src tests scripts
	@cd web && $(BUN_BIN) run format:check
	@$(TERRAFORM_BIN) -chdir=infra/terraform fmt -check -recursive

lint:
	@$(UV_BIN) run --locked ruff check src tests scripts
	@cd web && $(BUN_BIN) run lint

typecheck:
	@$(UV_BIN) run --locked pyright
	@cd web && $(BUN_BIN) run typecheck

test-unit:
	@$(UV_BIN) run --locked pytest tests/foundation tests/unit
	@cd web && $(BUN_BIN) run test

test-property:
	@$(UV_BIN) run --locked pytest tests/property

test-integration:
	@DOCKER_BIN="$(DOCKER_BIN)" UV_BIN="$(UV_BIN)" sh scripts/test_integration.sh

test-contract:
	@$(UV_BIN) run --locked pytest tests/contract

openapi-check:
	@$(UV_BIN) run --locked python scripts/generate_openapi.py --check
	@cd web && $(BUN_BIN) run api:check

architecture-check:
	@$(UV_BIN) run --locked lint-imports
	@$(UV_BIN) run --locked python scripts/check_repository.py
	@$(UV_BIN) run --locked python scripts/check_frontend_architecture.py

web-check:
	@cd web && $(BUN_BIN) install --frozen-lockfile
	@cd web && $(BUN_BIN) run check
	@cd web && $(BUN_BIN) run build

terraform-check:
	@$(TERRAFORM_BIN) -chdir=infra/terraform init -backend=false -input=false
	@$(TERRAFORM_BIN) -chdir=infra/terraform validate

license-check:
	@$(UV_BIN) run --locked python scripts/check_licenses.py

sbom:
	@mkdir -p build/sbom
	@$(UV_BIN) export --quiet --locked --format cyclonedx1.5 --no-dev --output-file build/sbom/python.cdx.json
	@cd web && $(BUN_BIN) pm ls --all > ../build/sbom/web-dependency-tree.txt

container-check:
	@$(DOCKER_BIN) build --tag $(FOUNDATION_IMAGE) .
	@$(DOCKER_BIN) run --rm --entrypoint sh $(FOUNDATION_IMAGE) -ceu 'python --version; python -c "from sopara.domain.instruments import NES; from sopara.api.app import create_app; assert str(NES.tick_value.value) == \"0.250\""; sopara --help >/dev/null; test -f /app/static/_shell.html; test -d /app/static/assets; test -z "$$(command -v node || true)"; test -z "$$(command -v bun || true)"; test -z "$$(command -v bunx || true)"; test ! -e /web; echo "container runtime checks passed"'

check: toolchain format-check lint typecheck test-unit test-property test-integration test-contract openapi-check architecture-check web-check terraform-check license-check sbom
