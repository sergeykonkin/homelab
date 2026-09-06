#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/config

run_update() {
    local should_restart="${1:-true}"
    echo "$(date): Running model update..."
    if python3 /app/update_config.py; then
        local new_sha=""
        if [[ -f /app/config/config.yaml.sha256sum ]]; then
            new_sha="$(cat /app/config/config.yaml.sha256sum)"
        fi
        if [[ "$OLD_SHA" != "$new_sha" ]]; then
            if [[ -z "$OLD_SHA" ]]; then
                echo "$(date): Initial config built"
            else
                echo "$(date): Config changed"
            fi
            if [[ "$should_restart" == "true" ]]; then
                echo "$(date): Restarting litellm..."
                docker restart litellm
            fi
        else
            echo "$(date): No config changes"
        fi
    else
        echo "$(date): update_config.py failed" >&2
    fi
}

OLD_SHA=""
if [[ -f /app/config/config.yaml.sha256sum ]]; then
    OLD_SHA="$(cat /app/config/config.yaml.sha256sum)"
fi

run_update true

while true; do
    NOW_EPOCH=$(date +%s)
    TARGET=$(date -d "$(date +%Y-%m-%d) 04:20:00" +%s)
    if [[ "$TARGET" -le "$NOW_EPOCH" ]]; then
        TARGET=$((TARGET + 86400))
    fi
    SLEEP_SECONDS=$((TARGET - NOW_EPOCH))
    echo "$(date): Sleeping $((SLEEP_SECONDS / 3600))h $((SLEEP_SECONDS % 3600 / 60))m until 4:20 AM"
    sleep "$SLEEP_SECONDS"

    OLD_SHA=""
    if [[ -f /app/config/config.yaml.sha256sum ]]; then
        OLD_SHA="$(cat /app/config/config.yaml.sha256sum)"
    fi

    run_update
done
