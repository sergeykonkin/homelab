# ai

FriendlyElec NanoPi Zero2 host for Docker and the LiteLLM workload.

Run these commands from the repository root:

```sh
make init

make ansible host=ai playbook=bootstrap

make ansible host=ai playbook=site

make apply workload=litellm
```
