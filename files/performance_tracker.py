"""
performance_tracker.py
Phase 4, Task 4.3 — MacroSignalTool

Calcola e visualizza le metriche di performance del paper trading.

Metriche:
  - Win rate, avg win, avg loss, profit factor
  - Sharpe ratio simulato (annualizzato)
  - Max drawdown
  - P&L per categoria evento
  - P&L per holding period (< 5d, 5-20d, > 20d)
  - Equity curve vs SPY benchmark
  - Go-live checklist (30 trade, win_rate > 52%, Sharpe > 0.8, drawdown < 15%)

Dipendenze: sqlite3, yfinance (per benchmark SPY)
Testabile: python performance_tracker.py --report
"""

import argparse
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

import sys
sys.path.insert(0, str(Path(__file__).parent))
from portfolio_manager import DB_PATH, get_conn, get_closed_positions, get_portfolio_state, INITIAL_NAV

# ─── Config ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("performance_tracker")

RISK_FREE_RATE = 0.045   # 4.5% annuo (circa tassi attuali)
TRADING_DAYS_YEAR = 252

# Go-live thresholds (da macro_trading_tool.json)
GOLIVE_MIN_TRADES = 30
GOLIVE_MIN_WIN_RATE = 0.52
GOLIVE_MIN_SHARPE = 0.80
GOLIVE_MAX_DRAWDOWN = 15.0   # %


# ─── Fetch benchmark SPY ──────────────────────────────────────────────────────

def _fetch_spy_return(start_date: str, end_date: str) -> Optional[float]:
    """Restituisce il return % di SPY tra due date."""
    if not YFINANCE_AVAILABLE:
        return None
    try:
        spy = yf.Ticker("SPY")
        hist = spy.history(start=start_date[:10], end=end_date[:10])
        if hist.empty or len(hist) < 2:
            return None
        start_price = hist["Close"].iloc[0]
        end_price = hist["Close"].iloc[-1]
        return ((end_price - start_price) / start_price) * 100
    except Exception as e:
        logger.warning(f"SPY benchmark non disponibile: {e}")
        return None


# ─── Calcoli statistici ───────────────────────────────────────────────────────

def _sharpe_from_returns(daily_returns: list[float]) -> float:
    """
    Calcola Sharpe annualizzato da lista di daily returns (in %).
    Sharpe = (mean_daily - rfr_daily) / std_daily * sqrt(252)
    """
    if len(daily_returns) < 3:
        return 0.0
    n = len(daily_returns)
    mean = sum(daily_returns) / n
    variance = sum((r - mean) ** 2 for r in daily_returns) / max(n - 1, 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    rfr_daily = (RISK_FREE_RATE / TRADING_DAYS_YEAR) * 100
    sharpe = ((mean - rfr_daily) / std) * math.sqrt(TRADING_DAYS_YEAR)
    return round(sharpe, 3)


def _max_drawdown(nav_series: list[float]) -> float:
    """Calcola max drawdown % da una serie NAV."""
    if len(nav_series) < 2:
        return 0.0
    peak = nav_series[0]
    max_dd = 0.0
    for nav in nav_series:
        if nav > peak:
            peak = nav
        dd = ((peak - nav) / peak) * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 3)


# ─── Report principale ────────────────────────────────────────────────────────

def generate_report(db_path: Path = DB_PATH) -> dict:
    """
    Genera il report completo di performance dal paper trading DB.
    """
    closed = get_closed_positions(db_path)
    portfolio = get_portfolio_state(db_path)

    if not closed:
        return {
            "status": "NO_DATA",
            "message": "Nessun trade chiuso ancora. Esegui e chiudi almeno 1 trade.",
            "portfolio_state": portfolio,
        }

    # ── Trade-level stats ──────────────────────────────────────────────────────
    total_trades = len(closed)
    wins = [t for t in closed if t["pnl_eur"] > 0]
    losses = [t for t in closed if t["pnl_eur"] < 0]
    breakevens = [t for t in closed if t["pnl_eur"] == 0]

    win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
    avg_win_pct = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0.0
    avg_loss_pct = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0.0
    avg_win_eur = sum(t["pnl_eur"] for t in wins) / len(wins) if wins else 0.0
    avg_loss_eur = sum(t["pnl_eur"] for t in losses) / len(losses) if losses else 0.0

    gross_profit = sum(t["pnl_eur"] for t in wins)
    gross_loss = abs(sum(t["pnl_eur"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    total_pnl_eur = sum(t["pnl_eur"] for t in closed)
    total_return_pct = (total_pnl_eur / INITIAL_NAV) * 100

    # ── Holding periods ───────────────────────────────────────────────────────
    def holding_days(trade: dict) -> float:
        try:
            e = datetime.fromisoformat(trade["entry_date"].replace("Z", "+00:00"))
            c = datetime.fromisoformat(trade["close_date"].replace("Z", "+00:00"))
            return (c - e).total_seconds() / 86400
        except Exception:
            return 0.0

    avg_holding = sum(holding_days(t) for t in closed) / total_trades if total_trades else 0.0
    avg_holding_wins = sum(holding_days(t) for t in wins) / len(wins) if wins else 0.0
    avg_holding_losses = sum(holding_days(t) for t in losses) / len(losses) if losses else 0.0

    # ── P&L per categoria evento ───────────────────────────────────────────────
    pnl_by_category: dict[str, dict] = {}
    for trade in closed:
        cat = trade.get("event_category", "UNKNOWN") or "UNKNOWN"
        if cat not in pnl_by_category:
            pnl_by_category[cat] = {"trades": 0, "wins": 0, "pnl_eur": 0.0, "win_rate": 0.0}
        pnl_by_category[cat]["trades"] += 1
        pnl_by_category[cat]["pnl_eur"] += trade["pnl_eur"]
        if trade["pnl_eur"] > 0:
            pnl_by_category[cat]["wins"] += 1
    for cat, data in pnl_by_category.items():
        data["win_rate"] = round(data["wins"] / data["trades"], 3) if data["trades"] > 0 else 0.0
        data["pnl_eur"] = round(data["pnl_eur"], 2)

    # ── P&L per close reason ──────────────────────────────────────────────────
    pnl_by_reason: dict[str, dict] = {}
    for trade in closed:
        reason = trade.get("close_reason", "unknown") or "unknown"
        if reason not in pnl_by_reason:
            pnl_by_reason[reason] = {"trades": 0, "pnl_eur": 0.0}
        pnl_by_reason[reason]["trades"] += 1
        pnl_by_reason[reason]["pnl_eur"] += trade["pnl_eur"]
    for r in pnl_by_reason.values():
        r["pnl_eur"] = round(r["pnl_eur"], 2)

    # ── NAV history e Sharpe ──────────────────────────────────────────────────
    with get_conn(db_path) as conn:
        nav_rows = conn.execute(
            "SELECT timestamp, nav FROM nav_history ORDER BY id ASC"
        ).fetchall()

    nav_series = [row["nav"] for row in nav_rows]
    nav_dates = [row["timestamp"][:10] for row in nav_rows]

    # Daily returns da NAV history
    daily_returns = []
    if len(nav_series) >= 2:
        for i in range(1, len(nav_series)):
            prev = nav_series[i - 1]
            if prev > 0:
                daily_returns.append(((nav_series[i] - prev) / prev) * 100)

    sharpe = _sharpe_from_returns(daily_returns)
    max_dd = _max_drawdown(nav_series) if nav_series else 0.0

    # ── Equity curve (ultimi 30 punti) ────────────────────────────────────────
    equity_curve = [
        {"date": nav_dates[i], "nav": nav_series[i]}
        for i in range(len(nav_series))
    ][-30:]   # max 30 punti per leggerezza

    # ── Benchmark SPY ─────────────────────────────────────────────────────────
    spy_return = None
    if nav_dates:
        spy_return = _fetch_spy_return(nav_dates[0], nav_dates[-1])

    alpha = None
    if spy_return is not None:
        alpha = round(total_return_pct - spy_return, 3)

    # ── Go-live checklist ─────────────────────────────────────────────────────
    go_live = {
        "min_trades_required": GOLIVE_MIN_TRADES,
        "min_win_rate": GOLIVE_MIN_WIN_RATE,
        "min_sharpe": GOLIVE_MIN_SHARPE,
        "max_drawdown_allowed_pct": GOLIVE_MAX_DRAWDOWN,
        "current_status": "NOT_STARTED",
        "criteria": {
            "trades_count": {"value": total_trades, "target": GOLIVE_MIN_TRADES,
                             "met": total_trades >= GOLIVE_MIN_TRADES},
            "win_rate":     {"value": round(win_rate, 3), "target": GOLIVE_MIN_WIN_RATE,
                             "met": win_rate >= GOLIVE_MIN_WIN_RATE},
            "sharpe":       {"value": sharpe, "target": GOLIVE_MIN_SHARPE,
                             "met": sharpe >= GOLIVE_MIN_SHARPE},
            "drawdown":     {"value": max_dd, "target": GOLIVE_MAX_DRAWDOWN,
                             "met": max_dd <= GOLIVE_MAX_DRAWDOWN},
        }
    }
    criteria_met = sum(1 for c in go_live["criteria"].values() if c["met"])
    if criteria_met == 4:
        go_live["current_status"] = "READY_FOR_GOLIVE"
    elif criteria_met >= 2:
        go_live["current_status"] = f"IN_PROGRESS ({criteria_met}/4 criteri)"
    else:
        go_live["current_status"] = f"NOT_READY ({criteria_met}/4 criteri)"

    # ── Report finale ─────────────────────────────────────────────────────────
    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "summary": {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "breakevens": len(breakevens),
            "win_rate": round(win_rate, 3),
            "avg_win_pct": round(avg_win_pct, 3),
            "avg_loss_pct": round(avg_loss_pct, 3),
            "avg_win_eur": round(avg_win_eur, 2),
            "avg_loss_eur": round(avg_loss_eur, 2),
            "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else "∞",
            "total_pnl_eur": round(total_pnl_eur, 2),
            "total_return_pct": round(total_return_pct, 3),
        },
        "risk_metrics": {
            "sharpe_simulated": sharpe,
            "max_drawdown_pct": max_dd,
            "avg_holding_days": round(avg_holding, 1),
            "avg_holding_wins": round(avg_holding_wins, 1),
            "avg_holding_losses": round(avg_holding_losses, 1),
        },
        "benchmark": {
            "tool_return_pct": round(total_return_pct, 3),
            "SPY_return_pct": round(spy_return, 3) if spy_return is not None else None,
            "alpha_pct": alpha,
            "period_start": nav_dates[0] if nav_dates else None,
            "period_end": nav_dates[-1] if nav_dates else None,
        },
        "pnl_by_category": pnl_by_category,
        "pnl_by_close_reason": pnl_by_reason,
        "equity_curve": equity_curve,
        "portfolio_state": {
            "total_nav": portfolio["total_nav"],
            "cash": portfolio["cash"],
            "open_positions": portfolio["num_open_positions"],
            "realized_pnl": portfolio["realized_pnl_eur"],
        },
        "go_live_checklist": go_live,
    }


def print_report(report: dict):
    """Stampa il report in formato leggibile."""
    if report.get("status") == "NO_DATA":
        print(f"\n⚠️  {report['message']}")
        return

    s = report["summary"]
    r = report["risk_metrics"]
    b = report["benchmark"]
    gl = report["go_live_checklist"]

    print("\n" + "="*65)
    print("📊  MACRO SIGNAL TOOL — PERFORMANCE REPORT")
    print("="*65)

    print(f"\n{'─'*40}")
    print("TRADE STATISTICS")
    print(f"{'─'*40}")
    print(f"  Totale trade:        {s['total_trades']}")
    print(f"  Win / Loss / BE:     {s['wins']} / {s['losses']} / {s['breakevens']}")
    print(f"  Win rate:            {s['win_rate']:.1%}")
    print(f"  Avg win:             +{s['avg_win_pct']:.2f}% (€{s['avg_win_eur']:+.2f})")
    print(f"  Avg loss:            {s['avg_loss_pct']:.2f}% (€{s['avg_loss_eur']:.2f})")
    print(f"  Profit factor:       {s['profit_factor']}")
    print(f"  Total P&L:           €{s['total_pnl_eur']:+.2f} ({s['total_return_pct']:+.2f}%)")

    print(f"\n{'─'*40}")
    print("RISK METRICS")
    print(f"{'─'*40}")
    print(f"  Sharpe simulato:     {r['sharpe_simulated']:.3f}")
    print(f"  Max drawdown:        {r['max_drawdown_pct']:.2f}%")
    print(f"  Avg holding:         {r['avg_holding_days']:.1f} giorni")
    print(f"    → Wins:            {r['avg_holding_wins']:.1f}d")
    print(f"    → Losses:          {r['avg_holding_losses']:.1f}d")

    print(f"\n{'─'*40}")
    print("BENCHMARK vs SPY")
    print(f"{'─'*40}")
    print(f"  Tool return:         {b['tool_return_pct']:+.2f}%")
    spy = b['SPY_return_pct']
    print(f"  SPY return:          {f'{spy:+.2f}%' if spy is not None else 'N/D'}")
    alpha = b['alpha_pct']
    print(f"  Alpha:               {f'{alpha:+.2f}%' if alpha is not None else 'N/D'}")
    if b.get("period_start"):
        print(f"  Periodo:             {b['period_start']} → {b['period_end']}")

    if report["pnl_by_category"]:
        print(f"\n{'─'*40}")
        print("P&L PER CATEGORIA EVENTO")
        print(f"{'─'*40}")
        for cat, data in sorted(report["pnl_by_category"].items(),
                                key=lambda x: x[1]["pnl_eur"], reverse=True):
            sign = "✅" if data["pnl_eur"] >= 0 else "❌"
            print(f"  {sign} {cat:<30} {data['trades']:>2} trade  "
                  f"WR={data['win_rate']:.0%}  P&L={data['pnl_eur']:+.2f}€")

    print(f"\n{'─'*40}")
    print("GO-LIVE CHECKLIST")
    print(f"{'─'*40}")
    for name, crit in gl["criteria"].items():
        icon = "✅" if crit["met"] else "❌"
        val = crit["value"]
        tgt = crit["target"]
        if name == "win_rate":
            print(f"  {icon} {name:<20} {val:.1%} (target: {tgt:.0%})")
        elif name == "drawdown":
            print(f"  {icon} {name:<20} {val:.1f}% (max: {tgt:.0f}%)")
        elif name == "sharpe":
            print(f"  {icon} {name:<20} {val:.3f} (target: {tgt:.1f})")
        else:
            print(f"  {icon} {name:<20} {val} (target: {tgt})")
    print(f"\n  → Stato: {gl['current_status']}")
    print()


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Performance Tracker — MacroSignalTool")
    parser.add_argument("--report", action="store_true", help="Genera e stampa report completo")
    parser.add_argument("--json", action="store_true", help="Output report in JSON")
    args = parser.parse_args()

    from portfolio_manager import init_db
    init_db()

    if args.json:
        report = generate_report()
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        report = generate_report()
        print_report(report)
