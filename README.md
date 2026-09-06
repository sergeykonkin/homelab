# homelab

Single source of truth for the hosts on my home network. Each host lives in its
own folder under `hosts/<name>/` and is provisioned with **Ansible**, with manual
prereqs (installing the OS on SD or eMMC) documented in that host's `README.md`.
Host secrets are encrypted at rest with **ansible-vault**. Reprovisioning needs
a fresh `git clone`, the age identity at `~/.age/age.key`, and the SSH private
key matching the public key configured in `roles/firstboot/vars/main.yml`.

## Hosts

| Host | Hardware | Role | Folder |
|------|----------|------|--------|
| `tailgate.home.arpa` | FriendlyElec NanoPi Zero2 (arm64) | Tailscale subnet router (`10.4.0.0/24`, `10.4.1.0/24`, `10.4.4.0/24`) | [`hosts/tailgate/`](hosts/tailgate/) |
| `ai.home.arpa` | FriendlyElec NanoPi Zero2 (arm64) | Docker host for the LiteLLM workload | [`hosts/ai/`](hosts/ai/) |
| `media.home.arpa` | FriendlyElec NanoPi R6S (arm64, 64 GB eMMC) | Docker host | [`hosts/media/`](hosts/media/) |

## Unmanaged hosts

`optiplex.home.arpa` (Dell x86_64, Debian) is **not** provisioned
by this repo. It sits in the DMZ and runs assorted services managed manually on
the box.

## Layout

```
homelab/
├── roles/                   # cross-host roles (promoted here when used by 2+ hosts)
│   ├── firstboot/          #   one-time bootstrap: passwords, pubkey, hostname, journald, apt, sshd harden, reboot
│   └── docker/             #   Docker + fuse-overlayfs (NanoPi overlay-root needs it)
├── hosts/
│   └── <name>/             # self-contained: ansible.cfg, inventory.ini, firstboot.yml, site.yml, roles/, secrets/
└── workloads/
    └── <name>/             # Compose workload, encrypted environment, deployment metadata
```

Each host folder is an independent Ansible project — `cd` into it and run a
playbook. There is no top-level inventory; `ls hosts/` is the list of hosts.

Roles start **colocated** in `hosts/<name>/roles/` and are promoted to `roles/`
only once a second host needs them (a pure `git mv` — playbooks reference roles
by name, not path).

## Bootstrap (one-time, on a new machine)

One ansible-vault password decrypts all tracked
`hosts/<name>/secrets/vault.yml` files. `.vault-pass.age` stores that password
encrypted to the age identity; keep the age identity and SSH private key outside
the repository.

1. `git clone` this repo, enter its directory, and run `make init` to configure
   Git to use the tracked `hooks/` directory.
2. Install `age` and make `~/.age/age.key` available. It decrypts the tracked
   `.vault-pass.age` file into the ignored local `.vault-pass` file during
   `make init`.
3. Make the SSH private key matching `firstboot_pubkey` in
   `roles/firstboot/vars/main.yml` available to SSH (via a default identity,
   SSH config, or an agent). Firstboot needs it to reconnect as root after reboot;
   subsequent playbooks use it for root access.

Playbooks decrypt their host's vault automatically. Reuse those
vaults when reprovisioning; use `ansible-vault edit secrets/vault.yml` from the
host directory to update values. Plaintext `vault.yml` files are **not ignored**:
verify the `$ANSIBLE_VAULT;` header before staging any vault.

The pre-commit hook checks the Git index for every file under
`hosts/*/secrets/`, except plaintext `.example` templates. It blocks commits if
any of these files lacks the `$ANSIBLE_VAULT;` header, without printing their
contents. Encrypt files from their host directory with `ansible-vault encrypt`,
then stage the encrypted files again. The hook checks the header only; it does
not decrypt or authenticate vault contents.

## Setting up a host

Each host has **two playbooks**:

- **`firstboot.yml`** — run *once* on a fresh OS installation on SD or eMMC.
  Connects with the default `pi:pi` image credential, escalates with sudo, sets strong passwords,
  installs the configured root SSH public key, sets the hostname, moves journald
  to RAM, runs apt upgrade, hardens sshd to key-only,
  and reboots. See the host's README for the exact manual prereqs (burn image,
  find the DHCP IP, etc.).
  ```bash
  cd hosts/<name>
  ansible-playbook firstboot.yml -e ansible_host=<DHCP-IP>
  ```

- **`site.yml`** — run *anytime after firstboot* to apply host configuration
  (Tailscale on tailgate; Docker on ai and media).
  Re-runnable; converges the host to the desired state.
  ```bash
  cd hosts/<name>
  ansible-playbook site.yml
  ```

See each host's `README.md` for the full setup walkthrough.

## Tooling on the control machine

- `make` and Python 3 — `make init` configures the repository's pre-commit hook,
  decrypts local workload environments, configures Docker contexts, and runs with
  `python3`.
- `ansible` (ansible-core ≥ 2.21) — `brew install ansible`. `firstboot.yml`
  connects to a fresh host with the default image password over SSH; ansible-core
  ≥ 2.19 handles password auth natively (no extra tooling required).
- `ansible-lint` (optional) — `brew install ansible-lint`
- `age` — decrypts `.vault-pass.age` and workload `.env.age` files using
  `~/.age/age.key`.
- Docker CLI with Compose and SSH support — applies workloads through Docker
  contexts.

## Workloads

Ansible configures hosts only. Compose workloads are applied from the control
machine through a Docker context.

Run the one-time local setup after cloning:

```bash
make init
```

It sets Git's local hooks path, decrypts `.vault-pass.age` and all workload
`.env.age` files, and creates Docker contexts for `ai` and `media`. Plaintext
`.vault-pass` and workload `.env` files are local, mode `0600`, and ignored by
Git.

Apply a workload by name:

```bash
make apply WORKLOAD=litellm
```
