"""Fetch -> filter -> dedupe -> store."""
import json, time, traceback
from datetime import timedelta
from . import config, fetchers, filters, store

# name, function, dated?  (undated scrapers: first run = baseline, no New badges)
SOURCES = [
    ("hn", lambda since, errs: fetchers.fetch_hn(since), True),
    ("arxiv", lambda since, errs: fetchers.fetch_arxiv(since), True),
    ("youtube", lambda since, errs: fetchers.fetch_youtube_rss(since, errs), True),
    ("youtube_api", None, True),          # special-cased: needs YOUTUBE_API_KEY
    ("reddit", None, True),               # disabled stub
    ("3gpp", lambda since, errs: fetchers.fetch_3gpp(since), True),
    ("nga", lambda since, errs: fetchers.fetch_nga(since), False),
    ("ieeetv", lambda since, errs: fetchers.fetch_ieeetv(since), False),
]

def _since(db, name, now):
    r = db.execute("SELECT last_success_at FROM source_state WHERE source=?", (name,)).fetchone()
    if name in config.ROLLING_LOOKBACK_DAYS:
        return now - timedelta(days=config.ROLLING_LOOKBACK_DAYS[name]), not (r and r[0])
    if r and r[0]:
        return store.parse_iso(r[0]) - timedelta(hours=config.OVERLAP_HOURS), False
    return now - timedelta(days=config.FIRST_LOOKBACK_DAYS.get(name, 7)), True

def process(db, raw, origin, since_dt, ts, st):
    if not raw["title"]:
        return
    pub = store.parse_iso(raw.get("published_at"))
    if pub and pub < since_dt:
        st["old"] += 1
        return
    sc, hits = filters.score(raw["title"], raw.get("summary"), raw["source"], raw["kind"], raw.get("points"))
    verdict = filters.decide(sc)
    if verdict == "borderline":
        db.execute("INSERT OR REPLACE INTO borderline VALUES (?,?,?,?,?,?)",
                   (raw["source"], raw["source_id"], raw["title"], raw["url"], sc, ts))
        st["borderline"] += 1
        if not (config.LLM_BORDERLINE and filters.llm_classify(raw) == "keep"):
            return
        classifier = "llm:keep"
    elif verdict == "drop":
        st["dropped"] += 1
        return
    else:
        classifier = "regex"
    st["passed"] += 1
    canon = store.canonical_url(raw["url"])
    key = store.stable_key(raw["url"])
    cand = [key] + ([f"hn:{raw['source_id']}"] if raw["source"] == "hn" else [])
    existing, how = store.resolve(db, cand, [canon], store.norm_title(raw["title"]))
    if existing:
        st["duplicates"] += 1
        st["dup_by_" + how] = st.get("dup_by_" + how, 0) + 1
        db.execute("UPDATE items SET last_seen_at=? WHERE item_key=?", (ts, existing))
        for c in cand:
            if c != existing:
                store.add_alias(db, c, existing)
        store.add_mention(db, raw["source"], raw["source_id"], existing, raw.get("discussion_url") or raw["url"], ts,
                          raw.get("points"), raw.get("comments"))
        return
    item = dict(key=key, canonical_url=canon, url=raw["url"], title=raw["title"], kind=raw["kind"],
                topic=filters.topic_for(raw["title"], raw.get("summary")), site=raw.get("site"), summary=raw.get("summary"),
                score=sc, classifier=classifier, published_at=raw.get("published_at"), discussion_url=raw.get("discussion_url"))
    store.insert_item(db, item, origin, ts)
    for c in cand[1:]:
        store.add_alias(db, c, key)
    store.add_mention(db, raw["source"], raw["source_id"], key, raw.get("discussion_url") or raw["url"], ts,
                      raw.get("points"), raw.get("comments"))
    st["new" if origin == "check" else "baseline"] += 1
    st.setdefault("new_titles", []).append(raw["title"][:120])

def run(only=None):
    db = store.connect()
    now = store.now_utc()
    ts = store.iso(now)
    cur = db.execute("INSERT INTO runs(started_at) VALUES (?)", (ts,))
    run_id = cur.lastrowid
    db.commit()
    stats = {}
    for name, fn, dated in SOURCES:
        if only and name not in only:
            continue
        st = dict(status="ok", fetched=0, old=0, dropped=0, borderline=0, passed=0, duplicates=0, new=0, baseline=0, errors=[])
        stats[name] = st
        since_dt, first = _since(db, name, now)
        try:
            if name == "reddit":
                st["status"] = "disabled (needs a registered Reddit app; see fetchers.fetch_reddit)"
                continue
            if name == "youtube_api":
                raw = fetchers.fetch_youtube_api(store.iso(since_dt))
                if raw is None:
                    st["status"] = "skipped (YOUTUBE_API_KEY not set)"
                    continue
            else:
                raw = fn(since_dt.timestamp(), st["errors"])
            st["fetched"] = len(raw)
            origin = "baseline" if (first and not dated) else "check"
            for r in raw:
                process(db, r, origin, since_dt if dated else since_dt.replace(year=1970), ts, st)
            db.execute("""INSERT INTO source_state(source, last_success_at, last_error, consecutive_failures) VALUES (?,?,NULL,0)
                          ON CONFLICT(source) DO UPDATE SET last_success_at=excluded.last_success_at, last_error=NULL,
                          consecutive_failures=0""", (name, ts))
            if st["errors"]:
                st["status"] = f"partial ({len(st['errors'])} sub-feed errors)"
            db.commit()
        except Exception as e:
            db.rollback()
            st["status"] = "failed"
            st["errors"].append(f"{type(e).__name__}: {e}")
            db.execute("""INSERT INTO source_state(source, last_error, consecutive_failures) VALUES (?,?,1)
                          ON CONFLICT(source) DO UPDATE SET last_error=excluded.last_error,
                          consecutive_failures=consecutive_failures+1""", (name, str(e)[:500]))
            db.commit()
    db.execute("UPDATE runs SET finished_at=?, stats=? WHERE run_id=?", (store.iso(store.now_utc()), json.dumps(stats), run_id))
    db.commit()
    return run_id, stats
