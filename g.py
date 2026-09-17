#!/usr/bin/env python3
"""
axis_patcher.py — STAFF_PWA_TESTS_HOTFIX_1
==========================================

Fixes two failing tests in ``axis_saas/tests/test_staff_pwa.py``.

The previous pass's ``StaffUrlConfigTests`` iterated
``axis_saas.staff_urls.urlpatterns`` and accessed ``p.name`` on every
element. ``urlpatterns`` contains a mix of ``URLPattern`` and
``URLResolver`` objects — the latter comes from ``path('biometric/',
include(...))`` — and ``URLResolver`` has no ``.name`` attribute
until Django 5.1. On this project's Django it raises:

    AttributeError: 'URLResolver' object has no attribute 'name'

The two affected tests are:

  * ``test_no_duplicate_route_names``
  * ``test_required_pwa_routes_are_registered``

Both are rewritten to walk the list with ``getattr(p, 'name', None)``
and filter out ``None`` values. Behaviour is unchanged: duplicate
named routes are still detected, and the required PWA route names are
still asserted. The third test — ``test_no_duplicate_route_paths`` —
already used ``p.pattern`` which exists on both URLPattern and
URLResolver, so it is left untouched.

Idempotency
-----------
Uses exact-string anchor replacements. After a successful run the
anchors no longer match and re-running is a clean no-op.

Usage
-----
    python axis_patcher.py --dry-run --verbose
    python axis_patcher.py
    python axis_patcher.py --target-dir /path/to/fee_management
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


TEST_FILE_REL = Path("axis_saas") / "tests" / "test_staff_pwa.py"


# ---------------------------------------------------------------------------
# Fix 1 — test_no_duplicate_route_names
# ---------------------------------------------------------------------------
#
# Original code:
#     names = [p.name for p in mod.urlpatterns if p.name]
#
# ``URLResolver`` (from ``path('biometric/', include(...))``) has no
# ``.name`` attribute on Django < 5.1. Replaced with a getattr-based
# walk that tolerates both URLPattern and URLResolver elements.

DUP_NAMES_ANCHOR = (
    "    def test_no_duplicate_route_names(self):\n"
    "        import axis_saas.staff_urls as mod\n"
    "        names = [p.name for p in mod.urlpatterns if p.name]\n"
    "        duplicates = sorted({n for n in names if names.count(n) > 1})\n"
)

DUP_NAMES_REPLACEMENT = (
    "    def test_no_duplicate_route_names(self):\n"
    "        import axis_saas.staff_urls as mod\n"
    "        # STAFF_PWA_TESTS_HOTFIX_1: ``urlpatterns`` mixes\n"
    "        # URLPattern and URLResolver. URLResolver has no\n"
    "        # ``.name`` attribute on Django < 5.1, so walk with\n"
    "        # getattr and drop the unnamed entries.\n"
    "        names = [\n"
    "            getattr(p, 'name', None) for p in mod.urlpatterns\n"
    "        ]\n"
    "        names = [n for n in names if n]\n"
    "        duplicates = sorted({n for n in names if names.count(n) > 1})\n"
)


# ---------------------------------------------------------------------------
# Fix 2 — test_required_pwa_routes_are_registered
# ---------------------------------------------------------------------------

REQUIRED_ROUTES_ANCHOR = (
    "    def test_required_pwa_routes_are_registered(self):\n"
    "        import axis_saas.staff_urls as mod\n"
    "        names = {p.name for p in mod.urlpatterns if p.name}\n"
    "        for required in ('staff_manifest',\n"
)

REQUIRED_ROUTES_REPLACEMENT = (
    "    def test_required_pwa_routes_are_registered(self):\n"
    "        import axis_saas.staff_urls as mod\n"
    "        # STAFF_PWA_TESTS_HOTFIX_1: same URLResolver guard as\n"
    "        # the duplicate-names test above.\n"
    "        names = {\n"
    "            getattr(p, 'name', None) for p in mod.urlpatterns\n"
    "        }\n"
    "        names.discard(None)\n"
    "        for required in ('staff_manifest',\n"
)


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Log:
    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose

    def info(self, message: str) -> None:
        print(f"[{ts()}] {message}")

    def detail(self, message: str) -> None:
        if self.verbose:
            print(f"[{ts()}]   -> {message}")

    def error(self, message: str) -> None:
        print(f"[{ts()}] ERROR: {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Anchor-replacement helper
# ---------------------------------------------------------------------------

def patch_literal(
    target: Path,
    anchor: str,
    replacement: str,
    success_marker: str,
    log: Log,
    dry_run: bool,
) -> int:
    if not target.exists():
        log.error(f"target file does not exist: {target}")
        return 1
    if target.is_dir():
        log.error(f"target path is a directory: {target}")
        return 1

    try:
        src = target.read_text(encoding="utf-8")
    except OSError as exc:
        log.error(f"could not read {target}: {exc}")
        return 1

    # Idempotency: the success_marker is unique to the replacement.
    # When present and the anchor is gone, the file is already patched.
    if success_marker in src and anchor not in src:
        log.info(f"{target.name}: already patched (idempotent no-op)")
        return 0

    n = src.count(anchor)
    log.info(f"{target.name}: anchor matches = {n}")

    if n == 0:
        log.error(
            f"{target.name}: anchor not found — file may have drifted. "
            f"Nothing changed in this file."
        )
        return 1
    if n > 1:
        log.error(
            f"{target.name}: {n} anchor matches — refusing to guess. "
            f"Nothing changed in this file."
        )
        return 1

    patched = src.replace(anchor, replacement, 1)
    if patched == src:
        log.info(f"{target.name}: no change produced")
        return 0

    log.info(f"{target.name}: replacement prepared")

    if dry_run:
        log.detail(f"would write: {target}")
        return 0

    try:
        target.write_text(patched, encoding="utf-8")
    except OSError as exc:
        log.error(f"failed to write {target}: {exc}")
        return 1

    log.info(f"{target.name}: written")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "STAFF_PWA_TESTS_HOTFIX_1 — guard the staff_urls tests "
            "against URLResolver lacking a .name attribute."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without writing to disk.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show detailed output for every action.",
    )
    parser.add_argument(
        "--target-dir", default=".",
        help="Project root (default: current directory).",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    log = Log(args.verbose)

    try:
        root = Path(args.target_dir).expanduser().resolve()
    except OSError as exc:
        print(f"[{ts()}] ERROR: cannot resolve target dir: {exc}",
              file=sys.stderr)
        return 1

    log.info(f"target root : {root}")
    if args.dry_run:
        log.info("mode        : DRY RUN (no files will be written)")
    log.info("patcher     : STAFF_PWA_TESTS_HOTFIX_1")
    log.info("")

    if not root.exists() or not root.is_dir():
        log.error(
            f"target directory does not exist or is not a directory: {root}"
        )
        return 1

    target = root / TEST_FILE_REL
    rc = 0

    log.info(f"--- {TEST_FILE_REL} (test_no_duplicate_route_names) ---")
    rc |= patch_literal(
        target,
        DUP_NAMES_ANCHOR,
        DUP_NAMES_REPLACEMENT,
        success_marker="STAFF_PWA_TESTS_HOTFIX_1",
        log=log,
        dry_run=args.dry_run,
    )

    log.info("")
    log.info(f"--- {TEST_FILE_REL} (test_required_pwa_routes_are_registered) ---")
    rc |= patch_literal(
        target,
        REQUIRED_ROUTES_ANCHOR,
        REQUIRED_ROUTES_REPLACEMENT,
        success_marker="names.discard(None)",
        log=log,
        dry_run=args.dry_run,
    )

    log.info("")
    if args.dry_run:
        log.info("dry run complete - no changes were written")
    elif rc == 0:
        log.info("done.")
    else:
        log.info("done with errors - see above.")

    return rc


if __name__ == "__main__":
    sys.exit(main())
