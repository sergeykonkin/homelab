#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR=/app/config
CONFIG_FILE="$CONFIG_DIR/config.yaml"
HASH_FILE="$CONFIG_DIR/config.yaml.sha256sum"
APPLIED_HASH_FILE="$CONFIG_DIR/config.yaml.applied-sha256"
RETRY_SECONDS=60

mkdir -p "$CONFIG_DIR"

file_contents() {
    if [[ -f "$1" ]]; then
        cat "$1"
    fi
}

config_usable() {
    [[ -s "$CONFIG_FILE" && -s "$HASH_FILE" ]]
}

# Restart litellm when it is not running the config on disk. The applied hash
# is recorded only after the restart succeeds, so a failed restart is retried
# on a later run instead of being hidden by the already-stored config hash.
apply_config() {
    local new_sha applied_sha
    new_sha="$(file_contents "$HASH_FILE")"
    applied_sha="$(file_contents "$APPLIED_HASH_FILE")"
    if [[ -z "$new_sha" ]]; then
        echo "$(date): update_config.py left no config hash" >&2
        return 1
    fi
    if [[ "$new_sha" == "$applied_sha" ]]; then
        echo "$(date): No config changes"
        return 0
    fi
    if [[ -z "$applied_sha" ]]; then
        echo "$(date): Initial config built"
    else
        echo "$(date): Config changed"
    fi
    echo "$(date): Restarting litellm..."
    if docker restart litellm; then
        echo "$new_sha" > "$APPLIED_HASH_FILE"
    else
        echo "$(date): litellm restart failed; will retry" >&2
        return 1
    fi
}

run_update() {
    echo "$(date): Running model update..."
    if ! python3 /app/update_config.py; then
        echo "$(date): update_config.py failed" >&2
        return 1
    fi
    apply_config
}

# A failed update can leave a previously written config without its litellm
# restart; converge from the on-disk hash so the restart is not deferred to
# the next successful update.
converge_pending_restart() {
    if config_usable; then
        apply_config
    fi
}

# litellm starts only once this container is healthy, which requires
# config.yaml. Retry promptly until a usable configuration exists instead of
# idling until the next daily run.
while ! config_usable; do
    run_update || converge_pending_restart || true
    if ! config_usable; then
        echo "$(date): No usable config yet; retrying in ${RETRY_SECONDS}s" >&2
        sleep "$RETRY_SECONDS"
    fi
done

run_update || converge_pending_restart || true

while true; do
    NOW_EPOCH=$(date +%s)
    TARGET=$(date -d "$(date +%Y-%m-%d) 04:20:00" +%s)
    if [[ "$TARGET" -le "$NOW_EPOCH" ]]; then
        TARGET=$((TARGET + 86400))
    fi
    SLEEP_SECONDS=$((TARGET - NOW_EPOCH))
    echo "$(date): Sleeping $((SLEEP_SECONDS / 3600))h $((SLEEP_SECONDS % 3600 / 60))m until 4:20 AM"
    sleep "$SLEEP_SECONDS"

    run_update || converge_pending_restart || true
done
