import { useState, useEffect, useCallback, useRef } from 'react'
import { getPortfolio, getLatestSignals, fetchNews, runSignals, updatePrices } from '../api.js'
import { CATEGORY_COLORS, KELLY_COLOR, LoadingSpinner, EmptyState } from '../App.jsx'

// ── Stat Card ─────────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, color = 'text-white', trend }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
      <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
      {trend !== undefined && (
        <p className={`text-xs mt-1 font-medium ${trend >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {trend >= 0 ? '▲' : '▼'} {Math.abs(trend).toFixed(2)}%
        </p>
      )}
    </div>
  )
}

// ── Position Row ──────────────────────────────────────────────────────────────
function PositionRow({ pos }) {
  const pnlColor = (pos.pnl_eur ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
  const catBadge = CATEGORY_COLORS[pos.event_category] || 'bg-slate-600 text-slate-300'
  const stopDist = pos.current_price && pos.stop_price
    ? (((pos.current_price - pos.stop_price) / pos.current_price) * 100).toFixed(1)
    : null

  return (
    <tr className="border-t border-slate-700/50 hover:bg-slate-700/20 transition-colors">
      <td className="py-2.5 px-3">
        <div className="flex items-center gap-2">
          <span className={`text-xs px-1.5 py-0.5 rounded font-bold ${
            pos.direction === 'LONG' ? 'bg-green-900/60 text-green-300' : 'bg-red-900/60 text-red-300'
          }`}>{pos.direction}</span>
          <span className="font-semibold text-white">{pos.ticker}</span>
        </div>
      </td>
      <td className="py-2.5 px-3 text-right text-slate-300">€{pos.size_eur?.toFixed(0)}</td>
      <td className="py-2.5 px-3 text-right text-slate-400 text-xs">{pos.entry_price?.toFixed(3)}</td>
      <td className="py-2.5 px-3 text-right text-slate-300">{pos.current_price?.toFixed(3) || '–'}</td>
      <td className="py-2.5 px-3 text-right">
        <span className={`font-semibold ${pnlColor}`}>
          {pos.pnl_eur >= 0 ? '+' : ''}{pos.pnl_eur?.toFixed(2)}€
        </span>
        <span className={`text-xs ml-1 ${pnlColor}`}>({pos.pnl_pct?.toFixed(2)}%)</span>
      </td>
      <td className="py-2.5 px-3 text-right text-xs text-slate-500">
        {stopDist ? `${stopDist}% da stop` : '–'}
      </td>
      <td className="py-2.5 px-3">
        <span className={`text-xs px-1.5 py-0.5 rounded ${catBadge}`}>
          {pos.event_category?.split('_').slice(0, 2).join(' ')}
        </span>
      </td>
    </tr>
  )
}

// ── Signal Preview Card ───────────────────────────────────────────────────────
function SignalPreview({ signal, onClick }) {
  const conf = signal.confidence_composite ?? 0
  const confBg = conf >= 0.75 ? 'border-green-500/40' : conf >= 0.55 ? 'border-yellow-500/40' : 'border-slate-700'
  const catBadge = CATEGORY_COLORS[signal.event_category] || 'bg-slate-600 text-slate-300'

  return (
    <div
      onClick={onClick}
      className={`bg-slate-800 border ${confBg} rounded-xl p-3 cursor-pointer hover:border-sky-500/50 transition-all`}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium shrink-0 ${catBadge}`}>
          {signal.event_category?.replace(/_/g, ' ')}
        </span>
        <div className="text-right shrink-0">
          <span className={`text-sm font-bold ${
            conf >= 0.75 ? 'text-green-400' : conf >= 0.55 ? 'text-yellow-400' : 'text-slate-400'
          }`}>{(conf * 100).toFixed(0)}%</span>
        </div>
      </div>
      <p className="text-sm text-slate-200 line-clamp-2 leading-snug mb-2">{signal.headline}</p>
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>Size: <span className="text-sky-400 font-medium">€{signal.position_size_eur?.toFixed(0) ?? '–'}</span></span>
        <span className={KELLY_COLOR[signal.kelly_quality] || 'text-slate-400'}>{signal.kelly_quality}</span>
        <span>{signal.entry_timing}</span>
      </div>
    </div>
  )
}

// ── Dashboard principale ──────────────────────────────────────────────────────
export default function Dashboard({ onSignalClick }) {
  const [portfolio, setPortfolio] = useState(null)
  const [signals, setSignals] = useState([])
  const [loading, setLoading] = useState(true)
  const [fetchingNews, setFetchingNews] = useState(false)
  const [updatingPrices, setUpdatingPrices] = useState(false)
  const [lastRefresh, setLastRefresh] = useState(null)
  const [toast, setToast] = useState(null)
  const [pipelineStatus, setPipelineStatus] = useState(null)
  const [fetchProgress, setFetchProgress] = useState(null) // {step, label, pct}
  const fetchTimerRef = useRef(null)

  const showToast = (msg, type = 'info') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3500)
  }

  const loadData = useCallback(async () => {
    try {
      const [portData, sigData] = await Promise.all([
        getPortfolio(),
        getLatestSignals().catch(() => ({ count: 0, signals: [] })),
      ])
      setPortfolio(portData)
      setSignals(sigData.signals || [])
      setLastRefresh(new Date().toLocaleTimeString('it-IT'))
    } catch (e) {
      showToast(`Errore caricamento: ${e.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
    const id = setInterval(loadData, 60_000)
    return () => clearInterval(id)
  }, [loadData])

  const handleFetchNews = async () => {
    setFetchingNews(true)
    // Fasi animate: simulano il progresso mentre il backend lavora in background
    const phases = [
      { pct: 8,  label: '📡 Connessione ai feed RSS...' },
      { pct: 25, label: '📰 Download notizie (FT, BBC, ANSA, MarketWatch...)' },
      { pct: 50, label: '🔍 Deduplicazione e normalizzazione news' },
      { pct: 68, label: '🧠 Classificazione con Claude AI in corso...' },
      { pct: 82, label: '🧠 Claude valuta materialità e categoria evento' },
      { pct: 92, label: '💾 Salvataggio nel database locale' },
    ]
    let phaseIdx = 0
    setFetchProgress({ pct: 5, label: '🔄 Avvio fetch news...' })

    const advance = () => {
      if (phaseIdx < phases.length) {
        setFetchProgress(phases[phaseIdx])
        phaseIdx++
        // Intervallo crescente: le fasi AI richiedono più tempo
        const delay = phaseIdx <= 2 ? 1800 : phaseIdx <= 4 ? 4000 : 3000
        fetchTimerRef.current = setTimeout(advance, delay)
      }
    }
    fetchTimerRef.current = setTimeout(advance, 600)

    try {
      await fetchNews()
      clearTimeout(fetchTimerRef.current)
      setFetchProgress({ pct: 100, label: '✅ Fetch completato!' })
      setTimeout(() => setFetchProgress(null), 2500)
    } catch (e) {
      clearTimeout(fetchTimerRef.current)
      setFetchProgress({ pct: 100, label: '❌ Errore durante il fetch', error: true })
      setTimeout(() => setFetchProgress(null), 3000)
      showToast(`Errore fetch: ${e.message}`, 'error')
    } finally {
      setFetchingNews(false)
    }
  }

  const handleUpdatePrices = async () => {
    setUpdatingPrices(true)
    try {
      await updatePrices()
      showToast('Aggiornamento prezzi avviato', 'success')
      setTimeout(loadData, 2000)
    } catch (e) {
      showToast(`Errore: ${e.message}`, 'error')
    } finally {
      setUpdatingPrices(false)
    }
  }

  const handleRunPipeline = async () => {
    setLoading(true)
    try {
      const result = await runSignals(30)
      setSignals(result.signals || [])
      // Calcola distribuzione filtri dai reject_summary
      const rejectByFilter = {}
      for (const r of (result.reject_summary || [])) {
        rejectByFilter[r.rejected_at] = (rejectByFilter[r.rejected_at] || 0) + 1
      }
      setPipelineStatus({
        timestamp: new Date().toLocaleTimeString('it-IT'),
        total: result.total_news_processed ?? 0,
        rejected: result.news_rejected ?? 0,
        signals: result.signals_generated ?? 0,
        rejectByFilter,
        topRejects: (result.reject_summary || []).slice(0, 3),
      })
      if (result.signals_generated > 0) {
        showToast(`✅ ${result.signals_generated} segnali generati!`, 'success')
      }
    } catch (e) {
      showToast(`Errore pipeline: ${e.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  if (loading && !portfolio) return <LoadingSpinner label="Caricamento dashboard..." />

  const nav = portfolio?.total_nav ?? 0
  const returnPct = portfolio?.total_return_pct ?? 0
  const openPnl = portfolio?.open_pnl_eur ?? 0
  const realizedPnl = portfolio?.realized_pnl_eur ?? 0
  const openPositions = portfolio?.open_positions ?? []
  const numOpen = portfolio?.num_open_positions ?? 0
  const numClosed = portfolio?.num_closed_positions ?? 0

  return (
    <div className="p-4 md:p-6 max-w-7xl mx-auto">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg transition-all ${
          toast.type === 'success' ? 'bg-green-700 text-white' :
          toast.type === 'error'   ? 'bg-red-700 text-white' : 'bg-slate-700 text-white'
        }`}>{toast.msg}</div>
      )}

      {/* Fetch Progress Bar */}
      {fetchProgress && (
        <div className={`mb-4 rounded-xl border px-4 py-3 transition-all ${
          fetchProgress.error ? 'bg-red-900/20 border-red-600/40' :
          fetchProgress.pct === 100 ? 'bg-green-900/20 border-green-600/40' :
          'bg-slate-800 border-sky-600/40'
        }`}>
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm font-medium text-white">{fetchProgress.label}</p>
            <span className="text-xs text-slate-400 font-mono">{fetchProgress.pct}%</span>
          </div>
          <div className="w-full bg-slate-700 rounded-full h-1.5 overflow-hidden">
            <div
              className={`h-1.5 rounded-full transition-all duration-700 ease-out ${
                fetchProgress.error ? 'bg-red-500' :
                fetchProgress.pct === 100 ? 'bg-green-500' : 'bg-sky-500'
              }`}
              style={{ width: `${fetchProgress.pct}%` }}
            />
          </div>
        </div>
      )}

      {/* Header row */}
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          {lastRefresh && (
            <p className="text-xs text-slate-500 mt-0.5">Aggiornato {lastRefresh}</p>
          )}
        </div>
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={handleFetchNews}
            disabled={fetchingNews}
            className="px-3 py-1.5 text-sm bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-200 rounded-lg transition-colors"
          >
            {fetchingNews ? '⏳' : '📡'} Fetch news
          </button>
          <button
            onClick={handleRunPipeline}
            className="px-3 py-1.5 text-sm bg-sky-700 hover:bg-sky-600 text-white rounded-lg transition-colors"
          >
            ▶ Pipeline
          </button>
          <button
            onClick={handleUpdatePrices}
            disabled={updatingPrices}
            className="px-3 py-1.5 text-sm bg-slate-700 hover:bg-slate-600 disabled:opacity-50 text-slate-200 rounded-lg transition-colors"
          >
            {updatingPrices ? '⏳' : '🔄'} Prezzi
          </button>
          <button
            onClick={loadData}
            className="px-3 py-1.5 text-sm bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg transition-colors"
          >
            ↻
          </button>
        </div>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <StatCard
          label="NAV portafoglio"
          value={`€${nav.toFixed(0)}`}
          sub={`Iniziale: €10.000`}
          color={returnPct >= 0 ? 'text-green-400' : 'text-red-400'}
          trend={returnPct}
        />
        <StatCard
          label="P&L aperto"
          value={`${openPnl >= 0 ? '+' : ''}€${openPnl.toFixed(2)}`}
          sub={`${numOpen} posizioni aperte`}
          color={openPnl >= 0 ? 'text-green-300' : 'text-red-300'}
        />
        <StatCard
          label="P&L realizzato"
          value={`${realizedPnl >= 0 ? '+' : ''}€${realizedPnl.toFixed(2)}`}
          sub={`${numClosed} trade chiusi`}
          color={realizedPnl >= 0 ? 'text-emerald-300' : 'text-red-300'}
        />
        <StatCard
          label="Segnali attivi"
          value={signals.length}
          sub={`In pipeline cache`}
          color="text-sky-400"
        />
      </div>

      {/* Pipeline Status */}
      {pipelineStatus && (
        <div className={`mb-6 rounded-xl border px-4 py-3 flex flex-wrap items-start gap-4 ${
          pipelineStatus.signals > 0
            ? 'bg-green-900/20 border-green-600/40'
            : 'bg-slate-800 border-slate-700'
        }`}>
          <div className="flex items-center gap-2 shrink-0">
            <span className={`text-lg ${pipelineStatus.signals > 0 ? '🟢' : '🔵'}`}>
              {pipelineStatus.signals > 0 ? '🟢' : '🔵'}
            </span>
            <div>
              <p className="text-sm font-semibold text-white">
                {pipelineStatus.signals > 0
                  ? `${pipelineStatus.signals} segnale${pipelineStatus.signals > 1 ? 'i' : ''} generato${pipelineStatus.signals > 1 ? 'i' : ''}!`
                  : 'Pipeline OK — nessun segnale oggi'}
              </p>
              <p className="text-xs text-slate-400">
                {pipelineStatus.timestamp} · {pipelineStatus.total} news analizzate · {pipelineStatus.rejected} rigettate
              </p>
            </div>
          </div>
          {pipelineStatus.signals === 0 && pipelineStatus.total > 0 && (
            <div className="flex flex-wrap gap-2 text-xs">
              {Object.entries(pipelineStatus.rejectByFilter).map(([filter, count]) => {
                const labels = {
                  F1_MATERIALITY: { label: 'F1 Materialità', color: 'bg-slate-700 text-slate-300' },
                  F2_GEOPOLITICAL: { label: 'F2 Geopolitica', color: 'bg-blue-900/50 text-blue-300' },
                  F3_CROSS_ASSET: { label: 'F3 Cross-Asset', color: 'bg-yellow-900/50 text-yellow-300' },
                  F4_TIMING: { label: 'F4 Timing', color: 'bg-orange-900/50 text-orange-300' },
                  F5_CONVICTION: { label: 'F5 Conviction', color: 'bg-purple-900/50 text-purple-300' },
                }
                const { label, color } = labels[filter] || { label: filter, color: 'bg-slate-700 text-slate-300' }
                return (
                  <span key={filter} className={`px-2 py-0.5 rounded-full font-medium ${color}`}>
                    {label}: {count}
                  </span>
                )
              })}
            </div>
          )}
          {pipelineStatus.signals === 0 && (
            <p className="text-xs text-slate-500 w-full mt-0.5">
              Il sistema filtra solo eventi macro con conferma cross-asset — nessun segnale significa mercati già prezzati o notizie non rilevanti.
            </p>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* Posizioni aperte */}
        <div className="lg:col-span-2">
          <div className="bg-slate-800 border border-slate-700 rounded-xl">
            <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
              <h2 className="font-semibold text-white text-sm">Posizioni aperte</h2>
              <span className="text-xs text-slate-500">{numOpen} posizioni</span>
            </div>
            {openPositions.length === 0 ? (
              <EmptyState
                message="Nessuna posizione aperta"
                hint="Esegui un segnale in paper trading per aprire posizioni."
              />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-slate-500 uppercase">
                      <th className="py-2 px-3 text-left">Ticker</th>
                      <th className="py-2 px-3 text-right">Size</th>
                      <th className="py-2 px-3 text-right">Entry</th>
                      <th className="py-2 px-3 text-right">Attuale</th>
                      <th className="py-2 px-3 text-right">P&L</th>
                      <th className="py-2 px-3 text-right">Stop dist.</th>
                      <th className="py-2 px-3 text-left">Evento</th>
                    </tr>
                  </thead>
                  <tbody>
                    {openPositions.map(pos => (
                      <PositionRow key={pos.id} pos={pos} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* Segnali attivi */}
        <div>
          <div className="bg-slate-800 border border-slate-700 rounded-xl">
            <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
              <h2 className="font-semibold text-white text-sm">Segnali recenti</h2>
              <span className="text-xs text-slate-500">{signals.length} in cache</span>
            </div>
            <div className="p-3 flex flex-col gap-2">
              {signals.length === 0 ? (
                <EmptyState
                  message="Nessun segnale"
                  hint="Clicca ▶ Pipeline per processare le news."
                />
              ) : (
                signals.slice(0, 4).map((s, i) => (
                  <SignalPreview
                    key={i}
                    signal={s}
                    onClick={() => onSignalClick(s.index ?? i)}
                  />
                ))
              )}
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}
