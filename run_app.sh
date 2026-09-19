#!/bin/bash
# Open an installed Guardian without reinstalling dependencies or updating code.
set -euo pipefail
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$DIR"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export AGENT_BACKEND=ollama
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_BASE_URL=http://127.0.0.1:11434/v1

if [ ! -x .venv/bin/python ] || ! command -v ollama >/dev/null; then
    echo "Guardian setup is incomplete. Run the install command from the README again." >&2
    exit 1
fi

# Reopen only a server belonging to this checkout; never stop another app's server.
if command -v lsof >/dev/null; then
    existing_pid=$(lsof -nP -iTCP:8501 -sTCP:LISTEN -t 2>/dev/null | head -1 || true)
    if [ -n "$existing_pid" ]; then
        command_line=$(ps -p "$existing_pid" -o command=)
        if [[ "$command_line" == *"$DIR/app.py"* ]]; then
            echo "Guardian is already running (PID $existing_pid): http://localhost:8501"
            if [ "$(uname -s)" = Darwin ]; then open http://localhost:8501; fi
            exit 0
        fi
        echo "Port 8501 is in use by another process. Close that app before opening Guardian." >&2
        exit 1
    fi
fi

ollama_pid=""
if ! curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    log_file=$(mktemp "${TMPDIR:-/tmp}/guardian-ollama.XXXXXX")
    echo "Starting Ollama. Log: $log_file"
    nohup ollama serve >"$log_file" 2>&1 </dev/null &
    ollama_pid=$!
    echo "Ollama PID: $ollama_pid"
    deadline=$((SECONDS + 30))
    ready=false
    while [ "$SECONDS" -lt "$deadline" ]; do
        if curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
            ready=true
            break
        fi
        kill -0 "$ollama_pid" 2>/dev/null || break
        sleep 1
    done
    if [ "$ready" != true ]; then
        echo "Ollama could not start. Details:" >&2
        tail -n 20 "$log_file" >&2
        kill "$ollama_pid" 2>/dev/null || true
        wait "$ollama_pid" 2>/dev/null || true
        exit 1
    fi
fi

model=$(.venv/bin/python - <<'PY'
import os
from dotenv import dotenv_values
print(os.getenv('OLLAMA_MODEL') or dotenv_values('.env').get('OLLAMA_MODEL') or 'qwen3.5:2b')
PY
)
export OLLAMA_MODEL="$model"
if ! ollama show "$model" >/dev/null 2>&1; then
    echo "Downloading $model. This is needed only once; progress appears below."
    pull_log=$(mktemp "${TMPDIR:-/tmp}/guardian-pull.XXXXXX")
    if ollama pull "$model" 2>&1 | tee "$pull_log"; then
        rm -f "$pull_log"
    else
        pull_status=$?
        needs_upgrade=false
        if grep -qi 'requires a newer version of Ollama' "$pull_log"; then
            needs_upgrade=true
        fi
        rm -f "$pull_log"
        # Do not leave an outdated server we started behind after a failed setup.
        # Existing servers may belong to another terminal/app; leave those alone.
        if [ -n "$ollama_pid" ]; then
            kill "$ollama_pid" 2>/dev/null || true
            wait "$ollama_pid" 2>/dev/null || true
        fi
        if $needs_upgrade && [ "${GUARDIAN_OLLAMA_UPGRADE_ATTEMPTED:-}" != 1 ]; then
            if [ "$(uname -s)" = Darwin ] && command -v brew >/dev/null &&
               brew list --formula --versions ollama >/dev/null 2>&1; then
                echo "This model needs a newer Ollama. Updating it with Homebrew..."
                brew update
                brew upgrade ollama
                if [ -n "$ollama_pid" ]; then
                    export GUARDIAN_OLLAMA_UPGRADE_ATTEMPTED=1
                    exec /bin/bash "$DIR/run_app.sh"
                fi
                echo "Ollama has been updated, but an older server is still running."
                echo "Restart your Mac, then double-click Guardian.command on your Desktop."
            else
                echo "Update Ollama from https://ollama.com/download, then restart your Mac and open Guardian.command." >&2
            fi
        elif $needs_upgrade; then
            echo "The updated Ollama still cannot download $model. Check https://ollama.com/download for a compatible release." >&2
        fi
        exit "$pull_status"
    fi
fi

echo "Opening Guardian at http://localhost:8501 (PID $$)."
echo "Keep this window open while using Guardian. Press Control-C here to stop the app."
exec .venv/bin/python -m streamlit run "$DIR/app.py" \
    --server.address=127.0.0.1 --server.port=8501 --server.headless=false \
    --browser.gatherUsageStats=false
