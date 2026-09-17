#!/usr/bin/env python3
"""
axis_patcher.py — ATTENDANCE_DATE_ANALYTICS_V1
================================================

Enhances the admin "Mark & History" modal so that:

  * The modal panel is noticeably WIDER (1200px on desktop).
  * A per-date ANALYTICS panel sits directly above the student list,
    showing every KPI for the currently-loaded date: total students,
    marked / unmarked, present / absent / late / half day / excused,
    completion %, attendance %, auto-marked count, and whether the
    date is locked.
  * A horizontal "RECENT DATES" strip lets the admin see at a glance
    how complete each of the last 14 days is.  Each pill shows a mini
    progress bar and is colour-coded by completion state.  Clicking a
    pill loads that date's roster + analytics in one shot.

Implementation
--------------
1.  Adds a new backend endpoint
        GET /portal/<schema>/api/attendance/recent-summary/
            ?class_id=<id>&days=<1..90>
    which returns per-day KPI rows for the class.

2.  Wires the new endpoint into public_urls.py.

3.  Extends templates/tenant/attendence.html with:
      - new CSS for the analytics panel + recent-dates strip
      - new HTML blocks inside #attTabMark
      - new JS functions (loadRecentDates, renderRecentDates,
        renderDateAnalytics, markActivePill, gotoDate) plus a small
        hook inside reloadStudents() and openMark().

Idempotent.  Safe to re-run.  Never deletes or overwrites unrelated
code.

Usage
-----
    python3 axis_patcher.py --dry-run --verbose
    python3 axis_patcher.py
    python3 axis_patcher.py --target-dir /srv/fee_management
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


MARKER = "ATTENDANCE_DATE_ANALYTICS_V1"


# ------------------------------------------------------------------ utils

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        return path.read_text(encoding="utf-8")
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
        path.write_text(content, encoding="utf-8")
        log(f"  WROTE: {path} ({label})")
        return True
    except Exception as e:
        log(f"  ERROR writing {path}: {e}")
        return False


# =====================================================================
# 1. admin_attendence.py — new recent-summary API
# =====================================================================

ADMIN_VIEWS_APPEND = '''

# =====================================================================
# ATTENDANCE_DATE_ANALYTICS_V1
# ---------------------------------------------------------------------
# Per-day KPI rows for the last N days of a class.  Powers the
# "Recent Dates" strip and the analytics panel inside the admin's
# Mark & History modal.
# =====================================================================


@require_http_methods(['GET'])
@require_tenant_type(['school', 'wing_school', 'single_small_school'])
@require_school_feature('attendance_management')
def admin_attendance_recent_summary_api(request, schema_name):
    """GET per-day analytics for the last N days of one class.

    Query params:
        class_id  (required)
        days      int 1..90 (default 14)

    Response::

        {
          "ok": True,
          "class_id": 7,
          "class_name": "Grade 1 - A",
          "total_students": 32,
          "days": 14,
          "rows": [
            {
              "date": "2026-09-17",
              "day_name": "Wed",
              "day_full": "Wednesday",
              "is_today": True,
              "is_holiday": False,
              "holiday_reason": "",
              "total_students": 32,
              "marked": 30,
              "present": 28, "absent": 2, "late": 0,
              "half_day": 0, "excused": 0, "holiday": 0,
              "completion_pct": 93.8,
              "attendance_pct": 93.3,
              "status": "partial",          # completed|partial|pending|holiday
              "period_marked": 40,
              "sources": {"teacher": 30, "auto_system": 0}
            },
            ...
          ]
        }
    """
    class_id = _parse_int(request.GET.get('class_id'))
    if not class_id:
        return JsonResponse(
            {'ok': False, 'error': 'class_id required'}, status=400,
        )
    try:
        days = int(request.GET.get('days', '14') or 14)
    except (TypeError, ValueError):
        days = 14
    days = max(1, min(90, days))

    today = _today()

    with schema_context(schema_name):
        school_class = SchoolClass.objects.filter(
            id=class_id, is_active=True,
        ).first()
        if not school_class:
            return JsonResponse(
                {'ok': False, 'error': 'Class not found'}, status=404,
            )

        total_students = Student.objects.filter(
            school_class=school_class, status='active',
        ).count()

        start = today - timedelta(days=days - 1)

        # ---- batch-load holiday context (2-3 queries total) ----
        try:
            holiday_dows = set(
                WeeklyHoliday.objects.values_list('day_of_week', flat=True)
            )
        except Exception:
            holiday_dows = set()
        try:
            annual_pairs = set(
                AnnualHoliday.objects.values_list('month', 'day')
            )
        except Exception:
            annual_pairs = set()
        try:
            vacations = list(
                Vacation.objects.values_list('start_date', 'end_date')
            )
        except Exception:
            vacations = []

        def _is_hol(d):
            if d.weekday() in holiday_dows:
                return True, 'Weekly holiday'
            if (d.month, d.day) in annual_pairs:
                return True, 'Annual holiday'
            for s, e in vacations:
                if s <= d <= e:
                    return True, 'Vacation'
            return False, ''

        # ---- full-day rows for the window ----
        rows_qs = (
            StudentAttendance.objects
            .filter(
                school_class=school_class,
                date__gte=start,
                date__lte=today,
                period_order__isnull=True,
            )
            .values('date', 'status', 'source')
        )

        from collections import defaultdict
        buckets = defaultdict(lambda: {
            'total_marked': 0,
            'statuses': defaultdict(int),
            'sources': defaultdict(int),
        })
        for r in rows_qs:
            b = buckets[r['date']]
            b['total_marked'] += 1
            st = r['status'] or 'present'
            b['statuses'][st] += 1
            src = r['source'] or 'teacher'
            b['sources'][src] += 1

        # ---- period-wise counts for the window ----
        period_rows_qs = (
            StudentAttendance.objects
            .filter(
                school_class=school_class,
                date__gte=start,
                date__lte=today,
                period_order__isnull=False,
            )
            .values('date')
            .annotate(n=Count('id'))
        )
        period_by_date = {r['date']: r['n'] for r in period_rows_qs}

        # ---- build rows, newest first ----
        out = []
        for i in range(days):
            d = today - timedelta(days=i)
            is_hol, hol_reason = _is_hol(d)
            b = buckets.get(d)
            marked = b['total_marked'] if b else 0
            statuses = b['statuses'] if b else {}
            sources = b['sources'] if b else {}

            if is_hol:
                status = 'holiday'
            elif total_students and marked >= total_students:
                status = 'completed'
            elif marked > 0:
                status = 'partial'
            else:
                status = 'pending'

            completion_pct = (
                round((marked / total_students) * 100, 1)
                if total_students else 0.0
            )
            present = statuses.get('present', 0) if statuses else 0
            late = statuses.get('late', 0) if statuses else 0
            present_like = present + late
            att_pct = (
                round((present_like / marked) * 100, 1)
                if marked else 0.0
            )

            out.append({
                'date': d.isoformat(),
                'day_name': d.strftime('%a'),
                'day_full': d.strftime('%A'),
                'is_today': d == today,
                'is_holiday': is_hol,
                'holiday_reason': hol_reason,
                'total_students': total_students,
                'marked': marked,
                'present': present,
                'absent': statuses.get('absent', 0) if statuses else 0,
                'late': late,
                'half_day': statuses.get('half_day', 0) if statuses else 0,
                'excused': statuses.get('excused', 0) if statuses else 0,
                'holiday': statuses.get('holiday', 0) if statuses else 0,
                'completion_pct': completion_pct,
                'attendance_pct': att_pct,
                'status': status,
                'period_marked': period_by_date.get(d, 0),
                'sources': dict(sources) if sources else {},
            })

        return JsonResponse({
            'ok': True,
            'class_id': school_class.id,
            'class_name': str(school_class),
            'total_students': total_students,
            'days': days,
            'rows': out,
        })
'''


def patch_admin_views(root, args):
    path = root / "axis_saas" / "views" / "admin_attendence.py"
    content = read_file(path)
    if content is None:
        return False

    if "admin_attendance_recent_summary_api" in content:
        log(f"  SKIP (already applied): recent-summary API in {path}")
        return True

    content = content.rstrip() + "\n" + ADMIN_VIEWS_APPEND
    return write_file(path, content, args.dry_run,
                      "add recent-summary API")


# =====================================================================
# 2. public_urls.py — register the new endpoint
# =====================================================================

def patch_public_urls(root, args):
    path = root / "axis_saas" / "public_urls.py"
    content = read_file(path)
    if content is None:
        return False

    if "admin_attendance_recent_summary_api" in content:
        log(f"  SKIP (already applied): recent-summary route in {path}")
        return True

    changes = []

    # ---- import ----
    old_import = "    admin_attendance_daily_logs_api,\n"
    new_import = (
        "    admin_attendance_daily_logs_api,\n"
        "    # ATTENDANCE_DATE_ANALYTICS_V1\n"
        "    admin_attendance_recent_summary_api,\n"
    )
    if old_import in content:
        content = content.replace(old_import, new_import, 1)
        changes.append("import")
    else:
        log(f"  WARN: daily_logs import anchor not found in {path}")

    # ---- route ----
    old_route = (
        "    path('portal/<slug:schema_name>/api/attendance/daily-logs/', "
        "portal_wrapper(login_required_for_schema(admin_attendance_daily_logs_api)), "
        "name='admin_attendance_daily_logs_api'),\n"
    )
    new_route = old_route + (
        "    # ATTENDANCE_DATE_ANALYTICS_V1\n"
        "    path('portal/<slug:schema_name>/api/attendance/recent-summary/', "
        "portal_wrapper(login_required_for_schema(admin_attendance_recent_summary_api)), "
        "name='admin_attendance_recent_summary_api'),\n"
    )
    if old_route in content:
        content = content.replace(old_route, new_route, 1)
        changes.append("route")
    else:
        log(f"  WARN: daily_logs route anchor not found in {path}")

    if not changes:
        return True

    return write_file(path, content, args.dry_run,
                      f"wire recent-summary ({', '.join(changes)})")


# =====================================================================
# 3. templates/tenant/attendence.html
# =====================================================================

NEW_CSS = r'''
    /* ============ ATTENDANCE_DATE_ANALYTICS_V1 ============
       Wider modal + per-date analytics + recent-dates strip. */

    /* Widen ONLY the mark & history modal. */
    #attMarkModal .att-modal-panel {
        width: min(1200px, 100%);
    }
    #attMarkModal .att-modal-body {
        max-height: 66vh;
    }

    /* --- per-date analytics panel --- */
    .att-day-analytics {
        padding: .9rem 1.2rem;
        background: var(--surface-alt);
        border-bottom: 1px solid var(--border);
    }
    .att-day-analytics .hero-row {
        display: flex;
        flex-wrap: wrap;
        gap: .5rem .9rem;
        align-items: center;
        margin-bottom: .7rem;
    }
    .att-day-analytics .date-big {
        font-size: 1.1rem;
        font-weight: 800;
    }
    .att-day-analytics .date-day {
        font-size: .78rem;
        color: var(--muted);
        font-weight: 700;
    }
    .att-day-analytics .meta-row {
        display: flex;
        flex-wrap: wrap;
        gap: .4rem;
        font-size: .72rem;
        color: var(--muted);
        margin-left: auto;
    }
    .att-day-analytics .meta-chip {
        display: inline-flex;
        align-items: center;
        gap: .3rem;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: .45rem;
        padding: .25rem .55rem;
        font-weight: 600;
    }
    .att-day-analytics .kpi-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(108px, 1fr));
        gap: .5rem;
    }
    .att-day-kpi {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: .6rem;
        padding: .55rem .65rem;
        position: relative;
        overflow: hidden;
    }
    .att-day-kpi::after {
        content: '';
        position: absolute;
        inset: 0 auto 0 0;
        width: 3px;
        background: var(--primary);
    }
    .att-day-kpi.k-present::after  { background: #10b981; }
    .att-day-kpi.k-absent::after   { background: #ef4444; }
    .att-day-kpi.k-late::after     { background: #f59e0b; }
    .att-day-kpi.k-halfday::after  { background: #8b5cf6; }
    .att-day-kpi.k-excused::after  { background: #0ea5e9; }
    .att-day-kpi.k-pct::after      { background: #6366f1; }
    .att-day-kpi.k-total::after    { background: #0ea5e9; }
    .att-day-kpi.k-auto::after     { background: #1e40af; }
    .att-day-kpi .k {
        font-size: .6rem;
        text-transform: uppercase;
        letter-spacing: .06em;
        color: var(--muted);
        font-weight: 800;
    }
    .att-day-kpi .v {
        font-size: 1.2rem;
        font-weight: 800;
        color: var(--text);
        line-height: 1.1;
        margin-top: .1rem;
    }
    .att-day-kpi .sub {
        font-size: .64rem;
        color: var(--muted);
        margin-top: .1rem;
    }

    /* --- recent dates strip --- */
    .att-recent-dates {
        display: flex;
        gap: .4rem;
        padding: .7rem 1.2rem;
        background: var(--surface);
        border-bottom: 1px solid var(--border);
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: thin;
    }
    .att-recent-dates::-webkit-scrollbar { height: 6px; }
    .att-recent-dates::-webkit-scrollbar-thumb {
        background: var(--border);
        border-radius: 3px;
    }
    .att-date-pill {
        position: relative;
        flex: 0 0 auto;
        min-width: 72px;
        padding: .45rem .5rem .4rem;
        border-radius: .55rem;
        border: 1px solid var(--border);
        background: var(--surface-alt);
        cursor: pointer;
        text-align: center;
        transition: all .12s ease;
        font-size: .68rem;
        user-select: none;
    }
    .att-date-pill:hover {
        border-color: var(--primary);
        transform: translateY(-1px);
    }
    .att-date-pill.active {
        border-color: var(--primary);
        background: var(--primary);
        color: #fff;
        box-shadow: 0 4px 12px rgba(99,102,241,.35);
    }
    .att-date-pill.status-holiday {
        opacity: .6;
        filter: grayscale(.5);
    }
    .att-date-pill .dp-day {
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: .04em;
        font-size: .58rem;
        opacity: .85;
    }
    .att-date-pill .dp-num {
        font-weight: 800;
        font-size: .95rem;
        line-height: 1.05;
        margin: .1rem 0 0;
    }
    .att-date-pill .dp-month {
        font-size: .56rem;
        opacity: .75;
        text-transform: uppercase;
        letter-spacing: .05em;
    }
    .att-date-pill .dp-bar {
        height: 3px;
        margin-top: .3rem;
        border-radius: 999px;
        background: var(--border);
        overflow: hidden;
    }
    .att-date-pill .dp-bar > span {
        display: block;
        height: 100%;
        border-radius: 999px;
        background: #10b981;
        transition: width .2s ease;
    }
    .att-date-pill.active .dp-bar {
        background: rgba(255,255,255,.35);
    }
    .att-date-pill.active .dp-bar > span { background: #fff; }
    .att-date-pill.status-partial .dp-bar > span  { background: #f59e0b; }
    .att-date-pill.status-pending .dp-bar > span  { background: #ef4444; }
    .att-date-pill.status-holiday .dp-bar > span  { background: #0ea5e9; }
    .att-date-pill.is-today {
        border-color: #10b981;
    }
    .att-date-pill.is-today::after {
        content: '●';
        color: #10b981;
        position: absolute;
        top: 1px;
        right: 3px;
        font-size: .5rem;
        line-height: 1;
    }
    .att-date-pill.active.is-today::after { color: #fff; }

    .att-recent-empty {
        width: 100%;
        text-align: center;
        font-size: .78rem;
        color: var(--muted);
        padding: .5rem 0;
    }
    /* ============ /ATTENDANCE_DATE_ANALYTICS_V1 ============ */
'''


NEW_HTML = r'''            <!-- ============ ATTENDANCE_DATE_ANALYTICS_V1 ============ -->
            <div id="attRecentDates" class="att-recent-dates" style="display:none;"></div>
            <div id="attDateAnalytics" class="att-day-analytics" style="display:none;"></div>
            <!-- ============ /ATTENDANCE_DATE_ANALYTICS_V1 ============ -->
'''


NEW_JS_FUNCTIONS = r'''    // ================= ATTENDANCE_DATE_ANALYTICS_V1 =================
    // Per-date analytics panel + recent-dates strip.  Purely additive:
    // no existing function is modified in a destructive way.

    var recentDatesCache = [];

    function fmtDateParts(dateStr) {
        // Returns { num, monthShort, dayShort, dayFull }
        try {
            var parts = dateStr.split('-');
            var d = new Date(
                parseInt(parts[0], 10),
                parseInt(parts[1], 10) - 1,
                parseInt(parts[2], 10),
            );
            var dayShort = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d.getDay()];
            var dayFull  = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][d.getDay()];
            var monthShort = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];
            return {
                num: parts[2],
                monthShort: monthShort,
                dayShort: dayShort,
                dayFull: dayFull,
            };
        } catch (e) {
            return { num: dateStr, monthShort: '', dayShort: '', dayFull: '' };
        }
    }

    function loadRecentDates() {
        if (!modalClassId) return;
        var strip = q('#attRecentDates');
        if (strip) {
            strip.style.display = 'flex';
            strip.innerHTML = '<div class="att-recent-empty">Loading recent dates…</div>';
        }
        var url = '/portal/' + SCHEMA
                + '/api/attendance/recent-summary/?class_id=' + modalClassId
                + '&days=14';
        fetch(url, { headers: {'X-Requested-With': 'XMLHttpRequest'} })
            .then(function(r) { return r.json(); })
            .then(function(j) {
                if (!j.ok) {
                    if (strip) {
                        strip.innerHTML = '<div class="att-recent-empty">'
                            + esc(j.error || 'Failed to load recent dates.') + '</div>';
                    }
                    return;
                }
                recentDatesCache = j.rows || [];
                renderRecentDates(j);
                markActivePill(q('#attModalDate').value);
            })
            .catch(function() {
                if (strip) {
                    strip.innerHTML = '<div class="att-recent-empty">Network error.</div>';
                }
            });
    }

    function renderRecentDates(j) {
        var strip = q('#attRecentDates');
        if (!strip) return;
        var rows = j.rows || [];
        if (!rows.length) {
            strip.innerHTML = '<div class="att-recent-empty">No recent dates.</div>';
            return;
        }
        // Newest first -> render oldest-left, today-right?  We want
        // today on the far right so the eye lands there.  rows comes
        // newest-first, so reverse for display.
        var disp = rows.slice().reverse();
        var h = '';
        disp.forEach(function(r) {
            var p = fmtDateParts(r.date);
            var cls = 'att-date-pill status-' + r.status
                    + (r.is_today ? ' is-today' : '');
            var barPct = Math.max(4, r.completion_pct || 0);
            var titleBits = [];
            titleBits.push(r.date + ' (' + r.day_full + ')');
            if (r.is_holiday) {
                titleBits.push('Holiday');
            } else {
                titleBits.push('Marked ' + r.marked + '/' + r.total_students);
                titleBits.push('P' + r.present + ' A' + r.absent
                             + ' L' + r.late + ' H' + r.half_day
                             + ' Lv' + r.excused);
                titleBits.push(r.completion_pct + '% done');
            }
            h += '<div class="' + cls + '"'
               + ' data-date="' + esc(r.date) + '"'
               + ' title="' + esc(titleBits.join('  ·  ')) + '"'
               + ' onclick="AXIS_ADMIN_ATT.gotoDate(\'' + esc(r.date) + '\')">'
               +   '<div class="dp-day">' + esc(p.dayShort) + '</div>'
               +   '<div class="dp-num">' + esc(p.num) + '</div>'
               +   '<div class="dp-month">' + esc(p.monthShort) + '</div>'
               +   '<div class="dp-bar"><span style="width:' + barPct + '%;"></span></div>'
               + '</div>';
        });
        strip.innerHTML = h;
    }

    function markActivePill(dateStr) {
        var strip = q('#attRecentDates');
        if (!strip) return;
        qa('.att-date-pill', strip).forEach(function(p) {
            if (p.getAttribute('data-date') === dateStr) {
                p.classList.add('active');
            } else {
                p.classList.remove('active');
            }
        });
    }

    function gotoDate(dateStr) {
        if (!modalClassId) return;
        var dp = q('#attModalDate');
        if (dp) dp.value = dateStr;
        var pp = q('#attModalPeriod');
        if (pp) pp.value = '';
        markActivePill(dateStr);
        reloadStudents();
    }

    function renderDateAnalytics(j) {
        // `j` is the payload from /api/attendance/students/.
        // We compute counts from the current roster.  Only rows that
        // are ALREADY marked count as present/absent/etc.; unmarked
        // students default to 'present' in the API response but are
        // not real marks.
        var panel = q('#attDateAnalytics');
        if (!panel) return;

        if (j.is_holiday) {
            var dpH = fmtDateParts(j.date || '');
            panel.style.display = 'block';
            panel.innerHTML =
                '<div class="hero-row">'
                + '<div><span class="date-big">' + esc(j.date || '') + '</span>'
                +   ' <span class="date-day">' + esc(dpH.dayFull || '') + '</span></div>'
                + '<div class="meta-row">'
                +   '<span class="meta-chip">🎉 Holiday'
                +   (j.holiday_reason ? ' — ' + esc(j.holiday_reason) : '')
                +   '</span></div>'
                + '</div>';
            return;
        }

        var rows = j.students || [];
        var total = rows.length;
        if (!total) {
            panel.style.display = 'none';
            return;
        }

        var marked = 0, present = 0, absent = 0, late = 0,
            halfDay = 0, excused = 0, autoCount = 0;
        rows.forEach(function(s) {
            if (s.already_marked) {
                marked++;
                if (s.status === 'present') present++;
                else if (s.status === 'absent') absent++;
                else if (s.status === 'late') late++;
                else if (s.status === 'half_day') halfDay++;
                else if (s.status === 'excused') excused++;
            }
            if (s.is_auto) autoCount++;
        });

        var completionPct = total
            ? Math.round((marked / total) * 100) : 0;
        var presentLike = present + late;
        var attPct = marked
            ? Math.round((presentLike / marked) * 100) : 0;

        var dp = fmtDateParts(j.date || '');
        var periodLabel = (j.period_order !== null &&
                           j.period_order !== undefined)
                          ? 'Period ' + j.period_order
                          : 'Full day';

        // Meta chips: status, auto, lock
        var statusChip;
        if (j.locked) {
            statusChip = '🔒 Already marked';
        } else {
            statusChip = '⏳ Not yet marked';
        }
        var autoChip = autoCount > 0
            ? '<span class="meta-chip" style="background:#dbeafe;color:#1e40af;">'
              + '🤖 ' + autoCount + ' auto</span>'
            : '';
        var percentChip = marked > 0
            ? '<span class="meta-chip" style="background:#d1fae5;color:#065f46;">'
              + '📊 ' + completionPct + '% done</span>'
            : '';

        var html = '';
        html += '<div class="hero-row">';
        html +=   '<div>'
               +    '<span class="date-big">' + esc(j.date || '') + '</span>'
               +    ' <span class="date-day">' + esc(dp.dayFull || '') + '</span>'
               +  '</div>';
        html +=   '<div class="meta-row">'
               +    '<span class="meta-chip">' + esc(periodLabel) + '</span>'
               +    '<span class="meta-chip">' + statusChip + '</span>'
               +    autoChip
               +    percentChip
               +  '</div>';
        html += '</div>';

        html += '<div class="kpi-grid">';
        html +=   '<div class="att-day-kpi k-total">'
               +    '<div class="k">Total</div>'
               +    '<div class="v">' + total + '</div>'
               +    '<div class="sub">active students</div>'
               +  '</div>';
        html +=   '<div class="att-day-kpi k-present">'
               +    '<div class="k">Present</div>'
               +    '<div class="v">' + present + '</div>'
               +    '<div class="sub">' + (marked ? Math.round(present/marked*100) : 0) + '% of marked</div>'
               +  '</div>';
        html +=   '<div class="att-day-kpi k-absent">'
               +    '<div class="k">Absent</div>'
               +    '<div class="v">' + absent + '</div>'
               +    '<div class="sub">' + (marked ? Math.round(absent/marked*100) : 0) + '% of marked</div>'
               +  '</div>';
        html +=   '<div class="att-day-kpi k-late">'
               +    '<div class="k">Late</div>'
               +    '<div class="v">' + late + '</div>'
               +    '<div class="sub">arrived late</div>'
               +  '</div>';
        html +=   '<div class="att-day-kpi k-halfday">'
               +    '<div class="k">Half day</div>'
               +    '<div class="v">' + halfDay + '</div>'
               +    '<div class="sub">partial</div>'
               +  '</div>';
        html +=   '<div class="att-day-kpi k-excused">'
               +    '<div class="k">Excused</div>'
               +    '<div class="v">' + excused + '</div>'
               +    '<div class="sub">on leave</div>'
               +  '</div>';
        html +=   '<div class="att-day-kpi k-pct">'
               +    '<div class="k">Attendance</div>'
               +    '<div class="v">' + attPct + '%</div>'
               +    '<div class="sub">of marked</div>'
               +  '</div>';
        html +=   '<div class="att-day-kpi k-auto">'
               +    '<div class="k">Auto-marked</div>'
               +    '<div class="v">' + autoCount + '</div>'
               +    '<div class="sub">by system</div>'
               +  '</div>';
        html += '</div>';

        panel.innerHTML = html;
        panel.style.display = 'block';
    }

    // ================ /ATTENDANCE_DATE_ANALYTICS_V1 =================
'''


def patch_template(root, args):
    path = root / "templates" / "tenant" / "attendence.html"
    content = read_file(path)
    if content is None:
        return False

    if MARKER in content:
        log(f"  SKIP (already applied): {path}")
        return True

    changes = []

    # ------------------------------------------------------------- CSS
    old_css_anchor = (
        "    /* ============== /ADMIN_ATTENDANCE_DASHBOARD_V1 ============== */\n"
        "</style>"
    )
    new_css_block = (
        "    /* ============== /ADMIN_ATTENDANCE_DASHBOARD_V1 ============== */\n"
        + NEW_CSS + "\n</style>"
    )
    if old_css_anchor in content:
        content = content.replace(old_css_anchor, new_css_block, 1)
        changes.append("css")
    else:
        log(f"  WARN: CSS anchor not found in {path}")

    # ------------------------------------------------------------ HTML
    old_html_anchor = '<div class="att-modal-body" id="attModalBody">'
    if old_html_anchor in content:
        content = content.replace(
            old_html_anchor,
            NEW_HTML + old_html_anchor,
            1,
        )
        changes.append("html")
    else:
        log(f"  WARN: HTML anchor not found in {path}")

    # -------------------------------------------------------------- JS
    # (a) state variable for the recent-dates cache
    old_state = "    var modalIsHoliday = false;\n"
    new_state = (
        "    var modalIsHoliday = false;\n"
        "    // ATTENDANCE_DATE_ANALYTICS_V1\n"
        "    var recentDatesCache = [];\n"
    )
    if old_state in content:
        content = content.replace(old_state, new_state, 1)
        changes.append("state var")
    else:
        log(f"  WARN: modal state anchor not found in {path}")

    # (b) openMark(): trigger the recent-dates strip load.
    old_openmark = (
        "        modalClassId = classId;\n"
        "        q('#attModalTitle').textContent"
    )
    new_openmark = (
        "        modalClassId = classId;\n"
        "        // ATTENDANCE_DATE_ANALYTICS_V1\n"
        "        loadRecentDates();\n"
        "        q('#attModalTitle').textContent"
    )
    if old_openmark in content:
        content = content.replace(old_openmark, new_openmark, 1)
        changes.append("openMark hook")
    else:
        log(f"  WARN: openMark anchor not found in {path}")

    # (c) reloadStudents(): after a successful load, render analytics
    #     and highlight the active pill.  Insert right before the
    #     closing of the successful `.then(...)` in reloadStudents.
    old_reload = (
        "                if (modalLocked) {\n"
        "                    q('#attLockedBanner').style.display = 'flex';\n"
        "                }\n"
        "            })\n"
        "            .catch(function() {\n"
        "                q('#attModalBody').innerHTML =\n"
        "                    '<div class=\"att-modal-empty\">Network error.</div>';\n"
        "            });\n"
        "    }\n"
    )
    new_reload = (
        "                if (modalLocked) {\n"
        "                    q('#attLockedBanner').style.display = 'flex';\n"
        "                }\n"
        "                // ATTENDANCE_DATE_ANALYTICS_V1\n"
        "                renderDateAnalytics(j);\n"
        "                markActivePill(q('#attModalDate').value);\n"
        "            })\n"
        "            .catch(function() {\n"
        "                q('#attModalBody').innerHTML =\n"
        "                    '<div class=\"att-modal-empty\">Network error.</div>';\n"
        "            });\n"
        "    }\n"
    )
    if old_reload in content:
        content = content.replace(old_reload, new_reload, 1)
        changes.append("reloadStudents hook")
    else:
        log(f"  WARN: reloadStudents anchor not found in {path}")

    # (d) insert the new JS functions before the init block.
    old_init_anchor = "    // ---------------- init ----------------"
    if old_init_anchor in content:
        content = content.replace(
            old_init_anchor,
            NEW_JS_FUNCTIONS + "\n" + old_init_anchor,
            1,
        )
        changes.append("js functions")
    else:
        log(f"  WARN: init anchor not found in {path}")

    # (e) expose gotoDate + loadRecentDates on the AXIS_ADMIN_ATT API.
    old_exports = (
        "        openClass: openClass,\n"
        "        // ATTENDANCE_LOGS_ANY_DATE_V1\n"
        "        openCurrentLogs: openCurrentLogs\n"
    )
    new_exports = (
        "        openClass: openClass,\n"
        "        // ATTENDANCE_LOGS_ANY_DATE_V1\n"
        "        openCurrentLogs: openCurrentLogs,\n"
        "        // ATTENDANCE_DATE_ANALYTICS_V1\n"
        "        gotoDate: gotoDate,\n"
        "        loadRecentDates: loadRecentDates\n"
    )
    if old_exports in content:
        content = content.replace(old_exports, new_exports, 1)
        changes.append("exports")
    else:
        log(f"  WARN: exports anchor not found in {path}")

    if not changes:
        log(f"  NO CHANGES for {path}")
        return True

    return write_file(path, content, args.dry_run,
                      ", ".join(changes))


# =====================================================================
# MAIN
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            f"{MARKER} — adds a per-date analytics panel and a "
            f"recent-dates strip to the admin Mark & History modal."
        )
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--target-dir", default=".")
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not (root / "manage.py").is_file():
        log(f"ERROR: manage.py not found in {root}")
        return 1

    log(f"Target: {root}")
    log(f"Mode:   {'DRY-RUN' if args.dry_run else 'APPLY'}")
    log(f"Patch:  {MARKER}")

    steps = [
        ("Admin views: recent-summary API", patch_admin_views),
        ("URLs: register recent-summary route", patch_public_urls),
        ("Template: analytics panel + strip", patch_template),
    ]

    results = []
    for label, fn in steps:
        log(f"--- {label} ---")
        try:
            ok = fn(root, args)
        except Exception as exc:
            log(f"  EXCEPTION: {exc.__class__.__name__}: {exc}")
            ok = False
        results.append((label, ok))

    log("=" * 65)
    for label, ok in results:
        log(f"  {'OK  ' if ok else 'FAIL'}  {label}")

    all_ok = all(ok for _, ok in results)
    if all_ok:
        log("All steps completed successfully.")
        if args.dry_run:
            log("Re-run without --dry-run to apply.")
        else:
            log("Next steps:")
            log("  1. Restart the Django server / WSGI workers.")
            log("  2. Open /portal/<schema>/attendance/ and click "
                "\"Mark & History\" on any class.")
        return 0
    log("One or more steps failed. See messages above.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
