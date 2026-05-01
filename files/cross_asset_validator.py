"""
cross_asset_validator.py
Phase 2, Task 2.3 — MacroSignalTool

Scarica prezzi live/recenti di 5 asset macro (Brent, Gold, DXY, US10Y, VIX)
via yfinance e calcola z-score vs rolling 60 giorni.
Restituisce un cross_asset_confirmation_score: quanti dei 5 asset si stanno
muovendo >= 1.5σ in direzione coerente con la categoria evento classificata.

Dipendenze: pip install yfinance pandas
Testabile: python cross_asset_validator.py --test
"""

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import yfinance as yf

# ─── Configurazione logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cross_asset_validator")

# ─── Costanti ────────────────────────────────────────────────────────────────

ROLLING_DAYS = 60          # finestra per calcolo media/std
SIGMA_THRESHOLD = 1.5      # soglia z-score per "movimento significativo"
MIN_CONFIRMING = 4         # almeno 4 di 5 asset devono confermare

# Mappa dei 5 asset macro con simbolo yfinance e direzione attesa per categoria
MACRO_ASSETS = {
    "BRENT": {
        "symbol": "BZ=F",
        "name": "Brent Crude Future",
        "expected_direction": {
            "ENERGY_SUPPLY_SHOCK": "up",
            "MILITARY_CONFLICT": "up",
            "SANCTIONS_IMPOSED": "up",
            "INFRASTRUCTURE_DISRUPTION": "up",
            "CENTRAL_BANK_SURPRISE_hawkish": "down",
            "CENTRAL_BANK_SURPRISE_dovish": "up",
            "TRADE_WAR_TARIFF": "down",
            "PANDEMIC_HEALTH": "down",
            "SOVEREIGN_CRISIS": "neutral",
            "NUCLEAR_THREAT": "up",
            "CYBER_ATTACK": "neutral",
            "ELECTION_SURPRISE": "neutral",
            "COMMODITY_SUPPLY_AGRI": "neutral",
        },
    },
    "GOLD": {
        "symbol": "GC=F",
        "name": "Gold Future",
        "expected_direction": {
            "ENERGY_SUPPLY_SHOCK": "up",
            "MILITARY_CONFLICT": "up",
            "SANCTIONS_IMPOSED": "up",
            "NUCLEAR_THREAT": "up",
            "SOVEREIGN_CRISIS": "up",
            "CENTRAL_BANK_SURPRISE_hawkish": "down",
            "CENTRAL_BANK_SURPRISE_dovish": "up",
            "TRADE_WAR_TARIFF": "up",
            "PANDEMIC_HEALTH": "up",
            "INFRASTRUCTURE_DISRUPTION": "up",
            "CYBER_ATTACK": "up",
            "ELECTION_SURPRISE": "up",
            "COMMODITY_SUPPLY_AGRI": "neutral",
        },
    },
    "DXY": {
        "symbol": "DX-Y.NYB",
        "name": "US Dollar Index",
        "expected_direction": {
            "ENERGY_SUPPLY_SHOCK": "up",
            "MILITARY_CONFLICT": "up",
            "SANCTIONS_IMPOSED": "up",
            "NUCLEAR_THREAT": "up",
            "SOVEREIGN_CRISIS": "up",
            "CENTRAL_BANK_SURPRISE_hawkish": "up",
            "CENTRAL_BANK_SURPRISE_dovish": "down",
            "TRADE_WAR_TARIFF": "up",
            "PANDEMIC_HEALTH": "up",
            "INFRASTRUCTURE_DISRUPTION": "neutral",
            "CYBER_ATTACK": "neutral",
            "ELECTION_SURPRISE": "neutral",
            "COMMODITY_SUPPLY_AGRI": "neutral",
        },
    },
    "US10Y": {
        "symbol": "^TNX",
        "name": "US 10-Year Treasury Yield",
        "expected_direction": {
            # In regime inflazionistico i rendimenti salgono (no flight-to-quality)
            # In regime deflazionistico/recessione scendono (flight-to-quality)
            "ENERGY_SUPPLY_SHOCK": "up",       # inflation channel
            "MILITARY_CONFLICT": "down",        # safety channel (dipende regime)
            "SANCTIONS_IMPOSED": "neutral",
            "NUCLEAR_THREAT": "down",           # panico → flight to quality
            "SOVEREIGN_CRISIS": "down",
            "CENTRAL_BANK_SURPRISE_hawkish": "up",
            "CENTRAL_BANK_SURPRISE_dovish": "down",
            "TRADE_WAR_TARIFF": "down",
            "PANDEMIC_HEALTH": "down",
            "INFRASTRUCTURE_DISRUPTION": "neutral",
            "CYBER_ATTACK": "down",
            "ELECTION_SURPRISE": "neutral",
            "COMMODITY_SUPPLY_AGRI": "up",
        },
    },
    "VIX": {
        "symbol": "^VIX",
        "name": "CBOE Volatility Index",
        "expected_direction": {
            # VIX sale su qualsiasi shock negativo
            "ENERGY_SUPPLY_SHOCK": "up",
            "MILITARY_CONFLICT": "up",
            "SANCTIONS_IMPOSED": "up",
            "NUCLEAR_THREAT": "up",
            "SOVEREIGN_CRISIS": "up",
            "CENTRAL_BANK_SURPRISE_hawkish": "up",
            "CENTRAL_BANK_SURPRISE_dovish": "down",
            "TRADE_WAR_TARIFF": "up",
            "PANDEMIC_HEALTH": "up",
            "INFRASTRUCTURE_DISRUPTION": "up",
            "CYBER_ATTACK": "up",
            "ELECTION_SURPRISE": "up",
            "COMMODITY_SUPPLY_AGRI": "neutral",
        },
    },
}

# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class AssetReading:
    """Lettura di un singolo asset macro."""
    asset_key: str
    symbol: str
    name: str
    current_price: float
    price_60d_ago: float
    rolling_mean: float
    rolling_std: float
    pct_change_1d: float        # variazione % ultima seduta
    zscore_1d: float            # z-score della variazione giornaliera vs rolling std
    expected_direction: str     # "up" / "down" / "neutral"
    actual_direction: str       # "up" / "down" / "flat"
    is_confirming: bool         # True se zscore >= threshold E direzione coerente
    note: str = ""


@dataclass
class CrossAssetResult:
    """Output completo della validazione cross-asset."""
    event_category: str
    timestamp_utc: str
    confirmation_score: int             # 0-5: quanti asset confermano
    confirming_assets: list             # nomi degli asset che confermano
    non_confirming_assets: list
    passes_filter: bool                 # True se confirmation_score >= MIN_CONFIRMING
    sigma_threshold_used: float
    min_confirming_required: int
    asset_readings: list                # lista di AssetReading serializzati
    macro_regime_hint: str              # "inflationary_shock" / "deflationary_shock" / "mixed"
    warning: str = ""


# ─── Fetching prezzi ─────────────────────────────────────────────────────────

def fetch_price_series(symbol: str, days: int = ROLLING_DAYS + 5) -> Optional[pd.Series]:
    """
    Scarica serie storica di close price via yfinance.
    Restituisce pd.Series indexed by date, o None se fallisce.
    """
    try:
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=days)
        ticker = yf.Ticker(symbol)
        hist = ticker.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
        if hist.empty or "Close" not in hist.columns:
            logger.warning(f"Nessun dato per {symbol}")
            return None
        series = hist["Close"].dropna()
        if len(series) < 5:
            logger.warning(f"Troppo pochi dati per {symbol}: {len(series)} barre")
            return None
        return series
    except Exception as e:
        logger.error(f"Errore fetch {symbol}: {e}")
        return None


def compute_zscore(series: pd.Series) -> tuple[float, float, float, float]:
    """
    Data una serie di prezzi, restituisce:
    (pct_change_1d, rolling_mean_pct_change, rolling_std_pct_change, zscore_1d)
    """
    pct_changes = series.pct_change().dropna() * 100  # in percentuale
    if len(pct_changes) < 3:
        return 0.0, 0.0, 1.0, 0.0

    rolling_mean = pct_changes.iloc[:-1].mean()
    rolling_std = pct_changes.iloc[:-1].std()
    last_change = pct_changes.iloc[-1]

    if rolling_std == 0:
        zscore = 0.0
    else:
        zscore = (last_change - rolling_mean) / rolling_std

    return float(last_change), float(rolling_mean), float(rolling_std), float(zscore)


# ─── Logica di validazione ────────────────────────────────────────────────────

def get_expected_direction(asset_key: str, event_category: str) -> str:
    """
    Restituisce la direzione attesa per un asset dato un event_category.
    Gestisce anche le sottocategorie (es. CENTRAL_BANK_SURPRISE_hawkish).
    """
    directions = MACRO_ASSETS[asset_key]["expected_direction"]

    # Cerca prima match esatto
    if event_category in directions:
        return directions[event_category]

    # Cerca prefisso (es. CENTRAL_BANK_SURPRISE)
    for key, direction in directions.items():
        if event_category.startswith(key.split("_")[0]):
            return direction

    return "neutral"


def validate_cross_asset(event_category: str) -> CrossAssetResult:
    """
    Funzione principale: per una data categoria evento, scarica i 5 asset macro,
    calcola gli z-score e restituisce il CrossAssetResult completo.
    """
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    confirming = []
    non_confirming = []
    asset_readings = []
    brent_direction = None  # per macro_regime_hint

    for asset_key, asset_info in MACRO_ASSETS.items():
        symbol = asset_info["symbol"]
        series = fetch_price_series(symbol)

        if series is None:
            # Asset non disponibile: lo consideriamo neutro (non confirming)
            reading = AssetReading(
                asset_key=asset_key,
                symbol=symbol,
                name=asset_info["name"],
                current_price=0.0,
                price_60d_ago=0.0,
                rolling_mean=0.0,
                rolling_std=0.0,
                pct_change_1d=0.0,
                zscore_1d=0.0,
                expected_direction=get_expected_direction(asset_key, event_category),
                actual_direction="flat",
                is_confirming=False,
                note="FETCH_ERROR — asset escluso dalla conferma",
            )
            non_confirming.append(asset_key)
            asset_readings.append(asdict(reading))
            continue

        pct_change_1d, rolling_mean, rolling_std, zscore = compute_zscore(series)

        # Direzione attuale
        if pct_change_1d > 0.05:
            actual_direction = "up"
        elif pct_change_1d < -0.05:
            actual_direction = "down"
        else:
            actual_direction = "flat"

        expected_direction = get_expected_direction(asset_key, event_category)

        # Conferma: abs(zscore) >= soglia E direzione coerente con atteso
        direction_match = (
            expected_direction == "neutral"  # neutral accetta qualsiasi mossa significativa
            or actual_direction == expected_direction
        )
        is_confirming = abs(zscore) >= SIGMA_THRESHOLD and direction_match

        # Nota
        note = ""
        if expected_direction == "neutral":
            note = "Asset neutro per questa categoria — movimento qualsiasi conta"
        elif not direction_match:
            note = f"Direzione attesa {expected_direction}, osservata {actual_direction} — contrarian"
        elif abs(zscore) < SIGMA_THRESHOLD:
            note = f"Zscore {zscore:.2f} < soglia {SIGMA_THRESHOLD} — movimento non significativo"

        reading = AssetReading(
            asset_key=asset_key,
            symbol=symbol,
            name=asset_info["name"],
            current_price=float(series.iloc[-1]),
            price_60d_ago=float(series.iloc[0]),
            rolling_mean=round(rolling_mean, 4),
            rolling_std=round(rolling_std, 4),
            pct_change_1d=round(pct_change_1d, 3),
            zscore_1d=round(zscore, 3),
            expected_direction=expected_direction,
            actual_direction=actual_direction,
            is_confirming=is_confirming,
            note=note,
        )

        if is_confirming:
            confirming.append(asset_key)
        else:
            non_confirming.append(asset_key)

        asset_readings.append(asdict(reading))

        if asset_key == "BRENT":
            brent_direction = actual_direction

    confirmation_score = len(confirming)
    passes_filter = confirmation_score >= MIN_CONFIRMING

    # Macro regime hint
    if event_category in ("ENERGY_SUPPLY_SHOCK", "MILITARY_CONFLICT", "SANCTIONS_IMPOSED"):
        if brent_direction == "up":
            macro_regime_hint = "inflationary_shock — Brent in rialzo, bonds potrebbero NON essere safe haven"
        else:
            macro_regime_hint = "mixed — Brent non conferma shock energetico"
    elif event_category == "CENTRAL_BANK_SURPRISE":
        macro_regime_hint = "policy_shift — verificare FedFunds futures per direzione"
    elif event_category in ("NUCLEAR_THREAT", "PANDEMIC_HEALTH", "SOVEREIGN_CRISIS"):
        macro_regime_hint = "deflationary_shock — flight-to-quality atteso, bonds safe haven"
    else:
        macro_regime_hint = "mixed — regime non determinato automaticamente"

    warning = ""
    if confirmation_score == 0:
        warning = "NESSUN asset conferma — evento probabilmente già prezzato o non macro-rilevante"
    elif confirmation_score < MIN_CONFIRMING and confirmation_score >= 2:
        warning = f"Solo {confirmation_score}/5 asset confermano — segnale debole, non aprire posizioni full size"

    return CrossAssetResult(
        event_category=event_category,
        timestamp_utc=timestamp,
        confirmation_score=confirmation_score,
        confirming_assets=confirming,
        non_confirming_assets=non_confirming,
        passes_filter=passes_filter,
        sigma_threshold_used=SIGMA_THRESHOLD,
        min_confirming_required=MIN_CONFIRMING,
        asset_readings=asset_readings,
        macro_regime_hint=macro_regime_hint,
        warning=warning,
    )


# ─── Interfaccia pubblica ─────────────────────────────────────────────────────

def run_validation(event_category: str) -> dict:
    """
    Entry point pubblico chiamato da signal_pipeline.py.
    Restituisce dict serializzato del CrossAssetResult.
    """
    logger.info(f"Avvio validazione cross-asset per categoria: {event_category}")
    result = validate_cross_asset(event_category)
    logger.info(
        f"Risultato: {result.confirmation_score}/5 asset confermano "
        f"— passes_filter={result.passes_filter}"
    )
    return asdict(result)


# ─── CLI test ─────────────────────────────────────────────────────────────────

def _run_test():
    """Test rapido con categoria ENERGY_SUPPLY_SHOCK."""
    print("\n" + "="*60)
    print("TEST: cross_asset_validator.py")
    print("Categoria evento: ENERGY_SUPPLY_SHOCK")
    print("="*60)

    result = run_validation("ENERGY_SUPPLY_SHOCK")

    print(f"\n📊 Confirmation score: {result['confirmation_score']}/{len(MACRO_ASSETS)}")
    print(f"✅ Passes filter (>= {MIN_CONFIRMING}): {result['passes_filter']}")
    print(f"🌍 Macro regime hint: {result['macro_regime_hint']}")

    if result["warning"]:
        print(f"⚠️  Warning: {result['warning']}")

    print(f"\n{'Asset':<10} {'Δ1d%':>8} {'Z-score':>9} {'Atteso':>8} {'Osservato':>10} {'Conferma':>10}")
    print("-"*60)
    for r in result["asset_readings"]:
        conferma = "✅" if r["is_confirming"] else "❌"
        print(
            f"{r['asset_key']:<10} "
            f"{r['pct_change_1d']:>8.2f}% "
            f"{r['zscore_1d']:>9.2f} "
            f"{r['expected_direction']:>8} "
            f"{r['actual_direction']:>10} "
            f"{conferma:>10}"
        )

    print("\n" + json.dumps(result, indent=2)[:800] + "\n...")
    print("\n✅ Test completato.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cross-Asset Validator — MacroSignalTool")
    parser.add_argument("--test", action="store_true", help="Esegui test rapido con ENERGY_SUPPLY_SHOCK")
    parser.add_argument(
        "--category",
        type=str,
        default="ENERGY_SUPPLY_SHOCK",
        help="Categoria evento da validare (default: ENERGY_SUPPLY_SHOCK)",
    )
    args = parser.parse_args()

    if args.test:
        _run_test()
    else:
        result = run_validation(args.category)
        print(json.dumps(result, indent=2))
