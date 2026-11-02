"""Render site/index.html, site/rss.xml, site/data.json and site/canvas.tsx from the DB."""
import html, json, os
from datetime import timedelta
from email.utils import format_datetime
from zoneinfo import ZoneInfo
from . import citations, config, store

KIND_ORDER = {"video": 0, "paper": 1, "report": 2, "page": 3, "thread": 4}
PT = ZoneInfo(config.TZ_DISPLAY)

def pt(iso_s, fmt="%b %-d, %Y %-I:%M %p PT"):
    d = store.parse_iso(iso_s)
    return d.astimezone(PT).strftime(fmt) if d else ""

def collect():
    db = store.connect()
    now = store.now_utc()
    badge_cut = now - timedelta(days=config.NEW_BADGE_DAYS)
    today_cut = now - timedelta(hours=config.NEW_TODAY_HOURS)
    topic_rank = {t: i for i, (t, _) in enumerate(config.TOPICS)}
    rows = db.execute("SELECT * FROM items WHERE status='live'").fetchall()
    ment = {}
    for m in db.execute("SELECT item_key, source FROM mentions"):
        ment.setdefault(m["item_key"], set()).add(m["source"])
    cite_of, comment_of = citations.load_signals(db)
    items = []
    for r in rows:
        fs = store.parse_iso(r["first_seen_at"])
        is_new = r["origin"] == "check" and fs >= badge_cut
        citation_count = cite_of.get(r["item_key"])
        comment_count = comment_of.get(r["item_key"])
        scored = citations.rank_score(citation_count, comment_count, r["published_at"], r["first_seen_at"], now)
        when = store.parse_iso(r["published_at"]) or fs
        items.append(dict(
            key=r["item_key"], title=r["title"], url=r["url"], kind=r["kind"], topic=r["topic"],
            topic_label=config.TOPIC_LABEL.get(r["topic"], r["topic"]), site=r["site"] or "", origin=r["origin"],
            published_at=r["published_at"], first_seen_at=r["first_seen_at"], first_seen_pt=pt(r["first_seen_at"], "%b %-d, %Y"),
            is_new=is_new, new_today=is_new and fs >= today_cut, discussion_url=r["discussion_url"],
            sources=sorted(ment.get(r["item_key"], [])), score=r["score"],
            citation_count=citation_count, comment_count=comment_count, rank=round(scored["rank"], 6),
            _ts=when.timestamp() if when else 0))
    # Topic, then videos before other kinds. Inside a kind, rank (recency + equal-weighted citations and comments).
    items.sort(key=lambda i: (topic_rank.get(i["topic"], 99), KIND_ORDER.get(i["kind"], 9), -i["rank"], -i["_ts"], i["key"]))
    for i in items:
        i.pop("_ts", None)
    last = db.execute("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY run_id DESC LIMIT 1").fetchone()
    topics = []
    for t, label in config.TOPICS:
        its = [i for i in items if i["topic"] == t]
        if its or t != "other":
            topics.append(dict(id=t, label=label, count=len(its), videos=sum(i["kind"] == "video" for i in its),
                               new=sum(i["is_new"] for i in its), new_today=sum(i["new_today"] for i in its)))
    run_stats = json.loads(last["stats"]) if last and last["stats"] else {}
    for st in run_stats.values():
        st.pop("new_titles", None)
    return dict(
        generated_at=store.iso(now), generated_at_pt=pt(store.iso(now)),
        last_run=dict(run_id=last["run_id"], started_at_pt=pt(last["started_at"]), stats=run_stats) if last else None,
        counts=dict(total=len(items), videos=sum(i["kind"] == "video" for i in items), seed=sum(i["origin"] == "seed" for i in items),
                    new_7d=sum(i["is_new"] for i in items), new_today=sum(i["new_today"] for i in items)),
        badge_days=config.NEW_BADGE_DAYS, topics=topics, items=items)

CSS = """
:root{--bg:#fafafa;--card:#fff;--txt:#111;--mut:#666;--line:#e5e5e5;--acc:#0a66ff;--new:#e8590c}
@media (prefers-color-scheme:dark){:root{--bg:#111;--card:#1a1a1a;--txt:#eee;--mut:#999;--line:#2a2a2a;--acc:#5b9bff}}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--txt)}
header{padding:24px 20px 8px;max-width:1100px;margin:auto}h1{margin:0 0 4px;font-size:26px}.mut{color:var(--mut)}
nav{display:flex;flex-wrap:wrap;gap:8px;padding:8px 20px;max-width:1100px;margin:auto}nav a{padding:4px 10px;border:1px solid var(--line);border-radius:999px;text-decoration:none;color:var(--txt);font-size:13px}
main{max-width:1100px;margin:auto;padding:0 20px 40px}section{margin-top:28px}h2{font-size:19px;margin:0 0 10px}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px}.stat b{font-size:20px;display:block}
ul.items{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px}
li.item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
li.item a.t{color:var(--txt);font-weight:600;text-decoration:none}li.item a.t:hover{color:var(--acc)}
.meta{font-size:12.5px;color:var(--mut);margin-top:4px}.kind{font-size:11px;text-transform:uppercase;letter-spacing:.04em;border:1px solid var(--line);border-radius:4px;padding:0 5px;margin-right:6px}
.badge{display:inline-block;background:var(--new);color:#fff;font-size:11px;font-weight:700;border-radius:4px;padding:1px 6px;margin-right:6px;vertical-align:1px}
.cites,.comments{color:var(--txt);font-weight:600}
.empty{color:var(--mut);font-style:italic}footer{max-width:1100px;margin:auto;padding:0 20px 30px;font-size:12px;color:var(--mut)}
"""

def _count_bits(i):
    bits = []
    if i.get("citation_count") is not None:
        n = i["citation_count"]
        bits.append(f'<span class="cites" data-citations="{n}">{n} citation{"" if n == 1 else "s"}</span>')
    if i.get("comment_count") is not None:
        n = i["comment_count"]
        bits.append(f'<span class="comments" data-comments="{n}">{n} comment{"" if n == 1 else "s"}</span>')
    return (" · " + " · ".join(bits)) if bits else ""

def _li(i):
    badge = (f'<span class="badge" data-first-seen="{i["first_seen_at"]}" title="First seen {html.escape(i["first_seen_pt"])}">New</span>'
             if i["is_new"] else "")
    disc = f' · <a href="{html.escape(i["discussion_url"])}">discussion</a>' if i["discussion_url"] and i["discussion_url"] != i["url"] else ""
    first = f' · first seen {html.escape(i["first_seen_pt"])}' if i["is_new"] else ""
    return (f'<li class="item{" is-new" if i["is_new"] else ""}" data-topic="{i["topic"]}" data-kind="{i["kind"]}">{badge}'
            f'<a class="t" href="{html.escape(i["url"])}" target="_blank" rel="noopener">{html.escape(i["title"])}</a>'
            f'<div class="meta"><span class="kind">{i["kind"]}</span>{html.escape(i["site"])}{_count_bits(i)}{first}{disc}</div></li>')

def render_html(d):
    c = d["counts"]
    today = [i for i in d["items"] if i["new_today"]]
    week = [i for i in d["items"] if i["is_new"] and not i["new_today"]]
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Wireless Tube</title><link rel=alternate type=application/rss+xml title='Wireless Tube: new items' href='rss.xml'>"
           f"<style>{CSS}</style></head><body><header><h1>Wireless Tube</h1>"
           f"<div class=mut>5G · 6G · Wi-Fi · MAC/PHY · signal processing · beamforming · massive MIMO. Videos first. "
           f"Updated {html.escape(d['generated_at_pt'])}.</div>"
           f"<div class=stats><div class=stat><b id=count-total>{c['total']}</b>items</div><div class=stat><b id=count-videos>{c['videos']}</b>videos</div>"
           f"<div class=stat><b id=count-new-today>{c['new_today']}</b>new today</div><div class=stat><b id=count-new-7d>{c['new_7d']}</b>new in {d['badge_days']} days</div></div></header>"
           "<nav><a href='#new-today'>New today</a>"]
    for t in d["topics"]:
        out.append(f"<a href='#topic-{t['id']}'>{html.escape(t['label'])} <span class=mut>{t['count']}"
                   f"{' · ' + str(t['new']) + ' new' if t['new'] else ''}</span></a>")
    # Relative (not root-absolute) so the page works at /wireless-tube/ on GitHub Pages.
    out.append("<a href='rss.xml'>RSS</a><a href='data.json'>JSON</a></nav><main>")
    out.append(f"<section id=new-today><h2>New today <span class=mut>({len(today)})</span></h2>")
    out.append("<ul class=items>" + "".join(_li(i) for i in today) + "</ul>" if today else "<p class=empty>Nothing new in the last 24 hours.</p>")
    if week:
        out.append(f"<h2 style='margin-top:18px'>Earlier this week <span class=mut>({len(week)})</span></h2><ul class=items>{''.join(_li(i) for i in week)}</ul>")
    out.append("</section>")
    for t in d["topics"]:
        its = [i for i in d["items"] if i["topic"] == t["id"]]
        out.append(f"<section id='topic-{t['id']}' data-count='{t['count']}'><h2>{html.escape(t['label'])} "
                   f"<span class=mut>({t['count']} · {t['videos']} videos{' · ' + str(t['new']) + ' new' if t['new'] else ''})</span></h2>"
                   + ("<ul class=items>" + "".join(_li(i) for i in its) + "</ul>" if its else "<p class=empty>No items yet.</p>") + "</section>")
    lr = d["last_run"]
    out.append(f"</main><footer>Last checker run #{lr['run_id']} started {html.escape(lr['started_at_pt'])}. " if lr else "</main><footer>")
    out.append(f"New badges last {d['badge_days']} days from first sighting. "
               f"Within each topic, videos come first; then recency plus citations and comments at equal weight. "
               f"Built {html.escape(d['generated_at'])}.</footer>"
               # expire badges client-side too, so a stale build never shows an old New badge
               f"<script>var cut=Date.now()-{d['badge_days']}*864e5;document.querySelectorAll('.badge[data-first-seen]').forEach(function(b){{if(Date.parse(b.dataset.firstSeen)<cut)b.remove();}});</script>"
               "</body></html>")
    return "".join(out)

def render_rss(d):
    items = sorted([i for i in d["items"] if i["is_new"]], key=lambda i: i["first_seen_at"], reverse=True)
    parts = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Wireless Tube: new items</title>'
             '<link>index.html</link><description>New wireless (5G/6G/Wi-Fi/PHY-MAC/DSP/beamforming/massive MIMO) items, last 7 days</description>'
             f'<lastBuildDate>{format_datetime(store.parse_iso(d["generated_at"]))}</lastBuildDate>']
    for i in items:
        parts.append(f"<item><title>{html.escape(i['title'])}</title><link>{html.escape(i['url'])}</link><guid isPermaLink='false'>{html.escape(i['key'])}</guid>"
                     f"<category>{html.escape(i['topic_label'])}</category><description>{html.escape(i['kind'] + ' · ' + i['site'])}</description>"
                     f"<pubDate>{format_datetime(store.parse_iso(i['first_seen_at']))}</pubDate></item>")
    parts.append("</channel></rss>")
    return "".join(parts)

def _slim(i):
    o = dict(t=i["title"], u=i["url"], k=i["kind"], s=i["site"], tp=i["topic"])
    if i["is_new"]:
        o.update(n=1, f=i["first_seen_pt"])
    if i["new_today"]:
        o["nt"] = 1
    if i["discussion_url"] and i["discussion_url"] != i["url"]:
        o["d"] = i["discussion_url"]
    if i.get("citation_count") is not None:
        o["ci"] = i["citation_count"]
    if i.get("comment_count") is not None:
        o["cm"] = i["comment_count"]
    return o

def render_canvas(d):
    tpl = open(os.path.join(os.path.dirname(__file__), "canvas_template.tsx")).read()
    slim = dict(generated_at_pt=d["generated_at_pt"], counts=d["counts"], badge_days=d["badge_days"],
                topics=[{k: t[k] for k in ("id", "label", "count", "videos", "new", "new_today")} for t in d["topics"]],
                last_run=None if not d["last_run"] else dict(run_id=d["last_run"]["run_id"], started_at_pt=d["last_run"]["started_at_pt"],
                    sources=[dict(name=n, status=s["status"].split(" (")[0], fetched=s["fetched"], new=s["new"]) for n, s in d["last_run"]["stats"].items()]),
                items=[_slim(i) for i in d["items"]])
    return tpl.replace("__DATA__", json.dumps(slim, ensure_ascii=False, separators=(",", ":")))

def build():
    d = collect()
    os.makedirs(config.SITE_DIR, exist_ok=True)
    files = {"index.html": render_html(d), "rss.xml": render_rss(d), "data.json": json.dumps(d, indent=1, ensure_ascii=False),
             "canvas.tsx": render_canvas(d)}
    for name, body in files.items():
        with open(os.path.join(config.SITE_DIR, name), "w") as f:
            f.write(body)
    c = d["counts"]
    return (f"site rebuilt in {config.SITE_DIR}: {c['total']} items ({c['videos']} videos, {c['seed']} seed), "
            f"new today {c['new_today']}, new in {d['badge_days']}d {c['new_7d']}")

if __name__ == "__main__":   # python3 -m wtube.build  -> rebuild site/ from the DB without fetching
    print(build())
