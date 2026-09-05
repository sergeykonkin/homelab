# ai

A FriendlyElec **NanoPi Zero2** (arm64, Debian Trixie) running the
[LiteLLM](https://github.com/BerriAI/litellm) proxy app + PostgreSQL 16 + a
`models-updater` sidecar via Docker Compose, serving TLS on `:443`.
[`open-webui`](https://github.com/open-webui/open-webui) is planned as a second
app on this host.

`litellm` here is the **app**, not a host. The host is `ai` (OS hostname
`ai`, managed via `ai.home.arpa`). The LiteLLM app serves
`litellm.home.arpa` (a separate DNS alias pointing at this box) as its public
TLS endpoint — that's an app concern, handled by the `litellm` role's cert.

## Manual prerequisites (do these by hand)

1. **Burn the image.** FriendlyElec NanoPi Zero2 Debian Trixie (arm64) → SD card.
2. **Boot and find the DHCP IP** (router lease table or `nmap -sn 10.4.0.0/24`).
3. **Prepare the control machine** using the [root README](../../README.md#bootstrap-one-time-on-a-new-machine)
   (vault password and matching SSH private key). Reuse the committed encrypted
   vault; update it only if needed:
   ```bash
   cd hosts/ai
   ansible-vault edit secrets/vault.yml
   ```

## First boot (run once)

```bash
cd hosts/ai
ansible-playbook firstboot.yml -e ansible_host=<DHCP-IP>
```

Connects as `pi:pi` and escalates with sudo. Bootstrap sets strong passwords
(vault), installs the configured SSH key for root, adds `nano`+`curl`, sets
hostname `ai`, moves journald to RAM, runs `apt upgrade`, hardens sshd to key-only
auth, and reboots. The reboot check reconnects as root using the SSH key.

## Real setup (after firstboot, re-runnable)

```bash
cd hosts/ai
ansible-playbook site.yml
```

Installs Docker with **fuse-overlayfs** (the NanoPi root is itself overlayfs;
the default `overlay2` driver won't nest on it), deploys `/opt/litellm` (compose,
Dockerfile, scripts, `.env` from vault, certs), and runs
`docker compose up -d --build`.

## Populating the vault

The vault holds the host's firstboot passwords and the LiteLLM app's secrets
(API key, master key, postgres password, TLS cert + key). `secrets/vault.yml.example`
shows the shape and how to encrypt the file.

| Vault var | What it is |
|-----------|------------|
| `vault_root_pw`, `vault_pi_pw` | New strong values (your choice) |
| `vault_nebius_api_key` | Nebius API key (from the Nebius console) |
| `vault_litellm_master_key` | LiteLLM master key (your choice; used to auth proxy clients) |
| `vault_postgres_password` | PostgreSQL password (your choice) |
| `vault_tls_crt` | TLS certificate PEM for `litellm.home.arpa` (see below) |
| `vault_tls_key` | TLS private key PEM (the matching key) |

For an existing vault, use `ansible-vault edit secrets/vault.yml` from this host
directory. If the vault is missing, create it with the command below, paste the
schema from `secrets/vault.yml.example` into the editor, and fill in values from
their sources (see the table and TLS section). Saving encrypts the file:

```bash
cd hosts/ai
ansible-vault create secrets/vault.yml  # only when no vault exists
```

<details><summary>If an <code>ai</code> host is already running (adopting a hand-set-up host)</summary>

When bringing an already-running host under this repo's management, the
existing app values can be transferred from `/opt/litellm/.env` and
`/opt/litellm/certs/tls.{crt,key}` on `root@ai.home.arpa` into the vault editor.
Use `ansible-vault edit` for an existing vault or `ansible-vault create` for a
missing one; avoid printing credentials or the private key into logs or replies.

</details>

## TLS certificate

The TLS cert + key live in the vault as a pair (`vault_tls_crt`, `vault_tls_key`),
not as a committed cert file. The cert is a **mkcert** development certificate
(issuer `mkcert root@optiplex`), SAN `litellm.home.arpa`, valid to Dec 2028.
The cert is for the app's public endpoint (`litellm.home.arpa`), not the host
name — it does not change with the host name. The cert isn't a secret, but
keeping it with the key in the vault means the pair can't drift apart in the
tree and rotates together.

TODO: document cert renewal cadence and where the mkcert CA / root trust is
installed (clients must trust the mkcert root CA to avoid browser warnings).

## Files

- `ansible.cfg`, `inventory.ini` — per-host ansible config (host = `ai`)
- `firstboot.yml` — one-time bootstrap (uses `shared/firstboot`)
- `site.yml` — real setup: `shared/docker` + colocated `litellm` role
- `roles/litellm/tasks/main.yml` — deploy /opt/litellm + `docker compose up -d --build`
- `roles/litellm/templates/docker-compose.yml.j2` — the deploy compose (443:4000, TLS, db, models-updater)
- `roles/litellm/files/` — `Dockerfile.models-updater`, `update_config.py`, `entrypoint.sh`
- `secrets/vault.yml` — encrypted: root/pi pw, nebius key, litellm master key, postgres pw, tls crt + key

## How model sync works

1. `models-updater` runs `update_config.py` on startup and daily at 04:20 in the
   container's timezone; Compose does not configure a timezone override.
2. `update_config.py` fetches text-to-text models from the Nebius API, writes
   `config.yaml` into a shared docker volume.
3. A SHA-256 hash prevents unchanged config/hash writes. The startup update never
   restarts LiteLLM; Compose waits for the config file and healthy database
   before starting the proxy.
4. On scheduled updates, a change from an existing hash triggers
   `docker restart litellm`. An initial config with no previous hash does not.
   The socket is mounted with `:ro`, which allows Docker API mutations,
   including restarts; the sidecar has privileged access to the Docker daemon.

`config.yaml` is **generated at runtime**, not stored in this repo.
