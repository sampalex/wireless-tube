"""Include/exclude dual filter, scoring and topic assignment."""
from . import config

def score(title, summary, source, kind, points=None):
    """Return (score, hits). 0 means vetoed or no wireless term."""
    text = f"{title} {summary or ''}"
    if config.EXCLUDE.search(text):
        return 0, []
    hits = {m.group(0) for m in config.INCLUDE_CS.finditer(text)} | \
           {m.group(0).lower() for m in config.INCLUDE_CI.finditer(text)}
    if not hits:
        return 0, []
    s = 2 * min(len(hits), 5)                     # cap so long abstracts do not dominate
    if source in config.SOURCE_IMPLIES_CTX or config.WIRELESS_CTX.search(text):
        s += 2
    s += config.SOURCE_PRIOR.get(source, 0)
    if kind == "video":
        s += 2
    if points is not None:
        s += 2 if points >= 100 else 1 if points >= 20 else 0
    return s, sorted(hits)

def decide(s):
    if s >= config.KEEP_SCORE:
        return "keep"
    if s <= config.DROP_SCORE:
        return "drop"
    return "borderline"

def llm_classify(item):
    """Optional borderline step (off in v1). Plug a classifier in here and set WT_LLM_BORDERLINE=1.
    Prompt sketch: 'Is this primarily about wireless PHY/MAC, 5G/6G, Wi-Fi, beamforming, massive MIMO or comms
    signal processing (not generic AI, crypto, non-wireless)? Answer keep/drop + topic.'"""
    return "drop"

def topic_for(title, summary=""):
    best, best_score = "other", 0
    for tid, rx in config.TOPIC_RULES:
        sc = 3 * len(rx.findall(title)) + len(rx.findall(summary or ""))
        if sc > best_score:
            best, best_score = tid, sc
    return best
