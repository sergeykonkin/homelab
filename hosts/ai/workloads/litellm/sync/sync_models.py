"""Mirror the Token Factory catalog into LiteLLM's model database."""

import copy
import json
import logging
import math
import os
import signal
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


TOKEN_FACTORY_BASE = "https://api.tokenfactory.nebius.com/v1"
LITELLM_BASE = "http://litellm:4000"
HTTP_TIMEOUT = 30
LOGGER = logging.getLogger(__name__)


class SyncError(Exception):
    """A cycle failure whose message contains no remote payload or credentials."""


@dataclass(frozen=True)
class Settings:
    nebius_api_key: str
    master_key: str
    interval: int

    @classmethod
    def from_env(cls):
        nebius_key = os.environ.get("NEBIUS_API_KEY", "")
        master_key = os.environ.get("LITELLM_MASTER_KEY", "")
        if not nebius_key.strip() or not master_key.strip():
            raise SyncError("NEBIUS_API_KEY and LITELLM_MASTER_KEY are required")
        interval_text = os.environ.get("SYNC_INTERVAL", "600")
        if not interval_text.isascii() or not interval_text.isdecimal():
            raise SyncError("SYNC_INTERVAL must be a positive integer")
        try:
            interval = int(interval_text)
        except ValueError:
            raise SyncError("SYNC_INTERVAL must be a positive integer") from None
        if interval <= 0:
            raise SyncError("SYNC_INTERVAL must be a positive integer")
        return cls(nebius_key, master_key, interval)


class API:
    """JSON HTTP transport with a bounded timeout and sanitized errors."""

    def __init__(self, base, key):
        self.base = base
        self.key = key

    def request(self, method, path, payload=None):
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise SyncError(f"HTTP request failed (status {status})") from None
        except (urllib.error.URLError, OSError, ValueError):
            raise SyncError("HTTP transport failed") from None
        if not body:
            if method == "GET":
                raise SyncError("HTTP response has no JSON body")
            return None
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise SyncError("HTTP response is not valid JSON") from None


def nonnegative_number(value):
    """Validate a finite rate or limit and return its JSON numeric value."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise SyncError("Catalog contains invalid numeric metadata")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise SyncError("Catalog contains invalid numeric metadata")
        result = float(number)
        if not math.isfinite(result):
            raise SyncError("Catalog contains invalid numeric metadata")
        return result
    except (InvalidOperation, ValueError, OverflowError):
        raise SyncError("Catalog contains invalid numeric metadata") from None


def model_definition(model):
    """Validate one source record and build its authoritative LiteLLM definition."""
    if not isinstance(model, dict) or not isinstance(model.get("id"), str):
        raise SyncError("Catalog contains a model without a string ID")
    model_id = model["id"]
    if not model_id.strip():
        raise SyncError("Catalog contains an empty model ID")
    for field in ("name", "description", "object", "owned_by"):
        if field in model and not isinstance(model[field], str):
            raise SyncError("Catalog contains invalid text metadata")
    for field in ("context_length", "created"):
        if field in model:
            value = model[field]
            if type(value) is not int or value < 0:
                raise SyncError("Catalog contains invalid integer metadata")
    for field in ("supported_features", "supported_sampling_parameters"):
        value = model.get(field)
        if value is not None and (
            not isinstance(value, list) or any(not isinstance(item, str) for item in value)
        ):
            raise SyncError("Catalog contains invalid capability metadata")

    architecture = model.get("architecture", {})
    if not isinstance(architecture, dict):
        raise SyncError("Catalog contains invalid architecture metadata")
    modality = architecture.get("modality")
    if modality is not None and not isinstance(modality, str):
        raise SyncError("Catalog contains invalid modality metadata")

    pricing = model.get("pricing", {})
    if not isinstance(pricing, dict):
        raise SyncError("Catalog contains invalid pricing metadata")
    rates = {
        name: nonnegative_number(value)
        for name, value in pricing.items()
        if value is not None
    }
    limits = model.get("per_request_limits")
    if limits is not None:
        if not isinstance(limits, dict):
            raise SyncError("Catalog contains invalid limit metadata")
        for value in limits.values():
            nonnegative_number(value)

    info = {"tokenfactory": copy.deepcopy(model)}
    for source, destination in (
        ("prompt", "input_cost_per_token"),
        ("completion", "output_cost_per_token"),
    ):
        if source in rates:
            info[destination] = rates[source]
    if "context_length" in model:
        info["max_input_tokens"] = model["context_length"]

    if modality is not None:
        inputs, separator, output = modality.partition("->")
        input_types = set(inputs.split("+"))
        known_inputs = {"text", "image", "audio", "video"}
        if separator and input_types <= known_inputs:
            modes = {
                "text": "audio_transcription" if input_types == {"audio"} else "chat",
                "embedding": "embedding",
                "image": "image_edit" if "image" in input_types else "image_generation",
                "audio": "audio_speech",
                "video": "video_generation",
                "rerank": "rerank",
            }
            if output in modes:
                info["mode"] = modes[output]
                info["supports_vision"] = "image" in input_types

    return {
        "model_name": model_id,
        "litellm_params": {
            "model": f"nebius/{model_id}",
            "api_base": TOKEN_FACTORY_BASE,
            "api_key": "os.environ/NEBIUS_API_KEY",
        },
        "model_info": info,
    }


def catalog_definitions(response):
    """Validate the entire catalog before a cycle can write to LiteLLM."""
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise SyncError("Catalog response must contain a data list")
    if not response["data"]:
        raise SyncError("Catalog is empty; database models are preserved")
    definitions = {}
    for record in response["data"]:
        definition = model_definition(record)
        name = definition["model_name"]
        if name in definitions:
            raise SyncError("Catalog contains duplicate model IDs")
        definitions[name] = definition
    try:
        json.dumps(definitions, allow_nan=False)
    except (ValueError, TypeError):
        raise SyncError("Catalog contains invalid JSON metadata") from None
    return definitions


def database_models(response):
    """Validate and group database deployments by their client-facing names."""
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise SyncError("LiteLLM model response must contain a data list")
    models = {}
    ids = set()
    for record in response["data"]:
        if not isinstance(record, dict) or not isinstance(record.get("model_info"), dict):
            raise SyncError("LiteLLM returned invalid model metadata")
        info = record["model_info"]
        if "db_model" in info and type(info["db_model"]) is not bool:
            raise SyncError("LiteLLM returned an invalid database model flag")
        if info.get("db_model") is False:
            continue
        name, model_id = record.get("model_name"), info.get("id")
        if not isinstance(name, str) or not name.strip():
            raise SyncError("LiteLLM returned an invalid model name")
        if not isinstance(model_id, str) or not model_id.strip() or model_id in ids:
            raise SyncError("LiteLLM returned an invalid or repeated database ID")
        ids.add(model_id)
        models.setdefault(name, []).append(model_id)
    return {name: sorted(model_ids) for name, model_ids in models.items()}


def sync_once(source, target):
    """Upsert the full catalog, then prune stale and duplicate database models."""
    desired = catalog_definitions(source.request("GET", "/models?verbose=true"))
    existing = database_models(target.request("GET", "/model/info"))
    counts = {"created": 0, "updated": 0, "deleted": 0}
    phase = "upsert"
    try:
        for name, definition in desired.items():
            if name in existing:
                model_id = existing[name][0]
                definition["model_info"]["id"] = model_id
                path_id = urllib.parse.quote(model_id, safe="")
                target.request("PATCH", f"/model/{path_id}/update", definition)
                counts["updated"] += 1
            else:
                target.request("POST", "/model/new", definition)
                counts["created"] += 1
        phase = "delete"
        for name, model_ids in existing.items():
            stale_ids = model_ids[1:] if name in desired else model_ids
            for model_id in stale_ids:
                target.request("POST", "/model/delete", {"id": model_id})
                counts["deleted"] += 1
    except SyncError:
        LOGGER.warning(
            "Cycle interrupted during %s: created=%d updated=%d deleted=%d",
            phase, counts["created"], counts["updated"], counts["deleted"],
        )
        raise
    LOGGER.info(
        "Cycle complete: models=%d created=%d updated=%d deleted=%d",
        len(desired), counts["created"], counts["updated"], counts["deleted"],
    )
    return counts


def run(settings, stop):
    """Run immediate and periodic cycles until a termination event is set."""
    source = API(TOKEN_FACTORY_BASE, settings.nebius_api_key)
    target = API(LITELLM_BASE, settings.master_key)
    while not stop.is_set():
        try:
            sync_once(source, target)
        except SyncError as error:
            LOGGER.error("Cycle failed: %s", error)
        if stop.wait(settings.interval):
            break


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = Settings.from_env()
    except SyncError as error:
        LOGGER.error("Invalid settings: %s", error)
        return 1
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signum, _frame: stop.set())
    run(settings, stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
