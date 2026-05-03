"""
comment_handler.py
Phase 8 — Kairós · MacroSignalTool

Legge i commenti sui post Instagram e risponde automaticamente
con tono Kairós + CTA verso il canale Telegram.

Funzionalità:
  - Legge commenti nuovi ogni ora (tramite scheduler in main.py)
  - Claude Haiku genera risposta pertinente e sobria
  - Ogni risposta chiude con CTA Telegram
  - Traccia i commenti già risposti per evitare duplicati
  - Simula poll/quiz via commenti ("Rispondi A o B")

Testabile: python comment_handler.py --test
"""

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    import anthropic
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

logger = logging.getLogger("comment_handler")

MODEL = "claude-haiku-4-5-20251001"
REPLIED_LOG_PATH = Path(__file__).parent / "ig_replied_comments.json"

TELEGRAM_CHANNEL = os.getenv("IG_TELEGRAM_CTA", "il canale Telegram @kairos.macro")


# ─── Tracking commenti già risposti ──────────────────────────────────────────

def _load_replied() -> set:
    if REPLIED_LOG_PATH.exists():
        try:
            with open(REPLIED_LOG_PATH, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def _save_replied(replied: set):
    with open(REPLIED_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(list(replied), f)


# ─── Generazione risposta con Claude ─────────────────────────────────────────

def generate_reply(comment_text: str, post_context: str = "") -> str:
    """
    Genera una risposta al commento in tono Kairós.
    Chiude sempre con CTA verso Telegram.
    """
    if not ANTHROPIC_AVAILABLE:
        return _fallback_reply(comment_text)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _fallback_reply(comment_text)

    try:
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""Sei il community manager di Kairós, una pubblicazione macro finanziaria italiana.
Il tuo tono: sobrio, preciso, mai condiscendente. Nessuna emoji. Nessun punto esclamativo.
Rispondi in italiano. Massimo 3 righe.

Contesto del post: {post_context[:200] if post_context else 'Analisi macro della settimana'}
Commento dell'utente: {comment_text[:300]}

Regole:
- Rispondi in modo pertinente al commento
- Se l'utente fa una domanda tecnica, dai una risposta breve e precisa
- Se l'utente chiede segnali operativi o consigli di trading, rimandalo gentilmente a Telegram
- Chiudi SEMPRE con: "I segnali operativi sono su {TELEGRAM_CHANNEL}."
- Nessuna emoji, nessun punto esclamativo
- Non usare parole come: garantito, sicuro, boom, crash, imperdibile

Scrivi solo la risposta, senza introduzioni."""

        response = client.messages.create(
            model=MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        reply = response.content[0].text.strip()

        # Assicura che la CTA ci sia sempre
        cta = f"I segnali operativi sono su {TELEGRAM_CHANNEL}."
        if TELEGRAM_CHANNEL.split("@")[-1].lower() not in reply.lower():
            reply = reply.rstrip(".") + f". {cta}"

        return reply[:1000]

    except Exception as e:
        logger.error(f"Errore generazione risposta: {e}")
        return _fallback_reply(comment_text)


def _fallback_reply(comment_text: str) -> str:
    """Risposta di fallback senza Claude."""
    return (
        f"Grazie per il commento. "
        f"Per approfondimenti e segnali operativi: {TELEGRAM_CHANNEL}."
    )


# ─── Handler principale ───────────────────────────────────────────────────────

async def process_post_comments(
    post_id: str,
    post_caption: str = "",
    dry_run: bool = False,
) -> dict:
    """
    Legge e risponde ai commenti di un post.

    Args:
        post_id: ID del post Instagram
        post_caption: caption del post per contesto
        dry_run: se True, non invia risposta (solo log)

    Returns:
        dict con stats: replied, skipped, errors
    """
    from instagram_publisher import get_recent_comments, reply_to_comment

    replied_log = _load_replied()
    stats = {"replied": 0, "skipped": 0, "errors": 0}

    comments = await get_recent_comments(post_id)
    logger.info(f"Post {post_id}: {len(comments)} commenti trovati")

    for comment in comments:
        comment_id = comment.get("id", "")
        text = comment.get("text", "")
        username = comment.get("username", "utente")

        if not comment_id or not text:
            continue

        if comment_id in replied_log:
            stats["skipped"] += 1
            continue

        # Ignora commenti troppo corti o spam-like
        if len(text.strip()) < 5:
            replied_log.add(comment_id)
            stats["skipped"] += 1
            continue

        logger.info(f"  Commento da @{username}: {text[:60]}...")

        reply = generate_reply(text, post_caption)
        logger.info(f"  Risposta: {reply[:80]}...")

        if not dry_run:
            success = await reply_to_comment(comment_id, reply)
            if success:
                replied_log.add(comment_id)
                stats["replied"] += 1
                logger.info(f"  ✅ Risposta inviata a @{username}")
                # Pausa per evitare rate limit
                await asyncio.sleep(2)
            else:
                stats["errors"] += 1
        else:
            logger.info(f"  [DRY RUN] Non inviata")
            replied_log.add(comment_id)
            stats["replied"] += 1

    _save_replied(replied_log)
    return stats


async def process_all_recent_posts(days: int = 1, dry_run: bool = False) -> dict:
    """
    Processa i commenti di tutti i post degli ultimi N giorni.
    Da chiamare ogni ora dallo scheduler.
    """
    from instagram_publisher import get_account_info, _get_config, _api_get
    import aiohttp

    cfg = _get_config()
    if not cfg["access_token"] or not cfg["account_id"]:
        logger.warning("Instagram non configurato — skip comment handling")
        return {}

    total_stats = {"replied": 0, "skipped": 0, "errors": 0, "posts_processed": 0}

    async with aiohttp.ClientSession() as session:
        from instagram_publisher import _api_get as api_get
        try:
            media = await api_get(
                session,
                f"{cfg['account_id']}/media",
                {
                    "fields": "id,caption,timestamp",
                    "limit": 10,
                    "access_token": cfg["access_token"],
                }
            )

            posts = media.get("data", [])
            cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)

            for post in posts:
                ts = post.get("timestamp", "")
                try:
                    post_ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    if post_ts < cutoff:
                        continue
                except Exception:
                    pass

                post_id = post.get("id", "")
                caption = post.get("caption", "")[:200]

                stats = await process_post_comments(post_id, caption, dry_run)
                total_stats["replied"] += stats.get("replied", 0)
                total_stats["skipped"] += stats.get("skipped", 0)
                total_stats["errors"] += stats.get("errors", 0)
                total_stats["posts_processed"] += 1

        except Exception as e:
            logger.error(f"Errore lettura post recenti: {e}")

    logger.info(
        f"Comment handler: {total_stats['posts_processed']} post processati, "
        f"{total_stats['replied']} risposte inviate, "
        f"{total_stats['skipped']} skippate"
    )
    return total_stats


# ─── CLI test ─────────────────────────────────────────────────────────────────

def _run_test():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    print(f"\n{'='*60}")
    print("TEST: comment_handler.py")
    print("="*60)

    test_comments = [
        "Ottima analisi! Ma come faccio a sapere quando comprare?",
        "Hormuz è davvero così importante per il petrolio europeo?",
        "Quali ETF consigli per esporsi all'energia?",
        "A",  # troppo corto, skip
        "Quando pubblicate il prossimo segnale?",
    ]

    print("\nTest generazione risposte (senza pubblicare):\n")
    for comment in test_comments:
        if len(comment.strip()) < 5:
            print(f"  SKIP (troppo corto): '{comment}'")
            continue
        reply = generate_reply(comment, "Analisi su chiusura Stretto di Hormuz")
        print(f"  Commento: {comment}")
        print(f"  Risposta: {reply}")
        print()

    print("✅ Test completato.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Comment Handler — Kairós")
    parser.add_argument("--test", action="store_true", help="Test generazione risposte")
    parser.add_argument("--process", action="store_true", help="Processa commenti recenti (dry run)")
    args = parser.parse_args()

    if args.test:
        _run_test()
    elif args.process:
        asyncio.run(process_all_recent_posts(dry_run=True))
    else:
        parser.print_help()
