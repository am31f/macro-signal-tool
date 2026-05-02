/**
 * api.js — Client per MacroSignalTool FastAPI (localhost:8000)
 * Tutti i fetch passano per il proxy Vite (/api → :8000)
 */

const BASE = import.meta.env.VITE_API_BASE || '/api'

async function fetchJSON(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`API ${path} → ${res.status}: ${err}`)
  }
  return res.json()
}

// ── Health ──────────────────────────────────────────────────────────────────
export const getHealth       = () => fetchJSON('/')

// ── News ────────────────────────────────────────────────────────────────────
export const fetchNews       = () => fetchJSON('/news/fetch', { method: 'POST' })
export const getUnclassified = (limit = 10) => fetchJSON(`/news/unclassified?limit=${limit}`)

// ── Signals ─────────────────────────────────────────────────────────────────
export const runSignals      = (limit = 30) => fetchJSON(`/signals/run?limit=${limit}`)
export const getLatestSignals = () => fetchJSON('/signals/latest')

// ── Trades ──────────────────────────────────────────────────────────────────
export const executeSignal   = (signalIndex) =>
  fetchJSON('/trade/execute', {
    method: 'POST',
    body: JSON.stringify({ signal_index: signalIndex, confirm: true }),
  })

export const closePosition   = (positionId, closePrice, reason = 'manual') =>
  fetchJSON('/trade/close', {
    method: 'POST',
    body: JSON.stringify({ position_id: positionId, close_price: closePrice, reason }),
  })

// ── Portfolio ────────────────────────────────────────────────────────────────
export const getPortfolio    = () => fetchJSON('/portfolio')
export const getPositions    = () => fetchJSON('/portfolio/positions')
export const updatePrices    = () => fetchJSON('/portfolio/update-prices', { method: 'POST' })

// ── Performance ──────────────────────────────────────────────────────────────
export const getPerformance  = () => fetchJSON('/performance')

// ── Journal ──────────────────────────────────────────────────────────────────
export const getJournal      = (limit = 20) => fetchJSON(`/journal?limit=${limit}`)
