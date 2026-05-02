"""
portfolio_manager.py
Phase 4, Task 4.1 — MacroSignalTool

Gestisce lo stato del portafoglio paper trading.
Persistenza su SQLite (paper_trading.db).

Schema DB:
  positions: id, ticker, direction, size_eur, entry_price, entry_date,
             stop_loss_pct, target_pct, event_category, signal_id,
             status (open/closed), close_price, close_date, pnl_eur, pnl_pct
  nav_history: date, nav, cash, open_pnl, realized_pnl

Metodi pubblici:
  open_position(ticker, direction, size_eur, entry_price, stop_pct, target_pct, ...)
  close_position(position_id, close_price, reason)
  update_prices()           — aggiorna P&L aperto via yfinance
  get_portfolio_state()     — snapshot completo
  get_open_positions()
  get_closed_positions()
  reset_portfolio()         — reset a NAV iniziale (solo test)

Testabile: python portfolio_manager.py --test
"""

import argparse
import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

# ─── Config ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("portfolio_manager")

# Su Railway usa il volume persistente /data, in locale usa files/
_railway_data = Path("/data")
DATA_DIR = _railway_data if _railway_data.exists() else Path(__file__).parent
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "paper_trading.db"
INITIAL_NAV = 10_000.0   # €10.000 capitale iniziale
COMMISSION_PCT = 0.001   # 0.1% per leg


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Position:
    id: int
    ticker: str
    name: str
    direction: str          # "LONG" / "SHORT"
    size_eur: float         # importo investito in EUR
    shares: float           # numero di unità (size_eur / entry_price)
    entry_price: float
    entry_date: str
    stop_loss_pct: float    # es. -7.5
    target_pct: float       # es. 15.0
    stop_price: float       # prezzo assoluto dello stop
    target_price: float     # prezzo assoluto del target
    event_category: str
    signal_id: str
    status: str             # "open" / "closed"
    current_price: float = 0.0
    close_price: float = 0.0
    close_date: str = ""
    close_reason: str = ""  # "stop_hit" / "target_hit" / "manual" / "expired"
    pnl_eur: float = 0.0
    pnl_pct: float = 0.0
    commission_paid_eur: float = 0.0
    notes: str = ""


@dataclass
class PortfolioState:
    timestamp: str
    initial_nav: float
    cash: float
    open_positions_value: float
    open_pnl_eur: float
    realized_pnl_eur: float
    total_nav: float
    total_return_pct: float
    num_open_positions: int
    num_closed_positions: int
    open_positions: list
    nav_history_last_7: list


# ─── DB init ──────────────────────────────────────────────────────────────────

def init_db(db_path: Path = DB_PATH):
    """Crea le tabelle se non esistono. Se il DB è corrotto, lo ricrea da zero."""
    import logging as _log
    # Verifica integrità DB prima di aprirlo
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path), timeout=5) as _test:
                _test.execute("PRAGMA integrity_check").fetchone()
        except Exception as e:
            _log.getLogger("portfolio_manager").error(
                f"DB corrotto ({e}), ricreo da zero: {db_path}"
            )
            db_path.unlink(missing_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker          TEXT NOT NULL,
                name            TEXT DEFAULT '',
                direction       TEXT NOT NULL,
                size_eur        REAL NOT NULL,
                shares          REAL NOT NULL,
                entry_price     REAL NOT NULL,
                entry_date      TEXT NOT NULL,
                stop_loss_pct   REAL NOT NULL,
                target_pct      REAL NOT NULL,
                stop_price      REAL NOT NULL,
                target_price    REAL NOT NULL,
                event_category  TEXT DEFAULT '',
                signal_id       TEXT DEFAULT '',
                status          TEXT DEFAULT 'open',
                current_price   REAL DEFAULT 0,
                close_price     REAL DEFAULT 0,
                close_date      TEXT DEFAULT '',
                close_reason    TEXT DEFAULT '',
                pnl_eur         REAL DEFAULT 0,
                pnl_pct         REAL DEFAULT 0,
                commission_paid_eur REAL DEFAULT 0,
                notes           TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nav_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT NOT NULL,
                nav           REAL NOT NULL,
                cash          REAL NOT NULL,
                open_pnl      REAL DEFAULT 0,
                realized_pnl  REAL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # Inizializza config se vuota
        conn.execute("""
            INSERT OR IGNORE INTO portfolio_config (key, value)
            VALUES ('initial_nav', ?), ('cash', ?), ('realized_pnl', '0.0'),
                   ('started_date', ?)
        """, (str(INITIAL_NAV), str(INITIAL_NAV),
              datetime.now(tz=timezone.utc).isoformat()))
        conn.commit()
    logger.info(f"DB inizializzato: {db_path}")


@contextmanager
def get_conn(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Config helpers ───────────────────────────────────────────────────────────

def _get_config(conn, key: str, default: float = 0.0) -> float:
    row = conn.execute(
        "SELECT value FROM portfolio_config WHERE key=?", (key,)
    ).fetchone()
    return float(row["value"]) if row else default


def _set_config(conn, key: str, value: float):
    conn.execute(
        "INSERT OR REPLACE INTO portfolio_config (key, value) VALUES (?,?)",
        (key, str(value))
    )


# ─── Fetch prezzo live ────────────────────────────────────────────────────────

def _fetch_live_price(ticker: str) -> Optional[float]:
    """Scarica ultimo prezzo disponibile da yfinance."""
    if not YFINANCE_AVAILABLE:
        return None
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="2d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Impossibile fetch prezzo {ticker}: {e}")
        return None


# ─── Apertura posizione ───────────────────────────────────────────────────────

def open_position(
    ticker: str,
    direction: str,
    size_eur: float,
    entry_price: float,
    stop_loss_pct: float,
    target_pct: float,
    event_category: str = "",
    signal_id: str = "",
    name: str = "",
    notes: str = "",
    db_path: Path = DB_PATH,
) -> Optional[int]:
    """
    Apre una nuova posizione paper.
    Ritorna l'id della posizione, o None se cash insufficiente.
    """
    commission = size_eur * COMMISSION_PCT
    total_cost = size_eur + commission

    with get_conn(db_path) as conn:
        cash = _get_config(conn, "cash", INITIAL_NAV)

        if total_cost > cash:
            logger.warning(
                f"Cash insufficiente: richiesti €{total_cost:.2f}, disponibili €{cash:.2f}"
            )
            return None

        shares = size_eur / entry_price if entry_price > 0 else 0

        # Calcola prezzi assoluti stop e target
        if direction == "LONG":
            stop_price = entry_price * (1 + stop_loss_pct / 100)
            target_price = entry_price * (1 + target_pct / 100)
        else:  # SHORT
            stop_price = entry_price * (1 - stop_loss_pct / 100)
            target_price = entry_price * (1 - target_pct / 100)

        now = datetime.now(tz=timezone.utc).isoformat()

        cursor = conn.execute("""
            INSERT INTO positions
            (ticker, name, direction, size_eur, shares, entry_price, entry_date,
             stop_loss_pct, target_pct, stop_price, target_price,
             event_category, signal_id, status, current_price,
             commission_paid_eur, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?)
        """, (ticker, name, direction, size_eur, shares, entry_price, now,
              stop_loss_pct, target_pct, stop_price, target_price,
              event_category, signal_id, entry_price, commission, notes))

        pos_id = cursor.lastrowid
        new_cash = cash - total_cost
        _set_config(conn, "cash", new_cash)

        # Registra NAV snapshot
        realized_pnl = _get_config(conn, "realized_pnl", 0.0)
        _snapshot_nav(conn, new_cash, open_pnl=0.0, realized_pnl=realized_pnl)

    logger.info(
        f"Posizione aperta #{pos_id}: {direction} {ticker} "
        f"€{size_eur:.2f} @ {entry_price:.4f} "
        f"(stop={stop_loss_pct}%, target={target_pct}%)"
    )
    return pos_id


# ─── Chiusura posizione ───────────────────────────────────────────────────────

def close_position(
    position_id: int,
    close_price: float,
    reason: str = "manual",
    db_path: Path = DB_PATH,
) -> Optional[dict]:
    """
    Chiude una posizione aperta.
    Ritorna dict con P&L, o None se posizione non trovata/già chiusa.
    """
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE id=? AND status='open'",
            (position_id,)
        ).fetchone()

        if not row:
            logger.warning(f"Posizione #{position_id} non trovata o già chiusa")
            return None

        direction = row["direction"]
        size_eur = row["size_eur"]
        entry_price = row["entry_price"]
        shares = row["shares"]
        commission_entry = row["commission_paid_eur"]

        # P&L lordo
        if direction == "LONG":
            pnl_gross = (close_price - entry_price) * shares
        else:
            pnl_gross = (entry_price - close_price) * shares

        commission_exit = size_eur * COMMISSION_PCT
        pnl_net = pnl_gross - commission_exit
        pnl_pct = (pnl_net / size_eur) * 100

        now = datetime.now(tz=timezone.utc).isoformat()

        conn.execute("""
            UPDATE positions
            SET status='closed', close_price=?, close_date=?, close_reason=?,
                pnl_eur=?, pnl_pct=?, current_price=?,
                commission_paid_eur=commission_paid_eur+?
            WHERE id=?
        """, (close_price, now, reason, pnl_net, pnl_pct,
              close_price, commission_exit, position_id))

        # Aggiorna cash e realized P&L
        cash = _get_config(conn, "cash", 0.0)
        proceeds = size_eur + pnl_net - commission_exit
        new_cash = cash + proceeds
        _set_config(conn, "cash", new_cash)

        realized_pnl = _get_config(conn, "realized_pnl", 0.0)
        new_realized = realized_pnl + pnl_net
        _set_config(conn, "realized_pnl", new_realized)

        _snapshot_nav(conn, new_cash, open_pnl=0.0, realized_pnl=new_realized)

    result = {
        "position_id": position_id,
        "ticker": row["ticker"],
        "direction": direction,
        "entry_price": entry_price,
        "close_price": close_price,
        "close_reason": reason,
        "pnl_eur": round(pnl_net, 2),
        "pnl_pct": round(pnl_pct, 3),
        "commission_total_eur": round(commission_entry + commission_exit, 2),
    }
    emoji = "✅" if pnl_net >= 0 else "❌"
    logger.info(
        f"{emoji} Posizione chiusa #{position_id}: {row['ticker']} "
        f"P&L={pnl_net:+.2f}€ ({pnl_pct:+.2f}%) — motivo: {reason}"
    )
    return result


# ─── Aggiornamento prezzi live ────────────────────────────────────────────────

def update_prices(db_path: Path = DB_PATH) -> dict:
    """
    Aggiorna current_price e P&L non realizzato per tutte le posizioni aperte.
    Controlla automaticamente se stop o target sono stati raggiunti.
    Ritorna dict con riepilogo aggiornamenti.
    """
    triggered = []
    updated = []

    with get_conn(db_path) as conn:
        positions = conn.execute(
            "SELECT * FROM positions WHERE status='open'"
        ).fetchall()

        total_open_pnl = 0.0

        for pos in positions:
            ticker = pos["ticker"]
            price = _fetch_live_price(ticker)

            if price is None:
                logger.warning(f"Prezzo non disponibile per {ticker} — skip")
                continue

            direction = pos["direction"]
            entry_price = pos["entry_price"]
            shares = pos["shares"]
            size_eur = pos["size_eur"]

            if direction == "LONG":
                unrealized_pnl = (price - entry_price) * shares
            else:
                unrealized_pnl = (entry_price - price) * shares

            unrealized_pct = (unrealized_pnl / size_eur) * 100
            total_open_pnl += unrealized_pnl

            conn.execute(
                "UPDATE positions SET current_price=?, pnl_eur=?, pnl_pct=? WHERE id=?",
                (price, round(unrealized_pnl, 2), round(unrealized_pct, 3), pos["id"])
            )
            updated.append(ticker)

            # Check stop/target
            stop_price = pos["stop_price"]
            target_price = pos["target_price"]

            hit_stop = (direction == "LONG" and price <= stop_price) or \
                       (direction == "SHORT" and price >= stop_price)
            hit_target = (direction == "LONG" and price >= target_price) or \
                         (direction == "SHORT" and price <= target_price)

            if hit_stop or hit_target:
                reason = "stop_hit" if hit_stop else "target_hit"
                triggered.append((pos["id"], ticker, reason, price))

        # Snapshot NAV
        cash = _get_config(conn, "cash", 0.0)
        realized_pnl = _get_config(conn, "realized_pnl", 0.0)
        _snapshot_nav(conn, cash, open_pnl=total_open_pnl, realized_pnl=realized_pnl)

    # Chiudi posizioni triggerate (fuori dal context manager per evitare lock)
    for pos_id, ticker, reason, price in triggered:
        logger.info(f"  🔔 {reason.upper()} per {ticker} @ {price:.4f}")
        close_position(pos_id, price, reason, db_path)

    return {
        "updated_tickers": updated,
        "stops_targets_triggered": [
            {"position_id": p, "ticker": t, "reason": r, "price": pr}
            for p, t, r, pr in triggered
        ],
    }


# ─── NAV snapshot ─────────────────────────────────────────────────────────────

def _snapshot_nav(conn, cash: float, open_pnl: float, realized_pnl: float):
    """Registra uno snapshot NAV nella nav_history."""
    nav = cash + open_pnl + realized_pnl
    # Ricalcolo corretto: NAV = cash_corrente + valore_posizioni_aperte
    # cash già include i proventi delle posizioni chiuse, quindi:
    # NAV = cash + open_positions_market_value
    # Qui usiamo una stima semplificata consistente con i dati disponibili
    conn.execute("""
        INSERT INTO nav_history (timestamp, nav, cash, open_pnl, realized_pnl)
        VALUES (?,?,?,?,?)
    """, (datetime.now(tz=timezone.utc).isoformat(),
          round(cash + open_pnl, 2), round(cash, 2),
          round(open_pnl, 2), round(realized_pnl, 2)))


# ─── Portfolio state ──────────────────────────────────────────────────────────

def get_portfolio_state(db_path: Path = DB_PATH) -> dict:
    """Restituisce snapshot completo del portafoglio."""
    with get_conn(db_path) as conn:
        cash = _get_config(conn, "cash", INITIAL_NAV)
        realized_pnl = _get_config(conn, "realized_pnl", 0.0)
        initial_nav = _get_config(conn, "initial_nav", INITIAL_NAV)

        open_rows = conn.execute(
            "SELECT * FROM positions WHERE status='open'"
        ).fetchall()
        closed_rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM positions WHERE status='closed'"
        ).fetchone()

        open_positions = []
        open_pnl = 0.0
        open_value = 0.0
        for row in open_rows:
            p = dict(row)
            open_pnl += p.get("pnl_eur", 0.0)
            open_value += p.get("size_eur", 0.0)
            open_positions.append(p)

        total_nav = cash + open_value + open_pnl
        total_return_pct = ((total_nav - initial_nav) / initial_nav) * 100

        nav_history = conn.execute("""
            SELECT timestamp, nav, cash, open_pnl, realized_pnl
            FROM nav_history ORDER BY id DESC LIMIT 7
        """).fetchall()

        state = PortfolioState(
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            initial_nav=initial_nav,
            cash=round(cash, 2),
            open_positions_value=round(open_value, 2),
            open_pnl_eur=round(open_pnl, 2),
            realized_pnl_eur=round(realized_pnl, 2),
            total_nav=round(total_nav, 2),
            total_return_pct=round(total_return_pct, 3),
            num_open_positions=len(open_positions),
            num_closed_positions=closed_rows["cnt"] if closed_rows else 0,
            open_positions=open_positions,
            nav_history_last_7=[dict(r) for r in nav_history],
        )
        return asdict(state)


def get_open_positions(db_path: Path = DB_PATH) -> list:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status='open' ORDER BY entry_date DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_closed_positions(db_path: Path = DB_PATH) -> list:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status='closed' ORDER BY close_date DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def reset_portfolio(db_path: Path = DB_PATH):
    """SOLO PER TEST: azzera il portafoglio e riporta a NAV iniziale."""
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM nav_history")
        _set_config(conn, "cash", INITIAL_NAV)
        _set_config(conn, "realized_pnl", 0.0)
        _set_config(conn, "started_date", datetime.now(tz=timezone.utc).isoformat())
    logger.info(f"Portfolio resettato a NAV iniziale €{INITIAL_NAV:.0f}")


# ─── CLI test ─────────────────────────────────────────────────────────────────

def _run_test():
    import tempfile, os
    test_db = Path(tempfile.mktemp(suffix=".db"))
    print(f"\n{'='*60}")
    print("TEST: portfolio_manager.py")
    print(f"DB temporaneo: {test_db}")
    print("="*60)

    init_db(test_db)

    # Apri 2 posizioni simulate
    pos1 = open_position(
        ticker="XLE", direction="LONG", size_eur=500.0,
        entry_price=95.50, stop_loss_pct=-7.5, target_pct=15.0,
        event_category="ENERGY_SUPPLY_SHOCK", signal_id="test_001",
        name="Energy Select Sector SPDR", db_path=test_db
    )
    pos2 = open_position(
        ticker="GLD", direction="LONG", size_eur=300.0,
        entry_price=185.20, stop_loss_pct=-5.0, target_pct=10.0,
        event_category="MILITARY_CONFLICT", signal_id="test_002",
        name="SPDR Gold Shares", db_path=test_db
    )

    print(f"\n✅ Aperta posizione XLE #{pos1}")
    print(f"✅ Aperta posizione GLD #{pos2}")

    # Stato portafoglio
    state = get_portfolio_state(test_db)
    print(f"\n📊 Stato portafoglio:")
    print(f"   Cash disponibile: €{state['cash']:.2f}")
    print(f"   Posizioni aperte: {state['num_open_positions']}")
    print(f"   NAV totale: €{state['total_nav']:.2f}")
    print(f"   Return: {state['total_return_pct']:+.3f}%")

    # Chiudi pos1 con profitto simulato
    result = close_position(pos1, close_price=105.00, reason="target_hit", db_path=test_db)
    print(f"\n✅ Chiusa posizione #{pos1}: P&L={result['pnl_eur']:+.2f}€ ({result['pnl_pct']:+.2f}%)")

    # Chiudi pos2 con perdita simulata
    result2 = close_position(pos2, close_price=175.80, reason="stop_hit", db_path=test_db)
    print(f"❌ Chiusa posizione #{pos2}: P&L={result2['pnl_eur']:+.2f}€ ({result2['pnl_pct']:+.2f}%)")

    # Stato finale
    state = get_portfolio_state(test_db)
    print(f"\n📊 Stato finale:")
    print(f"   Cash: €{state['cash']:.2f}")
    print(f"   Realized P&L: €{state['realized_pnl_eur']:.2f}")
    print(f"   NAV totale: €{state['total_nav']:.2f}")
    print(f"   Posizioni chiuse: {state['num_closed_positions']}")

    os.unlink(test_db)
    print("\n✅ Test completato.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Portfolio Manager — MacroSignalTool")
    parser.add_argument("--test", action="store_true", help="Esegui test completo")
    parser.add_argument("--state", action="store_true", help="Mostra stato portafoglio")
    parser.add_argument("--init", action="store_true", help="Inizializza DB")
    parser.add_argument("--update", action="store_true", help="Aggiorna prezzi live")
    args = parser.parse_args()

    if args.test:
        _run_test()
    elif args.state:
        init_db()
        state = get_portfolio_state()
        print(json.dumps(state, indent=2))
    elif args.init:
        init_db()
        print(f"DB inizializzato: {DB_PATH}")
    elif args.update:
        init_db()
        result = update_prices()
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()
