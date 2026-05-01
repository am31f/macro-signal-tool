"""
telegram_bot.py — MacroSignalTool Phase 6.1
============================================
Bot Telegram per alert in tempo reale su:
  (a) Nuovo segnale generato con confidence > ALERT_THRESHOLD
  (b) Posizione paper raggiunge stop o target
  (c) Comandi interattivi: /status, /positions, /signals, /help

Utilizzo standalone (test):
    python telegram_bot.py --test

Integrazione con main.py FastAPI:
    from telegram_bot import TelegramNotifier
    notifier = TelegramNotifier()
    await notifier.send_signal_alert(signal_dict)
    await notifier.send_trade_closed(position_dict)

Configurazione richiesta in .env:
    TELEGRAM_BOT_TOKEN=xxx:yyy
    TELEGRAM_CHAT_ID=123456789

Per ottenere il CHAT_ID:
    1. Avvia una chat con il tuo bot su Telegram
    2. Vai su https://api.telegram.org/bot<TOKEN>/getUpdates
    3. Copia il campo "chat.id" dal primo messaggio
"""

import asyncio
import logging
import os
import json
import sys
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Costanti ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
ALERT_THRESHOLD    = float(os.getenv("TELEGRAM_SIGNAL_THRESHOLD", "0.70"))

# Emoji per categorie evento
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

KELLY_EMOJI = {
    "STRONG":   "🟢",
    "MODERATE": "🟡",
    "WEAK":     "🟠",
    "NO_TRADE": "🔴",
}

VERDICT_EMOJI = {
    "WIN":       "✅",
    "LOSS":      "❌",
    "BREAKEVEN": "➖",
}


# ── TelegramNotifier ──────────────────────────────────────────────────────────
class TelegramNotifier:
    """
    Client asincrono per inviare notifiche Telegram.
    Non richiede un bot in ascolto — usa solo sendMessage via HTTP.
    """

    def __init__(
        self,
        token: str = TELEGRAM_BOT_TOKEN,
        chat_id: str = TELEGRAM_CHAT_ID,
    ):
        self.token   = token
        self.chat_id = chat_id
        self._enabled = bool(token and chat_id)

        if not self._enabled:
            logger.warning(
                "TelegramNotifier: TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID non configurati. "
                "Le notifiche saranno disabilitate."
            )

    # ── Invio messaggio raw ───────────────────────────────────────────────────
    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Invia un messaggio al chat_id configurato. Ritorna True se ok."""
        if not self._enabled:
            logger.debug(f"[Telegram mock] {text[:80]}...")
            return False

        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp non installato — install: pip install aiohttp")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id":    self.chat_id,
            "text":       text,
            "parse_mode": parse_mode,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        logger.info(f"Telegram: messaggio inviato ({len(text)} chars)")
                        return True
                    else:
                        body = await resp.text()
                        logger.error(f"Telegram API error {resp.status}: {body}")
                        return False
        except Exception as e:
            logger.error(f"Telegram send_message exception: {e}")
            return False

    # ── Alert: nuovo segnale ──────────────────────────────────────────────────
    async def send_signal_alert(self, signal: dict) -> bool:
        """
        Invia alert quando un segnale supera ALERT_THRESHOLD.
        signal: dict dalla pipeline (PipelineOutput.signal_candidates[i])
        """
        conf  = signal.get("confidence_composite", 0)
        if conf < ALERT_THRESHOLD:
            return False  # sotto soglia, non notificare

        cat     = signal.get("event_category", "UNKNOWN")
        emoji   = CATEGORY_EMOJI.get(cat, "📊")
        kelly   = signal.get("kelly_quality", "–")
        k_emoji = KELLY_EMOJI.get(kelly, "⚪")
        conf_pct = int(conf * 100)

        mat_pct   = int(signal.get("materiality_score", 0) * 100)
        nov_pct   = int(signal.get("novelty_score", 0) * 100)
        size_eur  = signal.get("position_size_eur", 0)
        timing    = signal.get("entry_timing", "–")
        headline  = signal.get("headline", "")[:200]
        trade_type = signal.get("trade_type", "–")

        # Struttura trade (se disponibile)
        instruments_str = ""
        instruments = signal.get("instruments", [])
        if instruments:
            lines = []
            for ins in instruments[:4]:
                ticker = ins.get("ticker", "?")
                direction = ins.get("direction", "?")
                weight = ins.get("weight_pct", 0)
                arrow = "↑" if direction == "LONG" else "↓"
                lines.append(f"  {arrow} <code>{ticker}</code> {weight}%")
            instruments_str = "\n" + "\n".join(lines)

        text = (
            f"{emoji} <b>NUOVO SEGNALE MacroSignalTool</b>\n\n"
            f"<b>Confidence:</b> {conf_pct}% {k_emoji} {kelly}\n"
            f"<b>Categoria:</b> {cat.replace('_', ' ')}\n"
            f"<b>Trade type:</b> {trade_type}\n"
            f"<b>Entry timing:</b> {timing}\n\n"
            f"<b>Notizia:</b>\n{headline}\n\n"
            f"<b>Metriche:</b>\n"
            f"  Materiality: {mat_pct}% | Novelty: {nov_pct}%\n"
            f"  Position size: <b>€{size_eur:.0f}</b>"
            f"{instruments_str}\n\n"
            f"🔗 <i>Apri MacroSignalTool → Segnali per il dettaglio completo</i>"
        )

        return await self.send_message(text)

    # ── Alert: posizione chiusa (stop/target) ─────────────────────────────────
    async def send_trade_closed(self, position: dict, close_reason: str = "manual") -> bool:
        """
        Invia alert quando una posizione paper viene chiusa.
        position: dict dalla portfolio_manager con i campi della posizione
        """
        verdict  = position.get("verdict", "BREAKEVEN")
        v_emoji  = VERDICT_EMOJI.get(verdict, "➖")
        ticker   = position.get("ticker", "?")
        direction = position.get("direction", "?")
        pnl_eur  = position.get("pnl_eur", 0)
        pnl_pct  = position.get("pnl_pct", 0)
        entry_price  = position.get("entry_price", 0)
        close_price  = position.get("close_price", 0)
        size_eur     = position.get("size_eur", 0)
        holding_days = position.get("holding_days", 0)
        event_cat    = position.get("event_category", "–")

        reason_labels = {
            "target_hit": "🎯 Target raggiunto",
            "stop_hit":   "🛑 Stop loss",
            "manual":     "✋ Chiusura manuale",
            "expired":    "⏰ Scaduto",
        }
        reason_label = reason_labels.get(close_reason, close_reason)

        pnl_sign = "+" if pnl_eur >= 0 else ""
        pnl_color_text = "🟢" if pnl_eur >= 0 else "🔴"

        text = (
            f"{v_emoji} <b>POSIZIONE CHIUSA — {ticker}</b>\n\n"
            f"<b>Esito:</b> {verdict} {pnl_color_text}\n"
            f"<b>Motivo:</b> {reason_label}\n\n"
            f"<b>Ticker:</b> {ticker} | <b>Dir:</b> {direction}\n"
            f"<b>P&L:</b> {pnl_sign}€{pnl_eur:.2f} ({pnl_sign}{pnl_pct:.2f}%)\n"
            f"<b>Size:</b> €{size_eur:.0f} | <b>Holding:</b> {holding_days:.1f}g\n"
            f"<b>Entry:</b> {entry_price:.4f} → <b>Close:</b> {close_price:.4f}\n"
            f"<b>Evento:</b> {event_cat.replace('_', ' ')}\n\n"
            f"🔗 <i>Vedi Journal per lesson learned</i>"
        )

        return await self.send_message(text)

    # ── Alert: segnale di allerta su posizione aperta ─────────────────────────
    async def send_position_warning(self, position: dict, warning_type: str) -> bool:
        """
        Avvisa se una posizione aperta è vicina allo stop o ha cross-asset incoerenti.
        warning_type: 'near_stop' | 'cross_asset_break'
        """
        ticker   = position.get("ticker", "?")
        direction = position.get("direction", "?")
        pnl_pct  = position.get("pnl_pct", 0)
        stop_pct = position.get("stop_loss_pct", 0)

        if warning_type == "near_stop":
            text = (
                f"⚠️ <b>ATTENZIONE — {ticker} vicino allo stop</b>\n\n"
                f"Ticker: <b>{ticker}</b> | Direzione: {direction}\n"
                f"P&L corrente: {pnl_pct:.2f}% | Stop: -{stop_pct:.1f}%\n\n"
                f"<i>Considera se ridurre la posizione o stringere lo stop.</i>"
            )
        elif warning_type == "cross_asset_break":
            text = (
                f"⚠️ <b>CROSS-ASSET BREAK — {ticker}</b>\n\n"
                f"I cross-asset non confermano più la tesi di {direction} su <b>{ticker}</b>.\n"
                f"P&L corrente: {pnl_pct:.2f}%\n\n"
                f"<i>Rivaluta la posizione — i mercati potrebbero stare prezzando uno scenario alternativo.</i>"
            )
        else:
            text = f"⚠️ Warning su {ticker}: {warning_type}"

        return await self.send_message(text)

    # ── Digest performance ────────────────────────────────────────────────────
    async def send_performance_snapshot(self, report: dict) -> bool:
        """
        Invia snapshot performance (usabile via /status command o dal digest).
        report: dict da performance_tracker.generate_report()
        """
        s = report.get("summary", {})
        r = report.get("risk_metrics", {})
        b = report.get("benchmark", {})
        portfolio = report.get("portfolio_state", {})

        nav = portfolio.get("total_nav", 10000)
        nav_initial = 10000
        nav_change = nav - nav_initial
        nav_sign = "+" if nav_change >= 0 else ""

        win_rate = s.get("win_rate", 0)
        wr_pct   = int(win_rate * 100)
        wins     = s.get("wins", 0)
        losses   = s.get("losses", 0)
        be       = s.get("breakevens", 0)
        total    = s.get("total_trades", 0)
        total_pnl = s.get("total_pnl_eur", 0)
        pnl_sign = "+" if total_pnl >= 0 else ""
        sharpe   = r.get("sharpe_simulated", 0)
        drawdown = r.get("max_drawdown_pct", 0)
        alpha    = b.get("alpha_pct", None)
        checklist = report.get("go_live_checklist", {})
        checklist_status = checklist.get("current_status", "UNKNOWN")

        alpha_str = f"{alpha:+.2f}%" if alpha is not None else "N/D"

        text = (
            f"📊 <b>MacroSignalTool — Performance Snapshot</b>\n"
            f"<i>{datetime.now().strftime('%d/%m/%Y %H:%M')}</i>\n\n"
            f"<b>NAV:</b> €{nav:.0f} ({nav_sign}€{nav_change:.0f})\n"
            f"<b>P&L totale:</b> {pnl_sign}€{total_pnl:.2f}\n\n"
            f"<b>Trade:</b> {total} | W/L/BE: {wins}/{losses}/{be}\n"
            f"<b>Win rate:</b> {wr_pct}% {'🟢' if win_rate >= 0.52 else '🟡'}\n"
            f"<b>Sharpe:</b> {sharpe:.3f} {'🟢' if sharpe >= 0.8 else '🟡' if sharpe >= 0.4 else '🔴'}\n"
            f"<b>Max drawdown:</b> {drawdown:.2f}%\n"
            f"<b>Alpha vs SPY:</b> {alpha_str}\n\n"
            f"<b>Go-live status:</b> {checklist_status}"
        )

        return await self.send_message(text)


# ── Bot con comandi interattivi ───────────────────────────────────────────────
class MacroSignalBot:
    """
    Bot Telegram con comandi interattivi.
    Richiede python-telegram-bot >= 20.x (async).
    """

    def __init__(self, notifier: TelegramNotifier):
        self.notifier = notifier
        self._app = None

    def setup(self):
        """Configura il bot con i command handler. Chiama prima di run()."""
        try:
            from telegram.ext import Application, CommandHandler
        except ImportError:
            logger.error("python-telegram-bot non installato. Esegui: pip install python-telegram-bot")
            return False

        self._app = Application.builder().token(self.notifier.token).build()

        from telegram.ext import CommandHandler
        self._app.add_handler(CommandHandler("start",     self._cmd_start))
        self._app.add_handler(CommandHandler("help",      self._cmd_help))
        self._app.add_handler(CommandHandler("status",    self._cmd_status))
        self._app.add_handler(CommandHandler("positions", self._cmd_positions))
        self._app.add_handler(CommandHandler("signals",   self._cmd_signals))

        return True

    def run_polling(self):
        """Avvia il bot in modalità polling (blocca il thread). Usare in processo separato."""
        if not self._app:
            if not self.setup():
                return
        logger.info("Telegram bot avviato in polling mode")
        self._app.run_polling()

    # ── Command handlers ──────────────────────────────────────────────────────
    async def _cmd_start(self, update, context):
        await update.message.reply_text(
            "👋 <b>MacroSignalTool Bot</b>\n\n"
            "Comandi disponibili:\n"
            "/status — Performance snapshot\n"
            "/positions — Posizioni aperte\n"
            "/signals — Ultimi segnali in cache\n"
            "/help — Aiuto",
            parse_mode="HTML",
        )

    async def _cmd_help(self, update, context):
        await update.message.reply_text(
            "📖 <b>MacroSignalTool Bot — Comandi</b>\n\n"
            "<b>/status</b> — NAV, P&L, win rate, Sharpe, drawdown e go-live status\n"
            "<b>/positions</b> — Lista posizioni paper aperte con P&L live\n"
            "<b>/signals</b> — Ultimi segnali generati dalla pipeline\n\n"
            "<i>Gli alert automatici arrivano su questo chat quando:</i>\n"
            f"• Nuovo segnale con confidence ≥ {int(ALERT_THRESHOLD*100)}%\n"
            "• Posizione raggiunge stop o target\n",
            parse_mode="HTML",
        )

    async def _cmd_status(self, update, context):
        """Recupera e invia il performance snapshot dal backend locale."""
        try:
            # Import diretto del tracker (stesso processo)
            sys.path.insert(0, os.path.dirname(__file__))
            from performance_tracker import generate_report
            report = generate_report()
            if report.get("status") == "NO_DATA":
                await update.message.reply_text("📊 Nessun dato ancora — esegui qualche trade paper prima.")
                return
            await self.notifier.send_performance_snapshot(report)
        except Exception as e:
            await update.message.reply_text(f"❌ Errore: {e}")

    async def _cmd_positions(self, update, context):
        """Lista posizioni aperte."""
        try:
            from portfolio_manager import PortfolioManager
            pm = PortfolioManager()
            state = pm.get_portfolio_state()
            positions = state.get("open_positions", [])

            if not positions:
                await update.message.reply_text("📭 Nessuna posizione aperta al momento.")
                return

            lines = [f"📋 <b>Posizioni aperte ({len(positions)})</b>\n"]
            for p in positions:
                pnl = p.get("pnl_eur", 0)
                pnl_sign = "+" if pnl >= 0 else ""
                arrow = "↑" if p.get("direction") == "LONG" else "↓"
                lines.append(
                    f"{arrow} <b>{p['ticker']}</b> | €{p.get('size_eur',0):.0f} "
                    f"| P&L: {pnl_sign}€{pnl:.2f} ({pnl_sign}{p.get('pnl_pct',0):.2f}%)"
                )

            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Errore: {e}")

    async def _cmd_signals(self, update, context):
        """Mostra gli ultimi segnali dalla cache (via API locale o file)."""
        try:
            # Tenta di caricare dal file di cache se il server non è raggiungibile
            cache_file = os.path.join(os.path.dirname(__file__), "signals_cache.json")
            if os.path.exists(cache_file):
                with open(cache_file) as f:
                    data = json.load(f)
                signals = data.get("signals", [])[:5]
            else:
                await update.message.reply_text(
                    "⚠️ Cache segnali non disponibile. Avvia il backend e premi '▶ Pipeline'."
                )
                return

            if not signals:
                await update.message.reply_text("📭 Nessun segnale in cache.")
                return

            lines = [f"⚡ <b>Segnali recenti ({len(signals)})</b>\n"]
            for s in signals:
                conf = int(s.get("confidence_composite", 0) * 100)
                cat  = s.get("event_category", "?").replace("_", " ")
                kelly = s.get("kelly_quality", "–")
                k_e   = KELLY_EMOJI.get(kelly, "⚪")
                headline = s.get("headline", "")[:80]
                lines.append(
                    f"{k_e} <b>{conf}%</b> — {cat}\n<i>{headline}…</i>"
                )

            await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Errore: {e}")


# ── Test standalone ───────────────────────────────────────────────────────────
async def _run_test():
    """Test: invia messaggi di prova al bot configurato."""
    print(f"Token configurato: {'SI' if TELEGRAM_BOT_TOKEN else 'NO'}")
    print(f"Chat ID configurato: {'SI' if TELEGRAM_CHAT_ID else 'NO'}")

    notifier = TelegramNotifier()

    # Test 1: messaggio semplice
    print("\n[Test 1] Invio messaggio di prova...")
    ok = await notifier.send_message(
        "🧪 <b>MacroSignalTool — Test connessione</b>\n\n"
        "Il bot Telegram è configurato correttamente!\n"
        f"<i>{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</i>"
    )
    print(f"  → {'OK' if ok else 'FALLITO (check TOKEN e CHAT_ID in .env)'}")

    # Test 2: signal alert
    print("[Test 2] Invio alert segnale di prova...")
    fake_signal = {
        "event_category": "ENERGY_SUPPLY_SHOCK",
        "confidence_composite": 0.78,
        "materiality_score": 0.81,
        "novelty_score": 0.74,
        "kelly_quality": "STRONG",
        "position_size_eur": 420.0,
        "entry_timing": "T+1",
        "trade_type": "DIRECTIONAL",
        "headline": "Iran announces indefinite closure of Strait of Hormuz to all non-Iranian vessels",
        "instruments": [
            {"ticker": "XLE", "direction": "LONG", "weight_pct": 40},
            {"ticker": "GLD", "direction": "LONG", "weight_pct": 30},
            {"ticker": "DAL", "direction": "SHORT", "weight_pct": 30},
        ],
    }
    ok = await notifier.send_signal_alert(fake_signal)
    print(f"  → {'OK' if ok else 'FALLITO'}")

    # Test 3: trade closed
    print("[Test 3] Invio alert trade chiuso...")
    fake_position = {
        "ticker": "XLE",
        "direction": "LONG",
        "pnl_eur": 38.50,
        "pnl_pct": 9.16,
        "verdict": "WIN",
        "size_eur": 420.0,
        "entry_price": 89.45,
        "close_price": 97.65,
        "holding_days": 4.2,
        "event_category": "ENERGY_SUPPLY_SHOCK",
    }
    ok = await notifier.send_trade_closed(fake_position, close_reason="target_hit")
    print(f"  → {'OK' if ok else 'FALLITO'}")

    print("\nTest completati.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MacroSignalTool Telegram Bot")
    parser.add_argument("--test", action="store_true", help="Invia messaggi di prova al bot")
    parser.add_argument("--poll",  action="store_true", help="Avvia bot in polling mode (comandi interattivi)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.test:
        asyncio.run(_run_test())
    elif args.poll:
        notifier = TelegramNotifier()
        bot = MacroSignalBot(notifier)
        bot.run_polling()
    else:
        print("Usa --test per testare la connessione o --poll per avviare il bot.")
        print("Esempio: python telegram_bot.py --test")
