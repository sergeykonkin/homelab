#!/usr/bin/env python3
"""Private acme-dns-compatible Cloudflare TXT update gateway."""

from __future__ import annotations

import collections
import dataclasses
import datetime
import hashlib
import hmac
import http.server
import ipaddress
import json
import logging
import os
import re
import signal
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


LOG = logging.getLogger("acme_dns_gateway")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
SUBDOMAIN_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
TXT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
ZONE_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
MANAGED_COMMENT = "managed by acme-dns-gateway"
MAX_BODY_BYTES = 1024
MAX_HEADER_BYTES = 256


class ConfigurationError(ValueError):
    """The secret gateway configuration is invalid."""


class CloudflareError(RuntimeError):
    """A Cloudflare operation failed or returned an unsafe state."""


@dataclasses.dataclass(frozen=True)
class Client:
    hostname: str
    username: str
    password: str
    subdomain: str
    record_name: str


@dataclasses.dataclass(frozen=True)
class Config:
    zone_id: str
    zone_name: str
    validation_zone: str
    cloudflare_api_token: str
    cloudflare_timeout_seconds: int
    auth_failures_per_minute: int
    updates_per_minute: int
    clients: dict[str, Client]

    @classmethod
    def load(cls, path: str) -> "Config":
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigurationError("configuration cannot be loaded") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: object) -> "Config":
        if not isinstance(raw, dict):
            raise ConfigurationError("configuration must be an object")
        required = {
            "zone_id",
            "zone_name",
            "validation_zone",
            "cloudflare_api_token",
            "cloudflare_timeout_seconds",
            "auth_failures_per_minute",
            "updates_per_minute",
            "clients",
        }
        if set(raw) != required:
            raise ConfigurationError("configuration keys are invalid")

        zone_id = require_string(raw["zone_id"], "zone_id")
        zone_name = require_dns_name(raw["zone_name"], "zone_name")
        validation_zone = require_dns_name(raw["validation_zone"], "validation_zone")
        token = require_string(raw["cloudflare_api_token"], "cloudflare_api_token")
        timeout = require_int(raw["cloudflare_timeout_seconds"], 1, 30)
        auth_limit = require_int(raw["auth_failures_per_minute"], 1, 100)
        update_limit = require_int(raw["updates_per_minute"], 1, 100)

        if not ZONE_ID_PATTERN.fullmatch(zone_id):
            raise ConfigurationError("zone_id is invalid")
        if len(token) < 20 or len(token) > 256:
            raise ConfigurationError("cloudflare_api_token is invalid")
        if validation_zone != f"acme.{zone_name}":
            raise ConfigurationError("validation_zone must be below zone_name")
        if not isinstance(raw["clients"], list) or not raw["clients"]:
            raise ConfigurationError("clients must be a non-empty array")

        clients: dict[str, Client] = {}
        subdomains: set[str] = set()
        hostnames: set[str] = set()
        passwords: set[str] = set()
        for item in raw["clients"]:
            client = parse_client(item, zone_name, validation_zone)
            if client.username in clients:
                raise ConfigurationError("client usernames must be unique")
            if client.subdomain in subdomains:
                raise ConfigurationError("client subdomains must be unique")
            if client.hostname in hostnames:
                raise ConfigurationError("client hostnames must be unique")
            password_fingerprint = hashlib.sha256(client.password.encode()).hexdigest()
            if password_fingerprint in passwords:
                raise ConfigurationError("client passwords must be unique")
            clients[client.username] = client
            subdomains.add(client.subdomain)
            hostnames.add(client.hostname)
            passwords.add(password_fingerprint)

        return cls(
            zone_id=zone_id,
            zone_name=zone_name,
            validation_zone=validation_zone,
            cloudflare_api_token=token,
            cloudflare_timeout_seconds=timeout,
            auth_failures_per_minute=auth_limit,
            updates_per_minute=update_limit,
            clients=clients,
        )


def require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{field} must be a non-empty string")
    return value


def require_dns_name(value: object, field: str) -> str:
    name = require_string(value, field).rstrip(".").lower()
    if len(name) > 253 or any(not SUBDOMAIN_PATTERN.fullmatch(label) for label in name.split(".")):
        raise ConfigurationError(f"{field} is not a valid DNS name")
    return name


def require_int(value: object, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ConfigurationError("numeric configuration value is out of range")
    return value


def parse_client(raw: object, zone_name: str, validation_zone: str) -> Client:
    if not isinstance(raw, dict) or set(raw) != {"hostname", "username", "password", "subdomain"}:
        raise ConfigurationError("client entry is invalid")
    hostname = require_dns_name(raw["hostname"], "hostname")
    username = require_string(raw["username"], "username")
    password = require_string(raw["password"], "password")
    subdomain = require_string(raw["subdomain"], "subdomain").lower()
    if not hostname.endswith(f".{zone_name}"):
        raise ConfigurationError("client hostname is outside zone_name")
    if not USERNAME_PATTERN.fullmatch(username):
        raise ConfigurationError("client username is invalid")
    if len(password) < 32 or len(password) > 256:
        raise ConfigurationError("client password is invalid")
    if not SUBDOMAIN_PATTERN.fullmatch(subdomain):
        raise ConfigurationError("client subdomain is invalid")
    return Client(hostname, username, password, subdomain, f"{subdomain}.{validation_zone}")


class SlidingWindowLimiter:
    """Thread-safe in-memory limiter for bounded one-minute windows."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.clock = clock
        self.events: dict[str, collections.deque[float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        current = self.clock()
        cutoff = current - 60
        with self.lock:
            events = self.events.setdefault(key, collections.deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(current)
            return True


class CloudflareUpdater:
    """Maintain the two-value rolling TXT set used by acme-dns clients."""

    def __init__(self, config: Config, opener: Callable = urllib.request.urlopen):
        self.config = config
        self.opener = opener
        self.locks = collections.defaultdict(threading.Lock)
        self.api_base = f"https://api.cloudflare.com/client/v4/zones/{config.zone_id}/dns_records"

    def update(self, client: Client, txt: str) -> None:
        with self.locks[client.record_name]:
            records = self._list_records(client.record_name)
            if any(record.get("content") == txt for record in records):
                return
            if len(records) < 2:
                self._request("POST", self.api_base, self._record_body(client.record_name, txt))
                return
            oldest = min(records, key=lambda record: parse_cloudflare_time(record.get("modified_on")))
            record_id = oldest.get("id")
            if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", record_id):
                raise CloudflareError("record identifier is invalid")
            self._request(
                "PUT",
                f"{self.api_base}/{record_id}",
                self._record_body(client.record_name, txt),
            )

    def _list_records(self, record_name: str) -> list[dict]:
        query = urllib.parse.urlencode(
            {"type": "TXT", "name.exact": record_name, "match": "all", "per_page": "100"}
        )
        payload = self._request("GET", f"{self.api_base}?{query}")
        result = payload.get("result")
        if not isinstance(result, list):
            raise CloudflareError("record list is invalid")
        records = []
        for record in result:
            if not isinstance(record, dict):
                raise CloudflareError("record entry is invalid")
            if record.get("type") != "TXT" or canonical_dns_name(record.get("name")) != record_name:
                raise CloudflareError("record query returned an unexpected record")
            if record.get("comment") != MANAGED_COMMENT:
                raise CloudflareError("validation name contains an unmanaged TXT record")
            records.append(record)
        if len(records) > 2:
            raise CloudflareError("validation name contains too many TXT records")
        return records

    @staticmethod
    def _record_body(record_name: str, txt: str) -> dict:
        return {
            "type": "TXT",
            "name": record_name,
            "content": txt,
            "ttl": 60,
            "comment": MANAGED_COMMENT,
        }

    def _request(self, method: str, url: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.config.cloudflare_api_token}",
                "Content-Type": "application/json",
                "User-Agent": "acme-dns-gateway/1",
            },
        )
        try:
            with self.opener(request, timeout=self.config.cloudflare_timeout_seconds) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CloudflareError("Cloudflare API is unavailable") from exc
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise CloudflareError("Cloudflare API rejected the update")
        return payload


def canonical_dns_name(value: object) -> str:
    return value.rstrip(".").lower() if isinstance(value, str) else ""


def parse_cloudflare_time(value: object) -> datetime.datetime:
    if not isinstance(value, str):
        raise CloudflareError("record modification time is invalid")
    try:
        timestamp = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CloudflareError("record modification time is invalid") from exc
    if timestamp.tzinfo is None:
        raise CloudflareError("record modification time is invalid")
    return timestamp


class GatewayState:
    def __init__(self, config: Config, updater: CloudflareUpdater):
        self.config = config
        self.updater = updater
        self.auth_failures = SlidingWindowLimiter(config.auth_failures_per_minute)
        self.updates = SlidingWindowLimiter(config.updates_per_minute)

    def authenticate(self, source: str, username: str, password: str) -> Client | None:
        client = self.config.clients.get(username)
        expected_password = client.password if client is not None else "\0" * 32
        valid = hmac.compare_digest(expected_password, password) and client is not None
        if valid:
            return client
        if not self.auth_failures.allow(source):
            raise RateLimited
        return None


class RateLimited(Exception):
    pass


class GatewayHandler(http.server.BaseHTTPRequestHandler):
    server_version = "acme-dns-gateway"
    sys_version = ""

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/update":
            self.send_json(404, {"error": "not found"})
            return
        source = self.client_source()
        username = self.headers.get("X-Api-User", "")
        password = self.headers.get("X-Api-Key", "")
        if len(username) > MAX_HEADER_BYTES or len(password) > MAX_HEADER_BYTES:
            username = ""
            password = ""
        try:
            client = self.server.state.authenticate(source, username, password)
        except RateLimited:
            self.send_json(429, {"error": "rate limited"})
            return
        if client is None:
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            payload = self.read_payload()
        except ValueError:
            self.send_json(400, {"error": "invalid request"})
            return
        if set(payload) != {"subdomain", "txt"}:
            self.send_json(400, {"error": "invalid request"})
            return
        subdomain = payload.get("subdomain")
        txt = payload.get("txt")
        if not isinstance(subdomain, str) or not hmac.compare_digest(subdomain, client.subdomain):
            self.send_json(403, {"error": "forbidden"})
            return
        if not isinstance(txt, str) or not TXT_PATTERN.fullmatch(txt):
            self.send_json(400, {"error": "invalid request"})
            return
        if not self.server.state.updates.allow(f"{source}:{client.username}"):
            self.send_json(429, {"error": "rate limited"})
            return
        try:
            self.server.state.updater.update(client, txt)
        except CloudflareError:
            LOG.error("Cloudflare update failed for source %s", source)
            self.send_json(502, {"error": "DNS update failed"})
            return
        LOG.info("DNS challenge updated for source %s", source)
        self.send_json(200, {"txt": txt})

    def read_payload(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as exc:
            raise ValueError from exc
        if length < 2 or length > MAX_BODY_BYTES:
            raise ValueError
        try:
            payload = json.loads(self.rfile.read(length))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError from exc
        if not isinstance(payload, dict):
            raise ValueError
        return payload

    def client_source(self) -> str:
        # The gateway terminates a single hop from Caddy over a private
        # network; no trusted upstream adds X-Forwarded-For, so a client
        # could forge it to spread its rate-limiter key. Use the socket
        # peer address directly.
        return str(ipaddress.ip_address(self.client_address[0]))

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


class GatewayServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: GatewayState):
        super().__init__(address, GatewayHandler)
        self.state = state

    def server_bind(self) -> None:
        # HTTPServer performs a reverse-DNS lookup here by default. The gateway
        # does not need it and readiness must not depend on resolver health.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = Config.load(os.environ.get("GATEWAY_CONFIG", "/run/secrets/gateway.json"))
        host = os.environ.get("GATEWAY_LISTEN_HOST", "0.0.0.0")
        port = int(os.environ.get("GATEWAY_LISTEN_PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError
    except (ConfigurationError, ValueError):
        LOG.critical("gateway configuration is invalid")
        raise SystemExit(1)

    state = GatewayState(config, CloudflareUpdater(config))
    server = GatewayServer((host, port), state)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: threading.Thread(target=server.shutdown).start())
    LOG.info("gateway listening on port %d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
