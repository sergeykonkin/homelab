# LiteLLM workload

LiteLLM, PostgreSQL, and the model updater run on `ai.home.arpa`. The stack
publishes no host port; the `caddy` workload terminates TLS for
`litellm.srjhome.net` and reaches LiteLLM over the external `caddy_litellm`
network (see [`docs/tls-ingress.md`](../../docs/tls-ingress.md)).

Run from the repository root:

```sh
make init
docker --context ai network create caddy_litellm
make apply workload=litellm
```
