"""Per-source fetchers. Each returns a list of raw dicts:
   source, source_id, title, url, kind, site, summary, published_at (ISO or None), points, discussion_url"""
import calendar, html, json, re, time, random, urllib.request, urllib.error, urllib.parse
import xml.etree.ElementTree as ET
from . import config

class FetchError(Exception):
    pass

def http_get(url, timeout=30, tries=3, accept=None):
    """GET with retry: 3 attempts, backoff 2s/8s/30s (+jitter), honors Retry-After, no retry on plain 4xx."""
    delays = [2, 8, 30]
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT, **({"Accept": accept} if accept else {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} for {url}"
            if e.code != 429 and e.code < 500:
                raise FetchError(last)
            ra = e.headers.get("Retry-After")
            wait = int(ra) if ra and ra.isdigit() else delays[min(attempt, 2)]
        except Exception as e:  # timeouts, DNS, resets
            last = f"{type(e).__name__}: {e} for {url}"
            wait = delays[min(attempt, 2)]
        if attempt < tries - 1:
            time.sleep(wait + random.uniform(0, 1))
    raise FetchError(last)

def strip_tags(s):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or ""))).strip()

# ---------------- Hacker News (Algolia search_by_date) ----------------
def fetch_hn(since_ts):
    out, seen = [], set()
    for term in config.HN_TERMS:
        q = urllib.parse.urlencode({"query": term, "tags": "story", "hitsPerPage": 100,
                                    "numericFilters": f"created_at_i>{int(since_ts)}"})
        data = json.loads(http_get("https://hn.algolia.com/api/v1/search_by_date?" + q))
        for h in data.get("hits", []):
            oid = h["objectID"]
            if oid in seen:
                continue
            seen.add(oid)
            if (h.get("points") or 0) < config.HN_MIN_POINTS:
                continue
            disc = f"https://news.ycombinator.com/item?id={oid}"
            url = h.get("url") or disc
            host = urllib.parse.urlsplit(url).hostname or ""
            kind = "video" if ("youtube.com" in host or "youtu.be" in host) else "paper" if "arxiv.org" in host else "page"
            out.append(dict(source="hn", source_id=oid, title=h.get("title") or "", url=url, kind=kind,
                            site=f"HN · {h.get('points', 0)} pts · {host}", summary=f"{host} {strip_tags(h.get('story_text'))[:400]}",
                            published_at=h.get("created_at"), points=h.get("points"), comments=h.get("num_comments"),
                            discussion_url=disc))
        time.sleep(0.3)
    return out

# ---------------- arXiv API ----------------
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
def fetch_arxiv(since_ts):
    cats = " OR ".join(f"cat:{c}" for c in config.ARXIV_CATS)
    terms = " OR ".join(f"abs:{t}" for t in config.ARXIV_TERMS)
    out, start, page = [], 0, 50
    while start < config.ARXIV_MAX_PER_RUN:
        q = urllib.parse.urlencode({"search_query": f"({cats}) AND ({terms})", "sortBy": "submittedDate",
                                    "sortOrder": "descending", "start": start, "max_results": page})
        root = ET.fromstring(http_get("https://export.arxiv.org/api/query?" + q, timeout=60))
        entries = root.findall("a:entry", ATOM)
        if not entries:
            break
        older = False
        for e in entries:
            pub = e.findtext("a:published", "", ATOM)
            ts = calendar.timegm(time.strptime(pub[:19], "%Y-%m-%dT%H:%M:%S"))
            if ts < since_ts:
                older = True
                continue
            aid = re.sub(r"v\d+$", "", e.findtext("a:id", "", ATOM).rsplit("/abs/", 1)[-1])
            prim = e.find("arxiv:primary_category", ATOM)
            out.append(dict(source="arxiv", source_id=aid, title=strip_tags(e.findtext("a:title", "", ATOM)),
                            url=f"https://arxiv.org/abs/{aid}", kind="paper",
                            site=f"arXiv {prim.get('term') if prim is not None else ''}".strip(),
                            summary=strip_tags(e.findtext("a:summary", "", ATOM)), published_at=pub, points=None))
        if older:
            break
        start += page
        time.sleep(3.1)   # arXiv API etiquette: 1 request / 3 s
    return out

# ---------------- YouTube channel RSS ----------------
YTNS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/"}
def fetch_youtube_rss(since_ts, errors):
    out = []
    for name, cid, group in config.YT_CHANNELS:
        try:
            root = ET.fromstring(http_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"))
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        for e in root.findall("a:entry", YTNS):
            vid = e.findtext("yt:videoId", "", YTNS)
            pub = e.findtext("a:published", "", YTNS)
            ts = calendar.timegm(time.strptime(pub[:19], "%Y-%m-%dT%H:%M:%S"))
            if ts < since_ts:
                continue
            desc = e.findtext("media:group/media:description", "", YTNS)
            out.append(dict(source=group, source_id=vid, title=e.findtext("a:title", "", YTNS),
                            url=f"https://www.youtube.com/watch?v={vid}", kind="video", site=f"YouTube · {name}",
                            summary=desc[:800], published_at=pub, points=None))
        time.sleep(0.5)
    return out

# ---------------- YouTube Data API (optional) ----------------
def fetch_youtube_api(since_iso):
    if not config.YT_API_KEY:
        return None   # skipped gracefully
    out = []
    for q in config.YT_API_QUERIES:
        params = urllib.parse.urlencode({"part": "snippet", "q": q, "type": "video", "order": "date", "maxResults": 25,
                                         "publishedAfter": since_iso, "relevanceLanguage": "en", "key": config.YT_API_KEY})
        data = json.loads(http_get("https://www.googleapis.com/youtube/v3/search?" + params))
        for it in data.get("items", []):
            vid = it["id"].get("videoId")
            sn = it["snippet"]
            if not vid:
                continue
            out.append(dict(source="youtube_api", source_id=vid, title=html.unescape(sn["title"]),
                            url=f"https://www.youtube.com/watch?v={vid}", kind="video",
                            site=f"YouTube · {sn.get('channelTitle', '')}", summary=sn.get("description", ""),
                            published_at=sn.get("publishedAt"), points=None))
    return out

# ---------------- Reddit (disabled stub) ----------------
def fetch_reddit(since_ts):
    """DISABLED in v1. Unauthenticated reddit.com JSON returns 403 (old.reddit 302, RSS 404/429) from this box.
    To enable: register a Reddit 'script' app, export REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET/REDDIT_USER_AGENT, then
    call https://oauth.reddit.com/r/5g+wifi+DSP+NextGComm+rfelectronics/new?limit=100 (100 QPM per client)."""
    return None

# ---------------- HTML scrapers ----------------
def _anchors(page, pattern):
    """Yield (href, inner_html) for <a> tags whose href contains pattern."""
    for m in re.finditer(r'<a\b[^>]*href="([^"]*' + re.escape(pattern) + r'[^"]*)"[^>]*>(.*?)</a>', page, re.S | re.I):
        yield m.group(1), m.group(2)

def _slug_title(href):
    slug = href.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"[-_]+", " ", slug).strip().capitalize()

def fetch_nga(_since_ts):
    page = http_get(config.NGA_URL)
    best = {}
    for pat in config.NGA_PATTERNS:
        for href, inner in _anchors(page, pat):
            txt = strip_tags(inner)
            if len(txt) > len(best.get(href, "")):
                best[href] = txt
    out = []
    for href, txt in best.items():
        if href.rstrip("/").endswith(tuple(p.strip("/") for p in config.NGA_PATTERNS)):
            continue  # index pages
        kind = "report" if "/white_papers/" in href else "page"
        out.append(dict(source="nga", source_id=href, title=txt if len(txt) > 8 else _slug_title(href), url=href,
                        kind=kind, site="Next G Alliance", summary="", published_at=None, points=None))
    return out

def fetch_3gpp(_since_ts):
    page = http_get(config.GPP_NEWS_URL)
    out, seen = [], set()
    for m in re.finditer(r'<a href="(/news-events/3gpp-news/[^"#?]+)"[^>]*>(.*?)</a>(.{0,1500})', page, re.S):
        href, inner, tail = m.groups()
        if href in seen:
            continue
        h3 = re.search(r"<h3[^>]*>(.*?)</h3>", inner + tail, re.S)
        if not h3:
            continue
        seen.add(href)
        dt = re.search(r'<time datetime="([^"]+)"', tail)
        url = "https://www.3gpp.org" + href
        out.append(dict(source="3gpp", source_id=href, title=strip_tags(h3.group(1)), url=url, kind="page",
                        site="3GPP news", summary="3GPP", published_at=dt.group(1) if dt else None, points=None))
    return out

def fetch_ieeetv(_since_ts):
    out, seen = [], set()
    for base in config.IEEETV_URLS:
        page = http_get(base)
        for href, inner in _anchors(page, "/video/"):
            full = urllib.parse.urljoin(base, href)
            if full in seen:
                continue
            seen.add(full)
            txt = strip_tags(inner)
            title = txt if len(txt) > 8 else _slug_title(href)
            # slug titles are lower-case; restore the acronyms the case-sensitive include filter relies on
            title = re.sub(r"\b(5g|6g|isac|mimo|ris|ofdm|otfs|nr|ntn|thz|mmwave|ran|o-ran|csi|phy|mac|3gpp|imt-2030)\b",
                           lambda m: {"mmwave": "mmWave", "thz": "THz"}.get(m.group(1), m.group(1).upper()), title)
            out.append(dict(source="ieeetv", source_id=full, title=title, url=full,
                            kind="video", site="IEEE.tv", summary="IEEE communications channel", published_at=None, points=None))
    return out
