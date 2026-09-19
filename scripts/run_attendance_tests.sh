#!/usr/bin/env bash
# ATTENDANCE_TESTS_RUNNER_HELPER_V1
# -------------------------------------------------------------------
# Runs the attendance test suite (or a specific sub-set of it) with
# the correct, FULL dotted paths.
#
# Why this exists:
#   `python manage.py test axis_saas.tests.test_attendance_system.`
#   `AutoMarkedHandlingTests.test_dashboard_shows_auto_marked_count`
#   is very easy to truncate by accident.  A truncated name causes:
#
#       AttributeError: type object 'AutoMarkedHandlingTests' has no
#       attribute 'test_dashboard_shows_auto_marked_'.
#
#   Pasting that name from this file (or from the helper usage below)
#   avoids the typo entirely.
#
# Usage:
#   bash scripts/run_attendance_tests.sh              # full suite
#   bash scripts/run_attendance_tests.sh --quick      # the 2 fixed tests
#   bash scripts/run_attendance_tests.sh --auto-mark  # lazy-mark tests
#   bash scripts/run_attendance_tests.sh --dashboard  # dashboard tests
# -------------------------------------------------------------------

set -u

# Resolve this script's directory (works through symlinks).
SCRIPT_SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" >/dev/null 2>&1 && pwd)"
    SCRIPT_SOURCE="$(readlink "$SCRIPT_SOURCE")"
    [[ "$SCRIPT_SOURCE" != /* ]] && SCRIPT_SOURCE="$DIR/$SCRIPT_SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" >/dev/null 2>&1 && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

# Locate a usable python interpreter (prefer the project venv).
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
    echo "[runner] FATAL: no python interpreter found" >&2
    exit 127
fi

cd "$PROJECT_ROOT" || {
    echo "[runner] FATAL: cannot cd to $PROJECT_ROOT" >&2
    exit 1
}

MODE="${1:-all}"

case "$MODE" in
    --quick)
        echo "[runner] Running the 2 fixed dashboard tests (V1 + V2)..."
        "$PYTHON_BIN" manage.py test \
            axis_saas.tests.test_attendance_system.AdminDashboardViewTests.test_dashboard_auto_marked_column \
            axis_saas.tests.test_attendance_system.AutoMarkedHandlingTests.test_dashboard_shows_auto_marked_count \
            -v 2
        ;;
    --auto-mark)
        echo "[runner] Running LazyAutoMark* test classes..."
        "$PYTHON_BIN" manage.py test \
            axis_saas.tests.test_attendance_system.LazyAutoMarkUnitTests \
            axis_saas.tests.test_attendance_system.LazyAutoMarkLockTests \
            axis_saas.tests.test_attendance_system.LazyAutoMarkViewIntegrationTests \
            -v 2
        ;;
    --dashboard)
        echo "[runner] Running AdminDashboardViewTests..."
        "$PYTHON_BIN" manage.py test \
            axis_saas.tests.test_attendance_system.AdminDashboardViewTests \
            -v 2
        ;;
    all|--all)
        echo "[runner] Running the full attendance suite..."
        "$PYTHON_BIN" manage.py test \
            axis_saas.tests.test_attendance_system \
            -v 2
        ;;
    -h|--help)
        sed -n '2,25p' "$0"
        exit 0
        ;;
    *)
        echo "[runner] Unknown mode: $MODE" >&2
        echo "[runner] Try: --quick | --auto-mark | --dashboard | all" >&2
        exit 2
        ;;
esac
