"""
pead_pipeline.py
Kairós — Pipeline PEAD (Post-Earnings Announcement Drift)

Prende i segnali grezzi da pead_scanner.py, li dimensiona con half-Kelly
(stesso approccio della pipeline macro) e li prepara per paper_executor.py.

Differenze rispetto alla pipeline macro:
  - Max 3% NAV per posizione (vs 5% macro)
  - Hold period fisso 20-45 giorni (vs half_life_days variabile)
  - Stop loss 4%, target 8% (2:1 R/R calibrato su ricerca PEAD)
  - ID segnale K-PEAD-YYYY-NNNN (separato da K-YYYY-NNNN macro)
  - Win rate/avg_win calibrati su letteratura PEAD (non su playbook geopolitici)

Dipendenze: pead_scanner.py, position_sizer.py, paper_executor.py
"""

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent))
from pead_scanner import PEADSignal, ScanReport, load_signals_cache, scan_earnings

# ─── yfinance per VIX ─────────────────────────────────────────────────────────
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pead_pipeline")

# ─── Percorsi ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
_railway_data = Path("/data")
_data_root = _railway_data if _railway_data.exists() else BASE_DIR
DB_PATH = _data_root / "paper_trading.db"
PEAD_RESULTS_CACHE = _data_root / "pead_results_cache.json"

# ─── Configurazione sizing PEAD ───────────────────────────────────────────────
MAX_POSITION_PCT_PEAD  = 0.03   # 3% NAV
KELLY_FRACTION         = 0.5    # half-Kelly
MIN_KELLY_FOR_TRADE    = 0.015  # kelly < 1.5% → skip
VIX_REDUCE_THRESHOLD   = 30     # VIX > 30 → dimezza size
VIX_REDUCE_FACTOR      = 0.50
COMMISSION_PCT         = 0.001  # 0.1% per leg

# Win rate e R calibrati su ricerca PEAD accademica
# Fonte: Garfinkel, Hribar & Hsiao 2024 + Alpha Architect PEAD research
PEAD_STATS = {
    "base": {
        "win_rate": 0.62,       # 62% win rate su segnali SUE >= 2
        "avg_win_pct": 8.0,     # drift medio positivo su 30 giorni
        "avg_loss_pct": 4.5,    # perdita media quando il segnale fallisce
    },
    "macro_boost": {
        "win_rate": 0.70,       # boost se regime macro conferma
        "avg_win_pct": 9.5,
        "avg_loss_pct": 4.2,
    },
    "high_sue": {               # SUE >= 3 — segnali molto forti
        "win_rate": 0.67,
        "avg_win_pct": 10.0,
        "avg_loss_pct": 5.0,
    },
}


# ─── Data class output ────────────────────────────────────────────────────────

@dataclass
class PEADTradeReady:
    """Segnale PEAD dimensionato, pronto per paper_executor."""
    signal_id: str
    ticker: str
    company_name: str
    direction: str
    current_price: float
    position_size_eur: float
    position_size_pct: float
    kelly_fraction_used: float
    kelly_quality: str          # STRONG / MODERATE / WEAK
    stop_price: float
    target_price: float
    hold_days_target: int
    sue_score: float
    eps_surprise_pct: float
    earnings_date: str
    sector: str
    confidence_base: float
    macro_regime_boost: bool
    macro_regime_note: str
    win_rate_used: float
    avg_win_pct_used: float
    strategy: str = "PEAD"
    generated_at: str = ""


# ─── VIX live ─────────────────────────────────────────────────────────────────

def _get_vix() -> float:
    """Scarica VIX corrente. Ritorna 20 (neutro) se non disponibile."""
    if not YFINANCE_AVAILABLE:
        return 20.0
    try:
        vix_data = yf.Ticker("^VIX").fast_info
        vix = float(vix_data.get("lastPrice", 20.0))
        return vix if vix > 0 else 20.0
    except Exception:
        return 20.0


# ─── Sizing Kelly per PEAD ────────────────────────────────────────────────────

def _size_pead_trade(signal: PEADSignal, nav_eur: float, vix: float) -> Optional[PEADTradeReady]:
    """
    Calcola il position sizing per un segnale PEAD con half-Kelly.
    Usa statistiche calibrate sulla ricerca PEAD (non su playbook geopolitici).
    """
    # Scegli statistiche appropriate
    if signal.macro_regime_boost:
        stats = PEAD_STATS["macro_boost"]
    elif abs(signal.sue_score) >= 3.0:
        stats = PEAD_STATS["high_sue"]
    else:
        stats = PEAD_STATS["base"]

    W = stats["win_rate"]
    avg_win = stats["avg_win_pct"] / 100
    avg_loss = stats["avg_loss_pct"] / 100
    R = avg_win / avg_loss if avg_loss > 0 else 1.0
    L = 1 - W

    # Kelly formula: f = (W*R - L) / R
    kelly = (W * R - L) / R
    half_kelly = kelly * KELLY_FRACTION

    if half_kelly < MIN_KELLY_FOR_TRADE:
        logger.info(f"  {signal.ticker}: Kelly {half_kelly:.3f} < {MIN_KELLY_FOR_TRADE} → skip")
        return None

    # Cap al massimo configurato
    position_pct = min(half_kelly, MAX_POSITION_PCT_PEAD)

    # Riduzione VIX
    if vix > VIX_REDUCE_THRESHOLD:
        position_pct *= VIX_REDUCE_FACTOR
        logger.info(f"  {signal.ticker}: VIX {vix:.0f} > {VIX_REDUCE_THRESHOLD} → size ridotta")

    # Commissioni simulate
    position_pct = max(0, position_pct - COMMISSION_PCT * 2)

    position_eur = nav_eur * position_pct

    # Kelly quality label
    if half_kelly >= MAX_POSITION_PCT_PEAD:
        kelly_quality = "STRONG"
    elif half_kelly >= MAX_POSITION_PCT_PEAD * 0.6:
        kelly_quality = "MODERATE"
    else:
        kelly_quality = "WEAK"

    # Stop e target calcolati sul close del giorno degli earnings (T+0),
    # non sul prezzo live al momento dello scan (che può essere giorni dopo).
    # Questo garantisce che stop/target siano coerenti con l'entry di T+1.
    price = signal.current_price
    earnings_close = price  # fallback: usa prezzo live se storico non disponibile
    if YFINANCE_AVAILABLE:
        try:
            from datetime import date as _date, timedelta as _td
            import yfinance as _yf
            t0 = signal.earnings_date  # stringa "YYYY-MM-DD"
            t0_dt = _date.fromisoformat(t0)
            t1_dt = t0_dt + _td(days=1)
            hist = _yf.download(
                signal.ticker,
                start=t0_dt.strftime("%Y-%m-%d"),
                end=t1_dt.strftime("%Y-%m-%d"),
                auto_adjust=True,
                progress=False,
            )
            if not hist.empty:
                if hasattr(hist.columns, "get_level_values"):
                    hist.columns = hist.columns.get_level_values(0)
                earnings_close = float(hist["Close"].iloc[-1])
                logger.debug(f"  {signal.ticker}: close T+0 = {earnings_close:.3f} (live = {price:.3f})")
        except Exception as e:
            logger.debug(f"  {signal.ticker}: errore fetch close T+0 — uso prezzo live ({e})")

    ref_price = earnings_close
    if signal.direction == "LONG":
        stop_price   = round(ref_price * (1 - signal.stop_loss_pct / 100), 4)
        target_price = round(ref_price * (1 + signal.target_pct / 100), 4)
    else:
        stop_price   = round(ref_price * (1 + signal.stop_loss_pct / 100), 4)
        target_price = round(ref_price * (1 - signal.target_pct / 100), 4)

    return PEADTradeReady(
        signal_id=signal.signal_id,
        ticker=signal.ticker,
        company_name=signal.company_name,
        direction=signal.direction,
        current_price=price,
        position_size_eur=round(position_eur, 2),
        position_size_pct=round(position_pct * 100, 2),
        kelly_fraction_used=round(half_kelly, 4),
        kelly_quality=kelly_quality,
        stop_price=stop_price,
        target_price=target_price,
        hold_days_target=signal.hold_days_target,
        sue_score=signal.sue_score,
        eps_surprise_pct=signal.eps_surprise_pct,
        earnings_date=signal.earnings_date,
        sector=signal.sector,
        confidence_base=signal.confidence_base,
        macro_regime_boost=signal.macro_regime_boost,
        macro_regime_note=signal.macro_regime_note,
        win_rate_used=W,
        avg_win_pct_used=stats["avg_win_pct"],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


# ─── Pipeline principale ──────────────────────────────────────────────────────

def run_pead_pipeline(
    nav_eur: float = 10000.0,
    lookback_days: int = 45,
    save_results: bool = True,
) -> tuple[list[PEADTradeReady], ScanReport]:
    """
    Esegue la pipeline PEAD completa:
      1. Scansiona earnings recenti (via pead_scanner)
      2. Dimensiona le posizioni con half-Kelly
      3. Salva i risultati nella cache

    Args:
        nav_eur: NAV corrente del portafoglio in EUR
        lookback_days: quanti giorni indietro cercare earnings
        save_results: se salvare in cache

    Returns:
        Tupla (lista PEADTradeReady pronti per esecuzione, ScanReport diagnostico).
        Il ScanReport è sempre popolato — anche quando la lista è vuota —
        per confermare che il programma ha girato correttamente.
    """
    logger.info(f"=== PEAD Pipeline — NAV €{nav_eur:.0f} ===")

    # Step 1: scan earnings
    raw_signals, scan_report = scan_earnings(lookback_days=lookback_days, save_cache=True)

    if not raw_signals:
        logger.info("Nessun segnale PEAD trovato dallo scanner.")
        return [], scan_report

    # Step 2: ottieni NAV reale dal DB se possibile
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT nav FROM nav_history ORDER BY timestamp_utc DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            nav_eur = float(row[0])
            logger.info(f"NAV dal DB: €{nav_eur:.2f}")
    except Exception:
        pass

    # Step 3: VIX corrente
    vix = _get_vix()
    logger.info(f"VIX corrente: {vix:.1f}")

    # Step 4: dimensiona ogni segnale
    results: list[PEADTradeReady] = []
    for signal in raw_signals:
        trade = _size_pead_trade(signal, nav_eur, vix)
        if trade is None:
            continue
        results.append(trade)
        logger.info(
            f"  [{trade.signal_id}] {trade.ticker} {trade.direction} "
            f"€{trade.position_size_eur:.0f} ({trade.position_size_pct:.1f}%) "
            f"| kelly={trade.kelly_quality} | stop={trade.stop_price:.3f} | target={trade.target_price:.3f}"
        )

    logger.info(f"\n  Segnali pronti: {len(results)} / {len(raw_signals)} scansionati")

    # Step 5: salva cache
    if save_results and results:
        _save_results_cache(results)

    return results, scan_report


def get_latest_pead_signals() -> list[dict]:
    """Carica i segnali PEAD più recenti dalla cache. Usato da main.py."""
    try:
        if not PEAD_RESULTS_CACHE.exists():
            return []
        data = json.loads(PEAD_RESULTS_CACHE.read_text())
        return data.get("signals", [])
    except Exception:
        return []


def _save_results_cache(results: list[PEADTradeReady]) -> None:
    try:
        existing = get_latest_pead_signals()
        existing_ids = {s.get("signal_id") for s in existing}
        new = [asdict(r) for r in results if r.signal_id not in existing_ids]
        all_signals = existing + new

        PEAD_RESULTS_CACHE.write_text(json.dumps({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(all_signals),
            "signals": all_signals,
        }, indent=2, ensure_ascii=False))
        logger.info(f"Cache risultati PEAD: {len(new)} nuovi, {len(all_signals)} totali")
    except Exception as e:
        logger.error(f"Errore salvataggio cache risultati PEAD: {e}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results, report = run_pead_pipeline(lookback_days=3)
    print(f"\nSegnali pronti per esecuzione: {len(results)}")
    for r in results:
        print(f"\n  [{r.signal_id}] {r.ticker} {r.direction}")
        print(f"  Size: €{r.position_size_eur:.0f} ({r.position_size_pct:.1f}%) | Kelly: {r.kelly_quality}")
        print(f"  Entry: {r.current_price:.3f} | Stop: {r.stop_price:.3f} | Target: {r.target_price:.3f}")
        print(f"  SUE: {r.sue_score:.2f} | EPS surprise: {r.eps_surprise_pct:+.1f}%")
        print(f"  Hold: {r.hold_days_target}d | Confidence: {r.confidence_base:.2f}")
    if not results:
        print(f"\n  Scan completato: {report.total_scanned} ticker analizzati, "
              f"{report.with_recent_earnings} con earnings recenti, 0 segnali generati.")
        for tr in report.ticker_results:
            print(f"  {tr.ticker} ({tr.earnings_date}): {tr.fail_reason}")