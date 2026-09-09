SHELL := /bin/sh

AGE_IDENTITY ?= $(HOME)/.age/age.key

.PHONY: init hooks decrypt-secrets docker-contexts apply bootstrap help

init: hooks decrypt-secrets docker-contexts ## Prepare local secrets, Git hooks, and Docker contexts

hooks: ## Configure the repository Git hooks
	git config --local core.hooksPath hooks

decrypt-secrets: ## Decrypt the vault password and workload secret files
	@command -v age >/dev/null || { echo "age is required" >&2; exit 1; }
	@test -f "$(AGE_IDENTITY)" || { echo "age identity not found: $(AGE_IDENTITY)" >&2; exit 1; }
	@set -eu; \
	for encrypted in .vault-pass.age $$(find hosts -type f -path '*/workloads/*' -name '*.age' | sort); do \
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
	for context_host in acme ai media; do \
		endpoint="ssh://root@$$context_host.home.arpa"; \
		if docker context inspect "$$context_host" >/dev/null 2>&1; then \
			docker context update "$$context_host" --docker "host=$$endpoint" >/dev/null; \
		else \
			docker context create "$$context_host" --docker "host=$$endpoint" >/dev/null; \
		fi; \
	done

apply: ## Build and apply a workload; pass host=<name> workload=<name>|all
	@test -n "$(host)" || { echo "usage: make apply host=<host> workload=<workload>|all" >&2; exit 1; }
	@test -n "$(workload)" || { echo "usage: make apply host=<host> workload=<workload>|all" >&2; exit 1; }
	@test "$(host)" != "all" || { echo "host=all is not supported; select one host" >&2; exit 1; }
	@test -f "hosts/$(host)/bootstrap/site.yml" || { echo "unknown host: $(host)" >&2; exit 1; }
	@command -v yq >/dev/null || { echo "yq is required" >&2; exit 1; }
	@set -eu; \
	target="$(host)"; \
	workloads_dir="hosts/$$target/workloads"; \
	docker context inspect "$$target" >/dev/null 2>&1 || { echo "Docker context unavailable for $$target; run make init" >&2; exit 1; }; \
	apply_one() { \
		name="$$1"; \
		workload_dir="$$workloads_dir/$$name"; \
		test -f "$$workload_dir/compose.yaml" || { echo "unknown workload for $$target: $$name" >&2; exit 1; }; \
		test -f "$$workload_dir/deploy.yml" || { echo "missing deployment manifest: $$workload_dir/deploy.yml" >&2; exit 1; }; \
		copy_files=$$(yq -r '(.copy_files // [])[]' "$$workload_dir/deploy.yml"); \
		external_networks=$$(yq -r '(.external_networks // [])[]' "$$workload_dir/deploy.yml"); \
		copy_dir=""; \
		test -z "$$copy_files" || copy_dir="/opt/$$name"; \
		if [ -f "$$workload_dir/.env.age" ]; then \
			test -f "$$workload_dir/.env" || { echo "run make init before applying $$name: missing .env" >&2; exit 1; }; \
			env_args="--env-file $$workload_dir/.env"; \
		else \
			env_args=""; \
		fi; \
		for copy_file in $$copy_files; do \
			test -f "$$workload_dir/$$copy_file" || { echo "run make init before applying $$name: missing $$copy_file" >&2; exit 1; }; \
		done; \
		for network in $$external_networks; do \
			docker --context "$$target" network inspect "$$network" >/dev/null 2>&1 || docker --context "$$target" network create "$$network" >/dev/null; \
		done; \
		if [ -n "$$copy_dir" ]; then \
			ssh "root@$$target.home.arpa" "install -d -m 0700 '$$copy_dir'"; \
			for copy_file in $$copy_files; do \
				ssh "root@$$target.home.arpa" "install -d -m 0700 '$$copy_dir'/$$(dirname "$$copy_file")"; \
				scp -p "$$workload_dir/$$copy_file" "root@$$target.home.arpa:$$copy_dir/$$copy_file"; \
				ssh "root@$$target.home.arpa" "chmod 0400 '$$copy_dir'/$$copy_file"; \
			done; \
		fi; \
		COPY_DIR="$$copy_dir" docker --context "$$target" compose $$env_args --project-directory "$$workload_dir" -f "$$workload_dir/compose.yaml" up -d --build; \
	}; \
	if [ "$(workload)" = "all" ]; then \
		for workload_dir in "$$workloads_dir"/*; do \
			[ -f "$$workload_dir/compose.yaml" ] || continue; \
			name=$$(basename "$$workload_dir"); \
			[ "$$name" = "caddy" ] || apply_one "$$name"; \
		done; \
		[ ! -f "$$workloads_dir/caddy/compose.yaml" ] || apply_one caddy; \
	else \
		apply_one "$(workload)"; \
	fi

bootstrap: ## Configure a host; pass host=<name> [ansible_args="..."]
	@test -n "$(host)" || { echo "usage: make bootstrap host=<host>" >&2; exit 1; }
	@test -f "hosts/$(host)/bootstrap/site.yml" || { echo "unknown host: $(host)" >&2; exit 1; }
	@cd "hosts/$(host)/bootstrap" && ansible-playbook site.yml $(ansible_args)

help: ## Show available Make targets
	@awk 'BEGIN { FS = ":.*##"; printf "Usage: make <target> [host=<name>] [workload=<name>|all]\n\nTargets:\n" } /^[[:alnum:]_-]+:.*##/ { printf "  %-18s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
