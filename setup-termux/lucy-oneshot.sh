#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# Same path convention as the daemon script: repo in private Termux home, notes
# in Android shared storage after you run `termux-setup-storage` once in the
# Termux shell.
LUCY_HOME="$HOME/demon_lucy"
NOTES_REPO="$HOME/storage/shared/Notes"

# Keep logs outside shared storage so scheduled jobs can write them even while
# Android is still preparing the shared-storage mount.
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

# Run from the checkout without requiring `pip install -e .` on the phone.
export PYTHONPATH="$LUCY_HOME${PYTHONPATH:+:$PYTHONPATH}"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') lucy oneshot start ==="

  # Mobile scheduled runs are intentionally narrow: git sync plus archive
  # cleanup are useful in the background, while editor-facing modules should stay
  # in the real-time daemon.
  "$PYTHON_BIN" main_oneshot.py \
    --oneshot-paths "$NOTES_REPO" \
    --sys-modules git archive \
    "$@"
} > >(tee -a "$ONESHOT_LOG") 2>&1
