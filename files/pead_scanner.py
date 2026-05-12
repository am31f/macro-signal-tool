"""
pead_scanner.py
Kairós — Modulo PEAD (Post-Earnings Announcement Drift)

Strategia parallela alla pipeline macro. Opera su eventi earnings invece
che su notizie geopolitiche/macro. I segnali sono identificati con
ID separato: K-PEAD-YYYY-NNNN.

Flusso:
  1. Scarica calendario earnings prossimi 7 giorni (USA + Europa) via yfinance
  2. Per ogni titolo con earnings recenti (ultimi 2 giorni), calcola SUE
  3. Applica 3 filtri:
       F1 — SUE >= 2.0 (sorpresa significativa, >= 2 deviazioni standard)
       F2 — Market cap < $100B (large cap troppo efficienti, PEAD arbitrato via)
       F3 — Macro regime coerente con Kairós (opzionale ma aumenta conviction)
  4. Genera PEADSignal con ID K-PEAD-YYYY-NNNN, direction, SUE score

Metriche PEAD:
  SUE = (EPS_effettivo - EPS_atteso) / std_sorprese_storiche
  Se std non disponibile: usa |EPS_atteso| * 0.15 come proxy (15% volatility)
  Sorpresa positiva → LONG | Sorpresa negativa → SHORT
  Hold period: 20-45 giorni (calibrato su ricerca accademica)
  Stop loss: 4% dal prezzo di entrata

Mercati coperti:
  USA: S&P 500 + Nasdaq mid/small cap
  Europa: FTSE, DAX, CAC (titoli con earnings su Yahoo Finance)

Dipendenze: yfinance, pandas, anthropic (opzionale per macro regime check)
Testabile: python pead_scanner.py --test
"""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Optional
import sys

# ─── yfinance ─────────────────────────────────────────────────────────────────
try:
    import yfinance as yf
    import pandas as pd
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pead_scanner")

# ─── Percorsi ─────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
_railway_data = Path("/data")
_data_root = _railway_data if _railway_data.exists() else BASE_DIR
PEAD_CACHE_PATH = _data_root / "pead_signals_cache.json"
DB_PATH = _data_root / "paper_trading.db"

# ─── Configurazione ───────────────────────────────────────────────────────────
SUE_THRESHOLD          = 2.0    # soglia minima SUE per considerare il segnale
MAX_MARKET_CAP_USD     = 100e9  # $100B — escludi mega cap (troppo efficienti)
MIN_MARKET_CAP_USD     = 500e6  # $500M — escludi micro cap (illiquidità eccessiva)
HOLD_DAYS_DEFAULT      = 30     # giorni di hold target
HOLD_DAYS_MIN          = 20     # hold minimo
HOLD_DAYS_MAX          = 45     # hold massimo
STOP_LOSS_PCT          = 4.0    # stop loss 4%
TARGET_PCT             = 8.0    # target 8% (2:1 R/R)
MAX_POSITION_PCT       = 0.03   # 3% NAV max per posizione PEAD
SUE_STD_PROXY_FACTOR   = 0.15   # proxy std se storico non disponibile

# ─── Universo titoli da monitorare ────────────────────────────────────────────
# USA: mix S&P 500 mid cap + settori ciclici (dove PEAD è più forte)
# Europa: principali titoli con earnings su Yahoo Finance
USA_WATCHLIST = [
    # Technology mid cap
    "ANET", "NTAP", "FFIV", "JNPR", "ZBRA", "TTWO", "EA", "RBLX",
    # Consumer discretionary
    "RCL", "CCL", "NCLH", "MGM", "LVS", "WYNN", "HLT", "MAR",
    # Energy
    "DVN", "FANG", "MRO", "APA", "OVV", "SM", "MTDR",
    # Financials
    "RF", "CFG", "FITB", "HBAN", "KEY", "ZION", "CMA",
    # Industrials
    "GXO", "XPO", "SAIA", "ODFL", "WERN", "JBHT",
    # Healthcare
    "JAZZ", "NBIX", "INCY", "ALNY", "BMRN",
    # Materials
    "CF", "MOS", "NEM", "AEM", "GOLD", "HL",
    # Retail
    "DKS", "BBY", "ULTA", "FIVE", "RH",
    # Semis mid cap
    "MCHP", "SWKS", "QRVO", "CRUS", "ONTO",
]

EUROPE_WATCHLIST = [
    # Germania (DAX/MDAX)
    "MBG.DE", "BMW.DE", "VOW3.DE", "BAS.DE", "BAYN.DE",
    "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "MUV2.DE",
    # Francia (CAC)
    "MC.PA", "OR.PA", "SAN.PA", "BNP.PA", "ACA.PA",
    "ENGI.PA", "TTE.PA", "AIR.PA", "RI.PA",
    # UK (FTSE)
    "SHEL.L", "AZN.L", "HSBA.L", "BP.L", "LLOY.L",
    "VOD.L", "GSK.L", "RIO.L", "AAL.L",
    # Italia
    "ENI.MI", "ENEL.MI", "ISP.MI", "UCG.MI", "STM.MI",
    # Spagna
    "SAN.MC", "BBVA.MC", "ITX.MC", "IBE.MC",
]

FULL_WATCHLIST = USA_WATCHLIST + EUROPE_WATCHLIST


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class EarningsSurprise:
    """Dati grezzi da yfinance per un singolo earnings."""
    ticker: str
    earnings_date: str
    eps_actual: float
    eps_estimate: float
    eps_surprise_abs: float       # actual - estimate
    eps_surprise_pct: float       # (actual - estimate) / |estimate| * 100
    sue_score: float              # standardizzato su std storica
    quarters_history: int         # quanti trimestri di storico disponibili


@dataclass
class PEADSignal:
    """Segnale PEAD completo, pronto per pead_pipeline.py."""
    signal_id: str                # K-PEAD-YYYY-NNNN
    ticker: str
    company_name: str
    earnings_date: str
    direction: str                # LONG o SHORT
    sue_score: float
    eps_actual: float
    eps_estimate: float
    eps_surprise_pct: float
    market_cap_usd: float
    sector: str
    current_price: float
    stop_loss_pct: float          = STOP_LOSS_PCT
    target_pct: float             = TARGET_PCT
    hold_days_target: int         = HOLD_DAYS_DEFAULT
    macro_regime_boost: bool      = False   # True se regime macro Kairós conferma
    macro_regime_note: str        = ""
    confidence_base: float        = 0.0    # calcolata da SUE e filtri
    generated_at: str             = ""
    strategy: str                 = "PEAD"
    # Filtri superati
    f1_sue_passed: bool           = False
    f2_market_cap_passed: bool    = False
    f3_macro_passed: bool         = False


# ─── Contatore ID segnali ─────────────────────────────────────────────────────

def _next_pead_id() -> str:
    """Genera ID progressivo K-PEAD-YYYY-NNNN leggendo la cache esistente."""
    year = datetime.now().year
    try:
        if PEAD_CACHE_PATH.exists():
            data = json.loads(PEAD_CACHE_PATH.read_text())
            existing = data.get("signals", [])
            # Conta quanti segnali dell'anno corrente esistono
            prefix = f"K-PEAD-{year}-"
            count = sum(1 for s in existing if s.get("signal_id", "").startswith(prefix))
            return f"K-PEAD-{year}-{count + 1:04d}"
    except Exception:
        pass
    return f"K-PEAD-{year}-0001"


# ─── Calcolo SUE ─────────────────────────────────────────────────────────────

def _calculate_sue(ticker: str) -> Optional[EarningsSurprise]:
    """
    Scarica storico earnings da yfinance e calcola SUE.
    Ritorna None se dati insufficienti o errore.
    """
    if not YFINANCE_AVAILABLE:
        return None

    try:
        t = yf.Ticker(ticker)
        earnings_hist = t.earnings_history  # DataFrame con colonne: epsActual, epsEstimate, surprisePercent

        if earnings_hist is None or earnings_hist.empty:
            return None

        # Ordina per data decrescente
        df = earnings_hist.sort_index(ascending=False)

        # Serve almeno 1 earnings recente con actual E estimate
        latest = df.iloc[0]
        eps_actual   = float(latest.get("epsActual", float("nan")))
        eps_estimate = float(latest.get("epsEstimate", float("nan")))

        if pd.isna(eps_actual) or pd.isna(eps_estimate) or eps_estimate == 0:
            return None

        eps_surprise_abs = eps_actual - eps_estimate
        eps_surprise_pct = (eps_surprise_abs / abs(eps_estimate)) * 100

        # Calcola std delle sorprese storiche (ultimi 8 trimestri se disponibili)
        history_rows = min(len(df), 8)
        if history_rows >= 3:
            surprises = []
            for i in range(1, history_rows):  # salta il più recente (già usato)
                row = df.iloc[i]
                a = float(row.get("epsActual", float("nan")))
                e = float(row.get("epsEstimate", float("nan")))
                if not pd.isna(a) and not pd.isna(e) and e != 0:
                    surprises.append(a - e)
            if len(surprises) >= 2:
                import statistics
                std_surprises = statistics.stdev(surprises)
            else:
                std_surprises = abs(eps_estimate) * SUE_STD_PROXY_FACTOR
        else:
            std_surprises = abs(eps_estimate) * SUE_STD_PROXY_FACTOR

        if std_surprises <= 0:
            std_surprises = abs(eps_estimate) * SUE_STD_PROXY_FACTOR or 0.01

        sue = eps_surprise_abs / std_surprises

        # Data dell'earnings più recente
        try:
            earnings_date_str = str(df.index[0].date())
        except Exception:
            earnings_date_str = str(date.today())

        return EarningsSurprise(
            ticker=ticker,
            earnings_date=earnings_date_str,
            eps_actual=eps_actual,
            eps_estimate=eps_estimate,
            eps_surprise_abs=eps_surprise_abs,
            eps_surprise_pct=eps_surprise_pct,
            sue_score=sue,
            quarters_history=history_rows,
        )

    except Exception as e:
        logger.debug(f"Errore SUE per {ticker}: {e}")
        return None


# ─── Info ticker (market cap, nome, settore, prezzo) ─────────────────────────

def _get_ticker_info(ticker: str) -> Optional[dict]:
    """Scarica info base del ticker. Ritorna dict o None."""
    if not YFINANCE_AVAILABLE:
        return None
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not info:
            return None
        return {
            "company_name": info.get("longName") or info.get("shortName") or ticker,
            "market_cap":   info.get("marketCap") or 0,
            "sector":       info.get("sector") or "Unknown",
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice") or 0,
        }
    except Exception as e:
        logger.debug(f"Errore info per {ticker}: {e}")
        return None


# ─── Filtro regime macro (integrazione con Kairós) ───────────────────────────

def _check_macro_regime_boost(direction: str, sector: str) -> tuple[bool, str]:
    """
    Verifica se il regime macro attuale di Kairós è coerente con il segnale PEAD.
    Legge signals_cache.json per capire il regime corrente.
    Ritorna (boost: bool, note: str).
    """
    try:
        cache_path = _data_root / "signals_cache.json"
        if not cache_path.exists():
            return False, "cache macro non disponibile"

        data = json.loads(cache_path.read_text())
        signals = data.get("signals", data) if isinstance(data, dict) else data

        if not signals:
            return False, "nessun segnale macro attivo"

        # Prendi il regime dal segnale macro più recente con confidence più alta
        top = max(signals, key=lambda s: s.get("confidence_composite", s.get("confidence", 0)))
        regime = top.get("macro_regime", "MIXED")

        # Logica di boost: segnali LONG su energia/materials in INFLATIONARY_SHOCK
        energy_sectors = {"Energy", "Basic Materials", "Materials", "Utilities"}
        defensive_sectors = {"Consumer Defensive", "Healthcare", "Utilities"}

        if regime == "INFLATIONARY_SHOCK":
            if direction == "LONG" and sector in energy_sectors:
                return True, f"regime INFLATIONARY_SHOCK conferma LONG su {sector}"
            if direction == "SHORT" and sector in defensive_sectors:
                return True, f"regime INFLATIONARY_SHOCK suggerisce pressione su {sector}"

        elif regime == "RISK_OFF":
            if direction == "LONG" and sector in defensive_sectors:
                return True, f"regime RISK_OFF favorisce LONG su {sector}"
            if direction == "SHORT" and sector in {"Technology", "Consumer Cyclical", "Financial Services"}:
                return True, f"regime RISK_OFF conferma SHORT su {sector}"

        elif regime == "RISK_ON":
            if direction == "LONG" and sector in {"Technology", "Consumer Cyclical", "Industrials"}:
                return True, f"regime RISK_ON conferma LONG su {sector}"

        return False, f"regime {regime} neutro per {direction} {sector}"

    except Exception as e:
        return False, f"errore check macro: {e}"


# ─── Calcolo confidence base ──────────────────────────────────────────────────

def _calc_confidence(sue: float, quarters_history: int, macro_boost: bool) -> float:
    """
    Calcola confidence base del segnale PEAD su scala 0-1.
    Basata su: intensità SUE, storico disponibile, conferma macro.
    """
    # Base da SUE: 2.0 → 0.45, 3.0 → 0.60, 4.0+ → 0.75
    sue_abs = abs(sue)
    if sue_abs >= 4.0:
        base = 0.75
    elif sue_abs >= 3.0:
        base = 0.60
    elif sue_abs >= 2.5:
        base = 0.52
    else:
        base = 0.45

    # Bonus storico: più trimestri → più affidabile la std
    history_bonus = min(0.05, quarters_history * 0.007)

    # Bonus macro conferma
    macro_bonus = 0.08 if macro_boost else 0.0

    return min(0.90, base + history_bonus + macro_bonus)


# ─── Scanner principale ───────────────────────────────────────────────────────

def scan_earnings(
    lookback_days: int = 2,
    watchlist: Optional[list] = None,
    save_cache: bool = True,
) -> list[PEADSignal]:
    """
    Scansiona i titoli del watchlist alla ricerca di sorprese earnings
    negli ultimi `lookback_days` giorni.

    Args:
        lookback_days: quanti giorni indietro guardare per earnings recenti
        watchlist: lista ticker da analizzare (default: FULL_WATCHLIST)
        save_cache: se salvare i segnali trovati in pead_signals_cache.json

    Returns:
        Lista di PEADSignal che hanno superato tutti i filtri
    """
    if not YFINANCE_AVAILABLE:
        logger.error("yfinance non installato. Installa con: pip install yfinance")
        return []

    if watchlist is None:
        watchlist = FULL_WATCHLIST

    cutoff_date = date.today() - timedelta(days=lookback_days)
    signals: list[PEADSignal] = []
    scanned = 0
    skipped_no_data = 0
    skipped_f1 = 0
    skipped_f2 = 0

    logger.info(f"=== PEAD Scanner: {len(watchlist)} titoli, lookback={lookback_days}d ===")

    for ticker in watchlist:
        scanned += 1
        time.sleep(0.3)  # rate limit yfinance

        # ── Calcola SUE ───────────────────────────────────────────────────────
        surprise = _calculate_sue(ticker)
        if surprise is None:
            skipped_no_data += 1
            continue

        # Controlla che l'earnings sia recente (entro lookback_days)
        try:
            earnings_dt = date.fromisoformat(surprise.earnings_date)
        except Exception:
            skipped_no_data += 1
            continue

        if earnings_dt < cutoff_date:
            # Earnings troppo vecchio
            continue

        # ── F1: SUE >= soglia ─────────────────────────────────────────────────
        if abs(surprise.sue_score) < SUE_THRESHOLD:
            skipped_f1 += 1
            logger.debug(f"  {ticker}: F1 FAIL — SUE={surprise.sue_score:.2f} < {SUE_THRESHOLD}")
            continue

        # ── Info ticker (market cap, nome, settore, prezzo) ───────────────────
        info = _get_ticker_info(ticker)
        if info is None:
            skipped_no_data += 1
            continue

        market_cap = info.get("market_cap", 0)
        current_price = info.get("current_price", 0)

        if current_price <= 0:
            skipped_no_data += 1
            continue

        # ── F2: Market cap range accettabile ─────────────────────────────────
        if market_cap > MAX_MARKET_CAP_USD:
            skipped_f2 += 1
            logger.debug(f"  {ticker}: F2 FAIL — market cap ${market_cap/1e9:.1f}B > $100B")
            continue
        if market_cap < MIN_MARKET_CAP_USD and market_cap > 0:
            skipped_f2 += 1
            logger.debug(f"  {ticker}: F2 FAIL — market cap ${market_cap/1e6:.0f}M < $500M")
            continue

        # ── Direzione ─────────────────────────────────────────────────────────
        direction = "LONG" if surprise.sue_score > 0 else "SHORT"

        # ── F3: Check regime macro (boost, non blocco) ────────────────────────
        sector = info.get("sector", "Unknown")
        macro_boost, macro_note = _check_macro_regime_boost(direction, sector)

        # ── Confidence ────────────────────────────────────────────────────────
        confidence = _calc_confidence(
            sue=surprise.sue_score,
            quarters_history=surprise.quarters_history,
            macro_boost=macro_boost,
        )

        # ── Genera segnale ────────────────────────────────────────────────────
        signal_id = _next_pead_id()

        signal = PEADSignal(
            signal_id=signal_id,
            ticker=ticker,
            company_name=info.get("company_name", ticker),
            earnings_date=surprise.earnings_date,
            direction=direction,
            sue_score=round(surprise.sue_score, 3),
            eps_actual=surprise.eps_actual,
            eps_estimate=surprise.eps_estimate,
            eps_surprise_pct=round(surprise.eps_surprise_pct, 2),
            market_cap_usd=market_cap,
            sector=sector,
            current_price=current_price,
            stop_loss_pct=STOP_LOSS_PCT,
            target_pct=TARGET_PCT,
            hold_days_target=HOLD_DAYS_DEFAULT,
            macro_regime_boost=macro_boost,
            macro_regime_note=macro_note,
            confidence_base=round(confidence, 3),
            generated_at=datetime.now(timezone.utc).isoformat(),
            f1_sue_passed=True,
            f2_market_cap_passed=True,
            f3_macro_passed=macro_boost,
        )
        signals.append(signal)

        logger.info(
            f"  ✦ {ticker} [{direction}] SUE={surprise.sue_score:.2f} "
            f"EPS {surprise.eps_surprise_pct:+.1f}% "
            f"| cap=${market_cap/1e9:.1f}B | conf={confidence:.2f}"
            + (" ← macro boost" if macro_boost else "")
        )

    logger.info(f"\n=== Risultati PEAD scan ===")
    logger.info(f"  Scansionati:       {scanned}")
    logger.info(f"  No dati:           {skipped_no_data}")
    logger.info(f"  F1 fail (SUE):     {skipped_f1}")
    logger.info(f"  F2 fail (mktcap):  {skipped_f2}")
    logger.info(f"  Segnali trovati:   {len(signals)}")

    if save_cache and signals:
        _save_signals_cache(signals)

    return signals


# ─── Calendario earnings prossimi 7 giorni ────────────────────────────────────

def get_upcoming_earnings(days_ahead: int = 7) -> list[dict]:
    """
    Ritorna il calendario earnings dei prossimi `days_ahead` giorni
    per i titoli del watchlist. Usato dalla dashboard.
    """
    if not YFINANCE_AVAILABLE:
        return []

    upcoming = []
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    for ticker in FULL_WATCHLIST[:60]:  # limita a 60 per velocità
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None:
                continue

            # calendar può essere dict o DataFrame
            if hasattr(cal, "to_dict"):
                cal = cal.to_dict()

            earnings_date = cal.get("Earnings Date")
            if earnings_date is None:
                continue

            # Normalizza a date
            if hasattr(earnings_date, "__iter__") and not isinstance(earnings_date, str):
                earnings_date = list(earnings_date)[0] if earnings_date else None
            if earnings_date is None:
                continue

            if hasattr(earnings_date, "date"):
                earnings_date = earnings_date.date()
            elif isinstance(earnings_date, str):
                earnings_date = date.fromisoformat(earnings_date[:10])

            if today <= earnings_date <= cutoff:
                upcoming.append({
                    "ticker": ticker,
                    "earnings_date": str(earnings_date),
                    "days_until": (earnings_date - today).days,
                })
            time.sleep(0.1)
        except Exception:
            continue

    return sorted(upcoming, key=lambda x: x["earnings_date"])


# ─── Cache ────────────────────────────────────────────────────────────────────

def _save_signals_cache(signals: list[PEADSignal]) -> None:
    """Salva/aggiorna la cache dei segnali PEAD."""
    try:
        existing = []
        if PEAD_CACHE_PATH.exists():
            data = json.loads(PEAD_CACHE_PATH.read_text())
            existing = data.get("signals", [])

        # Evita duplicati per stesso ticker+earnings_date
        existing_keys = {(s.get("ticker"), s.get("earnings_date")) for s in existing}
        new_signals = [
            asdict(s) for s in signals
            if (s.ticker, s.earnings_date) not in existing_keys
        ]

        all_signals = existing + new_signals

        # Mantieni solo ultimi 90 giorni
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        all_signals = [s for s in all_signals if s.get("generated_at", "") >= cutoff]

        PEAD_CACHE_PATH.write_text(json.dumps({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(all_signals),
            "signals": all_signals,
        }, indent=2, ensure_ascii=False))

        logger.info(f"Cache PEAD aggiornata: {len(new_signals)} nuovi, {len(all_signals)} totali")
    except Exception as e:
        logger.error(f"Errore salvataggio cache PEAD: {e}")


def load_signals_cache() -> list[dict]:
    """Carica segnali dalla cache. Usato da pead_pipeline e dashboard."""
    try:
        if not PEAD_CACHE_PATH.exists():
            return []
        data = json.loads(PEAD_CACHE_PATH.read_text())
        return data.get("signals", [])
    except Exception:
        return []


# ─── CLI / test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        print("\n=== TEST PEAD Scanner ===\n")
        print("Test con 5 titoli USA...\n")
        test_list = ["ANET", "DVN", "RF", "MGM", "MCHP"]
        results = scan_earnings(lookback_days=7, watchlist=test_list, save_cache=False)

        if results:
            for s in results:
                print(f"  [{s.signal_id}] {s.ticker} {s.direction}")
                print(f"    SUE={s.sue_score:.2f} | EPS surprise={s.eps_surprise_pct:+.1f}%")
                print(f"    Settore: {s.sector} | Cap: ${s.market_cap_usd/1e9:.1f}B")
                print(f"    Confidence: {s.confidence_base:.2f} | Macro boost: {s.macro_regime_boost}")
                print()
        else:
            print("  Nessun segnale PEAD trovato (normale se non ci sono earnings recenti)")

        print("Test calendario prossimi earnings...")
        upcoming = get_upcoming_earnings(days_ahead=7)
        print(f"  Earnings prossimi 7 giorni: {len(upcoming)}")
        for u in upcoming[:5]:
            print(f"    {u['ticker']}: {u['earnings_date']} (tra {u['days_until']} giorni)")

    else:
        # Scan completo
        signals = scan_earnings(lookback_days=2)
        print(f"\nSegnali trovati: {len(signals)}")
        for s in signals:
            print(f"  [{s.signal_id}] {s.ticker} {s.direction} SUE={s.sue_score:.2f} conf={s.confidence_base:.2f}")
