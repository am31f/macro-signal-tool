import { useState, useEffect } from 'react'
import { getJournal } from '../api.js'
import { LoadingSpinner, EmptyState } from '../App.jsx'

// ── Verdict badge ─────────────────────────────────────────────────────────────
function VerdictBadge({ verdict }) {
  const styles = {
    WIN:       'bg-green-900/60 text-green-300 border border-green-700/50',
    LOSS:      'bg-red-900/60 text-red-300 border border-red-700/50',
    BREAKEVEN: 'bg-slate-700 text-slate-300 border border-slate-600',
  }
  const icons = { WIN: '✅', LOSS: '❌', BREAKEVEN: '➖' }
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${styles[verdict] || styles.BREAKEVEN}`}>
      {icons[verdict] || ''} {verdict}
    </span>
  )
}

// ── Close reason badge ────────────────────────────────────────────────────────
function ReasonBadge({ reason }) {
  const styles = {
    target_hit: 'bg-green-800/40 text-green-400',
    stop_hit:   'bg-red-800/40 text-red-400',
    manual:     'bg-slate-700 text-slate-400',
    expired:    'bg-amber-800/40 text-amber-400',
  }
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${styles[reason] || styles.manual}`}>
      {reason?.replace('_', ' ')}
    </span>
  )
}

// ── Journal Entry Card ────────────────────────────────────────────────────────
function JournalCard({ entry }) {
  const [expanded, setExpanded] = useState(false)
  const pnlPos = entry.pnl_eur >= 0

  return (
    <div className={`bg-slate-800 border rounded-xl overflow-hidden transition-all ${
      pnlPos ? 'border-slate-700 hover:border-green-700/40' : 'border-slate-700 hover:border-red-700/40'
    }`}>
      {/* Header */}
      <div
        className="p-4 cursor-pointer"
        onClick={() => setExpanded(e => !e)}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1.5 flex-wrap">
              <VerdictBadge verdict={entry.verdict} />
              <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                entry.direction === 'LONG'
                  ? 'bg-green-900/60 text-green-300'
                  : 'bg-red-900/60 text-red-300'
              }`}>{entry.direction}</span>
              <span className="text-sm font-bold text-white">{entry.ticker}</span>
              <ReasonBadge reason={entry.close_reason} />
              <span className="text-xs text-slate-500">{entry.holding_days?.toFixed(1)}d</span>
            </div>
            <p className="text-xs text-slate-400 line-clamp-1">{entry.event_category?.replace(/_/g, ' ')}</p>
          </div>
          <div className="text-right shrink-0">
            <p className={`text-lg font-bold ${pnlPos ? 'text-green-400' : 'text-red-400'}`}>
              {pnlPos ? '+' : ''}€{entry.pnl_eur?.toFixed(2)}
            </p>
            <p className={`text-xs ${pnlPos ? 'text-green-500' : 'text-red-500'}`}>
              {pnlPos ? '+' : ''}{entry.pnl_pct?.toFixed(2)}%
            </p>
          </div>
        </div>

        {/* Trade prices inline */}
        <div className="flex items-center gap-4 mt-2 text-xs text-slate-500">
          <span>Entry: <span className="text-slate-400">{entry.entry_price?.toFixed(4)}</span></span>
          <span>→</span>
          <span>Close: <span className="text-slate-400">{entry.close_price?.toFixed(4)}</span></span>
          <span className="ml-auto text-slate-600">{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-slate-700/50 pt-3 space-y-3">
          {entry.what_happened && (
            <div>
              <p className="text-xs text-slate-500 uppercase tracking-wide mb-1">Cosa è successo</p>
              <p className="text-sm text-slate-300 leading-relaxed">{entry.what_happened}</p>
            </div>
          )}
          {entry.lesson_learned && (
            <div className="bg-sky-900/20 border border-sky-700/30 rounded-lg p-3">
              <p className="text-xs text-sky-400 uppercase tracking-wide mb-1">💡 Lesson learned</p>
              <p className="text-sm text-slate-200 leading-relaxed">{entry.lesson_learned}</p>
            </div>
          )}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <span className="text-slate-500">Entry date: </span>
              <span className="text-slate-300">{entry.entry_date?.slice(0, 10)}</span>
            </div>
            <div>
              <span className="text-slate-500">Close date: </span>
              <span className="text-slate-300">{entry.close_date?.slice(0, 10)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Journal stats bar ─────────────────────────────────────────────────────────
function JournalStats({ entries }) {
  if (!entries.length) return null
  const wins = entries.filter(e => e.verdict === 'WIN').length
  const losses = entries.filter(e => e.verdict === 'LOSS').length
  const be = entries.filter(e => e.verdict === 'BREAKEVEN').length
  const totalPnl = entries.reduce((s, e) => s + (e.pnl_eur ?? 0), 0)
  const wr = ((wins / entries.length) * 100).toFixed(0)

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
      {[
        { label: 'Trade nel journal', value: entries.length, color: 'text-white' },
        { label: 'Win rate', value: `${wr}%`, color: wins / entries.length >= 0.52 ? 'text-green-400' : 'text-yellow-400' },
        { label: 'Win / Loss / BE', value: `${wins} / ${losses} / ${be}`, color: 'text-slate-300' },
        { label: 'P&L totale', value: `${totalPnl >= 0 ? '+' : ''}€${totalPnl.toFixed(2)}`, color: totalPnl >= 0 ? 'text-green-400' : 'text-red-400' },
      ].map(item => (
        <div key={item.label} className="bg-slate-800 border border-slate-700 rounded-xl p-3 text-center">
          <p className="text-xs text-slate-500 mb-1">{item.label}</p>
          <p className={`font-bold text-lg ${item.color}`}>{item.value}</p>
        </div>
      ))}
    </div>
  )
}

// ── Journal principale ────────────────────────────────────────────────────────
export default function Journal() {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('ALL') // ALL | WIN | LOSS

  useEffect(() => {
    getJournal(50)
      .then(data => setEntries((data.entries || data || []).reverse()))
      .catch(() => setEntries([]))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <LoadingSpinner label="Caricamento journal..." />

  const filtered = filter === 'ALL'
    ? entries
    : entries.filter(e => e.verdict === filter)

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-5 flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-white">Trade Journal</h1>
        <div className="flex gap-1">
          {['ALL', 'WIN', 'LOSS'].map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1 text-xs rounded-lg font-medium transition-colors ${
                filter === f
                  ? 'bg-sky-600 text-white'
                  : 'bg-slate-700 text-slate-400 hover:text-white'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      <JournalStats entries={entries} />

      {filtered.length === 0 ? (
        <EmptyState
          message="Nessuna entry nel journal"
          hint="Le entry vengono generate automaticamente quando si chiude una posizione paper."
        />
      ) : (
        <div className="space-y-3">
          {filtered.map((entry, i) => (
            <JournalCard key={entry.position_id ?? i} entry={entry} />
          ))}
        </div>
      )}
    </div>
  )
}
