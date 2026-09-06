# tailgate

FriendlyElec NanoPi Zero2 Tailscale subnet router.

Run these commands from the repository root:

```sh
make init

make ansible host=tailgate playbook=bootstrap

make ansible host=tailgate playbook=site
```
