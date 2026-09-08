# ACME-DNS gateway workload

This Compose project contains Caddy and the restricted Cloudflare DNS update
backend for the hostname configured by `GATEWAY_HOSTNAME`.

Create and age-encrypt `.env`, `secrets/gateway.json`, and
`secrets/caddy-acmedns.json` from their `.example` schemas. `make init` decrypts
the tracked ASCII-armored `.age` files to mode `0600`; plaintext files remain
outside Git. The Cloudflare token needs `Zone / DNS / Edit` for the configured zone.
For example, encrypt each completed file for the control-machine identity:

```sh
age --armor --encrypt \
  --recipient "$(age-keygen -y ~/.age/age.key)" \
  --output .env.age .env

age --armor --encrypt \
  --recipient "$(age-keygen -y ~/.age/age.key)" \
  --output secrets/gateway.json.age secrets/gateway.json

age --armor --encrypt \
  --recipient "$(age-keygen -y ~/.age/age.key)" \
  --output secrets/caddy-acmedns.json.age secrets/caddy-acmedns.json
```

The gateway hostname has a permanent public challenge CNAME:

```text
_acme-challenge.acme.example.com
  CNAME <gateway-subdomain>.acme.example.com
```

Split-horizon DNS maps the gateway hostname to the deployment host's private
address. Caddy publishes `443` on all interfaces and proxies `POST /update`
to the gateway backend, which authenticates each request with its configured
per-client credentials.

Deploy from the repository root:

```sh
make init
make apply workload=acme-dns-gateway
```

`make apply` provisions the decrypted files listed under `copy_files` in
[`workloads.yml`](../../workloads.yml) to `/opt/acme-dns-gateway/` on the
target host, preserving their workload-relative paths (root-owned, directories
`0700`, files `0400`), and points Compose at them with `COPY_DIR`. This
indirection exists because Compose resolves `secrets: file:` and bind-mount
paths on the client; a remote SSH Docker daemon cannot mount the control
machine's filesystem. Without `COPY_DIR`, the Compose project falls back to
the local workload directory for validation. Static configuration that does
not change at runtime (the Caddyfile) is baked into its image at build time
instead of being copied.

Run the backend tests from the repository root:

```sh
python3 -m unittest discover -s workloads/acme-dns-gateway/tests -v
```

Adding or rotating a client identity edits the `clients` array in
`secrets/gateway.json`, re-encrypts the tracked `.age` file, and re-applies
the workload. `make apply` updates the provisioned files on the host but
Compose does not recreate the container on secret-content changes, so restart
it to reload the configuration:

```sh
docker --context acme restart acme-dns-gateway
```
