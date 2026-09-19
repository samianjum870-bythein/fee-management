#!/usr/bin/env bash
# ATTENDANCE_CRON_SETUP_V1
# -------------------------------------------------------------------
# Idempotent crontab installer for the attendance auto-mark job.
#
#   bash scripts/install_attendance_cron.sh
#       -> just prints the exact line to add (dry run)
#
#   bash scripts/install_attendance_cron.sh --install
#       -> installs the line into the current user's crontab,
#          replacing any previous copy of the same job.
#
#   bash scripts/install_attendance_cron.sh --install --time "0 23"
#       -> override the schedule (default 00:05 local time).
#
# The job runs `attendance_auto_present` for yesterday's date once
# per day. Combined with the lazy catch-up already wired into the
# admin / staff attendance pages, this gives you both a safety net
# and a guaranteed nightly sweep.
# -------------------------------------------------------------------

set -u

# -- parse args -----------------------------------------------------
DO_INSTALL=0
# ATTENDANCE_CRON_SAFETY_V1
# Default 03:05 local time. 03:05 is safely past midnight in every
# UTC+4..UTC+6 timezone, so the OS calendar date and Django's
# timezone.localdate() agree. The previous default (00:05) could
# mismatch when the host OS zone and Django TIME_ZONE differ by a
# fraction of an hour — e.g. Debian set to Asia/Kolkata (UTC+5:30)
# while settings.py sets TIME_ZONE = 'Asia/Karachi' (UTC+5).
CRON_TIME="5 3"   # 03:05 local time, every day

while [ "$#" -gt 0 ]; do
    case "$1" in
        --install) DO_INSTALL=1; shift ;;
        --time)    CRON_TIME="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,25p' "$0"
            exit 0
            ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

# -- resolve project root -------------------------------------------
SCRIPT_SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" >/dev/null 2>&1 && pwd)"
    SCRIPT_SOURCE="$(readlink "$SCRIPT_SOURCE")"
    [[ "$SCRIPT_SOURCE" != /* ]] && SCRIPT_SOURCE="$DIR/$SCRIPT_SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" >/dev/null 2>&1 && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

TARGET_SCRIPT="$PROJECT_ROOT/scripts/attendance_auto_present.sh"

if [ ! -x "$TARGET_SCRIPT" ]; then
    echo "ERROR: $TARGET_SCRIPT not found or not executable." >&2
    echo "       Run the patcher again, or: chmod +x \"$TARGET_SCRIPT\"" >&2
    exit 1
fi

# -- build the crontab line -----------------------------------------
# Use /bin/bash explicitly: cron often runs with a minimal $PATH and
# no shell features, so we do not rely on $SHELL being anything
# useful.
CRON_LINE="$CRON_TIME * * * /bin/bash \"$TARGET_SCRIPT\" >> \"$PROJECT_ROOT/logs/attendance_cron_cronoutput.log\" 2>&1"

# Unique marker so we can find and replace our own entry without
# touching any other cron jobs the user has installed.
MARKER="# AXIS_ATTENDANCE_AUTO_MARK"

echo "Project root   : $PROJECT_ROOT"
echo "Target script  : $TARGET_SCRIPT"
echo "Schedule       : $CRON_TIME (minute hour, daily)"
echo
echo "Crontab entry:"
echo "  $CRON_LINE $MARKER"
echo

if [ "$DO_INSTALL" -eq 0 ]; then
    echo "Dry run only. Re-run with --install to install this entry."
    echo
    echo "Manual install:"
    echo "  crontab -e"
    echo "  # paste the line above at the bottom, save, quit"
    echo
    echo "Verify:"
    echo "  crontab -l | grep AXIS_ATTENDANCE_AUTO_MARK"
    exit 0
fi

# -- install (idempotent) -------------------------------------------
if ! command -v crontab >/dev/null 2>&1; then
    echo "ERROR: crontab command not found." >&2
    echo "       Install cron first: sudo apt-get install cron" >&2
    exit 1
fi

TMP_OLD="$(mktemp)"
TMP_NEW="$(mktemp)"
trap 'rm -f "$TMP_OLD" "$TMP_NEW"' EXIT

# Capture existing crontab (may be empty).
crontab -l > "$TMP_OLD" 2>/dev/null || true

# Drop any previous copy of OUR entry, keep everything else.
grep -v "$MARKER" "$TMP_OLD" > "$TMP_NEW" || true

# Append the fresh entry.
echo "$CRON_LINE $MARKER" >> "$TMP_NEW"

# Install.
if crontab "$TMP_NEW"; then
    echo "✔ Installed. Current entry:"
    crontab -l | grep "$MARKER" || echo "  (entry not visible — check crontab -l)"
    echo
    echo "Logs will appear in:"
    echo "  $PROJECT_ROOT/logs/attendance_cron.log"
    echo "  $PROJECT_ROOT/logs/attendance_cron_cronoutput.log"
    echo
    echo "First run will fire at $CRON_TIME tomorrow. To test right now:"
    echo "  bash \"$TARGET_SCRIPT\""
else
    echo "ERROR: crontab install failed." >&2
    exit 1
fi
