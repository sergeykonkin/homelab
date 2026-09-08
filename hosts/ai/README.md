# ai

FriendlyElec NanoPi Zero2 host for Docker and the LiteLLM workload.

Run these commands from the repository root:

```sh
make init

make bootstrap host=ai

docker --context ai network create caddy_litellm
make apply workload=caddy
make apply workload=litellm
```
