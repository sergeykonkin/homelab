# media

FriendlyElec NanoPi R6S host for Docker workloads.

Run these commands from the repository root:

```sh
make init

make bootstrap host=media
```

Docker workloads belong under `workloads/<name>/` and are applied with:

```sh
make apply host=media workload=<name>
```
