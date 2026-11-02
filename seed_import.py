#!/usr/bin/env python3
"""Load the 109 seed links (seed_links.json) as already-seen items (origin='seed', no New badge).
Idempotent: re-running skips items already present. HN seed items get an alias for their target URL so
future HN/YouTube/arXiv sightings of the same link dedupe against the seed."""
import json, os, sys, time, urllib.request
from wtube import store, fetchers

def main():
    db = store.connect()
    ts = store.iso(store.now_utc())
    seeds = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_links.json")))
    added = skipped = aliases = 0
    for s in seeds:
        key = store.stable_key(s["url"])
        canon = store.canonical_url(s["url"])
        # the seed list is curated: only an exact key match counts as "already present"
        if db.execute("SELECT 1 FROM items WHERE item_key=?", (key,)).fetchone():
            skipped += 1
            continue
        parts = s["site"].split(" · ")
        item = dict(key=key, canonical_url=canon, url=s["url"], title=s["title"], kind=s["kind"], topic=s["topic"],
                    site=" · ".join(parts[1:]) or parts[0], summary="", score=None, classifier="seed",
                    published_at=(s.get("date") or None), discussion_url=s["url"] if key.startswith("hn:") else None)
        store.insert_item(db, item, "seed", ts)
        store.add_mention(db, "seed", key, key, s["url"], ts)
        added += 1
        if key.startswith("hn:"):
            try:
                d = json.loads(fetchers.http_get(f"https://hn.algolia.com/api/v1/items/{key[3:]}"))
                if d.get("url"):
                    store.add_alias(db, store.stable_key(d["url"]), key)
                    aliases += 1
                time.sleep(0.2)
            except Exception as e:
                print(f"  warn: could not resolve HN target for {key}: {e}", file=sys.stderr)
    db.commit()
    total = db.execute("SELECT COUNT(*) FROM items WHERE origin='seed'").fetchone()[0]
    print(f"seed import: added {added}, already present {skipped}, HN target aliases {aliases}; seed items in DB: {total}")

if __name__ == "__main__":
    main()
