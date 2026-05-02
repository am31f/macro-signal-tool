"""
news_ingestion.py
Phase 2, Task 2.1 — MacroSignalTool

Legge 5 RSS feeds macro ogni ora, normalizza le news in formato standardizzato
e le salva in JSON (+ opzionale SQLite). Progettato per essere chiamato da
APScheduler ogni 60 minuti o manualmente per test.

Dipendenze: pip install feedparser requests python-dateutil
"""

import feedparser
import requests
import json
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

# ─── Configurazione logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
log = logging.getLogger("news_ingestion")

# ─── Percorsi ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent          # = cartella files/
# Su Railway usa il volume persistente /data, in locale usa files/
_railway_data = Path("/data")
_data_root = _railway_data if _railway_data.exists() else BASE_DIR
_data_root.mkdir(parents=True, exist_ok=True)
DATA_DIR = _data_root / "news_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH  = _data_root / "paper_trading.db"  # stesso DB usato da portfolio_manager
NEWS_CACHE_PATH = DATA_DIR / "news_cache.json"

# ─── Feed RSS definiti ────────────────────────────────────────────────────────
RSS_FEEDS = [
    {
        "name": "AP News Business",
        "url": "https://rsshub.app/apnews/topics/business-news",
        "category": "business",
        "language": "en",
        "priority": 1
    },
    {
        "name": "Reuters (via rss.app)",
        "url": "https://rss.app/feeds/tJbAMbCCKSgxNK8n.xml",
        "category": "world",
        "language": "en",
        "priority": 1
    },
    {
        "name": "Financial Times",
        "url": "https://www.ft.com/rss/home/uk",
        "category": "finance",
        "language": "en",
        "priority": 1
    },
    {
        "name": "BBC World",
        "url": "http://feeds.bbci.co.uk/news/world/rss.xml",
        "category": "world",
        "language": "en",
        "priority": 2
    },
    {
        "name": "ANSA Economia",
        "url": "https://www.ansa.it/sito/notizie/economia/economia_rss.xml",
        "category": "economy",
        "language": "it",
        "priority": 2
    },
    {
        "name": "Il Sole 24 Ore",
        "url": "https://www.ilsole24ore.com/rss/mondo.xml",
        "category": "world_it",
        "language": "it",
        "priority": 2
    },
    {
        "name": "MarketWatch Top Stories",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "category": "markets",
        "language": "en",
        "priority": 1
    },
    {
        "name": "TGCom24 Economia",
        "url": "https://www.tgcom24.mediaset.it/rss/economia.xml",
        "category": "economy_it",
        "language": "it",
        "priority": 2
    },
    {
        "name": "Corriere della Sera Economia",
        "url": "https://xml2.corriereobjects.it/rss/economia.xml",
        "category": "economy_it",
        "language": "it",
        "priority": 2
    },
    {
        "name": "Repubblica Economia",
        "url": "https://www.repubblica.it/rss/economia/rss2.0.xml",
        "category": "economy_it",
        "language": "it",
        "priority": 2
    },
]

# ─── Struttura standardizzata ─────────────────────────────────────────────────
@dataclass
class NewsItem:
    id: str                         # SHA256 hash(url + headline)
    headline: str
    source: str
    source_feed: str
    timestamp_utc: str              # ISO 8601
    timestamp_unix: int
    full_text_snippet: str          # Max 800 chars
    url: str
    language: str
    category: str
    ingested_at: str                # Quando l'abbiamo letta noi
    classified: bool = False        # True dopo news_classifier.py
    classification_result: Optional[dict] = None
    materiality_score: Optional[float] = None


def _make_id(url: str, headline: str) -> str:
    """Genera ID deterministico per deduplicazione."""
    raw = f"{url}||{headline}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _clean_text(text: str, max_len: int = 800) -> str:
    """Pulisce HTML/XML residui e tronca."""
    import re
    # Rimuove tag HTML
    text = re.sub(r"<[^>]+>", " ", text or "")
    # Normalizza whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _parse_date(entry) -> tuple[str, int]:
    """Estrae data dall'entry RSS, fallback a now."""
    try:
        from email.utils import parsedate_to_datetime
        if hasattr(entry, "published"):
            dt = parsedate_to_datetime(entry.published)
            dt_utc = dt.astimezone(timezone.utc)
            return dt_utc.isoformat(), int(dt_utc.timestamp())
    except Exception:
        pass
    now = datetime.now(timezone.utc)
    return now.isoformat(), int(now.timestamp())


def fetch_feed(feed_config: dict) -> list[NewsItem]:
    """Scarica e parsa un singolo feed RSS. Ritorna lista di NewsItem."""
    items = []
    try:
        log.info(f"Fetching: {feed_config['name']} — {feed_config['url']}")

        # feedparser gestisce sia http che https, redirect, encoding
        headers = {"User-Agent": "MacroSignalTool/0.1 (research bot)"}
        resp = requests.get(feed_config["url"], headers=headers, timeout=15)
        resp.raise_for_status()

        feed = feedparser.parse(resp.content)

        if feed.bozo:
            log.warning(f"Feed malformed ({feed_config['name']}): {feed.bozo_exception}")

        ingested_at = datetime.now(timezone.utc).isoformat()

        for entry in feed.entries[:30]:  # max 30 per feed per poll
            headline = _clean_text(getattr(entry, "title", ""), 300)
            if not headline:
                continue

            url = getattr(entry, "link", "") or getattr(entry, "id", "")
            snippet = _clean_text(
                getattr(entry, "summary", "") or getattr(entry, "description", ""),
                800
            )
            ts_iso, ts_unix = _parse_date(entry)

            item = NewsItem(
                id=_make_id(url, headline),
                headline=headline,
                source=feed_config["name"],
                source_feed=feed_config["url"],
                timestamp_utc=ts_iso,
                timestamp_unix=ts_unix,
                full_text_snippet=snippet,
                url=url,
                language=feed_config.get("language", "en"),
                category=feed_config.get("category", "general"),
                ingested_at=ingested_at,
            )
            items.append(item)

        log.info(f"  → {len(items)} items da {feed_config['name']}")

    except requests.exceptions.RequestException as e:
        log.error(f"Errore HTTP feed {feed_config['name']}: {e}")
    except Exception as e:
        log.error(f"Errore parsing feed {feed_config['name']}: {e}", exc_info=True)

    return items


def load_cache() -> dict[str, dict]:
    """Carica il cache locale delle news già viste (per deduplicazione)."""
    if NEWS_CACHE_PATH.exists():
        try:
            with open(NEWS_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            log.warning("Cache corrotta, reset.")
    return {}


def save_cache(cache: dict[str, dict]):
    """Salva il cache. Mantiene solo le ultime 2000 news per non crescere all'infinito."""
    # Ordina per timestamp e taglia
    sorted_items = sorted(cache.values(), key=lambda x: x.get("timestamp_unix", 0), reverse=True)
    trimmed = {item["id"]: item for item in sorted_items[:2000]}
    with open(NEWS_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def _check_db_integrity(db_path: Path) -> bool:
    """Verifica integrità DB SQLite. Se corrotto, lo cancella. Ritorna True se OK."""
    if not db_path.exists():
        return True
    try:
        with sqlite3.connect(str(db_path), timeout=5) as _c:
            result = _c.execute("PRAGMA integrity_check").fetchone()
            return result and result[0] == "ok"
    except Exception as e:
        log.error(f"DB news corrotto ({e}), ricreo: {db_path}")
        try:
            db_path.unlink()
        except Exception:
            pass
        return True


def save_to_sqlite(items: list[NewsItem]):
    """Opzionale: salva in SQLite per query storiche."""
    _check_db_integrity(DB_PATH)
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id TEXT PRIMARY KEY,
                headline TEXT,
                source TEXT,
                timestamp_utc TEXT,
                timestamp_unix INTEGER,
                full_text_snippet TEXT,
                url TEXT,
                language TEXT,
                category TEXT,
                ingested_at TEXT,
                classified INTEGER DEFAULT 0,
                materiality_score REAL,
                classification_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_ts ON news(timestamp_unix)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_classified ON news(classified)")

        for item in items:
            conn.execute("""
                INSERT OR IGNORE INTO news
                (id, headline, source, timestamp_utc, timestamp_unix,
                 full_text_snippet, url, language, category, ingested_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                item.id, item.headline, item.source, item.timestamp_utc,
                item.timestamp_unix, item.full_text_snippet, item.url,
                item.language, item.category, item.ingested_at
            ))
        conn.commit()
        conn.close()
        log.info(f"SQLite: {len(items)} news inserite (ignore duplicates)")
    except Exception as e:
        log.error(f"Errore SQLite: {e}")


def run_ingestion(feeds: list[dict] = None, save_db: bool = True) -> list[NewsItem]:
    """
    Entry point principale. Scarica tutti i feed, deduplica, salva cache + DB.
    
    Returns:
        Lista di NewsItem NUOVI (non già visti in cache).
    """
    if feeds is None:
        feeds = RSS_FEEDS

    log.info(f"=== Inizio ingestion: {len(feeds)} feed ===")

    cache = load_cache()
    new_items = []

    for feed_config in feeds:
        items = fetch_feed(feed_config)
        for item in items:
            if item.id not in cache:
                cache[item.id] = asdict(item)
                new_items.append(item)

    log.info(f"=== Nuovi articoli trovati: {len(new_items)} ===")

    # Salva cache aggiornata
    save_cache(cache)

    # Salva in SQLite
    if save_db and new_items:
        save_to_sqlite(new_items)

    return new_items


def reload_cache_to_db() -> int:
    """
    Forza il reinserimento di tutte le news dalla cache JSON nel DB SQLite.
    Utile quando il DB è vuoto ma la cache ha già articoli.
    Ritorna il numero di articoli inseriti/aggiornati.
    """
    cache = load_cache()
    if not cache:
        log.info("reload_cache_to_db: cache vuota, niente da fare")
        return 0

    items = []
    for data in cache.values():
        try:
            item = NewsItem(
                id=data["id"],
                headline=data.get("headline", ""),
                source=data.get("source", ""),
                source_feed=data.get("source_feed", ""),
                timestamp_utc=data.get("timestamp_utc", ""),
                timestamp_unix=data.get("timestamp_unix", 0),
                full_text_snippet=data.get("full_text_snippet", ""),
                url=data.get("url", ""),
                language=data.get("language", "en"),
                category=data.get("category", "general"),
                ingested_at=data.get("ingested_at", ""),
                classified=data.get("classified", False),
                materiality_score=data.get("materiality_score"),
            )
            items.append(item)
        except Exception as e:
            log.warning(f"reload_cache_to_db: skip item {data.get('id','?')}: {e}")

    save_to_sqlite(items)
    log.info(f"reload_cache_to_db: {len(items)} articoli sincronizzati nel DB")
    return len(items)


def get_unclassified(limit: int = 50) -> list[dict]:
    """
    Recupera news non ancora classificate dal DB SQLite.
    Usato da news_classifier.py come input.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute("""
            SELECT id, headline, full_text_snippet, source, timestamp_utc, url
            FROM news
            WHERE classified = 0
            ORDER BY timestamp_unix DESC
            LIMIT ?
        """, (limit,))
        rows = [
            {
                "id": r[0], "headline": r[1], "full_text_snippet": r[2],
                "source": r[3], "timestamp_utc": r[4], "url": r[5]
            }
            for r in cursor.fetchall()
        ]
        conn.close()
        return rows
    except Exception as e:
        log.error(f"Errore get_unclassified: {e}")
        return []


def mark_classified(news_id: str, result: dict, materiality_score: float):
    """Aggiorna il record DB dopo classificazione."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            UPDATE news
            SET classified = 1,
                materiality_score = ?,
                classification_json = ?
            WHERE id = ?
        """, (materiality_score, json.dumps(result), news_id))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Errore mark_classified: {e}")


# ─── Esecuzione diretta (test / cron) ─────────────────────────────────────────
if __name__ == "__main__":
    import sys

    test_mode = "--test" in sys.argv

    if test_mode:
        log.info("Modalita test: solo Reuters + MarketWatch")
        feeds_to_use = [f for f in RSS_FEEDS if "Reuters" in f["name"] or "MarketWatch" in f["name"]]
    else:
        feeds_to_use = RSS_FEEDS

    new_news = run_ingestion(feeds_to_use, save_db=True)

    print(f"\n{'='*60}")
    print(f"Nuovi articoli ingested: {len(new_news)}")
    print(f"{'='*60}")
    for item in new_news[:5]:
        print(f"\n[{item.source}] {item.timestamp_utc[:16]}")
        print(f"  {item.headline}")
        print(f"  {item.full_text_snippet[:120]}...")
    if len(new_news) > 5:
        print(f"\n  ... e altri {len(new_news) - 5} articoli.")
