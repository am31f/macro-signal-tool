"""
instagram_content_generator.py
Phase 8.2 — Kairós · MacroSignalTool

Genera contenuto editoriale brandizzato Kairós per Instagram a partire
dalla notizia top del giorno estratta dal pipeline segnali.

Output per ogni post:
  - hook_title     : titolo della slide 1 (max 60 char)
  - eyebrow        : categoria in maiuscolo (es. "GEOPOLITICA · ENERGIA")
  - causal_steps   : lista 3 step catena causale
  - bullish_sectors: settori/strumenti che beneficiano
  - bearish_sectors: settori/strumenti sotto pressione
  - slide_texts    : testi completi per ogni slide (5 slide)
  - caption        : testo caption Instagram (max 2200 char)
  - hashtags       : lista hashtag (max 30)
  - disclaimer     : disclaimer fisso in fondo

Tono di voce Kairós:
  - Nessun punto esclamativo, nessuna emoji, nessun linguaggio hype
  - Preciso sui numeri, sobrio sugli aggettivi
  - Italiano principale, termini tecnici in inglese dove appropriato
  - Fonte sempre citata

Testabile: python instagram_content_generator.py --test
"""

import json
import logging
import os
import argparse
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

try:
    import anthropic
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ig_content_gen")

MODEL = "claude-haiku-4-5-20251001"


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class IGCarouselContent:
    """Contenuto completo per un carosello Instagram Kairós."""
    # Slide 1 — Hook
    eyebrow: str          # es. "GEOPOLITICA · ENERGIA"
    hook_title: str       # titolo principale, max 60 char
    hook_subtitle: str    # sottotitolo breve, max 80 char
    date_label: str       # es. "3 maggio 2026"

    # Slide 2 — Contesto
    context_title: str
    context_stats: list   # lista di {icon, value, label} per 3 bullet

    # Slide 3 — Storico
    historical_title: str
    historical_rows: list # lista di {label, value, positive: bool}

    # Slide 4 — Settori
    sectors_title: str
    bullish_sectors: str  # testo libero con virgola
    bearish_sectors: str  # testo libero con virgola

    # Slide 5 — CTA
    cta_question: str     # es. "Vuoi i segnali operativi?"
    cta_body: str         # 1-2 righe
    cta_channel: str      # es. "@Kairós su Telegram"

    # Caption e hashtag
    caption: str
    hashtags: list        # lista stringhe senza #

    # Meta
    source_label: str     # es. "Reuters · Bloomberg"
    event_category: str
    signal_id: str
    disclaimer: str = "Contenuto informativo · Non è consulenza finanziaria · Elaborato da IA su fonti pubbliche"


# ─── Generator principale ─────────────────────────────────────────────────────

def generate_carousel_content(
    signal: dict,
    trade_structure: dict,
) -> Optional[IGCarouselContent]:
    """
    Genera il contenuto editoriale Kairós per un carosello Instagram.

    Args:
        signal: dict del segnale (da signal_pipeline)
        trade_structure: dict della struttura trade (da trade_structurer)

    Returns:
        IGCarouselContent o None se generazione fallisce
    """
    if not ANTHROPIC_AVAILABLE:
        logger.warning("Anthropic non disponibile — uso contenuto mock")
        return _mock_content(signal)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY non configurata — uso contenuto mock")
        return _mock_content(signal)

    try:
        client = anthropic.Anthropic(api_key=api_key)

        headline = signal.get("headline", "")
        category = signal.get("event_category", "MACRO")
        causal_chain = signal.get("causal_chain", "")
        materiality = signal.get("materiality_score", 0.7)
        instruments = trade_structure.get("instruments", [])
        primary_thesis = trade_structure.get("primary_thesis", "")

        bullish = [i for i in instruments if i.get("direction") == "LONG"]
        bearish = [i for i in instruments if i.get("direction") == "SHORT"]
        bullish_names = ", ".join([i.get("name", i.get("ticker", "")) for i in bullish[:3]])
        bearish_names = ", ".join([i.get("name", i.get("ticker", "")) for i in bearish[:3]])

        CAROUSEL_SYSTEM = """Sei il redattore editoriale di Kairós, una pubblicazione macro finanziaria italiana.
Il tuo tono: preciso, sobrio, autorevole. Nessun punto esclamativo. Nessuna emoji. Nessun linguaggio hype.
Sei come un central banker che legge romanzi: preciso sui numeri, elegante nelle parole.

Genera contenuto per caroselli Instagram di 5 slide in formato JSON ESATTO.
Struttura JSON richiesta:
{
  "eyebrow": "CATEGORIA · SOTTOCATEGORIA (max 30 char, maiuscolo, es. GEOPOLITICA · ENERGIA)",
  "hook_title": "Titolo che ferma lo scroll (max 55 char, in italiano, sobrio, senza ! )",
  "hook_subtitle": "Sottotitolo che introduce il tema (max 80 char)",
  "context_title": "Titolo slide 2 — il contesto (max 40 char)",
  "context_stats": [
    {"value": "20%", "label": "del petrolio mondiale transita da Hormuz"},
    {"value": "17M", "label": "barili al giorno riforniscono Europa e Asia"},
    {"value": "48h", "label": "di chiusura bastano per far salire il Brent"}
  ],
  "historical_title": "In eventi simili, i mercati si sono mossi così",
  "historical_rows": [
    {"label": "Petrolio (Brent)", "value": "+25% / +35%", "positive": true},
    {"label": "Titoli energia", "value": "+10% / +18%", "positive": true},
    {"label": "Compagnie aeree", "value": "-8% / -15%", "positive": false}
  ],
  "sectors_title": "Cosa tenere d'occhio",
  "bullish_sectors": "Energia integrata, produttori petrolio USA, oro",
  "bearish_sectors": "Compagnie aeree, shipping, manifattura energy-intensive",
  "cta_question": "Ogni mattina analizziamo l'evento che muoverà i mercati",
  "cta_body": "Su Telegram analizziamo anche i titoli azionari e gli strumenti potenzialmente impattati dall'evento del giorno.",
  "cta_channel": "@Kairós su Telegram",
  "caption": "Caption in 3 blocchi separati da doppio a capo. BLOCCO 1 (2-3 frasi): fatto con numeri precisi. BLOCCO 2 (1-2 frasi): impatto cross-asset, cosa cambia per chi investe. BLOCCO 3 (1 frase): CTA specifica verso Telegram. Formato: 'Sul canale: [cosa specifico]. Link in bio.' Vincoli: nessuna emoji, nessun punto esclamativo, max 220 parole.",
  "hashtags": ["macroinvestor", "macroresearch", "ratesmarkets", "kairosmacro"]
}
Adatta tutti i campi alla notizia specifica. Rispondi SOLO con il JSON, nessun testo aggiuntivo."""

        prompt = f"""NOTIZIA:
Titolo: {headline}
Categoria: {category}
Catena causale: {causal_chain}
Materialità: {materiality:.0%}
Tesi principale: {primary_thesis}
Strumenti beneficio: {bullish_names or "da definire"}
Strumenti pressione: {bearish_names or "da definire"}

Genera il contenuto per il carosello Instagram seguendo esattamente la struttura JSON definita."""

        response = client.messages.create(
            model=MODEL,
            max_tokens=1200,
            system=[{"type": "text", "text": CAROUSEL_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        # Pulisci eventuale markdown wrapper
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        data = json.loads(raw)

        # Costruisci data label
        from datetime import datetime
        today = datetime.now()
        months_it = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
                     "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
        date_label = f"{today.day} {months_it[today.month - 1]} {today.year}"

        source_label = _extract_source(headline)

        content = IGCarouselContent(
            eyebrow=data.get("eyebrow", category.replace("_", " ")),
            hook_title=data.get("hook_title", headline[:55]),
            hook_subtitle=data.get("hook_subtitle", ""),
            date_label=date_label,
            context_title=data.get("context_title", "Il contesto"),
            context_stats=data.get("context_stats", []),
            historical_title=data.get("historical_title", "Storico eventi simili"),
            historical_rows=data.get("historical_rows", []),
            sectors_title=data.get("sectors_title", "Cosa tenere d'occhio"),
            bullish_sectors=data.get("bullish_sectors", bullish_names),
            bearish_sectors=data.get("bearish_sectors", bearish_names),
            cta_question=data.get("cta_question", "Ogni mattina analizziamo l'evento che muoverà i mercati"),
            cta_body=data.get("cta_body", "Su Telegram analizziamo anche i titoli azionari e gli strumenti potenzialmente impattati."),
            cta_channel=data.get("cta_channel", "@Kairós su Telegram"),
            caption=data.get("caption", ""),
            hashtags=data.get("hashtags", []),
            source_label=source_label,
            event_category=category,
            signal_id=signal.get("news_id", ""),
        )

        logger.info(f"Contenuto generato per: {content.hook_title[:40]}...")
        return content

    except json.JSONDecodeError as e:
        logger.error(f"Errore parsing JSON da Claude: {e}")
        return _mock_content(signal)
    except Exception as e:
        logger.error(f"Errore generazione contenuto: {e}")
        return _mock_content(signal)


def _extract_source(headline: str) -> str:
    """Tenta di estrarre la fonte dal titolo notizia."""
    sources = ["Reuters", "Bloomberg", "FT", "Financial Times", "WSJ",
               "ANSA", "Sole 24 Ore", "BBC", "AP", "MarketWatch"]
    for s in sources:
        if s.lower() in headline.lower():
            return s
    return "Fonti pubbliche"


def _mock_content(signal: dict) -> IGCarouselContent:
    """Contenuto mock per test senza API key."""
    from datetime import datetime
    months_it = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
                 "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
    today = datetime.now()
    date_label = f"{today.day} {months_it[today.month - 1]} {today.year}"

    headline = signal.get("headline", "Notizia macro")
    category = signal.get("event_category", "MACRO")

    return IGCarouselContent(
        eyebrow=f"{category.replace('_', ' ')} · ANALISI",
        hook_title=headline[:55] if len(headline) > 55 else headline,
        hook_subtitle="Cosa significa per i mercati e i tuoi investimenti.",
        date_label=date_label,
        context_title="Il contesto",
        context_stats=[
            {"value": "—", "label": "Dati in elaborazione"},
            {"value": "—", "label": "Dati in elaborazione"},
            {"value": "—", "label": "Dati in elaborazione"},
        ],
        historical_title="In eventi simili, i mercati si sono mossi così",
        historical_rows=[
            {"label": "Asset primario", "value": "+15% / +25%", "positive": True},
            {"label": "Safe haven", "value": "+5% / +10%", "positive": True},
            {"label": "Settore esposto", "value": "-8% / -15%", "positive": False},
        ],
        sectors_title="Cosa tenere d'occhio",
        bullish_sectors="Energia, oro, materie prime",
        bearish_sectors="Trasporti, manifattura, consumer",
        cta_question="Ogni mattina analizziamo l'evento che muoverà i mercati",
        cta_body="Su Telegram analizziamo anche i titoli azionari e gli strumenti potenzialmente impattati dall'evento del giorno.",
        cta_channel="@Kairós su Telegram",
        caption=f"Analisi macro: {headline}\n\nQuesta notizia impatta i mercati attraverso una serie di canali interconnessi. Il nostro sistema di analisi ha identificato i settori e gli strumenti più esposti.\n\nSeguici per il contesto completo. I segnali operativi sono riservati al canale Telegram.\n\nContenuto informativo — non è consulenza finanziaria.",
        hashtags=["macro", "finanza", "mercati", "economia", "trading", "investimenti", "borsa", "geopolitica", "kairos", "analisi"],
        source_label="Fonti pubbliche",
        event_category=category,
        signal_id=signal.get("news_id", "mock"),
    )


# ─── Selezione notizia top ─────────────────────────────────────────────────────

def pick_top_signal(signals_cache_path: Path = None) -> tuple[dict, dict]:
    """
    Seleziona il segnale con confidence più alta dalla cache.
    Restituisce (signal, trade_structure).
    """
    if signals_cache_path is None:
        signals_cache_path = Path(__file__).parent / "signals_cache.json"

    if not signals_cache_path.exists():
        logger.warning("signals_cache.json non trovata — uso segnale mock")
        return _mock_signal_pair()

    try:
        with open(signals_cache_path, encoding="utf-8") as f:
            cache = json.load(f)

        if not cache:
            return _mock_signal_pair()

        # Ordina per confidence_composite decrescente
        def get_confidence(entry):
            sig = entry.get("signal", entry)
            return sig.get("confidence_composite", 0)

        best = max(cache, key=get_confidence)

        signal = best.get("signal", best)
        trade = best.get("trade_structure", {})

        logger.info(f"Segnale top: {signal.get('headline', '')[:60]} (confidence={signal.get('confidence_composite', 0):.2f})")
        return signal, trade

    except Exception as e:
        logger.error(f"Errore lettura signals_cache: {e}")
        return _mock_signal_pair()


def _mock_signal_pair() -> tuple[dict, dict]:
    return (
        {
            "news_id": "mock_001",
            "headline": "Iran chiude lo Stretto di Hormuz — tensioni ai massimi dal 2019",
            "event_category": "ENERGY_SUPPLY_SHOCK",
            "materiality_score": 0.92,
            "novelty_score": 0.88,
            "causal_chain": "Chiusura Hormuz → -20% offerta petrolio globale → Brent +25/35% → titoli energia +15%",
            "confidence_composite": 0.81,
        },
        {
            "trade_type": "directional",
            "primary_thesis": "Long energia su shock offerta Hormuz",
            "instruments": [
                {"ticker": "XLE", "name": "Energy Select SPDR", "direction": "LONG",
                 "instrument_type": "ETF", "weight_pct": 60},
                {"ticker": "GLD", "name": "SPDR Gold Shares", "direction": "LONG",
                 "instrument_type": "ETF", "weight_pct": 40},
            ],
            "stop_loss_pct": -7.5,
            "target_pct": 15.0,
            "conviction_pct": 82,
        }
    )


# ─── CLI test ─────────────────────────────────────────────────────────────────

def _run_test():
    print(f"\n{'='*60}")
    print("TEST: instagram_content_generator.py")
    print("="*60)

    signal, trade = pick_top_signal()
    print(f"\nSegnale selezionato: {signal.get('headline', '')[:60]}")
    print(f"Confidence: {signal.get('confidence_composite', 0):.2f}")

    content = generate_carousel_content(signal, trade)

    if content:
        print(f"\n✅ Contenuto generato:")
        print(f"   Eyebrow:     {content.eyebrow}")
        print(f"   Hook title:  {content.hook_title}")
        print(f"   Hook sub:    {content.hook_subtitle}")
        print(f"   Data:        {content.date_label}")
        print(f"\n   Slide 2 — Contesto: {content.context_title}")
        for s in content.context_stats:
            print(f"     {s.get('value','?')} — {s.get('label','')}")
        print(f"\n   Slide 3 — Storico: {content.historical_title}")
        for r in content.historical_rows:
            sign = "+" if r.get("positive") else ""
            print(f"     {r.get('label','?'):25} {r.get('value','')}")
        print(f"\n   Slide 4 — Settori:")
        print(f"     Bullish: {content.bullish_sectors}")
        print(f"     Bearish: {content.bearish_sectors}")
        print(f"\n   Slide 5 — CTA: {content.cta_question}")
        print(f"\n   Caption ({len(content.caption)} char):")
        print(f"   {content.caption[:200]}...")
        print(f"\n   Hashtag ({len(content.hashtags)}): #{' #'.join(content.hashtags[:5])}...")
        print(f"\n   Fonte: {content.source_label}")
        print(f"   Disclaimer: {content.disclaimer}")

        # Salva output come JSON per slide_renderer
        out_path = Path(__file__).parent / "ig_content_preview.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(asdict(content), f, indent=2, ensure_ascii=False)
        print(f"\n✅ Salvato in: {out_path}")
    else:
        print("❌ Generazione fallita")

    print("\n✅ Test completato.")


# ─── Contenuto PEAD per Instagram ────────────────────────────────────────────

def generate_pead_carousel_content(pead_signal: dict) -> Optional[IGCarouselContent]:
    """
    Genera contenuto carosello Instagram per un segnale PEAD.
    Badge: [EARNINGS] giallo — distinto da [MACRO] blu.

    Args:
        pead_signal: dict da PEADTradeReady (pead_pipeline.py)

    Returns:
        IGCarouselContent con eyebrow "EARNINGS · <SETTORE>" oppure None
    """
    from datetime import datetime

    ticker      = pead_signal.get("ticker", "?")
    company     = pead_signal.get("company_name", ticker)
    direction   = pead_signal.get("direction", "LONG")
    sue         = pead_signal.get("sue_score", 0)
    eps_surp    = pead_signal.get("eps_surprise_pct", 0)
    sector      = pead_signal.get("sector", "Unknown")
    signal_id   = pead_signal.get("signal_id", "K-PEAD")
    earn_date   = pead_signal.get("earnings_date", "")
    macro_boost = pead_signal.get("macro_regime_boost", False)
    macro_note  = pead_signal.get("macro_regime_note", "")
    conf        = pead_signal.get("confidence_base", 0)

    months_it = ["gennaio","febbraio","marzo","aprile","maggio","giugno",
                 "luglio","agosto","settembre","ottobre","novembre","dicembre"]
    today = datetime.now()
    date_label = f"{today.day} {months_it[today.month-1]} {today.year}"

    eps_sign = "+" if eps_surp >= 0 else ""
    dir_label = "rialzo" if direction == "LONG" else "ribasso"
    dir_sector = "beneficiano" if direction == "LONG" else "subiscono pressione"

    # Genera con Claude se disponibile, altrimenti mock
    if ANTHROPIC_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
        try:
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            prompt = f"""Sei il content team di Kairós. Genera il contenuto per un carosello Instagram
su un segnale PEAD (Post-Earnings Announcement Drift).

Segnale:
- Ticker: {ticker} ({company})
- Settore: {sector}
- Direzione: {direction}
- Sorpresa EPS: {eps_sign}{eps_surp:.1f}%
- SUE score: {sue:.2f} (significativo se >= 2)
- Data earnings: {earn_date}
- Macro regime boost: {macro_boost} {('— ' + macro_note) if macro_boost else ''}

Genera un JSON con questa struttura (senza markdown, solo JSON valido):
{{
  "hook_title": "titolo slide 1, max 55 char, in italiano",
  "hook_subtitle": "sottotitolo, max 80 char",
  "context_stats": [
    {{"value": "...", "label": "..."}},
    {{"value": "...", "label": "..."}},
    {{"value": "...", "label": "..."}}
  ],
  "sectors_benefiting": "2-3 settori/ETF che {dir_sector}",
  "sectors_pressure": "2-3 settori/ETF opposti",
  "caption": "caption Instagram max 800 char, tono Kairós — sobrio, preciso, no hype",
  "hashtags": ["earnings", "analisi", ...] // max 10 tag
}}

Tono Kairós: preciso sui numeri, sobrio sugli aggettivi, no punti esclamativi, no emoji."""

            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            parsed = json.loads(raw)

            return IGCarouselContent(
                eyebrow=f"EARNINGS · {sector.upper()[:20]}",
                hook_title=parsed.get("hook_title", f"{ticker} batte le stime EPS"),
                hook_subtitle=parsed.get("hook_subtitle", f"Sorpresa {eps_sign}{eps_surp:.1f}% — cosa cambia per il titolo"),
                date_label=date_label,
                context_title="Il dato che conta",
                context_stats=parsed.get("context_stats", [
                    {"value": f"{eps_sign}{eps_surp:.1f}%", "label": "Sorpresa EPS"},
                    {"value": f"{sue:.1f}σ", "label": "SUE score"},
                    {"value": f"{int(conf*100)}%", "label": "Confidence"},
                ]),
                historical_title="Post-earnings drift — cosa dice la ricerca",
                historical_rows=[
                    {"label": "Drift medio 30gg (SUE>2)", "value": "+4% / +8%", "positive": direction == "LONG"},
                    {"label": "Win rate storico", "value": "62%", "positive": True},
                    {"label": "R/R obiettivo", "value": "2:1", "positive": True},
                ],
                sectors_title="Cosa osserviamo",
                bullish_sectors=parsed.get("sectors_benefiting", sector),
                bearish_sectors=parsed.get("sectors_pressure", "–"),
                cta_question="Segui i segnali PEAD su Telegram",
                cta_body="I trade operativi con entry, stop e target sono disponibili nel canale Telegram riservato agli iscritti.",
                cta_channel="@Kairós su Telegram",
                caption=parsed.get("caption", f"Earnings {ticker}: sorpresa {eps_sign}{eps_surp:.1f}%\n\nSegnale PEAD attivo — {direction} con drift atteso."),
                hashtags=parsed.get("hashtags", ["earnings", "analisi", "borsa", "kairos", "pead"]),
                source_label="Yahoo Finance · Consensus analisti",
                event_category="EARNINGS_PEAD",
                signal_id=signal_id,
            )
        except Exception as e:
            logger.warning(f"Errore generazione PEAD content: {e} — uso mock")

    # Mock fallback
    return IGCarouselContent(
        eyebrow=f"EARNINGS · {sector.upper()[:20]}",
        hook_title=f"{ticker}: sorpresa EPS {eps_sign}{eps_surp:.1f}%",
        hook_subtitle=f"Il titolo potrebbe muoversi al {dir_label} nelle prossime settimane.",
        date_label=date_label,
        context_title="Il dato che conta",
        context_stats=[
            {"value": f"{eps_sign}{eps_surp:.1f}%", "label": "Sorpresa EPS"},
            {"value": f"{sue:.1f}σ", "label": "SUE score"},
            {"value": f"{int(conf*100)}%", "label": "Confidence"},
        ],
        historical_title="Post-earnings drift — cosa dice la ricerca",
        historical_rows=[
            {"label": "Drift medio 30gg (SUE>2)", "value": "+4% / +8%", "positive": direction == "LONG"},
            {"label": "Win rate storico PEAD", "value": "62%", "positive": True},
            {"label": "R/R obiettivo", "value": "2:1", "positive": True},
        ],
        sectors_title="Cosa osserviamo",
        bullish_sectors=sector if direction == "LONG" else "–",
        bearish_sectors=sector if direction == "SHORT" else "–",
        cta_question="Segui i segnali PEAD su Telegram",
        cta_body="I trade operativi con entry, stop e target sono nel canale riservato.",
        cta_channel="@Kairós su Telegram",
        caption=f"Earnings {ticker} ({company}): sorpresa EPS {eps_sign}{eps_surp:.1f}%.\n\nSUE score {sue:.1f}σ — segnale PEAD {direction}.\n\nContenuto informativo — non è consulenza finanziaria.",
        hashtags=["earnings", "pead", "analisi", "borsa", "kairos", "mercati"],
        source_label="Yahoo Finance · Consensus analisti",
        event_category="EARNINGS_PEAD",
        signal_id=signal_id,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Instagram Content Generator - Kairos")
    parser.add_argument("--test", action="store_true", help="Test generazione contenuto")
    parser.add_argument("--cache", default="signals_cache.json", help="Path cache segnali")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.test:
        import asyncio
        content = asyncio.run(generate_carousel_content(args.cache))
        if content:
            print(f"\nContenuto generato:")
            print(f"  Hook: {content.hook_title}")
            print(f"  Eyebrow: {content.eyebrow}")
            print(f"  CTA channel: {content.cta_channel}")
        else:
            print("Generazione fallita")
