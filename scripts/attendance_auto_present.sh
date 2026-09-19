#!/usr/bin/env bash
# ATTENDANCE_CRON_SETUP_V1
# -------------------------------------------------------------------
# Portable cron target for `manage.py attendance_auto_present`.
#
# * Finds the project root (parent of this script's directory) so the
#   path in crontab never has to be hard-coded.
# * Picks the venv python automatically (venv/ then .venv/).
# * Logs to logs/attendance_cron.log so you can audit its runs.
# * Safe to invoke by hand for testing.
#
# Usage:
#     bash scripts/attendance_auto_present.sh
#     bash scripts/attendance_auto_present.sh --days 30
# -------------------------------------------------------------------

set -u

# Resolve this script's real directory (works through symlinks).
SCRIPT_SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" >/dev/null 2>&1 && pwd)"
    SCRIPT_SOURCE="$(readlink "$SCRIPT_SOURCE")"
    [[ "$SCRIPT_SOURCE" != /* ]] && SCRIPT_SOURCE="$DIR/$SCRIPT_SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" >/dev/null 2>&1 && pwd)"

# Project root = parent of scripts/
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

# Locate a usable python interpreter.
PYTHON_BIN=""
for candidate in \
        "$PROJECT_ROOT/venv/bin/python" \
        "$PROJECT_ROOT/.venv/bin/python" \
        "$PROJECT_ROOT/env/bin/python" \
        "$(command -v python3 2>/dev/null)" \
        "$(command -v python 2>/dev/null)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "[attendance_cron] FATAL: no python interpreter found" >&2
    exit 127
fi

# Make sure the log directory exists.
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/attendance_cron.log"

# Timestamp header — helps audit the log at a glance.
{
    echo "=========================================================="
    echo "[attendance_cron] $(date '+%Y-%m-%d %H:%M:%S %Z') starting"
    echo "[attendance_cron] project=$PROJECT_ROOT"
    echo "[attendance_cron] python=$PYTHON_BIN"
} >> "$LOG_FILE"

cd "$PROJECT_ROOT" || {
    echo "[attendance_cron] FATAL: cannot cd to $PROJECT_ROOT" >> "$LOG_FILE"
    exit 1
}

# ATTENDANCE_CRON_SAFETY_V1
# ---------------------------------------------------------------
# Default to a 3-day catch-up window. A single missed night (machine
# off, deploy restart, transient DB error) self-heals on the next run
# instead of leaving that day permanently unmarked. Callers can still
# override by passing --days N explicitly, or by setting the env var
# AXIS_ATTENDANCE_DAYS.
# ---------------------------------------------------------------
_HAS_DAYS=0
for _a in "$@"; do
    if [ "$_a" = "--days" ]; then
        _HAS_DAYS=1
        break
    fi
done
if [ "$_HAS_DAYS" -eq 0 ]; then
    _DEFAULT_DAYS="${AXIS_ATTENDANCE_DAYS:-3}"
    set -- --days "$_DEFAULT_DAYS" "$@"
fi

"$PYTHON_BIN" manage.py attendance_auto_present "$@" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "[attendance_cron] exit=$EXIT_CODE" >> "$LOG_FILE"
exit $EXIT_CODE
