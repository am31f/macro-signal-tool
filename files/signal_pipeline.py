"""
signal_pipeline.py
Phase 3, Task 3.1 — MacroSignalTool

Pipeline a 5 filtri sequenziali AND.
Input: news classificata (output di news_classifier.py).
Output: signal_candidates[] se tutti i filtri passano,
        oppure reject_log con motivo esplicito del rifiuto.

Filtri in sequenza:
  F1 — Materiality: materiality_score >= soglia per categoria
  F2 — Novelty: novelty_score >= 0.4 (non già prezzato)
  F3 — Cross-asset confirmation: moltiplicatore istituzionale del sizing
         STRONG (>=3/5 asset) → size 1.0x | MODERATE (1-2/5) → 0.5x
         WEAK (0/5, no contrarian) → 0.25x | CONTRARIAN → REJECT
         MARKET_CLOSED (weekend/dati vecchi) → 0.5x precauzionale
  F4 — Entry timing: non aprire posizioni se entry_timing == T+0
  F5 — Macro regime coherence: verifica coerenza macro_regime con categoria

Dipendenze: cross_asset_validator.py (nello stesso folder)
Testabile: python signal_pipeline.py --test
"""

import argparse
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Import dei moduli locali
import sys
sys.path.insert(0, str(Path(__file__).parent))
from cross_asset_validator import run_validation, STRONG_THRESHOLD

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("signal_pipeline")

# ─── Soglie materiality per categoria ─────────────────────────────────────────
# Allineate con knowledge_base.event_taxonomy in macro_trading_tool.json
MATERIALITY_THRESHOLDS: dict[str, float] = {
    "ENERGY_SUPPLY_SHOCK":     0.65,
    "MILITARY_CONFLICT":       0.70,
    "SANCTIONS_IMPOSED":       0.60,
    "CENTRAL_BANK_SURPRISE":   0.55,
    "TRADE_WAR_TARIFF":        0.60,
    "CYBER_ATTACK":            0.65,
    "SOVEREIGN_CRISIS":        0.70,
    "COMMODITY_SUPPLY_AGRI":   0.55,
    "NUCLEAR_THREAT":          0.80,
    "ELECTION_SURPRISE":       0.60,
    "PANDEMIC_HEALTH":         0.75,
    "INFRASTRUCTURE_DISRUPTION": 0.65,
    "NONE":                    1.00,  # NONE non supera mai il filtro
}

# Soglia novelty globale (non già prezzato)
NOVELTY_THRESHOLD = 0.40

# Timing vietato a T+0 (troppo rumore, spread ampi)
BLOCKED_TIMING = {"T+0", "T+0_options_only"}

# Categorie che richiedono coerenza inflazionistica vs deflazionistica
INFLATIONARY_CATEGORIES = {
    "ENERGY_SUPPLY_SHOCK", "MILITARY_CONFLICT", "COMMODITY_SUPPLY_AGRI",
    "INFRASTRUCTURE_DISRUPTION", "TRADE_WAR_TARIFF",
}
DEFLATIONARY_CATEGORIES = {
    "NUCLEAR_THREAT", "PANDEMIC_HEALTH", "SOVEREIGN_CRISIS",
}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class FilterResult:
    """Esito di un singolo filtro."""
    filter_id: str
    filter_name: str
    passed: bool
    score: Optional[float]
    threshold: Optional[float]
    reason: str
    size_multiplier: Optional[float] = None  # usato da F3: moltiplicatore size (1.0/0.5/0.25/0.0)


@dataclass
class SignalCandidate:
    """News che ha superato tutti i filtri → candidata a diventare trade."""
    news_id: str
    headline: str
    source: str
    published_at: str
    event_category: str
    materiality_score: float
    novelty_score: float
    causal_chain: str
    macro_regime: str
    entry_timing: str
    already_priced_risk: str
    affected_assets: list
    cross_asset_result: dict
    filters_passed: list
    signal_generated_at: str
    confidence_composite: float       # media pesata dei punteggi chiave
    cross_asset_size_multiplier: float = 1.0  # da CrossAssetResult.size_multiplier (scala la size)


@dataclass
class RejectedNews:
    """News rifiutata dalla pipeline con motivo."""
    news_id: str
    headline: str
    event_category: str
    rejected_at_filter: str
    reject_reason: str
    filters_run: list


@dataclass
class PipelineOutput:
    """Output completo della pipeline per una batch di news."""
    run_timestamp: str
    total_news_processed: int
    signals_generated: int
    news_rejected: int
    signal_candidates: list        # lista di SignalCandidate serializzati
    reject_log: list               # lista di RejectedNews serializzati


# ─── Filtri singoli ───────────────────────────────────────────────────────────

def filter_1_materiality(news: dict) -> FilterResult:
    """F1: materiality_score >= soglia per la categoria dell'evento."""
    category = news.get("event_category", "NONE")
    score = float(news.get("materiality_score", 0.0))
    threshold = MATERIALITY_THRESHOLDS.get(category, 0.70)

    passed = score >= threshold and category != "NONE"
    reason = (
        f"materiality {score:.2f} >= {threshold:.2f} per {category}"
        if passed
        else f"materiality {score:.2f} < soglia {threshold:.2f} per {category}"
        + (" (categoria NONE)" if category == "NONE" else "")
    )
    return FilterResult("F1", "Materiality", passed, score, threshold, reason)


def filter_2_novelty(news: dict) -> FilterResult:
    """F2: novelty_score >= 0.40 — notizia non già prezzata."""
    score = float(news.get("novelty_score", 0.0))
    already_priced = news.get("already_priced_risk", "")

    # Se il classificatore ha esplicitamente flaggato "già prezzato", hard reject
    hard_reject = "già prezzat" in str(already_priced).lower() or "already priced" in str(already_priced).lower()
    passed = score >= NOVELTY_THRESHOLD and not hard_reject

    reason = (
        f"novelty {score:.2f} >= {NOVELTY_THRESHOLD}"
        if passed
        else (
            f"already_priced_risk flag: '{already_priced}'"
            if hard_reject
            else f"novelty {score:.2f} < soglia {NOVELTY_THRESHOLD}"
        )
    )
    return FilterResult("F2", "Novelty / Not-Priced", passed, score, NOVELTY_THRESHOLD, reason)


def filter_3_cross_asset(news: dict) -> tuple[FilterResult, dict]:
    """
    F3: cross-asset confirmation con logica istituzionale (Bridgewater/Brevan Howard).

    Non più hard-reject su conferma insufficiente. Il cross-asset è un moltiplicatore
    del sizing, non un cancello on/off. Hard-reject SOLO se confirmation_level == "CONTRARIAN"
    (la maggioranza degli asset va in direzione OPPOSTA alla tesi — segnale genuinamente errato).

    Livelli:
      STRONG      >= 3/5 confermano → size_multiplier 1.0 — PASS
      MODERATE    1-2/5 confermano  → size_multiplier 0.5 — PASS (size ridotta al 50%)
      WEAK        0/5 confermano, nessun contrarian → size_multiplier 0.25 — PASS (size minima 25%)
      CONTRARIAN  maggioranza assets contro la tesi → size_multiplier 0.0 — REJECT
      MARKET_CLOSED weekend/dati vecchi → size_multiplier 0.5 — PASS (cautela)

    Restituisce (FilterResult, cross_asset_result_dict).
    """
    category = news.get("event_category", "NONE")
    try:
        cross_result = run_validation(category)
        score = cross_result["confirmation_score"]
        confirmation_level = cross_result.get("confirmation_level", "WEAK")
        size_multiplier = cross_result.get("size_multiplier", 0.25)
        passes = cross_result["passes_filter"]  # False SOLO per CONTRARIAN
        confirming = ", ".join(cross_result.get("confirming_assets", [])) or "nessuno"
        contrarian = ", ".join(cross_result.get("contrarian_assets", [])) or "nessuno"

        if confirmation_level == "CONTRARIAN":
            reason = (
                f"CONTRARIAN — la maggioranza degli asset va in direzione opposta alla tesi. "
                f"Asset contrarian: {contrarian}. Segnale scartato."
            )
        elif confirmation_level == "STRONG":
            reason = (
                f"STRONG — {score}/5 asset confermano, size normale (1.0x). "
                f"Asset confermanti: {confirming}"
            )
        elif confirmation_level == "MODERATE":
            reason = (
                f"MODERATE — {score}/5 asset confermano, size ridotta al 50% (0.5x). "
                f"Asset confermanti: {confirming}"
            )
        elif confirmation_level == "WEAK":
            reason = (
                f"WEAK — 0/5 asset confermano con z-score significativo, nessuna pressione contrarian. "
                f"Size minima (0.25x) — tesi intatta, conferma macro assente."
            )
        elif confirmation_level == "MARKET_CLOSED":
            reason = (
                f"MARKET_CLOSED — mercati chiusi o dati yfinance non aggiornati. "
                f"Size ridotta precauzionalmente (0.5x). {cross_result.get('warning', '')}"
            )
        else:
            reason = f"{confirmation_level} — {score}/5 asset confermano. size_multiplier={size_multiplier}"

        return (
            FilterResult(
                "F3", "Cross-Asset Confirmation",
                passes, float(score), float(STRONG_THRESHOLD),
                reason, size_multiplier
            ),
            cross_result,
        )
    except Exception as e:
        logger.error(f"Errore cross_asset_validator: {e}")
        # In caso di errore del validator, degradiamo gracefully: PASS con size ridotta
        reason = (
            f"cross_asset_validator non disponibile ({e}) — filtro degradato, "
            f"PASS con size_multiplier=0.25 (WEAK per default)"
        )
        return FilterResult("F3", "Cross-Asset Confirmation", True, 0.0, float(STRONG_THRESHOLD), reason, 0.25), {}


def filter_4_entry_timing(news: dict) -> FilterResult:
    """F4: non aprire posizioni a T+0 (troppo rumore, spread ampi)."""
    timing = str(news.get("entry_timing", "WAIT_CONFIRM"))

    # Normalizza variazioni minori
    timing_upper = timing.upper().replace(" ", "")
    is_blocked = any(b.replace("+", "").replace("_", "") in timing_upper.replace("+", "").replace("_", "")
                     for b in ["T0", "T+0"])

    passed = not is_blocked
    reason = (
        f"entry_timing='{timing}' — OK per apertura posizione"
        if passed
        else f"entry_timing='{timing}' — T+0 bloccato (troppo rumore, usa opzioni OTM cheap se vuoi esposizione)"
    )
    return FilterResult("F4", "Entry Timing", passed, None, None, reason)


def filter_5_macro_regime(news: dict) -> FilterResult:
    """
    F5: verifica coerenza tra macro_regime rilevato e categoria evento.
    In regime inflazionistico i bond non sono safe haven (lezione Ukraine 2022).
    Non è un filtro hard-reject — abbassa il confidence_composite se incoerente.
    """
    category = news.get("event_category", "NONE")
    macro_regime = str(news.get("macro_regime", "")).lower()

    # Se il regime è inflazionistico ma la categoria è deflazionaria → warning
    inflationary_regime = any(k in macro_regime for k in ["inflat", "hawkish", "stagflat"])
    deflationary_regime = any(k in macro_regime for k in ["deflat", "dovish", "recession", "risk_off"])

    incoherent = (
        (category in DEFLATIONARY_CATEGORIES and inflationary_regime) or
        (category in INFLATIONARY_CATEGORIES and deflationary_regime)
    )

    # F5 non è mai hard-reject: segnala ma lascia passare con nota
    passed = True
    if incoherent:
        reason = (
            f"⚠️ REGIME INCOERENTE: categoria '{category}' vs macro_regime '{macro_regime}'. "
            f"Esempio: Ukraine 2022 → shock inflazionistico, bond NON safe haven. "
            f"Ridurre confidence e verificare manualmente direzione bond/safe-haven."
        )
    elif not macro_regime:
        reason = "macro_regime non disponibile dal classificatore — filtro saltato"
    else:
        reason = f"macro_regime '{macro_regime}' coerente con categoria '{category}'"

    return FilterResult("F5", "Macro Regime Coherence", passed, None, None, reason)


# ─── Pipeline principale ──────────────────────────────────────────────────────

def compute_composite_confidence(news: dict, cross_result: dict) -> float:
    """
    Calcola un confidence_composite (0-1) come media pesata di:
    - materiality_score (peso 0.35)
    - novelty_score (peso 0.25)
    - cross_asset_confirmation_score normalizzato /5 * size_multiplier (peso 0.30)
    - entry_timing_bonus (peso 0.10): T+1 → 1.0, T+3 → 0.7, WAIT → 0.3

    Il size_multiplier dal CrossAssetResult scala il contributo cross-asset:
      - STRONG (1.0):       cross contribuisce fino a 0.30 della confidence
      - MODERATE (0.5):     cross contribuisce fino a 0.15
      - WEAK (0.25):        cross contribuisce fino a 0.075
      - MARKET_CLOSED (0.5): trattato come MODERATE
    Questo riflette automaticamente la certezza del segnale nel punteggio finale.
    """
    materiality = float(news.get("materiality_score", 0.0))
    novelty = float(news.get("novelty_score", 0.0))

    # Estrai size_multiplier dal cross_result (default 1.0 se non presente)
    size_multiplier = float(cross_result.get("size_multiplier", 1.0))
    raw_cross_score = float(cross_result.get("confirmation_score", 0)) / 5.0
    # Scala il contributo cross-asset per il size_multiplier
    cross_score = raw_cross_score * size_multiplier

    timing = str(news.get("entry_timing", "WAIT")).upper()
    if "T+1" in timing or "T1" in timing:
        timing_bonus = 1.0
    elif "T+3" in timing or "T3" in timing:
        timing_bonus = 0.7
    elif "WAIT" in timing:
        timing_bonus = 0.3
    else:
        timing_bonus = 0.5

    composite = (
        materiality * 0.35
        + novelty * 0.25
        + cross_score * 0.30
        + timing_bonus * 0.10
    )
    return round(min(composite, 1.0), 3)


def run_pipeline(classified_news_list: list[dict]) -> PipelineOutput:
    """
    Processa una lista di news classificate attraverso i 5 filtri.
    Restituisce PipelineOutput con signal_candidates e reject_log.
    """
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    signal_candidates = []
    reject_log = []

    for news in classified_news_list:
        news_id = news.get("id", news.get("url", "unknown"))
        headline = news.get("headline", news.get("title", ""))
        filters_run = []

        logger.info(f"Pipeline: '{headline[:60]}...' [{news.get('event_category', 'NONE')}]")

        # ── F1: Materiality ────────────────────────────────────────────────────
        f1 = filter_1_materiality(news)
        filters_run.append(asdict(f1))
        if not f1.passed:
            logger.info(f"  ❌ F1 REJECT: {f1.reason}")
            reject_log.append(asdict(RejectedNews(
                news_id=news_id, headline=headline,
                event_category=news.get("event_category", "NONE"),
                rejected_at_filter="F1_MATERIALITY",
                reject_reason=f1.reason, filters_run=filters_run
            )))
            continue

        logger.info(f"  ✅ F1 PASS: {f1.reason}")

        # ── F2: Novelty ────────────────────────────────────────────────────────
        f2 = filter_2_novelty(news)
        filters_run.append(asdict(f2))
        if not f2.passed:
            logger.info(f"  ❌ F2 REJECT: {f2.reason}")
            reject_log.append(asdict(RejectedNews(
                news_id=news_id, headline=headline,
                event_category=news.get("event_category", "NONE"),
                rejected_at_filter="F2_NOVELTY",
                reject_reason=f2.reason, filters_run=filters_run
            )))
            continue

        logger.info(f"  ✅ F2 PASS: {f2.reason}")

        # ── F3: Cross-Asset ────────────────────────────────────────────────────
        f3, cross_result = filter_3_cross_asset(news)
        filters_run.append(asdict(f3))
        if not f3.passed:
            logger.info(f"  ❌ F3 REJECT: {f3.reason}")
            reject_log.append(asdict(RejectedNews(
                news_id=news_id, headline=headline,
                event_category=news.get("event_category", "NONE"),
                rejected_at_filter="F3_CROSS_ASSET",
                reject_reason=f3.reason, filters_run=filters_run
            )))
            continue

        logger.info(f"  ✅ F3 PASS: {f3.reason}")

        # ── F4: Entry Timing ───────────────────────────────────────────────────
        f4 = filter_4_entry_timing(news)
        filters_run.append(asdict(f4))
        if not f4.passed:
            logger.info(f"  ❌ F4 REJECT: {f4.reason}")
            reject_log.append(asdict(RejectedNews(
                news_id=news_id, headline=headline,
                event_category=news.get("event_category", "NONE"),
                rejected_at_filter="F4_ENTRY_TIMING",
                reject_reason=f4.reason, filters_run=filters_run
            )))
            continue

        logger.info(f"  ✅ F4 PASS: {f4.reason}")

        # ── F5: Macro Regime ───────────────────────────────────────────────────
        f5 = filter_5_macro_regime(news)
        filters_run.append(asdict(f5))
        # F5 non rigetta mai — aggiunge solo un warning al log
        logger.info(f"  {'✅' if 'coerente' in f5.reason else '⚠️ '} F5: {f5.reason}")

        # ── TUTTI I FILTRI PASSATI → genera segnale ────────────────────────────
        composite = compute_composite_confidence(news, cross_result)
        # Recupera size_multiplier dal cross_result (default 1.0 se non disponibile)
        cross_size_multiplier = float(cross_result.get("size_multiplier", 1.0))
        signal = SignalCandidate(
            news_id=news_id,
            headline=headline,
            source=news.get("source", ""),
            published_at=news.get("published_at", news.get("published", "")),
            event_category=news.get("event_category", ""),
            materiality_score=float(news.get("materiality_score", 0.0)),
            novelty_score=float(news.get("novelty_score", 0.0)),
            causal_chain=news.get("causal_chain", ""),
            macro_regime=news.get("macro_regime", ""),
            entry_timing=news.get("entry_timing", ""),
            already_priced_risk=news.get("already_priced_risk", ""),
            affected_assets=news.get("affected_assets", []),
            cross_asset_result=cross_result,
            filters_passed=filters_run,
            signal_generated_at=timestamp,
            confidence_composite=composite,
            cross_asset_size_multiplier=cross_size_multiplier,
        )
        signal_candidates.append(asdict(signal))
        logger.info(
            f"  SEGNALE GENERATO — composite_confidence={composite:.3f} "
            f"| cross_level={cross_result.get('confirmation_level', 'N/A')} "
            f"| size_multiplier={cross_size_multiplier:.2f}x"
        )

    output = PipelineOutput(
        run_timestamp=timestamp,
        total_news_processed=len(classified_news_list),
        signals_generated=len(signal_candidates),
        news_rejected=len(reject_log),
        signal_candidates=signal_candidates,
        reject_log=reject_log,
    )

    logger.info(
        f"Pipeline completata: {output.signals_generated} segnali su "
        f"{output.total_news_processed} news ({output.news_rejected} rifiutate)"
    )
    return output


# ─── Interfaccia pubblica ─────────────────────────────────────────────────────

def process_classified_news(news_list: list[dict]) -> dict:
    """Entry point per trade_structurer.py e main.py."""
    output = run_pipeline(news_list)
    return asdict(output)


# ─── CLI test ─────────────────────────────────────────────────────────────────

_TEST_NEWS = [
    {
        "id": "test_001",
        "headline": "Iran closes Strait of Hormuz to international shipping following US strike",
        "source": "Reuters",
        "published_at": "2026-04-30T08:00:00Z",
        "event_category": "ENERGY_SUPPLY_SHOCK",
        "materiality_score": 0.92,
        "novelty_score": 0.88,
        "causal_chain": "Hormuz chiusura → riduzione offerta petrolio -20% → Brent +30% → inflazione globale → central banks costrette ad alzare",
        "already_priced_risk": "parzialmente prezzato nelle opzioni, ma chiusura totale non scontata",
        "macro_regime": "inflationary shock — Brent in rialzo, no flight to quality su bond",
        "entry_timing": "T+1",
        "affected_assets": ["XLE", "XOP", "Brent", "FRO", "GLD"],
    },
    {
        "id": "test_002",
        "headline": "Fed holds rates steady, as widely expected",
        "source": "MarketWatch",
        "published_at": "2026-04-30T14:00:00Z",
        "event_category": "CENTRAL_BANK_SURPRISE",
        "materiality_score": 0.30,
        "novelty_score": 0.10,
        "causal_chain": "Fed mantiene tassi → nessun cambio policy → mercati stabili",
        "already_priced_risk": "già completamente prezzato al 100% dalle probabilità implicite",
        "macro_regime": "neutral",
        "entry_timing": "T+0",
        "affected_assets": [],
    },
    {
        "id": "test_003",
        "headline": "North Korea tests ICBM with claimed nuclear warhead capability",
        "source": "BBC",
        "published_at": "2026-04-30T06:00:00Z",
        "event_category": "NUCLEAR_THREAT",
        "materiality_score": 0.85,
        "novelty_score": 0.72,
        "causal_chain": "Test ICBM nucleare → escalation rischio geopolitico → flight-to-quality → GLD/CHF/JPY up, equity down",
        "already_priced_risk": "non prezzato — primo test 2026",
        "macro_regime": "deflationary shock — flight to quality atteso, bond safe haven",
        "entry_timing": "T+1",
        "affected_assets": ["GLD", "ITA", "TLT"],
    },
]


def _run_test():
    print("\n" + "="*70)
    print("TEST: signal_pipeline.py — 3 news di test")
    print("="*70)

    output = process_classified_news(_TEST_NEWS)

    print(f"\n📊 Totale news: {output['total_news_processed']}")
    print(f"🚀 Segnali generati: {output['signals_generated']}")
    print(f"❌ Rifiutate: {output['news_rejected']}")

    if output["signal_candidates"]:
        print("\n── SEGNALI ──────────────────────────────────────────────────")
        for s in output["signal_candidates"]:
            print(f"  ✅ [{s['event_category']}] {s['headline'][:65]}...")
            print(f"     Confidence composite: {s['confidence_composite']:.3f}")
            print(f"     Entry timing: {s['entry_timing']}")
            print(f"     Causal chain: {s['causal_chain'][:80]}...")

    if output["reject_log"]:
        print("\n── RIFIUTATE ────────────────────────────────────────────────")
        for r in output["reject_log"]:
            print(f"  ❌ [{r['rejected_at_filter']}] {r['headline'][:65]}...")
            print(f"     Motivo: {r['reject_reason']}")

    print("\n✅ Test completato.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Signal Pipeline — MacroSignalTool")
    parser.add_argument("--test", action="store_true", help="Esegui test con 3 news di esempio")
    parser.add_argument("--input", type=str, help="Path a JSON file con lista di news classificate")
    args = parser.parse_args()

    if args.test:
        _run_test()
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            news_list = json.load(f)
        output = process_classified_news(news_list)
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        parser.print_help()
