# ai

A FriendlyElec **NanoPi Zero2** (arm64, Debian Trixie) configured as a Docker
host. Application workloads are managed from the control machine through Docker
contexts; this host's Ansible project does not deploy applications.

## Manual prerequisites

1. Burn the FriendlyElec NanoPi Zero2 Debian Trixie (arm64) image to an SD card.
2. Boot it and find its DHCP IP address.
3. Prepare the control machine according to the [root README](../../README.md#bootstrap-one-time-on-a-new-machine).

## First boot

```bash
cd hosts/ai
ansible-playbook firstboot.yml -e ansible_host=<DHCP-IP>
```

Firstboot connects as `pi:pi`, configures passwords and root key access, sets
the hostname, upgrades packages, hardens SSH, and reboots.

## Host configuration

```bash
cd hosts/ai
ansible-playbook site.yml
```

The playbook installs Docker CE, Docker Compose, Buildx, and `fuse-overlayfs`.
The NanoPi root filesystem uses overlayfs, so Docker uses fuse-overlayfs.

## Workloads

Run from the repository root:

```bash
make init
make apply WORKLOAD=litellm
```

`make init` creates the `ai` Docker context at `ssh://root@ai.home.arpa` and
decrypts `workloads/litellm/.env.age` into the ignored local `.env` file. The
LiteLLM workload serves HTTP on `ai.home.arpa:4000`. Caddy provides TLS and
public routing when it is deployed as an ingress workload.

## Host vault

`secrets/vault.yml` contains only the root and pi passwords used by firstboot.
Use `ansible-vault edit secrets/vault.yml` from this directory to change them.
