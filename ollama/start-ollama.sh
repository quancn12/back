#!/bin/sh
# Start Ollama and make sure the configured models are present before the
# container reports healthy.
#
# OLLAMA_PULL_MODELS is this project's own space separated list of models to
# download. Do not reuse OLLAMA_MODELS for that: Ollama itself reads
# OLLAMA_MODELS as the directory that stores models, so putting model names in
# it breaks the model volume.
set -eu

READY_FILE=/tmp/ollama-ready
rm -f "$READY_FILE"

if [ -n "${OLLAMA_MODELS:-}" ]; then
    case "${OLLAMA_MODELS}" in
        /*) : ;;
        *)
            echo "OLLAMA_MODELS must be a directory path, not a model list." >&2
            echo "Use OLLAMA_PULL_MODELS to list models to download." >&2
            exit 1
            ;;
    esac
fi

ollama serve &
ollama_pid=$!

cleanup() {
    kill "$ollama_pid" 2>/dev/null || true
    wait "$ollama_pid" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

until ollama list >/dev/null 2>&1; do
    if ! kill -0 "$ollama_pid" 2>/dev/null; then
        echo "Ollama server exited before it became reachable." >&2
        exit 1
    fi
    sleep 1
done

for model in ${OLLAMA_PULL_MODELS:-qwen2.5:3b qwen3-embedding:0.6b}; do
    case "$(printf '%s' "$model" | tr '[:upper:]' '[:lower:]')" in
        *:*cloud*|*cloud:*)
            echo "Refusing cloud model in a self hosted deployment: $model" >&2
            exit 1
            ;;
    esac

    if ollama list | awk 'NR > 1 {print $1}' | grep -Fx "$model" >/dev/null; then
        echo "Ollama model already available: $model"
        continue
    fi

    attempt=1
    until ollama pull "$model"; do
        if [ "$attempt" -ge 3 ]; then
            echo "Unable to download Ollama model after $attempt attempts: $model" >&2
            exit 1
        fi
        attempt=$((attempt + 1))
        echo "Retrying Ollama model download in 10 seconds: $model" >&2
        sleep 10
    done
done

# Only now is the container genuinely usable; the healthcheck watches this file
# so it does not have to hardcode any model name.
touch "$READY_FILE"
echo "Ollama ready with models: ${OLLAMA_PULL_MODELS:-qwen2.5:3b qwen3-embedding:0.6b}"

wait "$ollama_pid"
