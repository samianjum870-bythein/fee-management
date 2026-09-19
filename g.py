#!/usr/bin/env python3
"""
Hotfix: STAFF_LIST_MEGA_V1_HOTFIX_2
====================================

The V1 hotfix rewrote ``get_staff_list_context`` in ``staff.py`` and
introduced ``Count`` into its query. The existing import line at the top
of ``staff.py`` was::

    from django.db.models import Q, Sum

``Count`` was never imported, so every request to ``/portal/<schema>/staff/``
raised::

    NameError: name 'Count' is not defined.

This hotfix adds ``Count`` to that import line. Idempotent.
"""

import argparse
import ast
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def log(msg, level='INFO'):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] [{level}] {msg}', flush=True)


def parse_args():
    p = argparse.ArgumentParser(description='Staff list V1 hotfix 2')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--target-dir', default='.')
    return p.parse_args()


def validate_py(content, label):
    try:
        ast.parse(content)
        return True
    except SyntaxError as exc:
        log(f'Syntax check failed for {label}: {exc}', 'ERROR')
        return False


def patch_file(path, pattern, replacement, label, dry_run=False, required=True):
    path = Path(path)
    if not path.exists():
        if required:
            log(f'{path} not found - cannot patch {label}', 'ERROR')
        return False
    try:
        original = path.read_text(encoding='utf-8')
    except Exception as exc:
        log(f'Cannot read {path}: {exc}', 'ERROR')
        return False

    new_content, count = re.subn(
        pattern, replacement, original,
        flags=re.MULTILINE | re.DOTALL,
    )
    if count == 0:
        if replacement.strip()[:120] in original:
            log(f'{path}: "{label}" already applied')
            return True
        if required:
            log(f'Anchor for "{label}" not found in {path}', 'WARN')
        return False
    if path.suffix == '.py' and not validate_py(new_content, str(path)):
        return False
    if dry_run:
        log(f'[DRY-RUN] Would patch {path} ("{label}", {count} match(es))')
        return True
    path.write_text(new_content, encoding='utf-8')
    log(f'Patched {path} ("{label}", {count} match(es))')
    return True


def run_cmd(cmd, cwd=None, dry_run=False, timeout=300):
    if dry_run:
        log(f'[DRY-RUN] Would run: {" ".join(cmd)}')
        return True
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True,
                                text=True, timeout=timeout)
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        if result.returncode != 0:
            log(f'Command exited {result.returncode}', 'ERROR')
            return False
        return True
    except subprocess.TimeoutExpired:
        log('Command timed out', 'ERROR')
        return False
    except Exception as exc:
        log(f'Command failed: {exc}', 'ERROR')
        return False


# --------------------------------------------------------------------------

STAFF_PY_IMPORT_OLD = r"^from django\.db\.models import Q, Sum\s*$"
STAFF_PY_IMPORT_NEW = "from django.db.models import Q, Sum, Count"


def main():
    args = parse_args()
    target_dir = Path(args.target_dir).resolve()
    log(f'Target directory: {target_dir}')
    log(f'Dry run: {args.dry_run}')

    if not (target_dir / 'manage.py').exists():
        log('manage.py not found - is --target-dir correct?', 'ERROR')
        sys.exit(1)

    results = {}

    results['staff.py Count import'] = patch_file(
        target_dir / 'axis_saas' / 'views' / 'staff.py',
        STAFF_PY_IMPORT_OLD,
        STAFF_PY_IMPORT_NEW,
        'add Count to django.db.models import',
        dry_run=args.dry_run,
    )

    log('-' * 60)
    for key, ok in results.items():
        log(f'{key}: {"OK" if ok else "SKIPPED/FAILED"}')
    log('-' * 60)

    manage_py = str(target_dir / 'manage.py')
    run_cmd([sys.executable, manage_py, 'check', '--verbosity', '1'],
            cwd=str(target_dir), dry_run=args.dry_run)

    log('Done.')


if __name__ == '__main__':
    main()
