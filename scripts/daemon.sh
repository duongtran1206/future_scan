#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/run"
LOG_FILE="${RUN_DIR}/future_scan.log"
PID_FILE="${RUN_DIR}/future_scan.pid"

mkdir -p "${RUN_DIR}"

start() {
  if [ -f "${PID_FILE}" ]; then
    local pid
    pid="$(cat "${PID_FILE}")"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      echo "Already running with PID ${pid}"
      exit 0
    fi
    rm -f "${PID_FILE}"
  fi

  cd "${ROOT_DIR}"
  nohup ./scripts/run_server.sh >>"${LOG_FILE}" 2>&1 &
  local pid=$!
  echo "${pid}" >"${PID_FILE}"
  echo "Started future_scan in background. PID=${pid}"
  echo "Log file: ${LOG_FILE}"
}

stop() {
  if [ ! -f "${PID_FILE}" ]; then
    echo "Not running (no PID file)."
    exit 0
  fi

  local pid
  pid="$(cat "${PID_FILE}")"
  if [ -z "${pid}" ]; then
    rm -f "${PID_FILE}"
    echo "Not running (empty PID file)."
    exit 0
  fi

  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" || true
    sleep 1
    if kill -0 "${pid}" 2>/dev/null; then
      kill -9 "${pid}" || true
    fi
    echo "Stopped PID ${pid}"
  else
    echo "Process ${pid} not found."
  fi

  rm -f "${PID_FILE}"
}

status() {
  if [ ! -f "${PID_FILE}" ]; then
    echo "Not running"
    exit 1
  fi

  local pid
  pid="$(cat "${PID_FILE}")"
  if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
    echo "Running (PID ${pid})"
    exit 0
  fi

  echo "Not running (stale PID file)"
  exit 1
}

logs() {
  touch "${LOG_FILE}"
  tail -f "${LOG_FILE}"
}

case "${1:-}" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  restart)
    stop || true
    start
    ;;
  status)
    status
    ;;
  logs)
    logs
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs}"
    exit 2
    ;;
esac
