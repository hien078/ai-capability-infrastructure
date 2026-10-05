#!/usr/bin/env bash
# Weekly READ-ONLY ACI telemetry reports (operational host, home-sever).
#
# The "come back when telemetry is sufficient" instrument (benchmark pause,
# 2026-10-03): every week, snapshot what real clients did. All three scripts
# only read the operational DB — none ingests, promotes or writes the
# registry. (NOT weekly_cycle.sh: that is the FROZEN acquisition cycle.)
#
#   usage_report.py           ORGANIC vs MEASUREMENT route_runs, outcomes, agent_runs
#   evidence_completeness.py  how much outcome evidence the bundles carry
#   detect_gaps.py            demand with no serving skill (proposal-only)
#
# Layout:  data/reports/<YYYY-MM-DD>/{usage,evidence,gaps}.txt  (keep 26 = ~6 months)
# Env:     ACI_DATABASE_URL     (default aci_bench on localhost)
#          ACI_REPORTS_KEEP     retention count (default 26)
# Run by deploy/systemd/aci-weekly-reports.{service,timer} (systemd --user).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
export ACI_DATABASE_URL="${ACI_DATABASE_URL:-postgresql+psycopg://aci:aci@127.0.0.1:5432/aci_bench}"
KEEP="${ACI_REPORTS_KEEP:-26}"
OUT="$REPO/data/reports/$(date +%F)"
mkdir -p "$OUT"

status=0
run() {
    local name="$1"; shift
    if "$PY" "$REPO/scripts/$1" "${@:2}" > "$OUT/$name.txt" 2>&1; then
        echo "ok      $name"
    else
        echo "FAILED  $name (see $OUT/$name.txt)"; status=1
    fi
}
run usage usage_report.py
run evidence evidence_completeness.py
run gaps detect_gaps.py --database-url "$ACI_DATABASE_URL"

# Retention: only after a fully successful run; dated dirs sort lexically.
if [[ $status -eq 0 ]]; then
    mapfile -t dirs < <(find "$REPO/data/reports" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort)
    excess=$(( ${#dirs[@]} - KEEP ))
    for ((i = 0; i < excess; i++)); do rm -r -- "${dirs[$i]}"; done
fi
echo "reports: $OUT"
exit $status
