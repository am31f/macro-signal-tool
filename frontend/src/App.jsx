import { useState, useEffect, useCallback } from 'react'
import Dashboard from './components/Dashboard.jsx'
import SignalDetail from './components/SignalDetail.jsx'
import Performance from './components/Performance.jsx'
import Journal from './components/Journal.jsx'
import { getHealth, getLatestSignals, runSignals, getPeadSignals, runPeadScan, getPeadCalendar, deletePeadSignal, deletePeadScanResult } from './api.js'

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
  { id: 'signals',     label: 'Macro',       icon: Icon.signal },
  { id: 'pead',        label: 'PEAD',        icon: (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
    </svg>
  )},
  { id: 'performance', label: 'Performance', icon: Icon.performance },
  { id: 'journal',     label: 'Journal',     icon: Icon.journal },
]

// ── Shared utilities ──────────────────────────────────────────────────────────
// Badge PEAD — giallo per distinguerlo da MACRO blu
export const PEAD_BADGE = 'bg-yellow-800/60 text-yellow-300'

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

// ── PEADPage ──────────────────────────────────────────────────────────────────
function PEADPage() {
  const [signals, setSignals]           = useState([])
  const [scanReport, setScanReport]     = useState(null)
  const [calendar, setCalendar]         = useState([])
  const [loading, setLoading]           = useState(true)
  const [scanning, setScanning]         = useState(false)
  const [lastUpdate, setLastUpdate]     = useState(null)
  const [deleting, setDeleting]         = useState(null)   // signal_id o ticker in corso di eliminazione
  const [confirmDelete, setConfirmDelete] = useState(null) // { type: 'signal'|'scan', id, label }

  // Filtri segnali attivi
  const [filterDir, setFilterDir]       = useState('ALL')  // ALL | LONG | SHORT
  const [filterKelly, setFilterKelly]   = useState('ALL')  // ALL | STRONG | MODERATE | WEAK

  // Filtri ticker scartati
  const [filterPassed, setFilterPassed] = useState('ALL')  // ALL | passed | failed
  const [filterSector, setFilterSector] = useState('ALL')

  const load = useCallback(async () => {
    try {
      const [sigData, calData] = await Promise.all([
        getPeadSignals().catch(() => ({ count: 0, signals: [], scan_report: null })),
        getPeadCalendar(7).catch(() => ({ count: 0, upcoming: [] })),
      ])
      setSignals(sigData.signals || [])
      setScanReport(sigData.scan_report || null)
      setCalendar(calData.upcoming || [])
      setLastUpdate(new Date().toLocaleTimeString('it-IT'))
    } catch (e) {
      console.error('PEAD load error:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleScan = async () => {
    setScanning(true)
    try {
      await runPeadScan()
      let attempts = 0
      const poll = async () => {
        attempts++
        await load()
        if (attempts < 12) setTimeout(poll, 15000)
        else setScanning(false)
      }
      setTimeout(poll, 10000)
    } catch (e) {
      alert(`Errore scan: ${e.message}`)
      setScanning(false)
    }
  }

  const handleDeleteSignal = async (signalId, label) => {
    if (confirmDelete?.id === signalId) {
      // Secondo click: conferma ed esegui
      setDeleting(signalId)
      setConfirmDelete(null)
      try {
        await deletePeadSignal(signalId)
        setSignals(prev => prev.filter(s => (s.signal_id ?? s) !== signalId))
      } catch (e) {
        alert(`Errore eliminazione: ${e.message}`)
      } finally {
        setDeleting(null)
      }
    } else {
      // Primo click: chiedi conferma
      setConfirmDelete({ type: 'signal', id: signalId, label })
      setTimeout(() => setConfirmDelete(null), 4000) // annulla dopo 4s
    }
  }

  const handleDeleteScanResult = async (ticker) => {
    if (confirmDelete?.id === ticker) {
      setDeleting(ticker)
      setConfirmDelete(null)
      try {
        await deletePeadScanResult(ticker)
        setScanReport(prev => prev ? {
          ...prev,
          ticker_results: (prev.ticker_results || []).filter(tr => tr.ticker !== ticker),
        } : prev)
      } catch (e) {
        alert(`Errore eliminazione: ${e.message}`)
      } finally {
        setDeleting(null)
      }
    } else {
      setConfirmDelete({ type: 'scan', id: ticker, label: ticker })
      setTimeout(() => setConfirmDelete(null), 4000)
    }
  }

  if (loading) return <LoadingSpinner label="Caricamento PEAD..." />

  // Segnali filtrati
  const filteredSignals = signals.filter(s => {
    if (filterDir !== 'ALL' && s.direction !== filterDir) return false
    if (filterKelly !== 'ALL' && s.kelly_quality !== filterKelly) return false
    return true
  })

  // Ticker scartati filtrati
  const allTickers = scanReport?.ticker_results || []
  const sectors = [...new Set(allTickers.map(t => t.sector).filter(Boolean))]
  const filteredTickers = allTickers.filter(tr => {
    if (filterPassed === 'passed' && !tr.passed) return false
    if (filterPassed === 'failed' && tr.passed) return false
    if (filterSector !== 'ALL' && tr.sector !== filterSector) return false
    return true
  })

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h1 className="text-2xl font-bold text-white">PEAD</h1>
            <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-800/60 text-yellow-300 font-medium">
              EARNINGS DRIFT
            </span>
          </div>
          <p className="text-xs text-slate-500">
            Post-Earnings Announcement Drift — strategia parallela alla pipeline macro
            {lastUpdate && ` · Aggiornato ${lastUpdate}`}
          </p>
        </div>
        <button
          onClick={handleScan}
          disabled={scanning}
          className="px-4 py-2 bg-yellow-700 hover:bg-yellow-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
        >
          {scanning ? '⏳ Scan in corso...' : '🔍 Scan earnings'}
        </button>
      </div>

      {/* Segnali attivi */}
      <section className="mb-6">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
            Segnali attivi ({filteredSignals.length}{filteredSignals.length !== signals.length ? ` / ${signals.length}` : ''})
          </h2>
          {signals.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              {/* Filtro direzione */}
              <div className="flex rounded-lg overflow-hidden border border-slate-600 text-xs">
                {['ALL','LONG','SHORT'].map(v => (
                  <button key={v} onClick={() => setFilterDir(v)}
                    className={`px-2.5 py-1 font-medium transition-colors ${filterDir === v ? 'bg-yellow-700 text-white' : 'bg-slate-800 text-slate-400 hover:text-white'}`}>
                    {v === 'ALL' ? 'Dir.' : v}
                  </button>
                ))}
              </div>
              {/* Filtro kelly */}
              <div className="flex rounded-lg overflow-hidden border border-slate-600 text-xs">
                {['ALL','STRONG','MODERATE','WEAK'].map(v => (
                  <button key={v} onClick={() => setFilterKelly(v)}
                    className={`px-2.5 py-1 font-medium transition-colors ${filterKelly === v ? 'bg-yellow-700 text-white' : 'bg-slate-800 text-slate-400 hover:text-white'}`}>
                    {v === 'ALL' ? 'Kelly' : v.charAt(0) + v.slice(1).toLowerCase()}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {signals.length === 0 ? (
          <div className="bg-slate-800 border border-slate-700 rounded-xl p-6 text-center">
            <p className="text-slate-400 text-sm">Nessun segnale PEAD attivo</p>
            <p className="text-slate-500 text-xs mt-1">
              Lo scanner gira automaticamente alle 07:00 e 22:00 CET, oppure clicca "Scan earnings"
            </p>
          </div>
        ) : filteredSignals.length === 0 ? (
          <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 text-center">
            <p className="text-slate-500 text-xs">Nessun segnale corrisponde ai filtri selezionati</p>
          </div>
        ) : (
          <div className="grid gap-3">
            {filteredSignals.map((s, i) => {
              const conf = s.confidence_base ?? 0
              const confColor = conf >= 0.65 ? 'text-green-400' : conf >= 0.52 ? 'text-yellow-400' : 'text-slate-400'
              const dirColor = s.direction === 'LONG' ? 'bg-green-900/60 text-green-300' : 'bg-red-900/60 text-red-300'
              const kellyColor = { STRONG: 'text-green-400', MODERATE: 'text-yellow-400', WEAK: 'text-orange-400' }
              const isDeleting = deleting === s.signal_id
              const isConfirming = confirmDelete?.id === s.signal_id
              return (
                <div key={i} className="bg-slate-800 border border-yellow-700/30 rounded-xl p-4">
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-800/60 text-yellow-300 font-medium">EARNINGS</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full font-bold ${dirColor}`}>{s.direction}</span>
                      <span className="font-semibold text-white">{s.ticker}</span>
                      <span className="text-xs text-slate-400">{s.company_name}</span>
                    </div>
                    <div className="flex items-center gap-3 shrink-0">
                      <div className="text-right">
                        <div className={`text-lg font-bold ${confColor}`}>{(conf * 100).toFixed(0)}%</div>
                        <div className="text-xs text-slate-500">confidence</div>
                      </div>
                      {/* Bottone elimina */}
                      <button
                        onClick={() => handleDeleteSignal(s.signal_id, s.ticker)}
                        disabled={isDeleting}
                        title={isConfirming ? 'Clicca ancora per confermare' : 'Elimina segnale'}
                        className={`p-1.5 rounded-lg text-xs font-medium transition-colors ${
                          isConfirming
                            ? 'bg-red-600 text-white animate-pulse'
                            : 'bg-slate-700 text-slate-400 hover:bg-red-900/60 hover:text-red-300'
                        }`}
                      >
                        {isDeleting ? '⏳' : isConfirming ? 'Conferma?' : '✕'}
                      </button>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs mt-2">
                    <div><span className="text-slate-500">SUE score</span><p className="text-white font-mono font-medium">{s.sue_score?.toFixed(2)}σ</p></div>
                    <div><span className="text-slate-500">EPS surprise</span>
                      <p className={`font-mono font-medium ${s.eps_surprise_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {s.eps_surprise_pct >= 0 ? '+' : ''}{s.eps_surprise_pct?.toFixed(1)}%
                      </p>
                    </div>
                    <div><span className="text-slate-500">Size</span><p className="text-sky-400 font-medium">€{s.position_size_eur?.toFixed(0) ?? '–'}</p></div>
                    <div><span className="text-slate-500">Kelly</span><p className={kellyColor[s.kelly_quality] || 'text-slate-400'}>{s.kelly_quality ?? '–'}</p></div>
                    <div><span className="text-slate-500">Stop</span><p className="text-red-400 font-mono">{s.stop_price?.toFixed(3)}</p></div>
                    <div><span className="text-slate-500">Target</span><p className="text-green-400 font-mono">{s.target_price?.toFixed(3)}</p></div>
                    <div><span className="text-slate-500">Hold</span><p className="text-slate-300">{s.hold_days_target}g</p></div>
                    <div><span className="text-slate-500">Settore</span><p className="text-slate-300 truncate">{s.sector ?? '–'}</p></div>
                  </div>
                  {s.macro_regime_boost && (
                    <div className="mt-2 text-xs text-yellow-300 bg-yellow-900/20 rounded px-2 py-1">✦ Macro boost: {s.macro_regime_note}</div>
                  )}
                  {conf < 0.60 && (
                    <div className="mt-2 text-xs text-orange-300 bg-orange-900/20 border border-orange-700/30 rounded px-2 py-1">
                      ⚠ Confidence {(conf * 100).toFixed(0)}% — sotto soglia alert Telegram (60%). Segnale valido ma notifica non inviata.
                    </div>
                  )}
                  <div className="mt-2 text-xs text-slate-600 font-mono">{s.signal_id} · earnings {s.earnings_date}</div>
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* Dettaglio ultimo scan — sempre visibile */}
      {scanReport && (
        <section className="mb-6">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
              Dettaglio ultimo scan
            </h2>
            {allTickers.length > 0 && (
              <div className="flex items-center gap-2 flex-wrap">
                {/* Filtro passed/failed */}
                <div className="flex rounded-lg overflow-hidden border border-slate-600 text-xs">
                  {[['ALL','Tutti'],['passed','✦ Segnali'],['failed','✕ Scartati']].map(([v, label]) => (
                    <button key={v} onClick={() => setFilterPassed(v)}
                      className={`px-2.5 py-1 font-medium transition-colors ${filterPassed === v ? 'bg-yellow-700 text-white' : 'bg-slate-800 text-slate-400 hover:text-white'}`}>
                      {label}
                    </button>
                  ))}
                </div>
                {/* Filtro settore */}
                {sectors.length > 1 && (
                  <select
                    value={filterSector}
                    onChange={e => setFilterSector(e.target.value)}
                    className="bg-slate-800 border border-slate-600 text-slate-300 text-xs rounded-lg px-2 py-1 focus:outline-none focus:border-yellow-600"
                  >
                    <option value="ALL">Tutti i settori</option>
                    {sectors.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                )}
              </div>
            )}
          </div>

          <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
            {/* Intestazione */}
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full bg-green-500" />
                <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">Scan completato</span>
              </div>
              {scanReport.scanned_at && (
                <span className="text-xs text-slate-500 font-mono">
                  {new Date(scanReport.scanned_at).toLocaleString('it-IT', {
                    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
                  })} UTC
                </span>
              )}
            </div>

            {/* Contatori */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
              {[
                { label: 'Analizzati',        value: scanReport.total_scanned,        color: 'text-slate-300' },
                { label: 'Con earnings rec.', value: scanReport.with_recent_earnings, color: 'text-yellow-300' },
                { label: 'Segnali generati',  value: scanReport.signals_generated,    color: scanReport.signals_generated > 0 ? 'text-green-400' : 'text-slate-400' },
                { label: 'No dati yfinance',  value: scanReport.skipped_no_data,      color: 'text-slate-500' },
              ].map(({ label, value, color }) => (
                <div key={label} className="bg-slate-700/40 rounded-lg px-3 py-2 text-center">
                  <p className={`text-xl font-bold font-mono ${color}`}>{value ?? '–'}</p>
                  <p className="text-xs text-slate-500 mt-0.5">{label}</p>
                </div>
              ))}
            </div>

            {/* Dettaglio ticker */}
            {filteredTickers.length > 0 ? (
              <div>
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                  Ticker esaminati ({filteredTickers.length}{filteredTickers.length !== allTickers.length ? ` / ${allTickers.length}` : ''})
                </p>
                <div className="space-y-2">
                  {filteredTickers.map((tr, i) => {
                    const isDeleting = deleting === tr.ticker
                    const isConfirming = confirmDelete?.id === tr.ticker
                    return (
                      <div key={i} className={`rounded-lg px-3 py-2 border ${
                        tr.passed ? 'bg-green-900/20 border-green-700/40' : 'bg-slate-700/30 border-slate-600/40'
                      }`}>
                        <div className="flex items-start justify-between gap-2 flex-wrap">
                          <div className="flex items-center gap-2 flex-wrap">
                            {tr.passed
                              ? <span className="text-green-400 text-xs font-bold">✦ SEGNALE</span>
                              : <span className="text-red-400 text-xs font-bold">✕ SCARTATO</span>
                            }
                            <span className="font-mono font-semibold text-yellow-300 text-sm">{tr.ticker}</span>
                            <span className="text-xs text-slate-500">{tr.earnings_date}</span>
                            {tr.sector && <span className="text-xs text-slate-600">{tr.sector}</span>}
                          </div>
                          <div className="flex items-center gap-3">
                            <div className="flex items-center gap-3 text-xs font-mono">
                              <span className={tr.eps_surprise_pct >= 0 ? 'text-green-400' : 'text-red-400'}>
                                EPS {tr.eps_surprise_pct >= 0 ? '+' : ''}{tr.eps_surprise_pct?.toFixed(1)}%
                              </span>
                              <span className={Math.abs(tr.sue_score) >= 2 ? 'text-yellow-300' : 'text-slate-400'}>
                                SUE {tr.sue_score?.toFixed(2)}σ
                              </span>
                              {tr.market_cap_usd > 0 && (
                                <span className="text-slate-500">${(tr.market_cap_usd / 1e9).toFixed(1)}B</span>
                              )}
                            </div>
                            {/* Bottone elimina */}
                            <button
                              onClick={() => handleDeleteScanResult(tr.ticker)}
                              disabled={isDeleting}
                              title={isConfirming ? 'Clicca ancora per confermare' : 'Rimuovi dal report'}
                              className={`p-1 rounded text-xs transition-colors ${
                                isConfirming
                                  ? 'bg-red-600 text-white animate-pulse'
                                  : 'text-slate-600 hover:text-red-400 hover:bg-red-900/30'
                              }`}
                            >
                              {isDeleting ? '⏳' : isConfirming ? 'Sì?' : '✕'}
                            </button>
                          </div>
                        </div>
                        {!tr.passed && tr.fail_reason && (
                          <p className="text-xs text-slate-500 mt-1 leading-relaxed">{tr.fail_reason}</p>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            ) : allTickers.length === 0 ? (
              <p className="text-xs text-slate-500 text-center py-2">
                Nessun ticker con earnings recenti nel watchlist (lookback {scanReport.lookback_days}g)
              </p>
            ) : (
              <p className="text-xs text-slate-500 text-center py-2">Nessun ticker corrisponde ai filtri selezionati</p>
            )}

            <p className="text-xs text-slate-600 mt-3 text-center">
              Filtri: F1 SUE ≥ 2.0σ · F2 cap $500M–$100B · Alert Telegram: confidence ≥ 60%
            </p>
          </div>
        </section>
      )}

      {/* Calendario earnings prossimi 7 giorni */}
      <section>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Prossimi earnings (7 giorni)
        </h2>
        {calendar.length === 0 ? (
          <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 text-center">
            <p className="text-slate-500 text-xs">Nessun earnings imminente nel watchlist</p>
          </div>
        ) : (
          <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 text-xs text-slate-500 uppercase tracking-wider">
                  <th className="text-left px-4 py-2">Ticker</th>
                  <th className="text-left px-4 py-2">Data</th>
                  <th className="text-right px-4 py-2">Tra</th>
                </tr>
              </thead>
              <tbody>
                {calendar.map((e, i) => (
                  <tr key={i} className="border-t border-slate-700/50 hover:bg-slate-700/20">
                    <td className="px-4 py-2 font-mono font-medium text-yellow-300">{e.ticker}</td>
                    <td className="px-4 py-2 text-slate-300">{e.earnings_date}</td>
                    <td className="px-4 py-2 text-right text-slate-400 text-xs">
                      {e.days_until === 0 ? 'oggi' : e.days_until === 1 ? 'domani' : `${e.days_until}g`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Info strategia */}
      <div className="mt-6 bg-slate-800/50 border border-slate-700/40 rounded-xl p-4 text-xs text-slate-500">
        <p className="font-medium text-slate-400 mb-1">Come funziona PEAD</p>
        <p>SUE ≥ 2σ · Market cap $500M–$100B · Hold 20-45 giorni · Stop 4% · Target 8% (R/R 2:1)</p>
        <p className="mt-1">Scanner automatico alle 07:00 e 22:00 CET · Segnali ID: K-PEAD-YYYY-NNNN</p>
        <p className="mt-1">Win rate atteso: 62% base, 70% con macro boost · Mercati: USA + Europa</p>
      </div>

      {/* Calendario stagioni earnings */}
      <div className="mt-4 bg-slate-800/50 border border-slate-700/40 rounded-xl p-4 text-xs text-slate-500">
        <p className="font-medium text-slate-400 mb-3">Calendario stagioni earnings</p>
        <div className="grid grid-cols-1 gap-2">
          {[
            { stagione: 'Q1 2026', periodo: 'Apr – Mag 2026', picco: 'Metà apr – metà mag', stato: 'closing' },
            { stagione: 'Q2 2026', periodo: 'Lug – Ago 2026', picco: 'Metà lug – metà ago', stato: 'next' },
            { stagione: 'Q3 2026', periodo: 'Ott – Nov 2026', picco: 'Metà ott – metà nov', stato: 'future' },
            { stagione: 'Q4 2026', periodo: 'Gen – Feb 2027', picco: 'Metà gen – metà feb 2027', stato: 'future' },
          ].map(({ stagione, periodo, picco, stato }) => (
            <div key={stagione} className="flex items-center justify-between bg-slate-700/30 rounded-lg px-3 py-2">
              <div className="flex items-center gap-2">
                <span className={`inline-block w-2 h-2 rounded-full ${
                  stato === 'closing' ? 'bg-orange-400' :
                  stato === 'next'    ? 'bg-yellow-400' :
                  'bg-slate-600'
                }`} />
                <span className="font-medium text-slate-300">{stagione}</span>
              </div>
              <span className="text-slate-400">{periodo}</span>
              <span className="text-slate-500 hidden sm:block">picco: {picco}</span>
              <span className={`text-xs px-2 py-0.5 rounded-full ${
                stato === 'closing' ? 'bg-orange-900/40 text-orange-300' :
                stato === 'next'    ? 'bg-yellow-900/40 text-yellow-300' :
                'bg-slate-700 text-slate-500'
              }`}>
                {stato === 'closing' ? 'in chiusura' : stato === 'next' ? 'prossima' : 'attesa'}
              </span>
            </div>
          ))}
        </div>
        <div className="mt-3 pt-3 border-t border-slate-700/50 space-y-1">
          <p><span className="text-slate-400">USA</span>: reportistica trimestrale regolare (Q1-Q4)</p>
          <p><span className="text-slate-400">Europa (DE, FR, UK, IT)</span>: report con 1-2 settimane di ritardo rispetto agli USA · alcune aziende (DE, FR) riportano solo semestrale (H1/H2) - meno segnali PEAD ma comunque presenti</p>
          <p className="text-slate-600 mt-1">Tra una stagione e l'altra è normale non ricevere segnali per 4–6 settimane.</p>
        </div>
      </div>
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
        {page === 'pead'        && <PEADPage />}
        {page === 'performance' && <Performance />}
        {page === 'journal'     && <Journal />}
      </main>
    </div>
  )
}
