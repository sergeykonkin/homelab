SHELL := /bin/sh

AGE_IDENTITY ?= $(HOME)/.age/age.key

.PHONY: init hooks decrypt-secrets docker-contexts apply bootstrap help

init: hooks decrypt-secrets docker-contexts ## Prepare local secrets, Git hooks, and Docker contexts

hooks: ## Configure the repository Git hooks
	git config --local core.hooksPath hooks

decrypt-secrets: ## Decrypt the vault password and workload environments
	@command -v age >/dev/null || { echo "age is required" >&2; exit 1; }
	@test -f "$(AGE_IDENTITY)" || { echo "age identity not found: $(AGE_IDENTITY)" >&2; exit 1; }
	@set -eu; \
	for encrypted in .vault-pass.age workloads/*/.env.age; do \
		[ -f "$$encrypted" ] || continue; \
		target=$${encrypted%.age}; \
		temporary=$$(mktemp "$${target}.XXXXXX"); \
		trap 'rm -f "$$temporary"' EXIT HUP INT TERM; \
		chmod 600 "$$temporary"; \
		age --decrypt --identity "$(AGE_IDENTITY)" --output "$$temporary" "$$encrypted"; \
		if [ -f "$$target" ] && cmp -s "$$temporary" "$$target"; then \
			rm -f "$$temporary"; \
		else \
			mv "$$temporary" "$$target"; \
		fi; \
		chmod 600 "$$target"; \
		trap - EXIT HUP INT TERM; \
	done

docker-contexts: ## Create or update Docker contexts for managed Docker hosts
	@set -eu; \
	for context_host in ai media; do \
		endpoint="ssh://root@$$context_host.home.arpa"; \
		if docker context inspect "$$context_host" >/dev/null 2>&1; then \
			docker context update "$$context_host" --docker "host=$$endpoint" >/dev/null; \
		else \
			docker context create "$$context_host" --docker "host=$$endpoint" >/dev/null; \
		fi; \
	done

apply: ## Build and apply a workload; pass workload=<name> [host=<name>|all]
	@test -n "$(workload)" || { echo "usage: make apply workload=<workload> [host=<host>|all]" >&2; exit 1; }
	@test -f "workloads/$(workload)/compose.yaml" || { echo "unknown workload: $(workload)" >&2; exit 1; }
	@test -f "workloads/$(workload)/.env" || { echo "run make init before applying $(workload)" >&2; exit 1; }
	@command -v yq >/dev/null || { echo "yq is required" >&2; exit 1; }
	@set -eu; \
	workload_dir="workloads/$(workload)"; \
	allowed_hosts=$$(WORKLOAD_NAME="$(workload)" yq -r '(.workloads[strenv(WORKLOAD_NAME)].allowed_hosts // [])[]' workloads.yml | tr '\n' ' '); \
	test -n "$$allowed_hosts" || { echo "no deployment hosts configured for $(workload)" >&2; exit 1; }; \
	if [ -z "$(host)" ]; then \
		set -- $$allowed_hosts; \
		[ "$$#" -eq 1 ] || { echo "$(workload) has multiple allowed hosts; pass host=<host> or host=all (allowed: $$allowed_hosts)" >&2; exit 1; }; \
		targets="$$allowed_hosts"; \
	elif [ "$(host)" = "all" ]; then \
		targets="$$allowed_hosts"; \
	else \
		printf '%s\\n' "$$allowed_hosts" | tr ' ' '\\n' | grep -Fx "$(host)" >/dev/null || { echo "$(workload) cannot be deployed to $(host); allowed hosts: $$allowed_hosts" >&2; exit 1; }; \
		targets="$(host)"; \
	fi; \
	for target in $$targets; do \
		test -f "hosts/$$target/site.yml" || { echo "unknown host: $$target" >&2; exit 1; }; \
		docker context inspect "$$target" >/dev/null 2>&1 || { echo "Docker context unavailable for $$target; run make init" >&2; exit 1; }; \
	done; \
	for target in $$targets; do \
		docker --context "$$target" compose --env-file "$$workload_dir/.env" --project-directory "$$workload_dir" -f "$$workload_dir/compose.yaml" up -d --build; \
	done

bootstrap: ## Configure a host; pass host=<name> [ansible_args="..."]
	@test -n "$(host)" || { echo "usage: make bootstrap host=<host>" >&2; exit 1; }
	@test -f "hosts/$(host)/site.yml" || { echo "unknown host: $(host)" >&2; exit 1; }
	@cd "hosts/$(host)" && ansible-playbook site.yml $(ansible_args)

help: ## Show available Make targets
	@awk 'BEGIN { FS = ":.*##"; printf "Usage: make <target> [workload=<name>] [host=<name>|all]\n\nTargets:\n" } /^[[:alnum:]_-]+:.*##/ { printf "  %-18s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
