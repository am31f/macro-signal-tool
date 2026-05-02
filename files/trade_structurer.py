"""
trade_structurer.py
Phase 3, Task 3.2 — MacroSignalTool

Per ogni SignalCandidate generato dalla pipeline, chiama Claude API per
strutturare il trade completo:
  - direction (LONG / SHORT / LONG+SHORT hedged)
  - instrument_type (ETF / single_stock / future / option)
  - specific_tickers[] con rationale per ciascuno
  - entry_timing (T+1 / T+3)
  - stop_loss_pct / target_pct
  - timeframe_days
  - conviction_pct
  - alternative_scenario (cosa invalida il trade)

Usa asset_map.json e geographic_exposure.json come context aggiuntivo
per il prompt Claude (così il modello non deve "inventare" i ticker).

Dipendenze: anthropic, pathlib
Richiede: ANTHROPIC_API_KEY in .env
Testabile: python trade_structurer.py --test
"""

import argparse
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

# ─── Setup ────────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("trade_structurer")

# ─── Costanti ─────────────────────────────────────────────────────────────────
MODEL = "claude-sonnet-4-20250514"
MAX_RETRIES = 3
RETRY_DELAY = 5
DATA_DIR = Path(__file__).parent

ASSET_MAP_PATH = DATA_DIR / "asset_map.json"
GEO_EXPOSURE_PATH = DATA_DIR / "geographic_exposure.json"


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class TradeInstrument:
    """Un singolo strumento nel trade proposto."""
    ticker: str
    name: str
    direction: str           # "LONG" / "SHORT"
    instrument_type: str     # "ETF" / "single_stock" / "future" / "option_call" / "option_put"
    rationale: str
    weight_pct: float        # % del totale trade size (somma = 100%)
    option_strike_hint: str = ""    # solo per opzioni: "ATM" / "5% OTM" / ecc.
    option_expiry_hint: str = ""    # solo per opzioni: "30d" / "60d" / "next_monthly"


@dataclass
class TradeStructure:
    """Struttura completa del trade per un SignalCandidate."""
    signal_id: str
    event_category: str
    headline: str
    # Struttura trade
    primary_thesis: str
    trade_type: str                  # "directional" / "convex" / "hedged_pairs"
    instruments: list                # lista di TradeInstrument serializzati
    entry_timing: str
    timeframe_days: int
    stop_loss_pct: float
    target_pct: float
    risk_reward_ratio: float
    conviction_pct: float
    # Gestione rischio
    alternative_scenario: str        # cosa invalida il trade
    hedge_suggestion: str            # hedge complementare
    position_notes: str              # note operative (es. "non aprire all market open")
    # Regime check
    inflation_channel_dominant: bool
    bond_safe_haven_warning: bool
    # Metadata
    structured_at: str
    claude_model_used: str
    raw_claude_response: str = ""


# ─── Carica knowledge base ────────────────────────────────────────────────────

def _load_asset_context(event_category: str) -> str:
    """
    Carica i ticker rilevanti da asset_map.json per la categoria evento.
    Restituisce una stringa compatta da inserire nel prompt Claude.
    """
    if not ASSET_MAP_PATH.exists():
        return "asset_map.json non disponibile"
    try:
        with open(ASSET_MAP_PATH, encoding="utf-8") as f:
            asset_map = json.load(f)

        relevant_tickers = []
        # ETF
        for etf in asset_map.get("etf_universe", []):
            reactions = etf.get("reaction_by_event", {})
            if event_category in reactions:
                r = reactions[event_category]
                relevant_tickers.append(
                    f"{etf['ticker']} ({etf['name']}, ETF) → "
                    f"{r['direction']} avg {r['avg_move_pct']}% wr={r['win_rate']}"
                )
        # Single stocks
        for stock in asset_map.get("single_stocks_universe", []):
            reactions = stock.get("reaction_by_event", {})
            if event_category in reactions:
                r = reactions[event_category]
                relevant_tickers.append(
                    f"{stock['ticker']} ({stock['name']}, stock) → "
                    f"{r['direction']} avg {r['avg_move_pct']}% wr={r['win_rate']}"
                )

        if not relevant_tickers:
            return f"Nessun ticker specifico mappato per {event_category}"
        return "\n".join(relevant_tickers[:20])  # max 20 per non sovraccaricare il prompt
    except Exception as e:
        logger.warning(f"Errore caricamento asset_map: {e}")
        return "asset_map.json non leggibile"


def _load_geo_context(event_category: str) -> str:
    """
    Carica le aziende con esposizione geografica critica da geographic_exposure.json.
    Utile per eventi Middle East / Russia / China.
    """
    if not GEO_EXPOSURE_PATH.exists():
        return "geographic_exposure.json non disponibile"

    region_map = {
        "ENERGY_SUPPLY_SHOCK": "middle_east",
        "MILITARY_CONFLICT": "middle_east",
        "SANCTIONS_IMPOSED": "russia_cis",
        "TRADE_WAR_TARIFF": "china",
        "NUCLEAR_THREAT": "middle_east",
        "INFRASTRUCTURE_DISRUPTION": "middle_east",
    }
    target_region = region_map.get(event_category, "")
    if not target_region:
        return ""

    try:
        with open(GEO_EXPOSURE_PATH, encoding="utf-8") as f:
            geo_data = json.load(f)

        high_exposure = []
        companies = geo_data.get("companies", geo_data) if isinstance(geo_data, dict) else geo_data
        if isinstance(companies, dict):
            companies = list(companies.values())

        for company in companies:
            if not isinstance(company, dict):
                continue
            exposure = company.get("revenue_breakdown", {})
            region_pct = exposure.get(target_region, 0)
            if isinstance(region_pct, (int, float)) and region_pct >= 15:
                critical = company.get("critical_regions", [])
                flag = "⚠️ CRITICA" if target_region in critical else ""
                high_exposure.append(
                    f"{company.get('ticker','?')} ({company.get('name','?')}): "
                    f"{region_pct}% revenue da {target_region} {flag}"
                )

        if not high_exposure:
            return f"Nessuna azienda con esposizione >= 15% a {target_region}"
        return "\n".join(high_exposure[:10])
    except Exception as e:
        logger.warning(f"Errore caricamento geographic_exposure: {e}")
        return "geographic_exposure.json non leggibile"


# ─── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sei il lead portfolio manager di un macro hedge fund tier-1 (stile Brevan Howard / Tudor).
Hai ricevuto un segnale macro già validato da una pipeline di 5 filtri quantitativi.
Il tuo compito è strutturare il trade ottimale.

REGOLE OPERATIVE:
1. Preferisci ETF liquidi a singoli titoli (meno rischio idiosincratico).
2. Per eventi con alta incertezza (nuclear, cyberattack) usa strutture convesse: opzioni OTM cheap piuttosto che delta-1.
3. Non raccomandare mai posizioni T+0. Minimo T+1.
4. Indica SEMPRE stop-loss (max -8% su ETF, -12% su single stock) e target realistico.
5. Distingui il canale dominante: se inflazionistico, i bond NON sono safe haven (lezione Ukraine 2022).
6. Usa solo i ticker forniti nel contesto asset_map — non inventare ticker.
7. Il campo `instruments` deve avere weights che sommano a 100%.
8. Se non sei convinto >= 60%, non strutturare il trade (restituisci trade_type: "NO_TRADE" con motivazione).

Restituisci SOLO un JSON valido (no markdown, no spiegazioni fuori dal JSON) con questa struttura:
{
  "primary_thesis": "<1-2 frasi sul tesi del trade>",
  "trade_type": "<directional | convex | hedged_pairs | NO_TRADE>",
  "no_trade_reason": "<solo se NO_TRADE>",
  "instruments": [
    {
      "ticker": "<ticker>",
      "name": "<nome>",
      "direction": "<LONG | SHORT>",
      "instrument_type": "<ETF | single_stock | future | option_call | option_put>",
      "rationale": "<perché questo strumento>",
      "weight_pct": <numero 0-100>,
      "option_strike_hint": "<ATM | 5% OTM | blank se non opzione>",
      "option_expiry_hint": "<30d | 60d | blank se non opzione>"
    }
  ],
  "entry_timing": "<T+1 | T+3>",
  "timeframe_days": <numero>,
  "stop_loss_pct": <numero negativo, es. -7.5>,
  "target_pct": <numero positivo, es. 15.0>,
  "risk_reward_ratio": <target/abs(stop), es. 2.0>,
  "conviction_pct": <0-100>,
  "alternative_scenario": "<cosa invalida il trade>",
  "hedge_suggestion": "<hedge complementare, es. long put SPY per tail risk>",
  "position_notes": "<note operative>",
  "inflation_channel_dominant": <true | false>,
  "bond_safe_haven_warning": <true | false>
}"""


def _build_user_prompt(signal: dict, asset_context: str, geo_context: str) -> str:
    """Costruisce il prompt utente per Claude con il contesto del segnale."""
    cross = signal.get("cross_asset_result", {})
    confirming = cross.get("confirming_assets", [])
    macro_regime = cross.get("macro_regime_hint", signal.get("macro_regime", ""))

    return f"""SEGNALE VALIDATO:
Headline: {signal.get('headline', '')}
Categoria: {signal.get('event_category', '')}
Materiality score: {signal.get('materiality_score', '')}
Novelty score: {signal.get('novelty_score', '')}
Causal chain: {signal.get('causal_chain', '')}
Already priced risk: {signal.get('already_priced_risk', '')}
Entry timing suggerito: {signal.get('entry_timing', '')}
Macro regime: {macro_regime}
Cross-asset confirming ({len(confirming)}/5): {', '.join(confirming) if confirming else 'N/A'}
Confidence composite: {signal.get('confidence_composite', '')}

ASSET UNIVERSE RILEVANTE (da asset_map.json):
{asset_context}

ESPOSIZIONE GEOGRAFICA CRITICA (da geographic_exposure.json):
{geo_context if geo_context else 'N/A per questa categoria'}

Struttura il trade ottimale rispettando le regole operative nel system prompt."""


# ─── Chiamata Claude API ──────────────────────────────────────────────────────

def _call_claude(client: anthropic.Anthropic, user_prompt: str) -> str:
    """Chiama Claude API con retry su rate limit."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text.strip()
        except anthropic.RateLimitError:
            wait = RETRY_DELAY * attempt
            logger.warning(f"Rate limit — attendo {wait}s (tentativo {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
        except anthropic.APIError as e:
            logger.error(f"APIError tentativo {attempt}: {e}")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_DELAY)
    raise RuntimeError("Max retry raggiunti per chiamata Claude API")


def _parse_trade_json(raw: str) -> dict:
    """
    Estrae il JSON dalla risposta di Claude.
    Gestisce eventuali markdown code fences residui.
    """
    # Rimuovi markdown fences se presenti
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    # Trova il primo { e l'ultimo }
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"Nessun JSON trovato nella risposta: {raw[:200]}")

    return json.loads(cleaned[start:end])


# ─── Strutturazione singolo trade ─────────────────────────────────────────────

def structure_trade(signal: dict, client: Optional[anthropic.Anthropic] = None) -> TradeStructure:
    """
    Struttura un singolo trade per un SignalCandidate.
    Restituisce TradeStructure.
    """
    if client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY non trovata. Configura .env")
        client = anthropic.Anthropic(api_key=api_key)

    category = signal.get("event_category", "")
    asset_context = _load_asset_context(category)
    geo_context = _load_geo_context(category)

    user_prompt = _build_user_prompt(signal, asset_context, geo_context)

    logger.info(f"Strutturando trade per: [{category}] {signal.get('headline', '')[:60]}...")
    raw_response = _call_claude(client, user_prompt)

    try:
        trade_json = _parse_trade_json(raw_response)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Parse error risposta Claude: {e}\nRaw: {raw_response[:300]}")
        # Fallback: trade struttura vuota con error note
        trade_json = {
            "primary_thesis": "PARSE ERROR — risposta Claude non valida",
            "trade_type": "NO_TRADE",
            "no_trade_reason": f"Parse error: {e}",
            "instruments": [],
            "entry_timing": "WAIT_CONFIRM",
            "timeframe_days": 0,
            "stop_loss_pct": 0.0,
            "target_pct": 0.0,
            "risk_reward_ratio": 0.0,
            "conviction_pct": 0,
            "alternative_scenario": "",
            "hedge_suggestion": "",
            "position_notes": "",
            "inflation_channel_dominant": False,
            "bond_safe_haven_warning": False,
        }

    # Converti instruments in lista di TradeInstrument
    instruments = []
    for inst in trade_json.get("instruments", []):
        instruments.append(asdict(TradeInstrument(
            ticker=inst.get("ticker", ""),
            name=inst.get("name", ""),
            direction=inst.get("direction", "LONG"),
            instrument_type=inst.get("instrument_type", "ETF"),
            rationale=inst.get("rationale", ""),
            weight_pct=float(inst.get("weight_pct", 0)),
            option_strike_hint=inst.get("option_strike_hint", ""),
            option_expiry_hint=inst.get("option_expiry_hint", ""),
        )))

    stop = float(trade_json.get("stop_loss_pct", -7.0))
    target = float(trade_json.get("target_pct", 14.0))
    rr = round(abs(target / stop), 2) if stop != 0 else 0.0

    structure = TradeStructure(
        signal_id=signal.get("news_id", ""),
        event_category=category,
        headline=signal.get("headline", ""),
        primary_thesis=trade_json.get("primary_thesis", ""),
        trade_type=trade_json.get("trade_type", "directional"),
        instruments=instruments,
        entry_timing=trade_json.get("entry_timing", signal.get("entry_timing", "T+1")),
        timeframe_days=int(trade_json.get("timeframe_days", 20)),
        stop_loss_pct=stop,
        target_pct=target,
        risk_reward_ratio=rr,
        conviction_pct=int(trade_json.get("conviction_pct", 0)),
        alternative_scenario=trade_json.get("alternative_scenario", ""),
        hedge_suggestion=trade_json.get("hedge_suggestion", ""),
        position_notes=trade_json.get("position_notes", ""),
        inflation_channel_dominant=bool(trade_json.get("inflation_channel_dominant", False)),
        bond_safe_haven_warning=bool(trade_json.get("bond_safe_haven_warning", False)),
        structured_at=datetime.now(tz=timezone.utc).isoformat(),
        claude_model_used=MODEL,
        raw_claude_response=raw_response,
    )

    logger.info(
        f"Trade strutturato: type={structure.trade_type}, "
        f"conviction={structure.conviction_pct}%, "
        f"RR={structure.risk_reward_ratio:.1f}x"
    )
    return structure


# ─── Batch processing ─────────────────────────────────────────────────────────

def structure_all_signals(signal_candidates: list[dict]) -> list[dict]:
    """
    Struttura tutti i SignalCandidate passati dalla pipeline.
    Restituisce lista di TradeStructure serializzati.
    """
    if not signal_candidates:
        logger.info("Nessun segnale da strutturare.")
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY non trovata. Configura .env")
    client = anthropic.Anthropic(api_key=api_key)

    results = []
    for i, signal in enumerate(signal_candidates, 1):
        logger.info(f"Strutturando {i}/{len(signal_candidates)}...")
        try:
            trade = structure_trade(signal, client)
            results.append(asdict(trade))
        except Exception as e:
            logger.error(f"Errore strutturazione trade {i}: {e}")
            results.append({
                "signal_id": signal.get("news_id", ""),
                "error": str(e),
                "trade_type": "ERROR",
                "instruments": [],
                "conviction_pct": 0,
                "stop_loss_pct": -7.0,
                "target_pct": 14.0,
                "risk_reward_ratio": 2.0,
                "timeframe_days": 20,
                "entry_timing": "T+1",
                "primary_thesis": f"Strutturazione fallita: {str(e)[:100]}",
                "no_trade_reason": str(e),
            })
        # Rate limit buffer tra chiamate
        if i < len(signal_candidates):
            time.sleep(1)

    return results


# ─── CLI test ─────────────────────────────────────────────────────────────────

_TEST_SIGNAL = {
    "news_id": "test_003_nuclear",
    "headline": "North Korea tests ICBM with claimed nuclear warhead capability",
    "source": "BBC",
    "published_at": "2026-04-30T06:00:00Z",
    "event_category": "NUCLEAR_THREAT",
    "materiality_score": 0.85,
    "novelty_score": 0.72,
    "causal_chain": "Test ICBM nucleare → escalation rischio geopolitico → flight-to-quality → GLD/CHF/JPY up, equity down",
    "already_priced_risk": "non prezzato — primo test 2026",
    "macro_regime": "deflationary shock — flight to quality atteso",
    "entry_timing": "T+1",
    "confidence_composite": 0.74,
    "cross_asset_result": {
        "confirmation_score": 4,
        "confirming_assets": ["GOLD", "VIX", "DXY", "US10Y"],
        "macro_regime_hint": "deflationary_shock — flight-to-quality, bonds safe haven",
    },
    "affected_assets": ["GLD", "ITA", "TLT", "GDX"],
}


def _run_test():
    print("\n" + "="*70)
    print("TEST: trade_structurer.py — segnale NUCLEAR_THREAT")
    print("="*70)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("⚠️  ANTHROPIC_API_KEY non trovata. Imposta .env o variabile d'ambiente.")
        print("    Esecuzione con mock response per mostrare la struttura...\n")
        # Mock per test senza API key
        mock_trade = TradeStructure(
            signal_id="test_003_nuclear",
            event_category="NUCLEAR_THREAT",
            headline=_TEST_SIGNAL["headline"],
            primary_thesis="MOCK: Long gold e defense su escalation nucleare Nord Korea",
            trade_type="hedged_pairs",
            instruments=[
                asdict(TradeInstrument("GLD","SPDR Gold","LONG","ETF","Safe haven primario su nuclear threat",50.0)),
                asdict(TradeInstrument("ITA","iShares Defense","LONG","ETF","Defense spending up su escalation",30.0)),
                asdict(TradeInstrument("GDX","VanEck Gold Miners","LONG","ETF","Beta leverage su gold",20.0)),
            ],
            entry_timing="T+1", timeframe_days=10,
            stop_loss_pct=-6.0, target_pct=12.0, risk_reward_ratio=2.0,
            conviction_pct=72,
            alternative_scenario="Risoluzione diplomatica rapida o test fallito → reversal immediato",
            hedge_suggestion="Long put SPY 1-month ATM per tail risk equity",
            position_notes="Non aprire nelle prime 2 ore di trading (spread ampi). Ridurre size se VIX > 35.",
            inflation_channel_dominant=False,
            bond_safe_haven_warning=False,
            structured_at=datetime.now(tz=timezone.utc).isoformat(),
            claude_model_used="MOCK",
        )
        result = asdict(mock_trade)
    else:
        result = asdict(structure_trade(_TEST_SIGNAL))

    print(f"\n📋 Thesis: {result['primary_thesis']}")
    print(f"🎯 Trade type: {result['trade_type']}")
    print(f"⏱  Entry: {result['entry_timing']} | Timeframe: {result['timeframe_days']}d")
    print(f"🛑 Stop: {result['stop_loss_pct']}% | 🎯 Target: {result['target_pct']}%")
    print(f"⚖️  R/R: {result['risk_reward_ratio']}x | 💪 Conviction: {result['conviction_pct']}%")
    print(f"\n📊 Strumenti proposti:")
    for inst in result.get("instruments", []):
        print(f"   {inst['direction']:5} {inst['ticker']:8} ({inst['instrument_type']:12}) {inst['weight_pct']}% — {inst['rationale'][:60]}")
    print(f"\n🔄 Alt scenario: {result['alternative_scenario']}")
    print(f"🛡  Hedge: {result['hedge_suggestion']}")
    print(f"📝 Note: {result['position_notes']}")
    print(f"\n⚠️  Bond safe haven warning: {result['bond_safe_haven_warning']}")
    print(f"🔥 Inflation channel dominant: {result['inflation_channel_dominant']}")
    print("\n✅ Test completato.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trade Structurer — MacroSignalTool")
    parser.add_argument("--test", action="store_true", help="Test con segnale NUCLEAR_THREAT di esempio")
    parser.add_argument("--input", type=str, help="Path a JSON con lista signal_candidates")
    args = parser.parse_args()

    if args.test:
        _run_test()
    elif args.input:
        with open(args.input, encoding="utf-8") as f:
            signals = json.load(f)
        results = structure_all_signals(signals)
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        parser.print_help()
