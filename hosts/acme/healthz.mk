# acme healthcheck — invoked by `make check-health` from the repository root.
# Docker host: SSH, Docker daemon, workload containers, and the HTTP health
# checks declared in each workload's deploy.yml.
HEALTHZ_HOSTS += acme

.PHONY: healthz-acme
healthz-acme:
	@set -u; \
	$(HEALTHZ_HELPERS) \
	healthz_docker_host "acme"
