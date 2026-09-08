# TLS ingress design

This document defines TLS ingress for Docker workloads on managed homelab
hosts.

## Overview

Each host that runs HTTPS services has one Caddy container, deployed as the
`workloads/caddy/` Compose project. Caddy is the only web ingress for that
host and is responsible for reverse proxying, ACME certificate issuance,
certificate renewal, and TLS private-key storage.

A dedicated private DNS update gateway (`workloads/acme-dns-gateway/`) is the
only component with automated write access to the public DNS zone. It exposes
a restricted, acme-dns-compatible update API to ACME clients on the internal
network. Each client has credentials that authorize updates only to its
assigned DNS-01 challenge records.

```text
                 Let's Encrypt
                   |       ^
        TXT lookup |       | ACME orders
                   v       |
       Cloudflare authoritative DNS (srjhome.net)
                   ^
                   | restricted TXT updates
           acme.home.arpa
           acme-dns-gateway workload
                   ^
                   | POST /update over HTTPS
             ai.home.arpa
             caddy workload
                   |
            caddy_litellm network
                   |
                litellm
```

There is no central ACME issuer, certificate distribution service, or shared
private-key store. Each Caddy instance manages certificates only for the
services on its host. The update gateway is a centralized DNS authorization
service; it does not request, receive, store, or distribute client
certificates or their private keys.

## Domain names and DNS

The public domain is `srjhome.net` and Cloudflare is authoritative for its
DNS zone.

Every HTTPS service has a flat public DNS name under `srjhome.net`:

```text
service.srjhome.net
```

The LiteLLM service uses `litellm.srjhome.net`.

For each service, the update gateway reconciles two public records: a
permanent CNAME from the ACME challenge name to a unique validation name,
and an unproxied A record mapping the service name to the private address
of its host:

```text
_acme-challenge.litellm.srjhome.net
  CNAME <unique-id>.acme.srjhome.net
litellm.srjhome.net
  A <private address of the service host>
```

The update gateway creates and replaces TXT records only at these unique
validation names. Let's Encrypt follows the CNAME when validating the
challenge.

The public A record is the only name-to-address mapping for the service;
there is no split-horizon DNS. Internal clients and Tailscale clients
resolve the name through ordinary public DNS, and tailnet traffic reaches
the address through the subnet routes advertised by `tailgate`. Resolvers
that filter private addresses from public answers (DNS rebind protection)
need a one-time whitelist for `srjhome.net`. The service does not require
internet-reachable ports.

## Host ingress

Caddy runs as the `workloads/caddy/` Compose project, deployed with
`make apply workload=caddy`. The Caddyfile is baked into the image: build
contexts cross SSH Docker contexts, bind mounts of control-machine paths do
not. The Caddyfile renders a single site from `SITE_HOSTNAME` and
`SITE_UPSTREAM`; serving additional HTTPS services on the same host requires
extending the Caddyfile and its environment accordingly.

Caddy is the only web container that publishes a host port:

```yaml
ports:
  - "443:443"
```

Port 80 is not published. Plain HTTP is unavailable and is not redirected to
HTTPS. DNS-01 validation does not need port 80.

Application containers do not publish ports with Docker `ports:`. They are
reachable only through their private Docker networks and Caddy. Host-local
diagnostics use container logs, `docker exec`, or a request through Caddy, for
example:

```sh
curl --resolve litellm.srjhome.net:443:127.0.0.1 https://litellm.srjhome.net/
```

## Docker network isolation

Each proxied service receives a dedicated Docker network. Only Caddy and that
service join this network:

```text
Caddy <-> caddy_litellm <-> litellm
```

An application with internal dependencies uses a separate application network:

```text
Caddy -- caddy_litellm -- litellm -- default -- postgres
```

Caddy does not join the application network; the database does not join
`caddy_litellm`. This prevents proxied services from receiving implicit
network access to each other or to their peers' databases.

Each `caddy_<service>` network is declared `external` in both Compose
projects and is created once on the target host:

```sh
docker --context <host> network create caddy_<service>
```

## Caddy configuration

The Caddy image is built with xcaddy and has a pinned, compatible Caddy
version (2.11.4) and `caddy-dns/acmedns` module version (v0.7.0). Its DNS
provider points to the update gateway over HTTPS on the internal network
(`https://acme.srjhome.net`). Caddy configuration contains no literal
credentials; secret values are supplied through a root-readable runtime file
mounted as a Compose secret.

The client credentials live in `workloads/caddy/secrets/caddy-acmedns.json`.
The plaintext file is gitignored (mode `0600`); its ASCII-armored age
counterpart is tracked. `make apply` provisions it to
`/opt/caddy/secrets/caddy-acmedns.json` on the target host through the
`copy_files` mechanism (root-owned, directories `0700`, files `0400`) and
points Compose at it with `COPY_DIR`.

Caddy persists its configuration and ACME state in the `caddy_data` Docker
volume. The ACME state includes certificates, private keys, and ACME account
data. It is not stored in Git or synchronized between hosts. Any backup of
this state is encrypted.

## DNS update authorization

No managed client host has a Cloudflare API token. Gateway client identities
live in the gateway's `secrets/gateway.json` (gitignored plaintext,
age-encrypted counterpart tracked, provisioned to `/opt/acme-dns-gateway/`):

```json
{
  "hostname": "litellm.srjhome.net",
  "username": "...",
  "password": "...",
  "subdomain": "<unique-id>"
}
```

Each hostname has a distinct client identity and validation name. A client
may update only the validation name associated with its identity. Reusing one
identity or validation name across hostnames is not permitted.

The gateway is compatible with the acme-dns provider API; it does not
implement the ACME protocol and is not an authoritative DNS server. ACME
clients use the normal DNS-01 flow and communicate with the gateway through
an acme-dns provider or hook. Caddy uses `caddy-dns/acmedns`. Other ACME
clients can use the same gateway when they provide a compatible acme-dns
integration.

Client identities are provisioned through the administrative deployment path.
The client API does not implement `/register`, and clients are always
configured with a pre-provisioned username, password, and validation
subdomain. This prevents an untrusted caller from allocating writable
validation names.

The gateway implements the acme-dns `/update` request contract used by
Caddy:

- accept authenticated `POST` requests only (`X-Api-User`/`X-Api-Key`);
- map the supplied client identity to one exact validation name;
- reject a request whose subdomain does not match that mapping;
- accept only bounded DNS-01 TXT values;
- rate-limit authentication failures and update attempts;
- omit credentials and challenge values from logs;
- fail closed when authentication, configuration, or the Cloudflare API is
  unavailable.

A successful update returns the acme-dns response shape expected by
compatible clients. Record cleanup follows acme-dns semantics: subsequent
challenges replace or rotate the bounded TXT values associated with the
validation name.

The client identities and their validation-name mappings are stored in the
gateway's root-readable secret configuration and are absent from source
control. The gateway stores the Cloudflare API token in the same security
boundary. That token has `Zone / DNS / Edit` for `srjhome.net`. The gateway
uses a configured Cloudflare Zone ID and does not require permission to
enumerate zones. Cloudflare does not support restricting a DNS write token to
individual record names. The token is never distributed to client hosts.

The gateway cannot change the authorization mapping represented by the
challenge CNAME records through its client API; the mapping is driven only
by the administrative configuration (see DNS record reconciliation below).

Updating provisioned secret contents (gateway clients, Caddy client
credentials) is a two-step operation: `make apply` copies the new files to
the host, and the affected container must then be restarted, because Compose
does not recreate containers when only secret file contents change. For the
gateway:

```sh
make apply workload=acme-dns-gateway
docker --context acme restart acme-dns-gateway
```

## DNS record reconciliation

The gateway converges the public records of every configured client at
startup and every `reconcile_interval_seconds`:

- the `_acme-challenge.<hostname>` CNAME targeting the client's validation
  name — the DNS-01 authorization mapping;
- the `<hostname>` A record with the client entry's `address` — the
  private IPv4 of the service host, always unproxied with a 60-second TTL.

Each client entry carries an `address`; only RFC1918 addresses are
accepted. The gateway creates or corrects a record only when it carries
the gateway's ownership comment, and it never deletes records. A name
occupied by a record without the ownership comment is left untouched and
logged; a conflicting manually created record is removed once with
administrative DNS access so the gateway can recreate it under its
ownership. Removing a client stops reconciliation for its names; leftover
records are removed manually.

Reconciliation is driven only by the administrative configuration, so the
client API cannot move the authorization mapping represented by a
challenge CNAME. A failed pass is logged and retried on the next interval;
it does not interrupt the update API, and renewals for already-issued
clients continue while Cloudflare is unreachable.

## Private gateway

The gateway runs on `acme.home.arpa`, a dedicated NanoPi Zero2. Its complete
implementation is one Compose project with two services:

```text
acme.home.arpa
  -> Caddy
       -> binds port 443 on the private host address
       -> manages the gateway TLS certificate
       -> reverse proxies POST /update over a private Docker network
       -> responds 404 to everything else
  -> DNS update gateway
       -> has no published host ports
       -> authenticates clients and enforces validation-name ACLs
       -> writes assigned TXT records through the Cloudflare API
```

The gateway backend and Caddy share one internal Docker network. The backend
does not publish a port to the host. Caddy is the only container that accepts
client connections and exposes HTTPS on the private network. Client
authorization rests on per-client credentials and validation-name ACLs rather
than network-level filtering.

The Compose project mounts root-readable configuration and secret files into
the containers, provisioned through `copy_files` from age-encrypted secret
storage. Docker Compose secret mounts do not provide encrypted storage; the
source files remain outside source control.

The server requires outbound HTTPS access to Cloudflare and the ACME CA, plus
ordinary DNS resolution. It does not run an authoritative DNS service and
does not require a public IP address, public A or AAAA records, inbound
Internet connectivity, or TCP/UDP port 53.

The gateway backend is available through a host-local HTTP endpoint that is
not reachable from the internal network. The gateway Caddy obtains and renews
the gateway's own certificate for `acme.srjhome.net` by reaching the backend
directly through the private container network
(`"server_url": "http://gateway:8080"` in its acme-dns client file). Initial
issuance does not depend on the gateway already having a valid HTTPS
certificate:

```text
gateway Caddy
  -> internal HTTP /update
  -> gateway writes its assigned TXT record through the Cloudflare API
  -> Let's Encrypt validates the public TXT record
  -> Caddy serves the gateway HTTPS endpoint
```

The gateway has its own client identity and unique validation name.
Reconciliation publishes its challenge CNAME and an unproxied A record
mapping `acme.srjhome.net` to the private address of `acme.home.arpa`, so
client hosts reach the gateway through ordinary public DNS:

```text
_acme-challenge.acme.srjhome.net
  CNAME <gateway-unique-id>.acme.srjhome.net
acme.srjhome.net
  A <private address of acme.home.arpa>
```

The local update path remains available for renewal even when the
gateway's client-facing certificate is expired, preventing a certificate
bootstrap deadlock.

## Certificate lifecycle

```text
Caddy starts
  -> reads configured hostnames
  -> sends a DNS-01 value and its scoped credentials to the gateway
  -> gateway validates the identity and target validation name
  -> gateway writes the assigned TXT record through the Cloudflare API
  -> Let's Encrypt validates the TXT record
  -> Caddy stores the certificate and private key in persistent storage
  -> Caddy serves TLS on port 443
  -> Caddy renews certificates automatically
```

Certbot, systemd renewal timers, cron jobs, certificate-copy jobs, and deploy
hooks are not part of this design. Caddy reloads its configuration when route
configuration changes; certificate management remains internal to Caddy.

Initial validation uses the Let's Encrypt staging CA (`ACME_CA` in the
workload's `.env`). Production issuance follows successful DNS-01 and routing
validation.

## Service onboarding

Adding an HTTPS service to a host requires these coordinated changes:

1. Deploy the application without published host ports.
2. Create the service-specific Caddy Docker network on the host:
   `docker --context <host> network create caddy_<service>`.
3. Join the application to that network as `external` in its Compose project;
   join the caddy project to the same network.
4. Generate a unique client identity (username, password, validation
   subdomain UUID) and add it with the service host's private `address` to
   the gateway's `secrets/gateway.json`; re-encrypt the tracked `.age`
   file, `make apply workload=acme-dns-gateway`, and restart the gateway
   container. Reconciliation then creates the service's challenge CNAME
   and A record in Cloudflare.
5. Store the client credentials as
   `workloads/caddy/secrets/caddy-acmedns.json` with
   `"server_url": "https://acme.srjhome.net"` and re-encrypt the tracked
   `.age` counterpart.
6. Set `SITE_HOSTNAME` and `SITE_UPSTREAM` in `workloads/caddy/.env` and
   start with the Let's Encrypt staging CA.
7. Apply both workloads and verify staging issuance; switch `ACME_CA` to the
   production directory, re-apply the caddy workload, and verify the service
   through its public name.

## Security boundary

For web workloads, port 443 is the only host-published application port. SSH,
Tailscale, and other host services remain governed by their own
configuration.

Publishing service names with private addresses discloses internal
addressing to anyone who can query the public zone. The records grant no
reachability on their own: services answer only on the LAN and on
Tailscale-approved subnet routes, and port 443 remains the only published
application port.

Compromise of Caddy on a host exposes its TLS private keys and gateway client
credentials. Those credentials permit certificate issuance only for hostnames
whose permanent challenge CNAME records target the validation names assigned
to that host. They cannot modify ordinary DNS records or challenge records
assigned to another host.

Compromise of the gateway or its Cloudflare API token exposes DNS write
access to the entire `srjhome.net` zone and permits certificate issuance for
any name in that zone. The gateway is therefore a security-critical
centralized policy boundary. Its source and deployed configuration are kept
minimal, auditable, and fail-closed.

Gateway unavailability prevents new client certificate issuance and renewal
but does not interrupt HTTPS served with unexpired certificates already
stored by Caddy. Cloudflare remains authoritative for all public DNS; no
public VM, delegated authoritative DNS server, or inbound port 53 is
required.
