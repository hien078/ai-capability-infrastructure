#!/usr/bin/env bash
# Weekly acquisition cycle (user decision 2026-09-29): scheduled crawl +
# tiered auto-approval. ~10 minutes a week, demand-scoped.
#
# The cycle (auto2.md §95 cadence + §97 demand priority):
#   1. gap detector      — what do clients actually ask for? (demand scope)
#   2. crawler           — scan KNOWN sources for new skills (trusted tier)
#   3. scout             — ONLY if the gap detector found real gaps
#   4. auto-approve       — known tier → approved_for_fetch (no human)
#   5. report            — what is new, what needs YOUR eyes
#
# Scheduling: this script is idempotent and safe to run any time.
# Install the weekly schedule with:
#   crontab -e
#     # ACI weekly acquisition cycle — Mondays 09:23
#     23 9 * * 1  cd /home/hien/Data/Projects/AII && ACI_DATABASE_URL=postgresql+psycopg://aci:aci@localhost:5432/aci_bench .venv/bin/python scripts/weekly_cycle.sh >> data/cycle.log 2>&1
#
# (The user installs the cron line themselves — the platform never edits
# the user's crontab.)

set -euo pipefail
cd "$(dirname "$0")/.."

export ACI_DATABASE_URL="${ACI_DATABASE_URL:-postgresql+psycopg://aci:aci@localhost:5432/aci_bench}"
PY=.venv/bin/python

echo "=== ACI weekly acquisition cycle $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

echo "--- 1/4 gap detector (demand scope) ---"
$PY scripts/detect_gaps.py || true

echo ""
echo "--- 2/4 known-source crawler ---"
$PY scripts/crawl_known_sources.py || true

echo ""
echo "--- 3/4 auto-approve (known tier only) ---"
$PY scripts/auto_approve.py || true

echo ""
echo "--- 4/4 health + deprecation proposals ---"
$PY scripts/detect_deprecation.py || true

echo ""
echo "=== cycle complete ==="
echo "human gate: capctl promotion approve — staging evidence is in the DB;"
echo "run 'capctl promotion propose <cap> <ver>' for anything you want to promote."
