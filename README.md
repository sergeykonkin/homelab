# homelab

Ansible source of truth for the hosts on the home network. Each managed host
lives under `hosts/<name>/`; the repository also contains Compose workloads.

## Hosts

| Host | Purpose |
| --- | --- |
| [`tailgate.home.arpa`](hosts/tailgate/) | Tailscale subnet router |
| [`ai.home.arpa`](hosts/ai/) | Docker host for LiteLLM |
| [`media.home.arpa`](hosts/media/) | Docker host |

`optiplex.home.arpa` is unmanaged and does not use this repository.

## Prepare the control machine

```sh
brew install ansible ansible-lint age docker docker-compose docker-buildx
make init
```

## Configure a host

```sh
make bootstrap host=<host>
```

Use `tailgate`, `ai`, or `media` for `<host>`. The run applies all host
configuration and performs at most one reboot after its final tasks.

## Apply a workload

```sh
make apply workload=litellm
```
