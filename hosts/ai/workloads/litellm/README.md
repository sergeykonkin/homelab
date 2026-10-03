# LiteLLM workload

LiteLLM, PostgreSQL, and the `models-sync` sidecar run on `ai.home.arpa`. The stack
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

## Configuration and model synchronization

LiteLLM uses Compose environment variables for its master key, database URL,
database model storage, and prompt/response spend logging. The workload's
`.env.example` lists the required credentials and `SYNC_INTERVAL` in seconds
(default `600`).

`config.yaml` maps five Claude IDs (Haiku 4.5, Sonnet 5.5, Opus 5.5, Fable 5.1,
and Sonnet 5) to `zai-org/GLM-5.3`. Failed requests fall back to
`moonshotai/Kimi-K2.7-Code`, then `deepseek-ai/DeepSeek-V4-Pro-0813`.
The deployment copies this file to `/opt/litellm/config.yaml` on the host and
mounts it read-only. After editing an existing configuration, run
`make deploy host=ai workload=litellm force_recreate=true` to reload it.

The sidecar reads every model from Nebius Token Factory and mirrors the entire
LiteLLM model database through its authenticated management API. Client-facing
names are exact Token Factory IDs. Pricing and capability metadata are stored
with the models. Manual model additions and edits, including other providers,
are overwritten or removed during synchronization.

Model updates use LiteLLM's `PATCH /model/{id}/update` endpoint to persist
routing parameters and model metadata together.

Synchronization runs at startup and waits `SYNC_INTERVAL` seconds after each
cycle. Empty or invalid catalogs leave database models untouched. Failed creates
or updates prevent deletion; partial writes converge on subsequent cycles.
Cycle outcomes and mutation counts appear in the sidecar's container logs.

API references: [Token Factory model catalog](https://docs.tokenfactory.nebius.com/api-reference/models/list-models),
[LiteLLM model management](https://docs.litellm.ai/docs/proxy/model_management),
and [LiteLLM configuration](https://docs.litellm.ai/docs/proxy/config_settings).

## Local validation

Run from the repository root:

```sh
python3 -m unittest discover -s hosts/ai/workloads/litellm/sync -p '*_test.py' -v
docker compose --env-file hosts/ai/workloads/litellm/.env.example \
  -f hosts/ai/workloads/litellm/compose.yaml config --quiet
```

The tests use only Python's standard library and mock HTTP and timing. They do
not contact Token Factory or LiteLLM.
