# homelab

Single source of truth for the hosts on my home network. Each host lives in its
own folder under `hosts/<name>/` and is provisioned with **Ansible**, with manual
prereqs (burning the SD image, etc.) documented in that host's `README.md`.
Secrets are encrypted at rest with **ansible-vault**, so a fresh `git clone` plus
one password can re-setup any host.

## Hosts

| Host | Hardware | Role | Folder |
|------|----------|------|--------|
| `tailgate.home.arpa` | FriendlyElec NanoPi Zero2 (arm64) | Tailscale subnet router (`10.4.0.0/24`, `10.4.1.0/24`, `10.4.4.0/24`) | [`hosts/tailgate/`](hosts/tailgate/) |
| `ai.home.arpa` | FriendlyElec NanoPi Zero2 (arm64) | LiteLLM proxy app (serving `litellm.home.arpa` on :443); `open-webui` planned | [`hosts/ai/`](hosts/ai/) |

## Layout

```
homelab/
├── shared/                  # cross-host roles (promoted here when used by 2+ hosts)
│   ├── firstboot/          #   one-time bootstrap: passwords, pubkey, hostname, journald, apt, sshd harden, reboot
│   └── docker/             #   docker + fuse-overlayfs (NanoPi overlay-root needs it)
└── hosts/
    └── <name>/             # self-contained: ansible.cfg, inventory.ini, firstboot.yml, site.yml, roles/, secrets/
```

Each host folder is an independent Ansible project — `cd` into it and run a
playbook. There is no top-level inventory; `ls hosts/` is the list of hosts.

Roles start **colocated** in `hosts/<name>/roles/` and are promoted to `shared/`
only once a second host needs them (a pure `git mv` — playbooks reference roles
by name, not path).

## Bootstrap (one-time, on a new machine)

There is exactly **one secret not in this repo**: the ansible-vault password.
Everything else is encrypted in `hosts/<name>/secrets/vault.yml` and decrypts
with it.

1. `git clone` this repo.
2. Put your vault password (from your password manager) into `.vault-pass`:
   ```bash
   echo -n 'your-vault-password' > .vault-pass   # no trailing newline
   ```
   `.vault-pass` is gitignored — never commit it.
3. Done. Any playbook now decrypts its host's secrets automatically.

## Setting up a host

Each host has **two playbooks**:

- **`firstboot.yml`** — run *once* on a freshly-burned SD card. Connects with the
  default image credential, sets strong passwords, drops your SSH key, sets the
  hostname, moves journald to RAM, runs apt upgrade, hardens sshd to key-only,
  and reboots. See the host's README for the exact manual prereqs (burn image,
  find the DHCP IP, etc.).
  ```bash
  cd hosts/<name>
  ansible-playbook firstboot.yml -e ansible_host=<DHCP-IP>
  ```

- **`site.yml`** — run *anytime after firstboot* to install the host's actual
  purpose (Tailscale on tailgate; Docker + LiteLLM app on ai). Re-runnable; converges
  the host to the desired state.
  ```bash
  cd hosts/<name>
  ansible-playbook site.yml
  ```

See each host's `README.md` for the full setup walkthrough.

## Tooling on the control machine

- `ansible` (ansible-core ≥ 2.21) — `brew install ansible`. `firstboot.yml`
  connects to a fresh host with the default image password over SSH; ansible-core
  ≥ 2.19 handles password auth natively (no extra tooling required).
- `ansible-lint` (optional) — `brew install ansible-lint`
