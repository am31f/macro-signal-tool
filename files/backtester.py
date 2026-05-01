"""
backtester.py — MacroSignalTool Phase 7.1
==========================================
Backtesting della strategia su 5 eventi storici con prezzi reali yfinance.

Per ogni evento:
  1. Scarica prezzi storici (event_date - 30gg → event_date + 60gg)
  2. Simula entry a T+1 (chiusura del giorno successivo all'evento)
  3. Simula exit quando si raggiunge il target o lo stop
  4. Calcola P&L, win_rate, return%, confronto vs SPY

Eventi testati:
  - Gulf War 1990-08-02 (Iraq invade Kuwait)
  - 9/11 2001-09-11 (Attentati USA)
  - Ukraine 2022-02-24 (Russia invade Ucraina)
  - Iran strikes 2024-04-13 (Iran attacca Israele con droni/missili)
  - Iran escalation 2026-02-28 (Operation Epic Fury — da asset_map.json)

Utilizzo:
    python backtester.py               # esegui tutti gli eventi
    python backtester.py --event 9_11  # esegui un solo evento
    python backtester.py --summary     # mostra solo il riepilogo finale

Output:
    files/backtest_results.json        # risultati completi
"""

import json
import logging
import os
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Costanti ──────────────────────────────────────────────────────────────────
DATA_DIR     = Path(__file__).parent
ASSET_MAP    = DATA_DIR / "asset_map.json"
RESULTS_FILE = DATA_DIR / "backtest_results.json"
INITIAL_NAV  = 10_000.0
MAX_POS_PCT  = 0.05          # 5% NAV per posizione
COMMISSION   = 0.001         # 0.1% per leg
LOOKBACK_DAYS = 30           # giorni pre-evento per context
HOLDOUT_DAYS  = 60           # giorni post-evento per exit

# ── Definizione eventi storici ────────────────────────────────────────────────
HISTORICAL_EVENTS = [
    {
        "id": "GULF_WAR_1990",
        "label": "Gulf War — Iraq invade Kuwait",
        "date": "1990-08-02",
        "category": "MILITARY_CONFLICT",
        "description": "Iraq invades Kuwait. Brent +100% in 2 months. S&P -16%.",
        "trades": [
            # (ticker, direction, stop_pct, target_pct, conviction)
            # Stop e target calibrati sui playbook istituzionali
            ("XLE",  "LONG",  -8.0,  18.0, 0.75),
            ("GLD",  "LONG",  -5.0,   9.0, 0.65),
            ("DAL",  "SHORT",  8.0, -15.0, 0.70),
        ],
        "benchmark": "SPY",
        "note": "Desert Storm reversal: il 17 gen 1991 al lancio del bombardamento il petrolio crolla -33%. Nel backtest limitato a +60gg dall'invasione.",
    },
    {
        "id": "9_11_2001",
        "label": "9/11 — Attentati USA",
        "date": "2001-09-11",
        "category": "MILITARY_CONFLICT",
        "description": "Airlines -40%, defense +30-50% a 12 mesi. Fed taglia 425bp totali.",
        "trades": [
            ("ITA",  "LONG",  -7.0,  20.0, 0.80),  # Aerospace & Defense ETF
            ("GLD",  "LONG",  -5.0,  10.0, 0.65),
            ("TLT",  "LONG",  -6.0,  12.0, 0.60),  # Bond (Fed dovish)
            ("DAL",  "SHORT",  8.0, -25.0, 0.75),
        ],
        "benchmark": "SPY",
        "note": "Borsa USA chiusa 17-21 settembre 2001. Entry simulata a T+3 (riapertura 24-09-2001).",
        "entry_offset_days": 3,  # borsa chiusa T+1, T+2
    },
    {
        "id": "UKRAINE_2022",
        "label": "Russia invade Ucraina",
        "date": "2022-02-24",
        "category": "MILITARY_CONFLICT",
        "description": "Oil +44%, gold +9%. Bond NON safe haven (inflazione). HFRI Macro +9%.",
        "trades": [
            ("XLE",  "LONG",  -8.0,  20.0, 0.80),
            ("LMT",  "LONG",  -7.0,  25.0, 0.80),   # Lockheed Martin
            ("NTR",  "LONG",  -8.0,  22.0, 0.75),   # Nutrien (fertilizzanti)
            ("GLD",  "LONG",  -5.0,  10.0, 0.65),
            ("TLT",  "SHORT",  7.0, -10.0, 0.60),   # Bond SHORT (inflazione)
            ("DAL",  "SHORT",  8.0, -15.0, 0.65),
        ],
        "benchmark": "SPY",
        "note": "Evento più recente con dati yfinance completi. Bond come SHORT confermato da regime inflazionistico.",
    },
    {
        "id": "IRAN_STRIKES_2024",
        "label": "Iran strikes Israel — Operazione True Promise",
        "date": "2024-04-13",
        "category": "MILITARY_CONFLICT",
        "description": "Iran lancia 300+ droni e missili su Israele. Oil +3%, gold +2%. Mercati resilienti.",
        "trades": [
            ("XLE",  "LONG",  -6.0,  12.0, 0.60),
            ("LMT",  "LONG",  -6.0,  12.0, 0.65),
            ("RTX",  "LONG",  -6.0,  12.0, 0.65),   # Raytheon (Patriot)
            ("GLD",  "LONG",  -5.0,   9.0, 0.65),
        ],
        "benchmark": "SPY",
        "note": "Evento contenuto — Israele intercetta il 99% dei proiettili. Segnale relativamente debole, buon test di calibrazione.",
    },
    {
        "id": "IRAN_2026",
        "label": "Iran escalation 2026 — Operation Epic Fury",
        "date": "2026-02-28",
        "category": "MILITARY_CONFLICT",
        "description": "Conflitto Iran. Brent +67% YTD. S&P solo -3.7% (dislocazione). HFRI Macro +4.9% Q1.",
        "trades": [
            ("XLE",  "LONG",  -8.0,  25.0, 0.80),
            ("XOP",  "LONG",  -10.0, 30.0, 0.75),
            ("LMT",  "LONG",  -7.0,  20.0, 0.80),
            ("GLD",  "LONG",  -5.0,  12.0, 0.70),
            ("FRO",  "LONG",  -10.0, 28.0, 0.70),   # Frontline (tankers)
            ("DAL",  "SHORT",  8.0, -18.0, 0.70),
        ],
        "benchmark": "SPY",
        "note": "Evento più recente nel dataset. Dati yfinance disponibili dal 2026-03 in poi.",
    },
]

# ── Ticker yfinance map ────────────────────────────────────────────────────────
# Alcuni ticker storici hanno simboli diversi o non sono disponibili
TICKER_YFINANCE = {
    "XLE":  "XLE",
    "XOP":  "XOP",
    "ITA":  "ITA",
    "GLD":  "GLD",
    "TLT":  "TLT",
    "GDX":  "GDX",
    "DAL":  "DAL",
    "UAL":  "UAL",
    "LMT":  "LMT",
    "RTX":  "RTX",
    "NOC":  "NOC",
    "GD":   "GD",
    "NTR":  "NTR",
    "FRO":  "FRO",
    "SPY":  "SPY",  # benchmark
}

# Ticker non disponibili in periodi storici lontani — usare proxy
TICKER_PROXY = {
    # 1990: ETF settoriali non esistevano, usiamo quello più simile disponibile
    "XLE":  {"before_2000": "XOM"},    # ExxonMobil come proxy energy
    "ITA":  {"before_2002": "LMT"},    # Lockheed come proxy defense
    "GLD":  {"before_2004": "GC=F"},   # Gold futures
    "TLT":  {"before_2002": "^TNX"},   # 10Y yield (inverso)
    "XOP":  {"before_2007": "XOM"},
    "FRO":  {"before_2001": None},     # Non disponibile
}


# ── Dataclasses ───────────────────────────────────────────────────────────────
@dataclass
class TradeResult:
    ticker:        str
    direction:     str
    entry_date:    str
    entry_price:   float
    exit_date:     Optional[str]
    exit_price:    Optional[float]
    exit_reason:   str       # 'target', 'stop', 'expired', 'no_data'
    pnl_pct:       float
    pnl_eur:       float
    holding_days:  int
    verdict:       str       # 'WIN', 'LOSS', 'BREAKEVEN'


@dataclass
class EventBacktestResult:
    event_id:      str
    event_label:   str
    event_date:    str
    category:      str
    trades:        list = field(default_factory=list)
    # Aggregati
    total_trades:  int   = 0
    wins:          int   = 0
    losses:        int   = 0
    win_rate:      float = 0.0
    total_pnl_eur: float = 0.0
    total_return_pct: float = 0.0
    spy_return_pct:   float = 0.0
    alpha_pct:        float = 0.0
    note:          str   = ""
    data_quality:  str   = "OK"  # 'OK', 'PARTIAL', 'NO_DATA'


@dataclass
class BacktestSummary:
    total_events:    int   = 0
    total_trades:    int   = 0
    total_wins:      int   = 0
    total_losses:    int   = 0
    overall_win_rate: float = 0.0
    total_pnl_eur:   float = 0.0
    avg_return_pct:  float = 0.0
    avg_alpha_pct:   float = 0.0
    best_event:      str   = ""
    worst_event:     str   = ""
    sharpe_cross_events: float = 0.0
    go_live_signal:  str   = ""  # 'READY', 'NOT_READY', 'MARGINAL'


# ── Price fetcher ─────────────────────────────────────────────────────────────
def _fetch_prices(ticker: str, start: str, end: str) -> Optional[dict]:
    """
    Scarica prezzi storici con yfinance.
    Ritorna dict {date_str: close_price} o None se non disponibile.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance non installato. Esegui: pip install yfinance")
        return None

    try:
        # Risolvi proxy per ticker storici
        yf_ticker = ticker
        proxy_map = TICKER_PROXY.get(ticker, {})
        event_year = int(start[:4])
        for period_key, proxy_ticker in proxy_map.items():
            if "before_" in period_key:
                cutoff_year = int(period_key.replace("before_", ""))
                if event_year < cutoff_year:
                    if proxy_ticker is None:
                        logger.info(f"{ticker} non disponibile nel {event_year}, skip")
                        return None
                    logger.info(f"{ticker} → usando proxy {proxy_ticker} per {event_year}")
                    yf_ticker = proxy_ticker
                    break

        hist = yf.download(
            yf_ticker,
            start=start,
            end=end,
            progress=False,
            auto_adjust=True,
        )

        if hist.empty:
            logger.warning(f"Nessun dato per {yf_ticker} ({start} → {end})")
            return None

        # Gestisci multi-level columns (yfinance v0.2+)
        if hasattr(hist.columns, 'levels'):
            hist.columns = hist.columns.get_level_values(0)

        close = hist["Close"] if "Close" in hist.columns else hist.iloc[:, 0]
        return {str(d.date()): float(v) for d, v in close.items()}

    except Exception as e:
        logger.warning(f"Errore fetch {ticker}: {e}")
        return None


def _get_price_on_or_after(prices: dict, target_date: str, max_days: int = 5) -> Optional[tuple]:
    """Trova il prezzo alla data o al primo giorno di borsa successivo (max_days)."""
    from datetime import date
    dt = date.fromisoformat(target_date)
    for i in range(max_days + 1):
        d_str = str(dt + timedelta(days=i))
        if d_str in prices:
            return d_str, prices[d_str]
    return None


def _find_exit(
    prices: dict,
    entry_date: str,
    entry_price: float,
    direction: str,
    stop_pct: float,
    target_pct: float,
) -> tuple:
    """
    Scansiona i prezzi post-entry per trovare quando si raggiunge stop o target.
    stop_pct: valore negativo (es. -8.0 per -8%)
    target_pct: valore positivo per LONG (es. 20.0), negativo per SHORT
    Ritorna (exit_date, exit_price, exit_reason, pnl_pct, holding_days)
    """
    from datetime import date
    sorted_dates = sorted(d for d in prices.keys() if d > entry_date)

    stop_mult   = 1 + stop_pct / 100
    target_mult = 1 + target_pct / 100

    for dt_str in sorted_dates:
        price = prices[dt_str]
        move_pct = (price - entry_price) / entry_price * 100
        if direction == "SHORT":
            move_pct = -move_pct  # per SHORT il profitto è al contrario

        holding = (date.fromisoformat(dt_str) - date.fromisoformat(entry_date)).days

        if direction == "LONG":
            if price <= entry_price * stop_mult:
                return dt_str, price, "stop", move_pct, holding
            if price >= entry_price * target_mult:
                return dt_str, price, "target", move_pct, holding
        else:  # SHORT
            # Per SHORT: profitto se il prezzo scende
            effective_pnl_pct = -((price - entry_price) / entry_price * 100)
            if effective_pnl_pct <= stop_pct:    # stop = perdita
                return dt_str, price, "stop", effective_pnl_pct, holding
            if effective_pnl_pct >= abs(target_pct):  # target = profitto
                return dt_str, price, "target", effective_pnl_pct, holding

    # Scaduto senza toccare stop/target
    if sorted_dates:
        last_date  = sorted_dates[-1]
        last_price = prices[last_date]
        from datetime import date as _date
        holding = (_date.fromisoformat(last_date) - _date.fromisoformat(entry_date)).days
        if direction == "LONG":
            final_pnl = (last_price - entry_price) / entry_price * 100
        else:
            final_pnl = -((last_price - entry_price) / entry_price * 100)
        return last_date, last_price, "expired", final_pnl, holding

    return None, None, "no_data", 0.0, 0


# ── Backtest singolo evento ───────────────────────────────────────────────────
def backtest_event(event: dict) -> EventBacktestResult:
    """Esegue il backtest per un singolo evento storico."""
    event_date = event["date"]
    event_dt   = datetime.strptime(event_date, "%Y-%m-%d")
    entry_offset = event.get("entry_offset_days", 1)

    start_date = (event_dt - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end_date   = (event_dt + timedelta(days=HOLDOUT_DAYS + 5)).strftime("%Y-%m-%d")
    entry_date = (event_dt + timedelta(days=entry_offset)).strftime("%Y-%m-%d")

    result = EventBacktestResult(
        event_id    = event["id"],
        event_label = event["label"],
        event_date  = event_date,
        category    = event["category"],
        note        = event.get("note", ""),
    )

    # Fetch SPY per benchmark
    spy_prices = _fetch_prices("SPY", start_date, end_date)
    spy_entry = _get_price_on_or_after(spy_prices, entry_date) if spy_prices else None
    spy_exit  = None
    if spy_prices:
        sorted_spy = sorted(d for d in spy_prices if d > (spy_entry[0] if spy_entry else entry_date))
        if sorted_spy:
            spy_exit_date = sorted_spy[min(HOLDOUT_DAYS - 1, len(sorted_spy) - 1)]
            spy_exit = (spy_exit_date, spy_prices[spy_exit_date])

    spy_return = 0.0
    if spy_entry and spy_exit:
        spy_return = (spy_exit[1] - spy_entry[1]) / spy_entry[1] * 100

    total_pnl_eur = 0.0
    trade_results = []
    data_issues   = 0

    for ticker, direction, stop_pct, target_pct, conviction in event["trades"]:
        # Position size: proporzione fissa del portafoglio simulato
        size_eur = INITIAL_NAV * MAX_POS_PCT * conviction
        size_eur_after_comm = size_eur * (1 - COMMISSION)

        # Fetch prezzi
        prices = _fetch_prices(ticker, start_date, end_date)
        if not prices:
            trade_results.append(TradeResult(
                ticker=ticker, direction=direction,
                entry_date=entry_date, entry_price=0,
                exit_date=None, exit_price=None,
                exit_reason="no_data", pnl_pct=0.0, pnl_eur=0.0,
                holding_days=0, verdict="NO_DATA",
            ))
            data_issues += 1
            continue

        # Entry
        entry_info = _get_price_on_or_after(prices, entry_date)
        if not entry_info:
            logger.warning(f"{ticker}: nessun prezzo disponibile per entry {entry_date}")
            trade_results.append(TradeResult(
                ticker=ticker, direction=direction,
                entry_date=entry_date, entry_price=0,
                exit_date=None, exit_price=None,
                exit_reason="no_data", pnl_pct=0.0, pnl_eur=0.0,
                holding_days=0, verdict="NO_DATA",
            ))
            data_issues += 1
            continue

        actual_entry_date, entry_price = entry_info

        # Exit
        exit_date, exit_price, exit_reason, pnl_pct, holding_days = _find_exit(
            prices, actual_entry_date, entry_price, direction, stop_pct, target_pct
        )

        if exit_date is None:
            pnl_eur = 0.0
            verdict = "NO_DATA"
        else:
            # P&L netto commissioni (0.1% entry + 0.1% exit)
            pnl_pct_net = pnl_pct - COMMISSION * 100 * 2
            pnl_eur = size_eur_after_comm * pnl_pct_net / 100
            total_pnl_eur += pnl_eur

            if pnl_pct_net > 0.5:
                verdict = "WIN"
            elif pnl_pct_net < -0.5:
                verdict = "LOSS"
            else:
                verdict = "BREAKEVEN"

        trade_results.append(TradeResult(
            ticker=ticker,
            direction=direction,
            entry_date=actual_entry_date,
            entry_price=round(entry_price, 4),
            exit_date=exit_date,
            exit_price=round(exit_price, 4) if exit_price else None,
            exit_reason=exit_reason,
            pnl_pct=round(pnl_pct, 2) if exit_date else 0.0,
            pnl_eur=round(pnl_eur, 2),
            holding_days=holding_days,
            verdict=verdict,
        ))

    # Aggregati evento
    valid_trades = [t for t in trade_results if t.verdict != "NO_DATA"]
    result.trades        = trade_results
    result.total_trades  = len(valid_trades)
    result.wins          = sum(1 for t in valid_trades if t.verdict == "WIN")
    result.losses        = sum(1 for t in valid_trades if t.verdict == "LOSS")
    result.win_rate      = result.wins / result.total_trades if result.total_trades else 0.0
    result.total_pnl_eur = round(total_pnl_eur, 2)
    result.total_return_pct = round(total_pnl_eur / INITIAL_NAV * 100, 2)
    result.spy_return_pct   = round(spy_return, 2)
    result.alpha_pct        = round(result.total_return_pct - spy_return, 2)
    result.data_quality     = "NO_DATA" if data_issues == len(event["trades"]) else \
                              "PARTIAL" if data_issues > 0 else "OK"

    return result


# ── Backtest completo ─────────────────────────────────────────────────────────
def run_full_backtest(event_ids: Optional[list] = None) -> dict:
    """
    Esegue il backtest su tutti gli eventi (o un sottoinsieme).
    Ritorna un dict con i risultati per evento e il summary aggregato.
    """
    events_to_run = HISTORICAL_EVENTS
    if event_ids:
        events_to_run = [e for e in HISTORICAL_EVENTS if e["id"] in event_ids]

    event_results = []
    for event in events_to_run:
        logger.info(f"\n{'='*60}")
        logger.info(f"Backtest: {event['label']} ({event['date']})")
        logger.info("="*60)
        res = backtest_event(event)
        event_results.append(res)
        logger.info(
            f"  Trades: {res.total_trades} | W/L: {res.wins}/{res.losses} "
            f"| WR: {res.win_rate*100:.0f}% | P&L: €{res.total_pnl_eur:.2f} "
            f"| Return: {res.total_return_pct:+.2f}% | Alpha: {res.alpha_pct:+.2f}%"
        )

    # Summary aggregato
    summary = _compute_summary(event_results)

    # Struttura output
    output = {
        "metadata": {
            "generated_at":  datetime.now().isoformat(),
            "initial_nav":   INITIAL_NAV,
            "max_pos_pct":   MAX_POS_PCT,
            "commission_pct": COMMISSION * 100,
            "entry_offset":  "T+1 (T+3 per 9/11)",
            "exit_rule":     "First of: target hit, stop hit, or holdout expiry (+60d)",
        },
        "events": [asdict(r) for r in event_results],
        "summary": asdict(summary),
    }

    # Salva JSON
    RESULTS_FILE.write_text(json.dumps(output, indent=2, default=str))
    logger.info(f"\nRisultati salvati in {RESULTS_FILE}")

    return output


def _compute_summary(event_results: list) -> BacktestSummary:
    """Calcola le statistiche aggregate su tutti gli eventi."""
    valid = [r for r in event_results if r.data_quality != "NO_DATA"]
    if not valid:
        return BacktestSummary(go_live_signal="NO_DATA")

    total_trades = sum(r.total_trades for r in valid)
    total_wins   = sum(r.wins for r in valid)
    total_losses = sum(r.losses for r in valid)
    overall_wr   = total_wins / total_trades if total_trades else 0
    total_pnl    = sum(r.total_pnl_eur for r in valid)
    returns      = [r.total_return_pct for r in valid]
    alphas       = [r.alpha_pct for r in valid]

    import math
    avg_return = sum(returns) / len(returns) if returns else 0
    avg_alpha  = sum(alphas) / len(alphas) if alphas else 0

    # Sharpe cross-event (deviazione standard dei return per evento)
    if len(returns) > 1:
        mean_r = avg_return
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(variance) if variance > 0 else 0.001
        rf_equiv = 4.5 / 12  # RF mensile approssimato
        sharpe = (mean_r - rf_equiv) / std_r if std_r > 0 else 0
    else:
        sharpe = 0.0

    best  = max(valid, key=lambda r: r.total_return_pct, default=None)
    worst = min(valid, key=lambda r: r.total_return_pct, default=None)

    # Segnale go-live basato sui risultati backtest
    if overall_wr >= 0.60 and avg_alpha >= 5.0 and sharpe >= 0.5:
        go_live = "READY"
    elif overall_wr >= 0.50 and avg_alpha >= 0:
        go_live = "MARGINAL"
    else:
        go_live = "NOT_READY"

    return BacktestSummary(
        total_events     = len(valid),
        total_trades     = total_trades,
        total_wins       = total_wins,
        total_losses     = total_losses,
        overall_win_rate = round(overall_wr, 3),
        total_pnl_eur    = round(total_pnl, 2),
        avg_return_pct   = round(avg_return, 2),
        avg_alpha_pct    = round(avg_alpha, 2),
        best_event       = best.event_id if best else "",
        worst_event      = worst.event_id if worst else "",
        sharpe_cross_events = round(sharpe, 3),
        go_live_signal   = go_live,
    )


# ── Pretty printer ────────────────────────────────────────────────────────────
def _print_results(output: dict):
    """Stampa i risultati in formato leggibile."""
    print("\n" + "="*70)
    print("  MacroSignalTool — BACKTEST RISULTATI")
    print("="*70)

    for ev in output["events"]:
        dq = ev.get("data_quality", "?")
        dq_icon = "✅" if dq == "OK" else "⚠️ " if dq == "PARTIAL" else "❌"
        print(f"\n{dq_icon}  {ev['event_label']} ({ev['event_date']})")
        print(f"   Trades: {ev['total_trades']} | "
              f"W/L: {ev['wins']}/{ev['losses']} | "
              f"WR: {ev['win_rate']*100:.0f}% | "
              f"P&L: €{ev['total_pnl_eur']:+.2f} | "
              f"Return: {ev['total_return_pct']:+.2f}% | "
              f"Alpha: {ev['alpha_pct']:+.2f}%")
        for t in ev.get("trades", []):
            icon = "✅" if t["verdict"] == "WIN" else "❌" if t["verdict"] == "LOSS" else "➖" if t["verdict"] == "BREAKEVEN" else "⛔"
            arrow = "↑" if t["direction"] == "LONG" else "↓"
            print(f"     {icon} {arrow} {t['ticker']:5s}  "
                  f"entry: {t['entry_price']:.2f}  "
                  f"exit: {t.get('exit_price') or '—':>7}  "
                  f"P&L: {t['pnl_pct']:+.1f}%  ({t['exit_reason']:8s}, {t['holding_days']}d)")

    s = output["summary"]
    print("\n" + "─"*70)
    print("  SUMMARY AGGREGATO")
    print("─"*70)
    print(f"  Totale trade:   {s['total_trades']} su {s['total_events']} eventi")
    print(f"  Win rate glob.: {s['overall_win_rate']*100:.1f}%  (W:{s['total_wins']} / L:{s['total_losses']})")
    print(f"  P&L totale:     €{s['total_pnl_eur']:+.2f}")
    print(f"  Return medio:   {s['avg_return_pct']:+.2f}% per evento")
    print(f"  Alpha medio:    {s['avg_alpha_pct']:+.2f}% vs SPY")
    print(f"  Sharpe (cross): {s['sharpe_cross_events']:.3f}")
    print(f"  Miglior evento: {s['best_event']}")
    print(f"  Peggior evento: {s['worst_event']}")
    print()
    signal = s["go_live_signal"]
    icon = "🟢" if signal == "READY" else "🟡" if signal == "MARGINAL" else "🔴"
    print(f"  {icon}  GO-LIVE SIGNAL: {signal}")
    print("="*70)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MacroSignalTool Backtester")
    parser.add_argument("--event",   type=str, help="ID evento singolo (es. UKRAINE_2022)")
    parser.add_argument("--summary", action="store_true", help="Mostra solo riepilogo finale")
    parser.add_argument("--list",    action="store_true", help="Elenca eventi disponibili")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if not args.summary else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.list:
        print("\nEventi disponibili:")
        for ev in HISTORICAL_EVENTS:
            print(f"  {ev['id']:25s} — {ev['label']} ({ev['date']})")
        sys.exit(0)

    event_ids = [args.event] if args.event else None
    output = run_full_backtest(event_ids)

    if not args.summary:
        _print_results(output)
    else:
        s = output["summary"]
        print(f"WR: {s['overall_win_rate']*100:.1f}% | "
              f"Return medio: {s['avg_return_pct']:+.2f}% | "
              f"Alpha: {s['avg_alpha_pct']:+.2f}% | "
              f"Go-live: {s['go_live_signal']}")
