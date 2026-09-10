SHELL := /bin/sh

AGE_IDENTITY ?= $(HOME)/.age/age.key

.PHONY: init hooks decrypt-secrets docker-contexts deploy bootstrap check-health help

# Healthcheck machinery lives in healthz.mk (shared shell helpers);
# each host adds its own checks in hosts/<name>/healthz.mk, which appends
# to HEALTHZ_HOSTS and defines a healthz-<name> target invoked by `make check-health`.
include healthz.mk $(wildcard hosts/*/healthz.mk)


init: hooks decrypt-secrets docker-contexts ## Prepare local secrets, Git hooks, and Docker contexts

hooks: ## Configure the repository Git hooks
	git config --local core.hooksPath hooks

decrypt-secrets: ## Decrypt Ansible and workload secret files
	@command -v age >/dev/null || { echo "age is required" >&2; exit 1; }
	@test -f "$(AGE_IDENTITY)" || { echo "age identity not found: $(AGE_IDENTITY)" >&2; exit 1; }
	@set -eu; \
	for encrypted in $$(find hosts -type f -name '*.age' | sort); do \
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

deploy: ## Build and deploy a workload; pass host=<name> workload=<name>|all [force_recreate=true]
	@test -n "$(host)" || { echo "usage: make deploy host=<host> workload=<workload>|all [force_recreate=true]" >&2; exit 1; }
	@test -n "$(workload)" || { echo "usage: make deploy host=<host> workload=<workload>|all [force_recreate=true]" >&2; exit 1; }
	@test "$(host)" != "all" || { echo "host=all is not supported; select one host" >&2; exit 1; }
	@test -f "hosts/$(host)/ansible/site.yml" || { echo "unknown host: $(host)" >&2; exit 1; }
	@command -v yq >/dev/null || { echo "yq is required" >&2; exit 1; }
	@set -eu; \
	target="$(host)"; \
	workloads_dir="hosts/$$target/workloads"; \
	docker context inspect "$$target" >/dev/null 2>&1 || { echo "Docker context unavailable for $$target; run make init" >&2; exit 1; }; \
	case "$(force_recreate)" in \
		1|t|T|true|TRUE|True) force_args="--force-recreate" ;; \
		""|0|f|F|false|FALSE|False) force_args="" ;; \
		*) echo "invalid force_recreate: $(force_recreate) (accepts the same booleans as docker compose --force-recreate)" >&2; exit 1 ;; \
	esac; \
	deploy_one() { \
		name="$$1"; \
		workload_dir="$$workloads_dir/$$name"; \
		test -f "$$workload_dir/compose.yaml" || { echo "unknown workload for $$target: $$name" >&2; exit 1; }; \
		test -f "$$workload_dir/deploy.yml" || { echo "missing deployment manifest: $$workload_dir/deploy.yml" >&2; exit 1; }; \
		copy_files=$$(yq -r '(.copy_files // [])[]' "$$workload_dir/deploy.yml"); \
		external_networks=$$(yq -r '(.external_networks // [])[]' "$$workload_dir/deploy.yml"); \
		copy_dir=""; \
		test -z "$$copy_files" || copy_dir="/opt/$$name"; \
		if [ -f "$$workload_dir/.env.age" ]; then \
			test -f "$$workload_dir/.env" || { echo "run make init before deploying $$name: missing .env" >&2; exit 1; }; \
			env_args="--env-file $$workload_dir/.env"; \
		else \
			env_args=""; \
		fi; \
		for copy_file in $$copy_files; do \
			test -f "$$workload_dir/$$copy_file" || { echo "run make init before deploying $$name: missing $$copy_file" >&2; exit 1; }; \
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
		COPY_DIR="$$copy_dir" docker --context "$$target" compose $$env_args --project-directory "$$workload_dir" -f "$$workload_dir/compose.yaml" up -d --build $$force_args; \
	}; \
	if [ "$(workload)" = "all" ]; then \
		for workload_dir in "$$workloads_dir"/*; do \
			[ -f "$$workload_dir/compose.yaml" ] || continue; \
			name=$$(basename "$$workload_dir"); \
			[ "$$name" = "caddy" ] || deploy_one "$$name"; \
		done; \
		[ ! -f "$$workloads_dir/caddy/compose.yaml" ] || deploy_one caddy; \
	else \
		deploy_one "$(workload)"; \
	fi

check-health: ## Healthcheck hosts and workloads; pass [host=<name>|all] [workload=<name>]
	@command -v yq >/dev/null || { echo "yq is required" >&2; exit 1; }
	@command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
	@command -v dig >/dev/null || { echo "dig is required" >&2; exit 1; }
	@set -u; \
	sel_host="$(host)"; sel_wl="$(workload)"; \
	[ -n "$$sel_host" ] || sel_host=all; \
	if [ "$$sel_host" = "all" ]; then \
		[ -z "$$sel_wl" ] || { echo "make check-health: workload= requires a specific host" >&2; exit 1; }; \
		host_list="$(HEALTHZ_HOSTS)"; \
	else \
		case " $(HEALTHZ_HOSTS) " in \
			*" $$sel_host "*) : ;; \
			*) echo "unknown host: $$sel_host" >&2; exit 1 ;; \
		esac; \
		if [ -n "$$sel_wl" ]; then \
			[ -f "hosts/$$sel_host/workloads/$$sel_wl/compose.yaml" ] || { echo "unknown workload for $$sel_host: $$sel_wl" >&2; exit 1; }; \
		fi; \
		host_list=$$sel_host; \
	fi; \
	faildir=$$(mktemp -d "$${TMPDIR:-/tmp}/homelab-healthz.XXXXXX"); \
	trap 'rm -rf "$$faildir"' EXIT; \
	rc=0; \
	for h in $$host_list; do \
		$(MAKE) --no-print-directory "healthz-$$h" HEALTHZ_FAILFILE="$$faildir/$$h" || rc=1; \
	done; \
	if [ "$$rc" -eq 0 ]; then \
		echo "check-health: all checks passed"; \
	else \
		count=$$(cat "$$faildir"/* 2>/dev/null | wc -l | tr -d ' '); \
		bad_hosts=$$(for h in $$host_list; do [ -s "$$faildir/$$h" ] && printf '%s, ' "$$h"; done); \
		if [ -n "$$bad_hosts" ]; then \
			[ "$$count" -eq 1 ] && s= || s=s; \
			echo "check-health: $$count check$$s failed on $${bad_hosts%, }" >&2; \
		else \
			echo "check-health: failed (see errors above)" >&2; \
		fi; \
		exit 1; \
	fi

bootstrap: ## Configure a host; pass host=<name> [ansible_args="..."]
	@test -n "$(host)" || { echo "usage: make bootstrap host=<host>" >&2; exit 1; }
	@test -f "hosts/$(host)/ansible/site.yml" || { echo "unknown host: $(host)" >&2; exit 1; }
	@cd "hosts/$(host)/ansible" && ansible-playbook site.yml $(ansible_args)

help: ## Show available Make targets
	@awk 'BEGIN { FS = ":.*##"; printf "Usage: make <target> [host=<name>] [workload=<name>|all] [force_recreate=true]\n\nTargets:\n" } /^[[:alnum:]_-]+:.*##/ { printf "  %-18s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
