import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine,
} from 'recharts'
import { getLatestSignals, executeSignal } from '../api.js'
import { CATEGORY_COLORS, KELLY_COLOR, LoadingSpinner } from '../App.jsx'

// ── Sezione collassabile ──────────────────────────────────────────────────────
function Section({ title, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-white hover:bg-slate-750 transition-colors"
      >
        {title}
        <span className="text-slate-500 text-xs">{open ? '▲' : '▼'}</span>
      </button>
      {open && <div className="px-4 pb-4">{children}</div>}
    </div>
  )
}

// ── Cross-asset bar chart ─────────────────────────────────────────────────────
function CrossAssetChart({ readings }) {
  if (!readings?.length) return <p className="text-slate-500 text-sm">Dati non disponibili</p>

  const data = readings.map(r => ({
    name: r.asset_key,
    zscore: parseFloat(r.zscore_1d?.toFixed(2) ?? 0),
    confirming: r.is_confirming,
    direction: r.actual_direction,
    change: parseFloat(r.pct_change_1d?.toFixed(2) ?? 0),
  }))

  const CustomTooltip = ({ active, payload }) => {
    if (!active || !payload?.length) return null
    const d = payload[0].payload
    return (
      <div className="bg-slate-900 border border-slate-600 rounded p-2 text-xs">
        <p className="font-bold text-white">{d.name}</p>
        <p className="text-slate-300">Z-score: <span className={d.zscore >= 0 ? 'text-green-400' : 'text-red-400'}>{d.zscore}</span></p>
        <p className="text-slate-300">Δ1d: {d.change >= 0 ? '+' : ''}{d.change}%</p>
        <p className={d.confirming ? 'text-green-400' : 'text-slate-500'}>
          {d.confirming ? '✅ Conferma' : '❌ Non conferma'}
        </p>
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
        <XAxis dataKey="name" tick={{ fill: '#94a3b8', fontSize: 11 }} />
        <YAxis tick={{ fill: '#64748b', fontSize: 10 }} />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine y={1.5} stroke="#f59e0b" strokeDasharray="4 2" strokeWidth={1} />
        <ReferenceLine y={-1.5} stroke="#f59e0b" strokeDasharray="4 2" strokeWidth={1} />
        <Bar dataKey="zscore" radius={[4, 4, 0, 0]}>
          {data.map((d, i) => (
            <Cell
              key={i}
              fill={d.confirming ? '#22c55e' : d.zscore > 0 ? '#64748b' : '#ef4444'}
              opacity={0.85}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

// ── Instruments table ─────────────────────────────────────────────────────────
function InstrumentsTable({ instruments }) {
  if (!instruments?.length) return <p className="text-slate-500 text-sm">Nessuno strumento proposto.</p>

  const totalWeight = instruments.reduce((s, i) => s + (i.weight_pct ?? 0), 0)

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-slate-500 uppercase border-b border-slate-700">
            <th className="py-2 text-left">Ticker</th>
            <th className="py-2 text-left">Tipo</th>
            <th className="py-2 text-center">Dir.</th>
            <th className="py-2 text-right">Peso</th>
            <th className="py-2 text-left pl-3">Rationale</th>
          </tr>
        </thead>
        <tbody>
          {instruments.map((inst, i) => (
            <tr key={i} className="border-b border-slate-700/30 hover:bg-slate-700/20">
              <td className="py-2.5 font-semibold text-white">{inst.ticker}</td>
              <td className="py-2.5 text-xs text-slate-400">
                {inst.instrument_type}
                {inst.option_strike_hint && <span className="ml-1 text-sky-400">({inst.option_strike_hint})</span>}
              </td>
              <td className="py-2.5 text-center">
                <span className={`text-xs px-1.5 py-0.5 rounded font-bold ${
                  inst.direction === 'LONG' ? 'bg-green-900/60 text-green-300' : 'bg-red-900/60 text-red-300'
                }`}>{inst.direction}</span>
              </td>
              <td className="py-2.5 text-right">
                <div className="flex items-center justify-end gap-2">
                  <div className="w-16 bg-slate-700 rounded-full h-1.5">
                    <div
                      className="h-1.5 rounded-full bg-sky-500"
                      style={{ width: `${(inst.weight_pct / totalWeight) * 100}%` }}
                    />
                  </div>
                  <span className="text-slate-300 text-xs w-8 text-right">{inst.weight_pct}%</span>
                </div>
              </td>
              <td className="py-2.5 pl-3 text-xs text-slate-400 max-w-xs truncate">{inst.rationale}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Position size calculator ──────────────────────────────────────────────────
function SizingCalculator({ sizing, tradeStructure }) {
  const [nav, setNav] = useState(sizing?.portfolio_nav ?? 10000)
  const [vix, setVix] = useState(sizing?.current_vix ?? 20)

  const size_pct = sizing?.position_size_pct ?? 0
  const kelly_pct = sizing?.half_kelly_pct ?? 0
  const vix_factor = vix > 40 ? 0.25 : vix > 30 ? 0.5 : 1.0
  const computed_size_eur = (nav * (size_pct / 100) * vix_factor).toFixed(2)
  const stop = tradeStructure?.stop_loss_pct ?? -7.5
  const max_loss = (computed_size_eur * Math.abs(stop) / 100).toFixed(2)

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div>
        <label className="block text-xs text-slate-500 mb-1">Portfolio NAV (€)</label>
        <input
          type="number"
          value={nav}
          onChange={e => setNav(Number(e.target.value))}
          className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white focus:border-sky-500 outline-none"
        />
      </div>
      <div>
        <label className="block text-xs text-slate-500 mb-1">VIX corrente</label>
        <input
          type="number"
          value={vix}
          onChange={e => setVix(Number(e.target.value))}
          className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-white focus:border-sky-500 outline-none"
        />
        {vix > 30 && (
          <p className="text-xs text-yellow-400 mt-0.5">
            ⚠️ VIX &gt; 30 — size ridotta {vix > 40 ? '75%' : '50%'}
          </p>
        )}
      </div>
      <div className="sm:col-span-2 grid grid-cols-3 gap-3">
        <div className="bg-slate-700/50 rounded-lg p-3 text-center">
          <p className="text-xs text-slate-500 mb-1">Half-Kelly</p>
          <p className="text-lg font-bold text-sky-400">{kelly_pct?.toFixed(1)}%</p>
        </div>
        <div className="bg-slate-700/50 rounded-lg p-3 text-center">
          <p className="text-xs text-slate-500 mb-1">Size finale</p>
          <p className="text-lg font-bold text-white">€{computed_size_eur}</p>
        </div>
        <div className="bg-slate-700/50 rounded-lg p-3 text-center">
          <p className="text-xs text-slate-500 mb-1">Max loss</p>
          <p className="text-lg font-bold text-red-400">-€{max_loss}</p>
        </div>
      </div>
      <div className="sm:col-span-2 text-xs text-slate-500">
        {sizing?.sizing_rationale}
      </div>
    </div>
  )
}

// ── SignalDetail principale ───────────────────────────────────────────────────
export default function SignalDetail({ index, onBack }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [executing, setExecuting] = useState(false)
  const [execResult, setExecResult] = useState(null)
  const [toast, setToast] = useState(null)

  const showToast = (msg, type = 'info') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 4000)
  }

  useEffect(() => {
    getLatestSignals()
      .then(res => {
        const signals = res.signals || []
        const item = signals.find(s => (s.index ?? signals.indexOf(s)) === index) || signals[index]
        setData(item || null)
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [index])

  const handleExecute = async () => {
    if (!window.confirm('Eseguire questo trade in paper trading?')) return
    setExecuting(true)
    try {
      const res = await executeSignal(index)
      setExecResult(res.result)
      showToast(`✅ Eseguito! ${res.result?.positions_opened?.length ?? 0} posizioni aperte.`, 'success')
    } catch (e) {
      showToast(`Errore esecuzione: ${e.message}`, 'error')
    } finally {
      setExecuting(false)
    }
  }

  if (loading) return <LoadingSpinner label="Caricamento segnale..." />
  if (!data) return (
    <div className="p-6 text-center">
      <p className="text-slate-400">Segnale non trovato.</p>
      <button onClick={onBack} className="mt-4 text-sky-400 hover:text-sky-300 text-sm">← Torna indietro</button>
    </div>
  )

  const { signal, trade_structure: ts, sizing } = data
  const conf = signal?.confidence_composite ?? 0
  const crossResult = signal?.cross_asset_result ?? {}
  const catBadge = CATEGORY_COLORS[signal?.event_category] || 'bg-slate-600 text-slate-300'

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg ${
          toast.type === 'success' ? 'bg-green-700 text-white' : 'bg-red-700 text-white'
        }`}>{toast.msg}</div>
      )}

      {/* Back + header */}
      <div className="flex items-start justify-between gap-4 mb-5 flex-wrap">
        <div>
          <button
            onClick={onBack}
            className="flex items-center gap-1 text-slate-400 hover:text-white text-sm mb-2 transition-colors"
          >
            ← Segnali
          </button>
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${catBadge}`}>
              {signal?.event_category?.replace(/_/g, ' ')}
            </span>
            <span className="text-xs text-slate-500">{signal?.entry_timing}</span>
            <span className={`text-xs font-semibold ${
              conf >= 0.75 ? 'text-green-400' : conf >= 0.55 ? 'text-yellow-400' : 'text-red-400'
            }`}>Confidence: {(conf * 100).toFixed(0)}%</span>
          </div>
          <h2 className="text-lg font-bold text-white mt-1 leading-snug">{signal?.headline}</h2>
        </div>
        <button
          onClick={handleExecute}
          disabled={executing || !!execResult}
          className={`px-5 py-2.5 rounded-xl font-semibold text-sm transition-all flex items-center gap-2 shrink-0 ${
            execResult
              ? 'bg-green-700/40 text-green-300 border border-green-600 cursor-default'
              : 'bg-sky-600 hover:bg-sky-500 text-white shadow-lg hover:shadow-sky-500/20 disabled:opacity-50'
          }`}
        >
          {executing ? '⏳ Esecuzione...' : execResult ? '✅ Eseguito' : '▶ Esegui in paper'}
        </button>
      </div>

      {/* Esito esecuzione */}
      {execResult && (
        <div className="bg-green-900/30 border border-green-700 rounded-xl p-4 mb-4 text-sm">
          <p className="font-semibold text-green-300 mb-2">✅ Trade eseguito in paper</p>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <span className="text-slate-400">Posizioni aperte:</span>
            <span className="text-white">{execResult.positions_opened?.length ?? 0}</span>
            <span className="text-slate-400">Capitale deployato:</span>
            <span className="text-sky-400 font-semibold">€{execResult.total_capital_deployed_eur?.toFixed(2)}</span>
          </div>
          {execResult.positions_opened?.map((p, i) => (
            <div key={i} className="mt-1 text-xs text-slate-300">
              {p.direction} {p.ticker}: €{p.size_eur?.toFixed(0)} @ {p.entry_price?.toFixed(3)}
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-4">
        {/* Catena causale */}
        <Section title="🔗 Catena causale">
          <div className="bg-slate-900/50 rounded-lg p-3 text-sm text-slate-300 leading-relaxed border-l-2 border-sky-500">
            {signal?.causal_chain || 'Non disponibile.'}
          </div>
          {ts?.primary_thesis && (
            <div className="mt-3 text-sm text-slate-300">
              <span className="text-slate-500 text-xs uppercase tracking-wide">Thesis: </span>
              {ts.primary_thesis}
            </div>
          )}
          {ts?.bond_safe_haven_warning && (
            <div className="mt-2 bg-yellow-900/30 border border-yellow-700/50 rounded p-2 text-xs text-yellow-300">
              ⚠️ <strong>Bond safe-haven warning:</strong> regime inflazionistico rilevato — i bond potrebbero NON essere safe haven (lezione Ukraine 2022).
            </div>
          )}
        </Section>

        {/* Cross-asset */}
        <Section title={`📊 Cross-asset confirmation (${crossResult.confirmation_score ?? 0}/5)`}>
          <div className="flex items-center gap-3 mb-3 flex-wrap">
            <span className={`text-sm font-bold ${
              crossResult.passes_filter ? 'text-green-400' : 'text-red-400'
            }`}>
              {crossResult.passes_filter ? '✅ Filtro passato' : '❌ Filtro non passato'}
            </span>
            {crossResult.macro_regime_hint && (
              <span className="text-xs text-slate-400 bg-slate-700 px-2 py-0.5 rounded">
                {crossResult.macro_regime_hint}
              </span>
            )}
          </div>
          <CrossAssetChart readings={crossResult.asset_readings} />
          <p className="text-xs text-slate-500 mt-2">
            Barre verdi = asset che confermano (z-score ≥ 1.5σ in direzione attesa).
            Linee gialle = soglia ±1.5σ.
          </p>
        </Section>

        {/* Trade structure */}
        <Section title={`🎯 Trade structure (${ts?.trade_type ?? '–'})`}>
          {ts?.trade_type === 'NO_TRADE' ? (
            <p className="text-red-400 text-sm">❌ NO_TRADE: {ts.no_trade_reason}</p>
          ) : (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                <div className="text-center bg-slate-700/40 rounded-lg p-2">
                  <p className="text-xs text-slate-500">Entry</p>
                  <p className="text-sm font-bold text-white">{ts?.entry_timing ?? '–'}</p>
                </div>
                <div className="text-center bg-slate-700/40 rounded-lg p-2">
                  <p className="text-xs text-slate-500">Timeframe</p>
                  <p className="text-sm font-bold text-white">{ts?.timeframe_days ?? '–'}d</p>
                </div>
                <div className="text-center bg-red-900/30 rounded-lg p-2">
                  <p className="text-xs text-slate-500">Stop loss</p>
                  <p className="text-sm font-bold text-red-400">{ts?.stop_loss_pct}%</p>
                </div>
                <div className="text-center bg-green-900/30 rounded-lg p-2">
                  <p className="text-xs text-slate-500">Target</p>
                  <p className="text-sm font-bold text-green-400">+{ts?.target_pct}%</p>
                </div>
              </div>
              <div className="flex gap-3 mb-4 flex-wrap text-sm">
                <span className="text-slate-400">R/R: <span className="text-white font-semibold">{ts?.risk_reward_ratio?.toFixed(1)}x</span></span>
                <span className="text-slate-400">Conviction: <span className={ts?.conviction_pct >= 70 ? 'text-green-400' : 'text-yellow-400'} >{ts?.conviction_pct}%</span></span>
              </div>
              <InstrumentsTable instruments={ts?.instruments} />
              {ts?.alternative_scenario && (
                <div className="mt-3 text-xs text-slate-400 bg-slate-700/30 rounded p-2">
                  <span className="text-slate-500">Alt scenario: </span>{ts.alternative_scenario}
                </div>
              )}
              {ts?.hedge_suggestion && (
                <div className="mt-2 text-xs text-slate-400 bg-slate-700/30 rounded p-2">
                  <span className="text-slate-500">Hedge: </span>{ts.hedge_suggestion}
                </div>
              )}
            </>
          )}
        </Section>

        {/* Position sizing */}
        <Section title="⚖️ Position sizing (half-Kelly)">
          <div className="flex items-center gap-3 mb-3 flex-wrap">
            <span className={`text-xs px-2 py-0.5 rounded font-bold ${
              KELLY_COLOR[sizing?.kelly_quality] || 'text-slate-400'
            }`}>{sizing?.kelly_quality}</span>
            <span className="text-xs text-slate-400">
              WR storico: {((sizing?.historical_win_rate ?? 0) * 100).toFixed(0)}%
            </span>
            <span className="text-xs text-slate-400">
              R ratio: {sizing?.historical_R_ratio?.toFixed(1)}x
            </span>
          </div>
          <SizingCalculator sizing={sizing} tradeStructure={ts} />
        </Section>

        {/* Note operative */}
        {ts?.position_notes && (
          <Section title="📝 Note operative" defaultOpen={false}>
            <p className="text-sm text-slate-300 leading-relaxed">{ts.position_notes}</p>
          </Section>
        )}
      </div>
    </div>
  )
}
