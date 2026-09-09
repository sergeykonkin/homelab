# ai

FriendlyElec NanoPi Zero2 host for Docker and the LiteLLM workload.

Run these commands from the repository root:

```sh
make init

make bootstrap host=ai

make apply host=ai workload=litellm
make apply host=ai workload=caddy

# Apply every workload on the host, with Caddy applied last.
make apply host=ai workload=all
```
