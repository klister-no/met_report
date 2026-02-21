"""
news_fetcher.py
---------------
Henter relevante nyhetsartikler via RSS-feeds fra verifiserte kilder.
Filtrerer på søkeord knyttet til produksjonsregioner og transportkorridorer.

Returnerer en liste med article-dicts som injiseres i HTML av generate_html.py.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import json
import time

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

logger = logging.getLogger("news_fetcher")

# ── RSS FEEDS ─────────────────────────────────────────────────────────────────
# Kun åpne, verifiserte feeds. Ingen betalt API nødvendig.
RSS_FEEDS = [
    {
        "name": "Reuters",
        "tag": "reuters",
        "url": "https://feeds.reuters.com/reuters/topNews",
        "alt_url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",  # fallback
    },
    {
        "name": "BBC News",
        "tag": "bbc",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
    },
    {
        "name": "Associated Press",
        "tag": "ap",
        "url": "https://rsshub.app/ap/topics/apf-europe",
        "alt_url": "https://feeds.feedburner.com/APWorldNews",
    },
    {
        "name": "AFP (via Google News)",
        "tag": "afp",
        "url": "https://news.google.com/rss/search?q=AFP+storm+flood+Spain+Morocco+agriculture&hl=en&gl=US&ceid=US:en",
    },
    {
        "name": "ESA Copernicus EMSR",
        "tag": "copernicus",
        "url": "https://emergency.copernicus.eu/mapping/activations-rapid/feed",
    },
    {
        "name": "AEMET",
        "tag": "aemet",
        "url": "https://www.aemet.es/es/rss/avisos_cap.xml",
    },
    {
        "name": "Packaging Europe",
        "tag": "other",
        "url": "https://packagingeurope.com/rss/",
    },
    {
        "name": "Port of Rotterdam",
        "tag": "other",
        "url": "https://www.portofrotterdam.com/en/rss.xml",
    },
    {
        "name": "Google News — Supply Chain",
        "tag": "other",
        "url": "https://news.google.com/rss/search?q=Algeciras+OR+%22Tanger+Med%22+OR+%22Rotterdam+port%22+storm+flood+supply+chain&hl=en&gl=US&ceid=US:en",
    },
    {
        "name": "Google News — Huelva Sevilla",
        "tag": "afp",
        "url": "https://news.google.com/rss/search?q=Huelva+OR+Sevilla+OR+Andalusia+flood+agriculture+2026&hl=en&gl=ES&ceid=ES:es",
    },
    {
        "name": "Google News — Morocco floods",
        "tag": "reuters",
        "url": "https://news.google.com/rss/search?q=Morocco+flood+agriculture+Tanger+2026&hl=en&gl=MA&ceid=MA:en",
    },
]

# ── RELEVANCE KEYWORDS ─────────────────────────────────────────────────────────
# En artikkel inkluderes hvis den inneholder minst ett ord fra GEO_KEYWORDS
# og minst ett fra TOPIC_KEYWORDS.
GEO_KEYWORDS = [
    "huelva", "sevilla", "seville", "andalusia", "andalucía",
    "almería", "almeria", "murcia", "valencia",
    "portugal", "algarve", "lisboa", "lisbon",
    "morocco", "maroc", "marruecos", "tanger", "tangier", "agadir",
    "loukkos", "gharb", "souss",
    "algeciras", "rotterdam", "amsterdam",
    "tanger med", "strait of gibraltar",
    "iberia", "iberian",
]

TOPIC_KEYWORDS = [
    "flood", "flooding", "storm", "inundation", "inundación", "inundação",
    "rainfall", "precipitation", "hurricane", "cyclone",
    "agriculture", "agricultural", "farming", "harvest", "crop",
    "strawberry", "tomato", "vegetable", "fruit", "produce",
    "port", "ferry", "shipping", "logistics", "supply chain",
    "transport", "truck", "cargo", "reefer",
    "disruption", "delay", "closure", "blocked",
    "climate", "weather", "extreme",
    "copernicus", "emsr", "aemet",
]

# Prioritetsnivå basert på innhold
HIGH_PRIORITY_KEYWORDS = [
    "state of emergency", "emergency declared", "dead", "fatalities",
    "thousands displaced", "critical", "catastrophic",
    "port closed", "ferry suspended", "ferry cancelled",
    "record", "historic", "unprecedented",
]


def _score_article(title: str, summary: str) -> dict:
    """Return relevance score and priority level for an article."""
    text = (title + " " + summary).lower()

    geo_match   = any(k in text for k in GEO_KEYWORDS)
    topic_match = any(k in text for k in TOPIC_KEYWORDS)
    high_prio   = any(k in text for k in HIGH_PRIORITY_KEYWORDS)

    if not geo_match or not topic_match:
        return {"relevant": False}

    return {
        "relevant": True,
        "priority": "high" if high_prio else "moderate" if geo_match and topic_match else "info",
    }


def _parse_rss_date(date_str: str) -> datetime:
    """Parse RSS pubDate into datetime. Returns now() on failure."""
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return datetime.now(timezone.utc)


def _fetch_feed(feed: dict, timeout: int = 8) -> list[dict]:
    """Fetch and parse a single RSS feed. Returns list of article dicts."""
    if not HAS_REQUESTS:
        return []

    articles = []
    urls = [feed["url"]] + ([feed["alt_url"]] if "alt_url" in feed else [])

    for url in urls:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; SupplyChainBot/1.0; +https://github.com)",
                "Accept": "application/rss+xml, application/xml, text/xml",
            }
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()

            root = ET.fromstring(resp.content)

            # Handle both RSS 2.0 and Atom
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)

            for item in items[:30]:  # Max 30 per feed
                def _text(tag, default=""):
                    el = item.find(tag)
                    if el is None:
                        el = item.find(f"atom:{tag}", ns)
                    return (el.text or "").strip() if el is not None else default

                title   = _text("title")
                link    = _text("link") or _text("guid")
                summary = re.sub(r"<[^>]+>", "", _text("description") or _text("summary"))
                pub_date_str = _text("pubDate") or _text("published") or _text("updated")
                pub_date = _parse_rss_date(pub_date_str)

                score = _score_article(title, summary)
                if not score["relevant"]:
                    continue

                articles.append({
                    "source": feed["name"],
                    "tag":    feed["tag"],
                    "title":  title,
                    "link":   link,
                    "summary": summary[:400] + ("…" if len(summary) > 400 else ""),
                    "pub_date": pub_date,
                    "pub_date_str": pub_date.strftime("%-d. %B %Y") if pub_date else "",
                    "priority": score["priority"],
                })

            if articles:
                break  # Got results from first URL, skip alt

        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            continue

    return articles


def fetch_news(max_articles: int = 12,
               cache_path: str = "data/cache/news_cache.json",
               max_age_hours: int = 4) -> list[dict]:
    """
    Fetch news from all configured RSS feeds.
    Returns a deduplicated, sorted list of relevant articles.

    Uses file cache to avoid hitting feeds on every call.
    """
    cache = Path(cache_path)
    cache.parent.mkdir(parents=True, exist_ok=True)

    # ── Try cache first ───────────────────────────────────────────────────────
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            age_hours = (time.time() - cached.get("timestamp", 0)) / 3600
            if age_hours < max_age_hours:
                logger.info(f"Using cached news ({age_hours:.1f}h old)")
                return cached["articles"]
        except Exception:
            pass

    if not HAS_REQUESTS:
        logger.warning("requests not installed — skipping news fetch")
        return []

    # ── Fetch all feeds ───────────────────────────────────────────────────────
    all_articles = []
    for feed in RSS_FEEDS:
        logger.info(f"Fetching: {feed['name']}")
        try:
            articles = _fetch_feed(feed)
            all_articles.extend(articles)
            logger.info(f"  → {len(articles)} relevant articles")
        except Exception as e:
            logger.warning(f"Feed error ({feed['name']}): {e}")

    # ── Deduplicate by title similarity ──────────────────────────────────────
    seen_titles = set()
    deduped = []
    for art in all_articles:
        title_key = re.sub(r"[^a-z0-9]", "", art["title"].lower())[:60]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            deduped.append(art)

    # ── Sort: high priority first, then by date ───────────────────────────────
    priority_order = {"high": 0, "moderate": 1, "info": 2}
    deduped.sort(key=lambda a: (
        priority_order.get(a["priority"], 2),
        -(a["pub_date"].timestamp() if a["pub_date"] else 0),
    ))

    result = deduped[:max_articles]

    # ── Cache result ──────────────────────────────────────────────────────────
    try:
        # Make articles JSON-serializable
        serializable = []
        for a in result:
            a2 = dict(a)
            a2["pub_date"] = a["pub_date"].isoformat() if a.get("pub_date") else ""
            serializable.append(a2)
        cache.write_text(json.dumps({
            "timestamp": time.time(),
            "articles": serializable,
        }, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f"Could not write news cache: {e}")

    logger.info(f"News fetch complete: {len(result)} articles")
    return result


# ── HTML rendering ────────────────────────────────────────────────────────────

TAG_CSS = {
    "reuters":    "tag-reuters",
    "bbc":        "tag-bbc",
    "ap":         "tag-ap",
    "afp":        "tag-afp",
    "copernicus": "tag-copernicus",
    "aemet":      "tag-aemet",
    "other":      "tag-other",
}

PRIORITY_CSS = {
    "high":     "news-high",
    "moderate": "news-moderate",
    "info":     "news-info",
}


def render_news_html(articles: list[dict]) -> str:
    """Render articles list into HTML news-item divs."""
    if not articles:
        return """
    <div class="news-item news-info">
      <span class="news-source-tag tag-other">INFO</span>
      <div class="news-content">
        <div class="news-headline">Ingen nye relevante nyheter funnet ved denne oppdateringen</div>
        <div class="news-summary">RSS-feeds fra Reuters, BBC, AFP, ESA Copernicus og andre ble sjekket. Ingen artikler matchet søkekriteriene for produksjonsregionene og transportkorridorene. Prøv igjen ved neste oppdatering.</div>
        <div class="news-meta"><span>🔄 Neste sjekk: ved neste automatiske oppdatering</span></div>
      </div>
    </div>"""

    rows = []
    for art in articles:
        tag_css      = TAG_CSS.get(art.get("tag", "other"), "tag-other")
        priority_css = PRIORITY_CSS.get(art.get("priority", "info"), "news-info")
        source       = art.get("source", "Ukjent")
        title        = art.get("title", "")
        link         = art.get("link", "#")
        summary      = art.get("summary", "")
        date_str     = art.get("pub_date_str", "")

        headline_html = (
            f'<a href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>'
            if link and link != "#" else title
        )

        rows.append(f"""    <div class="news-item {priority_css}">
      <span class="news-source-tag {tag_css}">{source}</span>
      <div class="news-content">
        <div class="news-headline">{headline_html}</div>
        <div class="news-summary">{summary}</div>
        <div class="news-meta"><span>📅 {date_str}</span></div>
      </div>
    </div>""")

    return "\n".join(rows)
