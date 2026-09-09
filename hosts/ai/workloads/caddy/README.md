# Caddy ingress workload

Caddy is the only web ingress on its host: it publishes `443`, terminates
publicly trusted TLS, and reverse proxies to application containers over
dedicated per-service Docker networks. Certificates are issued by Let's
Encrypt through DNS-01 against the private ACME-DNS gateway; port 80 is not
published.

Create and age-encrypt `.env` and `secrets/caddy-acmedns.json` from their
`.example` schemas. `make init` decrypts the tracked ASCII-armored `.age`
files to mode `0600`; plaintext files remain outside Git. The credentials are
the service's client identity on the gateway; the gateway reconciles the
service's public challenge CNAME (targeting `<subdomain>.acme.example.com`)
and an unproxied A record mapping the service name to the deployment host's
private address.

Initial issuance runs against the Let's Encrypt staging CA; switch
`ACME_CA` to the production directory after DNS-01 and routing validate.

The Caddyfile is baked into the image and renders one site from
`SITE_HOSTNAME` and `SITE_UPSTREAM`. Each proxied service joins a dedicated
`caddy_<service>` network shared only with Caddy. Networks are external and
listed in `deploy.yml`; `make deploy` creates them when absent.

Deploy from the repository root:

```sh
make init
make deploy host=ai workload=caddy
```

`make deploy` provisions the decrypted files listed under `copy_files` in
`deploy.yml` to `/opt/caddy/` on the target host and points Compose at them
with `COPY_DIR`; see the ACME-DNS gateway README for why this indirection
exists. Without `COPY_DIR`, the project falls back to the local workload
directory for validation.

ACME account data and certificates persist in the `caddy_data` volume on the
host; Caddy renews certificates automatically.
