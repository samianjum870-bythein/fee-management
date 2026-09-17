#!/usr/bin/env python3
"""
axis_patcher.py
===============

Logs out ONLY the staff member whose password was just changed.
Nobody else is affected.

Three files are patched:

    1. axis_saas/views/staff_portal.py  ->  staff_change_password
       (teacher changes their own password from the staff portal)

    2. axis_saas/views/staff.py         ->  staff_reset_password
       (school admin resets a staff member's password from the
       tenant profile page)

    3. templates/mobile/staff/profile.html
       Handles the new `redirect` field so the teacher lands on the
       login page right after changing their own password.

What "logout" means here
------------------------
`Staff.logout_session()` (already defined in axis_saas/models.py)
does exactly what we need:

    * reads every session key registered for this exact
      (schema_name, staff_id) pair from the cache
    * flushes each of those DB-backed sessions
    * marks the cached session token as 'logged_out' so any stale
      tab fails `require_staff_login` on its very next request
    * clears the "online" flag and the session-key list

Other staff members' sessions are NEVER touched, because every cache
key is namespaced with the staff member's pk AND their schema name.

Idempotency
-----------
After this patch the exact anchor strings no longer match, so
re-running is a clean no-op.

Usage
-----
    python axis_patcher.py --dry-run --verbose
    python axis_patcher.py
    python axis_patcher.py --target-dir /path/to/fee_management --verbose
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


# --------------------------------------------------------------------------
# Target files
# --------------------------------------------------------------------------

STAFF_PORTAL_REL = Path("axis_saas") / "views" / "staff_portal.py"
STAFF_VIEWS_REL  = Path("axis_saas") / "views" / "staff.py"
PROFILE_TPL_REL  = Path("templates") / "mobile" / "staff" / "profile.html"


# --------------------------------------------------------------------------
# staff_portal.py — anchor + replacement
# --------------------------------------------------------------------------
# Exact anchor: end of staff_change_password() just before _ok().
# --------------------------------------------------------------------------

SP_ANCHOR = (
    "        StaffCredential.objects.filter(pk=credential.pk).update(\n"
    "            password=credential.password,\n"
    "            visible_password=new_password,\n"
    "        )\n"
    "\n"
    "    return _ok('Password updated successfully.')\n"
)

SP_REPLACEMENT = (
    "        StaffCredential.objects.filter(pk=credential.pk).update(\n"
    "            password=credential.password,\n"
    "            visible_password=new_password,\n"
    "        )\n"
    "\n"
    "    # PASSWORD_CHANGE_LOGOUT_V1: log out every active session for\n"
    "    # THIS staff member only. Nobody else is affected. Any other\n"
    "    # device that is still signed in is forced to re-authenticate\n"
    "    # with the new password.\n"
    "    with schema_context(schema_name):\n"
    "        _staff_to_logout = Staff.objects.filter(\n"
    "            pk=request.session.get('staff_id'),\n"
    "        ).first()\n"
    "        if _staff_to_logout is not None:\n"
    "            _staff_to_logout.logout_session()\n"
    "\n"
    "    _ok_msg = (\n"
    "        'Password updated successfully. '\n"
    "        'You have been signed out of all devices; please sign in '\n"
    "        'again with your new password.'\n"
    "    )\n"
    "    if is_ajax:\n"
    "        return JsonResponse({\n"
    "            'success': True,\n"
    "            'message': _ok_msg,\n"
    "            'redirect': '/portal/staff/login/',\n"
    "        })\n"
    "    messages.success(request, _ok_msg)\n"
    "    return redirect('staff_login')\n"
)


# --------------------------------------------------------------------------
# staff.py — anchor + replacement
# --------------------------------------------------------------------------
# Exact anchor: end of staff_reset_password() just before the return.
# --------------------------------------------------------------------------

SV_ANCHOR = (
    "        StaffCredential.objects.filter(pk=credential.pk).update(\n"
    "            password=credential.password,\n"
    "            visible_password=new_password,\n"
    "        )\n"
    "        if request.headers.get('x-requested-with') == 'XMLHttpRequest':\n"
    "            return JsonResponse({'success': True, 'message': 'Password reset successfully.'})\n"
    "        messages.success(request, 'Password reset successfully.')\n"
    "        return redirect('staff_profile', schema_name=schema_name, staff_id=staff_id)\n"
)

SV_REPLACEMENT = (
    "        StaffCredential.objects.filter(pk=credential.pk).update(\n"
    "            password=credential.password,\n"
    "            visible_password=new_password,\n"
    "        )\n"
    "\n"
    "    # PASSWORD_CHANGE_LOGOUT_V1: log out every active session for\n"
    "    # the target staff member ONLY. Nobody else is affected. Any\n"
    "    # other device that is still signed in is forced to\n"
    "    # re-authenticate with the new password.\n"
    "    with schema_context(schema_name):\n"
    "        _staff_to_logout = Staff.objects.filter(id=staff_id).first()\n"
    "        if _staff_to_logout is not None:\n"
    "            _staff_to_logout.logout_session()\n"
    "\n"
    "    _reset_msg = (\n"
    "        'Password reset successfully. '\n"
    "        'The staff member has been signed out of all devices.'\n"
    "    )\n"
    "    if request.headers.get('x-requested-with') == 'XMLHttpRequest':\n"
    "        return JsonResponse({'success': True, 'message': _reset_msg})\n"
    "    messages.success(request, _reset_msg)\n"
    "    return redirect('staff_profile', schema_name=schema_name, staff_id=staff_id)\n"
)


# --------------------------------------------------------------------------
# profile.html — JS handler for the new `redirect` field
# --------------------------------------------------------------------------

TPL_ANCHOR = (
    '            newPw.value = "";\n'
    '            confirmPw.value = "";\n'
    '            setPasswordVisibility(false);\n'
    '            showAlert("ok", message);\n'
)

TPL_REPLACEMENT = (
    '            newPw.value = "";\n'
    '            confirmPw.value = "";\n'
    '            setPasswordVisibility(false);\n'
    '            showAlert("ok", message);\n'
    '            /* PASSWORD_CHANGE_LOGOUT_V1: the backend has just\n'
    '               flushed every session for this staff member.\n'
    '               Follow the redirect hint it returns so the user\n'
    '               lands on the login page. */\n'
    '            if (data && data.redirect) {\n'
    '                setTimeout(function () {\n'
    '                    window.location.href = data.redirect;\n'
    '                }, 1200);\n'
    '            }\n'
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

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


def patch_literal(
    target: Path,
    anchor: str,
    replacement: str,
    success_marker: str,
    log: Log,
    dry_run: bool,
) -> int:
    """Replace one exact anchor string with a replacement. Returns 0
    on success or clean no-op, 1 on failure."""
    if not target.exists():
        log.error(f"target file does not exist: {target}")
        return 1
    if target.is_dir():
        log.error(f"target path is a directory, not a file: {target}")
        return 1

    try:
        src = target.read_text(encoding="utf-8")
    except OSError as exc:
        log.error(f"could not read {target}: {exc}")
        return 1

    # Idempotency: already patched.
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
        log.info(f"{target.name}: no change produced - nothing to do")
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


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Log out the specific staff member whose password was just "
            "changed or reset. Nobody else is affected."
        ),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing to disk.")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed output for every action.")
    parser.add_argument("--target-dir", default=".",
                        help="Project root (default: current directory).")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    log = Log(args.verbose)

    try:
        root = Path(args.target_dir).expanduser().resolve()
    except OSError as exc:  # pragma: no cover
        print(f"[{ts()}] ERROR: cannot resolve target dir: {exc}",
              file=sys.stderr)
        return 1

    log.info(f"target root : {root}")
    if args.dry_run:
        log.info("mode        : DRY RUN (no files will be written)")

    if not root.exists() or not root.is_dir():
        log.error(f"target directory does not exist or is not a directory: {root}")
        return 1

    rc = 0

    log.info("")
    log.info(f"--- {STAFF_PORTAL_REL} ---")
    rc |= patch_literal(
        root / STAFF_PORTAL_REL,
        SP_ANCHOR, SP_REPLACEMENT,
        success_marker="PASSWORD_CHANGE_LOGOUT_V1",
        log=log, dry_run=args.dry_run,
    )

    log.info("")
    log.info(f"--- {STAFF_VIEWS_REL} ---")
    rc |= patch_literal(
        root / STAFF_VIEWS_REL,
        SV_ANCHOR, SV_REPLACEMENT,
        success_marker="PASSWORD_CHANGE_LOGOUT_V1",
        log=log, dry_run=args.dry_run,
    )

    log.info("")
    log.info(f"--- {PROFILE_TPL_REL} ---")
    rc |= patch_literal(
        root / PROFILE_TPL_REL,
        TPL_ANCHOR, TPL_REPLACEMENT,
        success_marker="PASSWORD_CHANGE_LOGOUT_V1",
        log=log, dry_run=args.dry_run,
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
