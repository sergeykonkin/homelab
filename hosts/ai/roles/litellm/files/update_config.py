#!/usr/bin/env python3
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.tokenfactory.nebius.com/v1/"
REQUEST_TIMEOUT = 30
CONFIG_DIR = os.environ.get("CONFIG_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config"))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")
HASH_PATH = os.path.join(CONFIG_DIR, "config.yaml.sha256sum")


def fetch_models(api_key: str) -> list[dict]:
    req = urllib.request.Request(
        f"{API_BASE}models?verbose=1",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} from Nebius API: {body}", file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        print(f"Network error: {e.reason}", file=sys.stderr)
        raise
    except json.JSONDecodeError as e:
        print(f"Invalid JSON from API: {e}", file=sys.stderr)
        raise

    if not isinstance(data, dict):
        raise TypeError(f"Expected dict response, got {type(data).__name__}")
    if "data" not in data:
        raise KeyError(f"Missing 'data' key in response. Keys: {list(data.keys())}")
    if not isinstance(data["data"], list):
        raise TypeError(f"Expected list for 'data', got {type(data['data']).__name__}")

    return [
        m for m in data["data"]
        if m.get("architecture", {}).get("modality") == "text->text"
    ]


def build_config(models: list[dict]) -> str:
    content = "model_list:\n"
    for m in models:
        pricing = m.get("pricing", {})
        content += f"  - model_name: {m['id'].split('/')[-1]}\n"
        content += "    litellm_params:\n"
        content += f"      model: nebius/{m['id']}\n"
        content += f"      input_cost_per_token: {pricing.get('prompt', 0)}\n"
        content += f"      output_cost_per_token: {pricing.get('completion', 0)}\n"
    content += "\nlitellm_settings:\n  drop_params: true\n"
    return content


def main() -> None:
    try:
        api_key = os.environ["NEBIUS_API_KEY"]
    except KeyError:
        print("Error: NEBIUS_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    try:
        models = fetch_models(api_key)
    except Exception:
        sys.exit(1)

    try:
        content = build_config(models)
    except (KeyError, TypeError) as e:
        print(f"Error building config from model data: {e}", file=sys.stderr)
        sys.exit(1)

    sha = hashlib.sha256(content.encode()).hexdigest()

    old_sha = ""
    if os.path.exists(HASH_PATH):
        with open(HASH_PATH) as f:
            old_sha = f.read().strip()

    if sha != old_sha:
        try:
            with open(CONFIG_PATH, "w") as f:
                f.write(content)
            with open(HASH_PATH, "w") as f:
                f.write(sha)
        except OSError as e:
            print(f"Error writing config: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Updated {CONFIG_PATH} with {len(models)} models (changed)")
    else:
        print(f"No changes ({len(models)} models, same as before)")


if __name__ == "__main__":
    main()
