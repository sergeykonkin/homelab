SHELL := /bin/sh

AGE_IDENTITY ?= $(HOME)/.age/age.key
WORKLOAD ?= $(workload)

.PHONY: init hooks decrypt-secrets docker-contexts apply ansible help

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

apply: ## Build and apply a workload; pass WORKLOAD=<name>
	@test -n "$(WORKLOAD)" || { echo "usage: make apply WORKLOAD=<workload>" >&2; exit 1; }
	@test -f "workloads/$(WORKLOAD)/compose.yaml" || { echo "unknown workload: $(WORKLOAD)" >&2; exit 1; }
	@test -f "workloads/$(WORKLOAD)/.env" || { echo "run make init before applying $(WORKLOAD)" >&2; exit 1; }
	@set -eu; \
	workload_dir="workloads/$(WORKLOAD)"; \
	context=$$(sed -nE 's/^CONTEXT[[:space:]]*:?[[:space:]]*=[[:space:]]*//p' "$$workload_dir/workload.mk"); \
	test -n "$$context" || { echo "missing CONTEXT in $$workload_dir/workload.mk" >&2; exit 1; }; \
	docker --context "$$context" compose --env-file "$$workload_dir/.env" --project-directory "$$workload_dir" -f "$$workload_dir/compose.yaml" up -d --build

ansible: ## Run a playbook; pass host=<name> playbook=<bootstrap|site>
	@test -n "$(host)" || { echo "usage: make ansible host=<host> playbook=<bootstrap|site>" >&2; exit 1; }
	@test "$(playbook)" = bootstrap || test "$(playbook)" = site || { echo "playbook must be bootstrap or site" >&2; exit 1; }
	@test -f "hosts/$(host)/$(playbook).yml" || { echo "unknown host: $(host)" >&2; exit 1; }
	@cd "hosts/$(host)" && ansible-playbook "$(playbook).yml"

help: ## Show available Make targets
	@awk 'BEGIN { FS = ":.*##"; printf "Usage: make <target> [WORKLOAD=<name>]\n\nTargets:\n" } /^[[:alnum:]_-]+:.*##/ { printf "  %-18s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
