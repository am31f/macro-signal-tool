"""
main.py
Phase 4+6+8 — MacroSignalTool — FastAPI Orchestratore

Entry point dell'applicazione. Espone tutti i moduli via REST API.
APScheduler: polling news 1h, update prezzi 15min, email digest 8:00 giornaliero.
Telegram: alert su segnali nuovi (confidence > soglia), stop/target raggiunti.

Endpoints:
  GET  /                        — health check + stato portafoglio
  POST /news/fetch              — fetch + classifica news ora (manuale)
  GET  /news/unclassified       — news non ancora classificate
  GET  /signals/run             — esegui pipeline su news classificate recenti
  GET  /signals/latest          — ultimi segnali generati
  POST /trade/execute           — esegui segnale in paper trading
  POST /trade/close             — chiudi posizione manualmente
  GET  /portfolio               — stato portafoglio completo
  GET  /portfolio/positions     — posizioni aperte
  POST /portfolio/update-prices — aggiorna prezzi e controlla stop/target
  GET  /performance             — report performance completo
  GET  /journal                 — trade journal ultimi 20 entry

Phase 8 — Instagram:
  POST /instagram/publish       — pubblica carosello Instagram (trigger manuale)
  GET  /instagram/status        — stato Instagram (configurato, ultimo post, ecc.)
  POST /instagram/comments      — processa commenti recenti (dry_run=true di default)

Avvio: uvicorn main:app --reload --port 8000
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Moduli MacroSignalTool
import sys
sys.path.insert(0, str(Path(__file__).parent))

from news_ingestion import run_ingestion, get_unclassified, mark_classified, save_to_sqlite, reload_cache_to_db, DB_PATH as NEWS_DB_PATH
from news_classifier import run_classification_batch
from signal_pipeline import process_classified_news
from trade_structurer import structure_all_signals
from position_sizer import size_trade
from paper_executor import execute_signal, check_all_stops_and_targets, get_journal
from portfolio_manager import (
    init_db as init_portfolio_db, get_portfolio_state,
    get_open_positions, get_closed_positions, DB_PATH
)
from performance_tracker import generate_report

# Phase 6: Alerting (opzionale — degrada gracefully se non configurato)
try:
    from telegram_bot import TelegramNotifier
    _telegram = TelegramNotifier()
except ImportError:
    _telegram = None

try:
    from email_digest import send_daily_digest
    _email_digest_available = True
except ImportError:
    _email_digest_available = False

# Phase 8: Instagram (opzionale — degrada gracefully se non configurato)
try:
    from instagram_content_generator import generate_carousel_content
    from slide_renderer_pillow import render_carousel_slides_pillow as render_carousel_slides
    from instagram_publisher import publish_carousel
    from comment_handler import process_all_recent_posts as process_recent_comments

    def pick_top_signal(cache_path: str) -> Optional[dict]:
        """Seleziona il segnale con confidence più alta dalla cache."""
        try:
            with open(cache_path) as f:
                data = json.load(f)
            # La cache ha struttura {"timestamp":..., "signals":[...]}
            if isinstance(data, dict):
                signals = data.get("signals", [])
            elif isinstance(data, list):
                signals = data
            else:
                return None
            if not signals:
                return None
            return max(signals, key=lambda s: s.get("confidence_composite", s.get("confidence", 0)))
        except Exception:
            return None

    _ig_token = os.getenv("IG_ACCESS_TOKEN", "")
    _ig_account = os.getenv("IG_BUSINESS_ACCOUNT_ID", "")
    _instagram_available = bool(_ig_token and _ig_account)
except ImportError as _ig_err:
    _instagram_available = False
    generate_carousel_content = None
    render_carousel_slides = None
    publish_carousel = None
    process_recent_comments = None
    pick_top_signal = None

# ─── Config ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# Su Railway il DB deve stare su un volume persistente montato in /data
# In locale usa la cartella files/ come prima
_railway_data = Path("/data")
DATA_DIR = _railway_data if _railway_data.exists() else Path(__file__).parent
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Cache in memoria per ultimi segnali (evita ricalcoli frequenti)
_latest_signals: list = []
_latest_pipeline_output: dict = {}

# Stato Instagram (persiste in memoria tra le richieste)
_ig_last_post_id: Optional[str] = None
_ig_last_post_at: Optional[str] = None
_ig_last_error: Optional[str] = None

# ─── App FastAPI ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="MacroSignalTool API",
    description="Tool di analisi macro-geopolitica con paper trading integrato.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve le slide Instagram come file statici pubblici
# Meta Graph API richiede URL pubblici per le immagini del carosello
_ig_slides_dir = Path(__file__).parent / "ig_slides"
_ig_slides_dir.mkdir(exist_ok=True)
app.mount("/static/ig_slides", StaticFiles(directory=str(_ig_slides_dir)), name="ig_slides")


# ─── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """Inizializza DB e scheduler al bootstrap."""
    logger.info("MacroSignalTool avvio...")
    logger.info(f"DATA_DIR={DATA_DIR}, exists={DATA_DIR.exists()}")

    # Inizializza DB portfolio (con fallback se il volume non è montato)
    try:
        init_portfolio_db()
        logger.info("DB inizializzati ✅")
    except Exception as e:
        logger.error(f"Errore init DB: {e} — continuo comunque")

    # Avvia scheduler APScheduler (se disponibile)
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        # Ciclo completo ogni 3h: fetch news → classifica → pipeline segnali
        scheduler.add_job(
            _scheduled_full_cycle,
            "interval",
            hours=3,
            id="full_cycle",
            replace_existing=True,
        )
        scheduler.add_job(
            _scheduled_price_update,
            "interval",
            minutes=15,
            id="price_update",
            replace_existing=True,
        )
        # Phase 6.2: daily digest email alle 8:00
        if _email_digest_available:
            import asyncio as _aio
            scheduler.add_job(
                lambda: _aio.get_event_loop().run_in_executor(None, send_daily_digest),
                "cron",
                hour=8,
                minute=0,
                id="daily_digest",
                replace_existing=True,
            )
            logger.info("APScheduler: digest email schedulato alle 8:00 ✅")

        # Phase 8: Instagram carousel giornaliero alle 09:00 CET
        if _instagram_available:
            scheduler.add_job(
                _scheduled_instagram_post,
                "cron",
                hour=9,
                minute=0,
                timezone="Europe/Rome",
                id="instagram_carousel",
                replace_existing=True,
            )
            scheduler.add_job(
                _scheduled_instagram_comments,
                "interval",
                hours=1,
                id="instagram_comments",
                replace_existing=True,
            )
            logger.info("APScheduler: Instagram carosello 09:00 CET + commenti ogni ora ✅")

        scheduler.start()
        logger.info("APScheduler avviato: ciclo completo ogni 3h, prezzi ogni 15min ✅")
    except ImportError:
        logger.warning("APScheduler non installato — polling automatico disabilitato")
    except Exception as e:
        logger.error(f"Errore avvio scheduler: {e} — continuo comunque")


async def _scheduled_full_cycle():
    """Ciclo completo ogni 3h: fetch news → classifica → pipeline segnali."""
    logger.info("⏰ Scheduled: ciclo completo (fetch + classify + pipeline)...")
    try:
        loop = asyncio.get_event_loop()
        # Step 1: fetch + classifica
        await loop.run_in_executor(None, _fetch_and_classify_sync)
        logger.info("⏰ Scheduled: fetch+classify completato, avvio pipeline segnali...")
        # Step 2: pipeline segnali in background (non bloccante)
        loop.run_in_executor(None, lambda: _run_pipeline_sync(50))
    except Exception as e:
        logger.error(f"Scheduled full cycle error: {e}")


async def _scheduled_news_fetch():
    """Task schedulato: fetch + classifica news (legacy, non più usato dallo scheduler)."""
    logger.info("⏰ Scheduled: fetch + classify news...")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _fetch_and_classify_sync)
    except Exception as e:
        logger.error(f"Scheduled news fetch error: {e}")


async def _scheduled_price_update():
    """Task schedulato: aggiorna prezzi e controlla stop/target."""
    try:
        loop = asyncio.get_event_loop()
        closed = await loop.run_in_executor(
            None, lambda: check_all_stops_and_targets(DB_PATH)
        )
        # Phase 6.1: alert Telegram per ogni posizione chiusa automaticamente
        if _telegram and closed:
            for position in (closed if isinstance(closed, list) else []):
                reason = position.get("close_reason", "auto")
                await _telegram.send_trade_closed(position, close_reason=reason)
    except Exception as e:
        logger.error(f"Scheduled price update error: {e}")



async def _scheduled_instagram_post():
    """
    Task schedulato: pubblica carosello Instagram alle 09:00 CET.
    Legge il segnale con confidence piu alta dalla cache, genera il contenuto
    con Claude Haiku, renderizza le slide e pubblica su Instagram.
    """
    if not _instagram_available:
        logger.info("Instagram: non configurato, skip")
        return

    logger.info("Instagram: avvio pubblicazione carosello giornaliero...")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _publish_instagram_carousel_sync)
    except Exception as e:
        logger.error(f"Scheduled Instagram post error: {e}", exc_info=True)


async def _publish_instagram_carousel_sync(dry_run: bool = False) -> dict:
    """
    Flusso di pubblicazione carosello (async).
    Usato sia dallo scheduler che dall'endpoint manuale.
    """
    global _ig_last_post_id, _ig_last_post_at, _ig_last_error
    import tempfile

    try:
        cache_path = DATA_DIR / "signals_cache.json"
        if not cache_path.exists():
            logger.warning("Instagram: signals_cache.json non trovata — nessun post oggi")
            return {"status": "NO_SIGNALS", "message": "Cache segnali vuota"}

        top_signal = pick_top_signal(str(cache_path))
        if not top_signal:
            logger.warning("Instagram: nessun segnale idoneo per il carosello")
            return {"status": "NO_SIGNALS", "message": "Nessun segnale idoneo"}

        signal_headline = top_signal.get("headline", "")[:80]
        logger.info(f"Instagram: segnale selezionato -> {signal_headline}")

        content = generate_carousel_content(top_signal, top_signal)
        if not content:
            logger.error("Instagram: generate_carousel_content ha restituito None")
            return {"status": "ERROR", "message": "Generazione contenuto fallita"}

        content_dict = content.__dict__ if hasattr(content, "__dict__") else content

        with tempfile.TemporaryDirectory(prefix="kairos_ig_") as tmpdir:
            slide_paths = render_carousel_slides(content_dict, tmpdir)
            logger.info(f"Instagram: {len(slide_paths)} slide renderizzate")

            if not slide_paths:
                return {"status": "ERROR", "message": "Rendering slide fallito",
                        "content_keys": list(content_dict.keys()),
                        "pillow_renderer": os.environ.get("PILLOW_RENDERER", "not set"),
                        "slide_renderer_module": str(render_carousel_slides)}

            if dry_run:
                logger.info("Instagram: [DRY RUN] slide pronte, pubblicazione skippata")
                return {
                    "status": "DRY_RUN",
                    "slides_rendered": len(slide_paths),
                    "caption_preview": content_dict.get("caption", "")[:200],
                }

            caption = content_dict.get("caption", "")
            hashtags = content_dict.get("hashtags", [])
            if hashtags:
                caption = caption.rstrip() + "\n\n" + " ".join(f"#{h}" for h in hashtags)

            result = await publish_carousel(slide_paths, caption)
            if result and result.success:
                _ig_last_post_id = result.post_id
                _ig_last_post_at = datetime.now(tz=timezone.utc).isoformat()
                _ig_last_error = None
                logger.info(f"Instagram: carosello pubblicato OK post_id={result.post_id}")
                return {
                    "status": "published",
                    "post_id": result.post_id,
                    "slides": len(slide_paths),
                    "caption_preview": caption[:200],
                }
            else:
                err = result.error if result else "unknown"
                _ig_last_error = err
                logger.error(f"Instagram: pubblicazione fallita — {err}")
                return {"status": "ERROR", "message": err}

    except Exception as e:
        _ig_last_error = str(e)
        logger.error(f"_publish_instagram_carousel_sync error: {e}", exc_info=True)
        return {"status": "ERROR", "message": str(e)}


async def _scheduled_instagram_comments():
    """Task schedulato: processa e risponde ai commenti recenti ogni ora."""
    if not _instagram_available:
        return
    logger.info("Instagram: elaborazione commenti...")
    try:
        stats = await process_all_recent_posts(days=1, dry_run=False)
        logger.info(f"Instagram commenti: {stats}")
    except Exception as e:
        logger.error(f"Scheduled Instagram comments error: {e}")


def _fetch_and_classify_sync():
    """Fetch + classifica news (sincrono, usato in thread pool)."""
    # run_ingestion scarica tutti i feed RSS, deduplica e salva in SQLite
    new_items = run_ingestion()
    logger.info(f"Fetch completato: {len(new_items)} nuove news")
    # run_classification_batch legge le non-classificate dal DB, chiama Claude API e aggiorna DB
    classified = run_classification_batch(limit=100)
    logger.info(f"Classificate {len(classified)} news")
    return classified


# ─── Pydantic models ──────────────────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    signal_index: int = 0      # indice nel latest_signals
    confirm: bool = False       # deve essere True per eseguire


class ManualCloseRequest(BaseModel):
    position_id: int
    close_price: float
    reason: str = "manual"


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/", summary="Health check + stato portafoglio")
async def root():
    try:
        portfolio = get_portfolio_state(DB_PATH)
    except Exception:
        portfolio = {}
    return {
        "status": "ok",
        "version": "0.1.0",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "portfolio_nav": portfolio.get("total_nav"),
        "open_positions": portfolio.get("num_open_positions"),
        "total_return_pct": portfolio.get("total_return_pct"),
        "signals_in_cache": len(_latest_signals),
    }


@app.api_route("/news/fetch", methods=["GET", "POST"], summary="Fetch e classifica news (manuale)")
async def fetch_news(background_tasks: BackgroundTasks):
    """
    Esegue fetch RSS + classificazione Claude in background.
    Ritorna subito con il job_id. Polling su /news/unclassified per lo stato.
    """
    background_tasks.add_task(_fetch_and_classify_sync)
    return {
        "status": "started",
        "message": "Fetch e classificazione news avviati in background",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


@app.post("/news/reload-cache", summary="Forza sync cache JSON → DB SQLite")
async def reload_news_cache():
    """
    Reinserisce nel DB tutte le news già presenti nella cache JSON locale.
    Utile al primo avvio se il DB è vuoto ma la cache ha già articoli.
    Dopo questo, chiama GET /signals/run per generare segnali.
    """
    count = reload_cache_to_db()
    return {
        "status": "ok",
        "articles_synced": count,
        "message": f"{count} articoli sincronizzati dal cache JSON al DB. Ora puoi chiamare /signals/run.",
    }


@app.get("/news/unclassified", summary="News non classificate")
async def get_unclassified_news(limit: int = 10):
    news = get_unclassified(limit=limit)
    return {"count": len(news), "news": news}


@app.api_route("/news/classify", methods=["GET", "POST"], summary="Classifica news non classificate (manuale)")
async def classify_news_manual(background_tasks: BackgroundTasks, limit: int = 50):
    """
    Lancia la classificazione Haiku+Sonnet su tutte le news non ancora classificate.
    Utile dopo POST /news/reload-cache per classificare le news già in DB.
    """
    def _classify_only():
        classified = run_classification_batch(limit=limit)
        logger.info(f"Classificazione manuale completata: {len(classified)} news")
        return classified
    background_tasks.add_task(_classify_only)
    return {
        "status": "started",
        "message": f"Classificazione avviata in background (limit={limit})",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


@app.get("/news/debug", summary="Stato DB news (conteggi per stato classificazione)")
async def news_debug():
    """Mostra quante news ci sono nel DB e quante sono classificate vs non."""
    import sqlite3 as _sq
    db_path = NEWS_DB_PATH
    if not db_path.exists():
        return {"error": "DB non trovato", "db_path": str(db_path)}
    conn = _sq.connect(str(db_path))
    try:
        total = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        classified_count = conn.execute("SELECT COUNT(*) FROM news WHERE classified=1").fetchone()[0]
        unclassified_count = conn.execute("SELECT COUNT(*) FROM news WHERE classified=0").fetchone()[0]
        latest = conn.execute(
            "SELECT headline, source, timestamp_utc, classified, materiality_score FROM news ORDER BY timestamp_unix DESC LIMIT 10"
        ).fetchall()
        # last_fetch_at: timestamp dell'ultima news inserita nel DB
        last_fetch_row = conn.execute(
            "SELECT timestamp_utc FROM news ORDER BY timestamp_unix DESC LIMIT 1"
        ).fetchone()
        last_fetch_at = last_fetch_row[0] if last_fetch_row else None
        return {
            "db_path": str(db_path),
            "total_news": total,
            "classified": classified_count,
            "unclassified": unclassified_count,
            "last_fetch_at": last_fetch_at,
            "latest_10": [
                {"headline": r[0][:80], "source": r[1], "date": r[2][:10],
                 "classified": bool(r[3]), "materiality": r[4]}
                for r in latest
            ]
        }
    finally:
        conn.close()


_pipeline_running = False  # flag per sapere se la pipeline è in corso


@app.api_route("/signals/run/async", methods=["GET", "POST"], summary="Lancia pipeline segnali in background (non bloccante)")
async def run_signals_async(background_tasks: BackgroundTasks, limit: int = 50):
    """Versione non bloccante di /signals/run. Ritorna subito, la pipeline gira in background."""
    global _pipeline_running
    if _pipeline_running:
        return {"status": "already_running", "message": "Pipeline già in esecuzione. Controlla /signals/latest tra qualche minuto."}
    background_tasks.add_task(_run_pipeline_sync, limit)
    return {
        "status": "started",
        "message": f"Pipeline avviata in background (limit={limit}). Controlla /signals/latest tra 1-2 minuti.",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


def _run_pipeline_sync(limit: int):
    """Esegue la pipeline segnali in modo sincrono (chiamato da background task)."""
    global _pipeline_running, _latest_signals, _latest_pipeline_output
    _pipeline_running = True
    try:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(NEWS_DB_PATH))
        conn.row_factory = _sqlite3.Row
        rows = conn.execute("""
            SELECT id, headline, full_text_snippet, source, timestamp_utc, url,
                   materiality_score, classification_json, classified
            FROM news WHERE classified = 1
            ORDER BY timestamp_unix DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        news_list = []
        for r in rows:
            item = dict(r)
            if item.get("classification_json"):
                try:
                    cls = json.loads(item["classification_json"])
                    item.update(cls)
                except Exception:
                    pass
            news_list.append(item)
        if not news_list:
            logger.info("_run_pipeline_sync: nessuna news classificata trovata")
            return

        logger.info(f"_run_pipeline_sync: avvio pipeline su {len(news_list)} news...")
        pipeline_output = process_classified_news(news_list)
        signal_candidates = pipeline_output.get("signal_candidates", [])
        logger.info(f"_run_pipeline_sync: {len(signal_candidates)} segnali generati")

        structured_trades = []
        if signal_candidates:
            structured_trades = structure_all_signals(signal_candidates)

        portfolio_nav = get_portfolio_state(DB_PATH).get("total_nav", 10000)
        enriched_signals = []
        for i, signal in enumerate(signal_candidates):
            trade = structured_trades[i] if i < len(structured_trades) else {}
            sizing = size_trade(
                portfolio_nav=portfolio_nav,
                event_category=signal.get("event_category", ""),
                conviction_pct=float(trade.get("conviction_pct", 70)),
                confidence_composite=float(signal.get("confidence_composite", 0.6)),
                stop_loss_pct=float(trade.get("stop_loss_pct", -7.5)),
                target_pct=float(trade.get("target_pct", 15.0)),
            )
            enriched_signals.append({
                "index": i,
                "signal": signal,
                "trade_structure": trade,
                "sizing": sizing,
            })

        _latest_signals = enriched_signals
        _latest_pipeline_output = pipeline_output

        # Salva cache segnali su disco
        try:
            cache_path = DATA_DIR / "signals_cache.json"
            cache_data = {
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "signals": [
                    {
                        **es["signal"],
                        "position_size_eur": es["sizing"].get("position_size_eur", 0),
                        "kelly_quality":     es["sizing"].get("kelly_quality", "–"),
                        "instruments":       es["trade_structure"].get("instruments", []),
                        "trade_type":        es["trade_structure"].get("trade_type", "–"),
                    }
                    for es in enriched_signals
                ],
            }
            cache_path.write_text(json.dumps(cache_data, indent=2, default=str))
            logger.info(f"_run_pipeline_sync: cache salvata ({len(enriched_signals)} segnali)")
        except Exception as cache_err:
            logger.warning(f"_run_pipeline_sync: salvataggio cache fallito: {cache_err}")

        # Phase 6.1: Telegram alert per segnali ad alta confidence
        if _telegram and enriched_signals:
            signal_threshold = float(os.getenv("TELEGRAM_SIGNAL_THRESHOLD", "0.70"))
            for es in enriched_signals:
                conf = es["signal"].get("confidence_composite", 0)
                if conf >= signal_threshold:
                    signal_for_alert = {
                        **es["signal"],
                        "position_size_eur": es["sizing"].get("position_size_eur", 0),
                        "kelly_quality":     es["sizing"].get("kelly_quality", "–"),
                        "instruments":       es["trade_structure"].get("instruments", []),
                        "trade_type":        es["trade_structure"].get("trade_type", "–"),
                    }
                    try:
                        asyncio.ensure_future(_telegram.send_signal_alert(signal_for_alert))
                    except Exception as tg_err:
                        logger.error(f"Telegram alert error: {tg_err}")

    except Exception as e:
        logger.error(f"_run_pipeline_sync error: {e}", exc_info=True)
    finally:
        _pipeline_running = False


@app.get("/signals/run", summary="Esegui pipeline segnali su news recenti")
async def run_signals(limit: int = 30):
    """
    Carica le ultime N news classificate, le passa alla pipeline a 5 filtri,
    e struttura i trade per i segnali generati.
    Salva i risultati in cache per /signals/latest e /trade/execute.
    Per batch grandi (>30) usa POST /signals/run/async per evitare timeout.
    """
    global _latest_signals, _latest_pipeline_output

    # Carica news classificate recenti dal DB SQLite di news_ingestion
    import sqlite3 as _sqlite3
    try:
        conn = _sqlite3.connect(str(NEWS_DB_PATH))
        conn.row_factory = _sqlite3.Row
        rows = conn.execute("""
            SELECT id, headline, full_text_snippet, source, timestamp_utc, url,
                   materiality_score, classification_json, classified
            FROM news
            WHERE classified = 1
            ORDER BY timestamp_unix DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        news_list = []
        for r in rows:
            item = dict(r)
            # Espandi classification_json se presente
            if item.get("classification_json"):
                try:
                    cls = json.loads(item["classification_json"])
                    item.update(cls)
                except Exception:
                    pass
            news_list.append(item)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore lettura news DB: {e}")

    if not news_list:
        return {
            "status": "NO_NEWS",
            "message": "Nessuna news classificata disponibile. Chiama POST /news/fetch prima.",
        }

    # Pipeline segnali
    pipeline_output = process_classified_news(news_list)
    signal_candidates = pipeline_output.get("signal_candidates", [])

    # Struttura trade per segnali
    structured_trades = []
    if signal_candidates:
        structured_trades = structure_all_signals(signal_candidates)

    # Combina segnale + trade structure + sizing
    enriched_signals = []
    for i, signal in enumerate(signal_candidates):
        trade = structured_trades[i] if i < len(structured_trades) else {}
        sizing = size_trade(
            portfolio_nav=get_portfolio_state(DB_PATH).get("total_nav", 10000),
            event_category=signal.get("event_category", ""),
            conviction_pct=float(trade.get("conviction_pct", 70)),
            confidence_composite=float(signal.get("confidence_composite", 0.6)),
            stop_loss_pct=float(trade.get("stop_loss_pct", -7.5)),
            target_pct=float(trade.get("target_pct", 15.0)),
        )
        enriched_signals.append({
            "index": i,
            "signal": signal,
            "trade_structure": trade,
            "sizing": sizing,
        })

    # Aggiorna cache solo se abbiamo trovato segnali (non sovrascrivere run buone con 0)
    if enriched_signals:
        _latest_signals = enriched_signals
        _latest_pipeline_output = pipeline_output
    else:
        logger.info(f"run_signals: 0 segnali da questa run (limit={limit}), cache precedente mantenuta")

    # Phase 6.1: Telegram alert per segnali con confidence alta
    if _telegram and enriched_signals:
        signal_threshold = float(os.getenv("TELEGRAM_SIGNAL_THRESHOLD", "0.70"))
        for es in enriched_signals:
            conf = es["signal"].get("confidence_composite", 0)
            if conf >= signal_threshold:
                # Arricchisci signal dict con sizing per il messaggio
                signal_for_alert = {
                    **es["signal"],
                    "position_size_eur": es["sizing"].get("position_size_eur", 0),
                    "kelly_quality":     es["sizing"].get("kelly_quality", "–"),
                    "instruments":       es["trade_structure"].get("instruments", []),
                    "trade_type":        es["trade_structure"].get("trade_type", "–"),
                }
                asyncio.ensure_future(_telegram.send_signal_alert(signal_for_alert))

    # Salva cache segnali su disco per Telegram bot /signals command
    try:
        cache_path = DATA_DIR / "signals_cache.json"
        cache_data = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "signals": [
                {
                    **es["signal"],
                    "position_size_eur": es["sizing"].get("position_size_eur", 0),
                    "kelly_quality":     es["sizing"].get("kelly_quality", "–"),
                    "instruments":       es["trade_structure"].get("instruments", []),
                    "trade_type":        es["trade_structure"].get("trade_type", "–"),
                }
                for es in enriched_signals
            ],
        }
        cache_path.write_text(json.dumps(cache_data, indent=2, default=str))
    except Exception as cache_err:
        logger.warning(f"Salvataggio cache segnali fallito: {cache_err}")

    return {
        "status": "ok",
        "total_news_processed": pipeline_output.get("total_news_processed", 0),
        "signals_generated": len(signal_candidates),
        "news_rejected": pipeline_output.get("news_rejected", 0),
        "signals": [
            {
                "index": s["index"],
                "headline": s["signal"].get("headline", "")[:100],
                "event_category": s["signal"].get("event_category"),
                "materiality_score": s["signal"].get("materiality_score"),
                "confidence_composite": s["signal"].get("confidence_composite"),
                "trade_type": s["trade_structure"].get("trade_type"),
                "conviction_pct": s["trade_structure"].get("conviction_pct"),
                "position_size_eur": s["sizing"].get("position_size_eur"),
                "kelly_quality": s["sizing"].get("kelly_quality"),
                "entry_timing": s["signal"].get("entry_timing"),
            }
            for s in enriched_signals
        ],
        "reject_summary": [
            {
                "headline": r.get("headline", "")[:80],
                "rejected_at": r.get("rejected_at_filter"),
                "reason": r.get("reject_reason", "")[:100],
            }
            for r in pipeline_output.get("reject_log", [])[:5]
        ],
        "reject_log_full": pipeline_output.get("reject_log", []),
    }


@app.get("/signals/pipeline-status", summary="Stato pipeline segnali (running / idle)")
async def pipeline_status():
    return {
        "running": _pipeline_running,
        "signals_in_cache": len(_latest_signals),
        "last_run": _latest_pipeline_output.get("run_timestamp"),
    }


@app.get("/signals/latest", summary="Ultimi segnali in cache")
async def get_latest_signals():
    global _latest_signals
    # Se la cache in memoria è vuota, prova a caricarla dal disco (sopravvive ai restart)
    if not _latest_signals:
        cache_path = DATA_DIR / "signals_cache.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                disk_signals = cached.get("signals", [])
                if disk_signals:
                    logger.info(f"Caricati {len(disk_signals)} segnali da disco (signals_cache.json)")
                    # I segnali su disco sono flat — avvolgili in nested structure
                    # per compatibilità con SignalDetail che si aspetta {signal, trade_structure, sizing}
                    wrapped = []
                    for i, s in enumerate(disk_signals):
                        sig_fields = {k: v for k, v in s.items()
                                      if k not in ("position_size_eur","kelly_quality","instruments","trade_type",
                                                   "conviction_pct","stop_loss_pct","target_pct",
                                                   "primary_thesis","hedge_suggestion","alternative_scenario")}
                        wrapped.append({
                            "index": i,
                            "signal": sig_fields,
                            "trade_structure": {
                                "instruments":          s.get("instruments", []),
                                "trade_type":           s.get("trade_type", "–"),
                                "conviction_pct":       s.get("conviction_pct"),
                                "stop_loss_pct":        s.get("stop_loss_pct"),
                                "target_pct":           s.get("target_pct"),
                                "primary_thesis":       s.get("primary_thesis", ""),
                                "hedge_suggestion":     s.get("hedge_suggestion", ""),
                                "alternative_scenario": s.get("alternative_scenario", ""),
                            },
                            "sizing": {
                                "position_size_eur": s.get("position_size_eur", 0),
                                "kelly_quality":     s.get("kelly_quality", "–"),
                            },
                            # Mantieni anche i campi flat per la Dashboard card
                            **s,
                        })
                    return {
                        "count": len(wrapped),
                        "source": "disk_cache",
                        "timestamp": cached.get("timestamp"),
                        "signals": wrapped,
                    }
            except Exception as e:
                logger.warning(f"Errore lettura signals_cache.json: {e}")
        return {"status": "EMPTY", "message": "Nessun segnale disponibile. Chiama /signals/run/async."}
    # Restituisce oggetti che includono sia i campi nested (signal/trade_structure/sizing)
    # sia i campi flat al top level (per la Dashboard card).
    enriched_out = []
    for es in _latest_signals:
        if not isinstance(es, dict):
            continue
        sig   = es.get("signal", {})
        trade = es.get("trade_structure", {})
        sizing = es.get("sizing", {})
        # Oggetto con nested keys + flat keys per la dashboard
        out = {
            "index":          es.get("index", 0),
            "signal":         sig,
            "trade_structure": trade,
            "sizing":         sizing,
            # Flat fields letti da Dashboard.jsx / SignalPreview
            **sig,
            "position_size_eur":    sizing.get("position_size_eur", 0),
            "kelly_quality":        sizing.get("kelly_quality", "–"),
            "instruments":          trade.get("instruments", []),
            "trade_type":           trade.get("trade_type", "–"),
            "conviction_pct":       trade.get("conviction_pct"),
            "stop_loss_pct":        trade.get("stop_loss_pct"),
            "target_pct":           trade.get("target_pct"),
            "primary_thesis":       trade.get("primary_thesis", ""),
            "hedge_suggestion":     trade.get("hedge_suggestion", ""),
            "alternative_scenario": trade.get("alternative_scenario", ""),
        }
        enriched_out.append(out)
    return {
        "count": len(enriched_out),
        "source": "memory",
        "signals": enriched_out,
    }


@app.post("/trade/execute", summary="Esegui segnale in paper trading")
async def execute_trade(request: ExecuteRequest):
    """
    Esegue il segnale indicato dall'indice in paper trading.
    Richiede confirm=true per procedere (protezione da esecuzioni accidentali).
    """
    if not request.confirm:
        return {
            "status": "CONFIRM_REQUIRED",
            "message": "Imposta confirm=true per eseguire il trade in paper.",
        }

    # Se la cache in memoria è vuota, prova a ricaricarla dal disco
    if not _latest_signals:
        cache_path = DATA_DIR / "signals_cache.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                disk_signals = cached.get("signals", [])
                for i, s in enumerate(disk_signals):
                    sig_fields = {k: v for k, v in s.items()
                                  if k not in ("position_size_eur","kelly_quality","instruments","trade_type",
                                               "conviction_pct","stop_loss_pct","target_pct",
                                               "primary_thesis","hedge_suggestion","alternative_scenario")}
                    _latest_signals.append({
                        "index": i,
                        "signal": sig_fields,
                        "trade_structure": {
                            "instruments":   s.get("instruments", []),
                            "trade_type":    s.get("trade_type", "–"),
                            "conviction_pct": s.get("conviction_pct"),
                            "stop_loss_pct": s.get("stop_loss_pct"),
                            "target_pct":    s.get("target_pct"),
                            "primary_thesis": s.get("primary_thesis", ""),
                        },
                        "sizing": {
                            "position_size_eur": s.get("position_size_eur", 0),
                            "kelly_quality":     s.get("kelly_quality", "–"),
                        },
                    })
                logger.info(f"execute_trade: caricati {len(_latest_signals)} segnali da disco")
            except Exception as e:
                logger.warning(f"execute_trade: errore lettura cache disco: {e}")

    if not _latest_signals:
        raise HTTPException(status_code=404, detail="Nessun segnale disponibile. Chiama /signals/run prima.")

    if request.signal_index >= len(_latest_signals):
        raise HTTPException(status_code=400, detail=f"Indice {request.signal_index} fuori range (max {len(_latest_signals)-1})")

    enriched = _latest_signals[request.signal_index]
    try:
        result = execute_signal(
            signal=enriched["signal"],
            trade_structure=enriched["trade_structure"],
            sizing_result=enriched["sizing"],
            db_path=DB_PATH,
        )
        from dataclasses import asdict
        result_dict = asdict(result)
    except Exception as e:
        logger.error(f"execute_signal error idx={request.signal_index}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Errore esecuzione trade: {str(e)}")

    # Phase 6.1: Telegram alert se esecuzione ok
    if _telegram and result.positions_opened:
        headline = enriched["signal"].get("headline", "")[:100]
        msg = (
            f"✅ <b>Trade paper eseguito</b>\n\n"
            f"Segnale: <i>{headline}</i>\n"
            f"Posizioni aperte: {len(result.positions_opened)}\n"
            + "\n".join(
                f"  {'↑' if p.get('direction')=='LONG' else '↓'} "
                f"<b>{p.get('ticker','?')}</b> @{p.get('entry_price',0):.4f} "
                f"(€{p.get('size_eur',0):.0f})"
                for p in result.positions_opened
            )
        )
        asyncio.ensure_future(_telegram.send_message(msg))

    return {"status": "executed", "result": result_dict}


@app.post("/portfolio/reset", summary="Azzera portafoglio paper trading (NAV → 10000)")
async def reset_portfolio_endpoint():
    """Cancella tutte le posizioni e reimposta il NAV a €10.000. Usare solo per reset test."""
    try:
        from portfolio_manager import reset_portfolio as _reset_portfolio
        _reset_portfolio(DB_PATH)
        logger.info("Portfolio reset: NAV → €10.000, tutte le posizioni cancellate")
        return {"status": "ok", "message": "Portafoglio azzerato. NAV = €10.000", "nav": 10000.0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore reset: {e}")


@app.post("/trade/close", summary="Chiudi posizione manualmente")
async def close_trade_manual(request: ManualCloseRequest):
    from portfolio_manager import close_position
    result = close_position(request.position_id, request.close_price, request.reason, DB_PATH)
    if not result:
        raise HTTPException(status_code=404, detail=f"Posizione #{request.position_id} non trovata o già chiusa")

    # Phase 6.1: Telegram alert su chiusura posizione
    if _telegram and result:
        asyncio.ensure_future(_telegram.send_trade_closed(result, close_reason=request.reason))

    return {"status": "closed", "result": result}



@app.get("/news/search", summary="Cerca news per keyword nel DB")
async def search_news(q: str, limit: int = 50):
    """Cerca news contenenti la keyword nel titolo o snippet."""
    import sqlite3
    db_path = NEWS_DB_PATH
    if not db_path.exists():
        return {"count": 0, "results": [], "error": "DB non trovato"}
    q_lower = q.lower()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM news WHERE LOWER(headline) LIKE ? OR LOWER(full_text_snippet) LIKE ? ORDER BY timestamp_unix DESC LIMIT ?",
            (f"%{q_lower}%", f"%{q_lower}%", limit)
        ).fetchall()
        return {
            "count": len(rows),
            "query": q,
            "results": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@app.get("/portfolio", summary="Stato portafoglio completo")
async def get_portfolio():
    return get_portfolio_state(DB_PATH)


@app.get("/portfolio/positions", summary="Posizioni aperte")
async def get_positions():
    positions = get_open_positions(DB_PATH)
    return {"count": len(positions), "positions": positions}


@app.post("/portfolio/update-prices", summary="Aggiorna prezzi e controlla stop/target")
async def update_portfolio_prices(background_tasks: BackgroundTasks):
    background_tasks.add_task(check_all_stops_and_targets, DB_PATH)
    return {"status": "started", "message": "Aggiornamento prezzi avviato in background"}


@app.get("/performance", summary="Report performance paper trading")
async def get_performance():
    return generate_report(DB_PATH)


@app.get("/journal", summary="Trade journal")
async def get_trade_journal(limit: int = 20):
    journal = get_journal(limit)
    return {"count": len(journal), "entries": journal}


# ─── Phase 8: Instagram endpoints ─────────────────────────────────────────────

@app.post("/instagram/publish", summary="Pubblica carosello Instagram (trigger manuale)")
async def instagram_publish_manual(background_tasks: BackgroundTasks, dry_run: bool = False):
    """
    Trigger manuale per la pubblicazione del carosello Instagram.
    Legge la signals_cache.json, genera contenuto con Claude Haiku,
    renderizza le slide e pubblica su Instagram Business Account.

    Parametri:
      dry_run=true  — genera e renderizza le slide ma non pubblica
      dry_run=false — pubblica effettivamente (default)
    """
    if not _instagram_available:
        return {
            "status": "NOT_CONFIGURED",
            "message": (
                "Instagram non configurato. "
                "Aggiungi IG_ACCESS_TOKEN e IG_BUSINESS_ACCOUNT_ID nel file .env "
                "e riavvia il server."
            ),
        }

    import asyncio as _aio
    _aio.create_task(_publish_instagram_carousel_sync(dry_run))
    return {
        "status": "started",
        "dry_run": dry_run,
        "message": "Pubblicazione carosello avviata in background. Controlla i log.",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


@app.get("/instagram/render-test", summary="Testa il renderer slide con dati mock")
async def instagram_render_test():
    """Testa slide_renderer_pillow direttamente con dati mock per debug."""
    import tempfile, traceback as _tb
    try:
        from slide_renderer_pillow import render_carousel_slides_pillow
        mock = {
            "signal_id": "render_test_001",
            "eyebrow": "TEST · DEBUG",
            "date_label": "03 Maggio 2026",
            "hook_title": "Test rendering slide Kairós",
            "hook_subtitle": "Verifica che Pillow funzioni correttamente su Railway",
            "context_title": "Contesto di test",
            "context_stats": [{"value": "OK", "label": "Pillow funziona"}],
            "historical_title": "Storico test",
            "historical_rows": [{"label": "Test", "value": "+100%", "positive": True}],
            "sectors_title": "Settori test",
            "bullish_sectors": "Tech, AI, Cloud",
            "bearish_sectors": "Nessuno in test",
            "cta_question": "Funziona tutto?",
            "cta_body": "Se vedi questo, il renderer Pillow è ok.",
            "cta_channel": "@karios_finance",
            "source_label": "Test",
            "caption": "test",
            "hashtags": ["test"],
        }
        with tempfile.TemporaryDirectory(prefix="kairos_test_") as tmpdir:
            from pathlib import Path as _P
            slides = render_carousel_slides_pillow(mock, _P(tmpdir))
            return {
                "status": "ok",
                "slides_rendered": len(slides),
                "filenames": [_P(s).name for s in slides],
                "pillow_renderer_env": os.environ.get("PILLOW_RENDERER", "not set"),
            }
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "traceback": _tb.format_exc()}


@app.get("/instagram/font-test", summary="Testa disponibilità font su Railway")
async def instagram_font_test():
    """Verifica quali font sono disponibili e se Pillow funziona."""
    import traceback as _tb, subprocess as _sp
    result = {"pillow": False, "fonts": {}, "font_paths": {}, "errors": []}
    try:
        from PIL import Image, ImageDraw, ImageFont
        result["pillow"] = True
        # Prova a caricare default font
        df = ImageFont.load_default()
        img = Image.new("RGB", (100, 100))
        d = ImageDraw.Draw(img)
        try:
            bbox = d.textbbox((0, 0), "Test", font=df)
            result["default_font_textbbox"] = str(bbox)
        except Exception as e:
            result["default_font_textbbox_error"] = str(e)
        # Cerca font sul sistema
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
        for fp in font_paths:
            from pathlib import Path as _P
            result["font_paths"][fp] = _P(fp).exists()
        # Lista tutti i font trovati
        try:
            r = _sp.run(["find", "/usr/share/fonts", "-name", "*.ttf"],
                        capture_output=True, text=True, timeout=5)
            result["available_ttf"] = r.stdout.strip().splitlines()[:20]
        except Exception as e:
            result["font_find_error"] = str(e)
        # Prova render_slide1 con traceback completo
        try:
            from slide_renderer_pillow import render_carousel_slides_pillow
            import tempfile
            mock = {"signal_id": "ft", "hook_title": "Test", "hook_subtitle": "",
                    "eyebrow": "TEST", "date_label": "oggi",
                    "context_title": "ctx", "context_stats": [],
                    "historical_title": "hist", "historical_rows": [],
                    "sectors_title": "sec", "bullish_sectors": "A", "bearish_sectors": "B",
                    "cta_question": "CTA?", "cta_body": "body", "cta_channel": "@test",
                    "source_label": "src", "caption": "", "hashtags": []}
            with tempfile.TemporaryDirectory() as td:
                slides = render_carousel_slides_pillow(mock, _P(td))
                result["slides_rendered"] = len(slides)
        except Exception as e:
            result["render_error"] = str(e)
            result["render_traceback"] = _tb.format_exc()
    except ImportError as e:
        result["errors"].append(f"Pillow import failed: {e}")
    return result


@app.post("/instagram/publish-sync", summary="Pubblica carosello Instagram (sincrono, mostra errori)")
async def instagram_publish_sync(dry_run: bool = True):
    """
    Versione sincrona di /instagram/publish: aspetta il risultato e lo restituisce
    direttamente nella response. Utile per debug. Default dry_run=True per sicurezza.
    """
    if not _instagram_available:
        return {"status": "NOT_CONFIGURED"}
    try:
        result = await _publish_instagram_carousel_sync(dry_run)
        return result
    except Exception as e:
        import traceback as _tb
        return {"status": "EXCEPTION", "error": str(e), "traceback": _tb.format_exc()}


@app.get("/instagram/status", summary="Stato Instagram integration")
async def instagram_status():
    """
    Stato dell'integrazione Instagram: credenziali, info account, commenti risposti.
    """
    import traceback as _tb
    try:
        status: dict = {
            "configured": _instagram_available,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "last_post_id": _ig_last_post_id,
            "last_post_at": _ig_last_post_at,
            "last_error": _ig_last_error,
        }

        if _instagram_available:
            try:
                from instagram_publisher import get_account_info
                account = await get_account_info()
                status["account"] = account
            except Exception as e:
                status["account_error"] = str(e)

            try:
                from comment_handler import _load_replied
                replied = _load_replied()
                status["comments_replied_total"] = len(replied)
            except Exception:
                pass
        else:
            status["message"] = (
                "Aggiungi IG_ACCESS_TOKEN e IG_BUSINESS_ACCOUNT_ID in .env per attivare."
            )

        return status
    except Exception as e:
        return {"error": str(e), "traceback": _tb.format_exc()}


@app.post("/instagram/comments", summary="Elabora commenti Instagram recenti")
async def instagram_process_comments(days: int = 1, dry_run: bool = True):
    """
    Processa i commenti degli ultimi N giorni e genera risposte con Claude Haiku.
    dry_run=True di default: non invia effettivamente le risposte.
    """
    if not _instagram_available:
        return {"status": "NOT_CONFIGURED", "message": "Instagram non configurato."}
    try:
        stats = await process_all_recent_posts(days=days, dry_run=dry_run)
        return {
            "status": "ok",
            "dry_run": dry_run,
            "days_processed": days,
            "stats": stats,
        }
    except Exception as e:
        logger.error(f"instagram_process_comments error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Avvio MacroSignalTool API su porta {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)


@app.post("/telegram/test", summary="Invia messaggio di test su Telegram")
async def telegram_test():
    """Verifica che il bot Telegram sia configurato e funzionante."""
    import os
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return {"status": "NOT_CONFIGURED", "token_set": bool(token), "chat_id_set": bool(chat_id)}
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # Prima verifica il bot con getMe
            async with session.get(f"https://api.telegram.org/bot{token}/getMe") as r:
                me = await r.json()
            if not me.get("ok"):
                return {"status": "INVALID_TOKEN", "detail": me}
            # Poi invia messaggio di test
            msg = {"chat_id": chat_id, "text": "✅ Kairós MacroSignal — test connessione Telegram OK", "parse_mode": "HTML"}
            async with session.post(f"https://api.telegram.org/bot{token}/sendMessage", json=msg) as r:
                result = await r.json()
            return {
                "status": "OK" if result.get("ok") else "ERROR",
                "bot_name": me.get("result", {}).get("username"),
                "chat_id": chat_id,
                "message_sent": result.get("ok"),
                "detail": result if not result.get("ok") else None,
            }
    except Exception as e:
        return {"status": "EXCEPTION", "error": str(e)}


@app.post("/instagram/publish-custom", summary="Pubblica carosello Instagram con contenuto custom")
async def instagram_publish_custom(content: dict, dry_run: bool = False):
    """
    Pubblica un carosello Instagram con contenuto completamente custom.
    Passa un JSON con i campi IGCarouselContent direttamente.
    """
    if not _instagram_available:
        return {"status": "NOT_CONFIGURED"}
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="kairos_ig_custom_") as tmpdir:
            slide_paths = render_carousel_slides(content, tmpdir)
            if not slide_paths:
                return {"status": "ERROR", "message": "Rendering slide fallito"}
            if dry_run:
                return {"status": "DRY_RUN", "slides_rendered": len(slide_paths)}
            caption = content.get("caption", "")
            hashtags = content.get("hashtags", [])
            if hashtags:
                caption = caption.rstrip() + "\n\n" + " ".join(f"#{h}" for h in hashtags)
            result = await publish_carousel(slide_paths, caption)
            if result and result.success:
                return {"status": "published", "post_id": result.post_id, "slides": len(slide_paths)}
            else:
                return {"status": "ERROR", "message": result.error if result else "unknown"}
    except Exception as e:
        import traceback as _tb
        return {"status": "EXCEPTION", "error": str(e), "traceback": _tb.format_exc()}
