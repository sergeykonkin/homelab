# Repository guide

This repo is the Ansible source of truth for three FriendlyElec NanoPi hosts:
two Zero2 boards and one R6S with 64 GB eMMC (Debian Trixie, arm64).
Read the root README and the affected host's README
for setup prerequisites; prefer executable configuration when comments disagree.
`CLAUDE.md` is a relative symlink to this file; keep one source of instructions.

An unmanaged x86_64 host, `optiplex.home.arpa` (Dell, Debian),
also lives on the network in the DMZ. This repo does **not** provision it; it
runs services configured manually on the box.

## Layout and conventions

| Path | Responsibility |
| --- | --- |
| `hosts/tailgate/` | `tailgate.home.arpa`: Tailscale subnet router only |
| `hosts/ai/` | `ai.home.arpa`: Docker, LiteLLM, PostgreSQL, model updater |
| `hosts/media/` | `media.home.arpa`: R6S, SD-to-eMMC OS installation, bootstrap and Docker only |
| `roles/bootstrap/` | Passwords, root SSH key, hostname, apt upgrade, RAM logs, SSH hardening, reboot |
| `roles/docker/` | Docker CE/Compose installation and fuse-overlayfs configuration |

- Each host is an independent Ansible project: `ansible.cfg`, `inventory.ini`,
  `bootstrap.yml`, `site.yml`, local `roles/`, and `secrets/`. There is no root
  inventory or root playbook. Run Ansible **inside `hosts/<name>/`** so its config
  resolves `../../roles:./roles` and `../../.vault-pass` correctly.
- Keep new roles local until a second host needs them, then move them to
  `roles/`; playbooks reference role names. For a new host, follow the existing
  project layout and update the root host table and host README.
- Inventory groups use `<name>_hosts` to avoid host/group name collisions.
  Steady-state access is root over SSH keys; Python is `/usr/bin/python3`.
- Follow existing YAML style: two-space indentation, named tasks, fully qualified
  module names, quoted file modes, role-prefixed variables, and `vault_` secrets.
  Put tunables in role defaults; existing bootstrap settings/key live in
  `roles/bootstrap/vars/main.yml` (higher precedence than defaults).
- Keep repeatable setup convergent, use handlers for service configuration, and
  give command/shell tasks deliberate change/failure reporting. Update README
  instructions and vault examples when changing their interfaces.
- Write documentation and comments as descriptions of the current state. Omit
  change history and wording such as "now" or "previously" that narrates edits.
- Commit and push changes only when explicitly asked to deliver, ship, submit,
  or commit and push them. Push directly to `main` in this repo; do not create
  feature branches or pull requests.

## Commands and validation

Use the full `ansible` distribution with ansible-core >= 2.21
(`brew install ansible`); `ansible-lint` is optional. There is no CI, test suite,
dependency manifest, or top-level build command.

Run `make init` from the repo root to configure Git's local `core.hooksPath` as
`hooks`. The Python 3 pre-commit hook requires an Ansible Vault header on indexed
files under `hosts/*/secrets/`, an ASCII-armored age header on `.vault-pass.age`
and `workloads/*/.env.age`, and rejects staged plaintext secret files.

```sh
# Run from each affected host directory; roles/bootstrap changes affect all hosts.
cd hosts/ai                    # or hosts/tailgate or hosts/media
ansible-playbook --syntax-check bootstrap.yml
ansible-playbook --syntax-check site.yml
ansible-lint bootstrap.yml site.yml  # if installed

# Live provisioning, when deployment is part of the task:
ansible-playbook bootstrap.yml -e ansible_host=<DHCP-IP>  # fresh image only
ansible-playbook site.yml                              # after bootstrap
```

Syntax checks use the configured vault password without contacting the hosts.
They do not verify runtime behavior. `--check` is not a complete deployment
simulation: command tasks, password-hash results, and generated files depend on
real execution. Do not run live provisioning merely to validate repository edits.
For updater edits, use `bash -n workloads/litellm/entrypoint.sh` and
Python syntax validation from the repo root; exercise model parsing/config/hash
behavior with fixtures and a temporary `CONFIG_DIR`, avoiding live API calls.
The shell script requires Bash and GNU `date` inside its Linux container.
Finish with `git diff --check` and review the changed files.

## Secrets

- `.vault-pass` is ignored and must never be committed or printed. `.vault-pass.age`
  and workload `.env.age` files are ASCII-armored age ciphertext. All
  `hosts/*/secrets/vault.yml` files **are tracked** and must start with
  `$ANSIBLE_VAULT;`. Plaintext vaults are **not** protected by `.gitignore`.
  Check encryption before staging any vault.
- Use `ansible-vault edit secrets/vault.yml` from the host directory for existing
  secrets. The `.example` files define the schema with placeholders; never copy
  one over an existing vault as a routine setup step or expose decrypted values
  in logs, diffs, or replies.
- All vaults contain `vault_root_pw` and `vault_pi_pw`; Tailgate also has
  `vault_tailscale_authkey`. Workload secrets are stored in their workload's
  `.env.age` file. Preserve `no_log: true` on secret-bearing tasks and keep
  generated credentials and config out of Git.

## Bootstrap and Docker invariants

- Bootstrap connects as **`pi:pi`**, becomes root with sudo, changes both passwords,
  and switches the remaining sudo tasks to the new pi password.
- Install the configured root public key before disabling password SSH. Preserve
  the `00-homelab-hardening.conf` drop-in, `/run/sshd` creation plus `sshd -t`
  before restarting SSH, and handler flush before reboot. Reboot reconnects as
  root using public-key auth at the same overridden DHCP IP. Ensure the control
  machine has the private key matching `bootstrap_pubkey` before provisioning.
- Bootstrap changes access credentials, upgrades packages, and reboots; it is
  for fresh images. `site.yml` is the repeatable setup path. Current per-host
  configs disable SSH host-key checking.
- NanoPi's root filesystem is overlayfs: Docker needs **fuse-overlayfs**, since
  `overlay2` cannot nest on it. Flush the Docker restart handler before app roles.
  Docker and Tailscale apt URLs derive distribution/release from gathered facts;
  Docker's repository architecture is explicitly arm64. Preserve deb822 Python
  dependency installation and the apt-cache refresh after adding repositories.

## Host-specific behavior

- **Media:** NanoPi R6S with 64 GB eMMC. Install Debian Trixie from an SD eFlasher
  image onto eMMC and remove the SD card before bootstrap. `site.yml` runs only
  the shared `docker` role with its `fuse-overlayfs` default; no applications
  are configured. Its vault contains only the root and pi passwords.
- **Tailgate:** enables IPv4/IPv6 forwarding and advertises `10.4.0.0/24`
  (management), `10.4.1.0/24` (trusted), and `10.4.4.0/24` (isolated). Route approval
  in the Tailscale admin console is a manual prerequisite for usable routing.
  It accepts routes and enables auto-update. `tailscale_exit_node` is
  unused; setting `tailscale_auto_update: false` skips enabling it rather than
  actively disabling it. `tailscale up` always reports changed.
- **AI:** `site.yml` runs `docker` then `litellm`, deploying `/opt/litellm` and
  invoking `docker compose up -d --build`. `litellm.home.arpa` is the app's TLS
  alias, distinct from the managed host `ai.home.arpa`. TLS is terminated by
  LiteLLM on `443:4000`; clients need the mkcert CA trust described in the README.
  Renewal/trust-location documentation remains a TODO.
- The Compose template defines LiteLLM (`main-stable`), PostgreSQL 16, and a
  Python 3.12 updater image. Preserve persistent volumes `litellm_postgres_data`
  and `litellm_config`, dependency health checks, and LiteLLM's 300-second cold
  start allowance. The cert bind mount hardcodes `/opt/litellm/certs`; changing
  role deployment-path defaults alone does not relocate the entire stack.
- `update_config.py` uses only the Python standard library. It fetches Nebius
  `models?verbose=1`, filters `text->text`, writes model names/provider IDs/pricing
  plus `drop_params: true`, and uses SHA-256 to skip unchanged writes.
  `config.yaml` and its hash are generated in the shared volume, not deployed
  from Git. `entrypoint.sh` creates the directory, updates at startup without
  restarting LiteLLM, then runs daily at 04:20 in the container's timezone and
  restarts `litellm` when an existing hash changes. Preserve this startup ordering.
- The updater's Docker socket mount is marked `:ro` and permits Docker
  API mutations (including its restart command); treat it as privileged access.
  `open-webui` and a shared external Docker network are planned, not implemented.
