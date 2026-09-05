# tailgate

Tailscale subnet router on a FriendlyElec **NanoPi Zero2** (arm64, Ubuntu 24.04).
Advertises the home VLANs `10.4.0.0/24` (management), `10.4.1.0/24` (trusted),
and `10.4.4.0/24` (isolated) to the `batareeed@` tailnet. This repo deploys no
other application, Docker stack, or cron job on this host.

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
3. **Prepare the control machine** using the [root README](../../README.md#bootstrap-one-time-on-a-new-machine)
   (vault password and matching SSH private key). Reuse the committed encrypted
   vault; update it only if needed:
   ```bash
   cd hosts/tailgate
   ansible-vault edit secrets/vault.yml
   ```

   If the vault is missing, use `ansible-vault create secrets/vault.yml` instead
   and fill in the schema from `secrets/vault.yml.example` in the editor. It needs
   `vault_root_pw`, `vault_pi_pw`, and `vault_tailscale_authkey`; obtain the latter
   from [Tailscale keys](https://login.tailscale.com/settings/keys).

## First boot (run once)

```bash
cd hosts/tailgate
ansible-playbook firstboot.yml -e ansible_host=<DHCP-IP>
```

This connects with the default image credential (`pi`:`pi`), escalates with sudo,
sets strong passwords (vault), installs the configured SSH key for root, adds
`nano`+`curl`, sets the hostname to `tailgate`, moves journald to RAM, runs
`apt upgrade`, hardens
sshd to **key-only auth**, and reboots. The reboot check reconnects as root using
the SSH key.

## Real setup (after firstboot, re-runnable)

Once the host answers `tailgate.home.arpa` (DNS served by the gateway):

```bash
cd hosts/tailgate
ansible-playbook site.yml
```

This installs Tailscale, enables IP forwarding, and runs
`tailscale up --advertise-routes=10.4.0.0/24,10.4.1.0/24,10.4.4.0/24 --auth-key=…`
with `--reset=false`, `--accept-routes`, and auto-update enabled. The `tailscale up`
task always reports changed. Setting `tailscale_auto_update: false` skips the
enable task; it does not disable an already-enabled preference. The
`tailscale_exit_node` default is unused by the tasks.

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
