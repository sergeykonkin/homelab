# homelab

Ansible and Docker Compose source of truth for the hosts on the home network.
Each managed host lives under `hosts/<name>/`; host-specific Ansible projects
live in `ansible/` and Docker Compose projects live in `workloads/`.

## Hosts

| Host | Purpose |
| --- | --- |
| [`tailgate.home.arpa`](hosts/tailgate/) | Tailscale subnet router |
| [`ai.home.arpa`](hosts/ai/) | Docker host for LiteLLM |
| [`media.home.arpa`](hosts/media/) | Docker host |
| [`acme.home.arpa`](hosts/acme/) | Docker host for the ACME-DNS gateway |

`optiplex.home.arpa` is unmanaged and does not use this repository.

## Prepare the control machine

```sh
brew install ansible ansible-lint age docker docker-compose docker-buildx yq
make init
```

`make init` decrypts each tracked, ASCII-armored `.age` file to its gitignored
plaintext counterpart with `~/.age/age.key`. To update an Ansible secret, edit
`hosts/<host>/ansible/secrets/secrets.yml` and refresh its ciphertext:

```sh
recipient="$(age-keygen -y ~/.age/age.key)"
age --armor --recipient "$recipient" \
  --output hosts/<host>/ansible/secrets/secrets.yml.age \
  hosts/<host>/ansible/secrets/secrets.yml
```

## Configure a host

```sh
make bootstrap host=<host>
```

Use `tailgate`, `ai`, `media`, or `acme` for `<host>`. The run applies all host
configuration and performs at most one reboot after its final tasks.

## Deploy a workload

```sh
make deploy host=ai workload=litellm
make deploy host=ai workload=all
```

Both `host` and `workload` are required. Use `workload=all` to deploy every
Compose project under `hosts/<host>/workloads/`; `host=all` is not supported.
Each workload's `deploy.yml` lists files copied to `/opt/<workload>/` and
external Docker networks that must exist. `make deploy` drives the selected
host's Docker daemon through a context prepared by `make init`.

Pass `force_recreate=true` to recreate containers even when their images and
configuration are unchanged — for example to pick up Docker daemon changes such
as log rotation limits, which Docker applies only at container creation. It
accepts the same boolean values as `docker compose up --force-recreate`:

```sh
make deploy host=acme workload=acme-dns-gateway force_recreate=true
```
