"""
email_digest.py — MacroSignalTool Phase 6.2
============================================
Daily digest email HTML inviata via SMTP Gmail alle 8:00.
Contiene:
  - Top 3 news classificate (più recenti con categoria non NONE)
  - Segnali attivi in cache
  - Performance settimanale (P&L, win rate, posizioni aperte)

Utilizzo standalone (test):
    python email_digest.py --test

Integrazione con main.py (APScheduler):
    from email_digest import send_daily_digest
    scheduler.add_job(send_daily_digest, 'cron', hour=8, minute=0)

Configurazione .env:
    GMAIL_USER=tuoindirizzo@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx  (App Password Gmail, non password normale)
    DIGEST_RECIPIENT=destinatario@email.com  (default: stesso di GMAIL_USER)
"""

import os
import json
import smtplib
import sqlite3
import logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Config da .env ────────────────────────────────────────────────────────────
GMAIL_USER        = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
DIGEST_RECIPIENT  = os.getenv("DIGEST_RECIPIENT", GMAIL_USER)
DB_PATH           = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "paper_trading.db"))
SIGNALS_CACHE     = os.path.join(os.path.dirname(__file__), "signals_cache.json")
NEWS_DB           = os.path.join(os.path.dirname(__file__), "news_cache.db")

# ── Colori per categorie ──────────────────────────────────────────────────────
CATEGORY_COLORS = {
    "ENERGY_SUPPLY_SHOCK":       "#f97316",
    "MILITARY_CONFLICT":         "#ef4444",
    "SANCTIONS_IMPOSED":         "#a855f7",
    "CENTRAL_BANK_SURPRISE":     "#3b82f6",
    "TRADE_WAR_TARIFF":          "#eab308",
    "CYBER_ATTACK":              "#06b6d4",
    "SOVEREIGN_CRISIS":          "#f43f5e",
    "COMMODITY_SUPPLY_AGRI":     "#22c55e",
    "NUCLEAR_THREAT":            "#dc2626",
    "ELECTION_SURPRISE":         "#6366f1",
    "PANDEMIC_HEALTH":           "#14b8a6",
    "INFRASTRUCTURE_DISRUPTION": "#f59e0b",
}

CATEGORY_EMOJI = {
    "ENERGY_SUPPLY_SHOCK":       "🛢️",
    "MILITARY_CONFLICT":         "⚔️",
    "SANCTIONS_IMPOSED":         "🚫",
    "CENTRAL_BANK_SURPRISE":     "🏦",
    "TRADE_WAR_TARIFF":          "📦",
    "CYBER_ATTACK":              "💻",
    "SOVEREIGN_CRISIS":          "💸",
    "COMMODITY_SUPPLY_AGRI":     "🌾",
    "NUCLEAR_THREAT":            "☢️",
    "ELECTION_SURPRISE":         "🗳️",
    "PANDEMIC_HEALTH":           "🦠",
    "INFRASTRUCTURE_DISRUPTION": "🏗️",
}


# ── Data fetchers ─────────────────────────────────────────────────────────────
def _get_top_news(limit: int = 3) -> list[dict]:
    """Recupera le ultime news classificate con categoria != NONE dal DB."""
    if not os.path.exists(NEWS_DB):
        return []
    try:
        conn = sqlite3.connect(NEWS_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Prova prima nella tabella classified, poi in news
        try:
            rows = cur.execute("""
                SELECT title, source, published_at, event_category, materiality_score,
                       novelty_score, causal_chain, url
                FROM news
                WHERE event_category IS NOT NULL
                  AND event_category != 'NONE'
                ORDER BY published_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        except Exception:
            rows = []
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"_get_top_news error: {e}")
        return []


def _get_signals_cache() -> list[dict]:
    """Recupera i segnali dalla cache JSON."""
    if not os.path.exists(SIGNALS_CACHE):
        return []
    try:
        with open(SIGNALS_CACHE) as f:
            data = json.load(f)
        return data.get("signals", [])[:5]
    except Exception as e:
        logger.warning(f"_get_signals_cache error: {e}")
        return []


def _get_portfolio_state() -> dict:
    """Recupera stato portafoglio paper dal DB SQLite."""
    if not os.path.exists(DB_PATH):
        return {}
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from portfolio_manager import PortfolioManager
        pm = PortfolioManager(DB_PATH)
        return pm.get_portfolio_state()
    except Exception as e:
        logger.warning(f"_get_portfolio_state error: {e}")
        return {}


def _get_weekly_performance() -> dict:
    """Recupera metriche di performance dell'ultima settimana."""
    if not os.path.exists(DB_PATH):
        return {}
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        rows = cur.execute("""
            SELECT pnl_eur, pnl_pct, verdict
            FROM positions
            WHERE status = 'closed' AND close_date >= ?
        """, (week_ago,)).fetchall()
        conn.close()

        if not rows:
            return {"weekly_trades": 0}

        wins   = sum(1 for r in rows if r["verdict"] == "WIN")
        losses = sum(1 for r in rows if r["verdict"] == "LOSS")
        total_pnl = sum(r["pnl_eur"] for r in rows)
        wr = wins / len(rows) if rows else 0

        return {
            "weekly_trades": len(rows),
            "weekly_wins":   wins,
            "weekly_losses": losses,
            "weekly_pnl":    total_pnl,
            "weekly_win_rate": wr,
        }
    except Exception as e:
        logger.warning(f"_get_weekly_performance error: {e}")
        return {}


# ── HTML builder ──────────────────────────────────────────────────────────────
def _build_html(news: list, signals: list, portfolio: dict, weekly: dict) -> str:
    """Costruisce l'email HTML del digest giornaliero."""

    today = datetime.now().strftime("%A %d %B %Y")
    nav   = portfolio.get("total_nav", 10000)
    open_pnl  = portfolio.get("open_pnl_eur", 0)
    real_pnl  = portfolio.get("realized_pnl_eur", 0)
    num_open  = portfolio.get("num_open_positions", 0)
    ret_pct   = portfolio.get("total_return_pct", 0)

    weekly_trades = weekly.get("weekly_trades", 0)
    weekly_pnl    = weekly.get("weekly_pnl", 0)
    weekly_wr     = weekly.get("weekly_win_rate", 0)
    weekly_wins   = weekly.get("weekly_wins", 0)
    weekly_losses = weekly.get("weekly_losses", 0)

    pnl_color   = "#22c55e" if real_pnl >= 0 else "#ef4444"
    opnl_color  = "#22c55e" if open_pnl >= 0 else "#ef4444"
    ret_color   = "#22c55e" if ret_pct >= 0 else "#ef4444"
    wpnl_color  = "#22c55e" if weekly_pnl >= 0 else "#ef4444"
    pnl_sign    = "+" if real_pnl >= 0 else ""
    opnl_sign   = "+" if open_pnl >= 0 else ""
    ret_sign    = "+" if ret_pct >= 0 else ""
    wpnl_sign   = "+" if weekly_pnl >= 0 else ""

    # ── News section ──
    news_html = ""
    if not news:
        news_html = '<p style="color:#94a3b8;font-style:italic;">Nessuna news classificata nelle ultime 24h.</p>'
    else:
        for item in news:
            cat   = item.get("event_category", "UNKNOWN")
            color = CATEGORY_COLORS.get(cat, "#64748b")
            emoji = CATEGORY_EMOJI.get(cat, "📰")
            mat   = int(item.get("materiality_score", 0) * 100)
            title = item.get("title", "")
            chain = item.get("causal_chain", "")
            source = item.get("source", "")
            url    = item.get("url", "#")
            cat_label = cat.replace("_", " ")
            news_html += f"""
            <div style="margin-bottom:16px;padding:14px;background:#1e293b;border-radius:10px;border-left:4px solid {color};">
              <div style="margin-bottom:6px;">
                <span style="background:{color}22;color:{color};font-size:11px;padding:2px 8px;border-radius:20px;font-weight:600;">
                  {emoji} {cat_label}
                </span>
                <span style="color:#64748b;font-size:11px;margin-left:8px;">{source} · Materiality: {mat}%</span>
              </div>
              <p style="margin:0 0 6px 0;color:#e2e8f0;font-size:14px;font-weight:500;">
                <a href="{url}" style="color:#e2e8f0;text-decoration:none;">{title}</a>
              </p>
              {f'<p style="margin:0;color:#94a3b8;font-size:12px;font-style:italic;">{chain}</p>' if chain else ''}
            </div>
            """

    # ── Signals section ──
    signals_html = ""
    if not signals:
        signals_html = '<p style="color:#94a3b8;font-style:italic;">Nessun segnale attivo in cache.</p>'
    else:
        for s in signals:
            conf  = int(s.get("confidence_composite", 0) * 100)
            cat   = s.get("event_category", "UNKNOWN")
            color = CATEGORY_COLORS.get(cat, "#64748b")
            emoji = CATEGORY_EMOJI.get(cat, "📊")
            kelly = s.get("kelly_quality", "–")
            size  = s.get("position_size_eur", 0)
            timing = s.get("entry_timing", "–")
            headline = s.get("headline", "")[:150]
            conf_color = "#22c55e" if conf >= 75 else "#eab308" if conf >= 55 else "#ef4444"

            signals_html += f"""
            <div style="margin-bottom:12px;padding:12px;background:#1e293b;border-radius:10px;border:1px solid #334155;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                <span style="background:{color}22;color:{color};font-size:11px;padding:2px 8px;border-radius:20px;font-weight:600;">
                  {emoji} {cat.replace('_', ' ')}
                </span>
                <span style="font-weight:700;font-size:16px;color:{conf_color};">{conf}%</span>
              </div>
              <p style="margin:0 0 6px 0;color:#e2e8f0;font-size:13px;">{headline}</p>
              <div style="font-size:11px;color:#64748b;">
                Kelly: <b style="color:#e2e8f0;">{kelly}</b> &nbsp;·&nbsp;
                Size: <b style="color:#38bdf8;">€{size:.0f}</b> &nbsp;·&nbsp;
                Timing: <b style="color:#e2e8f0;">{timing}</b>
              </div>
            </div>
            """

    # ── Composizione HTML finale ──
    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MacroSignalTool Daily Digest — {today}</title>
</head>
<body style="margin:0;padding:0;background:#0f172a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#e2e8f0;">
  <div style="max-width:600px;margin:0 auto;padding:24px 16px;">

    <!-- Header -->
    <div style="text-align:center;padding:24px 0 20px;border-bottom:1px solid #1e293b;margin-bottom:24px;">
      <h1 style="margin:0;color:#38bdf8;font-size:22px;font-weight:700;">⚡ MacroSignalTool</h1>
      <p style="margin:4px 0 0;color:#64748b;font-size:13px;">Daily Digest — {today}</p>
    </div>

    <!-- Portfolio snapshot -->
    <div style="margin-bottom:24px;">
      <h2 style="margin:0 0 12px;font-size:14px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">
        Portafoglio Paper
      </h2>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div style="background:#1e293b;border-radius:10px;padding:14px;text-align:center;">
          <p style="margin:0 0 4px;font-size:11px;color:#64748b;text-transform:uppercase;">NAV</p>
          <p style="margin:0;font-size:22px;font-weight:700;color:{ret_color};">€{nav:.0f}</p>
          <p style="margin:2px 0 0;font-size:12px;color:{ret_color};">{ret_sign}{ret_pct:.2f}%</p>
        </div>
        <div style="background:#1e293b;border-radius:10px;padding:14px;text-align:center;">
          <p style="margin:0 0 4px;font-size:11px;color:#64748b;text-transform:uppercase;">P&L Realizzato</p>
          <p style="margin:0;font-size:22px;font-weight:700;color:{pnl_color};">{pnl_sign}€{real_pnl:.2f}</p>
          <p style="margin:2px 0 0;font-size:12px;color:#64748b;">{num_open} posizioni aperte</p>
        </div>
        <div style="background:#1e293b;border-radius:10px;padding:14px;text-align:center;">
          <p style="margin:0 0 4px;font-size:11px;color:#64748b;text-transform:uppercase;">P&L Aperto</p>
          <p style="margin:0;font-size:18px;font-weight:700;color:{opnl_color};">{opnl_sign}€{open_pnl:.2f}</p>
        </div>
        <div style="background:#1e293b;border-radius:10px;padding:14px;text-align:center;">
          <p style="margin:0 0 4px;font-size:11px;color:#64748b;text-transform:uppercase;">Settimana</p>
          <p style="margin:0;font-size:18px;font-weight:700;color:{wpnl_color};">{wpnl_sign}€{weekly_pnl:.2f}</p>
          <p style="margin:2px 0 0;font-size:12px;color:#64748b;">{weekly_trades} trade · WR {int(weekly_wr*100)}%</p>
        </div>
      </div>
    </div>

    <!-- Top news -->
    <div style="margin-bottom:24px;">
      <h2 style="margin:0 0 12px;font-size:14px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">
        📰 Top News Classificate
      </h2>
      {news_html}
    </div>

    <!-- Segnali attivi -->
    <div style="margin-bottom:24px;">
      <h2 style="margin:0 0 12px;font-size:14px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em;">
        ⚡ Segnali Attivi
      </h2>
      {signals_html}
    </div>

    <!-- Footer -->
    <div style="border-top:1px solid #1e293b;padding-top:16px;text-align:center;">
      <p style="margin:0;color:#475569;font-size:11px;">
        MacroSignalTool v0.1 · Paper Trading · Solo uso personale<br>
        Non costituisce consulenza finanziaria. Avvia l'app su <code>localhost:3000</code>
      </p>
    </div>

  </div>
</body>
</html>"""

    return html


# ── Send digest ───────────────────────────────────────────────────────────────
def send_daily_digest() -> bool:
    """
    Funzione principale: raccoglie i dati e invia il digest via Gmail SMTP.
    Ritorna True se inviato correttamente.
    """
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        logger.warning(
            "Email digest: GMAIL_USER o GMAIL_APP_PASSWORD non configurati. "
            "Imposta le variabili in .env."
        )
        return False

    try:
        # Raccolta dati
        news      = _get_top_news(3)
        signals   = _get_signals_cache()
        portfolio = _get_portfolio_state()
        weekly    = _get_weekly_performance()

        # Build HTML
        html_content = _build_html(news, signals, portfolio, weekly)
        today_str = datetime.now().strftime("%d/%m/%Y")

        # Composizione email
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"⚡ MacroSignalTool Digest — {today_str}"
        msg["From"]    = GMAIL_USER
        msg["To"]      = DIGEST_RECIPIENT

        # Versione testo plain (fallback)
        text_lines = [
            f"MacroSignalTool Daily Digest — {today_str}",
            "=" * 50,
            "",
            f"NAV: €{portfolio.get('total_nav', 10000):.0f} | "
            f"P&L: {'+' if portfolio.get('realized_pnl_eur', 0) >= 0 else ''}€{portfolio.get('realized_pnl_eur', 0):.2f}",
            f"Posizioni aperte: {portfolio.get('num_open_positions', 0)}",
            "",
            "TOP NEWS:",
        ]
        for n in news:
            text_lines.append(f"  [{n.get('event_category','?')}] {n.get('title','')}")
        text_lines += ["", "SEGNALI ATTIVI:"]
        for s in signals:
            conf = int(s.get("confidence_composite", 0) * 100)
            text_lines.append(f"  {conf}% — {s.get('event_category','?')} — {s.get('headline','')[:80]}")
        text_content = "\n".join(text_lines)

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        # Invio via Gmail SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, DIGEST_RECIPIENT, msg.as_string())

        logger.info(f"Daily digest inviato a {DIGEST_RECIPIENT}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error(
            "Gmail: autenticazione fallita. "
            "Verifica che GMAIL_APP_PASSWORD sia una App Password (non la password normale). "
            "Vai su https://myaccount.google.com/apppasswords per crearne una."
        )
        return False
    except Exception as e:
        logger.error(f"send_daily_digest error: {e}")
        return False


def send_digest_test() -> None:
    """Invia il digest immediatamente (per test)."""
    print(f"Gmail configurato: {'SI' if GMAIL_USER else 'NO'}")
    print(f"App Password configurata: {'SI' if GMAIL_APP_PASSWORD else 'NO'}")
    print(f"Destinatario: {DIGEST_RECIPIENT or '(non configurato)'}")

    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("\n⚠️  Configura GMAIL_USER e GMAIL_APP_PASSWORD in .env prima di testare.")
        print("   Guida App Password: https://myaccount.google.com/apppasswords")

        # Genera comunque l'HTML in locale per preview
        html = _build_html(
            news=[{
                "event_category": "MILITARY_CONFLICT",
                "title": "Iran strikes US base in Iraq with ballistic missiles",
                "source": "Reuters",
                "materiality_score": 0.87,
                "causal_chain": "Strike → escalation rischio → oil +8% → defense bid",
                "url": "#",
            }],
            signals=[{
                "event_category": "ENERGY_SUPPLY_SHOCK",
                "confidence_composite": 0.78,
                "kelly_quality": "STRONG",
                "position_size_eur": 420,
                "entry_timing": "T+1",
                "headline": "Hormuz shipping volumes drop 40% after Iranian naval blockade",
            }],
            portfolio={
                "total_nav": 10380,
                "realized_pnl_eur": 285.50,
                "open_pnl_eur": 94.50,
                "num_open_positions": 3,
                "total_return_pct": 3.8,
            },
            weekly={
                "weekly_trades": 5,
                "weekly_wins": 4,
                "weekly_losses": 1,
                "weekly_pnl": 210.30,
                "weekly_win_rate": 0.80,
            },
        )
        preview_path = os.path.join(os.path.dirname(__file__), "digest_preview.html")
        with open(preview_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n✅ Preview HTML generata: {preview_path}")
        print("   Apri nel browser per vedere il layout dell'email.")
        return

    print("\nInvio digest in corso...")
    ok = send_daily_digest()
    if ok:
        print(f"✅ Digest inviato a {DIGEST_RECIPIENT}")
    else:
        print("❌ Invio fallito. Controlla i log per dettagli.")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MacroSignalTool Email Digest")
    parser.add_argument("--test", action="store_true", help="Invia digest di test (o genera preview HTML)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.test:
        send_digest_test()
    else:
        print("Usa --test per inviare il digest ora.")
        print("In produzione è schedulato automaticamente da main.py alle 8:00.")
