# tailgate healthcheck — invoked by `make check-health` from the repository root.
# Subnet router: SSH, tailscaled, the Tailscale backend state, and that the
# role's advertise routes are still configured. Routes are read from the
# tailscale role defaults so the check tracks the provisioned configuration.
HEALTHZ_HOSTS += tailgate

.PHONY: healthz-tailgate
healthz-tailgate:
	@set -u; \
	$(HEALTHZ_HELPERS) \
	printf '== %s (%s) ==\n' tailgate "$$(host_ip tailgate)"; \
	ssh_ok tailgate; \
	report "ssh root@tailgate.home.arpa" $$?; \
	ssh -o BatchMode=yes -o ConnectTimeout=5 root@tailgate.home.arpa \
		'systemctl is-active --quiet tailscaled' >/dev/null 2>&1; \
	report "tailscaled service" $$?; \
	ssh -o BatchMode=yes -o ConnectTimeout=5 root@tailgate.home.arpa \
		'tailscale status --json | /usr/bin/python3 -c "import json,sys; s=json.load(sys.stdin); sys.exit(0 if s[\"BackendState\"]==\"Running\" else 1)"' >/dev/null 2>&1; \
	report "tailscale backend state Running" $$?; \
	routes=$$(yq -r '.tailscale_advertise_routes[]' hosts/tailgate/ansible/roles/tailscale/defaults/main.yml); \
	ssh -o BatchMode=yes -o ConnectTimeout=5 root@tailgate.home.arpa \
		'tailscale debug prefs | /usr/bin/python3 -c "import json,sys; p=json.load(sys.stdin); sys.exit(0 if set(sys.argv[1:]) <= set(p.get(\"AdvertiseRoutes\") or []) else 1)"' $$routes >/dev/null 2>&1; \
	rc=$$?; \
	report "subnet routes advertised ($$(echo $$routes))" $$rc; \
	[ ! -s "$$failfile" ]
