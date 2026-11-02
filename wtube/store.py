"""SQLite store, URL canonicalization, stable keys and dedupe."""
import hashlib, json, re, sqlite3, difflib
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  item_key      TEXT PRIMARY KEY,   -- yt:<id> | arxiv:<id no version> | doi:<doi> | hn:<id> | url:<sha1(canonical)>
  canonical_url TEXT NOT NULL,
  url           TEXT NOT NULL,      -- link shown to users
  title         TEXT NOT NULL,
  title_norm    TEXT NOT NULL,
  kind          TEXT NOT NULL,      -- video | paper | page | thread | report
  topic         TEXT NOT NULL,
  site          TEXT,               -- channel / venue / outlet label
  summary       TEXT,
  score         INTEGER,
  classifier    TEXT,               -- seed | regex | llm:keep
  origin        TEXT NOT NULL,      -- seed | baseline | check   (only 'check' earns a New badge)
  published_at  TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  discussion_url TEXT,
  status        TEXT NOT NULL DEFAULT 'live'
);
CREATE TABLE IF NOT EXISTS aliases (alias TEXT PRIMARY KEY, item_key TEXT NOT NULL REFERENCES items(item_key));
CREATE TABLE IF NOT EXISTS mentions (
  source TEXT NOT NULL, source_id TEXT NOT NULL, item_key TEXT NOT NULL REFERENCES items(item_key),
  source_url TEXT, points INTEGER, comments INTEGER, seen_at TEXT NOT NULL,
  PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS source_state (
  source TEXT PRIMARY KEY, last_success_at TEXT, last_error TEXT, consecutive_failures INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, stats TEXT
);
CREATE TABLE IF NOT EXISTS borderline (   -- scored 3..5; kept for review / optional LLM step
  source TEXT, source_id TEXT, title TEXT, url TEXT, score INTEGER, seen_at TEXT, PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_items_topic ON items(topic);
"""

def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)

def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def parse_iso(s):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def connect(path=None):
    db = sqlite3.connect(path or config.DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    migrate(db)
    return db

def migrate(db):
    """Additive migration for an existing wireless_tube.db. Safe to run every connect."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS citations (
      item_key        TEXT PRIMARY KEY,
      citation_count  INTEGER,             -- NULL when unknown; 0 is a real zero
      status          TEXT NOT NULL,       -- ok | not_found | error
      query           TEXT,
      source          TEXT,                -- openalex
      checked_at      TEXT,
      error           TEXT
    );
    CREATE TABLE IF NOT EXISTS comment_cache (
      item_key       TEXT PRIMARY KEY,
      comment_count  INTEGER,
      checked_at     TEXT,
      error          TEXT
    );
    """)
    db.commit()

# ---------- canonical URL + keys ----------
DROP_PARAMS = re.compile(r"^(utm_.*|si|feature|t|fbclid|gclid|ref|ref_src|source|pp|ab_channel|list|index)$", re.I)
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)

def canonical_url(url):
    url = url.strip()
    p = urlsplit(url if "://" in url else "https://" + url)
    host = (p.hostname or "").lower()
    for pre in ("www.", "m.", "mobile."):
        if host.startswith(pre):
            host = host[len(pre):]
    path = p.path or "/"
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False) if not DROP_PARAMS.match(k)]
    # YouTube forms -> youtube.com/watch?v=ID
    vid = None
    if host == "youtu.be":
        vid = path.strip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        m = re.match(r"^/(shorts|embed|live|v)/([^/?#]+)", path)
        if m:
            vid = m.group(2)
        elif path == "/watch":
            vid = dict(parse_qsl(p.query)).get("v")
    if vid and YT_ID.match(vid):
        return f"https://youtube.com/watch?v={vid}"
    # arXiv abs/pdf/versioned -> arxiv.org/abs/ID
    if host.endswith("arxiv.org"):
        m = re.match(r"^/(abs|pdf|html)/(.+?)(\.pdf)?/?$", path)
        if m:
            am = ARXIV_ID.match(m.group(2))
            if am:
                return f"https://arxiv.org/abs/{am.group(1)}"
    if host == "news.ycombinator.com" and path == "/item":
        hid = dict(parse_qsl(p.query)).get("id")
        return f"https://news.ycombinator.com/item?id={hid}"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, urlencode(sorted(q)), ""))

def stable_key(url):
    c = canonical_url(url)
    m = re.match(r"https://youtube\.com/watch\?v=(.+)$", c)
    if m:
        return "yt:" + m.group(1)
    m = re.match(r"https://arxiv\.org/abs/(.+)$", c)
    if m:
        return "arxiv:" + m.group(1)
    m = re.match(r"https://(dx\.)?doi\.org/(10\..+)$", c)
    if m:
        return "doi:" + m.group(2).lower()
    m = re.match(r"https://news\.ycombinator\.com/item\?id=(\d+)$", c)
    if m:
        return "hn:" + m.group(1)
    return "url:" + hashlib.sha1(c.encode()).hexdigest()[:16]

STOP = set("a an the of for and or in on to with by from via is are at as its it this that how what why we".split())
def norm_title(t):
    t = re.sub(r"\[(video|pdf)\]|\(\d{4}\)", " ", t.lower())
    toks = [w for w in re.findall(r"[a-z0-9]+", t) if w not in STOP]
    return " ".join(toks)

def title_similar(a, b):
    if not a or not b:
        return False
    ta, tb = set(a.split()), set(b.split())
    if min(len(ta), len(tb)) < 4:      # too short to fuzzy-match safely
        return a == b
    jac = len(ta & tb) / len(ta | tb)
    return jac >= 0.9 or difflib.SequenceMatcher(None, a, b).ratio() >= 0.93

# ---------- dedupe ----------
HARD_NS = {"yt", "arxiv", "doi"}

def resolve(db, candidate_keys, canon_urls, tnorm):
    """Return existing item_key if any id / canonical URL / near-identical title matches."""
    for k in candidate_keys:
        r = db.execute("SELECT item_key FROM items WHERE item_key=?", (k,)).fetchone() or \
            db.execute("SELECT item_key FROM aliases WHERE alias=?", (k,)).fetchone()
        if r:
            return r[0], "id"
    for u in canon_urls:
        r = db.execute("SELECT item_key FROM items WHERE canonical_url=?", (u,)).fetchone()
        if r:
            return r[0], "url"
    hard = {k.split(":", 1)[0] for k in candidate_keys if k.split(":", 1)[0] in HARD_NS}
    for r in db.execute("SELECT item_key, title_norm FROM items"):
        # two different YouTube videos / arXiv papers with near-identical titles are NOT duplicates
        if r["item_key"].split(":", 1)[0] in hard:
            continue
        if title_similar(tnorm, r["title_norm"]):
            return r["item_key"], "title"
    return None, None

def add_alias(db, alias, key):
    db.execute("INSERT OR IGNORE INTO aliases(alias, item_key) VALUES (?,?)", (alias, key))

def insert_item(db, it, origin, ts):
    db.execute("""INSERT INTO items(item_key, canonical_url, url, title, title_norm, kind, topic, site, summary, score,
                  classifier, origin, published_at, first_seen_at, last_seen_at, discussion_url)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (it["key"], it["canonical_url"], it["url"], it["title"], norm_title(it["title"]), it["kind"], it["topic"],
                it.get("site"), (it.get("summary") or "")[:600], it.get("score"), it.get("classifier"), origin,
                it.get("published_at"), ts, ts, it.get("discussion_url")))

def add_mention(db, source, source_id, key, source_url, ts, points=None, comments=None):
    db.execute("""INSERT INTO mentions(source, source_id, item_key, source_url, points, comments, seen_at)
                  VALUES (?,?,?,?,?,?,?)
                  ON CONFLICT(source, source_id) DO UPDATE SET points=excluded.points, comments=excluded.comments""",
               (source, source_id, key, source_url, points, comments, ts))
