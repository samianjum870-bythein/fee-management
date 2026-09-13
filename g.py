#!/usr/bin/env python3
"""
axis_patcher.py
===============

TIMETABLE_SEARCH_FIX_V1
-----------------------

The live search box on the Periods Timetable page wasn't filtering.
Two likely failure modes on the applied V2 patch:

  1. The `input` event listener may not have attached (some browsers/
     extensions, or an element-reference mismatch, silently no-op the
     `addEventListener` call). With no listener, typing does nothing.

  2. Even when it does attach, there was no visual feedback: typing a
     query that matched nothing left all cards visible if the class
     matcher or `dataset` key didn't line up. Hard to tell "not working"
     from "no matches".

This patcher replaces the search wiring with a belt-and-suspenders
implementation that is hard to break:

  * The search input gets an inline `oninput` fallback in the HTML, so
    even if `addEventListener` fails to run, a global function is
    invoked on every keystroke.
  * A second, document-level `input` delegation listener catches the
    same event if the direct listener is somehow detached.
  * `applyTtFilter` is rewritten to iterate over `container.children`
    directly (no `querySelectorAll('.card[data-tt-id]')` class-match
    dependency), use `style.setProperty('display', 'none')` /
    `removeProperty('display')` for hide/show, and count matches.
  * A "No timetables match your search" card appears when the query
    filters everything out.
  * The search wrap is made visible by default (removed the inline
    `display:none`); renderAll still toggles it off when the list is
    empty.
  * Optional `window.TIMETABLE_DEBUG = true` in DevTools logs each
    filter pass (query, total cards, visible count) for diagnosis.

Files modified:
  - templates/tenant/timetable_periods.html

Idempotent. Safe to run multiple times.

Usage:
    python3 axis_patcher.py [--dry-run] [--verbose] [--target-dir=.]
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path


MARKER = "TIMETABLE_SEARCH_FIX_V1"


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        if verbose:
            _log(f"Wrote {path} ({len(content)} bytes)")
        else:
            _log(f"Wrote {path}")
        return True
    except Exception as e:
        _log(f"ERROR writing {path}: {e}")
        return False


# =====================================================================
# STEP 1 — templates/tenant/timetable_periods.html
# =====================================================================

# ---- 1a. Search wrap: remove display:none, add inline oninput, add hint ----
SEARCH_HTML_OLD = (
    "<!-- SEARCH (TIMETABLE_PERIODS_V2) -->\n"
    "<div id=\"ttSearchWrap\" class=\"form-group\" style=\"max-width:380px; margin-bottom:1rem; display:none;\">\n"
    "    <input type=\"text\" id=\"ttSearchInput\" class=\"form-control\"\n"
    "           placeholder=\"\U0001f50d Search timetables by title or label...\">\n"
    "</div>\n"
)

SEARCH_HTML_NEW = (
    "<!-- SEARCH (TIMETABLE_SEARCH_FIX_V1) -->\n"
    "<div id=\"ttSearchWrap\" class=\"form-group\" style=\"max-width:420px; margin-bottom:1rem;\">\n"
    "    <input type=\"text\" id=\"ttSearchInput\" class=\"form-control\"\n"
    "           placeholder=\"\U0001f50d Search timetables by title or label...\"\n"
    "           autocomplete=\"off\" autocorrect=\"off\" autocapitalize=\"off\" spellcheck=\"false\"\n"
    "           oninput=\"window.__ttApplyFilter__ &amp;&amp; window.__ttApplyFilter__()\">\n"
    "    <small class=\"text-muted\" style=\"display:block; margin-top:0.35rem; font-size:0.72rem;\">\n"
    "        Filters the list below by matching titles and labels (case-insensitive).\n"
    "    </small>\n"
    "</div>\n"
    "\n"
    "<!-- NO-RESULTS (TIMETABLE_SEARCH_FIX_V1) -->\n"
    "<div id=\"ttSearchNoResults\" class=\"card\" style=\"display:none; text-align:center; color:var(--muted);\">\n"
    "    <p style=\"margin:0;\">No timetables match your search.</p>\n"
    "</div>\n"
)


# ---- 1b. Replace the search block (function + wiring) ----
SEARCH_FN_OLD = (
    "    // ================================================================\n"
    "    // SEARCH (TIMETABLE_PERIODS_V2)\n"
    "    // ================================================================\n"
    "    function applyTtFilter() {\n"
    "        if (!ttSearchInput) return;\n"
    "        const q = (ttSearchInput.value || '').trim().toLowerCase();\n"
    "        container.querySelectorAll('.card[data-tt-id]').forEach(function (card) {\n"
    "            const hay = card.dataset.searchText || '';\n"
    "            card.style.display = (!q || hay.indexOf(q) !== -1) ? '' : 'none';\n"
    "        });\n"
    "    }\n"
    "    if (ttSearchInput) {\n"
    "        ttSearchInput.addEventListener('input', applyTtFilter);\n"
    "    }\n"
)

SEARCH_FN_NEW = (
    "    // ================================================================\n"
    "    // SEARCH (TIMETABLE_SEARCH_FIX_V1)\n"
    "    // ================================================================\n"
    "    // Rewritten to be bulletproof:\n"
    "    //   * iterates container.children directly (no reliance on\n"
    "    //     querySelectorAll class matches)\n"
    "    //   * hides via style.setProperty / shows via removeProperty\n"
    "    //   * counts visible cards and toggles a 'no results' element\n"
    "    //   * logs each pass when window.TIMETABLE_DEBUG is true\n"
    "    function applyTtFilter() {\n"
    "        if (!ttSearchInput || !container) return;\n"
    "        const q = (ttSearchInput.value || '').trim().toLowerCase();\n"
    "        const noResultsEl = document.getElementById('ttSearchNoResults');\n"
    "        const kids = container.children;\n"
    "        let total = 0;\n"
    "        let visible = 0;\n"
    "        for (let i = 0; i < kids.length; i++) {\n"
    "            const card = kids[i];\n"
    "            // Only consider real timetable cards.\n"
    "            if (!card || !card.classList || !card.classList.contains('card')) continue;\n"
    "            if (!card.dataset || !card.dataset.ttId) continue;\n"
    "            total += 1;\n"
    "            const hay = card.dataset.searchText || '';\n"
    "            const match = !q || hay.indexOf(q) !== -1;\n"
    "            if (match) {\n"
    "                card.style.removeProperty('display');\n"
    "                visible += 1;\n"
    "            } else {\n"
    "                card.style.setProperty('display', 'none', 'important');\n"
    "            }\n"
    "        }\n"
    "        if (noResultsEl) {\n"
    "            noResultsEl.style.display = (q && total > 0 && visible === 0) ? 'block' : 'none';\n"
    "        }\n"
    "        if (window.TIMETABLE_DEBUG) {\n"
    "            console.log('[TimetableSearch] q=', JSON.stringify(q),\n"
    "                        'total=', total, 'visible=', visible);\n"
    "        }\n"
    "    }\n"
    "\n"
    "    // Expose so the inline oninput attribute can call it as a fallback.\n"
    "    window.__ttApplyFilter__ = applyTtFilter;\n"
    "\n"
    "    // Primary wiring: direct listener on the input.\n"
    "    if (ttSearchInput) {\n"
    "        ttSearchInput.addEventListener('input', applyTtFilter);\n"
    "        ttSearchInput.addEventListener('change', applyTtFilter);\n"
    "        ttSearchInput.addEventListener('keyup', applyTtFilter);\n"
    "    }\n"
    "\n"
    "    // Fallback wiring: document-level delegation. Even if the direct\n"
    "    // listener never attaches (unlikely, but cheap insurance), this\n"
    "    // catches the same event from anywhere on the page.\n"
    "    document.addEventListener('input', function (e) {\n"
    "        if (e && e.target && e.target.id === 'ttSearchInput') {\n"
    "            applyTtFilter();\n"
    "        }\n"
    "    });\n"
)


# ---- 1c. renderAll: use .style.removeProperty / setProperty for wrap ----
RENDER_TOGGLE_OLD = (
    "        emptyState.style.display = 'block';\n"
    "        if (ttSearchWrap) ttSearchWrap.style.display = 'none';\n"
    "        return;\n"
    "    }\n"
    "    emptyState.style.display = 'none';\n"
    "    // TIMETABLE_PERIODS_V2: show search whenever there is at least one\n"
    "    // timetable.\n"
    "    if (ttSearchWrap) ttSearchWrap.style.display = 'block';\n"
)

RENDER_TOGGLE_NEW = (
    "        emptyState.style.display = 'block';\n"
    "        if (ttSearchWrap) ttSearchWrap.style.setProperty('display', 'none');\n"
    "        // TIMETABLE_SEARCH_FIX_V1: also hide the 'no results' block\n"
    "        // when there are simply no timetables.\n"
    "        try {\n"
    "            const _nr = document.getElementById('ttSearchNoResults');\n"
    "            if (_nr) _nr.style.setProperty('display', 'none');\n"
    "        } catch (e) {}\n"
    "        return;\n"
    "    }\n"
    "    emptyState.style.display = 'none';\n"
    "    // TIMETABLE_SEARCH_FIX_V1: keep the search bar visible whenever\n"
    "    // there is at least one timetable.\n"
    "    if (ttSearchWrap) ttSearchWrap.style.removeProperty('display');\n"
)


# ---- 1d. renderAll: apply filter at end (already does; keep same) ----
# No change needed: renderAll already calls applyTtFilter() at the end via
# the guard `if (typeof applyTtFilter === 'function') applyTtFilter();`.
# That guard still holds because applyTtFilter is a hoisted function
# declaration.

# ---- 1e. Init: call applyTtFilter once at the very end ----
INIT_OLD = (
    "    // ================================================================\n"
    "    // INIT\n"
    "    // ================================================================\n"
    "    renderAll();\n"
)

INIT_NEW = (
    "    // ================================================================\n"
    "    // INIT\n"
    "    // ================================================================\n"
    "    renderAll();\n"
    "\n"
    "    // TIMETABLE_SEARCH_FIX_V1: one extra pass after the browser has\n"
    "    // finished attaching listeners, in case anything above raced.\n"
    "    try {\n"
    "        if (ttSearchInput) {\n"
    "            ttSearchInput.addEventListener('input', applyTtFilter);\n"
    "            if (ttSearchInput.value) applyTtFilter();\n"
    "        }\n"
    "    } catch (e) {}\n"
)


TEMPLATE_EDITS = [
    ("search wrap + no-results element", SEARCH_HTML_OLD,   SEARCH_HTML_NEW),
    ("rewrite applyTtFilter + wiring",   SEARCH_FN_OLD,     SEARCH_FN_NEW),
    ("renderAll: display toggling",      RENDER_TOGGLE_OLD, RENDER_TOGGLE_NEW),
    ("INIT: extra listener pass",        INIT_OLD,          INIT_NEW),
]


def patch_template(path: Path, dry_run: bool, verbose: bool) -> bool:
    _log(f"Patching template: {path}")
    content = _read(path)
    if content is None:
        return False

    if MARKER in content:
        _log("  - already patched, skipping")
        return True

    applied = 0
    for label, old, new in TEMPLATE_EDITS:
        if old not in content:
            _log(f"  WARN: anchor not found for '{label}'")
            continue
        content = content.replace(old, new, 1)
        _log(f"  + {label}")
        applied += 1

    # Marker just after {% block body %} for idempotency detection.
    anchor = "{% block body %}\n"
    if anchor in content and MARKER not in content:
        content = content.replace(anchor, anchor + "{# " + MARKER + " #}\n", 1)

    if applied == 0:
        _log("  WARN: no edits were applied")
    return _write(path, content, dry_run, verbose)


# =====================================================================
# main
# =====================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "TIMETABLE_SEARCH_FIX_V1 — Rewrite the Periods Timetable live "
            "search with defensive wiring (inline oninput + direct listener "
            "+ document delegation), a 'no results' state, and optional "
            "debug logging."
        )
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--target-dir', default='.')
    args = parser.parse_args()

    target = Path(args.target_dir).resolve()
    _log(f"Target dir: {target}")
    _log(f"Dry-run:    {args.dry_run}")
    _log(f"Verbose:    {args.verbose}")
    print('-' * 60)

    if not (target / 'manage.py').exists():
        _log("WARNING: manage.py not found at target root. Continuing anyway.")

    ok = True

    print('-' * 60)
    _log("STEP 1: templates/tenant/timetable_periods.html")
    ok &= patch_template(
        target / 'templates' / 'tenant' / 'timetable_periods.html',
        args.dry_run, args.verbose,
    )

    print('-' * 60)
    if ok:
        _log("DONE.")
        if not args.dry_run:
            _log("")
            _log("NEXT STEPS:")
            _log("")
            _log("  1. Hard-refresh the browser (Ctrl+F5 / Cmd+Shift+R).")
            _log("     No server restart needed — this patcher only touches")
            _log("     a template.")
            _log("")
            _log("  2. Open the Periods Timetable page.")
            _log("")
            _log("  3. The search bar should now be visible at the top of the")
            _log("     page (above the timetable cards).")
            _log("")
            _log("  4. Type part of a title or label. Cards should filter")
            _log("     live as you type.")
            _log("")
            _log("  5. Type something that matches nothing (e.g. 'zzzzz').")
            _log("     You should see a card reading:")
            _log("       'No timetables match your search.'")
            _log("")
            _log("  6. Clear the search box. All cards should re-appear.")
            _log("")
            _log("  7. If it STILL doesn't filter, open DevTools console and run:")
            _log("       window.TIMETABLE_DEBUG = true;")
            _log("     Then type in the search box. You should see log lines:")
            _log("       [TimetableSearch] q= 'sen' total= 3 visible= 1")
            _log("     * If NO log lines appear, the input listener never fires.")
            _log("       Check that no other script throws before this block.")
            _log("     * If log lines appear but the wrong count is reported,")
            _log("       the dataset.searchText values are wrong. Inspect one")
            _log("       card in Elements → Properties and check data-search-text.")
            _log("     * If log lines appear with correct counts but cards don't")
            _log("       visually hide, a CSS rule with higher specificity is")
            _log("       overriding the inline style. Search the page's CSS for")
            _log("       a '.card' rule with 'display: something !important'.")
            _log("")
            _log("  Notes on the design:")
            _log("   - Three independent wiring paths attach the filter:")
            _log("       (1) inline oninput on the <input>")
            _log("       (2) direct addEventListener on the input")
            _log("       (3) document-level input delegation")
            _log("     Any one of them is sufficient; the redundancy is")
            _log("     deliberate so a single failed attach can't disable")
            _log("     search entirely.")
            _log("   - The 'no results' card is separate from the empty state")
            _log("     so 'you have no timetables' and 'your search matched")
            _log("     nothing' are visually distinct.")
            _log("   - Cards are hidden with 'display: none !important' via")
            _log("     style.setProperty so any weakly-authored stylesheet")
            _log("     rule is overridden; showing uses removeProperty so the")
            _log("     original stylesheet value wins again.")
            _log("   - The filter survives a re-render (edit/delete triggers")
            _log("     renderAll(), which re-runs applyTtFilter() at the end).")
        return 0
    _log("FAILED: one or more steps did not complete.")
    return 1


if __name__ == '__main__':
    sys.exit(main())
