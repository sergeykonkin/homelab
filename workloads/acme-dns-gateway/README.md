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
_acme-challenge.acme-gateway.example.com
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

Run the backend tests from the repository root:

```sh
python3 -m unittest discover -s workloads/acme-dns-gateway/tests -v
```
