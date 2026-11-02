"""All tunables in one place: topics, filters, sources."""
import os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("WT_DB", os.path.join(ROOT, "wireless_tube.db"))
SITE_DIR = os.environ.get("WT_SITE", os.path.join(ROOT, "site"))
USER_AGENT = "WirelessTube/0.1 (+personal research aggregator; contact via repo owner)"
TZ_DISPLAY = "America/Los_Angeles"

NEW_BADGE_DAYS = 7          # "New" badge lifetime
NEW_TODAY_HOURS = 24        # "New today" window
OVERLAP_HOURS = 36          # re-scan overlap after the last successful run
# lookback used the very first time a source runs (no watermark yet)
FIRST_LOOKBACK_DAYS = {"hn": 7, "arxiv": 7, "youtube": 14, "youtube_api": 7, "3gpp": 30, "nga": 30, "ieeetv": 30}

# Sources whose "published" date can lag behind when the item becomes visible (arXiv: submission date vs. announcement,
# weekends) always look back this far, regardless of the watermark. Dedupe makes the re-scan harmless.
ROLLING_LOOKBACK_DAYS = {"arxiv": 7}

# ---- scoring (design: keep >= 6, drop <= 2, 3..5 borderline -> optional LLM, off in v1) ----
KEEP_SCORE = 6
DROP_SCORE = 2
LLM_BORDERLINE = os.environ.get("WT_LLM_BORDERLINE", "0") == "1"   # hook only; v1 keeps it off
HN_MIN_POINTS = int(os.environ.get("WT_HN_MIN_POINTS", "3"))

# Case-sensitive acronyms (so "MiMo" / "6GB" / "nr" do not match)
INCLUDE_CS = re.compile(r"\b(5G|5G-Advanced|6G|6GR|IMT-2030|3GPP|NR|PHY|MAC|HARQ|MLO|EHT|UHR|OFDMA?|OTFS|AFDM|MIMO|MU-MIMO|ISAC|JCAS|RIS|NTN|CSI|FR3|THz|mmWave|O-RAN|RAN|SDR|WLAN|BSS)\b")
# Case-insensitive phrases
INCLUDE_CI = re.compile(r"\b(wi-?fi ?(6e?|7|8)?|802\.11(ax|be|bn|ad|ay)?|beamform\w*|beam ?steer\w*|beamfocus\w*|precod\w*|phased[- ]array|massive mimo|cell-free|multi-ap|physical layer|mac layer|link adaptation|air interface|channel estimation|equali[sz]\w*|reconfigurable intelligent surface|release ?(1[5-9]|2[0-2])|rel-(1[5-9]|2[0-2])|5g nr|new radio|millimeter[- ]wave|terahertz|spectrum sharing|waveform|antenna array|software[- ]defined radio)\b", re.I)
EXCLUDE = re.compile(r"\b(blockchain|crypto\w*|bitcoin|ethereum|nft|web3|token sale|llm agents?|agentic|chatbot|gpt-?\d*|6 ?gb|ghz cpu|xiaomi|mimo-?v\d|5g (health|conspiracy|cancer|covid)|stocks?|earnings|investor|ultrasound|ultrasonic|medical imaging|eeg|ecg|seismic|hearing aids?|speech enhancement|microphone array)\b", re.I)
WIRELESS_CTX = re.compile(r"\b(wireless|radio|rf|antennas?|spectrum|cellular|wlan|base stations?|channels?|transceivers?|uplink|downlink|mobile networks?|telecom\w*)\b", re.I)

SOURCE_PRIOR = {"hn": 0, "reddit": 0, "arxiv": 3, "youtube": 3, "youtube_api": 1, "ieee": 3, "acm": 1, "ieeetv": 3, "nga": 3, "3gpp": 3}
SOURCE_IMPLIES_CTX = {"arxiv", "ieeetv", "nga", "3gpp"}   # these sources are wireless by construction

TOPICS = [  # display order
    ("5g", "5G"), ("6g", "6G"), ("wifi", "Wi-Fi 6/6E/7/8"), ("macphy", "MAC/PHY protocols"),
    ("sp", "Signal processing"), ("beamforming", "Beamforming"), ("massive-mimo", "Massive MIMO"), ("other", "Other wireless"),
]
TOPIC_LABEL = dict(TOPICS)
TOPIC_RULES = [  # tie-break order = list order
    ("6g", re.compile(r"\b6GR?\b|IMT-2030|\bTHz\b|terahertz|\bFR3\b|\bISAC\b|\bJCAS\b|\bNTN\b|release ?2[01]|rel-2[01]|next g", re.I)),
    ("5g", re.compile(r"\b5G\b|5G-Advanced|\bNR\b|new radio|release ?1[5-9]|rel-1[5-9]|\bLTE\b|private 5g", re.I)),
    ("wifi", re.compile(r"wi-?fi|802\.11|\bWLAN\b|\bMLO\b|\bEHT\b|\bUHR\b|multi-ap|access points?", re.I)),
    ("massive-mimo", re.compile(r"massive mimo|cell-free|mu-mimo|gigantic mimo|pilot (de)?contamination|\bMIMO\b")),
    ("beamforming", re.compile(r"beamform|precod|phased[- ]array|beam ?steer|beamfocus|\bRIS\b|reconfigurable intelligent|antenna array", re.I)),
    ("macphy", re.compile(r"\bMAC\b|\bPHY\b|physical layer|mac layer|\bHARQ\b|link adaptation|frame format|O-RAN|fronthaul|scheduling|spatial reuse", re.I)),
    ("sp", re.compile(r"OFDM|OTFS|AFDM|channel estimation|equali[sz]|signal processing|\bFFT\b|fourier|fading|waveform|\bSDR\b|software[- ]defined radio|modulation", re.I)),
]

# ---- sources ----
HN_TERMS = ["5G", "6G", "Wi-Fi", "WiFi", "802.11", "beamforming", "MIMO", "OFDM", "3GPP", "mmWave",
            "software defined radio", "SDR", "phased array", "signal processing", "spectrum"]
ARXIV_CATS = ["eess.SP", "cs.IT", "cs.NI"]
ARXIV_TERMS = ['"massive MIMO"', "beamforming", "precoding", '"cell-free"', "ISAC", "6G", "5G", '"Wi-Fi"', "802.11",
               "OFDM", "OTFS", "AFDM", '"channel estimation"', '"physical layer"', '"MAC layer"', "mmWave", "RIS"]
ARXIV_MAX_PER_RUN = int(os.environ.get("WT_ARXIV_MAX", "300"))

# Curated YouTube channels (IDs resolved from real video pages; RSS verified 200 on 2026-11-02)
YT_CHANNELS = [
    ("Wireless Future", "UCOrjRoYJPqGiR1SZvU3xcYQ", "youtube"),
    ("Iain Explains Signals", "UCrltzuSvRbL3rpsvLDnFkuQ", "youtube"),
    ("ATIS (Next G Alliance)", "UC4tZWKb97vS7hJLtEDSNE-A", "nga"),
    ("IEEE ComSoc", "UCumALaUizr9Ow7giZ39WoAQ", "ieee"),
    ("IEEE Signal Processing Society", "UCAgRLbgh-cW_8U_JN97RsyA", "ieee"),
    ("ACM SIGCOMM", "UCaSNALvlJ6ZJ0ZhLDOOhD7g", "acm"),
    ("ACM SIGMOBILE ONLINE", "UCphR-rPFRLhsQNElk2dSbtw", "acm"),
    ("Qualcomm Research", "UCDqolcMZUivtkZltY9klFvw", "youtube"),
    ("Rohde & Schwarz", "UCoJfm2BU72j699FH3IUr3mg", "youtube"),
    ("Keysight", "UCzexaoG8Uec4DXPXyFyWGcQ", "youtube"),
    ("MATLAB", "UCgdHSFcXvkN6O3NXvif0-pA", "youtube"),
    ("Wireless LAN Professionals", "UCIzBSS46vcqhwmBZ7ZpY-yg", "youtube"),
    ("Wireless & AI", "UCeBPR_Zz2HiTtc8BAyFL9Rg", "youtube"),
    ("Nokia Bell Labs", "UCJIjs5B0HQtbuH48uibfLqQ", "youtube"),
    ("6G Flagship", "UCi1VhmHuvkSnqrYuRCBx5Vw", "youtube"),
    ("CWNPTV", "UC4_fdLXjHf_Kd_PXKD3MxHw", "youtube"),
    ("Frame Level Wi-Fi with Rasika", "UCJxInkdnODk_iUrf2bS6ddA", "youtube"),
    ("Alethea Communications", "UCsygeJrLMv7Md9fQnDoqhjg", "youtube"),
    ("Hexa-X-II", "UCjgstiIVNRcIVEVwjmog7kw", "youtube"),
]
YT_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
YT_API_QUERIES = ["5G NR physical layer", "6G air interface", "Wi-Fi 7 MLO", "Wi-Fi 8 802.11bn", "OFDM tutorial",
                  "beamforming tutorial", "massive MIMO", "cell-free massive MIMO", "ISAC 6G", "3GPP Release 20",
                  "802.11 MAC layer", "5G NR HARQ"]   # 12 x 100 quota units = 1,200 of 10,000/day

NGA_URL = "https://nextgalliance.org/"
NGA_PATTERNS = ("/white_papers/", "/highlights/", "/press-releases/")
GPP_NEWS_URL = "https://www.3gpp.org/news-events/3gpp-news"
IEEETV_URLS = ["https://ieeetv.ieee.org/channels/communications"]
