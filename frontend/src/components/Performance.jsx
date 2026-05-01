import { useState, useEffect } from 'react'
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell, ReferenceLine, Legend,
} from 'recharts'
import { getPerformance } from '../api.js'
import { LoadingSpinner } from '../App.jsx'

// ── Metric Card ───────────────────────────────────────────────────────────────
function MetricCard({ label, value, sub, color = 'text-white', size = 'normal' }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
      <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">{label}</p>
      <p className={`font-bold ${color} ${size === 'large' ? 'text-3xl' : 'text-xl'}`}>{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
    </div>
  )
}

// ── Go-live checklist ─────────────────────────────────────────────────────────
function GoLiveChecklist({ checklist }) {
  if (!checklist) return null
  const criteria = checklist.criteria || {}
  const allMet = Object.values(criteria).every(c => c.met)

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-white text-sm">Go-Live Checklist</h3>
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
          allMet ? 'bg-green-700/40 text-green-300' : 'bg-slate-700 text-slate-400'
        }`}>{checklist.current_status}</span>
      </div>
      <div className="space-y-2">
        {Object.entries(criteria).map(([key, crit]) => {
          const progress = key === 'trades_count'
            ? Math.min((crit.value / crit.target) * 100, 100)
            : key === 'win_rate'
            ? Math.min((crit.value / crit.target) * 100, 100)
            : key === 'sharpe'
            ? Math.min((crit.value / crit.target) * 100, 100)
            : key === 'drawdown'
            ? Math.min(((crit.target - crit.value) / crit.target) * 100, 100)
            : 0

          const labels = {
            trades_count: 'Trade completati',
            win_rate: 'Win rate',
            sharpe: 'Sharpe ratio',
            drawdown: 'Max drawdown',
          }
          const formatVal = (k, v) => {
            if (k === 'win_rate') return `${(v * 100).toFixed(1)}%`
            if (k === 'sharpe') return v.toFixed(3)
            if (k === 'drawdown') return `${v.toFixed(1)}%`
            return v
          }
          const formatTarget = (k, t) => {
            if (k === 'win_rate') return `≥${(t * 100).toFixed(0)}%`
            if (k === 'sharpe') return `≥${t}`
            if (k === 'drawdown') return `≤${t}%`
            return `≥${t}`
          }

          return (
            <div key={key}>
              <div className="flex items-center justify-between text-xs mb-1">
                <span className="text-slate-400">{labels[key] || key}</span>
                <div className="flex items-center gap-2">
                  <span className={crit.met ? 'text-green-400' : 'text-slate-500'}>
                    {formatVal(key, crit.value)}
                  </span>
                  <span className="text-slate-600">/</span>
                  <span className="text-slate-500">{formatTarget(key, crit.target)}</span>
                  <span>{crit.met ? '✅' : '❌'}</span>
                </div>
              </div>
              <div className="w-full bg-slate-700 rounded-full h-1.5">
                <div
                  className={`h-1.5 rounded-full transition-all ${
                    crit.met ? 'bg-green-500' : 'bg-sky-600'
                  }`}
                  style={{ width: `${Math.max(progress, 2)}%` }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Equity curve chart ────────────────────────────────────────────────────────
function EquityCurve({ equityCurve, initialNav }) {
  if (!equityCurve?.length) return (
    <p className="text-slate-500 text-sm py-8 text-center">Nessun dato equity curve disponibile.</p>
  )

  // Aggiungi baseline
  const data = equityCurve.map(p => ({
    date: p.date?.slice(5) || '',  // MM-DD
    nav: parseFloat(p.nav?.toFixed(2)),
    baseline: initialNav,
  }))

  const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null
    const nav = payload[0]?.value
    const ret = nav ? (((nav - initialNav) / initialNav) * 100).toFixed(2) : 0
    return (
      <div className="bg-slate-900 border border-slate-600 rounded p-2 text-xs">
        <p className="text-slate-400">{label}</p>
        <p className="font-bold text-white">€{nav?.toFixed(2)}</p>
        <p className={Number(ret) >= 0 ? 'text-green-400' : 'text-red-400'}>{Number(ret) >= 0 ? '+' : ''}{ret}%</p>
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
        <XAxis dataKey="date" tick={{ fill: '#64748b', fontSize: 10 }} />
        <YAxis
          domain={['auto', 'auto']}
          tick={{ fill: '#64748b', fontSize: 10 }}
          tickFormatter={v => `€${v.toFixed(0)}`}
        />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine y={initialNav} stroke="#475569" strokeDasharray="4 2" strokeWidth={1} />
        <Line
          type="monotone"
          dataKey="nav"
          stroke="#0ea5e9"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, fill: '#0ea5e9' }}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

// ── P&L per categoria (bar chart) ─────────────────────────────────────────────
function PnlByCategory({ pnlByCategory }) {
  if (!pnlByCategory || !Object.keys(pnlByCategory).length) return (
    <p className="text-slate-500 text-sm py-8 text-center">Nessun dato disponibile.</p>
  )

  const data = Object.entries(pnlByCategory)
    .map(([cat, d]) => ({
      name: cat.replace(/_/g, ' ').slice(0, 20),
      pnl: parseFloat(d.pnl_eur?.toFixed(2)),
      trades: d.trades,
      win_rate: d.win_rate,
    }))
    .sort((a, b) => b.pnl - a.pnl)

  const CustomTooltip = ({ active, payload }) => {
    if (!active || !payload?.length) return null
    const d = payload[0].payload
    return (
      <div className="bg-slate-900 border border-slate-600 rounded p-2 text-xs">
        <p className="font-bold text-white">{d.name}</p>
        <p className={d.pnl >= 0 ? 'text-green-400' : 'text-red-400'}>P&L: {d.pnl >= 0 ? '+' : ''}€{d.pnl}</p>
        <p className="text-slate-400">Trade: {d.trades}</p>
        <p className="text-slate-400">Win rate: {(d.win_rate * 100).toFixed(0)}%</p>
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 20, left: 120, bottom: 0 }}>
        <XAxis type="number" tick={{ fill: '#64748b', fontSize: 10 }} tickFormatter={v => `€${v}`} />
        <YAxis type="category" dataKey="name" tick={{ fill: '#94a3b8', fontSize: 10 }} width={115} />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine x={0} stroke="#475569" />
        <Bar dataKey="pnl" radius={[0, 4, 4, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.pnl >= 0 ? '#22c55e' : '#ef4444'} opacity={0.8} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

// ── Performance principale ────────────────────────────────────────────────────
export default function Performance() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getPerformance()
      .then(setReport)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <LoadingSpinner label="Caricamento performance..." />

  if (error) return (
    <div className="p-6 text-center">
      <p className="text-red-400">Errore: {error}</p>
    </div>
  )

  if (report?.status === 'NO_DATA') return (
    <div className="p-6 max-w-2xl mx-auto text-center">
      <div className="text-5xl mb-4">📊</div>
      <h2 className="text-xl font-semibold text-white mb-2">Nessun dato ancora</h2>
      <p className="text-slate-400">{report.message}</p>
    </div>
  )

  const s = report?.summary || {}
  const r = report?.risk_metrics || {}
  const b = report?.benchmark || {}
  const initial = report?.portfolio_state ? 10000 : 10000

  return (
    <div className="p-4 md:p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-white">Performance</h1>
        <span className="text-xs text-slate-500">
          {b.period_start} → {b.period_end}
        </span>
      </div>

      {/* Key metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <MetricCard
          label="Total P&L"
          value={`${s.total_pnl_eur >= 0 ? '+' : ''}€${s.total_pnl_eur?.toFixed(2)}`}
          sub={`${s.total_return_pct >= 0 ? '+' : ''}${s.total_return_pct?.toFixed(2)}%`}
          color={s.total_pnl_eur >= 0 ? 'text-green-400' : 'text-red-400'}
          size="large"
        />
        <MetricCard
          label="Win rate"
          value={`${(s.win_rate * 100).toFixed(1)}%`}
          sub={`${s.wins}W / ${s.losses}L / ${s.breakevens}BE`}
          color={s.win_rate >= 0.52 ? 'text-green-400' : 'text-yellow-400'}
        />
        <MetricCard
          label="Sharpe simulato"
          value={r.sharpe_simulated?.toFixed(3)}
          sub="Annualizzato, RF 4.5%"
          color={r.sharpe_simulated >= 0.8 ? 'text-green-400' : r.sharpe_simulated >= 0.4 ? 'text-yellow-400' : 'text-red-400'}
        />
        <MetricCard
          label="Max drawdown"
          value={`${r.max_drawdown_pct?.toFixed(2)}%`}
          sub={`Profit factor: ${s.profit_factor}`}
          color={r.max_drawdown_pct <= 15 ? 'text-green-400' : 'text-red-400'}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">

        {/* Equity curve */}
        <div className="lg:col-span-2 bg-slate-800 border border-slate-700 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-white text-sm">Equity curve</h3>
            <div className="flex items-center gap-3 text-xs">
              <span className="text-sky-400">— Tool</span>
              {b.SPY_return_pct !== null && (
                <span>
                  Alpha: <span className={b.alpha_pct >= 0 ? 'text-green-400' : 'text-red-400'}>
                    {b.alpha_pct >= 0 ? '+' : ''}{b.alpha_pct?.toFixed(2)}%
                  </span>
                </span>
              )}
            </div>
          </div>
          <EquityCurve equityCurve={report?.equity_curve} initialNav={initial} />
        </div>

        {/* Go-live checklist */}
        <GoLiveChecklist checklist={report?.go_live_checklist} />

      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* P&L per categoria */}
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
          <h3 className="font-semibold text-white text-sm mb-3">P&L per categoria evento</h3>
          <PnlByCategory pnlByCategory={report?.pnl_by_category} />
        </div>

        {/* Stats dettagliate */}
        <div className="bg-slate-800 border border-slate-700 rounded-xl p-4">
          <h3 className="font-semibold text-white text-sm mb-3">Statistiche dettagliate</h3>
          <div className="space-y-2 text-sm">
            {[
              ['Totale trade', s.total_trades],
              ['Avg win', `+${s.avg_win_pct?.toFixed(2)}% (€${s.avg_win_eur?.toFixed(2)})`],
              ['Avg loss', `${s.avg_loss_pct?.toFixed(2)}% (€${s.avg_loss_eur?.toFixed(2)})`],
              ['Profit factor', s.profit_factor],
              ['Avg holding (tutti)', `${r.avg_holding_days?.toFixed(1)} giorni`],
              ['Avg holding (win)', `${r.avg_holding_wins?.toFixed(1)} giorni`],
              ['Avg holding (loss)', `${r.avg_holding_losses?.toFixed(1)} giorni`],
              ['Tool return', `${b.tool_return_pct >= 0 ? '+' : ''}${b.tool_return_pct?.toFixed(2)}%`],
              ['SPY return', b.SPY_return_pct !== null ? `${b.SPY_return_pct >= 0 ? '+' : ''}${b.SPY_return_pct?.toFixed(2)}%` : 'N/D'],
              ['Alpha', b.alpha_pct !== null ? `${b.alpha_pct >= 0 ? '+' : ''}${b.alpha_pct?.toFixed(2)}%` : 'N/D'],
            ].map(([label, val]) => (
              <div key={label} className="flex items-center justify-between border-b border-slate-700/30 pb-1.5">
                <span className="text-slate-500">{label}</span>
                <span className="text-slate-200 font-medium">{val}</span>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  )
}
