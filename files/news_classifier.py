"""
news_classifier.py
Phase 2, Task 2.2 — MacroSignalTool

Classificazione a DUE LIVELLI per minimizzare i costi API:

  Livello 1 — Pre-scrematura (Haiku, ~50x più economico):
    Risponde solo: {"relevant": true/false, "reason": "..."}
    Scarta cronaca locale, sport, tech consumer, lifestyle.
    Passa solo notizie potenzialmente macro-rilevanti.

  Livello 2 — Analisi completa (Sonnet):
    Solo sulle news che hanno passato il Livello 1.
    Produce JSON strutturato completo: categoria, materialità, timing, ecc.

Risparmio tipico: ~85-90% dei costi API sul batch di classificazione.

Dipendenze: pip install anthropic
Richiede: ANTHROPIC_API_KEY in .env o variabile d'ambiente
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

import anthropic

# Import dal modulo di ingestion
import sys
sys.path.insert(0, str(Path(__file__).parent))
from news_ingestion import get_unclassified, mark_classified, DB_PATH

# ─── Configurazione logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
log = logging.getLogger("news_classifier")

# ─── Modelli ──────────────────────────────────────────────────────────────────
MODEL_PRESCREENING = "claude-haiku-4-5-20251001"   # Livello 1 — economico
MODEL_ANALYSIS     = "claude-sonnet-4-20250514"     # Livello 2 — completo

# ─── Client Anthropic ─────────────────────────────────────────────────────────
def get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Fallback: prova a leggere dal .env nella root
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"')
                    break
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY non trovata. Settala in .env o come variabile d'ambiente."
        )
    return anthropic.Anthropic(api_key=api_key)


# ─── System prompt (da macro_trading_tool.json engine.claude_system_prompt_news_classifier) ─
SYSTEM_PROMPT = """Sei un analista macro senior di un top-tier hedge fund (stile Bridgewater/Brevan Howard).
Ricevi headline e snippet di una notizia e devi restituire SOLO un JSON valido, senza testo aggiuntivo, senza markdown, senza spiegazioni.

Struttura JSON richiesta:
{
  "event_category": "<ENERGY_SUPPLY_SHOCK|MILITARY_CONFLICT|SANCTIONS_IMPOSED|CENTRAL_BANK_SURPRISE|TRADE_WAR_TARIFF|CYBER_ATTACK|SOVEREIGN_CRISIS|COMMODITY_SUPPLY_AGRI|NUCLEAR_THREAT|ELECTION_SURPRISE|PANDEMIC_HEALTH|INFRASTRUCTURE_DISRUPTION|NONE>",
  "materiality_score": <float 0.0-1.0 — quanto questa news può spostare variabili macro misurabili: GDP, inflazione, commodity prices, risk premia>,
  "novelty_score": <float 0.0-1.0 — quanto è NUOVA vs già nota/prezzata dal mercato. 0.0=già vecchia, 1.0=shock totale>,
  "causal_chain": "<stringa max 150 chars: EVENTO → effetto1 → effetto2 → IMPATTO ASSET>",
  "affected_regions": ["<US|EU|MIDDLE_EAST|RUSSIA_CIS|CHINA|ASIA_EX_CHINA|EM|GLOBAL>"],
  "asset_directions": {
    "LONG": ["<ticker o categoria, es: XLE, GLD, defense_ETF, short_USD>"],
    "SHORT": ["<ticker o categoria>"]
  },
  "confidence": <float 0.0-1.0 — tua confidenza nella classificazione>,
  "entry_timing_recommendation": "<T0_OPTIONS_ONLY|T1|T3|WAIT_CONFIRM>",
  "half_life_days": <int — giorni stimati di validità del segnale prima che si dissipi>,
  "already_priced_risk": <float 0.0-1.0 — probabilità che il mercato abbia già scontato questa news>,
  "macro_regime": "<RISK_ON|RISK_OFF|INFLATIONARY_SHOCK|DEFLATIONARY_SHOCK|STAGFLATION|MIXED>"
}

Regole di classificazione:
- materiality_score >= 0.65: notizia che può muovere asset >2% in 24h
- materiality_score 0.40-0.64: rilevante ma non immediata
- materiality_score < 0.40: background noise, usa NONE
- novelty_score >= 0.7: evento davvero nuovo (non escalation lenta già nota)
- entry_timing: T0_OPTIONS_ONLY se troppo incerto; T1 se cross-asset da confermare; T3 se drift lento; WAIT_CONFIRM se segnali contraddittori
- already_priced_risk >= 0.8: segnale vecchio, probabilmente da skippare
- Se la notizia non è macro-rilevante: event_category=NONE, materiality_score<0.3, tutti gli altri campi minimal

Esempi di calibrazione:
- "Fed alza tassi di 25bp come atteso": materiality=0.2, novelty=0.1, already_priced=0.95
- "Iran chiude Stretto di Hormuz": materiality=0.95, novelty=0.95, already_priced=0.0
- "Erdogan vince elezioni": materiality=0.55, novelty=0.6, already_priced=0.3
- "Apple presenta nuovo iPhone": materiality=0.15, event_category=NONE"""


# ─── Prompt pre-scrematura Haiku (Livello 1) ─────────────────────────────────
PRESCREENING_SYSTEM = """Sei un filtro rapido per un sistema di trading macro.
Ricevi l'headline di una notizia e devi rispondere SOLO con un JSON valido:
{"relevant": true/false, "reason": "<max 60 chars>"}

Rispondi TRUE se la notizia riguarda ALMENO UNO di:
- Conflitti militari, sanzioni, tensioni geopolitiche
- Banche centrali, tassi d'interesse, politica monetaria
- Commodity (petrolio, gas, grano, metalli)
- Crisi sovrane, elezioni che cambiano politica economica
- Tariffe doganali, guerre commerciali
- Pandemia, cyberattack su infrastrutture critiche
- Shock energetici o climatici con impatto macro

Rispondi FALSE se la notizia riguarda:
- Cronaca locale (crimini, incidenti, omicidi)
- Sport, intrattenimento, celebrity
- Tecnologia consumer (nuovi iPhone, app, social media)
- Salute individuale, medicina non pandemica
- Notizie culturali, lifestyle, viaggi
- Qualsiasi notizia già ampiamente nota da >3 giorni"""


def prescreen_news(headline: str, client: anthropic.Anthropic) -> tuple[bool, str]:
    """
    Livello 1: chiama Haiku per decidere se la news vale un'analisi completa.
    Ritorna (is_relevant, reason). Molto economico: ~0.0003$/chiamata.
    """
    try:
        response = client.messages.create(
            model=MODEL_PRESCREENING,
            max_tokens=80,
            system=PRESCREENING_SYSTEM,
            messages=[{"role": "user", "content": f"HEADLINE: {headline}"}]
        )
        raw = response.content[0].text.strip()
        # Pulizia markdown se presente
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        return bool(parsed.get("relevant", False)), parsed.get("reason", "")
    except Exception as e:
        # In caso di errore, lascia passare la news (meglio falso positivo che perdere un segnale)
        log.warning(f"Prescreen error per '{headline[:60]}': {e} — passa al Livello 2")
        return True, "prescreen_error"


# ─── Struttura output classificazione ────────────────────────────────────────
@dataclass
class ClassificationResult:
    news_id: str
    headline: str
    event_category: str
    materiality_score: float
    novelty_score: float
    causal_chain: str
    affected_regions: list
    asset_directions: dict
    confidence: float
    entry_timing_recommendation: str
    half_life_days: int
    already_priced_risk: float
    macro_regime: str
    # Metadata
    classified_at: str = ""
    model_used: str = "claude-sonnet-4-20250514"
    raw_response: str = ""
    parse_error: bool = False
    prescreened_out: bool = False   # True = scartata da Haiku al Livello 1
    prescreen_reason: str = ""      # Motivo del filtro Haiku


EMPTY_CLASSIFICATION = {
    "event_category": "NONE",
    "materiality_score": 0.0,
    "novelty_score": 0.0,
    "causal_chain": "",
    "affected_regions": [],
    "asset_directions": {"LONG": [], "SHORT": []},
    "confidence": 0.0,
    "entry_timing_recommendation": "WAIT_CONFIRM",
    "half_life_days": 0,
    "already_priced_risk": 1.0,
    "macro_regime": "MIXED"
}


def classify_news(
    news_id: str,
    headline: str,
    snippet: str,
    source: str,
    client: anthropic.Anthropic,
    max_retries: int = 2
) -> ClassificationResult:
    """
    Chiama Claude API per classificare una singola news.
    Gestisce retry su errori parsing JSON.
    """
    from datetime import datetime, timezone

    user_message = f"""SOURCE: {source}
HEADLINE: {headline}
SNIPPET: {snippet}

Classifica questa notizia secondo il formato JSON richiesto."""

    raw = ""
    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=600,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}]
            )
            raw = response.content[0].text.strip()

            # Rimuovi eventuale markdown fence ```json ... ```
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            parsed = json.loads(raw)

            # Merge con defaults per campi mancanti
            result_dict = {**EMPTY_CLASSIFICATION, **parsed}

            now_iso = datetime.now(timezone.utc).isoformat()

            return ClassificationResult(
                news_id=news_id,
                headline=headline,
                classified_at=now_iso,
                raw_response=raw,
                **{k: result_dict[k] for k in EMPTY_CLASSIFICATION}
            )

        except json.JSONDecodeError as e:
            log.warning(f"JSON parse error (attempt {attempt+1}/{max_retries+1}): {e}")
            log.debug(f"Raw response: {raw[:300]}")
            if attempt == max_retries:
                log.error(f"Classificazione fallita dopo {max_retries+1} tentativi: {headline[:80]}")
                return ClassificationResult(
                    news_id=news_id,
                    headline=headline,
                    classified_at=datetime.now(timezone.utc).isoformat(),
                    parse_error=True,
                    raw_response=raw,
                    **EMPTY_CLASSIFICATION
                )
            time.sleep(1)

        except anthropic.APIRateLimitError:
            log.warning("Rate limit hit, attendo 30s...")
            time.sleep(30)

        except anthropic.APIError as e:
            log.error(f"Errore API Anthropic: {e}")
            return ClassificationResult(
                news_id=news_id,
                headline=headline,
                classified_at=datetime.now(timezone.utc).isoformat(),
                parse_error=True,
                raw_response=str(e),
                **EMPTY_CLASSIFICATION
            )

    # Unreachable ma per sicurezza
    return ClassificationResult(
        news_id=news_id,
        headline=headline,
        classified_at="",
        parse_error=True,
        raw_response="",
        **EMPTY_CLASSIFICATION
    )


def is_signal_candidate(result: ClassificationResult) -> bool:
    """
    Pre-filtro rapido: questa news vale la pena di passarla alla pipeline segnali?
    Filtri minimi (i 5 filtri completi sono in signal_pipeline.py).
    """
    if result.event_category == "NONE":
        return False
    if result.materiality_score < 0.45:
        return False
    if result.already_priced_risk > 0.85:
        return False
    if result.confidence < 0.40:
        return False
    return True


def run_classification_batch(
    limit: int = 50,
    save_to_db: bool = True,
    min_materiality_to_log: float = 0.5
) -> list[ClassificationResult]:
    """
    Classifica le news non classificate usando il sistema a DUE LIVELLI:

    Livello 1 (Haiku): pre-scrematura rapida ed economica su tutte le news.
    Livello 2 (Sonnet): analisi completa solo sulle news passate al Livello 1.

    Args:
        limit: quante news processare in questo batch
        save_to_db: se aggiornare il DB SQLite
        min_materiality_to_log: soglia materialità per log dettagliato

    Returns:
        Lista di ClassificationResult (tutte, incluse quelle filtrate da Haiku)
    """
    from datetime import datetime, timezone

    client = get_client()
    unclassified = get_unclassified(limit=limit)

    if not unclassified:
        log.info("Nessuna news non classificata trovata.")
        return []

    log.info(f"=== Classificazione batch: {len(unclassified)} news (2 livelli) ===")

    results = []
    signal_candidates = []
    prescreened_out_count = 0
    sonnet_calls = 0

    for i, news in enumerate(unclassified):
        headline = news["headline"]
        log.info(f"[{i+1}/{len(unclassified)}] {headline[:80]}")

        # ── Livello 1: pre-scrematura Haiku ──────────────────────────────────
        is_relevant, prescreen_reason = prescreen_news(headline, client)

        if not is_relevant:
            log.info(f"  ⊘ Haiku scarta: {prescreen_reason}")
            prescreened_out_count += 1
            # Crea un risultato NONE per segnare la news come classificata nel DB
            result = ClassificationResult(
                news_id=news["id"],
                headline=headline,
                classified_at=datetime.now(timezone.utc).isoformat(),
                model_used=MODEL_PRESCREENING,
                prescreened_out=True,
                prescreen_reason=prescreen_reason,
                **EMPTY_CLASSIFICATION
            )
        else:
            # ── Livello 2: analisi completa Sonnet ───────────────────────────
            log.info(f"  ✓ Haiku approva → Sonnet analizza")
            sonnet_calls += 1
            result = classify_news(
                news_id=news["id"],
                headline=headline,
                snippet=news.get("full_text_snippet", ""),
                source=news.get("source", "unknown"),
                client=client
            )
            result.prescreen_reason = prescreen_reason

        results.append(result)

        # Aggiorna DB (marca come classificata)
        if save_to_db:
            mark_classified(
                news_id=result.news_id,
                result=asdict(result),
                materiality_score=result.materiality_score
            )

        # Log segnali rilevanti
        if result.materiality_score >= min_materiality_to_log:
            log.info(
                f"  ★ {result.event_category} | "
                f"materiality={result.materiality_score:.2f} | "
                f"novelty={result.novelty_score:.2f} | "
                f"priced={result.already_priced_risk:.2f} | "
                f"timing={result.entry_timing_recommendation}"
            )
            log.info(f"    {result.causal_chain}")

        if is_signal_candidate(result):
            signal_candidates.append(result)

        time.sleep(0.2)

    log.info(f"\n=== Classificazione completata ===")
    log.info(f"  Totale news:          {len(results)}")
    log.info(f"  Filtrate da Haiku:    {prescreened_out_count} (Livello 1)")
    log.info(f"  Analizzate da Sonnet: {sonnet_calls} (Livello 2)")
    log.info(f"  Signal candidates:    {len(signal_candidates)}")
    log.info(f"  Parse errors:         {sum(1 for r in results if r.parse_error)}")
    if len(results) > 0:
        savings_pct = int(prescreened_out_count / len(results) * 100)
        log.info(f"  💰 Risparmio API stimato: ~{savings_pct}% chiamate Sonnet evitate")

    return results


def classify_single(headline: str, snippet: str = "", source: str = "manual") -> dict:
    """
    Utility per classificare una singola news manualmente (test / CLI).
    Ritorna dict invece di ClassificationResult per semplicità.
    """
    client = get_client()
    result = classify_news(
        news_id="manual_test",
        headline=headline,
        snippet=snippet,
        source=source,
        client=client
    )
    return asdict(result)


# ─── Esecuzione diretta ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        # Test con una notizia hardcoded
        print("\n=== TEST CLASSIFICAZIONE SINGOLA ===\n")
        test_headline = "Iran military forces seize control of Strait of Hormuz, threatening global oil shipments"
        test_snippet = "Iranian naval forces have blocked commercial tanker traffic through the Strait of Hormuz following escalating tensions with US carrier group. Oil futures spiked 8% in after-hours trading."

        result = classify_single(test_headline, test_snippet, source="Reuters")

        print(json.dumps(result, indent=2, ensure_ascii=False))

        print(f"\n→ Signal candidate: {is_signal_candidate(ClassificationResult(**{k: result[k] for k in result if k in EMPTY_CLASSIFICATION}, news_id='test', headline=test_headline))}")

    else:
        # Batch normale
        results = run_classification_batch(limit=20)

        print(f"\n{'='*60}")
        print(f"Classificate: {len(results)}")
        signal_list = [r for r in results if is_signal_candidate(r)]
        print(f"Signal candidates: {len(signal_list)}")

        for r in signal_list:
            print(f"\n  [{r.event_category}] materiality={r.materiality_score:.2f}")
            print(f"  {r.headline[:100]}")
            print(f"  → {r.causal_chain}")
            print(f"  LONG: {r.asset_directions.get('LONG', [])}")
            print(f"  SHORT: {r.asset_directions.get('SHORT', [])}")
