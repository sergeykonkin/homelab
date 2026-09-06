# acme

FriendlyElec NanoPi Zero2 host for Docker and the ACME-DNS gateway workload.

Run these commands from the repository root:

```sh
make init

make bootstrap host=acme

make apply workload=acme-dns-gateway
```
