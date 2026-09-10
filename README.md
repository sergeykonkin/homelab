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
plaintext counterpart with `~/.age/age.key`. To update an Ansible or workload
secret, edit its plaintext file and re-encrypt the changed files:

```sh
make encrypt-secrets
```

The target encrypts with the public key derived from `~/.age/age.key` and
rewrites only `.age` files whose plaintext changed.

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

## Healthcheck hosts and workloads

```sh
make check-health                           # every host and workload
make check-health host=ai                   # one host and all its workloads
make check-health host=ai workload=caddy    # one workload on one host
```

`make check-health` is read-only. It checks SSH access, the Docker context and
daemon, every Compose service's container (running, and healthy when the
service defines a healthcheck), and the HTTP `health_checks` URLs declared
in each workload's `deploy.yml`. Tailgate is checked as a subnet router:
`tailscaled` active, the Tailscale backend running, and its role's
advertise routes configured. Host-specific checks live in
`hosts/<host>/healthz.mk`, included by the root Makefile alongside the shared
helpers in `healthz.mk`. The command prints
each check's result and, when any check fails, reports the failed checks'
count and hosts and exits non-zero.
