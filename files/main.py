"""
main.py
Phase 4+6 — MacroSignalTool — FastAPI Orchestratore

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
        scheduler.add_job(
            _scheduled_news_fetch,
            "interval",
            hours=4,
            id="news_fetch",
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
        scheduler.start()
        logger.info("APScheduler avviato: news ogni 4h, prezzi ogni 15min ✅")
    except ImportError:
        logger.warning("APScheduler non installato — polling automatico disabilitato")
    except Exception as e:
        logger.error(f"Errore avvio scheduler: {e} — continuo comunque")


async def _scheduled_news_fetch():
    """Task schedulato: fetch + classifica news."""
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


def _fetch_and_classify_sync():
    """Fetch + classifica news (sincrono, usato in thread pool)."""
    # run_ingestion scarica tutti i feed RSS, deduplica e salva in SQLite
    new_items = run_ingestion()
    logger.info(f"Fetch completato: {len(new_items)} nuove news")
    # run_classification_batch legge le non-classificate dal DB, chiama Claude API e aggiorna DB
    classified = run_classification_batch(limit=20)
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
        return {
            "db_path": str(db_path),
            "total_news": total,
            "classified": classified_count,
            "unclassified": unclassified_count,
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
                        asyncio.run(_telegram.send_signal_alert(signal_for_alert))
                    except Exception:
                        pass

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


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Avvio MacroSignalTool API su porta {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
