#!/usr/bin/env bash
# Start the harness, wait for it to answer, then open the Electron app.
# The harness is stopped again when the app window closes.
#
#   ./scripts/dev.sh               # rebuild the GUI, then run
#   ./scripts/dev.sh --build       # kept for compatibility; build always runs
#   ./scripts/dev.sh --port 9000   # harness on another port (ZEN_HARNESS_URL follows)
#   ./scripts/dev.sh --wait 120    # seconds to wait for /health (default 60)
#
# A key in OPENROUTER_API_KEY binds the provider at start; without one the
# harness starts unbound and the app's Settings page binds it.
# ZEN_RECALL_GRAPH_TIMEOUT defaults to 60 s here (the harness's own default is 20):
# on a CPU-only machine Hindsight's local reranker can pass 20 s while an ingest runs.

set -euo pipefail

build=0
port=8765
wait_seconds=60

while [[ $# -gt 0 ]]; do
    case "$1" in
        -b|--build) build=1; shift ;;
        -p|--port) port="$2"; shift 2 ;;
        -w|--wait) wait_seconds="$2"; shift 2 ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [[ ! -d node_modules ]]; then
    echo '== installing npm packages (node_modules is missing)'
    if ! npm install; then
        echo 'npm install failed' >&2
        exit 1
    fi
fi

echo '== building the GUI'
if ! npm run build; then
    echo 'npm run build failed' >&2
    exit 1
fi

export ZEN_PORT="$port"
export ZEN_HARNESS_URL="http://127.0.0.1:$port"
export ZEN_RECALL_GRAPH_TIMEOUT="${ZEN_RECALL_GRAPH_TIMEOUT:-60}"

echo "== starting the harness on $ZEN_HARNESS_URL"
# job control puts the harness in its own process group, so uv and the
# python child it spawns can be taken down together
set -m
uv run python -m src.harness.main &
harness_pid=$!
set +m

stop_harness() {
    if kill -0 "$harness_pid" 2>/dev/null; then
        echo '== stopping the harness'
        kill -TERM -- "-$harness_pid" 2>/dev/null || true
        # a clean stop closes the memory store (its Hindsight server and Postgres stop) and may first wait out a memory write
        # (up to 30 s); usually it takes 2-3 s. Kill only past 45 s, so a slow close isn't cut off.
        for _ in $(seq 1 180); do
            kill -0 "$harness_pid" 2>/dev/null || return 0
            sleep 0.25
        done
        echo '== the harness did not stop in 45 s; killing it'
        kill -KILL -- "-$harness_pid" 2>/dev/null || true
    fi
}
trap stop_harness EXIT
trap 'exit 130' INT TERM

health() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 2 "$ZEN_HARNESS_URL/health"
    else
        wget -qO- --timeout=2 "$ZEN_HARNESS_URL/health"
    fi
}

deadline=$((SECONDS + wait_seconds))
body=''
ready=0
while (( SECONDS < deadline )); do
    if ! kill -0 "$harness_pid" 2>/dev/null; then
        wait "$harness_pid" && code=0 || code=$?
        echo "the harness exited early (code $code)" >&2
        exit 1
    fi
    if body="$(health 2>/dev/null)"; then
        ready=1
        break
    fi
    sleep 0.5
done
if [[ $ready -eq 0 ]]; then
    echo "the harness did not answer /health within ${wait_seconds} s" >&2
    exit 1
fi
echo "== harness ready: $body"

echo '== opening the app (close the window to stop everything)'
npm start
