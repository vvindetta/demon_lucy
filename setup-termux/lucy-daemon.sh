#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# Termux exposes Android shared storage under ~/storage after you run
# `termux-setup-storage` once in the Termux shell. Keep Lucy state in private
# Termux home so logs and pid files survive shared-storage remounts.
LUCY_HOME="$HOME/demon_lucy"
NOTES_REPO="$HOME/storage/shared/Notes"
CONFIG_PATH="$NOTES_REPO/.lucy/config.txt"

LUCY_STATE_DIR="$HOME/.lucy"
DAEMON_LAUNCH_LOG="$LUCY_STATE_DIR/lucy-daemon-launch.log"
DAEMON_RUN_LOG="$LUCY_STATE_DIR/lucy-daemon.log"
PID_FILE="$LUCY_STATE_DIR/lucy-daemon.pid"

PYTHON_BIN="python"

# Android may start Termux:Boot before shared storage is mounted. The fixed
# startup pause handles early boot churn; the loop below waits for Notes only.
INITIAL_SLEEP_SECONDS=15
WAIT_FOR_NOTES_SECONDS=60

mkdir -p "$LUCY_STATE_DIR"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') lucy daemon launch ==="

  # Best-effort Termux:API wake lock: useful during boot, harmless when the app
  # or API package is missing.
  termux-wake-lock >/dev/null 2>&1 || true

  sleep "$INITIAL_SLEEP_SECONDS"

  if [ ! -d "$LUCY_HOME" ]; then
    echo "lucy repo not found: $LUCY_HOME"
    exit 1
  fi

  waited=0
  while [ ! -d "$NOTES_REPO" ] && [ "$waited" -lt "$WAIT_FOR_NOTES_SECONDS" ]; do
    sleep 1
    waited=$((waited + 1))
  done

  if [ ! -d "$NOTES_REPO" ]; then
    echo "notes path not ready after ${WAIT_FOR_NOTES_SECONDS}s: $NOTES_REPO"
    exit 1
  fi

  if [ ! -f "$CONFIG_PATH" ]; then
    echo "config file not found: $CONFIG_PATH"
    exit 1
  fi

  # `kill -0` checks whether the pid exists without sending a real signal. This
  # avoids starting a second watchdog when Termux:Boot runs the script twice.
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "daemon already running with pid $(cat "$PID_FILE")"
    exit 0
  fi

  cd "$LUCY_HOME"

  # Run directly from a checkout. This keeps Termux setup simple and does not
  # require installing Demon Lucy as a Python package.
  export PYTHONPATH="$LUCY_HOME${PYTHONPATH:+:$PYTHONPATH}"

  # `nohup` detaches from the launcher shell; bash process substitution lets us
  # append stdout/stderr to a persistent log while still showing launch output.
  nohup "$PYTHON_BIN" main_daemon.py \
    --sys-config-path "$CONFIG_PATH" \
    "$@" > >(tee -a "$DAEMON_RUN_LOG") 2>&1 &

  daemon_pid=$!
  echo "$daemon_pid" > "$PID_FILE"
  echo "daemon started with pid $daemon_pid"
} > >(tee -a "$DAEMON_LAUNCH_LOG") 2>&1
