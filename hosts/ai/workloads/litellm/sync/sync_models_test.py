"""Pure unit tests for catalog mapping, reconciliation, and the sidecar loop."""

import copy
import io
import json
import threading
import unittest
import urllib.error
import urllib.parse
from unittest.mock import Mock, patch

import sync_models as sync


def source_model(model_id="vendor/chat", modality="text->text"):
    return {
        "id": model_id,
        "name": "Example model",
        "description": "Example description",
        "created": 123,
        "context_length": 8192,
        "architecture": {"modality": modality, "tokenizer": "Example", "instruct_type": None},
        "pricing": {
            "prompt": "0.000001", "completion": "0.000002", "request": "0",
            "input_cache_read": None,
        },
        "per_request_limits": {"prompt_tokens": 4096},
        "supported_features": ["tool_calling"],
        "supported_sampling_parameters": ["temperature", "top_p"],
    }


def database_model(model_id, name="vendor/chat", db_model=True):
    return {
        "model_name": name,
        "litellm_params": {"model": "other/provider", "api_key": "masked"},
        "model_info": {"id": model_id, "db_model": db_model},
    }


def model_page(records, page=1, total_count=None):
    count = len(records) if total_count is None else total_count
    return {
        "data": records, "total_count": count, "current_page": page,
        "total_pages": (count + 99) // 100, "size": 100,
    }


class CatalogTests(unittest.TestCase):
    def test_routing_and_metadata(self):
        record = source_model()
        definition = sync.model_definition(record)
        self.assertEqual(definition["model_name"], record["id"])
        self.assertEqual(definition["litellm_params"], {
            "model": "nebius/vendor/chat",
            "api_base": "https://api.tokenfactory.nebius.com/v1",
            "api_key": "os.environ/NEBIUS_API_KEY",
        })
        info = definition["model_info"]
        self.assertEqual(info["input_cost_per_token"], 0.000001)
        self.assertEqual(info["output_cost_per_token"], 0.000002)
        self.assertEqual(info["max_input_tokens"], 8192)
        self.assertEqual(info["tokenfactory"], record)
        record["supported_features"].append("another_feature")
        self.assertEqual(info["tokenfactory"]["supported_features"], ["tool_calling"])

    def test_every_modality_is_retained(self):
        cases = (
            ("text->text", "chat", False),
            ("text+image->text", "chat", True),
            ("text->embedding", "embedding", False),
            ("text->image", "image_generation", False),
            ("text+image->image", "image_edit", True),
            ("audio->text", "audio_transcription", False),
            ("text->audio", "audio_speech", False),
            ("text->video", "video_generation", False),
            ("text->rerank", "rerank", False),
            ("text->text+image", None, None),
            ("future->unknown", None, None),
        )
        records = []
        for index, (modality, mode, vision) in enumerate(cases):
            record = source_model(f"vendor/{index}", modality)
            records.append(record)
            info = sync.model_definition(record)["model_info"]
            with self.subTest(modality=modality):
                self.assertEqual(info.get("mode"), mode)
                self.assertEqual(info.get("supports_vision"), vision)
                self.assertEqual(info["tokenfactory"]["architecture"]["modality"], modality)
        self.assertEqual(len(sync.catalog_definitions({"data": records})), len(cases))

    def test_optional_metadata_does_not_invent_values(self):
        definition = sync.model_definition({"id": "vendor/basic"})
        self.assertEqual(definition["model_info"], {"tokenfactory": {"id": "vendor/basic"}})

    def test_zero_prices_are_numeric(self):
        record = source_model()
        record["pricing"] = {"prompt": "0", "completion": 0}
        info = sync.model_definition(record)["model_info"]
        self.assertEqual(info["input_cost_per_token"], 0.0)
        self.assertEqual(info["output_cost_per_token"], 0.0)

    def test_null_prices_are_preserved_without_invented_costs(self):
        cases = (
            ({"prompt": "0.000001", "completion": "0.000002", "input_cache_read": None},
             {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000002}),
            ({"prompt": None, "completion": "0", "input_cache_read": None},
             {"output_cost_per_token": 0.0}),
            ({"prompt": "0", "completion": None}, {"input_cost_per_token": 0.0}),
            ({"prompt": None, "completion": None}, {}),
        )
        for pricing, costs in cases:
            with self.subTest(pricing=pricing):
                record = source_model()
                record["pricing"] = pricing
                info = sync.model_definition(record)["model_info"]
                self.assertEqual(info["tokenfactory"]["pricing"], pricing)
                for field in ("input_cost_per_token", "output_cost_per_token"):
                    if field in costs:
                        self.assertEqual(info[field], costs[field])
                    else:
                        self.assertNotIn(field, info)

    def test_invalid_catalogs(self):
        records = [None, {}, {"id": " "}, {"id": 1}]
        for field, value in (
            ("context_length", True), ("context_length", -1), ("context_length", 1.5),
            ("created", "123"), ("name", None), ("description", []),
            ("architecture", []), ("architecture", {"modality": 5}),
            ("pricing", []), ("supported_features", "tools"),
            ("supported_sampling_parameters", [1]), ("per_request_limits", []),
            ("per_request_limits", {"tokens": -1}), ("unknown", float("nan")),
        ):
            record = source_model()
            record[field] = value
            records.append(record)
        invalid = [None, {}, {"data": {}}, {"data": []}]
        invalid.extend({"data": [source_model(), record]} for record in records)
        invalid.append({"data": [source_model(), source_model()]})
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(sync.SyncError):
                sync.catalog_definitions(response)

    def test_invalid_prices(self):
        for field in ("prompt", "completion", "input_cache_read"):
            for price in ("NaN", "Infinity", "-Infinity", "-1", "bad", True, {}, "1e9999"):
                record = source_model()
                record["pricing"][field] = price
                with self.subTest(field=field, price=price), self.assertRaises(sync.SyncError):
                    sync.model_definition(record)


class ReconciliationTests(unittest.TestCase):
    def clients(self, records=None, existing=None):
        source = Mock()
        source.request.return_value = {"data": records if records is not None else [source_model()]}
        target = Mock()
        target.request.return_value = model_page(existing or [])
        return source, target

    def test_upserts_before_pruning_entire_database(self):
        source, target = self.clients(
            records=[source_model(), source_model("vendor/new", "text->embedding")],
            existing=[database_model("b"), database_model("a"),
                      database_model("foreign", "other-provider/model")],
        )
        counts = sync.sync_once(source, target)
        source.request.assert_called_once_with("GET", "/models?verbose=true")
        calls = [call.args for call in target.request.call_args_list]
        self.assertEqual([call[1] for call in calls], [
            "/v2/model/info?page=1&size=100", "/model/a/update", "/model/new", "/model/delete", "/model/delete",
        ])
        update = calls[1][2]
        self.assertEqual(calls[1][0], "PATCH")
        self.assertEqual(update["model_info"]["id"], "a")
        self.assertEqual(update["model_info"]["tokenfactory"], source_model())
        self.assertEqual(update["model_info"]["input_cost_per_token"], 0.000001)
        self.assertEqual(update["litellm_params"]["model"], "nebius/vendor/chat")
        self.assertEqual(calls[2][2]["model_name"], "vendor/new")
        self.assertNotIn("id", calls[2][2]["model_info"])
        self.assertEqual(calls[3][2], {"id": "b"})
        self.assertEqual(calls[4][2], {"id": "foreign"})
        self.assertEqual(counts, {"created": 1, "updated": 1, "deleted": 2})

    def test_bad_catalog_performs_no_target_requests(self):
        for records in ([], [source_model(), {"id": "bad", "pricing": {"prompt": "NaN"}}]):
            source, target = self.clients(records=records)
            with self.subTest(records=records), self.assertRaises(sync.SyncError):
                sync.sync_once(source, target)
            target.request.assert_not_called()

    def test_empty_database_creates_models(self):
        source, target = self.clients()
        self.assertEqual(sync.sync_once(source, target),
                         {"created": 1, "updated": 0, "deleted": 0})
        self.assertEqual([call.args[1] for call in target.request.call_args_list],
                         ["/v2/model/info?page=1&size=100", "/model/new"])

    def test_reads_every_page_before_writing_and_pruning(self):
        rows = [database_model(str(index), f"foreign/{index}") for index in range(101)]
        source, target = self.clients()
        def request(method, path, payload=None):
            if method == "GET":
                page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["page"][0])
                return model_page(rows[(page - 1) * 100:page * 100], page, len(rows))
        target.request.side_effect = request
        counts = sync.sync_once(source, target)
        self.assertEqual(counts, {"created": 1, "updated": 0, "deleted": 101})
        self.assertEqual([call.args[1] for call in target.request.call_args_list[:3]],
                         ["/v2/model/info?page=1&size=100",
                          "/v2/model/info?page=2&size=100", "/model/new"])
        self.assertEqual(target.request.call_args.args[2], {"id": "100"})

    def test_invalid_pagination_performs_no_writes(self):
        for changes in (
            {"total_count": True}, {"total_count": -1}, {"total_pages": "1"},
            {"current_page": 2}, {"size": 50}, {"total_pages": 2},
            {"total_count": 2},
        ):
            source, target = self.clients()
            target.request.return_value = {**model_page([database_model("a")]), **changes}
            with self.subTest(changes=changes), self.assertRaises(sync.SyncError):
                sync.sync_once(source, target)
            target.request.assert_called_once_with("GET", "/v2/model/info?page=1&size=100")

    def test_failed_or_changed_second_page_performs_no_writes(self):
        first = model_page([database_model(str(index)) for index in range(100)], 1, 101)
        for second in (sync.SyncError("HTTP transport failed"),
                       model_page([database_model("100")], 2, 102)):
            source, target = self.clients()
            target.request.side_effect = [first, second]
            with self.subTest(second=type(second).__name__), self.assertRaises(sync.SyncError):
                sync.sync_once(source, target)
            self.assertEqual([call.args[0] for call in target.request.call_args_list], ["GET", "GET"])

    def test_invalid_database_response_performs_no_writes(self):
        for response in (
            {}, {"data": None}, model_page([None]), model_page([{"model_info": {}}]),
            model_page([database_model("a", db_model="true")]),
            model_page([database_model("same"), database_model("same")]),
        ):
            source, target = self.clients()
            target.request.return_value = response
            with self.subTest(response=response), self.assertRaises(sync.SyncError):
                sync.sync_once(source, target)
            target.request.assert_called_once_with("GET", "/v2/model/info?page=1&size=100")

    def test_file_owned_entries_are_outside_database_reconciliation(self):
        source, target = self.clients(existing=[database_model("file", "file-owned", False)])
        sync.sync_once(source, target)
        self.assertEqual([call.args[1] for call in target.request.call_args_list],
                         ["/v2/model/info?page=1&size=100", "/model/new"])

    def test_create_and_update_failures_prevent_deletion(self):
        for fail_at in ("/model/new", "/model/keep/update"):
            source, target = self.clients(
                records=[source_model(), source_model("vendor/new")],
                existing=[database_model("keep"), database_model("stale", "other/model")],
            )
            def request(method, path, payload=None):
                if method == "GET":
                    return model_page([database_model("keep"), database_model("stale", "other/model")])
                if path == fail_at:
                    raise sync.SyncError("HTTP transport failed")
            target.request.side_effect = request
            with self.subTest(fail_at=fail_at), self.assertLogs(sync.LOGGER, "WARNING") as logs:
                with self.assertRaises(sync.SyncError):
                    sync.sync_once(source, target)
            self.assertNotIn("/model/delete", [call.args[1] for call in target.request.call_args_list])
            self.assertIn("during upsert", logs.output[0])

    def test_repeated_cycles_converge_after_partial_failure(self):
        source, target = self.clients(records=[source_model(), source_model("vendor/new")])
        state = [database_model("stale", "foreign/model")]
        fail_once = True
        def request(method, path, payload=None):
            nonlocal fail_once
            if method == "GET":
                return model_page(copy.deepcopy(state))
            if path == "/model/new":
                if payload["model_name"] == "vendor/new" and fail_once:
                    fail_once = False
                    raise sync.SyncError("HTTP transport failed")
                row = copy.deepcopy(payload)
                row["model_info"].update(id=f"id-{row['model_name']}", db_model=True)
                state.append(row)
            elif method == "PATCH":
                row = next(row for row in state if row["model_info"]["id"] == payload["model_info"]["id"])
                row.update(copy.deepcopy(payload))
                row["model_info"]["db_model"] = True
            elif path == "/model/delete":
                state[:] = [row for row in state if row["model_info"]["id"] != payload["id"]]
        target.request.side_effect = request
        with self.assertLogs(sync.LOGGER, "WARNING"), self.assertRaises(sync.SyncError):
            sync.sync_once(source, target)
        self.assertEqual(len(state), 2)
        self.assertEqual(sync.sync_once(source, target), {"created": 1, "updated": 1, "deleted": 1})
        ids = {row["model_info"]["id"] for row in state}
        self.assertEqual(sync.sync_once(source, target), {"created": 0, "updated": 2, "deleted": 0})
        self.assertEqual({row["model_info"]["id"] for row in state}, ids)

    def test_updates_changed_pricing_and_capabilities_with_encoded_id(self):
        changed = source_model(modality="text+image->text")
        changed["pricing"]["prompt"] = "0.000003"
        changed["supported_features"] = ["tool_calling", "reasoning"]
        source, target = self.clients(records=[changed], existing=[database_model("a/b ?")])
        sync.sync_once(source, target)
        method, path, payload = target.request.call_args.args
        self.assertEqual((method, path), ("PATCH", "/model/a%2Fb%20%3F/update"))
        self.assertEqual(payload["model_info"]["id"], "a/b ?")
        self.assertEqual(payload["model_info"]["input_cost_per_token"], 0.000003)
        self.assertTrue(payload["model_info"]["supports_vision"])
        self.assertEqual(payload["model_info"]["tokenfactory"], changed)

    def test_delete_failure_reports_completed_mutations(self):
        source, target = self.clients(existing=[database_model("keep"), database_model("stale", "foreign")])
        target.request.side_effect = [target.request.return_value, {}, sync.SyncError("HTTP transport failed")]
        with self.assertLogs(sync.LOGGER, "WARNING") as logs, self.assertRaises(sync.SyncError):
            sync.sync_once(source, target)
        self.assertIn("during delete: created=0 updated=1 deleted=0", logs.output[0])


class TransportTests(unittest.TestCase):
    def response(self, body):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = body
        return response

    @patch("sync_models.urllib.request.urlopen")
    def test_authenticated_json_request_and_timeout(self, open_url):
        open_url.return_value = self.response(b'{"ok": true}')
        client = sync.API("http://example", "test-secret")
        self.assertEqual(client.request("PATCH", "/model/id-1/update", {"model_info": {"id": "id-1"}}), {"ok": True})
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "http://example/model/id-1/update")
        self.assertEqual(request.get_method(), "PATCH")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), {"model_info": {"id": "id-1"}})
        self.assertEqual(open_url.call_args.kwargs["timeout"], 30)

    @patch("sync_models.urllib.request.urlopen")
    def test_get_request_and_empty_mutation_response(self, open_url):
        open_url.return_value = self.response(b'{"data": []}')
        sync.API("https://example", "source-key").request("GET", "/models?verbose=true")
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer source-key")
        open_url.return_value = self.response(b"")
        client = sync.API("http://example", "master-key")
        self.assertIsNone(client.request("POST", "/model/delete", {"id": "id-1"}))
        with self.assertRaises(sync.SyncError):
            client.request("GET", "/v2/model/info?page=1&size=100")

    @patch("sync_models.urllib.request.urlopen")
    def test_transport_errors_do_not_expose_payloads_or_credentials(self, open_url):
        errors = (
            urllib.error.HTTPError("https://secret-url", 401, "secret-reason", {}, io.BytesIO(b"secret-body")),
            urllib.error.HTTPError("https://secret-url", 503, "secret-reason", {}, io.BytesIO(b"secret-body")),
            urllib.error.URLError("secret-reason"), TimeoutError("secret-reason"),
        )
        for error in errors:
            open_url.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(sync.SyncError) as raised:
                sync.API("http://example", "secret-key").request("GET", "/v2/model/info?page=1&size=100")
            self.assertNotIn("secret", str(raised.exception))

    @patch("sync_models.urllib.request.urlopen")
    def test_invalid_json(self, open_url):
        for body in (b"secret-body", b"\xff"):
            open_url.return_value = self.response(body)
            with self.subTest(body=body), self.assertRaises(sync.SyncError) as raised:
                sync.API("http://example", "secret-key").request("GET", "/v2/model/info?page=1&size=100")
            self.assertEqual(str(raised.exception), "HTTP response is not valid JSON")


class LoopTests(unittest.TestCase):
    def test_settings_default_and_override(self):
        env = {"NEBIUS_API_KEY": "source", "LITELLM_MASTER_KEY": "master"}
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(sync.Settings.from_env(), sync.Settings("source", "master", 600))
        with patch.dict("os.environ", {**env, "SYNC_INTERVAL": "25"}, clear=True):
            self.assertEqual(sync.Settings.from_env().interval, 25)

    def test_invalid_settings(self):
        env = {"NEBIUS_API_KEY": "source", "LITELLM_MASTER_KEY": "master"}
        for interval in ("0", "-1", "1.5", "", "abc", " 10 ", "١٠"):
            with self.subTest(interval=interval), patch.dict("os.environ", {**env, "SYNC_INTERVAL": interval}, clear=True):
                with self.assertRaises(sync.SyncError):
                    sync.Settings.from_env()
        for key in env:
            with patch.dict("os.environ", {**env, key: " "}, clear=True), self.assertRaises(sync.SyncError):
                sync.Settings.from_env()

    @patch("sync_models.API")
    @patch("sync_models.sync_once")
    def test_immediate_cycle_and_retry_without_sleeping(self, cycle, api):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        cycle.side_effect = [sync.SyncError("HTTP transport failed"), {}]
        settings = sync.Settings("source", "master", 20)
        with self.assertLogs(sync.LOGGER, "ERROR") as logs:
            sync.run(settings, stop)
        self.assertEqual(cycle.call_count, 2)
        self.assertEqual([call.args for call in stop.wait.call_args_list], [(20,), (20,)])
        self.assertEqual([call.args for call in api.call_args_list], [
            ("https://api.tokenfactory.nebius.com/v1", "source"),
            ("http://litellm:4000", "master"),
        ])
        self.assertIn("Cycle failed: HTTP transport failed", logs.output[0])

    @patch("sync_models.sync_once")
    def test_preexisting_shutdown_prevents_requests(self, cycle):
        stop = threading.Event()
        stop.set()
        sync.run(sync.Settings("source", "master", 600), stop)
        cycle.assert_not_called()

    @patch("sync_models.signal.signal")
    @patch("sync_models.run")
    @patch("sync_models.Settings.from_env", return_value=sync.Settings("source", "master", 600))
    @patch("sync_models.logging.basicConfig")
    def test_signal_handlers_interrupt_wait(self, configure_logging, settings, run, register):
        def simulate_wait(_settings, stop):
            for call in register.call_args_list:
                stop.clear()
                handler = call.args[1]
                handler(call.args[0], None)
                self.assertTrue(stop.wait(0))
        run.side_effect = simulate_wait
        self.assertEqual(sync.main(), 0)
        self.assertEqual([call.args[0] for call in register.call_args_list], [sync.signal.SIGTERM, sync.signal.SIGINT])

    @patch("sync_models.run")
    @patch("sync_models.logging.basicConfig")
    def test_invalid_startup_settings_exit_without_running(self, configure_logging, run):
        with patch.dict("os.environ", {}, clear=True), self.assertLogs(sync.LOGGER, "ERROR"):
            self.assertEqual(sync.main(), 1)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
