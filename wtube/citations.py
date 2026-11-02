"""Citation counts from OpenAlex and HN comment counts, plus the display rank.

Missing counts are neutral (treated as 0, never a penalty) and any API failure is recorded
and skipped. The daily run keeps going.

Citations and comments are weighted equally: the same log normalization, and equal coefficients
(config.RANK_CITE_COEF == config.RANK_COMMENT_COEF).
"""
import difflib, json, math, re, time, urllib.parse
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from . import config, fetchers, store

IEEE_ACM_HOSTS = {"ieeexplore.ieee.org", "dl.acm.org", "dlnext.acm.org"}
DOI_IN_URL = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
HN_URL = re.compile(r"news\.ycombinator\.com/item\?id=(\d+)")


def norm_count(n):
    """Same map for citations and for comments. 0 and missing -> 0; saturates at RANK_CAP."""
    if not n or n < 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(config.RANK_CAP))


def when_of(published_at, first_seen_at):
    d = store.parse_iso(published_at)
    if d:
        return d
    if published_at and re.fullmatch(r"\d{4}-\d{2}", published_at.strip()):
        return datetime.fromisoformat(published_at.strip() + "-01").replace(tzinfo=timezone.utc)
    if published_at and re.fullmatch(r"\d{4}", published_at.strip()):
        return datetime(int(published_at.strip()), 1, 1, tzinfo=timezone.utc)
    return store.parse_iso(first_seen_at)


def rank_score(citations, comments, published_at, first_seen_at, now):
    """Return the pieces of the display rank. Coefficients on the two signals are equal."""
    if config.RANK_CITE_COEF != config.RANK_COMMENT_COEF:
        raise RuntimeError("citation and comment coefficients must stay equal")
    cite_n = norm_count(citations)
    comment_n = norm_count(comments)
    engagement = config.RANK_CITE_COEF * cite_n + config.RANK_COMMENT_COEF * comment_n
    when = when_of(published_at, first_seen_at)
    if when is None:
        recency = 0.0
        age_days = None
    else:
        age_days = max(0.0, (now - when).total_seconds() / 86400.0)
        recency = 0.5 ** (age_days / config.RANK_HALF_LIFE_DAYS)
    return dict(cite_n=cite_n, comment_n=comment_n, engagement=engagement, recency=recency,
                age_days=age_days, rank=recency + engagement)


def _due(checked_at, published_at, first_seen_at, now, force=False):
    if force or not checked_at:
        return True
    checked = store.parse_iso(checked_at)
    if not checked:
        return True
    when = when_of(published_at, first_seen_at)
    age_days = (now - when).total_seconds() / 86400.0 if when else 10**9
    interval = config.CITATION_REFRESH_YOUNG_DAYS if age_days < config.CITATION_YOUNG_DAYS else config.CITATION_REFRESH_OLD_DAYS
    return now - checked >= timedelta(days=interval)


def _host(url):
    return (urlsplit(url or "").hostname or "").lower().removeprefix("www.")


def _doi_from_url(url):
    if not url:
        return None
    low = url.lower()
    if "doi.org/" in low:
        tail = url.split("doi.org/", 1)[1].split("?", 1)[0].split("#", 1)[0]
        return tail.rstrip("/").lower()
    m = DOI_IN_URL.search(url)
    return m.group(1).rstrip(").,").lower() if m else None


def _norm_doi(value):
    if not value:
        return ""
    s = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s.rstrip("/")


def lookup_query(item):
    """How to ask OpenAlex. None if this item is not a paper we can look up."""
    key = item["item_key"]
    url = item["url"] or ""
    if key.startswith("arxiv:"):
        return ("doi", "10.48550/arxiv." + key.split(":", 1)[1].lower())
    if key.startswith("doi:"):
        return ("doi", key.split(":", 1)[1].lower())
    doi = _doi_from_url(url)
    if doi:
        return ("doi", doi)
    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^?#]+)", url, re.I)
    if m:
        aid = re.sub(r"v\d+$", "", m.group(1)).rstrip("/").removesuffix(".pdf")
        return ("doi", "10.48550/arxiv." + aid.lower())
    host = _host(url)
    if host in IEEE_ACM_HOSTS or item["kind"] == "paper":
        title = (item["title"] or "").strip()
        if title:
            return ("title", title)
    return None


def _candidate(item):
    key = item["item_key"]
    if key.startswith(("arxiv:", "doi:")) or item["kind"] == "paper":
        return True
    return _host(item["url"]) in IEEE_ACM_HOSTS and item["kind"] != "video"


def _openalex_get(url):
    sep = "&" if "?" in url else "?"
    if "mailto=" not in url:
        url += sep + urllib.parse.urlencode({"mailto": config.OPENALEX_MAILTO})
    return json.loads(fetchers.http_get(url, timeout=60, accept="application/json"))


def _batch_dois(dois):
    """Return {doi: cited_by_count} for works OpenAlex knows. Raises on transport failure."""
    found = {}
    for i in range(0, len(dois), config.OPENALEX_BATCH):
        chunk = dois[i:i + config.OPENALEX_BATCH]
        q = urllib.parse.urlencode({
            "filter": "doi:" + "|".join(chunk),
            "select": "doi,cited_by_count",
            "per-page": str(len(chunk)),
            "mailto": config.OPENALEX_MAILTO,
        })
        data = _openalex_get("https://api.openalex.org/works?" + q)
        for w in data.get("results") or []:
            doi = _norm_doi(w.get("doi"))
            if doi:
                found[doi] = int(w.get("cited_by_count") or 0)
        time.sleep(0.25)
    return found


def _same_title(a, b):
    """Strict match so a title fallback cannot inherit a different paper's citations."""
    na, nb = store.norm_title(a), store.norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.97

def _title_count(title):
    """Top OpenAlex hit only if the title matches. (count, doi) or None."""
    q = urllib.parse.urlencode({
        "search": title[:240],
        "select": "doi,display_name,cited_by_count",
        "per-page": "1",
        "mailto": config.OPENALEX_MAILTO,
    })
    data = _openalex_get("https://api.openalex.org/works?" + q)
    results = data.get("results") or []
    time.sleep(0.25)
    if not results:
        return None
    hit = results[0]
    if not _same_title(title, hit.get("display_name") or ""):
        return None
    return int(hit.get("cited_by_count") or 0), _norm_doi(hit.get("doi")) or None


def _save_citation(db, key, count, status, query, now, error=None):
    db.execute("""INSERT INTO citations(item_key, citation_count, status, query, source, checked_at, error)
                  VALUES (?,?,?,?,?,?,?)
                  ON CONFLICT(item_key) DO UPDATE SET
                    citation_count=excluded.citation_count, status=excluded.status, query=excluded.query,
                    source=excluded.source, checked_at=excluded.checked_at, error=excluded.error""",
               (key, count, status, query, "openalex", store.iso(now), (error or "")[:300] or None))


def _refresh_citations(db, rows, cached, now, stats):
    due = []
    for item in rows:
        if not _candidate(item):
            continue
        spec = lookup_query(item)
        if spec is None:
            continue
        stats["candidates"] += 1
        prev = cached.get(item["item_key"])
        if prev and prev["status"] != "error" and not _due(prev["checked_at"], item["published_at"], item["first_seen_at"], now):
            stats["fresh"] += 1
            continue
        due.append((item, spec))
    doi_items = [(item, doi) for item, (kind, doi) in due if kind == "doi"]
    title_items = [(item, title, kind + ":" + title[:80]) for item, (kind, title) in due if kind == "title"]
    doi_list = list(dict.fromkeys(doi for _, doi in doi_items))
    found = {}
    batch_error = None
    if doi_list:
        try:
            found = _batch_dois(doi_list)
        except Exception as e:
            batch_error = f"{type(e).__name__}: {e}"
            stats["errors"] += 1
    if batch_error:
        # Do not stamp checked_at, so the next run retries. Counts stay whatever they were.
        stats["error_messages"].append(batch_error)
    else:
        for item, doi in doi_items:
            if doi in found:
                _save_citation(db, item["item_key"], found[doi], "ok", "doi:" + doi, now)
                stats["ok"] += 1
                stats["nonzero"] += int(found[doi] > 0)
            elif (item.get("title") or "").strip():
                # arXiv DOI not indexed yet; a published IEEE/ACM record sometimes has the same title.
                title_items.append((item, item["title"].strip(), "doi:" + doi))
            else:
                _save_citation(db, item["item_key"], None, "not_found", "doi:" + doi, now)
                stats["not_found"] += 1
    for item, title, query in title_items:
        try:
            hit = _title_count(title)
        except Exception as e:
            stats["errors"] += 1
            stats["error_messages"].append(f"{item['item_key']}: {type(e).__name__}: {e}")
            continue  # leave the previous cache; retry next run
        if hit is None:
            _save_citation(db, item["item_key"], None, "not_found", query[:240], now)
            stats["not_found"] += 1
        else:
            count, doi = hit
            label = ("title:" + doi) if doi else query
            _save_citation(db, item["item_key"], count, "ok", label[:240], now)
            stats["ok"] += 1
            stats["nonzero"] += int(count > 0)


def _hn_targets(db):
    """item_key -> hn numeric id, for stories we can ask HN about."""
    out = {}
    for r in db.execute("SELECT item_key, url, discussion_url FROM items WHERE status='live'"):
        hid = None
        if r["item_key"].startswith("hn:"):
            hid = r["item_key"].split(":", 1)[1]
        else:
            for text in (r["discussion_url"] or "", r["url"] or ""):
                m = HN_URL.search(text)
                if m:
                    hid = m.group(1)
                    break
        if hid and hid.isdigit():
            out[r["item_key"]] = hid
    return out


def _refresh_comments(db, now, stats):
    targets = _hn_targets(db)
    cached = {r["item_key"]: r for r in db.execute("SELECT item_key, checked_at FROM comment_cache")}
    items = {r["item_key"]: r for r in db.execute("SELECT item_key, published_at, first_seen_at FROM items")}
    for key, hid in targets.items():
        item = items.get(key)
        if not item:
            continue
        prev = cached.get(key)
        if prev and not _due(prev["checked_at"], item["published_at"], item["first_seen_at"], now):
            continue
        try:
            data = json.loads(fetchers.http_get(f"https://hacker-news.firebaseio.com/v0/item/{hid}.json", timeout=20))
            time.sleep(0.15)
        except Exception as e:
            stats["comment_errors"] += 1
            stats["error_messages"].append(f"hn:{hid}: {type(e).__name__}: {e}")
            continue
        if not data or not isinstance(data, dict):
            db.execute("""INSERT INTO comment_cache(item_key, comment_count, checked_at, error) VALUES (?,?,?,?)
                          ON CONFLICT(item_key) DO UPDATE SET checked_at=excluded.checked_at, error=excluded.error""",
                       (key, None, store.iso(now), "missing"))
            continue
        comments = data.get("descendants")
        if comments is None:
            comments = 0 if data.get("type") == "story" else None
        if comments is None:
            continue
        comments = int(comments)
        points = data.get("score")
        db.execute("UPDATE mentions SET comments=? WHERE item_key=?", (comments, key))
        if points is not None:
            db.execute("UPDATE mentions SET points=COALESCE(points, ?) WHERE item_key=?", (int(points), key))
        db.execute("""INSERT INTO comment_cache(item_key, comment_count, checked_at, error) VALUES (?,?,?,NULL)
                      ON CONFLICT(item_key) DO UPDATE SET comment_count=excluded.comment_count,
                        checked_at=excluded.checked_at, error=NULL""",
                   (key, comments, store.iso(now)))
        stats["comments_updated"] += 1


def refresh(db=None, now=None):
    """Update citation and comment caches. Never raises for a single-source failure.

    A programming error can still propagate; check.py also catches that and continues.
    """
    own = db is None
    if own:
        db = store.connect()
    now = now or store.now_utc()
    stats = dict(candidates=0, fresh=0, ok=0, nonzero=0, not_found=0, errors=0,
                 comments_updated=0, comment_errors=0, error_messages=[])
    rows = [dict(r) for r in db.execute("SELECT * FROM items WHERE status='live'")]
    cached = {r["item_key"]: r for r in db.execute("SELECT * FROM citations")}
    try:
        _refresh_citations(db, rows, cached, now, stats)
    except Exception as e:
        stats["errors"] += 1
        stats["error_messages"].append(f"{type(e).__name__}: {e}")
    try:
        _refresh_comments(db, now, stats)
    except Exception as e:
        stats["comment_errors"] += 1
        stats["error_messages"].append(f"comments: {type(e).__name__}: {e}")
    db.commit()
    stats["with_count"] = db.execute("SELECT COUNT(*) FROM citations WHERE status='ok'").fetchone()[0]
    stats["nonzero_total"] = db.execute("SELECT COUNT(*) FROM citations WHERE status='ok' AND citation_count>0").fetchone()[0]
    stats["comments_known"] = db.execute("SELECT COUNT(DISTINCT item_key) FROM mentions WHERE comments IS NOT NULL").fetchone()[0]
    if own:
        db.close()
    stats["error_messages"] = stats["error_messages"][:8]
    return stats


def load_signals(db):
    """item_key -> (citation_count or None, comment_count or None). Unknown stays None."""
    cites = {}
    for r in db.execute("SELECT item_key, citation_count, status FROM citations"):
        if r["status"] == "ok":
            cites[r["item_key"]] = r["citation_count"]
    comments = {}
    for r in db.execute("SELECT item_key, comments FROM mentions WHERE comments IS NOT NULL"):
        prev = comments.get(r["item_key"])
        comments[r["item_key"]] = r["comments"] if prev is None else max(prev, r["comments"])
    return cites, comments


def _self_check():
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    when = store.iso(now)
    base = rank_score(0, 0, when, when, now)
    cites = rank_score(37, 0, when, when, now)
    comments = rank_score(0, 37, when, when, now)
    # Same input on either signal moves the rank by the same amount.
    assert abs((cites["rank"] - base["rank"]) - (comments["rank"] - base["rank"])) < 1e-12, (cites, comments)
    assert abs(cites["cite_n"] - comments["comment_n"]) < 1e-12
    assert config.RANK_CITE_COEF == config.RANK_COMMENT_COEF
    # Missing is the same as a known zero.
    assert rank_score(None, None, when, when, now)["rank"] == base["rank"]
    # The cap stops a viral thread from outgrowing a paper that already reached it.
    assert norm_count(100) == norm_count(10000) == 1.0
    old = rank_score(0, 10000, store.iso(now - timedelta(days=120)), when, now)
    fresh = rank_score(0, 0, when, when, now)
    assert fresh["rank"] > old["rank"]
    print("rank self-check ok",
          f"37 cites delta={cites['rank'] - base['rank']:.6f}",
          f"37 comments delta={comments['rank'] - base['rank']:.6f}")


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        _self_check()
    else:
        print(json.dumps(refresh(), indent=2))
