#!/usr/bin/env python3
"""
axis_patcher.py
===============

One-shot patcher that OVERWRITES:

    templates/mobile/staff/attendence.html

Three focused changes on top of the previous professional UI:

1. HIDE THE HERO INSIDE THE DETAIL VIEW
   -------------------------------------
   The `.att-hero` block (which shows "Attendance", the staff name,
   and the day + date chip) is now hidden the moment a teacher opens
   a class or a subject period, and restored when they tap Back.
   This keeps the detail page focused on the task: marking students.

2. SVG ICON ON EACH CLASS CARD
   ---------------------------
   Every class-teacher card now shows a leading SVG badge icon
   (graduated book) with a subtle accent tint, matching the rest of
   the icon language. Keeps the existing left status stripe.

3. DATE-SPECIFIC ANALYTICS INSIDE THE DETAIL VIEW
   ----------------------------------------------
   A compact horizontal analytics strip is added inside the Today
   panel, right under the date picker. It updates every time the
   teacher loads a different date:

       • Total students in the class
       • Present / Absent / Late / Leave counts
       • A thin fill bar showing present% for that date

   All values are computed client-side from `currentStudents`, so no
   backend or view change is needed.

   The strip auto-hides on holiday/locked days (where no live count
   exists) and when the class has no students.

What is NOT changed
-------------------
* Every existing JavaScript function name, API endpoint URL, element
  ID, and `AXIS_ATT.*` call site is preserved 1:1.
* File name stays `attendence.html` (matches the view).
* No emojis. All iconography inline SVG.

Usage
-----
    python axis_patcher.py --dry-run --verbose
    python axis_patcher.py
    python axis_patcher.py --target-dir /path/to/fee_management --verbose

Idempotent: running it twice is a no-op.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

TARGET_REL_PATH = Path("templates") / "mobile" / "staff" / "attendence.html"

# --------------------------------------------------------------------------
# New file content
# --------------------------------------------------------------------------

NEW_CONTENT = r'''{% extends 'mobile/staff/base.html' %}
{% block title %}Attendance{% endblock %}
{% block body %}
<style>
    /* ============================================================
       STAFF ATTENDANCE — PROFESSIONAL UI
       Scoped with .att- prefix. Uses only the CSS variables that
       templates/mobile/staff/base.html already defines.
       No emojis. All iconography is inline SVG.
       ============================================================ */

    /* ---------------- HERO ---------------- */
    .att-hero {
        position: relative;
        border-radius: 20px;
        padding: 18px 18px 16px;
        color: #ffffff;
        background:
            radial-gradient(520px 200px at 108% -30%, rgba(255, 255, 255, 0.16), transparent 62%),
            linear-gradient(135deg, #12b3a2 0%, #0b6e64 60%, #084c46 100%);
        box-shadow: 0 18px 34px rgba(11, 110, 100, 0.22);
        margin-bottom: 16px;
    }

    .att-hero-top {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 10px;
    }

    .att-hero h2 {
        margin: 0;
        font-size: 1.32rem;
        font-weight: 800;
        letter-spacing: -0.01em;
        line-height: 1.15;
    }

    .att-hero .sub {
        margin-top: 4px;
        font-size: 0.78rem;
        font-weight: 600;
        color: rgba(255, 255, 255, 0.82);
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .att-hero .sub svg {
        width: 14px;
        height: 14px;
        stroke: currentColor;
        fill: none;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
        opacity: 0.85;
    }

    .att-today-chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.16);
        border: 1px solid rgba(255, 255, 255, 0.24);
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.10em;
        text-transform: uppercase;
        color: #fff8e1;
        white-space: nowrap;
    }

    .att-today-chip::before {
        content: "";
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: var(--gold);
    }

    /* ---------------- HOLIDAY BANNER ---------------- */
    .att-holiday-banner {
        display: flex;
        gap: 12px;
        align-items: flex-start;
        background: #fffbeb;
        border: 1px solid rgba(245, 158, 11, 0.32);
        border-left: 4px solid var(--warning);
        color: #78350f;
        border-radius: 14px;
        padding: 13px 14px;
        margin-bottom: 14px;
    }

    .att-holiday-icon {
        flex: 0 0 34px;
        width: 34px;
        height: 34px;
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: #fef3c7;
        color: #b45309;
    }

    .att-holiday-icon svg {
        width: 17px;
        height: 17px;
        stroke: currentColor;
        fill: none;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
    }

    .att-holiday-title {
        font-size: 0.88rem;
        font-weight: 800;
        letter-spacing: -0.005em;
    }

    .att-holiday-reason {
        margin-top: 3px;
        font-size: 0.78rem;
        font-weight: 700;
        color: #92400e;
    }

    .att-holiday-desc {
        margin-top: 4px;
        font-size: 0.78rem;
        font-weight: 600;
        color: #78350f;
        opacity: 0.9;
        line-height: 1.45;
    }

    /* ---------------- HOME ANALYTICS STRIP ---------------- */
    .att-analytics {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
        margin-bottom: 18px;
    }

    .att-stat {
        position: relative;
        overflow: hidden;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 13px 12px 12px;
        box-shadow: 0 8px 20px rgba(11, 110, 100, 0.05);
    }

    .att-stat::after {
        content: "";
        position: absolute;
        top: -22px;
        right: -22px;
        width: 54px;
        height: 54px;
        border-radius: 50%;
        background: var(--accent-soft);
        pointer-events: none;
    }

    .att-stat-icon {
        position: relative;
        width: 26px;
        height: 26px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: var(--accent-soft);
        color: var(--accent-deep);
        margin-bottom: 8px;
    }

    .att-stat-icon svg {
        width: 14px;
        height: 14px;
        stroke: currentColor;
        fill: none;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
    }

    .att-stat-value {
        position: relative;
        font-size: 1.32rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        color: var(--accent-deep);
        line-height: 1.05;
    }

    .att-stat-label {
        position: relative;
        margin-top: 4px;
        font-size: 9px;
        font-weight: 800;
        letter-spacing: 0.10em;
        text-transform: uppercase;
        color: var(--muted);
    }

    .att-progress-track {
        position: relative;
        margin-top: 8px;
        height: 4px;
        border-radius: 99px;
        background: var(--accent-soft);
        overflow: hidden;
    }

    .att-progress-bar {
        position: absolute;
        inset: 0 auto 0 0;
        width: 0%;
        border-radius: 99px;
        background: linear-gradient(90deg, var(--accent), var(--gold));
        transition: width 0.4s ease;
    }

    /* ---------------- SECTION HEADERS ---------------- */
    .att-section { margin-bottom: 18px; }

    .att-section-title {
        display: flex;
        align-items: center;
        gap: 9px;
        margin: 0 4px 10px;
        font-size: 0.8rem;
        font-weight: 800;
        letter-spacing: 0.10em;
        text-transform: uppercase;
        color: var(--muted);
    }

    .att-section-title::after {
        content: "";
        flex: 1;
        height: 1px;
        background: linear-gradient(90deg, rgba(201, 162, 39, 0.45), transparent);
    }

    .att-title-icon {
        width: 22px;
        height: 22px;
        border-radius: 7px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: var(--accent-soft);
        color: var(--accent-deep);
    }

    .att-title-icon svg {
        width: 12px;
        height: 12px;
        stroke: currentColor;
        fill: none;
        stroke-width: 2.2;
        stroke-linecap: round;
        stroke-linejoin: round;
    }

    .att-section-sub {
        font-weight: 700;
        font-size: 0.7rem;
        letter-spacing: 0.02em;
        color: var(--gold);
        text-transform: none;
    }

    /* ---------------- CLASS CARDS ---------------- */
    .att-class-card {
        position: relative;
        overflow: hidden;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 13px 15px 12px 17px;
        margin-bottom: 10px;
        box-shadow: 0 8px 20px rgba(11, 110, 100, 0.05);
        cursor: pointer;
        transition: transform 0.12s ease, box-shadow 0.18s ease, border-color 0.18s ease;
    }

    .att-class-card::before {
        content: "";
        position: absolute;
        top: 0;
        bottom: 0;
        left: 0;
        width: 3px;
        background: var(--muted);
    }

    .att-class-card.status-green::before { background: var(--success); }
    .att-class-card.status-amber::before { background: var(--warning); }
    .att-class-card.status-red::before   { background: var(--danger); }

    .att-class-card:hover {
        border-color: rgba(15, 157, 143, 0.35);
        box-shadow: 0 12px 26px rgba(11, 110, 100, 0.10);
    }

    .att-class-card:active { transform: scale(0.995); }

    .att-class-card-head {
        display: flex;
        justify-content: space-between;
        gap: 10px;
        align-items: center;
    }

    .att-class-head-left {
        display: flex;
        align-items: center;
        gap: 11px;
        min-width: 0;
    }

    .att-class-icon {
        flex: 0 0 38px;
        width: 38px;
        height: 38px;
        border-radius: 11px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: linear-gradient(140deg, #e8f6f3 0%, #cdeee8 100%);
        color: var(--accent-deep);
        border: 1px solid rgba(15, 157, 143, 0.18);
    }

    .att-class-icon svg {
        width: 18px;
        height: 18px;
        stroke: currentColor;
        fill: none;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
    }

    .att-class-name {
        font-weight: 800;
        font-size: 0.96rem;
        letter-spacing: -0.01em;
        line-height: 1.2;
        color: var(--text);
        display: flex;
        gap: 6px;
        align-items: center;
        flex-wrap: wrap;
        min-width: 0;
    }

    .att-class-card-meta {
        font-size: 0.77rem;
        font-weight: 600;
        color: var(--muted);
        margin-top: 8px;
        padding-left: 49px;
    }

    .att-class-card-perm {
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.01em;
        color: var(--muted);
        margin-top: 9px;
        padding-top: 9px;
        padding-left: 49px;
        border-top: 1px dashed var(--border);
        display: flex;
        align-items: center;
        gap: 6px;
    }

    .att-class-card-perm::before {
        content: "";
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: var(--gold);
    }

    /* ---------------- STATUS CHIPS ---------------- */
    .att-status-chip {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 3px 9px;
        border-radius: 8px;
        font-size: 10px;
        font-weight: 800;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        white-space: nowrap;
        flex: 0 0 auto;
    }

    .att-status-chip::before {
        content: "";
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: currentColor;
        opacity: 0.85;
    }

    .att-green { background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }
    .att-amber { background: #fffbeb; color: #92400e; border: 1px solid #fde68a; }
    .att-red   { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
    .att-blue  { background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; }

    /* ---------------- PERIOD CARDS ---------------- */
    .att-period-card {
        display: flex;
        gap: 12px;
        align-items: center;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 12px 14px;
        margin-bottom: 8px;
        box-shadow: 0 6px 16px rgba(11, 110, 100, 0.04);
        cursor: pointer;
        transition: transform 0.12s ease, border-color 0.18s ease, box-shadow 0.18s ease;
    }

    .att-period-card:hover {
        border-color: rgba(15, 157, 143, 0.35);
        box-shadow: 0 10px 22px rgba(11, 110, 100, 0.08);
    }

    .att-period-card:active { transform: scale(0.995); }

    .att-period-left {
        flex: 0 0 40px;
        width: 40px;
        height: 40px;
        border-radius: 11px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 800;
        font-size: 0.8rem;
        letter-spacing: 0.02em;
        color: #ffffff;
        background: linear-gradient(140deg, #12b3a2 0%, #0b6e64 100%);
    }

    .att-period-mid { flex: 1; min-width: 0; }

    .att-period-class {
        font-weight: 800;
        font-size: 0.9rem;
        line-height: 1.2;
        letter-spacing: -0.005em;
        color: var(--text);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .att-period-subject {
        margin-top: 3px;
        font-size: 0.73rem;
        font-weight: 600;
        color: var(--muted);
    }

    /* ---------------- EMPTY STATE ---------------- */
    .att-empty {
        text-align: center;
        color: var(--muted);
        padding: 26px 16px;
        font-size: 0.84rem;
        font-weight: 600;
        line-height: 1.5;
    }

    .att-empty svg {
        display: block;
        width: 34px;
        height: 34px;
        margin: 0 auto 10px;
        stroke: var(--accent);
        fill: none;
        stroke-width: 1.6;
        stroke-linecap: round;
        stroke-linejoin: round;
        opacity: 0.55;
    }

    /* ---------------- DETAIL HEADER ---------------- */
    .att-detail-header {
        display: flex;
        gap: 12px;
        align-items: center;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 12px 14px;
        margin-bottom: 14px;
        box-shadow: 0 8px 20px rgba(11, 110, 100, 0.05);
    }

    .att-back-btn {
        flex: 0 0 40px;
        width: 40px;
        height: 40px;
        border-radius: 12px;
        background: var(--surface-alt);
        border: 1px solid var(--border);
        color: var(--accent-deep);
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 0.15s ease, border-color 0.15s ease;
    }

    .att-back-btn svg {
        width: 18px;
        height: 18px;
        stroke: currentColor;
        fill: none;
        stroke-width: 2.2;
        stroke-linecap: round;
        stroke-linejoin: round;
    }

    .att-back-btn:hover {
        background: var(--accent-soft);
        border-color: rgba(15, 157, 143, 0.35);
    }

    .att-detail-header > div { min-width: 0; flex: 1; }

    .att-detail-title {
        font-weight: 800;
        font-size: 1rem;
        letter-spacing: -0.005em;
        color: var(--text);
        line-height: 1.2;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .att-detail-sub {
        font-size: 0.74rem;
        font-weight: 600;
        color: var(--muted);
        margin-top: 3px;
    }

    /* ---------------- TABS ---------------- */
    .att-tabs {
        display: flex;
        padding: 4px;
        background: var(--surface-alt);
        border: 1px solid var(--border);
        border-radius: 12px;
        margin-bottom: 14px;
        gap: 4px;
    }

    .att-tab {
        flex: 1;
        padding: 9px 12px;
        background: transparent;
        border: none;
        border-radius: 9px;
        font-weight: 800;
        font-size: 0.72rem;
        letter-spacing: 0.10em;
        text-transform: uppercase;
        color: var(--muted);
        cursor: pointer;
        transition: background 0.18s ease, color 0.18s ease;
    }

    .att-tab.active {
        background: #ffffff;
        color: var(--accent-deep);
        box-shadow: 0 4px 12px rgba(11, 110, 100, 0.08);
    }

    /* ---------------- CONTENT CARD ---------------- */
    .att-card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 15px;
        margin-bottom: 12px;
        box-shadow: 0 10px 24px rgba(11, 110, 100, 0.06);
    }

    /* ---------------- DATE ROW ---------------- */
    .att-date-row {
        display: flex;
        gap: 8px;
        align-items: center;
        margin-bottom: 12px;
        flex-wrap: wrap;
    }

    .att-date-row label {
        font-size: 0.68rem;
        font-weight: 800;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--muted);
        margin: 0;
    }

    .att-date-row input[type="date"] {
        flex: 1;
        min-width: 140px;
        padding: 9px 12px;
        border-radius: 10px;
        border: 1.5px solid var(--border);
        background: var(--surface-alt);
        color: var(--text);
        font-weight: 700;
        font-size: 0.85rem;
        transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
    }

    .att-date-row input[type="date"]:focus {
        outline: none;
        background: #ffffff;
        border-color: var(--accent);
        box-shadow: 0 0 0 3px var(--accent-soft);
    }

    .att-date-row input[type="date"]:disabled {
        opacity: 0.55;
        cursor: not-allowed;
    }

    .att-load-btn {
        padding: 9px 16px;
        border-radius: 10px;
        border: none;
        background: linear-gradient(135deg, #12b3a2 0%, #0b6e64 100%);
        color: #ffffff;
        font-weight: 800;
        font-size: 0.78rem;
        letter-spacing: 0.04em;
        cursor: pointer;
        box-shadow: 0 8px 16px rgba(11, 110, 100, 0.16);
        transition: transform 0.12s ease, filter 0.15s ease;
    }

    .att-load-btn:active { transform: scale(0.97); }

    /* ---------------- DATE ANALYTICS (detail view) ---------------- */
    .att-date-analytics {
        background: var(--surface-alt);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 11px 12px 10px;
        margin-bottom: 12px;
    }

    .att-da-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 9px;
    }

    .att-da-title {
        font-size: 0.66rem;
        font-weight: 800;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--muted);
    }

    .att-da-date {
        font-size: 0.68rem;
        font-weight: 800;
        letter-spacing: 0.04em;
        color: var(--accent-deep);
        background: var(--accent-soft);
        padding: 2px 8px;
        border-radius: 6px;
    }

    .att-da-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 5px;
        margin-bottom: 9px;
    }

    .att-da-item {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 1px;
        padding: 6px 3px 5px;
        border-radius: 8px;
        background: #ffffff;
        border: 1px solid var(--border);
    }

    .att-da-num {
        font-size: 0.92rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        line-height: 1;
        color: var(--text);
    }

    .att-da-lbl {
        font-size: 8.5px;
        font-weight: 800;
        letter-spacing: 0.09em;
        text-transform: uppercase;
        color: var(--muted);
    }

    .att-da-item.present  .att-da-num { color: #16a34a; }
    .att-da-item.absent   .att-da-num { color: #dc2626; }
    .att-da-item.late     .att-da-num { color: #d97706; }
    .att-da-item.half_day .att-da-num { color: #7c3aed; }
    .att-da-item.excused  .att-da-num { color: #0284c7; }

    .att-da-bar {
        position: relative;
        height: 5px;
        border-radius: 99px;
        background: rgba(15, 157, 143, 0.10);
        overflow: hidden;
        display: flex;
    }

    .att-da-seg {
        height: 100%;
        transition: width 0.35s ease;
    }
    .att-da-seg.present  { background: #16a34a; }
    .att-da-seg.absent   { background: #dc2626; }
    .att-da-seg.late     { background: #d97706; }
    .att-da-seg.half_day { background: #7c3aed; }
    .att-da-seg.excused  { background: #0284c7; }

    .att-da-summary {
        margin-top: 7px;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.02em;
        color: var(--muted);
        display: flex;
        justify-content: space-between;
        gap: 8px;
    }

    .att-da-summary b {
        color: var(--text);
        font-weight: 800;
    }

    /* ---------------- INFO BANNERS ---------------- */
    .att-banner {
        display: flex;
        gap: 10px;
        align-items: flex-start;
        padding: 11px 13px;
        border-radius: 12px;
        font-size: 0.81rem;
        font-weight: 600;
        line-height: 1.45;
        margin-bottom: 12px;
    }

    .att-banner svg {
        flex: 0 0 18px;
        width: 18px;
        height: 18px;
        margin-top: 1px;
        stroke: currentColor;
        fill: none;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
    }

    .att-banner.holiday {
        background: #fffbeb;
        border: 1px solid rgba(245, 158, 11, 0.32);
        border-left: 4px solid var(--warning);
        color: #78350f;
    }

    .att-banner.locked {
        background: #eef2ff;
        border: 1px solid rgba(99, 102, 241, 0.28);
        border-left: 4px solid #6366f1;
        color: #3730a3;
    }

    .att-banner.auto {
        background: #eff6ff;
        border: 1px solid rgba(30, 64, 175, 0.22);
        border-left: 4px solid #1e40af;
        color: #1e40af;
    }

    /* ---------------- STUDENT LIST ---------------- */
    .att-students-list { margin-bottom: 8px; }

    .att-student-row {
        display: flex;
        gap: 12px;
        align-items: center;
        padding: 11px 0;
        border-bottom: 1px solid var(--border);
    }

    .att-student-row:last-child { border-bottom: none; }

    .att-student-info { flex: 1; min-width: 0; }

    .att-student-roll {
        font-size: 0.66rem;
        font-weight: 800;
        letter-spacing: 0.10em;
        text-transform: uppercase;
        color: var(--muted);
    }

    .att-student-name {
        margin-top: 3px;
        font-weight: 800;
        font-size: 0.88rem;
        letter-spacing: -0.005em;
        color: var(--text);
        line-height: 1.25;
        display: flex;
        gap: 6px;
        align-items: center;
        flex-wrap: wrap;
    }

    .att-student-meta {
        margin-top: 2px;
        font-size: 0.71rem;
        font-weight: 600;
        color: var(--muted);
    }

    /* ---------------- TAGS ---------------- */
    .att-tag {
        display: inline-block;
        padding: 2px 7px;
        border-radius: 5px;
        font-size: 9px;
        font-weight: 800;
        letter-spacing: 0.10em;
        text-transform: uppercase;
    }

    .att-tag.leave { background: #e0f2fe; color: #075985; border: 1px solid #bae6fd; }
    .att-tag.auto  { background: #e0e7ff; color: #3730a3; border: 1px solid #c7d2fe; }

    /* ---------------- STATUS RADIO GROUP ---------------- */
    .att-status-group {
        display: flex;
        gap: 3px;
        flex-wrap: nowrap;
        flex: 0 0 auto;
    }

    .att-status-group input { display: none; }

    .att-status-group label {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 30px;
        height: 30px;
        padding: 0 7px;
        border-radius: 8px;
        font-size: 0.7rem;
        font-weight: 800;
        letter-spacing: 0.04em;
        cursor: pointer;
        border: 1.5px solid var(--border);
        background: var(--surface-alt);
        color: var(--muted);
        user-select: none;
        margin: 0;
        transition: background 0.14s ease, color 0.14s ease,
                    border-color 0.14s ease, transform 0.10s ease;
    }

    .att-status-group label:hover {
        border-color: var(--accent);
        color: var(--accent-deep);
    }

    .att-status-group input[value="present"]:checked  + label { background: #16a34a; color: #fff; border-color: #16a34a; }
    .att-status-group input[value="absent"]:checked   + label { background: #dc2626; color: #fff; border-color: #dc2626; }
    .att-status-group input[value="late"]:checked     + label { background: #d97706; color: #fff; border-color: #d97706; }
    .att-status-group input[value="half_day"]:checked + label { background: #7c3aed; color: #fff; border-color: #7c3aed; }
    .att-status-group input[value="excused"]:checked  + label { background: #0284c7; color: #fff; border-color: #0284c7; }

    .att-status-group input:disabled + label { opacity: 0.45; cursor: not-allowed; }

    /* ---------------- BULK ACTIONS ---------------- */
    .att-bulk-actions {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-top: 8px;
        padding-top: 12px;
        border-top: 1px dashed var(--border);
    }

    .att-ghost-btn {
        padding: 9px 14px;
        border-radius: 10px;
        background: var(--surface-alt);
        color: var(--accent-deep);
        border: 1px solid var(--border);
        font-weight: 800;
        font-size: 0.74rem;
        letter-spacing: 0.04em;
        cursor: pointer;
        transition: background 0.15s ease, border-color 0.15s ease, transform 0.12s ease;
    }

    .att-ghost-btn:hover { background: var(--accent-soft); border-color: rgba(15, 157, 143, 0.35); }
    .att-ghost-btn:active { transform: scale(0.97); }
    .att-ghost-btn:disabled { opacity: 0.45; cursor: not-allowed; transform: none; }

    /* ---------------- SAVE BAR ---------------- */
    .att-save-area {
        margin-top: 14px;
        display: flex;
        gap: 10px;
        align-items: center;
        flex-wrap: wrap;
    }

    .att-quota-info {
        flex: 1;
        min-width: 120px;
        font-size: 0.73rem;
        font-weight: 700;
        color: var(--muted);
        line-height: 1.35;
    }

    .att-save-btn {
        padding: 11px 20px;
        border-radius: 12px;
        border: none;
        background: linear-gradient(135deg, #12b3a2 0%, #0b6e64 100%);
        color: #ffffff;
        font-weight: 800;
        font-size: 0.82rem;
        letter-spacing: 0.04em;
        cursor: pointer;
        box-shadow: 0 10px 20px rgba(11, 110, 100, 0.20);
        transition: transform 0.12s ease, filter 0.15s ease;
    }

    .att-save-btn:hover { filter: brightness(1.05); }
    .att-save-btn:active { transform: scale(0.98); }
    .att-save-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

    .att-save-msg {
        font-size: 0.77rem;
        font-weight: 700;
        text-align: center;
        margin-top: 10px;
        color: var(--muted);
        min-height: 1em;
    }

    .att-save-msg.ok  { color: var(--success); }
    .att-save-msg.err { color: var(--danger); }

    /* ---------------- HISTORY ---------------- */
    .att-history-header {
        display: grid;
        grid-template-columns: 1fr 1.1fr auto;
        gap: 10px;
        padding: 0 4px 10px;
        border-bottom: 1px solid var(--border);
        margin-bottom: 4px;
        font-size: 0.66rem;
        font-weight: 800;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--muted);
    }

    .att-history-row {
        display: grid;
        grid-template-columns: 1fr 1.1fr auto;
        gap: 10px;
        align-items: center;
        padding: 12px 4px;
        border-bottom: 1px solid var(--border);
        cursor: pointer;
        transition: background 0.15s ease;
        border-radius: 8px;
    }

    .att-history-row:hover { background: var(--accent-soft); }
    .att-history-row:last-child { border-bottom: none; }

    .att-history-date {
        font-weight: 800;
        font-size: 0.84rem;
        letter-spacing: -0.005em;
        color: var(--text);
    }

    .att-history-day {
        font-size: 0.66rem;
        font-weight: 700;
        color: var(--muted);
        margin-top: 3px;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }

    .att-history-status {
        display: flex;
        flex-direction: column;
        gap: 4px;
        align-items: flex-start;
    }

    .att-history-counts {
        font-size: 0.71rem;
        font-weight: 700;
        color: var(--muted);
    }

    .att-history-quota {
        font-size: 0.66rem;
        font-weight: 700;
        color: var(--gold);
        letter-spacing: 0.03em;
    }

    .att-history-action { text-align: right; }

    .att-mini-btn {
        padding: 6px 12px;
        border-radius: 8px;
        background: linear-gradient(135deg, #12b3a2 0%, #0b6e64 100%);
        color: #ffffff;
        border: none;
        font-weight: 800;
        font-size: 0.68rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        cursor: pointer;
        box-shadow: 0 6px 12px rgba(11, 110, 100, 0.16);
    }

    .att-mini-lock {
        font-size: 0.66rem;
        font-weight: 800;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--muted);
    }

    /* ---------------- MODAL ---------------- */
    .att-modal {
        position: fixed;
        inset: 0;
        background: rgba(6, 45, 41, 0.52);
        backdrop-filter: blur(4px);
        -webkit-backdrop-filter: blur(4px);
        display: none;
        align-items: center;
        justify-content: center;
        z-index: 9999;
        padding: 20px;
    }

    .att-modal.open { display: flex; }

    .att-modal-card {
        position: relative;
        background: var(--surface);
        border-radius: 18px;
        width: min(400px, 100%);
        padding: 20px 20px 18px;
        box-shadow: 0 24px 48px rgba(6, 45, 41, 0.32);
        border: 1px solid var(--border);
        overflow: hidden;
    }

    .att-modal-card::before {
        content: "";
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
        background: linear-gradient(90deg, var(--accent), var(--gold));
    }

    .att-modal-title {
        margin: 0 0 8px;
        font-size: 1rem;
        font-weight: 800;
        letter-spacing: -0.005em;
        color: var(--text);
    }

    .att-modal-body {
        font-size: 0.86rem;
        font-weight: 600;
        color: var(--muted);
        line-height: 1.5;
        margin-bottom: 18px;
    }

    .att-modal-actions {
        display: flex;
        gap: 10px;
        justify-content: flex-end;
    }

    /* ---------------- RESPONSIVE ---------------- */
    @media (max-width: 400px) {
        .att-hero h2 { font-size: 1.2rem; }
        .att-status-group label { min-width: 28px; height: 28px; font-size: 0.66rem; padding: 0 5px; }
        .att-save-btn { width: 100%; }
        .att-load-btn { width: 100%; }
        .att-stat-value { font-size: 1.15rem; }
        .att-da-num { font-size: 0.84rem; }
        .att-da-lbl { font-size: 8px; letter-spacing: 0.06em; }
    }

    @media (prefers-reduced-motion: reduce) {
        * { transition: none !important; animation: none !important; }
    }
</style>

<!-- ==================== HERO (hidden in detail view) ==================== -->
<div class="att-hero" id="attHero">
    <div class="att-hero-top">
        <div>
            <h2>Attendance</h2>
            <div class="sub">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                    <circle cx="12" cy="8.5" r="3.8"/>
                    <path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>
                </svg>
                {{ staff.full_name }}
            </div>
        </div>
        <span class="att-today-chip">{{ day_name }} · {{ today }}</span>
    </div>
</div>

{% if is_holiday %}
<div class="att-holiday-banner" id="attPageHolidayBanner">
    <div class="att-holiday-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
            <rect x="3" y="4.5" width="18" height="16" rx="3"/>
            <path d="M8 3v3M16 3v3M3 10h18"/>
        </svg>
    </div>
    <div>
        <div class="att-holiday-title">Today is a holiday</div>
        <div class="att-holiday-reason">{{ holiday_reason }}</div>
        <div class="att-holiday-desc">Aaj holiday hai — attendance mark karne ki zaroorat nahi.</div>
    </div>
</div>
{% endif %}

<!-- ==================== HOME VIEW ==================== -->
<div id="attHomeView">

    <div class="att-analytics" id="attAnalytics" style="display:none;">
        <div class="att-stat">
            <div class="att-stat-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24"><path d="m4 6 8-3 8 3-8 3z"/><path d="M4 6v9l8 4 8-4V6"/></svg>
            </div>
            <div class="att-stat-value" id="attStatClasses">—</div>
            <div class="att-stat-label">Classes</div>
        </div>
        <div class="att-stat">
            <div class="att-stat-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24"><circle cx="9" cy="9" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17.5" cy="8.5" r="2.5"/><path d="M14.5 20a5.5 5.5 0 0 1 8-4.9"/></svg>
            </div>
            <div class="att-stat-value" id="attStatStudents">—</div>
            <div class="att-stat-label">Students</div>
        </div>
        <div class="att-stat">
            <div class="att-stat-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24"><path d="M4 19h16"/><path d="M6 16V9M11 16V5M16 16v-4M21 16V7"/></svg>
            </div>
            <div class="att-stat-value" id="attStatProgress">—</div>
            <div class="att-stat-label">Progress</div>
            <div class="att-progress-track" aria-hidden="true">
                <span class="att-progress-bar" id="attProgressBar"></span>
            </div>
        </div>
    </div>

    <div id="attCTSection" class="att-section" style="display:none;">
        <div class="att-section-title">
            <span class="att-title-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24"><path d="m4 6 8-3 8 3-8 3z"/><path d="M4 6v9l8 4 8-4V6"/></svg>
            </span>
            My Classes
            <span class="att-section-sub">(Class Teacher)</span>
        </div>
        <div id="attCTList"></div>
    </div>

    <div id="attSubSection" class="att-section" style="display:none;">
        <div class="att-section-title">
            <span class="att-title-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
            </span>
            My Periods Today
            <span class="att-section-sub">(Subject Teacher)</span>
        </div>
        <div id="attSubList"></div>
    </div>

    <div id="attNoClasses" class="att-card" style="display:none;">
        <div class="att-empty">
            <svg viewBox="0 0 24 24" aria-hidden="true">
                <circle cx="12" cy="12" r="9"/>
                <path d="M12 8v5M12 16h.01"/>
            </svg>
            You are not currently assigned as a class teacher or subject teacher to any class.<br>
            Please contact your school admin.
        </div>
    </div>
</div>

<!-- ==================== DETAIL VIEW ==================== -->
<div id="attDetailView" style="display:none;">
    <div class="att-detail-header">
        <button class="att-back-btn" onclick="AXIS_ATT.back()" aria-label="Back">
            <svg viewBox="0 0 24 24"><path d="M15 6 9 12l6 6"/></svg>
        </button>
        <div>
            <div class="att-detail-title" id="attDetailTitle">Class</div>
            <div class="att-detail-sub" id="attDetailSub"></div>
        </div>
    </div>

    <div class="att-tabs">
        <button class="att-tab active" data-tab="today" onclick="AXIS_ATT.switchTab('today')">Today</button>
        <button class="att-tab" data-tab="history" onclick="AXIS_ATT.switchTab('history')">History</button>
    </div>

    <!-- TODAY PANEL -->
    <div id="attTabToday" class="att-tab-panel">
        <div class="att-card">
            <div class="att-date-row">
                <label for="attDatePicker">Date</label>
                <input type="date" id="attDatePicker">
                <button class="att-load-btn" type="button" onclick="AXIS_ATT.loadDate()">Load</button>
            </div>

            <!-- Date-specific analytics (populated by JS) -->
            <div class="att-date-analytics" id="attDateAnalytics" style="display:none;">
                <div class="att-da-head">
                    <span class="att-da-title">Date Snapshot</span>
                    <span class="att-da-date" id="attDaDate">—</span>
                </div>
                <div class="att-da-grid">
                    <div class="att-da-item present">
                        <span class="att-da-num" id="attDaPresent">0</span>
                        <span class="att-da-lbl">Present</span>
                    </div>
                    <div class="att-da-item absent">
                        <span class="att-da-num" id="attDaAbsent">0</span>
                        <span class="att-da-lbl">Absent</span>
                    </div>
                    <div class="att-da-item late">
                        <span class="att-da-num" id="attDaLate">0</span>
                        <span class="att-da-lbl">Late</span>
                    </div>
                    <div class="att-da-item half_day">
                        <span class="att-da-num" id="attDaHalf">0</span>
                        <span class="att-da-lbl">Half</span>
                    </div>
                    <div class="att-da-item excused">
                        <span class="att-da-num" id="attDaExcused">0</span>
                        <span class="att-da-lbl">Leave</span>
                    </div>
                </div>
                <div class="att-da-bar" aria-hidden="true">
                    <span class="att-da-seg present" id="attDaSegPresent" style="width:0%"></span>
                    <span class="att-da-seg absent"  id="attDaSegAbsent"  style="width:0%"></span>
                    <span class="att-da-seg late"    id="attDaSegLate"    style="width:0%"></span>
                    <span class="att-da-seg half_day" id="attDaSegHalf"   style="width:0%"></span>
                    <span class="att-da-seg excused" id="attDaSegExcused" style="width:0%"></span>
                </div>
                <div class="att-da-summary">
                    <span id="attDaSummaryTotal">Total <b>0</b></span>
                    <span id="attDaSummaryPresent">Present <b>0%</b></span>
                </div>
            </div>

            <div id="attTodayBanner" class="att-banner" style="display:none;"></div>
            <div id="attTodayLock" class="att-banner locked" style="display:none;"></div>
            <div id="attTodayStudents" class="att-students-list">
                <div class="att-empty">Pick a class to begin.</div>
            </div>
            <div class="att-bulk-actions">
                <button class="att-ghost-btn" id="attBulkPresent" type="button" onclick="AXIS_ATT.bulk('present')">All Present</button>
                <button class="att-ghost-btn" id="attBulkAbsent"  type="button" onclick="AXIS_ATT.bulk('absent')">All Absent</button>
            </div>
            <div class="att-save-area">
                <div class="att-quota-info" id="attQuotaInfo"></div>
                <button class="att-save-btn" id="attSaveBtn" type="button" onclick="AXIS_ATT.confirmSave()">Save Attendance</button>
            </div>
            <div id="attSaveMsg" class="att-save-msg"></div>
        </div>
    </div>

    <!-- HISTORY PANEL -->
    <div id="attTabHistory" class="att-tab-panel" style="display:none;">
        <div class="att-card">
            <div class="att-history-header">
                <div>Date</div>
                <div>Status</div>
                <div></div>
            </div>
            <div id="attHistoryList" class="att-history-list">
                <div class="att-empty">Loading…</div>
            </div>
        </div>
    </div>
</div>

<!-- ==================== CONFIRM MODAL ==================== -->
<div id="attConfirmModal" class="att-modal" role="dialog" aria-modal="true" aria-labelledby="attConfirmTitle">
    <div class="att-modal-card">
        <div class="att-modal-title" id="attConfirmTitle">Confirm Save</div>
        <div class="att-modal-body" id="attConfirmBody">
            Are you sure you want to save this attendance?
        </div>
        <div class="att-modal-actions">
            <button class="att-ghost-btn" type="button" onclick="AXIS_ATT.closeConfirm()">Cancel</button>
            <button class="att-save-btn"  type="button" onclick="AXIS_ATT.doSave()">Yes, Save</button>
        </div>
    </div>
</div>

<script>
window.AXIS_ATT = (function() {
    /* =========================================================
       STAFF ATTENDANCE — logic preserved 1:1.
       Additions:
         • renderAnalytics()      — home view stat strip
         • renderDateAnalytics()  — detail view, updates per date
         • toggleHero()           — hide/show hero on detail enter/exit
         • Class card leading SVG badge
       No existing behaviour removed.
       ========================================================= */

    var CT_CLASSES = {{ class_teacher_classes_json|safe }};
    var SUBJECT_PERIODS = {{ subject_periods_json|safe }};
    var TODAY = '{{ today }}';

    var currentClass = null;
    var currentDate = TODAY;
    var currentPeriod = null;
    var currentStudents = [];
    var currentPermission = null;
    var currentLocked = false;

    /* --- inline SVG helpers (no emojis anywhere in the DOM) --- */
    function svgHoliday() {
        return '<svg viewBox="0 0 24 24" aria-hidden="true">'
             + '<rect x="3" y="4.5" width="18" height="16" rx="3"/>'
             + '<path d="M8 3v3M16 3v3M3 10h18"/>'
             + '</svg>';
    }
    function svgLock() {
        return '<svg viewBox="0 0 24 24" aria-hidden="true">'
             + '<rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/>'
             + '<path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>'
             + '</svg>';
    }
    function svgBot() {
        return '<svg viewBox="0 0 24 24" aria-hidden="true">'
             + '<rect x="4" y="7" width="16" height="12" rx="3"/>'
             + '<path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7"/>'
             + '<circle cx="9.5" cy="13" r="1"/><circle cx="14.5" cy="13" r="1"/>'
             + '<path d="M10 16.5h4"/>'
             + '</svg>';
    }
    /* Leading badge icon for class cards (graduated book). */
    function svgClassIcon() {
        return '<svg viewBox="0 0 24 24" aria-hidden="true">'
             + '<path d="M4 19.5V7.5A2.5 2.5 0 0 1 6.5 5H20v14.5"/>'
             + '<path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20"/>'
             + '<path d="M8 9h8M8 13h8"/>'
             + '</svg>';
    }

    function q(s, r) { return (r || document).querySelector(s); }
    function qa(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function(c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
        });
    }
    function csrf() {
        var m = document.querySelector('meta[name="csrf-token"]');
        if (m && m.getAttribute('content')) return m.getAttribute('content');
        var name = 'csrftoken=';
        var parts = (document.cookie || '').split(';');
        for (var i = 0; i < parts.length; i++) {
            var c = parts[i].trim();
            if (c.indexOf(name) === 0) return c.substring(name.length);
        }
        return '';
    }

    function permLabel(v) {
        if (v === 'read_write') return 'Read & Write';
        if (v === 'read') return 'Read only';
        return 'Today only';
    }

    function statusClass(s) {
        if (s === 'completed') return 'green';
        if (s === 'partial')   return 'amber';
        return 'red';
    }

    function statusText(s) {
        if (s === 'completed') return 'Completed';
        if (s === 'partial')   return 'Partial';
        return 'Pending';
    }

    /* ---- Hero / page banner toggle ---- */
    function toggleHero(show) {
        var hero = q('#attHero');
        if (hero) hero.style.display = show ? 'block' : 'none';
        var pg = q('#attPageHolidayBanner');
        if (pg) pg.style.display = show ? 'flex' : 'none';
    }

    /* ---- Home view analytics strip ---- */
    function renderAnalytics() {
        var box = q('#attAnalytics');
        if (!box) return;

        var ctCount = (CT_CLASSES && CT_CLASSES.length) ? CT_CLASSES.length : 0;
        var spCount = (SUBJECT_PERIODS && SUBJECT_PERIODS.length) ? SUBJECT_PERIODS.length : 0;
        var totalClasses = ctCount + spCount;

        var totalStudents = 0;
        var totalMarked = 0;
        (CT_CLASSES || []).forEach(function(c) {
            totalStudents += (c.student_count || 0);
            totalMarked   += (c.marked_today  || 0);
        });

        var pct = totalStudents > 0
            ? Math.min(100, Math.round(totalMarked * 100 / totalStudents))
            : 0;

        var classesEl  = q('#attStatClasses');
        var studentsEl = q('#attStatStudents');
        var progressEl = q('#attStatProgress');
        var barEl      = q('#attProgressBar');

        if (classesEl)  classesEl.textContent  = String(totalClasses);
        if (studentsEl) studentsEl.textContent = String(totalStudents);
        if (progressEl) progressEl.textContent = (totalStudents > 0 ? pct + '%' : '—');
        if (barEl)      barEl.style.width      = pct + '%';

        box.style.display = totalClasses > 0 ? 'grid' : 'none';
    }

    /* ---- Detail view analytics strip (per date) ---- */
    function renderDateAnalytics() {
        var box = q('#attDateAnalytics');
        if (!box) return;

        var total = currentStudents.length;
        if (!total) {
            box.style.display = 'none';
            return;
        }

        var c = { present: 0, absent: 0, late: 0, half_day: 0, excused: 0 };
        currentStudents.forEach(function(s) {
            var k = s.status || 'present';
            if (c[k] === undefined) c[k] = 0;
            c[k] += 1;
        });

        function setTxt(id, v) { var el = q(id); if (el) el.textContent = String(v); }
        setTxt('#attDaPresent', c.present);
        setTxt('#attDaAbsent',  c.absent);
        setTxt('#attDaLate',    c.late);
        setTxt('#attDaHalf',    c.half_day);
        setTxt('#attDaExcused', c.excused);

        function pctOf(n) { return total ? (n * 100 / total) : 0; }
        function setW(id, v) { var el = q(id); if (el) el.style.width = v.toFixed(2) + '%'; }
        setW('#attDaSegPresent', pctOf(c.present));
        setW('#attDaSegAbsent',  pctOf(c.absent));
        setW('#attDaSegLate',    pctOf(c.late));
        setW('#attDaSegHalf',    pctOf(c.half_day));
        setW('#attDaSegExcused', pctOf(c.excused));

        var presentPct = total ? Math.round(c.present * 100 / total) : 0;

        var dateEl = q('#attDaDate');
        if (dateEl) dateEl.textContent = currentDate || TODAY;
        var sumTot = q('#attDaSummaryTotal');
        if (sumTot) sumTot.innerHTML = 'Total <b>' + total + '</b>';
        var sumPre = q('#attDaSummaryPresent');
        if (sumPre) sumPre.innerHTML = 'Present <b>' + presentPct + '%</b>';

        box.style.display = 'block';
    }

    function init() {
        var anyClass = false;

        if (CT_CLASSES && CT_CLASSES.length) {
            var h = '';
            CT_CLASSES.forEach(function(c) {
                var sc = statusClass(c.status);
                var st = statusText(c.status);
                var autoTag = c.auto_marked
                    ? '<span class="att-tag auto">AUTO</span>' : '';
                h += '<div class="att-class-card status-' + sc + '" onclick="AXIS_ATT.openClass(' + c.id + ')">';
                h +=   '<div class="att-class-card-head">';
                h +=     '<div class="att-class-head-left">';
                h +=       '<span class="att-class-icon" aria-hidden="true">' + svgClassIcon() + '</span>';
                h +=       '<div class="att-class-name">' + esc(c.name) + autoTag + '</div>';
                h +=     '</div>';
                h +=     '<div class="att-status-chip att-' + sc + '">' + st + '</div>';
                h +=   '</div>';
                h +=   '<div class="att-class-card-meta">';
                h +=     c.student_count + ' students · ' + c.marked_today + ' marked today';
                h +=   '</div>';
                h +=   '<div class="att-class-card-perm">';
                h +=     'Permission: ' + permLabel(c.backdate_access)
                       + ' · Max ' + c.max_edits_per_date + ' edit(s)/date';
                h +=   '</div>';
                h += '</div>';
            });
            q('#attCTList').innerHTML = h;
            q('#attCTSection').style.display = 'block';
            anyClass = true;
        }

        if (SUBJECT_PERIODS && SUBJECT_PERIODS.length) {
            var ph = '';
            SUBJECT_PERIODS.forEach(function(p) {
                var sc = p.marked ? 'green' : 'amber';
                var st = p.marked ? 'Marked' : 'Pending';
                ph += '<div class="att-period-card" onclick="AXIS_ATT.openPeriod(' + p.class_id + ',' + p.period_order + ')">';
                ph +=   '<div class="att-period-left"><strong>P' + p.period_order + '</strong></div>';
                ph +=   '<div class="att-period-mid">';
                ph +=     '<div class="att-period-class">' + esc(p.class_name) + '</div>';
                ph +=     '<div class="att-period-subject">' + esc(p.subject || '') + '</div>';
                ph +=   '</div>';
                ph +=   '<div class="att-status-chip att-' + sc + '">' + st + '</div>';
                ph += '</div>';
            });
            q('#attSubList').innerHTML = ph;
            q('#attSubSection').style.display = 'block';
            anyClass = true;
        }

        if (!anyClass) {
            q('#attNoClasses').style.display = 'block';
        }

        q('#attDatePicker').value = TODAY;

        renderAnalytics();
    }

    function back() {
        q('#attHomeView').style.display = 'block';
        q('#attDetailView').style.display = 'none';
        q('#attDatePicker').disabled = false;
        currentClass = null;
        currentPeriod = null;
        currentStudents = [];
        currentPermission = null;
        currentLocked = false;

        /* Restore hero + page holiday banner on the home view */
        toggleHero(true);

        /* Detail analytics are no longer relevant here */
        var da = q('#attDateAnalytics');
        if (da) da.style.display = 'none';
    }

    function switchTab(which) {
        qa('.att-tab').forEach(function(t) {
            t.classList.toggle('active', t.getAttribute('data-tab') === which);
        });
        q('#attTabToday').style.display = which === 'today' ? 'block' : 'none';
        q('#attTabHistory').style.display = which === 'history' ? 'block' : 'none';
        if (which === 'history' && currentClass) loadHistory();
    }

    function openClass(classId) {
        var c = null;
        for (var i = 0; i < CT_CLASSES.length; i++) {
            if (CT_CLASSES[i].id === classId) { c = CT_CLASSES[i]; break; }
        }
        if (!c) return;
        currentClass = c;
        currentPeriod = null;
        currentDate = TODAY;
        q('#attDetailTitle').textContent = c.name;
        q('#attDetailSub').textContent = c.student_count + ' students · Class Teacher';
        q('#attDatePicker').value = TODAY;
        q('#attDatePicker').disabled = false;
        q('#attHomeView').style.display = 'none';
        q('#attDetailView').style.display = 'block';
        switchTab('today');

        /* Hide hero + page banner while marking attendance */
        toggleHero(false);

        loadStudents(c.id, null, TODAY);
    }

    function openPeriod(classId, periodOrder) {
        currentPeriod = periodOrder;
        currentClass = { id: classId, name: 'Period ' + periodOrder };
        currentDate = TODAY;
        q('#attDetailTitle').textContent = 'Period ' + periodOrder;
        q('#attDetailSub').textContent = 'Subject period attendance';
        q('#attDatePicker').value = TODAY;
        q('#attDatePicker').disabled = true;
        q('#attHomeView').style.display = 'none';
        q('#attDetailView').style.display = 'block';
        switchTab('today');

        toggleHero(false);

        loadStudents(classId, periodOrder, TODAY);
    }

    function loadDate() {
        if (!currentClass) return;
        currentDate = q('#attDatePicker').value || TODAY;
        loadStudents(currentClass.id, currentPeriod, currentDate);
    }

    function loadStudents(classId, periodOrder, date) {
        var url = '/portal/staff/api/attendance/students/?class_id=' + classId
                + '&date=' + encodeURIComponent(date);
        if (periodOrder) url += '&period_order=' + periodOrder;

        q('#attTodayStudents').innerHTML = '<div class="att-empty">Loading…</div>';
        q('#attTodayBanner').style.display = 'none';
        q('#attTodayLock').style.display = 'none';
        q('#attSaveMsg').textContent = '';
        q('#attSaveMsg').className = 'att-save-msg';

        /* Hide stale date analytics until new data lands. */
        var da = q('#attDateAnalytics');
        if (da) da.style.display = 'none';

        fetch(url, { headers: {'X-Requested-With': 'XMLHttpRequest'} })
            .then(function(r) { return r.json(); })
            .then(function(j) {
                if (!j.ok) {
                    q('#attTodayStudents').innerHTML =
                        '<div class="att-empty">' + esc(j.error || 'Failed') + '</div>';
                    return;
                }
                if (j.is_holiday) {
                    var b = q('#attTodayBanner');
                    b.className = 'att-banner holiday';
                    b.innerHTML = svgHoliday()
                        + '<span>' + esc(j.holiday_reason || 'Holiday') + '</span>';
                    b.style.display = 'flex';
                    q('#attTodayStudents').innerHTML =
                        '<div class="att-empty">No attendance is expected on a holiday.</div>';
                    q('#attSaveBtn').disabled = true;
                    q('#attSaveBtn').style.opacity = 0.5;
                    q('#attBulkPresent').disabled = true;
                    q('#attBulkAbsent').disabled = true;
                    return;
                }
                currentStudents = j.students || [];
                currentPermission = j.permission || null;
                currentLocked = !!j.locked;

                if (currentLocked) {
                    var lk = q('#attTodayLock');
                    lk.className = 'att-banner locked';
                    lk.innerHTML = svgLock()
                        + '<span>' + esc(j.lock_reason || 'This date is locked for you.') + '</span>';
                    lk.style.display = 'flex';
                } else if (j.auto_marked) {
                    var b2 = q('#attTodayBanner');
                    b2.className = 'att-banner auto';
                    b2.innerHTML = svgBot()
                        + '<span>This date was auto-marked by the system. You can still edit it.</span>';
                    b2.style.display = 'flex';
                }

                renderStudents();
                renderDateAnalytics();
                updateQuota();

                q('#attSaveBtn').disabled = currentLocked;
                q('#attSaveBtn').style.opacity = currentLocked ? 0.5 : 1;
                q('#attBulkPresent').disabled = currentLocked;
                q('#attBulkAbsent').disabled = currentLocked;
            })
            .catch(function() {
                q('#attTodayStudents').innerHTML =
                    '<div class="att-empty">Network error.</div>';
            });
    }

    function renderStudents() {
        if (!currentStudents.length) {
            q('#attTodayStudents').innerHTML =
                '<div class="att-empty">No active students in this class.</div>';
            return;
        }
        var h = '';
        currentStudents.forEach(function(s) {
            var tags = '';
            if (s.on_leave) tags += '<span class="att-tag leave">ON LEAVE</span>';
            if (s.is_auto)  tags += '<span class="att-tag auto">AUTO</span>';
            h += '<div class="att-student-row">';
            h +=   '<div class="att-student-info">';
            h +=     '<div class="att-student-roll">' + esc(s.roll_number || '—') + '</div>';
            h +=     '<div class="att-student-name">' + esc(s.name) + tags + '</div>';
            h +=     '<div class="att-student-meta">' + esc(s.father_name || '') + '</div>';
            h +=   '</div>';
            h +=   '<div class="att-status-group">';
            ['present', 'absent', 'late', 'half_day', 'excused'].forEach(function(v) {
                var label = {present:'P', absent:'A', late:'L', half_day:'H', excused:'Lv'}[v];
                var id = 'att_' + s.id + '_' + v;
                var checked = (s.status === v) ? 'checked' : '';
                var dis = currentLocked ? 'disabled' : '';
                h += '<input type="radio" name="st_' + s.id + '" id="' + id + '" value="' + v + '" ' + checked + ' ' + dis + '>';
                h += '<label for="' + id + '">' + label + '</label>';
            });
            h +=   '</div>';
            h += '</div>';
        });
        q('#attTodayStudents').innerHTML = h;
    }

    function updateQuota() {
        var el = q('#attQuotaInfo');
        if (!currentPermission || !currentPermission.is_class_teacher) {
            el.textContent = '';
            return;
        }
        if (currentDate === TODAY) {
            el.textContent = "Marking today's attendance.";
        } else {
            el.textContent = 'Edits used: ' + currentPermission.quota_used
                + ' / ' + currentPermission.quota_max
                + (currentPermission.quota_remaining > 0
                    ? ' (' + currentPermission.quota_remaining + ' remaining)'
                    : '');
        }
    }

    function bulk(status) {
        if (currentLocked) return;
        currentStudents.forEach(function(s) {
            var el = document.getElementById('att_' + s.id + '_' + status);
            if (el) el.checked = true;
            /* keep local model in sync so analytics reflect bulk change */
            s.status = status;
        });
        renderDateAnalytics();
    }

    function confirmSave() {
        if (currentLocked) return;
        var isEdit = currentDate !== TODAY;
        q('#attConfirmBody').textContent = isEdit
            ? 'Are you sure? This will use 1 of your remaining edits for ' + currentDate + '.'
            : "Are you sure you want to save today's attendance?";
        q('#attConfirmModal').classList.add('open');
    }

    function closeConfirm() {
        q('#attConfirmModal').classList.remove('open');
    }

    function doSave() {
        closeConfirm();
        if (!currentClass) return;

        var records = currentStudents.map(function(s) {
            var el = document.querySelector('input[name="st_' + s.id + '"]:checked');
            return { student_id: s.id, status: el ? el.value : 'present' };
        });
        var payload = {
            class_id: currentClass.id,
            date: currentDate,
            records: records,
        };
        if (currentPeriod) payload.period_order = currentPeriod;

        var msg = q('#attSaveMsg');
        msg.textContent = 'Saving…';
        msg.className = 'att-save-msg';

        fetch('/portal/staff/api/attendance/mark/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrf(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(payload),
        })
            .then(function(r) { return r.json(); })
            .then(function(j) {
                if (!j.ok) {
                    msg.textContent = j.error || 'Save failed.';
                    msg.className = 'att-save-msg err';
                    if (j.permission) {
                        currentPermission = j.permission;
                        updateQuota();
                    }
                    return;
                }
                msg.textContent = 'Saved ' + j.saved + ' record(s).';
                msg.className = 'att-save-msg ok';
                if (j.permission) {
                    currentPermission = j.permission;
                    updateQuota();
                }
                setTimeout(function() { window.location.reload(); }, 900);
            })
            .catch(function() {
                msg.textContent = 'Network error.';
                msg.className = 'att-save-msg err';
            });
    }

    function loadHistory() {
        if (!currentClass) return;
        q('#attHistoryList').innerHTML = '<div class="att-empty">Loading…</div>';
        fetch('/portal/staff/api/attendance/dates/?class_id=' + currentClass.id,
              { headers: {'X-Requested-With': 'XMLHttpRequest'} })
            .then(function(r) { return r.json(); })
            .then(function(j) {
                if (!j.ok) {
                    q('#attHistoryList').innerHTML =
                        '<div class="att-empty">' + esc(j.error || 'Failed') + '</div>';
                    return;
                }
                var h = '';
                (j.dates || []).forEach(function(d) {
                    var badge = '';
                    if (d.is_holiday) {
                        badge = '<span class="att-status-chip att-blue">Holiday</span>';
                    } else if (d.status === 'completed') {
                        badge = '<span class="att-status-chip att-green">Completed</span>';
                    } else if (d.status === 'partial') {
                        badge = '<span class="att-status-chip att-amber">Partial</span>';
                    } else {
                        badge = '<span class="att-status-chip att-red">Pending</span>';
                    }

                    var action = '';
                    if (d.can_edit) {
                        action = '<button type="button" class="att-mini-btn" '
                               + 'onclick="event.stopPropagation(); AXIS_ATT.jumpToDate(\''
                               + d.date + '\')">Edit</button>';
                    } else if (!d.can_view) {
                        action = '<span class="att-mini-lock">Locked</span>';
                    } else {
                        action = '<span class="att-mini-lock">View</span>';
                    }

                    var autoTag = d.auto_marked > 0
                        ? '<span class="att-tag auto">AUTO ×' + d.auto_marked + '</span>'
                        : '';

                    h += '<div class="att-history-row" '
                       + 'onclick="AXIS_ATT.jumpToDate(\'' + d.date + '\')">';
                    h +=   '<div>';
                    h +=     '<div class="att-history-date">' + d.date + '</div>';
                    h +=     '<div class="att-history-day">' + d.day_name
                           + (d.is_today ? ' · Today' : '') + '</div>';
                    h +=   '</div>';
                    h +=   '<div class="att-history-status">';
                    h +=     badge + autoTag;
                    h +=     '<div class="att-history-counts">'
                           + d.marked + '/' + d.total_students + ' marked</div>';
                    if (d.quota_max > 0 && !d.is_today && !d.is_holiday) {
                        h += '<div class="att-history-quota">Edits '
                           + d.quota_used + '/' + d.quota_max + '</div>';
                    }
                    h +=   '</div>';
                    h +=   '<div class="att-history-action">' + action + '</div>';
                    h += '</div>';
                });
                q('#attHistoryList').innerHTML = h
                    || '<div class="att-empty">No history available.</div>';
            })
            .catch(function() {
                q('#attHistoryList').innerHTML =
                    '<div class="att-empty">Network error.</div>';
            });
    }

    function jumpToDate(date) {
        if (!currentClass) return;
        if (currentPeriod) {
            alert('History navigation is only available for the class-teacher view.');
            return;
        }
        q('#attDatePicker').value = date;
        currentDate = date;
        switchTab('today');
        loadStudents(currentClass.id, null, date);
    }

    document.addEventListener('DOMContentLoaded', init);

    return {
        openClass: openClass,
        openPeriod: openPeriod,
        back: back,
        switchTab: switchTab,
        loadDate: loadDate,
        bulk: bulk,
        confirmSave: confirmSave,
        closeConfirm: closeConfirm,
        doSave: doSave,
        jumpToDate: jumpToDate,
    };
})();
</script>
{% endblock %}
'''

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


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Update templates/mobile/staff/attendence.html: hide hero in "
            "detail view, add class card SVG icons, add per-date analytics."
        ),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing anything to disk.")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed output for every action.")
    parser.add_argument("--target-dir", default=".",
                        help="Project root directory (default: current directory).")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    log = Log(args.verbose)

    try:
        root = Path(args.target_dir).expanduser().resolve()
    except OSError as exc:  # pragma: no cover - defensive
        print(f"[{ts()}] ERROR: cannot resolve target dir: {exc}", file=sys.stderr)
        return 1

    target = root / TARGET_REL_PATH

    log.info(f"target root : {root}")
    log.info(f"target file : {target}")
    if args.dry_run:
        log.info("mode        : DRY RUN (no files will be written)")

    if not root.exists() or not root.is_dir():
        log.error(f"target directory does not exist or is not a directory: {root}")
        return 1

    existing: str | None = None
    if target.exists():
        if target.is_dir():
            log.error(f"target path is a directory, not a file: {target}")
            return 1
        try:
            existing = target.read_text(encoding="utf-8")
            log.detail(f"read existing file ({len(existing)} chars)")
        except OSError as exc:
            log.error(f"could not read existing file: {exc}")
            return 1
    else:
        log.detail("target file does not exist yet; it will be created")

    if existing is not None and existing == NEW_CONTENT:
        log.info("already up to date - nothing to do (idempotent no-op)")
        return 0

    if existing is not None:
        if "attHero" not in existing:
            log.info("change #1: hero will now hide inside detail view")
        if "att-class-icon" not in existing:
            log.info("change #2: class cards will get a leading SVG badge")
        if "attDateAnalytics" not in existing:
            log.info("change #3: detail view will gain a per-date analytics strip")

    action = "overwrite" if existing is not None else "create"
    log.info(f"action      : {action} ({len(NEW_CONTENT)} chars)")

    if args.dry_run:
        log.info("dry run complete - no changes were written")
        log.detail("would write: " + str(target))
        return 0

    try:
        if not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            log.info(f"created directory: {target.parent}")
    except OSError as exc:
        log.error(f"could not create directory {target.parent}: {exc}")
        return 1

    try:
        target.write_text(NEW_CONTENT, encoding="utf-8")
    except OSError as exc:
        log.error(f"failed to write {target}: {exc}")
        return 1

    log.info(f"written     : {target}")
    log.info("done. 1 file updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
