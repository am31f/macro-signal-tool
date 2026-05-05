"""
story_generator.py
Kairós — Generatore contenuto Story giornaliera (12:00 CET)

Ogni giorno genera una Story con un dato macro sorprendente o un insight breve,
in rotazione su 3 temi settimanali:
  Lunedì/Giovedì   → DATO MACRO    (numero + contesto)
  Martedì/Venerdì  → LO SAPEVI CHE (curiosità storica o di mercato)
  Mercoledì/Sabato → PAROLA DEL GIORNO (termine finanziario spiegato)
  Domenica         → RECAP SETTIMANA

Usa Claude Haiku per generare contenuto fresco ancorato ai segnali del giorno.
Fallback statico se API non disponibile.
"""

import json
import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("story_generator")

# ─── Temi per giorno della settimana ──────────────────────────────────────────
THEME_MAP = {
    0: "DATO_MACRO",       # Lunedì
    1: "LO_SAPEVI_CHE",    # Martedì
    2: "PAROLA_DEL_GIORNO",# Mercoledì
    3: "DATO_MACRO",       # Giovedì
    4: "LO_SAPEVI_CHE",    # Venerdì
    5: "PAROLA_DEL_GIORNO",# Sabato
    6: "RECAP_SETTIMANA",  # Domenica
}

THEME_LABELS = {
    "DATO_MACRO":        "📊 DATO MACRO",
    "LO_SAPEVI_CHE":     "💡 LO SAPEVI CHE...",
    "PAROLA_DEL_GIORNO": "📖 PAROLA DEL GIORNO",
    "RECAP_SETTIMANA":   "🗓 SETTIMANA IN CIFRE",
}


# ─── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class StoryContent:
    theme: str                    # es. "DATO_MACRO"
    eyebrow: str                  # label in alto (es. "📊 DATO MACRO")
    headline: str                 # testo principale (grande)
    subline: str                  # riga secondaria sotto
    accent_word: str              # parola da colorare in gold
    caption: str                  # caption Instagram
    hashtags: list = field(default_factory=list)


# ─── Fallback statico ─────────────────────────────────────────────────────────

_FALLBACK = {
    "DATO_MACRO": [
        StoryContent(
            theme="DATO_MACRO",
            eyebrow="📊 DATO MACRO",
            headline="Il PIL mondiale supera i 100 trilioni di dollari",
            subline="Per la prima volta nella storia economica globale.",
            accent_word="100 trilioni",
            caption="Un numero che ridefinisce la scala dell'economia globale. Kairós analizza ogni mattina i dati che contano davvero.",
            hashtags=["macroeconomia", "PIL", "economia", "mercatifinanziari", "kairos"],
        ),
        StoryContent(
            theme="DATO_MACRO",
            eyebrow="📊 DATO MACRO",
            headline="L'oro ha battuto l'S&P 500 nel 2024",
            subline="+27% contro +23% — il metallo giallo torna protagonista.",
            accent_word="oro",
            caption="Quando l'oro batte le azioni, il mercato sta mandando un segnale preciso. Kairós lo legge per te.",
            hashtags=["oro", "gold", "SP500", "investimenti", "kairos"],
        ),
        StoryContent(
            theme="DATO_MACRO",
            eyebrow="📊 DATO MACRO",
            headline="La Fed ha alzato i tassi 11 volte in 16 mesi",
            subline="Il ciclo di rialzi più rapido dal 1980.",
            accent_word="11 volte",
            caption="Capire la Fed significa capire dove vanno i mercati. Ogni mattina su Kairós.",
            hashtags=["Fed", "tassi", "politicamonetaria", "macroeconomia", "kairos"],
        ),
    ],
    "LO_SAPEVI_CHE": [
        StoryContent(
            theme="LO_SAPEVI_CHE",
            eyebrow="💡 LO SAPEVI CHE...",
            headline="Il NYSE esiste dal 1792",
            subline="Fondato sotto un albero di platano a Wall Street.",
            accent_word="1792",
            caption="La storia dei mercati è piena di momenti che hanno cambiato tutto. Kairós li studia per anticipare i prossimi.",
            hashtags=["storia", "wallstreet", "NYSE", "finanza", "kairos"],
        ),
        StoryContent(
            theme="LO_SAPEVI_CHE",
            eyebrow="💡 LO SAPEVI CHE...",
            headline="Il Bitcoin ha perso il 80% quattro volte",
            subline="E ogni volta è tornato a nuovi massimi.",
            accent_word="80%",
            caption="I cicli di mercato si ripetono. Kairós analizza i pattern per capire dove siamo.",
            hashtags=["bitcoin", "crypto", "ciclieconomici", "trading", "kairos"],
        ),
        StoryContent(
            theme="LO_SAPEVI_CHE",
            eyebrow="💡 LO SAPEVI CHE...",
            headline="Warren Buffett ha guadagnato il 99% della sua ricchezza dopo i 50 anni",
            subline="Il potere del tempo nel compounding.",
            accent_word="99%",
            caption="La pazienza è la strategia più sottovalutata in finanza. Kairós ti aiuta a capire quando agire e quando aspettare.",
            hashtags=["Buffett", "investimenti", "compounding", "finanza", "kairos"],
        ),
    ],
    "PAROLA_DEL_GIORNO": [
        StoryContent(
            theme="PAROLA_DEL_GIORNO",
            eyebrow="📖 PAROLA DEL GIORNO",
            headline="Contango",
            subline="Quando il prezzo futuro supera quello spot — il mercato prezza scarsità futura.",
            accent_word="Contango",
            caption="Capire il linguaggio dei mercati è il primo passo per anticiparli. Ogni giorno una parola nuova su Kairós.",
            hashtags=["finanza", "trading", "glossario", "mercati", "kairos"],
        ),
        StoryContent(
            theme="PAROLA_DEL_GIORNO",
            eyebrow="📖 PAROLA DEL GIORNO",
            headline="Yield Curve",
            subline="La forma della curva dei rendimenti anticipa le recessioni da decenni.",
            accent_word="Yield Curve",
            caption="La curva dei rendimenti è uno degli indicatori più potenti in macroeconomia. Kairós la monitora per te.",
            hashtags=["yieldcurve", "obbligazioni", "recessione", "macroeconomia", "kairos"],
        ),
        StoryContent(
            theme="PAROLA_DEL_GIORNO",
            eyebrow="📖 PAROLA DEL GIORNO",
            headline="Carry Trade",
            subline="Prendi in prestito dove costa poco, investi dove rende di più.",
            accent_word="Carry Trade",
            caption="Il carry trade muove trilioni di dollari ogni giorno. Kairós spiega i meccanismi che guidano i mercati globali.",
            hashtags=["carrytrade", "forex", "macroeconomia", "trading", "kairos"],
        ),
    ],
    "RECAP_SETTIMANA": [
        StoryContent(
            theme="RECAP_SETTIMANA",
            eyebrow="🗓 SETTIMANA IN CIFRE",
            headline="Questa settimana sui mercati",
            subline="Analisi dei movimenti macro più rilevanti della settimana.",
            accent_word="mercati",
            caption="Ogni domenica Kairós fa il punto sui movimenti macro della settimana. Seguici per non perderti nulla.",
            hashtags=["recap", "mercati", "settimana", "macroeconomia", "kairos"],
        ),
    ],
}

# Indice round-robin per i fallback (in memoria, si azzera al restart)
_fallback_idx: dict = {k: 0 for k in _FALLBACK}


def _next_fallback(theme: str) -> StoryContent:
    items = _FALLBACK.get(theme, _FALLBACK["DATO_MACRO"])
    idx = _fallback_idx.get(theme, 0)
    content = items[idx % len(items)]
    _fallback_idx[theme] = idx + 1
    return content


# ─── Generazione con Haiku ────────────────────────────────────────────────────

def _generate_with_haiku(theme: str, signal_headline: str) -> Optional[StoryContent]:
    """Genera contenuto Story con Claude Haiku, ancorato al segnale del giorno."""
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None

        client = anthropic.Anthropic(api_key=api_key)
        label = THEME_LABELS.get(theme, theme)

        theme_instructions = {
            "DATO_MACRO": (
                "Genera UN dato macro sorprendente e verificabile collegato all'evento del giorno. "
                "Il dato deve essere specifico (con numero reale), breve (max 8 parole), e lasciare "
                "l'utente con la voglia di saperne di più."
            ),
            "LO_SAPEVI_CHE": (
                "Genera UNA curiosità storica o di mercato sorprendente collegata all'evento del giorno. "
                "Dev'essere una cosa che in pochi conoscono, verificabile, con un twist inaspettato."
            ),
            "PAROLA_DEL_GIORNO": (
                "Scegli UN termine finanziario o macro rilevante per l'evento del giorno. "
                "La definizione deve essere in max 12 parole, chiara, senza gergo accademico."
            ),
            "RECAP_SETTIMANA": (
                "Genera un titolo breve che riassume il tema macro dominante della settimana. "
                "Max 8 parole, deve sembrare un titolo di giornale finanziario di qualità."
            ),
        }

        instruction = theme_instructions.get(theme, theme_instructions["DATO_MACRO"])

        prompt = f"""Sei il content strategist di Kairós, un canale Instagram di macro-finanza con tono editoriale serio e accessibile.

Evento/segnale del giorno: "{signal_headline}"
Tema Story: {label}
Istruzione: {instruction}

Rispondi SOLO con JSON valido, nessun testo extra:
{{
  "headline": "...",      // max 8 parole, impatto immediato
  "subline": "...",       // max 15 parole, contestualizza o approfondisce
  "accent_word": "...",   // UNA parola o numero dell'headline da colorare in gold
  "caption": "...",       // 1-2 frasi per la caption Instagram, tono Kairós
  "hashtags": ["...", "..."]  // 5 hashtag rilevanti senza #, lowercase
}}"""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Estrai JSON
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)

        # Sanitizza hashtag
        hashtags = []
        for h in data.get("hashtags", []):
            h = str(h).lower().replace(" ", "").replace("-", "").replace("#", "")
            if h:
                hashtags.append(h)
        if "kairos" not in hashtags:
            hashtags.append("kairos")

        return StoryContent(
            theme=theme,
            eyebrow=label,
            headline=data.get("headline", ""),
            subline=data.get("subline", ""),
            accent_word=data.get("accent_word", ""),
            caption=data.get("caption", ""),
            hashtags=hashtags,
        )

    except Exception as e:
        logger.warning(f"story_generator Haiku fallback: {e}")
        return None


# ─── Funzione principale ──────────────────────────────────────────────────────

def generate_story(
    signals_cache_path: Optional[str] = None,
    force_theme: Optional[str] = None,
) -> StoryContent:
    """
    Genera il contenuto della Story giornaliera.

    Args:
        signals_cache_path: percorso alla cache segnali (signals_cache.json)
        force_theme: forza un tema specifico (utile per test)

    Returns:
        StoryContent pronto per il renderer
    """
    # Determina tema
    weekday = datetime.now().weekday()
    theme = force_theme or THEME_MAP.get(weekday, "DATO_MACRO")
    logger.info(f"story_generator: tema={theme} (weekday={weekday})")

    # Leggi segnale principale dalla cache
    signal_headline = ""
    if signals_cache_path:
        try:
            with open(signals_cache_path) as f:
                data = json.load(f)
            signals = data.get("signals", data) if isinstance(data, dict) else data
            if signals:
                top = max(signals, key=lambda s: s.get("confidence_composite", s.get("confidence", 0)))
                signal_headline = top.get("headline", "")[:120]
                logger.info(f"story_generator: segnale -> {signal_headline[:60]}...")
        except Exception as e:
            logger.warning(f"story_generator: impossibile leggere cache — {e}")

    # Prova Haiku (solo se c'è un segnale reale)
    if signal_headline:
        content = _generate_with_haiku(theme, signal_headline)
        if content:
            logger.info("story_generator: contenuto generato con Haiku ✅")
            return content

    # Fallback statico
    logger.info("story_generator: uso fallback statico")
    return _next_fallback(theme)
