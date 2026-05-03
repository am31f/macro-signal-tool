"""
paper_executor.py
Phase 4, Task 4.2 — MacroSignalTool

Esegue i trade in paper trading combinando:
  - TradeStructure (da trade_structurer.py)
  - SizingResult (da position_sizer.py)
  - PortfolioManager (da portfolio_manager.py)

Funzioni principali:
  execute_signal(signal, trade_structure, sizing_result) → apre posizioni paper
  check_all_stops_and_targets()                          → controlla tutte le posizioni aperte
  generate_trade_journal_entry(position_id)             → genera entry journal per trade chiuso

Il journal entry viene generato con Claude API per produrre una sintesi
della lesson learned in modo strutturato.

Testabile: python paper_executor.py --test
"""

import argparse
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import anthropic
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

import sys
sys.path.insert(0, str(Path(__file__).parent))
from portfolio_manager import (
    DB_PATH, init_db, open_position, close_position,
    update_prices, get_portfolio_state, get_closed_positions
)

# ─── Config ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("paper_executor")

MODEL = "claude-haiku-4-5-20251001"   # Haiku per journal (più economico)
DATA_DIR = Path(__file__).parent
JOURNAL_PATH = DATA_DIR / "trade_journal.json"


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    """Risultato dell'esecuzione di un segnale in paper trading."""
    signal_id: str
    headline: str
    event_category: str
    trade_type: str
    executed_at: str
    positions_opened: list      # lista di {position_id, ticker, direction, size_eur}
    positions_skipped: list     # ticker skippati (NO_TRADE o cash insufficiente)
    total_capital_deployed_eur: float
    sizing_summary: dict
    notes: str = ""


@dataclass
class JournalEntry:
    """Entry del trade journal per un trade completato."""
    position_id: int
    ticker: str
    direction: str
    event_category: str
    signal_id: str
    # Trade facts
    entry_price: float
    close_price: float
    entry_date: str
    close_date: str
    close_reason: str
    pnl_eur: float
    pnl_pct: float
    holding_days: float
    # AI-generated insight
    signal_source_headline: str
    causal_chain: str
    what_happened: str          # descrizione outcome
    lesson_learned: str         # generata da Claude
    verdict: str                # "WIN" / "LOSS" / "BREAKEVEN"
    generated_at: str


# ─── Executor principale ──────────────────────────────────────────────────────

def execute_signal(
    signal: dict,
    trade_structure: dict,
    sizing_result: dict,
    db_path: Path = DB_PATH,
) -> ExecutionResult:
    """
    Esegue un segnale in paper trading.
    Apre una posizione per ogni strumento nel trade, scalando il size
    proporzionalmente ai weight_pct definiti in trade_structure.
    """
    signal_id = signal.get("news_id", "")
    headline = signal.get("headline", "")
    category = signal.get("event_category", "")
    trade_type = trade_structure.get("trade_type", "directional")
    executed_at = datetime.now(tz=timezone.utc).isoformat()

    # Controllo NO_TRADE
    if trade_type == "NO_TRADE":
        reason = trade_structure.get("no_trade_reason", "")
        logger.info(f"NO_TRADE per '{headline[:50]}': {reason}")
        return ExecutionResult(
            signal_id=signal_id, headline=headline,
            event_category=category, trade_type="NO_TRADE",
            executed_at=executed_at,
            positions_opened=[], positions_skipped=[],
            total_capital_deployed_eur=0.0,
            sizing_summary=sizing_result,
            notes=f"NO_TRADE: {reason}",
        )

    # Controllo Kelly NO_TRADE
    kelly_quality = sizing_result.get("kelly_quality", "MODERATE")
    if kelly_quality == "NO_TRADE":
        logger.info(f"Sizing NO_TRADE per '{headline[:50]}': kelly insufficiente")
        return ExecutionResult(
            signal_id=signal_id, headline=headline,
            event_category=category, trade_type="NO_TRADE",
            executed_at=executed_at,
            positions_opened=[], positions_skipped=[],
            total_capital_deployed_eur=0.0,
            sizing_result=sizing_result,
            notes=f"Kelly quality=NO_TRADE: {sizing_result.get('sizing_rationale','')}",
        )

    total_size_eur = sizing_result.get("position_size_eur", 0.0)
    if total_size_eur <= 0:
        logger.warning("Position size EUR = 0 — niente da eseguire")
        return ExecutionResult(
            signal_id=signal_id, headline=headline,
            event_category=category, trade_type=trade_type,
            executed_at=executed_at,
            positions_opened=[], positions_skipped=[],
            total_capital_deployed_eur=0.0,
            sizing_summary=sizing_result,
            notes="Size EUR = 0",
        )

    instruments = trade_structure.get("instruments", [])
    stop_loss_pct = trade_structure.get("stop_loss_pct") or -7.5
    target_pct = trade_structure.get("target_pct") or 15.0

    positions_opened = []
    positions_skipped = []
    total_deployed = 0.0

    init_db(db_path)

    # ── Controllo duplicati ────────────────────────────────────────────────────
    # 1) Stesso signal_id già eseguito (stessa notizia, esecuzione multipla)
    from portfolio_manager import get_open_positions
    open_pos = get_open_positions(db_path)

    if signal_id:
        already_executed = [p for p in open_pos if p.get("signal_id") == signal_id]
        if already_executed:
            logger.info(
                f"⚠️  Segnale '{signal_id}' già eseguito ({len(already_executed)} posizioni aperte) — skip duplicato"
            )
            return ExecutionResult(
                signal_id=signal_id, headline=headline,
                event_category=category, trade_type="DUPLICATE",
                executed_at=executed_at,
                positions_opened=[], positions_skipped=[],
                total_capital_deployed_eur=0.0,
                sizing_summary=sizing_result,
                notes=f"DUPLICATE: signal_id '{signal_id}' già in portafoglio",
            )

    # 2) Stesso ticker+direzione già aperto (notizie diverse, stessa esposizione)
    open_tickers = {
        (p.get("ticker", ""), p.get("direction", "")): p
        for p in open_pos
    }

    for inst in instruments:
        ticker = inst.get("ticker", "")
        direction = inst.get("direction", "LONG")
        weight = inst.get("weight_pct", 0.0) / 100.0
        name = inst.get("name", "")
        inst_type = inst.get("instrument_type", "ETF")

        if weight <= 0 or not ticker:
            positions_skipped.append({"ticker": ticker, "reason": "weight=0 o ticker vuoto"})
            continue

        # Opzioni: non apriamo posizioni su strumenti opzioni in paper
        # (troppo complesso senza pricing model) — registriamo come skip con nota
        if "option" in inst_type.lower():
            positions_skipped.append({
                "ticker": ticker,
                "reason": f"Strumento option ({inst_type}) — paper trading supporta solo ETF/stock/future",
                "note": f"Strike hint: {inst.get('option_strike_hint','')}, Expiry: {inst.get('option_expiry_hint','')}",
            })
            logger.info(f"  ⚠️ Skip opzione {ticker} — non supportata in paper trading")
            continue

        # Stesso ticker+direzione già aperto da un'altra notizia → skip
        existing = open_tickers.get((ticker, direction))
        if existing:
            positions_skipped.append({
                "ticker": ticker,
                "reason": (
                    f"Esposizione duplicata: {direction} {ticker} già aperta "
                    f"(signal_id={existing.get('signal_id','?')}, "
                    f"entry={existing.get('entry_price','?')})"
                ),
            })
            logger.info(
                f"  ⚠️ Skip {direction} {ticker} — esposizione già presente "
                f"da signal '{existing.get('signal_id','?')}'"
            )
            continue

        inst_size_eur = total_size_eur * weight

        # Fetch prezzo corrente per entry
        from portfolio_manager import _fetch_live_price
        entry_price = _fetch_live_price(ticker)
        if entry_price is None:
            # Fallback: usa prezzo dummy per test
            entry_price = 100.0
            logger.warning(f"  Prezzo non disponibile per {ticker} — uso dummy 100.0")

        pos_id = open_position(
            ticker=ticker,
            direction=direction,
            size_eur=inst_size_eur,
            entry_price=entry_price,
            stop_loss_pct=stop_loss_pct,   # negativo es. -8.0 → open_position calcola stop correttamente
            target_pct=target_pct,
            event_category=category,
            signal_id=signal_id,
            name=name,
            notes=f"Trade type: {trade_type} | Rationale: {inst.get('rationale','')[:100]}",
            db_path=db_path,
        )

        if pos_id:
            positions_opened.append({
                "position_id": pos_id,
                "ticker": ticker,
                "direction": direction,
                "size_eur": round(inst_size_eur, 2),
                "entry_price": entry_price,
                "weight_pct": inst.get("weight_pct", 0),
            })
            total_deployed += inst_size_eur
            logger.info(
                f"  ✅ Eseguito: {direction} {ticker} "
                f"€{inst_size_eur:.2f} @ {entry_price:.4f} "
                f"(weight={inst.get('weight_pct',0)}%)"
            )
        else:
            positions_skipped.append({"ticker": ticker, "reason": "cash insufficiente"})

    result = ExecutionResult(
        signal_id=signal_id,
        headline=headline,
        event_category=category,
        trade_type=trade_type,
        executed_at=executed_at,
        positions_opened=positions_opened,
        positions_skipped=positions_skipped,
        total_capital_deployed_eur=round(total_deployed, 2),
        sizing_summary={
            "kelly_quality": kelly_quality,
            "position_size_pct": sizing_result.get("position_size_pct"),
            "position_size_eur": total_size_eur,
            "vix_at_execution": sizing_result.get("current_vix"),
            "conviction_pct": trade_structure.get("conviction_pct"),
        },
        notes=trade_structure.get("position_notes", ""),
    )

    logger.info(
        f"Esecuzione completata: {len(positions_opened)} posizioni aperte, "
        f"{len(positions_skipped)} skippate. "
        f"Capitale deployato: €{total_deployed:.2f}"
    )
    return result


# ─── Check stops/targets ──────────────────────────────────────────────────────

def check_all_stops_and_targets(db_path: Path = DB_PATH) -> dict:
    """
    Wrapper di update_prices() — aggiorna prezzi e controlla stop/target.
    Da chiamare periodicamente (es. ogni 15 min durante orario di mercato).
    """
    logger.info("Aggiornamento prezzi e check stop/target...")
    result = update_prices(db_path)
    triggered = result.get("stops_targets_triggered", [])

    if triggered:
        logger.info(f"  🔔 {len(triggered)} stop/target raggiunti:")
        for t in triggered:
            logger.info(f"     {t['reason'].upper()}: {t['ticker']} @ {t['price']:.4f}")
        # Genera journal entry per posizioni chiuse automaticamente
        for t in triggered:
            _auto_journal(t["position_id"], db_path)
    else:
        logger.info("  Nessuno stop/target raggiunto.")

    return result


# ─── Trade journal ─────────────────────────────────────────────────────────────

def _auto_journal(position_id: int, db_path: Path = DB_PATH):
    """Tenta di generare journal entry automaticamente. Non blocca se fallisce."""
    try:
        entry = generate_trade_journal_entry(position_id, db_path)
        if entry:
            _save_journal_entry(entry)
    except Exception as e:
        logger.warning(f"Journal entry non generata per #{position_id}: {e}")


def generate_trade_journal_entry(
    position_id: int,
    db_path: Path = DB_PATH,
) -> Optional[JournalEntry]:
    """
    Genera entry del trade journal per una posizione chiusa.
    Usa Claude Haiku per generare lesson_learned.
    """
    from portfolio_manager import get_conn
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE id=? AND status='closed'",
            (position_id,)
        ).fetchone()

    if not row:
        logger.warning(f"Posizione #{position_id} non trovata o non chiusa")
        return None

    row = dict(row)

    # Calcola holding days
    try:
        entry_dt = datetime.fromisoformat(row["entry_date"].replace("Z", "+00:00"))
        close_dt = datetime.fromisoformat(row["close_date"].replace("Z", "+00:00"))
        holding_days = (close_dt - entry_dt).total_seconds() / 86400
    except Exception:
        holding_days = 0.0

    verdict = "WIN" if row["pnl_eur"] > 0 else ("BREAKEVEN" if row["pnl_eur"] == 0 else "LOSS")

    # Genera lesson_learned con Claude
    lesson = _generate_lesson(row, verdict, holding_days)

    what_happened = (
        f"Posizione {row['direction']} {row['ticker']} "
        f"aperta @ {row['entry_price']:.4f}, "
        f"chiusa @ {row['close_price']:.4f} "
        f"({row['close_reason']}). "
        f"P&L: {row['pnl_eur']:+.2f}€ ({row['pnl_pct']:+.2f}%) "
        f"in {holding_days:.1f} giorni."
    )

    entry = JournalEntry(
        position_id=position_id,
        ticker=row["ticker"],
        direction=row["direction"],
        event_category=row["event_category"],
        signal_id=row["signal_id"],
        entry_price=row["entry_price"],
        close_price=row["close_price"],
        entry_date=row["entry_date"],
        close_date=row["close_date"],
        close_reason=row["close_reason"],
        pnl_eur=round(row["pnl_eur"], 2),
        pnl_pct=round(row["pnl_pct"], 3),
        holding_days=round(holding_days, 1),
        signal_source_headline=row.get("notes", "")[:200],
        causal_chain="",
        what_happened=what_happened,
        lesson_learned=lesson,
        verdict=verdict,
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
    )
    return entry


def _generate_lesson(position_data: dict, verdict: str, holding_days: float) -> str:
    """Chiama Claude Haiku per generare una lesson learned breve."""
    if not ANTHROPIC_AVAILABLE:
        return f"[Auto] Trade {verdict} su {position_data['ticker']} in {holding_days:.1f} giorni. Analisi manuale richiesta."

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return f"[Auto] {verdict}: {position_data['ticker']} P&L {position_data['pnl_pct']:+.2f}% in {holding_days:.1f}d."

    try:
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""Trade completato:
- Ticker: {position_data['ticker']} {position_data['direction']}
- Categoria evento: {position_data['event_category']}
- Entry: {position_data['entry_price']:.4f} → Close: {position_data['close_price']:.4f}
- Motivo chiusura: {position_data['close_reason']}
- P&L: {position_data['pnl_eur']:+.2f}€ ({position_data['pnl_pct']:+.2f}%)
- Holding: {holding_days:.1f} giorni
- Esito: {verdict}
- Note: {position_data.get('notes','')[:150]}

In 2 frasi max: cosa ha funzionato/fallito e cosa applicare ai trade futuri sullo stesso tipo di evento?"""

        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Claude journal generation failed: {e}")
        return f"[Auto] {verdict}: {position_data['ticker']} in {holding_days:.1f}d. P&L {position_data['pnl_pct']:+.2f}%."


def _save_journal_entry(entry: JournalEntry):
    """Salva l'entry nel file trade_journal.json."""
    journal = []
    if JOURNAL_PATH.exists():
        try:
            with open(JOURNAL_PATH, encoding="utf-8") as f:
                journal = json.load(f)
        except Exception:
            journal = []

    journal.append(asdict(entry))
    with open(JOURNAL_PATH, "w", encoding="utf-8") as f:
        json.dump(journal, f, indent=2, ensure_ascii=False)
    logger.info(f"Journal entry salvata per posizione #{entry.position_id}")


def get_journal(limit: int = 20) -> list:
    """Restituisce le ultime N entry del journal."""
    if not JOURNAL_PATH.exists():
        return []
    try:
        with open(JOURNAL_PATH, encoding="utf-8") as f:
            journal = json.load(f)
        return journal[-limit:]
    except Exception:
        return []


# ─── CLI test ─────────────────────────────────────────────────────────────────

_TEST_SIGNAL = {
    "news_id": "test_exec_001",
    "headline": "Iran closes Strait of Hormuz — oil supply disrupted",
    "event_category": "ENERGY_SUPPLY_SHOCK",
    "materiality_score": 0.92,
    "novelty_score": 0.88,
    "causal_chain": "Hormuz chiusura → riduzione offerta → Brent +30%",
    "entry_timing": "T+1",
    "confidence_composite": 0.81,
}

_TEST_TRADE = {
    "trade_type": "directional",
    "primary_thesis": "Long energia su shock offerta Hormuz",
    "instruments": [
        {"ticker": "XLE", "name": "Energy Select SPDR", "direction": "LONG",
         "instrument_type": "ETF", "weight_pct": 60,
         "rationale": "Esposizione diversificata al settore energia US"},
        {"ticker": "GLD", "name": "SPDR Gold Shares", "direction": "LONG",
         "instrument_type": "ETF", "weight_pct": 40,
         "rationale": "Safe haven + inflation hedge"},
    ],
    "stop_loss_pct": -7.5,
    "target_pct": 15.0,
    "conviction_pct": 82,
    "position_notes": "Non aprire nelle prime 2 ore. Ridurre se Brent > +40% già."
}

_TEST_SIZING = {
    "kelly_quality": "STRONG",
    "position_size_pct": 4.2,
    "position_size_eur": 420.0,
    "current_vix": 22.5,
    "sizing_rationale": "Half-Kelly ENERGY_SUPPLY_SHOCK con VIX normale",
}


def _run_test():
    import tempfile, os
    test_db = Path(tempfile.mktemp(suffix=".db"))
    print(f"\n{'='*60}")
    print("TEST: paper_executor.py")
    print(f"DB temporaneo: {test_db}")
    print("="*60)

    init_db(test_db)

    # Esegui il segnale in paper
    result = execute_signal(_TEST_SIGNAL, _TEST_TRADE, _TEST_SIZING, db_path=test_db)
    result_dict = asdict(result)

    print(f"\n📋 Risultato esecuzione:")
    print(f"   Trade type: {result_dict['trade_type']}")
    print(f"   Posizioni aperte: {len(result_dict['positions_opened'])}")
    print(f"   Capitale deployato: €{result_dict['total_capital_deployed_eur']:.2f}")
    for pos in result_dict["positions_opened"]:
        print(f"   ✅ {pos['direction']} {pos['ticker']}: €{pos['size_eur']:.2f} @ {pos['entry_price']:.4f}")
    for skip in result_dict["positions_skipped"]:
        print(f"   ⚠️ Skip {skip['ticker']}: {skip['reason']}")

    # Stato portafoglio
    state = get_portfolio_state(test_db)
    print(f"\n📊 Portfolio state:")
    print(f"   Cash: €{state['cash']:.2f}")
    print(f"   Open positions: {state['num_open_positions']}")
    print(f"   NAV: €{state['total_nav']:.2f}")

    # Simula chiusura manuale con profitto
    if result_dict["positions_opened"]:
        pos_id = result_dict["positions_opened"][0]["position_id"]
        close_result = close_position(pos_id, close_price=102.50, reason="manual", db_path=test_db)
        print(f"\n✅ Chiusura manuale #{pos_id}: P&L={close_result['pnl_eur']:+.2f}€")

        # Journal entry (senza API key → auto-generated)
        entry = generate_trade_journal_entry(pos_id, test_db)
        if entry:
            print(f"\n📓 Journal entry:")
            print(f"   Verdict: {entry.verdict}")
            print(f"   What happened: {entry.what_happened}")
            print(f"   Lesson: {entry.lesson_learned}")

    os.unlink(test_db)
    print("\n✅ Test completato.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paper Executor — MacroSignalTool")
    parser.add_argument("--test", action="store_true", help="Test esecuzione segnale paper")
    parser.add_argument("--check", action="store_true", help="Controlla stop/target posizioni aperte")
    parser.add_argument("--journal", action="store_true", help="Mostra trade journal")
    args = parser.parse_args()

    if args.test:
        _run_test()
    elif args.check:
        init_db()
        result = check_all_stops_and_targets()
        print(json.dumps(result, indent=2))
    elif args.journal:
        journal = get_journal()
        print(json.dumps(journal, indent=2, ensure_ascii=False))
    else:
        parser.print_help()
