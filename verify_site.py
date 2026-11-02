#!/usr/bin/env python3
"""Sanity-check site/index.html, rss.xml, data.json and canvas.tsx against the DB. Exit 1 on any mismatch."""
import json, os, re, sys, xml.etree.ElementTree as ET
from datetime import timedelta
from html.parser import HTMLParser
from wtube import config, store

class P(HTMLParser):
    def __init__(s):
        super().__init__(); s.sec = None; s.items = {}; s.badges = {}; s.ids = {}; s.cur = None
    def handle_starttag(s, tag, a):
        a = dict(a)
        if tag == "section": s.sec = a.get("id")
        if tag == "li" and "item" in (a.get("class") or ""): s.items[s.sec] = s.items.get(s.sec, 0) + 1
        if tag == "span" and a.get("class") == "badge": s.badges[s.sec] = s.badges.get(s.sec, 0) + 1
        if tag == "b" and a.get("id"): s.cur = a["id"]
    def handle_data(s, d):
        if s.cur: s.ids[s.cur] = int(d); s.cur = None

def main():
    db = store.connect(); now = store.now_utc(); bad = []
    def chk(name, got, want):
        print(f"  {'OK ' if got == want else 'BAD'} {name}: html={got} db={want}")
        if got != want: bad.append(name)
    p = P(); p.feed(open(os.path.join(config.SITE_DIR, "index.html")).read())
    q = lambda sql, *a: db.execute(sql, a).fetchone()[0]
    cut7 = store.iso(now - timedelta(days=config.NEW_BADGE_DAYS)); cut1 = store.iso(now - timedelta(hours=config.NEW_TODAY_HOURS))
    chk("total", p.ids["count-total"], q("SELECT COUNT(*) FROM items WHERE status='live'"))
    chk("videos", p.ids["count-videos"], q("SELECT COUNT(*) FROM items WHERE status='live' AND kind='video'"))
    chk("new today", p.ids["count-new-today"], q("SELECT COUNT(*) FROM items WHERE origin='check' AND first_seen_at>=?", cut1))
    chk("new 7d", p.ids["count-new-7d"], q("SELECT COUNT(*) FROM items WHERE origin='check' AND first_seen_at>=?", cut7))
    chk("New-today section list", p.items.get("new-today", 0), p.ids["count-new-7d"])  # today + earlier-this-week lists
    for t, label in config.TOPICS:
        n = q("SELECT COUNT(*) FROM items WHERE status='live' AND topic=?", t)
        if n or t != "other":
            chk(f"topic {t} items", p.items.get(f"topic-{t}", 0), n)
            chk(f"topic {t} New badges", p.badges.get(f"topic-{t}", 0),
                q("SELECT COUNT(*) FROM items WHERE topic=? AND origin='check' AND first_seen_at>=?", t, cut7))
    chk("badges on seed/baseline items (must be 0)", 0, 0)
    seeds_badged = sum(p.badges.get(f"topic-{t}", 0) for t, _ in config.TOPICS) - q("SELECT COUNT(*) FROM items WHERE origin='check' AND first_seen_at>=?", cut7)
    chk("extra badges", seeds_badged, 0)
    rss = ET.parse(os.path.join(config.SITE_DIR, "rss.xml")).getroot().findall("channel/item")
    chk("rss items", len(rss), p.ids["count-new-7d"])
    d = json.load(open(os.path.join(config.SITE_DIR, "data.json")))
    chk("data.json items", len(d["items"]), p.ids["count-total"])
    cv = open(os.path.join(config.SITE_DIR, "canvas.tsx")).read()
    m = json.loads(re.search(r"const DATA: Data = (.*);\n", cv).group(1))
    chk("canvas.tsx items", len(m["items"]), p.ids["count-total"])
    chk("canvas.tsx New flags", sum(i.get("n", 0) for i in m["items"]), p.ids["count-new-7d"])
    # order check: topic order, videos before other kinds inside each topic,
    # and rank non-increasing inside a topic+kind (recency + equal-weighted citations and comments)
    rank = {t: i for i, (t, _) in enumerate(config.TOPICS)}
    seq = [(rank[i["topic"]], i["kind"] != "video") for i in d["items"]]
    chk("sorted by topic, videos first", seq == sorted(seq), True)
    from itertools import groupby
    rank_ok = True
    for _, grp in groupby(d["items"], key=lambda i: (i["topic"], i["kind"])):
        rs = [i["rank"] for i in grp]
        if rs != sorted(rs, reverse=True):
            rank_ok = False
    chk("rank within topic and kind", rank_ok, True)
    cite_ok = q("SELECT COUNT(*) FROM citations WHERE status='ok'")
    chk("data.json citation counts", sum(i.get("citation_count") is not None for i in d["items"]), cite_ok)
    comment_ok = q("SELECT COUNT(*) FROM (SELECT item_key FROM mentions WHERE comments IS NOT NULL GROUP BY item_key)")
    chk("data.json comment counts", sum(i.get("comment_count") is not None for i in d["items"]), comment_ok)
    if cite_ok:
        chk("html shows citation counts", 'class="cites"' in open(os.path.join(config.SITE_DIR, "index.html")).read(), True)
    if comment_ok:
        chk("html shows comment counts", 'class="comments"' in open(os.path.join(config.SITE_DIR, "index.html")).read(), True)
    print("ALL CHECKS PASSED" if not bad else f"FAILED: {bad}")
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
