/**
 * ObservabilityTab.tsx — Platform observability dashboard.
 * Matches monolith admin: step breakdown cards, p50/p95 latency bars,
 * cost distribution, error list, paginated call log table.
 */
import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { observabilityApi } from '@/lib/api'
import { Button, Card, Badge, Skeleton, MonoLabel, EmptyState } from '@/components/ui'

const STEP_CFG: Record<string, { color: string; bg: string; label: string }> = {
  LLM_question:   { color: '#2563EB', bg: '#EFF6FF', label: 'LLM question' },
  LLM_evaluation: { color: '#7C3AED', bg: '#F5F3FF', label: 'LLM eval' },
  STT:            { color: '#16A34A', bg: '#F0FDF4', label: 'STT' },
  TTS:            { color: '#D97706', bg: '#FFFBEB', label: 'TTS' },
  resume_parsing: { color: '#EA580C', bg: '#FFF7ED', label: 'Resume parsing' },
}
const STEPS = Object.keys(STEP_CFG)
const fms = (v: number) => v >= 1000 ? (v / 1000).toFixed(1) + 's' : v + 'ms'
const fc = (v?: number) => v && v > 0 ? '$' + v.toFixed(5) : '—'
const PAGE_SIZE = 25

export default function ObservabilityTab() {
  const [window_, setWindow] = useState(86400)
  const [logFilter, setLogFilter] = useState<{ step?: string; status?: string; session_id?: string }>({})
  const [page, setPage] = useState(0)

  const { data: summary, isLoading, refetch } = useQuery({
    queryKey: ['obs-summary', window_],
    queryFn: () => observabilityApi.summary(window_).then(r => r.data),
    staleTime: 15_000, refetchInterval: 15_000,
  })

  const { data: logsData } = useQuery({
    queryKey: ['obs-logs', logFilter],
    queryFn: () => observabilityApi.logs({ ...logFilter, limit: 500 }).then(r => r.data),
    staleTime: 10_000, refetchInterval: 10_000,
  })

  const { data: health } = useQuery({
    queryKey: ['obs-health'],
    queryFn: () => observabilityApi.deepHealth().then(r => r.data),
    staleTime: 30_000, refetchInterval: 60_000,
  })

  const by = summary?.by_step || {}
  const totalCost = Math.max(STEPS.reduce((a, k) => a + (by[k]?.cost_usd || 0), 0), 0.000001)
  const totalCalls = Math.max(STEPS.reduce((a, k) => a + (by[k]?.total_calls || 0), 0), 1)
  const maxP95 = Math.max(...STEPS.map(k => by[k]?.latency?.p95 || 0), 1)

  const allLogs = logsData?.logs || []
  const filtered = allLogs
  const pageCount = Math.ceil(filtered.length / PAGE_SIZE) || 1
  const pageLogs = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  return (
    <div>
      {/* Health banner */}
      {health && (
        <div style={{
          display: 'flex', gap: 14, marginBottom: 18, padding: '10px 16px',
          background: health.all_ok ? 'rgba(34,197,94,0.06)' : 'rgba(239,68,68,0.06)',
          border: `1px solid ${health.all_ok ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)'}`,
          borderRadius: 10, alignItems: 'center', fontSize: 12,
        }}>
          <span style={{ color: health.all_ok ? 'var(--green, #22c55e)' : 'var(--red, #ef4444)', fontWeight: 600 }}>
            {health.all_ok ? '● HEALTHY' : '● DEGRADED'}
          </span>
          {health.checks && Object.entries(health.checks).map(([name, check]: [string, any]) => (
            <span key={name} style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: check.ok ? 'var(--text-2)' : 'var(--red, #ef4444)' }}>
              {name}: {check.ok ? `${check.latency_ms}ms` : 'DOWN'}
            </span>
          ))}
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-3)', marginLeft: 'auto' }}>
            {health.active_sessions} active
          </span>
        </div>
      )}

      {/* Controls */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <select value={window_} onChange={e => setWindow(Number(e.target.value))} style={selStyle}>
            <option value={3600}>Last 1h</option>
            <option value={21600}>Last 6h</option>
            <option value={86400}>Last 24h</option>
            <option value={259200}>Last 3 days</option>
          </select>
          <Button variant="secondary" size="sm" onClick={() => refetch()}>Refresh</Button>
        </div>
      </div>

      {isLoading ? <LoadSkel /> : (
        <>
          {/* Stat cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginBottom: 20 }}>
            <StatCard label="Total API calls" value={summary?.total_calls ?? 0} color="var(--blue, #3b82f6)"
              sub={`${STEPS.reduce((a, k) => a + (by[k]?.success || 0), 0)} success · ${STEPS.reduce((a, k) => a + (by[k]?.failures || 0), 0)} failed`} />
            <StatCard label="Avg latency" value={fms(STEPS.reduce((a, k) => a + (by[k]?.latency?.avg || 0), 0) / Math.max(STEPS.filter(k => by[k]?.latency?.avg).length, 1))} />
            <StatCard label="API spend" value={`$${totalCost.toFixed(4)}`} sub="LLM + STT + TTS" />
            <StatCard label="Success rate"
              value={`${(STEPS.reduce((a, k) => a + (by[k]?.success_rate || 0), 0) / Math.max(STEPS.filter(k => by[k]).length, 1) * 100).toFixed(1)}%`}
              color={STEPS.some(k => (by[k]?.success_rate || 1) < 0.97) ? 'var(--yellow, #eab308)' : 'var(--green, #22c55e)'} />
          </div>

          {/* Step breakdown cards */}
          <MonoLabel style={{ display: 'block', marginBottom: 10 }}>Step breakdown</MonoLabel>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10, marginBottom: 24 }}>
            {STEPS.map(k => {
              const s = by[k] || {}; const cfg = STEP_CFG[k]
              const lat = s.latency || {}
              return (
                <Card key={k} style={{ padding: '12px 14px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: cfg.color, flexShrink: 0 }} />
                    <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-0)' }}>{cfg.label}</span>
                  </div>
                  <StepMetric label="Avg" value={fms(lat.avg || 0)} color={cfg.color} />
                  <StepMetric label="p95" value={fms(lat.p95 || 0)} />
                  <StepMetric label="Calls" value={`${s.total_calls || 0} (${Math.round((s.total_calls || 0) / totalCalls * 100)}%)`} />
                  <StepMetric label="Failures" value={`${s.failures || 0}`} color={(s.failures || 0) > 0 ? 'var(--red, #ef4444)' : 'var(--green, #22c55e)'} />
                  <StepMetric label="Cost" value={fc(s.cost_usd)} />
                </Card>
              )
            })}
          </div>

          {/* Latency + Cost side by side */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24 }}>
            {/* Latency bars */}
            <Card>
              <MonoLabel style={{ display: 'block', marginBottom: 12 }}>Latency by step — p50 / p95</MonoLabel>
              {STEPS.map(k => {
                const s = by[k] || {}; const cfg = STEP_CFG[k]; const lat = s.latency || {}
                const p50pct = ((lat.p50 || 0) / maxP95 * 100).toFixed(1)
                const p95pct = ((lat.p95 || 0) / maxP95 * 100).toFixed(1)
                return (
                  <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 11 }}>
                    <div style={{ fontSize: 11, width: 110, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 7 }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: cfg.color, flexShrink: 0 }} />
                      {cfg.label}
                    </div>
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 3 }}>
                      <LatBar tag="p50" pct={p50pct} color={cfg.color + '88'} val={fms(lat.p50 || 0)} />
                      <LatBar tag="p95" pct={p95pct} color={cfg.color} val={fms(lat.p95 || 0)} />
                    </div>
                  </div>
                )
              })}
            </Card>

            {/* Cost distribution */}
            <Card>
              <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Cost distribution by step</MonoLabel>
              <div style={{ height: 22, borderRadius: 5, overflow: 'hidden', display: 'flex', marginBottom: 10 }}>
                {STEPS.map(k => {
                  const pct = Math.round((by[k]?.cost_usd || 0) / totalCost * 100) || 0
                  return <div key={k} style={{ flex: Math.max(pct, 1), background: STEP_CFG[k].color }} title={`${STEP_CFG[k].label}: ${fc(by[k]?.cost_usd)}`} />
                })}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
                {STEPS.filter(k => (by[k]?.cost_usd || 0) > 0).map(k => (
                  <span key={k} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--text-2)' }}>
                    <span style={{ width: 7, height: 7, borderRadius: '50%', background: STEP_CFG[k].color }} />
                    {STEP_CFG[k].label} — {fc(by[k]?.cost_usd)} ({((by[k]?.cost_usd || 0) / totalCost * 100).toFixed(1)}%)
                  </span>
                ))}
              </div>
              <div style={{ borderTop: '1px solid var(--border-0)', paddingTop: 12 }}>
                <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Call volume by step</MonoLabel>
                {STEPS.map(k => {
                  const pct = ((by[k]?.total_calls || 0) / totalCalls * 100).toFixed(1)
                  return (
                    <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 7 }}>
                      <div style={{ fontSize: 11, width: 110, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 7 }}>
                        <span style={{ width: 8, height: 8, borderRadius: '50%', background: STEP_CFG[k].color }} />
                        {STEP_CFG[k].label}
                      </div>
                      <div style={{ flex: 1, height: 4, background: 'var(--bg-2, #f4f4f5)', borderRadius: 2, overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${pct}%`, background: STEP_CFG[k].color, borderRadius: 2 }} />
                      </div>
                      <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-2)', width: 28, textAlign: 'right' as const }}>{by[k]?.total_calls || 0}</span>
                    </div>
                  )
                })}
              </div>
            </Card>
          </div>

          {/* Recent errors */}
          <MonoLabel style={{ display: 'block', marginBottom: 10 }}>Recent errors</MonoLabel>
          <div style={{ marginBottom: 18 }}>
            {(() => {
              const errs = STEPS.flatMap(k => {
                const s = by[k] || {}
                return (s.failures || 0) > 0 ? [{ step: k, failures: s.failures }] : []
              })
              return errs.length === 0 ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '11px 13px', fontSize: 12, color: 'var(--green, #22c55e)', background: 'rgba(34,197,94,0.06)', border: '1px solid rgba(34,197,94,0.2)', borderRadius: 8 }}>
                  <span>✓</span> No errors in selected window
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                  {errs.map(e => (
                    <div key={e.step} style={{ display: 'flex', gap: 9, alignItems: 'center', padding: '10px 12px', background: 'rgba(239,68,68,0.04)', border: '1px solid rgba(239,68,68,0.15)', borderRadius: 8 }}>
                      <span style={{ width: 24, height: 24, borderRadius: '50%', background: 'var(--red, #ef4444)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: '#fff', fontWeight: 700, flexShrink: 0 }}>!</span>
                      <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--red, #ef4444)' }}>{STEP_CFG[e.step]?.label} — {e.failures} failure(s)</span>
                    </div>
                  ))}
                </div>
              )
            })()}
          </div>
        </>
      )}

      {/* Raw call log */}
      <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Raw call log</MonoLabel>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <input type="text" placeholder="Filter by session ID…" value={logFilter.session_id || ''}
          onChange={e => { setLogFilter(f => ({ ...f, session_id: e.target.value || undefined })); setPage(0) }}
          style={{ flex: 1, minWidth: 140, ...selStyle }} />
        <select value={logFilter.step || ''} onChange={e => { setLogFilter(f => ({ ...f, step: e.target.value || undefined })); setPage(0) }} style={selStyle}>
          <option value="">All steps</option>
          {STEPS.map(k => <option key={k} value={k}>{STEP_CFG[k].label}</option>)}
        </select>
        <select value={logFilter.status || ''} onChange={e => { setLogFilter(f => ({ ...f, status: e.target.value || undefined })); setPage(0) }} style={selStyle}>
          <option value="">All status</option>
          <option value="success">Success only</option>
          <option value="failure">Failures only</option>
        </select>
      </div>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr style={{ background: 'var(--bg-1)', borderBottom: '1px solid var(--border-0)' }}>
              {['Time', 'Session', 'Step', 'Model', 'Latency', 'Tokens', 'Cost', 'Status'].map(h => (
                <th key={h} style={{ padding: '8px 12px', textAlign: 'left' as const, fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase' as const, color: 'var(--text-3)', fontWeight: 400 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageLogs.length === 0 ? (
              <tr><td colSpan={8} style={{ padding: 20, textAlign: 'center' as const, color: 'var(--text-3)', fontSize: 12 }}>No logs yet. Run an interview to generate data.</td></tr>
            ) : pageLogs.map((l: any, i: number) => {
              const cfg = STEP_CFG[l.step] || { color: '#888', bg: '#f4f4f2', label: l.step }
              return (
                <tr key={i} style={{ borderBottom: '1px solid var(--border-0)' }}>
                  <td style={{ padding: '7px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>{l.formatted_time || '—'}</td>
                  <td style={{ padding: '7px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>{(l.session_id || '—').slice(0, 8)}</td>
                  <td style={{ padding: '7px 12px' }}>
                    <span style={{ padding: '2px 8px', borderRadius: 4, background: cfg.bg, color: cfg.color, fontSize: 10, fontWeight: 500 }}>{cfg.label}</span>
                  </td>
                  <td style={{ padding: '7px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-3)', fontSize: 10 }}>{l.model || '—'}</td>
                  <td style={{ padding: '7px 12px' }}>
                    {l.latency_ms ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <div style={{ flex: 1, height: 3, background: 'var(--bg-2, #f4f4f5)', borderRadius: 2, overflow: 'hidden', maxWidth: 60 }}>
                          <div style={{ height: '100%', width: `${Math.min(100, l.latency_ms / 20)}%`, background: cfg.color, borderRadius: 2 }} />
                        </div>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10 }}>{fms(l.latency_ms)}</span>
                      </div>
                    ) : <span style={{ color: 'var(--text-4)' }}>—</span>}
                  </td>
                  <td style={{ padding: '7px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>{l.total_tokens || '—'}</td>
                  <td style={{ padding: '7px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>{fc(l.cost_usd)}</td>
                  <td style={{ padding: '7px 12px' }}>
                    {l.status === 'success'
                      ? <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: 'var(--green, #22c55e)' }}><span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--green, #22c55e)' }} />OK</span>
                      : <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: 'var(--red, #ef4444)' }}><span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--red, #ef4444)' }} />Fail</span>
                    }
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {/* Pagination */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12, padding: '10px 16px', borderTop: '1px solid var(--border-0)' }}>
          <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}
            style={{ ...pgBtn, opacity: page === 0 ? 0.4 : 1 }}>← Prev</button>
          <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>
            {filtered.length ? `${page * PAGE_SIZE + 1}–${Math.min((page + 1) * PAGE_SIZE, filtered.length)} of ${filtered.length}` : '0 logs'}
          </span>
          <button onClick={() => setPage(p => Math.min(pageCount - 1, p + 1))} disabled={page >= pageCount - 1}
            style={{ ...pgBtn, opacity: page >= pageCount - 1 ? 0.4 : 1 }}>Next →</button>
        </div>
      </Card>
    </div>
  )
}

// ── Sub-components ──────────────────────────────────────────────────────────

function StatCard({ label, value, sub, color }: { label: string; value: any; sub?: string; color?: string }) {
  return (
    <Card style={{ padding: '14px 18px' }}>
      <p style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase' as const, color: 'var(--text-3)', marginBottom: 8 }}>{label}</p>
      <p style={{ fontFamily: 'var(--font-mono)', fontSize: 24, color: color || 'var(--text-0)', lineHeight: 1 }}>{value}</p>
      {sub && <p style={{ fontSize: 10, color: 'var(--text-4)', marginTop: 4 }}>{sub}</p>}
    </Card>
  )
}

function StepMetric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
      <span style={{ fontSize: 10, color: 'var(--text-3)' }}>{label}</span>
      <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', fontWeight: 500, color: color || 'var(--text-1)' }}>{value}</span>
    </div>
  )
}

function LatBar({ tag, pct, color, val }: { tag: string; pct: string; color: string; val: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <span style={{ fontSize: 9, color: 'var(--text-4)', width: 20, flexShrink: 0 }}>{tag}</span>
      <div style={{ flex: 1, height: 5, background: 'var(--bg-2, #f4f4f5)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.4s' }} />
      </div>
      <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-2)', width: 48, textAlign: 'right' as const }}>{val}</span>
    </div>
  )
}

function LoadSkel() {
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginBottom: 24 }}>
        {[1, 2, 3, 4].map(i => <Skeleton key={i} h={80} style={{ borderRadius: 12 }} />)}
      </div>
      <Skeleton h={200} style={{ borderRadius: 12 }} />
    </div>
  )
}

const selStyle: React.CSSProperties = {
  padding: '6px 9px', border: '1px solid var(--border-2, #e5e7eb)', borderRadius: 7,
  background: 'var(--bg-0, #fff)', fontSize: 12, fontFamily: 'inherit', outline: 'none',
}

const pgBtn: React.CSSProperties = {
  padding: '5px 12px', border: '1px solid var(--border-2, #e5e7eb)', borderRadius: 6,
  background: 'var(--bg-0, #fff)', fontSize: 11, cursor: 'pointer', fontFamily: 'inherit',
}
