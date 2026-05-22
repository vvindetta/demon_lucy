#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

LUCY_HOME="${LUCY_HOME:-$HOME/lucy_notes_daemon}"
NOTES_REPO="${NOTES_REPO:-/storage/shared/Notes}"
PYTHON_BIN="${PYTHON_BIN:-python}"

if [ ! -d "$LUCY_HOME" ]; then
  echo "lucy repo not found: $LUCY_HOME" >&2
  exit 1
fi

cd "$LUCY_HOME"
export PYTHONPATH="$LUCY_HOME${PYTHONPATH:+:$PYTHONPATH}"

exec "$PYTHON_BIN" main_oneshot.py \
  --oneshot-paths "$NOTES_REPO" \
  --sys-modules git today \
  "$@"
