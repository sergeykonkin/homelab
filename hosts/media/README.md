# media

A FriendlyElec **NanoPi R6S** (arm64, 64 GB eMMC) running Debian Trixie
from eMMC. Ansible provides firstboot and Docker CE with the Compose plugin.
No application containers are configured.

## Manual prerequisites

1. **Install Debian Trixie onto eMMC using an SD card.** Follow FriendlyElec's
   [R6S eMMC installation guide](https://wiki.friendlyelec.com/wiki/index.php/NanoPi_R6S#Install_OS_to_eMMC).
   Download an R6S Debian Trixie arm64 eFlasher image from the official
   `01_Official images/02_SD-to-eMMC images` directory and write it to a
   microSD card. Boot the board from that card and let eFlasher install Debian Trixie
   to eMMC. Flashing overwrites the eMMC contents. Wait for completion, then
   remove the SD card and boot from eMMC before running Ansible.
2. **Connect Ethernet and find the DHCP IP** in the router's lease table.
   Confirm Debian Trixie boots with the SD card removed and that SSH login as `pi`
   with password `pi` and sudo access work. The shared firstboot role requires
   this account, `/usr/bin/python3`, and OpenSSL on the image.
3. **Prepare the control machine** using the
   [root README](../../README.md#bootstrap-one-time-on-a-new-machine), including
   the vault password and private key matching `firstboot_pubkey` in
   `roles/firstboot/vars/main.yml`. The encrypted vault contains independent
   random root and `pi` passwords. To edit them:
   ```bash
   cd hosts/media
   ansible-vault edit secrets/vault.yml
   ```
4. **Configure a DHCP reservation and DNS** so `media.home.arpa` resolves to the
   board's address for repeatable setup. Ansible sets the OS hostname to
   `media`; it does not configure the router or DNS.

## First boot (run once)

Run against the fresh Debian Trixie installation on eMMC:

```bash
cd hosts/media
ansible-playbook firstboot.yml -e ansible_host=<DHCP-IP>
```

The shared `firstboot` role connects as `pi:pi`, uses sudo, sets the root and
`pi` passwords from the vault, installs the root SSH public key, sets hostname
`media`, upgrades packages, installs `nano` and `curl`, configures RAM-backed
journald, hardens SSH to key-only authentication, and reboots. The reboot check
reconnects as root using the configured key at the supplied DHCP IP.

## Docker setup (repeatable)

```bash
cd hosts/media
ansible-playbook site.yml
```

The shared `docker` role installs Docker CE, Buildx, and the Compose plugin,
configures the `fuse-overlayfs` storage driver, and enables and starts Docker.
The driver is the shared role's default and supports the overlay-root layout
used by FriendlyElec images; eMMC describes the storage medium, not the root
filesystem type. This playbook does not require host secrets.

If DNS is not configured, supply `-e ansible_host=<DHCP-IP>` to `site.yml`.

## Vault

`secrets/vault.yml` contains `vault_root_pw` and `vault_pi_pw`. These passwords
provide console access after SSH hardening. Keep the encrypted vault for
reprovisioning; use `ansible-vault edit` to change its values. If it is missing,
run `ansible-vault create secrets/vault.yml` from this directory and populate
the schema in `secrets/vault.yml.example` with strong passwords.

## Files

- `ansible.cfg`, `inventory.ini` — host configuration for `media.home.arpa`
- `firstboot.yml` — one-time bootstrap using `roles/firstboot`
- `site.yml` — repeatable setup using `roles/docker`
- `roles/` — directory for host-specific roles
- `secrets/vault.yml` — encrypted root and `pi` passwords
- `secrets/vault.yml.example` — plaintext schema with placeholders
