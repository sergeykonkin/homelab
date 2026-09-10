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
| `hosts/ai/` | `ai.home.arpa`: Docker, LiteLLM, PostgreSQL, model updater |
| `hosts/media/` | `media.home.arpa`: R6S, SD-to-eMMC OS installation and Docker only |
| `hosts/acme/` | `acme.home.arpa`: Docker host for the ACME-DNS gateway workload |
| `hosts/<name>/ansible/` | Host-specific Ansible project |
| `hosts/<name>/workloads/` | Compose projects deployed to that host |
| `shared_roles/bootstrap/` | Passwords, root SSH key, hostname, apt upgrade, RAM logs, SSH hardening, final reboot |
| `shared_roles/docker/` | Docker CE/Compose installation and fuse-overlayfs configuration |
| `docs/` | Design documentation |

- Each host's `ansible/` directory is an independent Ansible project containing
  `ansible.cfg`, `inventory.ini`, `site.yml`, local `roles/`, and `secrets/`.
  There is no root inventory or root playbook. Run Ansible **inside
  `hosts/<name>/ansible/`** so its config resolves `../../../shared_roles:./roles`
  correctly.
- Each `hosts/<name>/workloads/<workload>/` directory is an independent Compose
  project. Its `deploy.yml` declares copied files and external Docker networks.
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
For updater edits, use `bash -n hosts/ai/workloads/litellm/entrypoint.sh` and
Python syntax validation from the repo root; exercise model parsing/config/hash
behavior with fixtures and a temporary `CONFIG_DIR`, avoiding live API calls.
The shell script requires Bash and GNU `date` inside its Linux container.
For ACME-DNS gateway edits, run its Python unit tests, validate the Compose
configuration with the example runtime values, and use `sh -n` for its host
scripts.
Finish with `git diff --check` and review the changed files.

## Secrets

- Ansible and workload `.age` files are tracked ASCII-armored age ciphertext.
  Their counterparts without `.age` are ignored plaintext files produced by
  `make init`. Encrypt edited plaintext with `age --armor` and the public key
  derived from `~/.age/age.key`.
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
- Install the configured root public key before disabling password SSH. Preserve
  the `00-homelab-hardening.conf` drop-in, `/run/sshd` creation plus `sshd -t`
  before restarting SSH, and handler flush before reboot. Reboot reconnects as
  root using public-key authentication. Ensure the control machine has the
  private key matching `bootstrap_pubkey` before provisioning.
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
- The Compose template defines LiteLLM (`main-stable`), PostgreSQL 16, and a
  Python 3.12 updater image. Preserve persistent volumes `litellm_postgres_data`
  and `litellm_config`, dependency health checks, and LiteLLM's 300-second cold
  start allowance.
- `update_config.py` uses only the Python standard library. It fetches Nebius
  `models?verbose=1`, filters models that accept text input and produce text
  output (`text->text` and `text+image->text`; not `text->embedding` or
  `text->text+image`), writes model names/provider IDs/pricing
  plus `drop_params: true`, and rejects an empty catalog so a transient empty
  response cannot wipe the working configuration. It skips unchanged writes by
  comparing SHA-256 of the generated content against `config.yaml` itself —
  regenerating the file when it is missing or corrupted despite a matching
  stored hash — and publishes replacements atomically. `config.yaml` and its
  hash are generated in the shared volume, not deployed
  from Git. `entrypoint.sh` creates the directory, updates at startup, then
  runs daily at 04:20 in the container's timezone. It retries the startup
  update until a usable configuration exists — an empty config volume would
  otherwise leave LiteLLM's healthy-updater dependency unmet — and restarts
  `litellm` on any config change, including the initial build, tracking the
  applied config hash separately so a failed restart is retried. Preserve this
  startup ordering.
- The updater's Docker socket mount is marked `:ro` and permits Docker
  API mutations (including its restart command); treat it as privileged access.
  `open-webui` and a shared external Docker network are planned, not implemented.
