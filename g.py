#!/usr/bin/env python3
"""
axis_patcher.py
===============
Adds a styled custom confirmation modal to the "Delete Timetable" button on
templates/tenant/timetable_periods.html, replacing the plain browser
`confirm()` dialog with:

  * A clear warning title/message
  * "Yes, I'm sure" button
  * "Cancel" button
  * Overlay backdrop + ESC to close

Only touches `templates/tenant/timetable_periods.html`.
Idempotent — safe to run multiple times.

Usage:
    python3 axis_patcher.py [--dry-run] [--verbose] [--target-dir=.]
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


# =====================================================================
# Patcher helpers
# =====================================================================
def _log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


def _read(path: Path):
    try:
        return path.read_text(encoding='utf-8')
    except Exception as e:
        _log(f"ERROR reading {path}: {e}")
        return None


def _write(path: Path, content: str, dry_run: bool, verbose: bool) -> bool:
    try:
        if dry_run:
            _log(f"DRY-RUN would write {path} ({len(content)} bytes)")
            return True
        path.write_text(content, encoding='utf-8')
        if verbose:
            _log(f"Wrote {path} ({len(content)} bytes)")
        else:
            _log(f"Wrote {path}")
        return True
    except Exception as e:
        _log(f"ERROR writing {path}: {e}")
        return False


def _replace_once(text, old, new, label):
    if old not in text:
        _log(f"  NOT FOUND anchor: {label}")
        return text, False
    return text.replace(old, new, 1), True


# =====================================================================
# Patch template
# =====================================================================
def patch_template(path: Path, dry_run: bool, verbose: bool) -> bool:
    _log(f"Patching {path}")
    content = _read(path)
    if content is None:
        return False

    original = content
    changes = 0

    # ---------------------------------------------------------------
    # 1) Add CSS for the confirm modal, right before </style>
    # ---------------------------------------------------------------
    if ".tt-confirm-overlay" not in content:
        css_anchor = (
            "    .tt-badge { display:inline-block; padding:0.15rem 0.6rem; "
            "background:var(--surface-alt); border-radius:1rem; "
            "font-size:0.75rem; color:var(--muted); margin-left:0.4rem; }\n"
            "</style>"
        )
        css_new = (
            "    .tt-badge { display:inline-block; padding:0.15rem 0.6rem; "
            "background:var(--surface-alt); border-radius:1rem; "
            "font-size:0.75rem; color:var(--muted); margin-left:0.4rem; }\n"
            "\n"
            "    /* Confirm modal */\n"
            "    .tt-confirm-overlay {\n"
            "        position: fixed; inset: 0;\n"
            "        background: rgba(0,0,0,0.6);\n"
            "        z-index: 100000;\n"
            "        display: none;\n"
            "        align-items: center;\n"
            "        justify-content: center;\n"
            "        backdrop-filter: blur(4px);\n"
            "        padding: 1rem;\n"
            "        animation: ttFadeIn 0.15s ease;\n"
            "    }\n"
            "    .tt-confirm-overlay.active { display: flex; }\n"
            "    .tt-confirm-box {\n"
            "        background: var(--surface, #fff);\n"
            "        border-radius: 1rem;\n"
            "        padding: 1.5rem 1.75rem;\n"
            "        max-width: 460px;\n"
            "        width: 100%;\n"
            "        box-shadow: 0 20px 60px rgba(0,0,0,0.35);\n"
            "        text-align: center;\n"
            "        animation: ttScaleIn 0.15s ease;\n"
            "    }\n"
            "    .tt-confirm-icon {\n"
            "        font-size: 2rem;\n"
            "        line-height: 1;\n"
            "        margin-bottom: 0.5rem;\n"
            "    }\n"
            "    .tt-confirm-title {\n"
            "        margin: 0 0 0.5rem 0;\n"
            "        font-size: 1.2rem;\n"
            "        font-weight: 700;\n"
            "        color: #b91c1c;\n"
            "    }\n"
            "    .tt-confirm-message {\n"
            "        color: var(--text, #333);\n"
            "        font-size: 0.92rem;\n"
            "        line-height: 1.5;\n"
            "        margin: 0 0 1.25rem 0;\n"
            "    }\n"
            "    .tt-confirm-hint {\n"
            "        color: var(--muted, #666);\n"
            "        font-size: 0.8rem;\n"
            "        margin: 0 0 1.25rem 0;\n"
            "    }\n"
            "    .tt-confirm-actions {\n"
            "        display: flex;\n"
            "        gap: 0.6rem;\n"
            "        justify-content: center;\n"
            "        flex-wrap: wrap;\n"
            "    }\n"
            "    .tt-confirm-actions button {\n"
            "        border: none;\n"
            "        padding: 0.6rem 1.3rem;\n"
            "        border-radius: 2rem;\n"
            "        font-weight: 600;\n"
            "        font-size: 0.9rem;\n"
            "        cursor: pointer;\n"
            "        transition: 0.15s;\n"
            "    }\n"
            "    .tt-confirm-yes { background: #dc2626; color: #fff; }\n"
            "    .tt-confirm-yes:hover { background: #b91c1c; }\n"
            "    .tt-confirm-cancel {\n"
            "        background: var(--surface-alt, #f3f4f6);\n"
            "        color: var(--text, #333);\n"
            "        border: 1px solid var(--border, #ddd) !important;\n"
            "    }\n"
            "    .tt-confirm-cancel:hover { background: var(--border, #e5e7eb); }\n"
            "    @keyframes ttFadeIn { from { opacity: 0; } to { opacity: 1; } }\n"
            "    @keyframes ttScaleIn {\n"
            "        from { transform: scale(0.95); opacity: 0; }\n"
            "        to { transform: scale(1); opacity: 1; }\n"
            "    }\n"
            "</style>"
        )
        content, ok = _replace_once(content, css_anchor, css_new, "confirm CSS")
        if ok:
            changes += 1
            _log("  + injected confirm modal CSS")
    else:
        _log("  - confirm modal CSS already present")

    # ---------------------------------------------------------------
    # 2) Add modal HTML, right after </div> of createOverlay
    # ---------------------------------------------------------------
    if 'id="ttConfirmOverlay"' not in content:
        html_anchor = (
            "            <div class=\"form-actions\">\n"
            "                <button type=\"button\" id=\"cancelBtn\" class=\"btn-secondary\">Cancel</button>\n"
            "                <button type=\"submit\" id=\"generateBtn\" class=\"btn-success\" disabled>Generate Periods Timetable</button>\n"
            "            </div>\n"
            "        </form>\n"
            "    </div>\n"
            "</div>\n"
        )
        html_new = (
            "            <div class=\"form-actions\">\n"
            "                <button type=\"button\" id=\"cancelBtn\" class=\"btn-secondary\">Cancel</button>\n"
            "                <button type=\"submit\" id=\"generateBtn\" class=\"btn-success\" disabled>Generate Periods Timetable</button>\n"
            "            </div>\n"
            "        </form>\n"
            "    </div>\n"
            "</div>\n"
            "\n"
            "<!-- CUSTOM CONFIRM MODAL -->\n"
            "<div id=\"ttConfirmOverlay\" class=\"tt-confirm-overlay\">\n"
            "    <div class=\"tt-confirm-box\">\n"
            "        <div class=\"tt-confirm-icon\">⚠️</div>\n"
            "        <h3 class=\"tt-confirm-title\" id=\"ttConfirmTitle\">Delete this timetable?</h3>\n"
            "        <p class=\"tt-confirm-message\" id=\"ttConfirmMessage\">\n"
            "            This action cannot be undone. The timetable will be permanently removed.\n"
            "        </p>\n"
            "        <p class=\"tt-confirm-hint\" id=\"ttConfirmHint\"></p>\n"
            "        <div class=\"tt-confirm-actions\">\n"
            "            <button type=\"button\" class=\"tt-confirm-cancel\" id=\"ttConfirmNoBtn\">Cancel</button>\n"
            "            <button type=\"button\" class=\"tt-confirm-yes\" id=\"ttConfirmYesBtn\">Yes, I'm sure</button>\n"
            "        </div>\n"
            "    </div>\n"
            "</div>\n"
        )
        content, ok = _replace_once(content, html_anchor, html_new, "confirm HTML")
        if ok:
            changes += 1
            _log("  + injected confirm modal HTML")
    else:
        _log("  - confirm modal HTML already present")

    # ---------------------------------------------------------------
    # 3) Add JS helper showTtConfirm() just before the DOM refs block
    # ---------------------------------------------------------------
    if "function showTtConfirm(" not in content:
        js_anchor = (
            "    // ---- DOM refs ----\n"
            "    const createBtn         = document.getElementById('createTimetableBtn');\n"
        )
        js_new = (
            "    // ---- Custom confirm dialog (returns Promise<boolean>) ----\n"
            "    function showTtConfirm(opts) {\n"
            "        opts = opts || {};\n"
            "        const title    = opts.title    || 'Are you sure?';\n"
            "        const message  = opts.message  || '';\n"
            "        const hint     = opts.hint     || '';\n"
            "        const yesLabel = opts.yesLabel || \"Yes, I'm sure\";\n"
            "        const noLabel  = opts.noLabel  || 'Cancel';\n"
            "        const overlayEl = document.getElementById('ttConfirmOverlay');\n"
            "        const titleEl   = document.getElementById('ttConfirmTitle');\n"
            "        const msgEl     = document.getElementById('ttConfirmMessage');\n"
            "        const hintEl    = document.getElementById('ttConfirmHint');\n"
            "        const yesBtn    = document.getElementById('ttConfirmYesBtn');\n"
            "        const noBtn     = document.getElementById('ttConfirmNoBtn');\n"
            "        titleEl.textContent = title;\n"
            "        msgEl.textContent   = message;\n"
            "        hintEl.textContent  = hint;\n"
            "        hintEl.style.display = hint ? 'block' : 'none';\n"
            "        yesBtn.textContent  = yesLabel;\n"
            "        noBtn.textContent   = noLabel;\n"
            "        overlayEl.classList.add('active');\n"
            "        return new Promise(function (resolve) {\n"
            "            function cleanup(result) {\n"
            "                overlayEl.classList.remove('active');\n"
            "                yesBtn.removeEventListener('click', onYes);\n"
            "                noBtn.removeEventListener('click', onNo);\n"
            "                overlayEl.removeEventListener('click', onOverlay);\n"
            "                document.removeEventListener('keydown', onKey);\n"
            "                resolve(result);\n"
            "            }\n"
            "            function onYes() { cleanup(true); }\n"
            "            function onNo()  { cleanup(false); }\n"
            "            function onOverlay(e) { if (e.target === overlayEl) cleanup(false); }\n"
            "            function onKey(e) { if (e.key === 'Escape') cleanup(false); }\n"
            "            yesBtn.addEventListener('click', onYes);\n"
            "            noBtn.addEventListener('click', onNo);\n"
            "            overlayEl.addEventListener('click', onOverlay);\n"
            "            document.addEventListener('keydown', onKey);\n"
            "        });\n"
            "    }\n"
            "\n"
            "    // ---- DOM refs ----\n"
            "    const createBtn         = document.getElementById('createTimetableBtn');\n"
        )
        content, ok = _replace_once(content, js_anchor, js_new, "showTtConfirm helper")
        if ok:
            changes += 1
            _log("  + injected showTtConfirm() helper")
    else:
        _log("  - showTtConfirm() already present")

    # ---------------------------------------------------------------
    # 4) Replace the plain confirm() call in the delete-btn handler
    # ---------------------------------------------------------------
    old_del = (
        "        header.querySelector('.delete-btn').addEventListener('click', function () {\n"
        "            if (!confirm('Delete this timetable?')) return;\n"
        "            deleteTimetable(idx);\n"
        "        });\n"
    )
    new_del = (
        "        header.querySelector('.delete-btn').addEventListener('click', async function () {\n"
        "            const dayCount = (tt.days || []).length;\n"
        "            const ok = await showTtConfirm({\n"
        "                title: 'Delete this timetable?',\n"
        "                message: 'You are about to permanently delete \"' + (tt.title || 'this timetable') + '\" '\n"
        "                       + '(Label: ' + (tt.label || '-') + ').\\n\\n'\n"
        "                       + 'This will remove all its ' + dayCount + ' day(s) and period timings.',\n"
        "                hint: 'This action cannot be undone.',\n"
        "                yesLabel: \"Yes, I'm sure\",\n"
        "                noLabel: 'Cancel'\n"
        "            });\n"
        "            if (!ok) return;\n"
        "            deleteTimetable(idx);\n"
        "        });\n"
    )
    if old_del in content:
        content = content.replace(old_del, new_del, 1)
        changes += 1
        _log("  + replaced plain confirm() with custom modal")
    else:
        if "await showTtConfirm({" in content:
            _log("  - delete-btn already uses showTtConfirm()")
        else:
            _log("  WARN: could not find original delete-btn handler")

    # ---------------------------------------------------------------
    # Write
    # ---------------------------------------------------------------
    if changes == 0:
        _log("  no changes needed")
        return True
    if content != original:
        return _write(path, content, dry_run, verbose)
    return True


# =====================================================================
# main
# =====================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description='Add styled confirm modal to the Delete Timetable button.'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview only, do not write files.')
    parser.add_argument('--verbose', action='store_true',
                        help='Verbose output.')
    parser.add_argument('--target-dir', default='.',
                        help='Project root (default: current directory).')
    args = parser.parse_args()

    target = Path(args.target_dir).resolve()
    _log(f"Target dir: {target}")
    _log(f"Dry-run:    {args.dry_run}")
    _log(f"Verbose:    {args.verbose}")
    print('-' * 60)

    if not (target / 'manage.py').exists():
        _log("WARNING: manage.py not found at target root. Continuing anyway.")

    tmpl = target / 'templates' / 'tenant' / 'timetable_periods.html'
    if not tmpl.exists():
        _log(f"ERROR: {tmpl} not found.")
        return 2

    ok = patch_template(tmpl, args.dry_run, args.verbose)

    print('-' * 60)
    if ok:
        _log("DONE.")
        if not args.dry_run:
            _log("Hard-refresh the Periods page: Ctrl+Shift+R")
            _log("Then click the '× Delete' button on any generated timetable.")
        return 0
    _log("FAILED: could not patch template.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
