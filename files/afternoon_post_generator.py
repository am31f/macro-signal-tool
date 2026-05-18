"""
afternoon_post_generator.py
Kairós — Post pomeridiano automatico (17:00 CET)

Genera un post singolo giornaliero con rotazione automatica tra 3 temi:
  1. CURIOSITA_MACRO   — domanda/risposta su un fenomeno macro
  2. EVENTO_STORICO    — evento di mercato del passato + lezione
  3. METODO_KAIROS     — spiegazione del metodo di selezione segnali

Il tema viene scelto in base al giorno della settimana e alle notizie
classificate del giorno (mantiene rilevanza con l'attualità).

Output: AfternoonPostContent dataclass con tutti i campi per il renderer.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Dataclass output ─────────────────────────────────────────────────────────

@dataclass
class AfternoonPostContent:
    theme: str                  # CURIOSITA_MACRO | EVENTO_STORICO | METODO_KAIROS
    headline: str               # Testo grande — 1 riga (max 60 chars)
    subline: str                # Testo piccolo — 1 riga (max 80 chars), può essere ""
    eyebrow: str                # Label piccola in alto (es. "CURIOSITÀ MACRO")
    caption: str                # Didascalia Instagram (testo completo sotto il post)
    hashtags: list              # Lista hashtag senza #
    accent_word: str            # Parola da colorare in Gold nel headline (può essere "")


# ─── Rotazione temi ──────────────────────────────────────────────────────────

THEME_ROTATION = {
    0: "METODO_KAIROS",     # Lunedì
    1: "CURIOSITA_MACRO",   # Martedì
    2: "EVENTO_STORICO",    # Mercoledì
    3: "CURIOSITA_MACRO",   # Giovedì
    4: "METODO_KAIROS",     # Venerdì
    5: "EVENTO_STORICO",    # Sabato
    6: "CURIOSITA_MACRO",   # Domenica
}

EYEBROW_LABELS = {
    "CURIOSITA_MACRO": "CURIOSITÀ MACRO",
    "EVENTO_STORICO":  "MERCATI · STORIA",
    "METODO_KAIROS":   "IL NOSTRO METODO",
}

# ─── Contenuti statici di fallback ────────────────────────────────────────────
# Usati se Claude Haiku non è disponibile o fallisce.
# Struttura: lista di dict per tema, vengono scelti in modo round-robin.

FALLBACK_CONTENT = {
    "CURIOSITA_MACRO": [
        {
            "headline": "Perché il dollaro sale quando c'è crisi?",
            "subline": "Il paradosso del safe haven più usato al mondo.",
            "accent_word": "dollaro",
            "caption": (
                "Nei momenti di panico i capitali si spostano verso il dollaro, "
                "anche quando la crisi è americana.\n\n"
                "Il motivo: il 60% del debito globale è denominato in USD. "
                "Chi deve rimborsare debiti in dollari li compra sul mercato "
                "anche se il prezzo sale — creando una domanda strutturale.\n\n"
                "Ogni mattina analizziamo quale evento può muovere questo meccanismo. "
                "Segnali operativi su @Kairós."
            ),
            "hashtags": ["macroinvestor", "macroresearch", "kairosmacro"],
        },
        {
            "headline": "Cosa muove il prezzo del petrolio?",
            "subline": "Non solo domanda e offerta.",
            "accent_word": "petrolio",
            "caption": (
                "Il petrolio è mosso da 4 forze: offerta OPEC, domanda cinese, "
                "dollaro USA e geopolitica mediorientale.\n\n"
                "Quando tutte e 4 si allineano nella stessa direzione, "
                "il movimento è violento. È quello che monitoriamo ogni mattina.\n\n"
                "Segnali operativi su @Kairós."
            ),
            "hashtags": ["macroinvestor", "macroresearch", "kairosmacro"],
        },
        {
            "headline": "Cosa succede ai mercati quando la Fed taglia?",
            "subline": "La risposta non è quella che ti aspetti.",
            "accent_word": "Fed",
            "caption": (
                "Storicamente, i mercati azionari scendono nei 3 mesi successivi "
                "al primo taglio Fed di un ciclo — non salgono.\n\n"
                "Perché? Il taglio arriva quando l'economia rallenta già. "
                "Il mercato prezza il rallentamento, non lo stimolo.\n\n"
                "Ogni mattina analizziamo il regime macro attuale. "
                "Segnali su @Kairós."
            ),
            "hashtags": ["macroinvestor", "macroresearch", "kairosmacro"],
        },
    ],
    "EVENTO_STORICO": [
        {
            "headline": "Brexit: cosa successe ai mercati.",
            "subline": "24 giugno 2016 — una lezione ancora valida.",
            "accent_word": "Brexit",
            "caption": (
                "Il 24 giugno 2016 la sterlina perse il 10% in una notte. "
                "I mercati azionari europei aprirono con -8%.\n\n"
                "Chi aveva letto i segnali macro nei giorni precedenti "
                "(spread UK-EU in allargamento, volatilità implicita in salita) "
                "aveva già ridotto l'esposizione.\n\n"
                "È esattamente quello che fa Kairós ogni mattina — "
                "leggere i segnali prima che diventino notizia.\n\n"
                "Canale operativo: @Kairós"
            ),
            "hashtags": ["macroinvestor", "macroresearch", "kairosmacro"],
        },
        {
            "headline": "Lehman Brothers: il segnale c'era.",
            "subline": "Settembre 2008 — cosa lo annunciò.",
            "accent_word": "Lehman",
            "caption": (
                "Prima del crollo di Lehman il 15 settembre 2008, tre segnali erano visibili da settimane: "
                "gli spread interbancari ai massimi storici, le azioni bancarie a −40% da gennaio, "
                "i credit default swap di Lehman a 700bps.\n\n"
                "Il mercato non credeva che avrebbero lasciato fallire una banca sistemica. "
                "Aveva torto. Chi leggeva i segnali macro aveva già ridotto l'esposizione.\n\n"
                "Sul canale: l'analisi del regime macro attuale e i segnali attivi. Link in bio."
            ),
            "hashtags": ["macroinvestor", "macroresearch", "kairosmacro"],
        },
        {
            "headline": "Black Monday 1987: -22% in un giorno.",
            "subline": "La borsa più grande della storia — e cosa la causò.",
            "accent_word": "Black Monday",
            "caption": (
                "Il 19 ottobre 1987 il Dow Jones perse il 22,6% in una seduta. "
                "Il peggior crollo giornaliero della storia.\n\n"
                "Causa principale: il portfolio insurance — "
                "un sistema automatico di vendita che amplificò il crollo "
                "invece di proteggerlo.\n\n"
                "La lezione: i meccanismi di protezione automatica "
                "possono diventare la causa del panico che cercano di evitare.\n\n"
                "Segnali macro ogni mattina — @Kairós"
            ),
            "hashtags": ["macroinvestor", "macroresearch", "kairosmacro"],
        },
    ],
    "METODO_KAIROS": [
        {
            "headline": "Come scegliamo un segnale.",
            "subline": "5 filtri. Solo i migliori passano.",
            "accent_word": "segnale",
            "caption": (
                "Ogni mattina il sistema analizza centinaia di notizie. "
                "Un segnale operativo deve superare cinque filtri in sequenza: "
                "materialità (può spostare variabili macro misurabili), novità (è già prezzato), "
                "causalità (c'è un percorso chiaro verso un asset), timing (effetto immediato o ritardato), "
                "regime (il contesto macro supporta la direzione).\n\n"
                "Solo i segnali che superano tutti e cinque vengono pubblicati. "
                "La soglia di confidence score è 70 su 100.\n\n"
                "Sul canale: i segnali attivi questa settimana con entry e conviction. Link in bio."
            ),
            "hashtags": ["macroinvestor", "macroresearch", "kairosmacro"],
        },
        {
            "headline": "Cos'è il confidence score.",
            "subline": "Il numero che decide se un segnale vale.",
            "accent_word": "confidence score",
            "caption": (
                "Ogni segnale Kairós ha un confidence score da 0 a 100. "
                "È la sintesi di cinque metriche: materialità (quanto sposta i mercati), "
                "novità (quanto è già prezzato), chiarezza causale (percorso causa-effetto), "
                "timing (effetto immediato o ritardato), allineamento di regime (il contesto macro supporta).\n\n"
                "Pubblichiamo solo i segnali con score superiore a 70. "
                "Non per esclusività — per disciplina.\n\n"
                "Sul canale: i segnali attivi con confidence score e conviction. Link in bio."
            ),
            "hashtags": ["macroinvestor", "macroresearch", "kairosmacro"],
        },
        {
            "headline": "Perché usiamo notizie, non grafici.",
            "subline": "Il segnale arriva prima del prezzo.",
            "accent_word": "notizie",
            "caption": (
                "L'analisi tecnica legge il passato.\n"
                "Kairós legge il futuro — attraverso le notizie.\n\n"
                "Quando un evento macro materiale accade, "
                "il mercato impiega ore o giorni ad aggiornare i prezzi. "
                "In quel gap c'è l'opportunità.\n\n"
                "Monitoriamo Reuters, FT, Bloomberg e altre 8 fonti in tempo reale. "
                "Claude AI classifica ogni notizia per materialità e novità.\n\n"
                "Il segnale arriva su @Kairós prima che il mercato si muova."
            ),
            "hashtags": ["macroinvestor", "macroresearch", "kairosmacro"],
        },
    ],
}

# Indice rotazione fallback (persiste in memoria)
_fallback_index = {"CURIOSITA_MACRO": 0, "EVENTO_STORICO": 0, "METODO_KAIROS": 0}


def _get_fallback(theme: str) -> dict:
    """Restituisce il prossimo contenuto fallback per il tema, in round-robin."""
    items = FALLBACK_CONTENT[theme]
    idx = _fallback_index[theme] % len(items)
    _fallback_index[theme] += 1
    return items[idx]


# ─── Generazione con Claude Haiku ────────────────────────────────────────────

def _generate_with_haiku(theme: str, top_news: Optional[str] = None) -> Optional[dict]:
    """
    Genera contenuto con Claude Haiku, ancorato alla notizia top del giorno se disponibile.
    Ritorna dict con headline, subline, accent_word, caption, hashtags — oppure None se fallisce.
    """
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None

        client = anthropic.Anthropic(api_key=api_key)

        news_context = f"\nNotizia macro del giorno (usa come spunto se rilevante): {top_news[:200]}" if top_news else ""

        theme_instructions = {
            "CURIOSITA_MACRO": (
                "Genera una curiosità o domanda macro interessante per i follower di Instagram. "
                "Deve essere educativa, sorprendente, e collegata ai mercati finanziari. "
                "Formato: una domanda come headline, risposta sintetica nella caption."
            ),
            "EVENTO_STORICO": (
                "Genera un riferimento a un evento storico di mercato (anni '80-2020). "
                "Deve essere rilevante oggi e insegnare qualcosa di concreto sui mercati. "
                "Formato: evento come headline, lezione nella caption."
            ),
            "METODO_KAIROS": (
                "Spiega un aspetto del metodo Kairós: come funziona la pipeline segnali, "
                "cosa sono i filtri, il confidence score, o perché usiamo notizie invece di grafici. "
                "Deve essere chiaro e concreto, non promozionale."
            ),
        }

        AFTERNOON_SYSTEM = """Sei il redattore editoriale di Kairós — una pubblicazione macro finanziaria italiana su Instagram.

TONO DI VOCE (regole assolute):
- Nessuna emoji, nessun punto esclamativo, nessun linguaggio hype
- Nessun elenco puntato con numeri o simboli (scrivi in prosa)
- Nessun consiglio di acquisto o promessa di rendimento
- Numeri precisi: non "i tassi sono saliti" ma "i Fed Funds sono al 5.25%"
- Tono: "un central banker che legge romanzi" — autorevole, sobrio, preciso
- Tagline: "We don't predict the market. We mark the moment."

STRUTTURA CAPTION (segui esattamente):
- BLOCCO 1 (2 frasi): il fatto o concetto con dato quantitativo preciso
- BLOCCO 2 (1-2 frasi): perché conta oggi, cosa cambia per chi investe
- BLOCCO 3 (1 frase): CTA specifica verso Telegram — non generica, cita cosa c'è nel canale che qui non c'è. Formato: "Sul canale: [cosa specifico]. Link in bio."
- Blocchi separati da doppio a capo. Max 180 parole totali.

Genera un post Instagram con questo formato JSON esatto (nessun testo aggiuntivo):
{
  "headline": "<frase di max 55 caratteri — testo grande sul post, senza punto esclamativo>",
  "subline": "<frase di max 75 caratteri — testo piccolo sotto, può essere stringa vuota>",
  "accent_word": "<una parola del headline da colorare in oro, oppure stringa vuota>",
  "caption": "<testo completo seguendo la struttura a 3 blocchi indicata sopra>",
  "hashtags": ["macroinvestor", "macroresearch", "kairosmacro"]
}"""

        prompt = f"""Tema di oggi: {theme_instructions[theme]}
{news_context}

Genera il post Instagram seguendo esattamente il formato JSON definito."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=[{"type": "text", "text": AFTERNOON_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}]
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        parsed = json.loads(raw)
        # Sanifica hashtag: rimuovi spazi, converti in minuscolo
        if "hashtags" in parsed:
            parsed["hashtags"] = [
                h.lower().replace(" ", "").replace("-", "")
                for h in parsed["hashtags"] if h
            ]
        return parsed

    except Exception as e:
        logger.warning(f"afternoon_post_generator: Haiku fallito ({e}), uso fallback")
        return None


# ─── Funzione principale ──────────────────────────────────────────────────────

def generate_afternoon_post(
    signals_cache_path: Optional[str] = None,
    force_theme: Optional[str] = None,
) -> AfternoonPostContent:
    """
    Genera il contenuto per il post pomeridiano.

    Args:
        signals_cache_path: percorso al signals_cache.json (per leggere la notizia top del giorno)
        force_theme: forza un tema specifico (CURIOSITA_MACRO | EVENTO_STORICO | METODO_KAIROS)

    Returns:
        AfternoonPostContent pronto per il renderer
    """
    now = datetime.now(timezone.utc)
    weekday = now.weekday()

    # Scegli tema
    theme = force_theme or THEME_ROTATION.get(weekday, "CURIOSITA_MACRO")
    logger.info(f"afternoon_post_generator: tema={theme}, weekday={weekday}")

    # Leggi notizia top del giorno dalla cache segnali (se disponibile)
    top_news = None
    if signals_cache_path:
        try:
            with open(signals_cache_path) as f:
                cache = json.load(f)
            signals = cache.get("signals", cache) if isinstance(cache, dict) else cache
            if signals:
                top = max(signals, key=lambda s: s.get("confidence_composite", s.get("confidence", 0)))
                top_news = top.get("headline", "")
        except Exception:
            pass

    # Prova con Haiku, fallback su contenuto statico
    generated = _generate_with_haiku(theme, top_news)

    if generated:
        return AfternoonPostContent(
            theme=theme,
            headline=generated.get("headline", "")[:60],
            subline=generated.get("subline", "")[:80],
            eyebrow=EYEBROW_LABELS[theme],
            caption=generated.get("caption", ""),
            hashtags=generated.get("hashtags", []),
            accent_word=generated.get("accent_word", ""),
        )
    else:
        fb = _get_fallback(theme)
        return AfternoonPostContent(
            theme=theme,
            headline=fb["headline"],
            subline=fb["subline"],
            eyebrow=EYEBROW_LABELS[theme],
            caption=fb["caption"],
            hashtags=fb["hashtags"],
            accent_word=fb["accent_word"],
        )


# ─── Test standalone ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", choices=["CURIOSITA_MACRO", "EVENTO_STORICO", "METODO_KAIROS"])
    parser.add_argument("--cache", default="signals_cache.json")
    args = parser.parse_args()

    content = generate_afternoon_post(
        signals_cache_path=args.cache if Path(args.cache).exists() else None,
        force_theme=args.theme,
    )
    print(f"\nTema:     {content.theme}")
    print(f"Eyebrow:  {content.eyebrow}")
    print(f"Headline: {content.headline}")
    print(f"Subline:  {content.subline}")
    print(f"Accent:   {content.accent_word}")
    print(f"Caption:  {content.caption[:200]}...")
    print(f"Hashtags: {content.hashtags}")
