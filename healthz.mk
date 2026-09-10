# Shared healthcheck machinery. HEALTHZ_HELPERS holds the shell helpers the
# per-host healthz-<name> recipes (hosts/*/healthz.mk) share; it expands into
# a recipe as one logical shell line, so every line ends with a backslash and
# shell variables use $$. GNU Make 3.81 collapses backslash-newlines inside a
# define at read time and swallows the endef when the final line ends with a
# backslash, so the last line carries none.
# `make check-health` passes HEALTHZ_FAILFILE to each sub-make so failed
# checks aggregate into its summary; direct healthz-<name> invocations fall
# back to a private temporary file.
HEALTHZ_HOSTS :=

define HEALTHZ_HELPERS
	if [ -n "$(HEALTHZ_FAILFILE)" ]; then \
		failfile="$(HEALTHZ_FAILFILE)"; \
	else \
		failfile=$$(mktemp "$${TMPDIR:-/tmp}/homelab-healthz.XXXXXX"); \
		trap 'rm -f "$$failfile"' EXIT; \
	fi; \
	report() { \
		if [ "$$2" -eq 0 ]; then \
			printf '  ok    %s\n' "$$1"; \
		else \
			printf '  FAIL  %s\n' "$$1"; \
			printf '  FAIL  %s\n' "$$1" >> "$$failfile"; \
		fi; \
	}; \
	ssh_ok() { \
		ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$$1.home.arpa" true >/dev/null 2>&1; \
	}; \
	host_ip() { \
		ip=$$(dig +short +time=3 +tries=1 "$$1.home.arpa" A 2>/dev/null | head -n 1); \
		[ -n "$$ip" ] || ip=unresolved; \
		printf '%s' "$$ip"; \
	}; \
	docker_ready() { \
		if ! docker context inspect "$$1" >/dev/null 2>&1; then \
			report "docker context $$1 (run make init)" 1; \
			return 1; \
		fi; \
		if docker --context "$$1" version --format '{{.Server.Version}}' >/dev/null 2>&1; then \
			report "docker daemon" 0; \
			return 0; \
		fi; \
		report "docker daemon" 1; \
		return 1; \
	}; \
	check_containers() { \
		out=$$(docker --context "$$1" ps -a \
			--filter "label=com.docker.compose.project=$$2" \
			--filter "label=com.docker.compose.service=$$4" \
			--format '{{.Names}}|{{.State}}|{{.Status}}' 2>/dev/null); \
		if [ -z "$$out" ]; then \
			report "$$3/$$4: no container found" 1; \
			return; \
		fi; \
		printf '%s\n' "$$out" | while IFS='|' read -r cname cstate cstatus; do \
			rc=0; \
			[ "$$cstate" = "running" ] || rc=1; \
			if [ "$$5" = "true" ]; then \
				case "$$cstatus" in \
					*"(healthy)"*) : ;; \
					*) rc=1 ;; \
				esac; \
			fi; \
			if [ "$$rc" -eq 0 ]; then \
				case "$$cstatus" in \
					*"(healthy)"*) note="running, healthy" ;; \
					*) note="running" ;; \
				esac; \
				report "$$3/$$4 ($$cname): $$note" 0; \
			else \
				report "$$3/$$4 ($$cname): $$cstate $$cstatus" 1; \
			fi; \
		done; \
	}; \
	check_workload() { \
		dir="hosts/$$1/workloads/$$2"; \
		project=$$(yq -r '.name // ""' "$$dir/compose.yaml"); \
		[ -n "$$project" ] || project=$$2; \
		for svc in $$(yq -r '.services | keys | .[]' "$$dir/compose.yaml"); do \
			hc=$$(yq -r ".services[\"$$svc\"] | has(\"healthcheck\")" "$$dir/compose.yaml"); \
			check_containers "$$1" "$$project" "$$2" "$$svc" "$$hc"; \
		done; \
	}; \
	check_urls() { \
		dir="hosts/$$2/workloads/$$1"; \
		[ -f "$$dir/deploy.yml" ] || return 0; \
		for url in $$(yq -r '(.health_checks // [])[]' "$$dir/deploy.yml"); do \
			code=$$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "$$url" 2>/dev/null) || code=000; \
			case "$$code" in \
				2??|3??) report "$$1: $$url -> $$code" 0 ;; \
				*) report "$$1: $$url -> $$code" 1 ;; \
			esac; \
		done; \
	}; \
	healthz_docker_host() { \
		host=$$1; only="$(workload)"; \
		printf '== %s (%s) ==\n' "$$host" "$$(host_ip "$$host")"; \
		ssh_ok "$$host"; \
		report "ssh root@$$host.home.arpa" $$?; \
		docker_ok=0; \
		if docker_ready "$$host"; then \
			docker_ok=1; \
		else \
			echo "  skip  workload container checks (docker unreachable)"; \
		fi; \
		if [ -n "$$only" ]; then \
			[ "$$docker_ok" -eq 1 ] && check_workload "$$host" "$$only"; \
			check_urls "$$only" "$$host"; \
		else \
			for dir in "hosts/$$host/workloads"/*; do \
				[ -f "$$dir/compose.yaml" ] || continue; \
				name=$$(basename "$$dir"); \
				[ "$$docker_ok" -eq 1 ] && check_workload "$$host" "$$name"; \
				check_urls "$$name" "$$host"; \
			done; \
		fi; \
		[ ! -s "$$failfile" ]; \
	};
endef
