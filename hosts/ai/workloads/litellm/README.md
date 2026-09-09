# LiteLLM workload

LiteLLM, PostgreSQL, and the model updater run on `ai.home.arpa`. The stack
publishes no host port; the `caddy` workload terminates TLS for
`litellm.srjhome.net` and reaches LiteLLM over the external `caddy_litellm`
network (see [`docs/tls-ingress.md`](../../../../docs/tls-ingress.md)).

Run from the repository root:

```sh
make init
make deploy host=ai workload=litellm
```

The workload's `deploy.yml` causes `make deploy` to create the external
`caddy_litellm` network when it is absent.
