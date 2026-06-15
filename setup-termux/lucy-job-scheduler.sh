#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# Environment overrides make the same script usable from Termux:Boot, manual
# shell runs, and custom installs without editing the file.
LUCY_HOME="${LUCY_HOME:-$HOME/demon_lucy}"
LUCY_STATE_DIR="${LUCY_STATE_DIR:-$HOME/.lucy}"

ONESHOT_SCRIPT="${ONESHOT_SCRIPT:-$HOME/lucy-oneshot.sh}"

# Android JobScheduler replaces an existing job with the same id. Keep this id
# stable so repeated boot/manual registration updates Lucy's job instead of
# creating duplicate periodic jobs.
JOB_ID="${JOB_ID:-101}"

# Termux:API passes this value directly to Android JobScheduler. Android may
# batch or delay the job for battery policy, so this is a minimum period, not a
# wall-clock guarantee.
JOB_PERIOD_MS="${JOB_PERIOD_MS:-1800000}"

# These are Termux:API option values, not shell booleans. Use strings accepted by
# `termux-job-scheduler` so callers can override them through the environment.
JOB_NETWORK="${JOB_NETWORK:-any}"
JOB_BATTERY_NOT_LOW="${JOB_BATTERY_NOT_LOW:-true}"
JOB_PERSISTED="${JOB_PERSISTED:-true}"

SCHEDULER_LOG="$LUCY_STATE_DIR/lucy-job-scheduler.log"

mkdir -p "$LUCY_STATE_DIR"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') lucy job scheduler register ==="

  if ! command -v termux-job-scheduler >/dev/null 2>&1; then
    echo "termux-job-scheduler is missing; install Termux:API app and termux-api package"
    exit 1
  fi

  # Prefer a user-installed shortcut at ~/lucy-oneshot.sh, but fall back to the
  # repo copy so a fresh checkout can register the job before shortcuts are set.
  if [ ! -x "$ONESHOT_SCRIPT" ] && [ -x "$LUCY_HOME/setup-termux/lucy-oneshot.sh" ]; then
    ONESHOT_SCRIPT="$LUCY_HOME/setup-termux/lucy-oneshot.sh"
  fi

  if [ ! -x "$ONESHOT_SCRIPT" ]; then
    echo "oneshot script is not executable: $ONESHOT_SCRIPT"
    echo "set ONESHOT_SCRIPT or install setup-termux/lucy-oneshot.sh as ~/lucy-oneshot.sh"
    exit 1
  fi

  # `--persisted true` asks Android to keep the job across reboot. Termux:Boot is
  # still useful because some devices drop persisted jobs after app updates or
  # aggressive battery-management resets.
  termux-job-scheduler \
    --job-id "$JOB_ID" \
    --script "$ONESHOT_SCRIPT" \
    --period-ms "$JOB_PERIOD_MS" \
    --network "$JOB_NETWORK" \
    --battery-not-low "$JOB_BATTERY_NOT_LOW" \
    --persisted "$JOB_PERSISTED"

  # Print the scheduler's view of registered jobs into the same log; this is the
  # quickest way to confirm which script/path Android will run later.
  termux-job-scheduler --pending
} > >(tee -a "$SCHEDULER_LOG") 2>&1
