#!/usr/bin/env bash
# Daily entry point: run the checker and rebuild the static site + Page data.
# Schedule once a day at 13:00 UTC (5:00 AM PT). GitHub Actions runs this via .github/workflows/publish.yml.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
LOG="logs/run_$(date -u +%Y%m%dT%H%M%SZ).log"
{
  echo "== Wireless Tube daily run $(date -u +%FT%TZ) =="
  [ -f wireless_tube.db ] || python3 seed_import.py      # first run only
  python3 check.py
  python3 verify_site.py
} 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
