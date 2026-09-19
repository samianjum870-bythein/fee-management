#!/usr/bin/env python3
"""
axis_patcher.py — ATTENDANCE_TESTS_RUNNER_HELPER_V1
====================================================

Context
-------
The actual fix is ALREADY in place.  The current
``axis_saas/tests/test_attendance_system.py`` contains BOTH markers:

    * ATTENDANCE_AUTO_MARK_LAZY_TEST_FIX_V1
      (AdminDashboardViewTests.test_dashboard_auto_marked_column)
    * ATTENDANCE_AUTO_MARK_LAZY_TEST_FIX_V2
      (AutoMarkedHandlingTests.test_dashboard_shows_auto_marked_count)

The failure you just saw was not a code failure — it was a typo in the
``manage.py test`` command:

    python manage.py test ... \
        AutoMarkedHandlingTests.test_dashboard_shows_auto_marked_

                                          ^^^^^
                              truncated; missing "count"

Django could not find a method named ``test_dashboard_shows_auto_marked_``
and raised ``AttributeError``.  Running the FULL method name fixes it:

    python manage.py test axis_saas.tests.test_attendance_system.\\
        AutoMarkedHandlingTests.test_dashboard_shows_auto_marked_count

This patcher
------------
Adds a small shell helper ``scripts/run_attendance_tests.sh`` so the two
long, easy-to-truncate test names live in one place and you never have
to type them by hand again.

It also VERIFIES (read-only) that both FIX_V1 and FIX_V2 markers are
present in the test file — a quick sanity check after any git operation
or file copy.

Files created
-------------
  scripts/run_attendance_tests.sh

Idempotent — safe to run multiple times.  Will NOT overwrite an
existing ``scripts/run_attendance_tests.sh``.

Usage
-----
    python axis_patcher.py --dry-run --verbose
    python axis_patcher.py
    python axis_patcher.py --target-dir /home/sami/fee_management

After running
-------------
    bash scripts/run_attendance_tests.sh              # full suite
    bash scripts/run_attendance_tests.sh --quick      # 2 fixed tests
    bash scripts/run_attendance_tests.sh --auto-mark  # lazy-mark tests
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Log:
    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose
        self.errors = 0

    def info(self, msg: str) -> None:
        print(f"[{_ts()}] {msg}")

    def detail(self, msg: str) -> None:
        if self.verbose:
            print(f"[{_ts()}]   -> {msg}")

    def warn(self, msg: str) -> None:
        print(f"[{_ts()}] WARN: {msg}", file=sys.stderr)

    def error(self, msg: str) -> None:
        self.errors += 1
        print(f"[{_ts()}] ERROR: {msg}", file=sys.stderr)


def read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def write_text(
    path: Path,
    content: str,
    dry_run: bool,
    log: Log,
    overwrite: bool = False,
) -> bool:
    if path.exists() and not overwrite:
        log.warn(f"refusing to overwrite existing file: {path}")
        return False
    if dry_run:
        log.detail(f"would write: {path}")
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        try:
            path.chmod(0o755)
        except OSError:
            pass
        log.detail(f"wrote: {path}")
        return True
    except OSError as exc:
        log.error(f"failed to write {path}: {exc}")
        return False


# =====================================================================
# STEP 1 — sanity-check the two fix markers are present in the test file
# =====================================================================

TEST_FILE = "axis_saas/tests/test_attendance_system.py"

MARKER_V1 = "ATTENDANCE_AUTO_MARK_LAZY_TEST_FIX_V1"
MARKER_V2 = "ATTENDANCE_AUTO_MARK_LAZY_TEST_FIX_V2"


def verify_fixes(root: Path, log: Log) -> None:
    log.info("STEP 1 — verify both lazy-mark fix markers are present")
    path = root / TEST_FILE
    src = read_text(path)
    if src is None:
        log.error(f"missing file: {path}")
        return

    for name, marker in (
        ("FIX_V1 (AdminDashboardViewTests.test_dashboard_auto_marked_column)",
         MARKER_V1),
        ("FIX_V2 (AutoMarkedHandlingTests.test_dashboard_shows_auto_marked_count)",
         MARKER_V2),
    ):
        if marker in src:
            log.detail(f"OK  {name}")
        else:
            log.warn(
                f"{name}: marker {marker!r} is MISSING from "
                f"{TEST_FILE}.  Re-run the corresponding patcher."
            )

    # Sanity: also confirm the exact long test method names exist so the
    # helper script can rely on them.
    for method_name in (
        "def test_dashboard_auto_marked_column",
        "def test_dashboard_shows_auto_marked_count",
    ):
        if method_name in src:
            log.detail(f"OK  method present: {method_name}")
        else:
            log.error(f"method MISSING: {method_name}")


# =====================================================================
# STEP 2 — create the runner helper shell script
# =====================================================================

RUNNER = r'''#!/usr/bin/env bash
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
'''


def create_runner(root: Path, log: Log, dry_run: bool) -> None:
    log.info("STEP 2 — create scripts/run_attendance_tests.sh")
    path = root / "scripts" / "run_attendance_tests.sh"
    write_text(path, RUNNER, dry_run, log, overwrite=False)


# =====================================================================
# Verify
# =====================================================================

def verify(root: Path, log: Log) -> None:
    log.info("VERIFY")
    path = root / "scripts" / "run_attendance_tests.sh"
    if not path.exists():
        log.error(f"verify: missing {path}")
        return
    head = read_text(path) or ""
    if not head.startswith("#!/usr/bin/env bash"):
        log.warn(f"{path.name}: unexpected shebang")
    else:
        log.detail(f"OK  {path.relative_to(root)}")

    # The runner must contain the FULL, un-truncated method names.
    required = [
        "test_dashboard_auto_marked_column",
        "test_dashboard_shows_auto_marked_count",
        "LazyAutoMarkUnitTests",
        "LazyAutoMarkLockTests",
        "LazyAutoMarkViewIntegrationTests",
    ]
    missing = [n for n in required if n not in head]
    if missing:
        for n in missing:
            log.error(f"verify: runner is missing {n!r}")
    else:
        log.detail(f"OK  runner contains all {len(required)} expected names")


# =====================================================================
# Main
# =====================================================================

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ATTENDANCE_TESTS_RUNNER_HELPER_V1 — sanity-check the two "
            "lazy-mark fix markers and install a small runner script "
            "with the correct, FULL test names so you never truncate "
            "them again."
        ),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing.")
    parser.add_argument("--verbose", action="store_true",
                        help="Show every file touched.")
    parser.add_argument("--target-dir", default=".",
                        help="Project root (default: current directory).")
    args = parser.parse_args(argv)

    log = Log(args.verbose)
    root = Path(args.target_dir).expanduser().resolve()

    log.info(f"target root : {root}")
    if args.dry_run:
        log.info("mode        : DRY RUN (no files will be written)")
    log.info("patcher     : ATTENDANCE_TESTS_RUNNER_HELPER_V1")
    log.info("")

    if not root.exists() or not root.is_dir():
        log.error(f"invalid target directory: {root}")
        return 1

    if not (root / "manage.py").exists():
        log.warn(
            f"manage.py not found in {root} — is this the project root?"
        )

    steps = [
        ("verify_fixes", verify_fixes),
        ("create_runner", create_runner),
    ]

    for name, fn in steps:
        try:
            fn(root, log, args.dry_run) if name == "create_runner" else fn(root, log)
        except Exception as exc:  # noqa: BLE001
            log.error(f"{name} raised: {exc!r}")
        log.info("")

    try:
        verify(root, log)
    except Exception as exc:  # noqa: BLE001
        log.error(f"verify raised: {exc!r}")

    log.info("")
    if args.dry_run:
        log.info("dry run complete — no changes were written")
    elif log.errors == 0:
        log.info("done.")
        log.info("")
        log.info("Your previous failure was a TYPO in the test name —")
        log.info("you ran `test_dashboard_shows_auto_marked_` but the")
        log.info("real method is `test_dashboard_shows_auto_marked_count`.")
        log.info("")
        log.info("Use the runner from now on:")
        log.info(f"    cd {root}")
        log.info("    bash scripts/run_attendance_tests.sh --quick")
        log.info("")
        log.info("Or if you prefer to type it manually, the FULL command is:")
        log.info(
            "    python manage.py test "
            "axis_saas.tests.test_attendance_system."
            "AutoMarkedHandlingTests.test_dashboard_shows_auto_marked_count "
            "-v 2"
        )
    else:
        log.info(f"done with {log.errors} error(s) — see above.")
    return 0 if log.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
