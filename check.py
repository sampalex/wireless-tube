#!/usr/bin/env python3
"""Daily checker: fetch all sources, filter, dedupe, store, then rebuild site/ (index.html, rss.xml, data.json, canvas.tsx).
Usage: python3 check.py [--only hn,arxiv,...] [--no-build]"""
import argparse, json, sys
from wtube import pipeline, build

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated source names: " + ",".join(n for n, *_ in pipeline.SOURCES))
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    run_id, stats = pipeline.run(set(a.only.split(",")) if a.only else None)
    print(f"run {run_id}")
    total_new = 0
    for name, st in stats.items():
        total_new += st.get("new", 0)
        print(f"  {name:12s} {st['status']:<60.60s} fetched={st['fetched']:<4} kept={st['passed']:<4} new={st['new']:<4} "
              f"baseline={st['baseline']:<3} dup={st['duplicates']:<3} borderline={st['borderline']:<4} dropped={st['dropped']:<4} too_old={st['old']}")
        for e in st["errors"][:5]:
            print(f"      error: {e}")
    print(f"TOTAL new items this run: {total_new}")
    if not a.no_build:
        print(build.build())
    enabled = [st for st in stats.values() if not st["status"].startswith(("disabled", "skipped"))]
    sys.exit(2 if enabled and all(st["status"] == "failed" for st in enabled) else 0)   # 2 = every source failed

if __name__ == "__main__":
    main()
