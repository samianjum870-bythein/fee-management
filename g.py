#!/usr/bin/env python3
"""
axis_patcher.py — STAFF_CSRF_META_FIX
======================================

Fixes the 403 "CSRF token from the 'X-Csrftoken' HTTP header has
incorrect length" error that the new staff attendance page hits when
saving.

Root cause
----------
``templates/mobile/staff/attendence.html`` reads the CSRF token from
a meta tag::

    function csrf(){ const m = document.querySelector('meta[name="csrf-token"]'); return m ? m.getAttribute('content') : ''; }

But ``templates/mobile/staff/base.html`` never emitted that meta tag —
only ``templates/tenant/base.html`` does. So ``csrf()`` returned an
empty string, the browser sent ``X-CSRFToken: ""``, and Django's CSRF
middleware rejected the request with::

    Forbidden (CSRF token from the 'X-Csrftoken' HTTP header has incorrect length.)

Fix
---
1.  Add ``<meta name="csrf-token" content="{{ csrf_token }}">`` to the
    <head> of ``templates/mobile/staff/base.html``.
2.  Harden the ``csrf()`` helper in
    ``templates/mobile/staff/attendence.html`` to fall back to the
    ``csrftoken`` cookie if the meta tag is ever missing. This is the
    standard Django recommendation and makes the page resilient.

Both changes are idempotent and scoped to two files.

Usage:
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


# --------------------------------------------------------------------- helpers

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError:
        log(f"  ERROR: not found: {path}")
        return None
    except Exception as e:
        log(f"  ERROR reading {path}: {e}")
        return None


def write_file(path, content, dry_run=False, label=""):
    if dry_run:
        log(f"  DRY-RUN: would write {path} ({label})")
        return True
    try:
        path.write_text(content, encoding='utf-8')
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as e:
        log(f"  ERROR writing {path}: {e}")
        return False


def replace_once(path, old, new, dry_run=False, label=""):
    content = read_file(path)
    if content is None:
        return False
    if old not in content:
        if new and new in content:
            log(f"  SKIP (already applied): {label}")
            return True
        log(f"  WARN: pattern not found for: {label}")
        return False
    new_content = content.replace(old, new, 1)
    return write_file(path, new_content, dry_run, label)


# --------------------------------------------------------------------- patches

BASE_REL = Path('templates') / 'mobile' / 'staff' / 'base.html'
ATT_REL  = Path('templates') / 'mobile' / 'staff' / 'attendence.html'

CSRF_META = '<meta name="csrf-token" content="{{ csrf_token }}">'


def patch_base_meta(root, args):
    """Insert the CSRF meta tag into the staff base <head>."""
    path = root / BASE_REL
    content = read_file(path)
    if content is None:
        return False

    if CSRF_META in content:
        log("  SKIP (already applied): csrf-token meta tag in staff base")
        return True

    # Anchor: the <title> line is stable and always present in the head.
    anchor = '    <title>{% block title %}AXIS Staff Portal{% endblock %}</title>\n'
    if anchor not in content:
        log("  WARN: title anchor not found in staff base.html")
        return False

    replacement = anchor + '    ' + CSRF_META + '\n'
    return replace_once(path, anchor, replacement, args.dry_run,
                        "add csrf-token meta to staff base.html")


def patch_attendance_csrf_helper(root, args):
    """Make the csrf() helper fall back to the csrftoken cookie."""
    path = root / ATT_REL
    content = read_file(path)
    if content is None:
        return False

    old = (
        "    function csrf(){ const m = document.querySelector('meta[name=\"csrf-token\"]'); "
        "return m ? m.getAttribute('content') : ''; }\n"
    )
    new = (
        "    function csrf(){\n"
        "        var m = document.querySelector('meta[name=\"csrf-token\"]');\n"
        "        if (m && m.getAttribute('content')) return m.getAttribute('content');\n"
        "        // Fallback: read the csrftoken cookie (Django's default).\n"
        "        var name = 'csrftoken=';\n"
        "        var parts = (document.cookie || '').split(';');\n"
        "        for (var i = 0; i < parts.length; i++) {\n"
        "            var c = parts[i].trim();\n"
        "            if (c.indexOf(name) === 0) return c.substring(name.length);\n"
        "        }\n"
        "        return '';\n"
        "    }\n"
    )

    if 'Fallback: read the csrftoken cookie' in content:
        log("  SKIP (already applied): csrf cookie fallback")
        return True

    return replace_once(path, old, new, args.dry_run,
                        "harden csrf() helper with cookie fallback")


# --------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(
        description=(
            'STAFF_CSRF_META_FIX — add the CSRF meta tag to the staff '
            'base template and harden the attendance page csrf() helper.'
        )
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview without writing.')
    parser.add_argument('--verbose', action='store_true',
                        help='Verbose output.')
    parser.add_argument('--target-dir', default='.',
                        help='Project root (default: current dir).')
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / 'manage.py').is_file():
        log(f"ERROR: manage.py not found in {root}. Wrong --target-dir?")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")

    log("--- 1/2: add csrf-token meta to staff base.html ---")
    ok1 = patch_base_meta(root, args)

    log("--- 2/2: harden csrf() helper in staff attendence.html ---")
    ok2 = patch_attendance_csrf_helper(root, args)

    log("=" * 60)
    if ok1 and ok2:
        log("All steps completed successfully.")
        if args.dry_run:
            log("Re-run without --dry-run to apply.")
        else:
            log("Now reload the staff attendance page in the browser")
            log("(hard refresh: Ctrl+Shift+R) and try saving again.")
        return 0

    log("One or more steps failed. See messages above.")
    return 2


if __name__ == '__main__':
    sys.exit(main())
