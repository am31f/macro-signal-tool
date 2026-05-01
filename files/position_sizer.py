"""
position_sizer.py
Phase 3, Task 3.3 — MacroSignalTool

Calcola il position sizing ottimale usando half-Kelly formula.
Inputs:
  - TradeStructure (da trade_structurer.py)
  - SignalCandidate con confidence_composite
  - Portfolio NAV corrente
  - VIX corrente (da cross_asset_validator o yfinance)

Formula:
  kelly_fraction = (W * R - L) / R
    W = win_rate storica per categoria (da asset_map.json)
    R = avg_win / avg_loss storica per categoria
    L = 1 - W  (loss rate)
  half_kelly = 0.5 * kelly_fraction
  position_size_pct = min(half_kelly, MAX_POSITION_PCT)
  if VIX > VIX_REDUCE_THRESHOLD:
      position_size_pct *= VIX_REDUCE_FACTOR

Caps e limiti:
  - Max 5% NAV per posizione (default, configurabile)
  - Max 15% NAV totale in posizioni correlate (stesso evento)
  - Se VIX > 30: size dimezzata
  - Se VIX > 40: size ridotta a 25% (mercato in panico)
  - Kelly negativo o < 0.02 → NO_TRADE

Dipendenze: yfinance (per VIX live), json
Testabile: python position_sizer.py --test
"""

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# yfinance opzionale (degrada gracefully)
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("position_sizer")

# ─── Configurazione (allineata con engine.sizing_config in macro_trading_tool.json) ──
MAX_POSITION_PCT = 0.05         # 5% NAV max per posizione
KELLY_FRACTION = 0.5            # half-Kelly
VIX_REDUCE_THRESHOLD_1 = 30    # VIX > 30 → size dimezzata
VIX_REDUCE_THRESHOLD_2 = 40    # VIX > 40 → size ulteriormente ridotta (25%)
VIX_REDUCE_FACTOR_1 = 0.50
VIX_REDUCE_FACTOR_2 = 0.25
MIN_KELLY_FOR_TRADE = 0.02      # kelly < 2% → non vale aprire la posizione
COMMISSION_PCT = 0.001          # 0.1% per leg (simulata)

DATA_DIR = Path(__file__).parent
ASSET_MAP_PATH = DATA_DIR / "asset_map.json"

# ─── Win-rate e R per categoria (fallback se asset_map non disponibile) ───────
# Dati calibrati su playbooks storici (Gulf War, 9/11, Ukraine 2022, Iran 2026)
DEFAULT_STATS_BY_CATEGORY: dict[str, dict] = {
    "ENERGY_SUPPLY_SHOCK":       {"win_rate": 0.72, "avg_win_pct": 12.5, "avg_loss_pct": 6.2},
    "MILITARY_CONFLICT":         {"win_rate": 0.68, "avg_win_pct": 10.8, "avg_loss_pct": 5.5},
    "SANCTIONS_IMPOSED":         {"win_rate": 0.64, "avg_win_pct": 9.2,  "avg_loss_pct": 5.8},
    "CENTRAL_BANK_SURPRISE":     {"win_rate": 0.71, "avg_win_pct": 7.5,  "avg_loss_pct": 4.0},
    "TRADE_WAR_TARIFF":          {"win_rate": 0.61, "avg_win_pct": 8.0,  "avg_loss_pct": 5.5},
    "CYBER_ATTACK":              {"win_rate": 0.66, "avg_win_pct": 8.5,  "avg_loss_pct": 5.0},
    "SOVEREIGN_CRISIS":          {"win_rate": 0.67, "avg_win_pct": 11.0, "avg_loss_pct": 6.0},
    "COMMODITY_SUPPLY_AGRI":     {"win_rate": 0.63, "avg_win_pct": 8.8,  "avg_loss_pct": 5.5},
    "NUCLEAR_THREAT":            {"win_rate": 0.58, "avg_win_pct": 9.5,  "avg_loss_pct": 7.0},
    "ELECTION_SURPRISE":         {"win_rate": 0.59, "avg_win_pct": 7.5,  "avg_loss_pct": 5.5},
    "PANDEMIC_HEALTH":           {"win_rate": 0.65, "avg_win_pct": 14.0, "avg_loss_pct": 7.5},
    "INFRASTRUCTURE_DISRUPTION": {"win_rate": 0.67, "avg_win_pct": 9.0,  "avg_loss_pct": 5.2},
}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class SizingInput:
    """Inputs per il calcolo del position sizing."""
    portfolio_nav: float
    event_category: str
    conviction_pct: float        # 0-100, da TradeStructure
    confidence_composite: float  # 0-1, da SignalCandidate
    trade_stop_loss_pct: float   # es. -7.5
    trade_target_pct: float      # es. 15.0
    current_vix: Optional[float] = None
    existing_correlated_exposure_pct: float = 0.0  # % NAV già in posizioni correlate


@dataclass
class SizingResult:
    """Output completo del position sizer."""
    # Inputs usati
    portfolio_nav: float
    event_category: str
    current_vix: Optional[float]
    # Stats storiche usate
    historical_win_rate: float
    historical_avg_win_pct: float
    historical_avg_loss_pct: float
    historical_R_ratio: float
    # Kelly calc
    raw_kelly_pct: float          # kelly grezzo (può essere > 1)
    half_kelly_pct: float         # half-kelly applicato
    # Aggiustamenti
    vix_adjustment_factor: float  # 1.0 = nessun aggiustamento
    conviction_adjustment_factor: float  # scala in base a conviction
    correlation_cap_applied: bool
    # Risultato finale
    position_size_pct: float      # % NAV da allocare
    position_size_eur: float      # importo in EUR/USD
    max_loss_eur: float           # perdita massima attesa se stop raggiunto
    commission_cost_eur: float    # costo commissioni stimato (2 legs)
    # Qualità
    kelly_quality: str            # "STRONG" / "MODERATE" / "WEAK" / "NO_TRADE"
    sizing_rationale: str
    warnings: list
    computed_at: str


# ─── Fetch VIX live ───────────────────────────────────────────────────────────

def get_current_vix() -> Optional[float]:
    """Scarica VIX corrente da yfinance."""
    if not YFINANCE_AVAILABLE:
        logger.warning("yfinance non installato — VIX non disponibile, nessun aggiustamento")
        return None
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="2d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Errore fetch VIX: {e}")
        return None


# ─── Carica stats storiche ─────────────────────────────────────────────────────

def get_historical_stats(event_category: str) -> dict:
    """
    Restituisce win_rate, avg_win_pct, avg_loss_pct per la categoria.
    Prima tenta asset_map.json, poi fallback su DEFAULT_STATS_BY_CATEGORY.
    """
    # Prova asset_map.json
    if ASSET_MAP_PATH.exists():
        try:
            with open(ASSET_MAP_PATH, encoding="utf-8") as f:
                asset_map = json.load(f)
            # Aggregazione su tutti i ticker per categoria
            win_rates, avg_wins, avg_losses = [], [], []
            for group in ("etf_universe", "single_stocks_universe"):
                for asset in asset_map.get(group, []):
                    reactions = asset.get("reaction_by_event", {})
                    if event_category in reactions:
                        r = reactions[event_category]
                        if "win_rate" in r:
                            win_rates.append(r["win_rate"])
                        if "avg_move_pct" in r:
                            if r.get("direction") == "LONG":
                                avg_wins.append(abs(r["avg_move_pct"]))
                            else:
                                avg_losses.append(abs(r["avg_move_pct"]))
            if win_rates:
                return {
                    "win_rate": sum(win_rates) / len(win_rates),
                    "avg_win_pct": sum(avg_wins) / len(avg_wins) if avg_wins else 10.0,
                    "avg_loss_pct": sum(avg_losses) / len(avg_losses) if avg_losses else 5.5,
                }
        except Exception as e:
            logger.warning(f"Errore lettura asset_map per stats: {e}")

    # Fallback default
    stats = DEFAULT_STATS_BY_CATEGORY.get(event_category)
    if stats:
        return stats
    # Fallback generico
    return {"win_rate": 0.62, "avg_win_pct": 9.0, "avg_loss_pct": 5.5}


# ─── Kelly Formula ────────────────────────────────────────────────────────────

def compute_kelly(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
    """
    Formula Kelly standard: f* = (W*R - L) / R
    dove R = avg_win / avg_loss, L = 1 - W
    Restituisce kelly fraction (0-1+).
    """
    if avg_loss_pct <= 0:
        return 0.0
    R = avg_win_pct / avg_loss_pct
    W = win_rate
    L = 1.0 - W
    kelly = (W * R - L) / R
    return max(kelly, 0.0)  # non può essere negativo (sarebbe NO_TRADE)


# ─── Calcolo principale ───────────────────────────────────────────────────────

def compute_position_size(inputs: SizingInput) -> SizingResult:
    """
    Calcola il position sizing completo con tutti gli aggiustamenti.
    """
    warnings = []
    stats = get_historical_stats(inputs.event_category)

    win_rate = stats["win_rate"]
    avg_win = stats["avg_win_pct"]
    avg_loss = stats["avg_loss_pct"]
    R_ratio = avg_win / avg_loss if avg_loss > 0 else 1.0

    # Kelly grezzo
    raw_kelly = compute_kelly(win_rate, avg_win, avg_loss)
    half_kelly = raw_kelly * KELLY_FRACTION

    # Controllo: kelly troppo basso → NO_TRADE
    if raw_kelly < MIN_KELLY_FOR_TRADE:
        return SizingResult(
            portfolio_nav=inputs.portfolio_nav,
            event_category=inputs.event_category,
            current_vix=inputs.current_vix,
            historical_win_rate=win_rate,
            historical_avg_win_pct=avg_win,
            historical_avg_loss_pct=avg_loss,
            historical_R_ratio=round(R_ratio, 2),
            raw_kelly_pct=round(raw_kelly * 100, 2),
            half_kelly_pct=round(half_kelly * 100, 2),
            vix_adjustment_factor=1.0,
            conviction_adjustment_factor=1.0,
            correlation_cap_applied=False,
            position_size_pct=0.0,
            position_size_eur=0.0,
            max_loss_eur=0.0,
            commission_cost_eur=0.0,
            kelly_quality="NO_TRADE",
            sizing_rationale=f"Kelly {raw_kelly*100:.1f}% < soglia minima {MIN_KELLY_FOR_TRADE*100:.0f}% — edge insufficiente per aprire posizione",
            warnings=["Kelly negativo o insufficiente — skip trade"],
            computed_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    # Aggiustamento VIX
    vix = inputs.current_vix
    if vix is None:
        vix = get_current_vix()
        inputs = SizingInput(**{**asdict(inputs), "current_vix": vix})

    vix_factor = 1.0
    if vix is not None:
        if vix > VIX_REDUCE_THRESHOLD_2:
            vix_factor = VIX_REDUCE_FACTOR_2
            warnings.append(f"VIX={vix:.1f} > {VIX_REDUCE_THRESHOLD_2} — size ridotta al 25% (mercato panico)")
        elif vix > VIX_REDUCE_THRESHOLD_1:
            vix_factor = VIX_REDUCE_FACTOR_1
            warnings.append(f"VIX={vix:.1f} > {VIX_REDUCE_THRESHOLD_1} — size dimezzata (volatilità elevata)")

    # Aggiustamento conviction (scala da 0.5x a 1.2x)
    # conviction < 60% → scale down, conviction >= 80% → leggero scale up
    conv = inputs.conviction_pct / 100.0
    conf = inputs.confidence_composite
    conviction_factor = min(max((conv * 0.7 + conf * 0.3), 0.3), 1.2)

    # Calcola size pre-cap
    adjusted_kelly = half_kelly * vix_factor * conviction_factor
    position_pct = min(adjusted_kelly, MAX_POSITION_PCT)

    # Cap correlazione: se già troppa esposizione correlata, riduci
    correlation_cap_applied = False
    max_correlated_total = 0.15  # max 15% NAV in posizioni correlate
    available_correlated = max_correlated_total - inputs.existing_correlated_exposure_pct
    if available_correlated <= 0:
        warnings.append(f"Cap correlazione raggiunto: {inputs.existing_correlated_exposure_pct*100:.0f}% NAV già esposto — NO_TRADE per questa categoria")
        position_pct = 0.0
        correlation_cap_applied = True
    elif position_pct > available_correlated:
        position_pct = available_correlated
        correlation_cap_applied = True
        warnings.append(f"Size ridotta a {position_pct*100:.1f}% per cap correlazione (max {max_correlated_total*100:.0f}% totale)")

    # Importi
    position_eur = inputs.portfolio_nav * position_pct
    max_loss_eur = position_eur * abs(inputs.trade_stop_loss_pct / 100)
    commission_eur = position_eur * COMMISSION_PCT * 2  # 2 legs (entry + exit)

    # Kelly quality label
    if raw_kelly >= 0.20:
        quality = "STRONG"
    elif raw_kelly >= 0.10:
        quality = "MODERATE"
    else:
        quality = "WEAK"

    rationale = (
        f"Kelly grezzo={raw_kelly*100:.1f}% (W={win_rate:.0%}, R={R_ratio:.1f}x). "
        f"Half-Kelly={half_kelly*100:.1f}%. "
        f"Dopo VIX factor ({vix_factor:.2f}x) e conviction ({conviction_factor:.2f}x): "
        f"size finale={position_pct*100:.2f}% NAV = €{position_eur:.0f}."
    )

    if vix is not None and vix > 30:
        rationale += f" ⚠️ VIX={vix:.1f}: mercato volatile, size ridotta."

    return SizingResult(
        portfolio_nav=inputs.portfolio_nav,
        event_category=inputs.event_category,
        current_vix=vix,
        historical_win_rate=round(win_rate, 3),
        historical_avg_win_pct=round(avg_win, 2),
        historical_avg_loss_pct=round(avg_loss, 2),
        historical_R_ratio=round(R_ratio, 2),
        raw_kelly_pct=round(raw_kelly * 100, 2),
        half_kelly_pct=round(half_kelly * 100, 2),
        vix_adjustment_factor=vix_factor,
        conviction_adjustment_factor=round(conviction_factor, 3),
        correlation_cap_applied=correlation_cap_applied,
        position_size_pct=round(position_pct * 100, 3),
        position_size_eur=round(position_eur, 2),
        max_loss_eur=round(max_loss_eur, 2),
        commission_cost_eur=round(commission_eur, 2),
        kelly_quality=quality,
        sizing_rationale=rationale,
        warnings=warnings,
        computed_at=datetime.now(tz=timezone.utc).isoformat(),
    )


# ─── Interfaccia pubblica ─────────────────────────────────────────────────────

def size_trade(
    portfolio_nav: float,
    event_category: str,
    conviction_pct: float,
    confidence_composite: float,
    stop_loss_pct: float,
    target_pct: float,
    current_vix: Optional[float] = None,
    existing_correlated_pct: float = 0.0,
) -> dict:
    """Entry point pubblico per paper_executor.py e main.py."""
    inputs = SizingInput(
        portfolio_nav=portfolio_nav,
        event_category=event_category,
        conviction_pct=conviction_pct,
        confidence_composite=confidence_composite,
        trade_stop_loss_pct=stop_loss_pct,
        trade_target_pct=target_pct,
        current_vix=current_vix,
        existing_correlated_exposure_pct=existing_correlated_pct,
    )
    result = compute_position_size(inputs)
    return asdict(result)


# ─── CLI test ─────────────────────────────────────────────────────────────────

def _run_test():
    print("\n" + "="*65)
    print("TEST: position_sizer.py")
    print("="*65)

    test_cases = [
        {
            "label": "ENERGY_SUPPLY_SHOCK — NAV €10k, VIX normale (~18)",
            "nav": 10000, "category": "ENERGY_SUPPLY_SHOCK",
            "conviction": 82, "confidence": 0.78,
            "stop": -7.5, "target": 15.0, "vix": 18.0,
        },
        {
            "label": "NUCLEAR_THREAT — NAV €10k, VIX alto (35)",
            "nav": 10000, "category": "NUCLEAR_THREAT",
            "conviction": 70, "confidence": 0.65,
            "stop": -6.0, "target": 12.0, "vix": 35.0,
        },
        {
            "label": "MILITARY_CONFLICT — NAV €10k, VIX panico (42)",
            "nav": 10000, "category": "MILITARY_CONFLICT",
            "conviction": 75, "confidence": 0.72,
            "stop": -8.0, "target": 16.0, "vix": 42.0,
        },
        {
            "label": "ELECTION_SURPRISE — NAV €10k, conviction bassa (55)",
            "nav": 10000, "category": "ELECTION_SURPRISE",
            "conviction": 55, "confidence": 0.48,
            "stop": -5.0, "target": 8.0, "vix": 22.0,
        },
    ]

    for tc in test_cases:
        print(f"\n📋 {tc['label']}")
        result = size_trade(
            portfolio_nav=tc["nav"],
            event_category=tc["category"],
            conviction_pct=tc["conviction"],
            confidence_composite=tc["confidence"],
            stop_loss_pct=tc["stop"],
            target_pct=tc["target"],
            current_vix=tc["vix"],
        )
        print(f"   Kelly grezzo:     {result['raw_kelly_pct']:.2f}%")
        print(f"   Half-Kelly:       {result['half_kelly_pct']:.2f}%")
        print(f"   VIX factor:       {result['vix_adjustment_factor']}x  (VIX={result['current_vix']})")
        print(f"   Conviction factor:{result['conviction_adjustment_factor']}x")
        q_emoji = {"STRONG": "💪", "MODERATE": "👍", "WEAK": "⚠️", "NO_TRADE": "🚫"}.get(result["kelly_quality"], "?")
        print(f"   Kelly quality:    {q_emoji} {result['kelly_quality']}")
        print(f"   → SIZE FINALE:    {result['position_size_pct']:.3f}% NAV = €{result['position_size_eur']:.2f}")
        print(f"   → Max loss:       €{result['max_loss_eur']:.2f}")
        print(f"   → Commissioni:    €{result['commission_cost_eur']:.2f}")
        for w in result["warnings"]:
            print(f"   ⚠️  {w}")

    print("\n✅ Test completato.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Position Sizer — MacroSignalTool")
    parser.add_argument("--test", action="store_true", help="Esegui test con 4 scenari di esempio")
    parser.add_argument("--nav", type=float, default=10000, help="Portfolio NAV (default: 10000)")
    parser.add_argument("--category", type=str, default="ENERGY_SUPPLY_SHOCK")
    parser.add_argument("--conviction", type=float, default=75)
    parser.add_argument("--confidence", type=float, default=0.70)
    parser.add_argument("--stop", type=float, default=-7.5)
    parser.add_argument("--target", type=float, default=15.0)
    parser.add_argument("--vix", type=float, default=None)
    args = parser.parse_args()

    if args.test:
        _run_test()
    else:
        result = size_trade(
            portfolio_nav=args.nav,
            event_category=args.category,
            conviction_pct=args.conviction,
            confidence_composite=args.confidence,
            stop_loss_pct=args.stop,
            target_pct=args.target,
            current_vix=args.vix,
        )
        print(json.dumps(result, indent=2))
