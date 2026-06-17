#!/usr/bin/env bash
# ─── EDGE API Server launcher ──────────────────────────────────────────────
# Starts the API in the background, logs to a file, PID tracked for stop/restart.
#
# Usage:
#   ./api_server.sh start
#   ./api_server.sh stop
#   ./api_server.sh restart
#   ./api_server.sh status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
API_SCRIPT="${SCRIPT_DIR}/api.py"
PID_FILE="${SCRIPT_DIR}/api.pid"
LOG_FILE="${SCRIPT_DIR}/api.log"
PORT=8081

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "EDGE API is already running (PID $(cat "$PID_FILE"))."
        return
    fi

    echo "Starting EDGE API on port ${PORT}…"
    nohup "$PYTHON" "$API_SCRIPT" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "PID $(cat "$PID_FILE") — logs: ${LOG_FILE}"

    # Wait up to 5 s for readiness
    for i in $(seq 1 10); do
        if curl -sf "http://localhost:${PORT}/api/health" >/dev/null 2>&1; then
            echo "EDGE API is ready."
            return
        fi
        sleep 0.5
    done
    echo "Warning: health check did not respond within 5 s. Check ${LOG_FILE}."
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "No PID file found — EDGE API is not running."
        return
    fi
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping EDGE API (PID ${PID})…"
        kill "$PID"
        # Wait for process to exit
        for _ in $(seq 1 10); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 0.5
        done
        kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null || true
        rm -f "$PID_FILE"
        echo "Stopped."
    else
        echo "Process ${PID} not found — cleaning up PID file."
        rm -f "$PID_FILE"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "EDGE API is running (PID $(cat "$PID_FILE"))."
    else
        echo "EDGE API is not running."
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; start ;;
    status)  status ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
