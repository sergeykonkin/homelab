# tailgate

Tailscale subnet router on a FriendlyElec **NanoPi Zero2** (arm64, Ubuntu 24.04).
Advertises the home VLANs `10.4.0.0/24` (management), `10.4.1.0/24` (trusted),
and `10.4.4.0/24` (isolated) to the `batareeed@` tailnet. Nothing else runs on
this host — no Docker, no cron, no custom services.

## Manual prerequisites (do these by hand)

1. **Burn the image.** Download the FriendlyElec NanoPi Zero2 Ubuntu 24.04
   (arm64) image and write it to a microSD card:
   ```bash
   # with rpi-imager, balenaEtcher, or dd:
   sudo dd if=nanopi-zero2-ubuntu.img of=/dev/rdiskN bs=4M conv=fsync
   ```
2. **Boot and find the DHCP IP.** Insert the card, power on. The host grabs an
   IP via DHCP on the management VLAN. Find it in the router's lease table or:
   ```bash
   nmap -sn 10.4.0.0/24   # or check the gateway's DHCP leases
   ```
3. **Prepare the vault** (one-time, per the repo root README):
   ```bash
   cd hosts/tailgate
   cp secrets/vault.yml.example secrets/vault.yml
   # edit secrets/vault.yml: set vault_root_pw, vault_pi_pw, vault_tailscale_authkey
   # mint the tailscale auth key at https://login.tailscale.com/settings/keys
   ansible-vault encrypt secrets/vault.yml
   ```

## First boot (run once)

```bash
cd hosts/tailgate
ansible-playbook firstboot.yml -e ansible_host=<DHCP-IP>
```

This connects with the default image credential (`root`:`pi`), sets strong
passwords (vault), drops your SSH key for root, installs `nano`+`curl`, sets
the hostname to `tailgate`, moves journald to RAM, runs `apt upgrade`, hardens
sshd to **key-only auth**, and reboots.

## Real setup (after firstboot, re-runnable)

Once the host answers `tailgate.home.arpa` (DNS served by the gateway):

```bash
cd hosts/tailgate
ansible-playbook site.yml
```

This installs Tailscale, enables IP forwarding, and runs
`tailscale up --advertise-routes=10.4.0.0/24,10.4.1.0/24,10.4.4.0/24 --auth-key=…`
with auto-update enabled.

### One-time, in the Tailscale admin console

After the first `site.yml` run, **approve the advertised routes** at
[login.tailscale.com/admin/machines](https://login.tailscale.com/admin/machines)
→ tailgate → Edit route settings → enable `10.4.0.0/24, 10.4.1.0/24, 10.4.4.0/24`.
The playbook advertises the routes; the admin console makes them usable by the
tailnet. (Tailscale requires this approval for any advertised route.)

## Reprovisioning (wiped SD card)

Burn a fresh image → `firstboot.yml` (with the new DHCP IP) → `site.yml`.
The reusable Tailscale auth key in the vault lets the node rejoin the tailnet
unattended. It may get a new `100.x` Tailscale IP unless you preserve state.

## Files

- `ansible.cfg` — per-host ansible config (roles_path, vault pass)
- `inventory.ini` — `tailgate` host + `base_hostname` var
- `firstboot.yml` — one-time bootstrap (uses `shared/firstboot`)
- `site.yml` — real setup (uses the colocated `tailscale` role)
- `roles/tailscale/` — Tailscale install + subnet-router bring-up
- `secrets/vault.yml` — encrypted: `vault_root_pw`, `vault_pi_pw`, `vault_tailscale_authkey`
