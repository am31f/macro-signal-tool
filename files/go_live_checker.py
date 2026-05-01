"""
go_live_checker.py — MacroSignalTool Phase 7.2
===============================================
Valuta se il tool è pronto per il real trading confrontando le metriche
paper trading attuali con le 4 soglie della go-live checklist:

  1. Trades completati:  >= 30
  2. Win rate:           >= 52%
  3. Sharpe simulato:    >= 0.8
  4. Max drawdown:       <= 15%

Produce un report leggibile e aggiorna il campo go_live_checklist
nel JSON di progetto.

Utilizzo:
    python go_live_checker.py            # report completo
    python go_live_checker.py --watch    # ri-controlla ogni 5 min
    python go_live_checker.py --json     # output JSON puro (per integrazione)

Include anche i risultati del backtesting se backtest_results.json esiste.
"""

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR        = Path(__file__).parent
DB_PATH         = DATA_DIR / "paper_trading.db"
BACKTEST_FILE   = DATA_DIR / "backtest_results.json"
PROJECT_JSON    = DATA_DIR.parent / "macro_trading_tool.json"

# ── Soglie go-live ─────────────────────────────────────────────────────────────
CRITERIA = {
    "trades_count": {
        "label":  "Trade paper completati",
        "target": 30,
        "unit":   "trade",
        "better": "higher",
    },
    "win_rate": {
        "label":  "Win rate",
        "target": 0.52,
        "unit":   "%",
        "better": "higher",
        "format": "pct",
    },
    "sharpe": {
        "label":  "Sharpe simulato (annualizzato, RF 4.5%)",
        "target": 0.8,
        "unit":   "",
        "better": "higher",
    },
    "drawdown": {
        "label":  "Max drawdown",
        "target": 15.0,
        "unit":   "%",
        "better": "lower",
        "note":   "Must stay BELOW target",
    },
}


# ── DB reader ─────────────────────────────────────────────────────────────────
def _read_paper_metrics() -> dict:
    """
    Legge le metriche di performance direttamente dal DB paper_trading.db.
    Ritorna un dict con tutte le metriche necessarie per la checklist.
    """
    if not DB_PATH.exists():
        return {"error": f"DB non trovato: {DB_PATH}"}

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Posizioni chiuse
        closed = cur.execute("""
            SELECT pnl_eur, pnl_pct, verdict, close_date, entry_date,
                   entry_price, close_price, close_reason, event_category
            FROM positions
            WHERE status = 'closed'
            ORDER BY close_date DESC
        """).fetchall()

        # NAV history per Sharpe e drawdown
        nav_hist = cur.execute("""
            SELECT date, nav FROM nav_history ORDER BY date ASC
        """).fetchall()

        # Portfolio state
        port = cur.execute("""
            SELECT * FROM portfolio_config LIMIT 1
        """).fetchone()

        conn.close()

        if not closed:
            return {
                "trades_count": 0,
                "win_rate":     0.0,
                "sharpe":       0.0,
                "drawdown":     0.0,
                "total_pnl_eur": 0.0,
                "total_return_pct": 0.0,
                "closed_trades": [],
                "nav_history":  [],
                "status": "NO_TRADES",
            }

        trades = [dict(r) for r in closed]
        wins   = sum(1 for t in trades if t.get("verdict") == "WIN")
        losses = sum(1 for t in trades if t.get("verdict") == "LOSS")
        n      = len(trades)
        win_rate = wins / n if n else 0
        total_pnl = sum(t.get("pnl_eur", 0) for t in trades)
        initial_nav = float(port["initial_nav"]) if port else 10000.0
        total_return_pct = total_pnl / initial_nav * 100

        # Sharpe da NAV history
        sharpe = _calc_sharpe([dict(r) for r in nav_hist]) if nav_hist else 0.0

        # Max drawdown da NAV history
        drawdown = _calc_max_drawdown([dict(r) for r in nav_hist]) if nav_hist else 0.0

        return {
            "trades_count":    n,
            "wins":            wins,
            "losses":          losses,
            "breakevens":      n - wins - losses,
            "win_rate":        round(win_rate, 4),
            "sharpe":          round(sharpe, 4),
            "drawdown":        round(drawdown, 2),
            "total_pnl_eur":   round(total_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "avg_win_pct":     round(
                sum(t["pnl_pct"] for t in trades if t.get("verdict") == "WIN") / wins, 2
            ) if wins else 0.0,
            "avg_loss_pct":    round(
                sum(t["pnl_pct"] for t in trades if t.get("verdict") == "LOSS") / losses, 2
            ) if losses else 0.0,
            "closed_trades":   trades[-10:],  # ultimi 10
            "status": "OK",
        }

    except Exception as e:
        logger.error(f"Errore lettura DB: {e}")
        return {"error": str(e)}


def _calc_sharpe(nav_hist: list) -> float:
    """Calcola Sharpe annualizzato da sequenza di NAV giornalieri."""
    if len(nav_hist) < 2:
        return 0.0
    import math
    navs = [float(r["nav"]) for r in nav_hist]
    daily_returns = [(navs[i] - navs[i-1]) / navs[i-1] for i in range(1, len(navs))]
    if not daily_returns:
        return 0.0
    mean_r  = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
    std_r   = math.sqrt(variance) if variance > 0 else 0.001
    rfr_daily = 0.045 / 252
    sharpe = (mean_r - rfr_daily) / std_r * math.sqrt(252)
    return sharpe


def _calc_max_drawdown(nav_hist: list) -> float:
    """Calcola max drawdown in % dalla sequenza NAV."""
    if not nav_hist:
        return 0.0
    navs = [float(r["nav"]) for r in nav_hist]
    peak = navs[0]
    max_dd = 0.0
    for nav in navs:
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ── Checklist evaluator ───────────────────────────────────────────────────────
def evaluate_checklist(metrics: dict) -> dict:
    """Valuta le 4 soglie go-live con le metriche attuali."""
    if "error" in metrics:
        return {"error": metrics["error"], "all_met": False, "criteria": {}}

    results = {}
    for key, crit in CRITERIA.items():
        value  = metrics.get(key, 0)
        target = crit["target"]
        better = crit.get("better", "higher")

        if better == "higher":
            met = value >= target
            progress = min(value / target * 100, 100) if target > 0 else 0
        else:  # lower is better (drawdown)
            met = value <= target
            progress = min((target - value) / target * 100 + 50, 100) if target > 0 else 0
            if value <= 0:
                progress = 100

        results[key] = {
            "label":    crit["label"],
            "value":    value,
            "target":   target,
            "met":      met,
            "progress_pct": round(progress, 1),
            "unit":     crit.get("unit", ""),
            "better":   better,
        }

    all_met = all(v["met"] for v in results.values())
    met_count = sum(1 for v in results.values() if v["met"])
    total_criteria = len(results)

    # Stato complessivo
    if all_met:
        status = "GO_LIVE_READY"
    elif met_count >= 3:
        status = "ALMOST_READY"
    elif met_count >= 2:
        status = "IN_PROGRESS"
    else:
        status = "EARLY_STAGE"

    return {
        "all_met":       all_met,
        "criteria":      results,
        "met_count":     met_count,
        "total_criteria": total_criteria,
        "current_status": status,
        "evaluated_at":  datetime.now().isoformat(),
    }


# ── Backtest summary reader ────────────────────────────────────────────────────
def _read_backtest_summary() -> Optional[dict]:
    """Legge il summary dal file backtest_results.json se disponibile."""
    if not BACKTEST_FILE.exists():
        return None
    try:
        with open(BACKTEST_FILE) as f:
            data = json.load(f)
        return data.get("summary")
    except Exception:
        return None


# ── Report printer ────────────────────────────────────────────────────────────
def _print_report(metrics: dict, checklist: dict, backtest: Optional[dict]):
    """Stampa il report go-live in formato leggibile."""
    print("\n" + "="*65)
    print("  MacroSignalTool — GO-LIVE CHECKLIST")
    print("="*65)
    print(f"  Valutazione: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print()

    if "error" in checklist:
        print(f"  ❌ Errore: {checklist['error']}")
        print("="*65)
        return

    status = checklist.get("current_status", "?")
    status_map = {
        "GO_LIVE_READY": "🟢 GO-LIVE READY",
        "ALMOST_READY":  "🟡 QUASI PRONTO (manca 1 criterio)",
        "IN_PROGRESS":   "🟠 IN CORSO (2/4 criteri soddisfatti)",
        "EARLY_STAGE":   "🔴 FASE INIZIALE (< 2 criteri)",
    }
    print(f"  STATO: {status_map.get(status, status)}")
    print(f"  Criteri soddisfatti: {checklist['met_count']}/{checklist['total_criteria']}")
    print()

    criteria = checklist.get("criteria", {})
    for key, crit in criteria.items():
        met = crit["met"]
        icon = "✅" if met else "❌"
        value = crit["value"]
        target = crit["target"]
        unit = crit.get("unit", "")

        # Formatta value e target
        if key == "win_rate":
            v_str = f"{value*100:.1f}%"
            t_str = f"≥{target*100:.0f}%"
        elif key == "drawdown":
            v_str = f"{value:.2f}%"
            t_str = f"≤{target:.0f}%"
        elif key == "sharpe":
            v_str = f"{value:.3f}"
            t_str = f"≥{target}"
        else:
            v_str = str(int(value))
            t_str = f"≥{int(target)}"

        progress = crit.get("progress_pct", 0)
        bar_len  = 20
        filled   = int(bar_len * progress / 100)
        bar      = "█" * filled + "░" * (bar_len - filled)

        print(f"  {icon} {crit['label']}")
        print(f"     Attuale: {v_str}  Target: {t_str}")
        print(f"     [{bar}] {progress:.0f}%")
        print()

    # Metriche aggiuntive
    if metrics.get("status") != "NO_TRADES":
        print("─"*65)
        print("  METRICHE PAPER TRADING")
        print("─"*65)
        n     = metrics.get("trades_count", 0)
        wins  = metrics.get("wins", 0)
        losses= metrics.get("losses", 0)
        be    = metrics.get("breakevens", 0)
        pnl   = metrics.get("total_pnl_eur", 0)
        ret   = metrics.get("total_return_pct", 0)
        avg_w = metrics.get("avg_win_pct", 0)
        avg_l = metrics.get("avg_loss_pct", 0)
        pf    = abs(avg_w * wins) / abs(avg_l * losses) if losses and avg_l else float('inf')

        pnl_sign = "+" if pnl >= 0 else ""
        ret_sign = "+" if ret >= 0 else ""
        print(f"  Totale trade:   {n}  (W:{wins} / L:{losses} / BE:{be})")
        print(f"  P&L totale:     {pnl_sign}€{pnl:.2f}  ({ret_sign}{ret:.2f}%)")
        print(f"  Avg win:        +{avg_w:.2f}%")
        print(f"  Avg loss:       {avg_l:.2f}%")
        print(f"  Profit factor:  {pf:.2f}" if pf != float('inf') else f"  Profit factor:  ∞ (no losses)")
        print()

    # Backtest summary
    if backtest:
        print("─"*65)
        print("  BACKTEST STORICO (5 eventi)")
        print("─"*65)
        bt_wr    = backtest.get("overall_win_rate", 0)
        bt_return = backtest.get("avg_return_pct", 0)
        bt_alpha = backtest.get("avg_alpha_pct", 0)
        bt_sharpe = backtest.get("sharpe_cross_events", 0)
        bt_signal = backtest.get("go_live_signal", "?")
        bt_best  = backtest.get("best_event", "?")
        bt_worst = backtest.get("worst_event", "?")
        bt_total = backtest.get("total_trades", 0)

        bt_icon = "🟢" if bt_signal == "READY" else "🟡" if bt_signal == "MARGINAL" else "🔴"
        print(f"  Segnale backtest: {bt_icon} {bt_signal}")
        print(f"  Trade totali:     {bt_total}")
        print(f"  Win rate:         {bt_wr*100:.1f}%")
        print(f"  Return medio:     {bt_return:+.2f}% per evento")
        print(f"  Alpha medio:      {bt_alpha:+.2f}% vs SPY")
        print(f"  Sharpe:           {bt_sharpe:.3f}")
        print(f"  Miglior evento:   {bt_best}")
        print(f"  Peggior evento:   {bt_worst}")
        print()

    # Raccomandazione finale
    print("─"*65)
    print("  RACCOMANDAZIONE")
    print("─"*65)
    if status == "GO_LIVE_READY":
        print("  🟢 Tutti i criteri sono soddisfatti.")
        print("     Il tool può passare dal paper al real trading.")
        print("     Suggerimento: inizia con size ridotta (25% del normale)")
        print("     e osserva il comportamento per 2-4 settimane.")
    elif status == "ALMOST_READY":
        missing = [k for k, v in criteria.items() if not v["met"]]
        m = missing[0] if missing else "?"
        print(f"  🟡 Manca solo il criterio '{criteria.get(m,{}).get('label',m)}'.")
        print(f"     Continua il paper trading ancora qualche settimana.")
    elif status == "IN_PROGRESS":
        missing = [criteria[k]["label"] for k, v in criteria.items() if not v["met"]]
        print(f"  🟠 Mancano {len(missing)} criteri:")
        for m in missing:
            print(f"     • {m}")
        print("     Continua il paper trading sistematicamente.")
    else:
        n = metrics.get("trades_count", 0)
        needed = 30 - n
        print(f"  🔴 Fase iniziale. Servono ancora ~{needed} trade paper.")
        print("     Esegui segnali ogni volta che la pipeline genera")
        print("     un segnale con kelly_quality >= MODERATE.")

    print("="*65)


# ── Generate report dict ──────────────────────────────────────────────────────
def generate_go_live_report() -> dict:
    """Genera il report go-live completo come dict (usabile da main.py)."""
    metrics   = _read_paper_metrics()
    checklist = evaluate_checklist(metrics)
    backtest  = _read_backtest_summary()

    return {
        "metrics":   metrics,
        "checklist": checklist,
        "backtest":  backtest,
        "generated_at": datetime.now().isoformat(),
    }


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MacroSignalTool Go-Live Checker")
    parser.add_argument("--json",  action="store_true", help="Output JSON puro")
    parser.add_argument("--watch", action="store_true", help="Controlla ogni 5 minuti")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    def _run_once():
        metrics   = _read_paper_metrics()
        checklist = evaluate_checklist(metrics)
        backtest  = _read_backtest_summary()

        if args.json:
            print(json.dumps({"metrics": metrics, "checklist": checklist, "backtest": backtest}, indent=2))
        else:
            _print_report(metrics, checklist, backtest)

    if args.watch:
        print("Modalità watch — aggiornamento ogni 5 minuti. Ctrl+C per uscire.")
        while True:
            _run_once()
            time.sleep(300)
    else:
        _run_once()
