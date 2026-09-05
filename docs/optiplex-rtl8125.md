# optiplex — second NIC (RTL8125 2.5GbE) setup

`optiplex.home.arpa` (`10.4.1.10`) is a Dell x86_64 running Ubuntu
`5.15.0-176-generic`. It is **not** one of the Ansible-managed NanoPi hosts in
this repo; this file records manual configuration done directly on the box.

## Hardware

| Interface | PCI | Controller | Driver | State |
|-----------|-----|------------|--------|-------|
| `enp0s31f6` | `00:1f.6` | Intel I219-LM (onboard) | `e1000e` | DOWN, no carrier |
| `enp3s0` | `03:00.0` | Realtek RTL8125 2.5GbE (added) | `r8125` 9.016.01 | UP, 2.5 Gbps, `10.4.1.10` (DHCP) |

`enp3s0` is the active interface on the network; the onboard `enp0s31f6` is
unplugged. The RTL8125 needs Realtek's out-of-tree `r8125` driver for reliable
2.5G; the in-kernel `r8169` also claims the device and must be kept off it.

## 1. Driver via DKMS

Installed from `https://github.com/awesometic/realtek-r8125-dkms` (cloned to
`/root/realtek-r8125-dkms`, version `9.016.01`). The repo's `dkms-install.sh`
copies the source to `/usr/src/r8125-9.016.01` and runs
`dkms add`/`build`/`install` for the running kernel.

- `dkms.conf` sets `AUTOINSTALL="yes"`, so the module rebuilds automatically on
  kernel upgrade. It is currently built for both installed kernels
  (`5.15.0-176-generic` and `5.15.0-191-generic`; `dkms status` confirms).
- The built module lands at `/lib/modules/<kver>/updates/dkms/r8125.ko`. The
  `/updates` path gives it priority over the in-tree `r8169`, and DKMS owns it —
  do not hand-edit files under `/usr/src/r8125-*` or `/var/lib/dkms/r8125`.

To reinstall after a kernel change or a DKMS breakage, from
`/root/realtek-r8125-dkms`:

```sh
./dkms-remove.sh   # optional, clears the old DKMS tree first
./dkms-install.sh
```

## 2. Blacklist the in-kernel driver

`/etc/modprobe.d/blacklist-r8169.conf`:

```
blacklist r8169
```

Without this, `r8169` binds to the RTL8125 at boot and the link is unstable or
capped below 2.5G. The blacklist plus the `/updates` module path together
guarantee `r8125` is the driver in use — verify with `lspci -k` (kernel driver
in use: `r8125`) and `modinfo r8125`.

## 3. Post-boot link fix

On some reboots the `r8125` carrier does not come up on its own (the interface
is `UP` in `ip link` but `carrier` reads `0`). A oneshot systemd unit bounces
the link when that happens.

`/etc/systemd/system/r8125-linkfix.service` (enabled, `WantedBy=multi-user.target`):

```ini
[Unit]
Description=Fix RTL8125 link after boot (carrier-based)
After=systemd-udev-settle.service
Wants=systemd-udev-settle.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/r8125-linkfix

[Install]
WantedBy=multi-user.target
```

`/usr/local/sbin/r8125-linkfix` (mode `0755`):

```sh
#!/bin/sh
set -eu

IFACE="enp3s0"
MAX_WAIT=30   # seconds
t=0

# Wait for the interface to appear
while [ ! -e "/sys/class/net/$IFACE" ] && [ $t -lt $MAX_WAIT ]; do
  sleep 1
  t=$((t+1))
done

[ -e "/sys/class/net/$IFACE" ] || exit 0

# If carrier is already up, do nothing
if [ -r "/sys/class/net/$IFACE/carrier" ] && [ "$(cat /sys/class/net/$IFACE/carrier)" = "1" ]; then
  exit 0
fi

# Bounce the link
ip link set "$IFACE" down || true
sleep 1
ip link set "$IFACE" up || true

# Wait briefly for carrier to come up (optional)
t=0
while [ -r "/sys/class/net/$IFACE/carrier" ] && [ "$(cat /sys/class/net/$IFACE/carrier)" = "0" ] && [ $t -lt 10 ]; do
  sleep 1
  t=$((t+1))
done

exit 0
```

The service runs after `systemd-udev-settle`, so the NIC is already renamed
from `eth0` to `enp3s0` before it fires. It is a no-op on boots where carrier is
already up — check `journalctl -u r8125-linkfix.service` for its one-line
"Finished" entry.

## 4. Network config (systemd-networkd, not netplan)

Cloud-init's netplan at `/etc/netplan/50-cloud-init.yaml` only knows the
onboard NIC (written by the installer, never edited):

```yaml
network:
  ethernets:
    enp0s31f6:
      dhcp4: true
  version: 2
```

The second NIC is configured directly with systemd-networkd instead of editing
the cloud-init netplan (which warns it does not persist across cloud-init
runs). `systemd-networkd` is enabled.

`/etc/systemd/network/10-enp3s0.network`:

```ini
[Match]
Name=enp3s0

[Network]
DHCP=ipv4
IPv6AcceptRA=yes

[DHCPv4]
RouteMetric=50
```

`networkctl status enp3s0` shows `routable (configured)`, `online`, address
`10.4.1.10` from `10.4.1.1`. NetworkManager is not installed/active.

## Verification

```sh
ethtool enp3s0 | grep -E 'Speed|Link'        # Speed: 2500Mb/s, Link detected: yes
lspci -k -s 03:00.0 | grep -i driver         # Kernel driver in use: r8125
dkms status | grep r8125                     # built for each installed kernel
systemctl is-enabled r8125-linkfix systemd-networkd   # both enabled
ip -br addr show enp3s0                       # UP  10.4.1.10/24
dmesg | grep r8125 | tail                     # r8125 ... link up
```

## Reapplying after a reinstall

1. `cd /root/realtek-r8125-dkms && ./dkms-install.sh` (or `dkms autoinstall`
   after a kernel upgrade rebuilds it automatically).
2. Confirm `/etc/modprobe.d/blacklist-r8169.conf` still contains
   `blacklist r8169`.
3. Confirm `/etc/systemd/network/10-enp3s0.network`, the `r8125-linkfix.service`
   unit, and `/usr/local/sbin/r8125-linkfix` are present and that the unit is
   enabled.
4. Reboot; verify carrier and the linkfix journal entry.
