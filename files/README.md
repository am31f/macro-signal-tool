# MacroSignalTool 🌍📊

Tool personale di analisi macro-geopolitica per segnali di trading con paper trading integrato.

**Owner:** Andrea  
**Versione:** 0.1.0  
**Stato:** Phase 1-2 in sviluppo

---

## Setup rapido

### 1. Prerequisiti
- Python 3.11+
- Node.js 18+ (per frontend, Phase 5)

### 2. Installa dipendenze
```bash
cd macro-signal-tool
pip install -r requirements.txt
```

### 3. Configura API key
```bash
cp .env.template .env
# Edita .env e inserisci ANTHROPIC_API_KEY
```

### 4. Test ingestion news
```bash
python backend/news_ingestion.py --test
```

### 5. Test classificatore (richiede API key)
```bash
python backend/news_classifier.py --test
```

---

## Architettura (7 Phase)

| Phase | Nome | Stato |
|-------|------|-------|
| 0 | Ideazione e ricerca | ✅ Done |
| 1 | Architettura e Knowledge Base | 🔄 In progress |
| 2 | News Ingestion Engine | 🔄 In progress |
| 3 | Signal Generator | ⏳ Todo |
| 4 | Paper Trading Engine | ⏳ Todo |
| 5 | Web Interface (React) | ⏳ Todo |
| 6 | Alerting e automazione | ⏳ Todo |
| 7 | Go-live preparation | ⏳ Todo |

---

## Struttura file

```
macro-signal-tool/
├── .env.template          ← Copia in .env e compila
├── requirements.txt
├── macro_trading_tool.json ← Stato progetto (tracking sessioni Claude)
├── data/
│   ├── asset_map.json         ← Universe ticker con reaction history
│   ├── geographic_exposure.json ← Revenue breakdown per area geografica
│   └── news_cache.json        ← Cache news ingested (auto-generato)
└── backend/
    ├── news_ingestion.py   ← Phase 2.1 ✅
    ├── news_classifier.py  ← Phase 2.2 ✅
    ├── cross_asset_validator.py  ← Phase 2.3 (todo)
    ├── signal_pipeline.py        ← Phase 3.1 (todo)
    ├── trade_structurer.py       ← Phase 3.2 (todo)
    ├── position_sizer.py         ← Phase 3.3 (todo)
    ├── portfolio_manager.py      ← Phase 4.1 (todo)
    ├── paper_executor.py         ← Phase 4.2 (todo)
    ├── performance_tracker.py    ← Phase 4.3 (todo)
    └── paper_trading.db          ← SQLite (auto-generato)
```

---

## Logica segnale (8 step)

1. **Categorizzazione evento** → 12 categorie macro
2. **Materiality score** → Può muovere variabili macro reali?
3. **Novelty score** → Già prezzato dai mercati?
4. **Cross-asset confirmation** → 4 di 5 asset confermano?
5. **GPR Index threshold** → Sopra 80° percentile?
6. **Struttura trade** → ETF / opzione / future?
7. **Position sizing half-Kelly** → Max 5% NAV
8. **Entry timing** → T+1 / T+3 / T+5

---

## Paper trading go-live checklist

- [ ] Min 30 trade completati
- [ ] Win rate > 52%
- [ ] Sharpe simulato > 0.8
- [ ] Max drawdown < 15% NAV

---

*Sessione di sviluppo tracciata in `macro_trading_tool.json`*
