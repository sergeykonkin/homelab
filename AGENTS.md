# Repository guide

This repo is the Ansible source of truth for four FriendlyElec NanoPi hosts:
three Zero2 boards and one R6S with 64 GB eMMC (Debian Trixie, arm64).
Prefer executable configuration when documentation disagrees.
`CLAUDE.md` is a relative symlink to this file; keep one source of instructions.

An unmanaged x86_64 host, `optiplex.home.arpa` (Dell, Debian),
also lives on the network in the DMZ. This repo does **not** provision it; it
runs services configured manually on the box.

## Documentation

README files are human-facing overviews with concise commands. Keep detailed
implementation constraints, agent instructions, and operational invariants in
this file. The TLS ingress design (per-host Caddy, ACME-DNS gateway, DNS-01)
lives in [`docs/tls-ingress.md`](docs/tls-ingress.md).

## Layout and conventions

| Path | Responsibility |
| --- | --- |
| `hosts/tailgate/` | `tailgate.home.arpa`: Tailscale subnet router only |
| `hosts/ai/` | `ai.home.arpa`: Docker, LiteLLM, PostgreSQL, Token Factory model sync |
| `hosts/media/` | `media.home.arpa`: R6S, SD-to-eMMC OS installation and Docker only |
| `hosts/acme/` | `acme.home.arpa`: Docker host for the ACME-DNS gateway workload |
| `hosts/<name>/ansible/` | Host-specific Ansible project |
| `hosts/<name>/workloads/` | Compose projects deployed to that host |
| `hosts/<name>/healthz.mk` | Host healthcheck target included by the root Makefile |
| `healthz.mk` | Shell helpers shared by the per-host healthcheck targets |
| `shared_roles/bootstrap/` | Passwords, root SSH key, hostname, apt upgrade, RAM logs, SSH hardening, final reboot |
| `shared_roles/docker/` | Docker CE/Compose installation and fuse-overlayfs configuration |
| `docs/` | Design documentation |

- Each host's `ansible/` directory is an independent Ansible project containing
  `ansible.cfg`, `inventory.ini`, `site.yml`, local `roles/`, and `secrets/`.
  There is no root inventory or root playbook. Run Ansible **inside
  `hosts/<name>/ansible/`** so its config resolves `../../../shared_roles:./roles`
  correctly.
- Each `hosts/<name>/workloads/<workload>/` directory is an independent Compose
  project. Its `deploy.yml` declares copied files, external Docker networks,
  and the HTTP `health_checks` URLs probed by `make check-health`.
  Workload placement is defined by its host directory.
- Keep new roles local until a second host needs them, then move them to
  `shared_roles/`; playbooks reference role names. For a new host, follow the existing
  project layout and update the root host table and host README.
- Inventory groups use `<name>_hosts` to avoid host/group name collisions.
  Steady-state access is root over SSH keys; Python is `/usr/bin/python3`.
- Follow existing YAML style: two-space indentation, named tasks, fully qualified
  module names, quoted file modes, and role-prefixed variables.
  Put tunables in role defaults; existing bootstrap settings/key live in
  `shared_roles/bootstrap/vars/main.yml` (higher precedence than defaults).
- Keep repeatable setup convergent, use handlers for service configuration, and
  give command/shell tasks deliberate change/failure reporting. Update README
  overviews, commands, and secret examples when changing their interfaces.
- Write documentation and comments as descriptions of the current state. Omit
  change history and wording such as "now" or "previously" that narrates edits.
- Commit and push changes only when explicitly asked to deliver, ship, submit,
  or commit and push them. Push directly to `main` in this repo; do not create
  feature branches or pull requests.

## Commands and validation

Use the full `ansible` distribution with ansible-core >= 2.21
(`brew install ansible`); `ansible-lint` is optional. There is no CI,
dependency manifest, or top-level build command. The ACME-DNS gateway has a
standard-library Python unit test suite under its workload directory.

Run `make init` from the repo root to configure Git's local `core.hooksPath` as
`hooks`. The Python 3 pre-commit hook requires an ASCII-armored age header on
Ansible and workload `.age` files. It rejects staged plaintext secret files.

```sh
# Run from each affected Ansible directory; shared role changes affect all hosts.
cd hosts/ai/ansible           # or the corresponding directory for another host
ansible-playbook --syntax-check site.yml
ansible-lint site.yml  # if installed

# Live provisioning, when deployment is part of the task:
ansible-playbook site.yml
```

Syntax checks use the plaintext files produced by `make init` without contacting
the hosts. They do not verify runtime behavior. `--check` is not a complete deployment
simulation: command tasks, password-hash results, and generated files depend on
real execution. Do not run live provisioning merely to validate repository edits.
`make check-health [host=<name>|all] [workload=<name>]` is a read-only healthcheck of
live hosts and workloads; on failure it reports the failed checks' count and
hosts and exits non-zero.
Its per-host logic lives in `hosts/<name>/healthz.mk` (included by the root
Makefile), with shared shell helpers in the root `healthz.mk`
`HEALTHZ_HELPERS` define.
For LiteLLM model sync edits, run `python3 -m unittest discover -s
hosts/ai/workloads/litellm/sync -p '*_test.py' -v`, validate Python syntax, and
validate Compose with the workload's `.env.example`. Unit tests must use mocks
for HTTP and timing; do not contact Token Factory or live LiteLLM for validation.
For ACME-DNS gateway edits, run its Python unit tests, validate the Compose
configuration with the example runtime values, and use `sh -n` for its host
scripts.
Finish with `git diff --check` and review the changed files.

## Secrets

- Ansible and workload `.age` files are tracked ASCII-armored age ciphertext.
  Their counterparts without `.age` are ignored plaintext files produced by
  `make init`. Run `make encrypt-secrets` to re-encrypt edited plaintext with
  `age --armor` and the public key derived from `~/.age/age.key`; it rewrites
  only `.age` files whose decrypted content differs.
- The Ansible `.example` files define the schema with placeholders. Ansible host
  secrets contain `bootstrap_root_password` and `bootstrap_pi_password`; Tailgate
  also has `tailscale_auth_key`. Preserve `no_log: true` on secret-bearing tasks
  and keep generated credentials and config out of Git.

## Bootstrap and Docker invariants

- `site.yml` connects with root SSH-key authentication when available; otherwise it
  connects as **`pi:pi`**, becomes root with sudo, changes both passwords, and
  switches the remaining sudo tasks to the new pi password. Password hashes are
  generated on-target with `openssl passwd -stdin`, so the passwords never
  appear in process arguments.
- Install the configured root public key before any sshd restart. Preserve
  the `00-homelab-hardening.conf` drop-in (root login key-only, password auth
  left enabled), `/run/sshd` creation plus `sshd -t` before restarting SSH,
  and handler flush before reboot. Reboot reconnects as root using public-key
  authentication. Ensure the control machine has the private key matching
  `bootstrap_pubkey` before provisioning.
- `site.yml` applies access configuration, host roles, and a final reboot in one
  run. Per-host configs disable SSH host-key checking.
- NanoPi's root filesystem is overlayfs: Docker needs **fuse-overlayfs**, since
  `overlay2` cannot nest on it. Flush the Docker restart handler before app roles.
  Docker and Tailscale apt URLs derive distribution/release from gathered facts;
  Docker's repository architecture is explicitly arm64. Preserve deb822 Python
  dependency installation and the apt-cache refresh after adding repositories.
  The daemon config also bounds json-file container logs (`max-size`/`max-file`),
  because journald's volatile limits do not cover those files.

## Host-specific behavior

- **Media:** NanoPi R6S with 64 GB eMMC. Install Debian Trixie from an SD eFlasher
  image onto eMMC and remove the SD card before running `site.yml`. `site.yml` runs only
  the shared `docker` role with its `fuse-overlayfs` default; no applications
  are configured. Its vault contains only the root and pi passwords.
- **ACME:** NanoPi Zero2. `site.yml` runs the `docker` role like AI and Media.
  `make deploy host=acme workload=acme-dns-gateway` deploys the gateway through the `acme`
  Docker context. Its vault contains only the root and pi passwords.
  `make deploy` provisions each workload's `copy_files` from its `deploy.yml`
  to `/opt/<workload>/` on the target, preserving workload-relative paths
  (root-owned, directories `0700`, files `0400`), and sets `COPY_DIR` so
  Compose mounts those host-side files; Compose `secrets: file:` and
  bind-mount paths resolve on the client and cannot cross SSH Docker
  contexts. Updating `copy_files` contents does not recreate containers;
  restart the affected container to reload secret-bearing processes.
  `make deploy` accepts `force_recreate=<bool>` to pass `--force-recreate` to
  Compose, recreating containers whose images and configuration are unchanged.
  The gateway's Caddy reverse-proxies unauthenticated `GET /health` to the
  backend alongside `POST /update`; `make check-health` probes it through the
  workload's declared `health_checks`.
- **Tailgate:** enables IPv4/IPv6 forwarding and advertises `10.4.0.0/24`
  (management), `10.4.1.0/24` (trusted), and `10.4.4.0/24` (isolated). Route approval
  in the Tailscale admin console is a manual prerequisite for usable routing.
  It accepts routes and enables auto-update. `tailscale_exit_node` is
  unused; setting `tailscale_auto_update: false` skips enabling it rather than
  actively disabling it. `tailscale up` always reports changed.
- **AI:** `site.yml` runs the `docker` role. `make deploy host=ai workload=litellm`
  deploys the stack and `make deploy host=ai workload=caddy` deploys the host's TLS
  ingress. LiteLLM publishes no host port; Caddy terminates TLS for
  `litellm.srjhome.net` on 443 and reverse proxies to `litellm:4000` over the
  external `caddy_litellm` network, declared in each workload's `deploy.yml`
  and created by `make deploy` when absent. See
  [`docs/tls-ingress.md`](docs/tls-ingress.md).
- The Compose template defines LiteLLM (`v1.103.2`), PostgreSQL 16, and the
  `models-sync` sidecar (`python:3.14-slim`).
  Preserve the persistent volume `litellm_postgres_data`, the database dependency
  health check, and LiteLLM's 300-second cold start allowance.
- LiteLLM settings use Compose environment variables: `DATABASE_URL`,
  `LITELLM_MASTER_KEY`, `STORE_MODEL_IN_DB=True`, and
  `STORE_PROMPTS_IN_SPEND_LOGS=True`. `NEBIUS_API_KEY` authenticates inference
  and Token Factory catalog requests. Prompts and responses are stored in
  database spend logs.
- LiteLLM reads static router aliases and ordered fallbacks from `config.yaml`.
  `deploy.yml` copies it to `/opt/litellm/config.yaml`; Compose mounts that host
  file read-only and passes `--config /app/config.yaml`. Model deployments remain
  database-managed. Claude aliases route to `zai-org/GLM-5.3`, with ordered
  fallbacks to `moonshotai/Kimi-K2.7-Code` and
  `deepseek-ai/DeepSeek-V4-Pro-0813`. Apply file edits with
  `make deploy host=ai workload=litellm force_recreate=true` because copying
  a bind-mounted configuration file does not restart LiteLLM.
- `sync/sync_models.py` uses only the Python standard library and mirrors every
  Token Factory model into LiteLLM's database through the master-key-authenticated
  management API. Token Factory is authoritative for the entire model database,
  including manually added models and models from other providers. Client-facing
  names are exact Token Factory IDs; routing uses `nebius/<id>` with the explicit
  Token Factory API base and an `os.environ/NEBIUS_API_KEY` credential reference.
  Source pricing and capabilities are preserved under `model_info.tokenfactory`.
  Preserve null prices as unavailable metadata; map only non-null prices to
  numeric costs and reject invalid, negative, or non-finite prices.
  Recognized rates, context lengths, modes, and vision support use LiteLLM fields.
  Unknown modalities are retained without inferred capabilities.
  Read every page of `GET /v2/model/info` before writes; this endpoint supports
  an empty database. Reject malformed or inconsistent pagination before writes.
  Model updates use `PATCH /model/{id}/update` to persist routing parameters
  and model metadata together; creation and deletion use `POST /model/new`
  and `POST /model/delete`.
- The sync sidecar starts after LiteLLM is healthy, runs immediately, and waits
  `SYNC_INTERVAL` seconds (positive integer, default `600`) after each cycle.
  HTTP requests have 30-second timeouts. The complete source catalog and target
  model list are validated before writes. Empty or invalid catalogs preserve
  database models. Creates and updates complete before absent models and
  duplicates are deleted; failed writes prevent deletion. Partial cycles converge
  on subsequent attempts. Logs contain cycle outcomes and mutation counts,
  without credentials or response bodies. `sync/sync_models_test.py` contains
  pure unit tests.
- `open-webui` and a shared external Docker network are planned, not implemented.
