# ai healthcheck — invoked by `make check-health` from the repository root.
# Docker host: SSH, Docker daemon, workload containers, and the HTTP health
# checks declared in each workload's deploy.yml.
HEALTHZ_HOSTS += ai

.PHONY: healthz-ai
healthz-ai:
	@set -u; \
	$(HEALTHZ_HELPERS) \
	healthz_docker_host "ai"
