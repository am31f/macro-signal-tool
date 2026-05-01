import { useState, useEffect, useCallback } from 'react'
import Dashboard from './components/Dashboard.jsx'
import SignalDetail from './components/SignalDetail.jsx'
import Performance from './components/Performance.jsx'
import Journal from './components/Journal.jsx'
import { getHealth, getLatestSignals, runSignals } from './api.js'

// ── Icone SVG inline ─────────────────────────────────────────────────────────
const Icon = {
  dashboard: (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
    </svg>
  ),
  signal: (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M13 10V3L4 14h7v7l9-11h-7z" />
    </svg>
  ),
  performance: (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
    </svg>
  ),
  journal: (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  ),
}

const NAV_ITEMS = [
  { id: 'dashboard',   label: 'Dashboard',   icon: Icon.dashboard },
  { id: 'signals',     label: 'Segnali',     icon: Icon.signal },
  { id: 'performance', label: 'Performance', icon: Icon.performance },
  { id: 'journal',     label: 'Journal',     icon: Icon.journal },
]

// ── Shared utilities ──────────────────────────────────────────────────────────
export const CATEGORY_COLORS = {
  ENERGY_SUPPLY_SHOCK:       'bg-orange-900/60 text-orange-300',
  MILITARY_CONFLICT:         'bg-red-900/60 text-red-300',
  SANCTIONS_IMPOSED:         'bg-purple-900/60 text-purple-300',
  CENTRAL_BANK_SURPRISE:     'bg-blue-900/60 text-blue-300',
  TRADE_WAR_TARIFF:          'bg-yellow-900/60 text-yellow-300',
  CYBER_ATTACK:              'bg-cyan-900/60 text-cyan-300',
  SOVEREIGN_CRISIS:          'bg-rose-900/60 text-rose-300',
  COMMODITY_SUPPLY_AGRI:     'bg-green-900/60 text-green-300',
  NUCLEAR_THREAT:            'bg-red-800/80 text-red-200',
  ELECTION_SURPRISE:         'bg-indigo-900/60 text-indigo-300',
  PANDEMIC_HEALTH:           'bg-teal-900/60 text-teal-300',
  INFRASTRUCTURE_DISRUPTION: 'bg-amber-900/60 text-amber-300',
}

export const KELLY_COLOR = {
  STRONG:   'text-green-400',
  MODERATE: 'text-yellow-400',
  WEAK:     'text-orange-400',
  NO_TRADE: 'text-red-400',
}

export function LoadingSpinner({ label = 'Caricamento...' }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-3">
      <div className="w-8 h-8 border-2 border-sky-500 border-t-transparent rounded-full animate-spin" />
      <span className="text-sm text-slate-400">{label}</span>
    </div>
  )
}

export function EmptyState({ message, hint }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-2 text-center">
      <div className="text-4xl mb-2">📭</div>
      <p className="text-slate-300 font-medium">{message}</p>
      {hint && <p className="text-slate-500 text-sm max-w-xs">{hint}</p>}
    </div>
  )
}

// ── Signal card (usata in SignalsList) ────────────────────────────────────────
function SignalCard({ signal, onSelect }) {
  const conf = signal.confidence_composite ?? 0
  const confColor = conf >= 0.75 ? 'text-green-400' : conf >= 0.55 ? 'text-yellow-400' : 'text-red-400'
  const catColor = CATEGORY_COLORS[signal.event_category] || 'bg-slate-600 text-slate-300'

  return (
    <div
      onClick={onSelect}
      className="bg-slate-800 border border-slate-700 rounded-xl p-4 cursor-pointer hover:border-sky-500/50 hover:bg-slate-750 transition-all group"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${catColor}`}>
              {signal.event_category?.replace(/_/g, ' ')}
            </span>
            {signal.trade_type && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-slate-600 text-slate-300">
                {signal.trade_type}
              </span>
            )}
            <span className="text-xs text-slate-500">{signal.entry_timing}</span>
          </div>
          <p className="text-sm text-slate-200 font-medium truncate group-hover:text-white">
            {signal.headline}
          </p>
        </div>
        <div className="text-right shrink-0">
          <div className={`text-lg font-bold ${confColor}`}>
            {(conf * 100).toFixed(0)}%
          </div>
          <div className="text-xs text-slate-500">confidence</div>
        </div>
      </div>
      <div className="flex items-center gap-4 mt-2 text-xs text-slate-500">
        <span>Materiality: <span className="text-slate-300">{((signal.materiality_score ?? 0) * 100).toFixed(0)}%</span></span>
        <span>Size: <span className="text-sky-400 font-medium">€{signal.position_size_eur?.toFixed(0) ?? '–'}</span></span>
        <span>Kelly: <span className={KELLY_COLOR[signal.kelly_quality] || 'text-slate-400'}>{signal.kelly_quality ?? '–'}</span></span>
      </div>
    </div>
  )
}

// ── SignalsList (pagina segnali quando nessun detail è aperto) ────────────────
function SignalsList({ onSelect }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)

  useEffect(() => {
    getLatestSignals()
      .then(d => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [])

  const handleRunPipeline = async () => {
    setRunning(true)
    try {
      const result = await runSignals(30)
      setData({ count: result.signals_generated, signals: result.signals || [] })
    } catch (e) {
      alert(`Errore pipeline: ${e.message}`)
    } finally {
      setRunning(false)
    }
  }

  if (loading) return <LoadingSpinner label="Caricamento segnali..." />

  const signals = data?.signals || []

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-xl font-semibold text-white">Segnali</h2>
          {signals.length > 0 && (
            <p className="text-xs text-slate-500 mt-0.5">{signals.length} segnali in cache — clicca per il dettaglio</p>
          )}
        </div>
        <button
          onClick={handleRunPipeline}
          disabled={running}
          className="flex items-center gap-2 px-4 py-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
        >
          {running ? '⏳ Elaborazione...' : '▶ Esegui pipeline'}
        </button>
      </div>

      {signals.length === 0 ? (
        <EmptyState
          message="Nessun segnale in cache"
          hint="Clicca 'Esegui pipeline' per processare le ultime news classificate."
        />
      ) : (
        <div className="grid gap-3">
          {signals.map((s, i) => (
            <SignalCard
              key={i}
              signal={s}
              onSelect={() => onSelect(s.index ?? i)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── App root ──────────────────────────────────────────────────────────────────
export default function App() {
  const [page, setPage] = useState('dashboard')
  const [selectedSignalIndex, setSelectedSignalIndex] = useState(null)
  const [apiStatus, setApiStatus] = useState('checking')
  const [navData, setNavData] = useState(null)

  // Polling salute API ogni 30s
  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const data = await getHealth()
        if (!cancelled) { setApiStatus('ok'); setNavData(data) }
      } catch {
        if (!cancelled) setApiStatus('error')
      }
    }
    check()
    const id = setInterval(check, 30_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  const goToSignalDetail = useCallback((index) => {
    setSelectedSignalIndex(index)
    setPage('signals')
  }, [])

  const navigate = useCallback((pageId) => {
    setPage(pageId)
    setSelectedSignalIndex(null)
  }, [])

  return (
    <div className="min-h-screen flex flex-col" style={{ background: '#0f172a' }}>

      {/* ── Topbar ─────────────────────────────────────────────────────────── */}
      <header
        className="flex items-center justify-between px-6 py-3 border-b border-slate-700/60"
        style={{ background: '#1e293b' }}
      >
        <div className="flex items-center gap-3">
          <span className="text-xl font-bold tracking-tight text-sky-400">⚡ MacroSignalTool</span>
          <span className="text-xs text-slate-500 hidden sm:block">v0.1 — Paper Trading</span>
        </div>

        <div className="flex items-center gap-4">
          {/* Nav desktop */}
          <nav className="hidden md:flex gap-1">
            {NAV_ITEMS.map(item => (
              <button
                key={item.id}
                onClick={() => navigate(item.id)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                  page === item.id
                    ? 'bg-sky-600 text-white'
                    : 'text-slate-400 hover:text-white hover:bg-slate-700'
                }`}
              >
                {item.icon}{item.label}
              </button>
            ))}
          </nav>

          {/* Status pill */}
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${
              apiStatus === 'ok'       ? 'bg-green-400' :
              apiStatus === 'error'    ? 'bg-red-400'   : 'bg-yellow-400 animate-pulse'
            }`} />
            <span className="text-xs text-slate-400 hidden sm:block">
              {apiStatus === 'ok'
                ? `API OK${navData?.portfolio_nav ? ` · NAV €${navData.portfolio_nav.toFixed(0)}` : ''}`
                : apiStatus === 'error'
                ? 'API offline — avvia uvicorn'
                : 'Connessione…'}
            </span>
          </div>
        </div>
      </header>

      {/* ── Mobile nav ─────────────────────────────────────────────────────── */}
      <nav className="md:hidden flex border-b border-slate-700/60" style={{ background: '#1e293b' }}>
        {NAV_ITEMS.map(item => (
          <button
            key={item.id}
            onClick={() => navigate(item.id)}
            className={`flex-1 flex flex-col items-center gap-1 py-2 text-xs font-medium transition-colors ${
              page === item.id ? 'text-sky-400 border-b-2 border-sky-400' : 'text-slate-500'
            }`}
          >
            {item.icon}{item.label}
          </button>
        ))}
      </nav>

      {/* ── Contenuto principale ────────────────────────────────────────────── */}
      <main className="flex-1 overflow-auto">
        {page === 'dashboard' && (
          <Dashboard onSignalClick={goToSignalDetail} />
        )}
        {page === 'signals' && (
          selectedSignalIndex !== null
            ? <SignalDetail
                index={selectedSignalIndex}
                onBack={() => setSelectedSignalIndex(null)}
              />
            : <SignalsList onSelect={goToSignalDetail} />
        )}
        {page === 'performance' && <Performance />}
        {page === 'journal'     && <Journal />}
      </main>
    </div>
  )
}
