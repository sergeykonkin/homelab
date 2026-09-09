# homelab

Ansible and Docker Compose source of truth for the hosts on the home network.
Each managed host lives under `hosts/<name>/`; host-specific Ansible projects
live in `bootstrap/` and Docker Compose projects live in `workloads/`.

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

## Configure a host

```sh
make bootstrap host=<host>
```

Use `tailgate`, `ai`, `media`, or `acme` for `<host>`. The run applies all host
configuration and performs at most one reboot after its final tasks.

## Apply a workload

```sh
make apply host=ai workload=litellm
make apply host=ai workload=all
```

Both `host` and `workload` are required. Use `workload=all` to apply every
Compose project under `hosts/<host>/workloads/`; `host=all` is not supported.
Each workload's `deploy.yml` lists files copied to `/opt/<workload>/` and
external Docker networks that must exist. `make apply` drives the selected
host's Docker daemon through a context prepared by `make init`.
