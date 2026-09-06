import contextlib
import http.client
import importlib.util
import io
import json
import pathlib
import sys
import threading
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "backend" / "gateway.py"
SPEC = importlib.util.spec_from_file_location("gateway", MODULE_PATH)
gateway = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gateway
SPEC.loader.exec_module(gateway)

TOKEN_A = "A" * 43
TOKEN_B = "B" * 43
TOKEN_C = "C" * 43


def config_dict():
    return {
        "zone_id": "a" * 32,
        "zone_name": "example.com",
        "validation_zone": "acme.example.com",
        "cloudflare_api_token": "token-" + "x" * 32,
        "cloudflare_timeout_seconds": 10,
        "auth_failures_per_minute": 2,
        "updates_per_minute": 2,
        "clients": [
            {
                "hostname": "acme.example.com",
                "username": "gateway-user-0001",
                "password": "p" * 32,
                "subdomain": "gateway-validation",
            }
        ],
    }


class FakeUpdater:
    def __init__(self):
        self.calls = []

    def update(self, client, txt):
        self.calls.append((client, txt))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ConfigTests(unittest.TestCase):
    def test_loads_exact_client_mapping(self):
        config = gateway.Config.from_dict(config_dict())
        client = config.clients["gateway-user-0001"]
        self.assertEqual(client.record_name, "gateway-validation.acme.example.com")

    def test_rejects_duplicate_validation_subdomains(self):
        raw = config_dict()
        duplicate = dict(raw["clients"][0])
        duplicate["hostname"] = "other.example.com"
        duplicate["username"] = "gateway-user-0002"
        duplicate["password"] = "q" * 32
        raw["clients"].append(duplicate)
        with self.assertRaises(gateway.ConfigurationError):
            gateway.Config.from_dict(raw)

    def test_rejects_hostname_outside_zone(self):
        raw = config_dict()
        raw["clients"][0]["hostname"] = "gateway.example.org"
        with self.assertRaises(gateway.ConfigurationError):
            gateway.Config.from_dict(raw)


class LimiterTests(unittest.TestCase):
    def test_sliding_window_expires_events(self):
        current = [100.0]
        limiter = gateway.SlidingWindowLimiter(2, lambda: current[0])
        self.assertTrue(limiter.allow("client"))
        self.assertTrue(limiter.allow("client"))
        self.assertFalse(limiter.allow("client"))
        current[0] = 161.0
        self.assertTrue(limiter.allow("client"))


class CloudflareUpdaterTests(unittest.TestCase):
    def make_updater(self, responses):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            response = responses.pop(0)
            return FakeResponse(json.dumps(response).encode())

        config = gateway.Config.from_dict(config_dict())
        return gateway.CloudflareUpdater(config, opener), requests, next(iter(config.clients.values()))

    def test_creates_first_txt_record(self):
        responses = [
            {"success": True, "result": []},
            {"success": True, "result": {"id": "1" * 32}},
        ]
        updater, requests, client = self.make_updater(responses)
        updater.update(client, TOKEN_A)
        self.assertEqual([item[0].method for item in requests], ["GET", "POST"])
        created = json.loads(requests[1][0].data)
        self.assertEqual(created["name"], client.record_name)
        self.assertEqual(created["content"], TOKEN_A)
        self.assertEqual(created["comment"], gateway.MANAGED_COMMENT)

    def test_rotates_oldest_of_two_records(self):
        records = [
            {
                "id": "1" * 32,
                "type": "TXT",
                "name": "gateway-validation.acme.example.com",
                "content": TOKEN_A,
                "comment": gateway.MANAGED_COMMENT,
                "modified_on": "2026-01-01T00:00:00Z",
            },
            {
                "id": "2" * 32,
                "type": "TXT",
                "name": "gateway-validation.acme.example.com",
                "content": TOKEN_B,
                "comment": gateway.MANAGED_COMMENT,
                "modified_on": "2026-02-01T00:00:00Z",
            },
        ]
        updater, requests, client = self.make_updater(
            [{"success": True, "result": records}, {"success": True, "result": {}}]
        )
        updater.update(client, TOKEN_C)
        self.assertEqual(requests[1][0].method, "PUT")
        self.assertTrue(requests[1][0].full_url.endswith("/" + "1" * 32))

    def test_skips_an_existing_value(self):
        record = {
            "id": "1" * 32,
            "type": "TXT",
            "name": "gateway-validation.acme.example.com",
            "content": TOKEN_A,
            "comment": gateway.MANAGED_COMMENT,
            "modified_on": "2026-01-01T00:00:00Z",
        }
        updater, requests, client = self.make_updater([{"success": True, "result": [record]}])
        updater.update(client, TOKEN_A)
        self.assertEqual(len(requests), 1)

    def test_fails_closed_for_unmanaged_record(self):
        record = {
            "id": "1" * 32,
            "type": "TXT",
            "name": "gateway-validation.acme.example.com",
            "content": TOKEN_A,
            "comment": None,
            "modified_on": "2026-01-01T00:00:00Z",
        }
        updater, _, client = self.make_updater([{"success": True, "result": [record]}])
        with self.assertRaises(gateway.CloudflareError):
            updater.update(client, TOKEN_B)


class HttpTests(unittest.TestCase):
    def setUp(self):
        config = gateway.Config.from_dict(config_dict())
        self.updater = FakeUpdater()
        self.server = gateway.GatewayServer(("127.0.0.1", 0), gateway.GatewayState(config, self.updater))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, payload=None, username="gateway-user-0001", password="p" * 32):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        headers = {"X-Api-User": username, "X-Api-Key": password}
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_valid_update_contract(self):
        status, payload = self.request(
            "POST", "/update", {"subdomain": "gateway-validation", "txt": TOKEN_A}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"txt": TOKEN_A})
        self.assertEqual(self.updater.calls[0][1], TOKEN_A)

    def test_rejects_wrong_subdomain(self):
        status, _ = self.request("POST", "/update", {"subdomain": "other", "txt": TOKEN_A})
        self.assertEqual(status, 403)
        self.assertEqual(self.updater.calls, [])

    def test_rejects_invalid_challenge_value(self):
        status, _ = self.request(
            "POST", "/update", {"subdomain": "gateway-validation", "txt": "not-a-challenge"}
        )
        self.assertEqual(status, 400)

    def test_rejects_registration(self):
        status, _ = self.request("POST", "/register", {})
        self.assertEqual(status, 404)

    def test_rate_limits_authentication_failures(self):
        self.assertEqual(self.request("POST", "/update", {}, password="wrong")[0], 401)
        self.assertEqual(self.request("POST", "/update", {}, password="wrong")[0], 401)
        self.assertEqual(self.request("POST", "/update", {}, password="wrong")[0], 429)

    def test_rate_limits_authenticated_update_attempts(self):
        payload = {"subdomain": "gateway-validation", "txt": TOKEN_A}
        self.assertEqual(self.request("POST", "/update", payload)[0], 200)
        self.assertEqual(self.request("POST", "/update", payload)[0], 200)
        self.assertEqual(self.request("POST", "/update", payload)[0], 429)

    def test_invalid_request_does_not_consume_update_slot(self):
        # Validation failures must not consume an update-limiter slot, so
        # two valid updates still both succeed (updates_per_minute is 2)
        # after a wrong subdomain and a malformed challenge value.
        self.assertEqual(
            self.request("POST", "/update", {"subdomain": "other", "txt": TOKEN_A})[0], 403
        )
        self.assertEqual(
            self.request(
                "POST", "/update", {"subdomain": "gateway-validation", "txt": "short"}
            )[0],
            400,
        )
        payload = {"subdomain": "gateway-validation", "txt": TOKEN_A}
        self.assertEqual(self.request("POST", "/update", payload)[0], 200)
        self.assertEqual(self.request("POST", "/update", payload)[0], 200)


if __name__ == "__main__":
    unittest.main()
