# Personal YouTube Downloader workload

The personal YouTube Downloader runs on `media.home.arpa`. Its application
port is private to Docker: a named Cloudflare Tunnel publishes the route
configured in Cloudflare and forwards it to the application over the `tunnel`
network. The downloader has a separate outbound network for YouTube and media
downloads.

`cloudflared` has the fixed `172.30.57.2` address on the tunnel network. The
application trusts only this proxy address when using `X-Forwarded-For` for
login rate limiting.

The Cloudflare sidecar serves its metrics endpoint on loopback port 2000. Its
container healthcheck uses the endpoint's `/ready` state, which requires an
active Cloudflare Tunnel connection.

Run all commands below from the repository root. To configure credentials,
create the ignored `.env` file and encrypt it:

```sh
cp hosts/media/workloads/personal-yt-downloader/.env.example hosts/media/workloads/personal-yt-downloader/.env
# Set PASSWORD and TUNNEL_TOKEN in hosts/media/workloads/personal-yt-downloader/.env.
make encrypt-secrets
```

Prepare the local environment and deploy:

```sh
make init
make deploy host=media workload=personal-yt-downloader
```

The tunnel token belongs to a Cloudflare named tunnel whose public hostname
route targets `http://172.30.57.3:8080` from the `cloudflared` container.
