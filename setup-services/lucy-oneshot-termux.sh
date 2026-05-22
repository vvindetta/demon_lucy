#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

LUCY_HOME="$HOME/lucy_notes_daemon"
NOTES_REPO="$HOME/storage/shared/Notes"

LUCY_STATE_DIR="$HOME/.lucy"
ONESHOT_LOG="$LUCY_STATE_DIR/lucy-oneshot.log"
PYTHON_BIN="python"

if [ ! -d "$LUCY_HOME" ]; then
  echo "lucy repo not found: $LUCY_HOME" >&2
  exit 1
fi

if [ ! -e "$NOTES_REPO" ]; then
  echo "notes path not found: $NOTES_REPO" >&2
  exit 1
fi

mkdir -p "$LUCY_STATE_DIR"

cd "$LUCY_HOME"
export PYTHONPATH="$LUCY_HOME${PYTHONPATH:+:$PYTHONPATH}"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') lucy oneshot start ==="
  "$PYTHON_BIN" main_oneshot.py \
    --oneshot-paths "$NOTES_REPO" \
    --sys-modules git today \
    "$@"
} > >(tee -a "$ONESHOT_LOG") 2>&1
