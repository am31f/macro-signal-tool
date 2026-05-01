# Deploy MacroSignalTool su Railway — Guida passo-passo

## 1. Crea repository GitHub

1. Vai su https://github.com/new
2. Nome: `macro-signal-tool` — Private ✓
3. Clicca "Create repository"
4. Sul tuo PC apri un terminale nella cartella del progetto:

```bash
cd "C:\Users\39320\Desktop\macro-signal-tool"
git init
git add .
git commit -m "Initial deploy"
git remote add origin https://github.com/TUO_USERNAME/macro-signal-tool.git
git push -u origin main
```

---

## 2. Deploy su Railway

1. Vai su https://railway.app → Log in con GitHub
2. Clicca **"New Project"** → **"Deploy from GitHub repo"**
3. Seleziona `macro-signal-tool`
4. Railway rileva automaticamente Python e avvia il build

---

## 3. Aggiungi Volume persistente (per il database)

1. Nel progetto Railway, clicca sul servizio → tab **"Volumes"**
2. Clicca **"Add Volume"**
3. Mount path: `/data`
4. Clicca **"Add"**

> Questo è fondamentale: senza il volume il DB si azzera ad ogni deploy.

---

## 4. Configura le variabili d'ambiente

Nel tab **"Variables"** del servizio Railway, aggiungi:

| Variabile | Valore |
|-----------|--------|
| `ANTHROPIC_API_KEY` | la tua chiave Anthropic |
| `TELEGRAM_BOT_TOKEN` | token del bot Telegram |
| `TELEGRAM_CHAT_ID` | il tuo chat ID Telegram |
| `TELEGRAM_SIGNAL_THRESHOLD` | `0.70` |
| `INITIAL_NAV` | `10000` |
| `MAX_POSITION_PCT` | `0.05` |
| `LOG_LEVEL` | `INFO` |
| `POLL_INTERVAL_MINUTES` | `60` |
| `PRICE_UPDATE_INTERVAL_MINUTES` | `15` |

---

## 5. Ottieni l'URL pubblico

1. Tab **"Settings"** → **"Networking"** → **"Generate Domain"**
2. Copia l'URL (es. `macro-signal-tool-production.up.railway.app`)
3. Questo è il tuo backend H24 — accessibile da ovunque

---

## 6. Aggiorna il frontend per puntare al server Railway

Nel file `frontend/src/api.js`, cambia:
```js
const BASE = '/api'
```
in:
```js
const BASE = 'https://macro-signal-tool-production.up.railway.app'
```

Oppure usa una variabile d'ambiente Vite (`.env.local`):
```
VITE_API_BASE=https://macro-signal-tool-production.up.railway.app
```

---

## Note importanti

- Railway free tier: 500h/mese — non sufficiente per H24
- Piano Hobby ($5/mese): risorse illimitate, H24
- Il backend su Railway gira in background — fetch news ogni ora, prezzi ogni 15min
- Telegram ti manda gli alert anche con il PC spento
