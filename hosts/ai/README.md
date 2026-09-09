# ai

FriendlyElec NanoPi Zero2 host for Docker and the LiteLLM workload.

Run these commands from the repository root:

```sh
make init

make bootstrap host=ai

make deploy host=ai workload=litellm
make deploy host=ai workload=caddy

# Deploy every workload on the host, with Caddy deployed last.
make deploy host=ai workload=all
```
