# LiteLLM workload

This Compose workload runs LiteLLM, PostgreSQL 16, and the Nebius model updater
on the `ai` Docker context. LiteLLM listens on HTTP port `4000`. An ingress
workload such as Caddy provides TLS and public routing.

## Secrets

`.env.age` contains the complete environment file encrypted to the age identity
whose private key is stored at `~/.age/age.key`. `.env` is the local decrypted
file and is ignored by Git.

Create or replace the encrypted environment file from a plaintext `.env`:

```bash
age-keygen -y ~/.age/age.key | age --armor --encrypt --recipients-file - --output .env.age .env
rm .env
make init
```

The file contains `NEBIUS_API_KEY`, `LITELLM_MASTER_KEY`, and
`POSTGRES_PASSWORD`. See `.env.example` for the expected keys.

## Apply

From the repository root:

```bash
make apply WORKLOAD=litellm
```

The workload keeps its existing named Docker volumes:

- `litellm_postgres_data`
- `litellm_config`
