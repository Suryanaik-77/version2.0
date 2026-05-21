import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis,
  Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { adminApi, observabilityApi, reviewerApi } from '@/lib/api'
import { Button, Card, Badge, Skeleton, MonoLabel, EmptyState, Divider } from '@/components/ui'
import { toast } from '@/hooks/useToast'
import { format } from 'date-fns'

type AdminTab = 'overview' | 'sessions' | 'latency' | 'cost' | 'prompts' | 'users' | 'events' | 'llm' | 'voice' | 'playground' | 'observability' | 'reviews'

export default function AdminPage() {
  const [tab, setTab] = useState<AdminTab>('overview')

  const TABS: { id: AdminTab; label: string }[] = [
    { id: 'overview',    label: 'Overview' },
    { id: 'sessions',    label: 'Sessions' },
    { id: 'llm',         label: 'LLM Config' },
    { id: 'voice',       label: 'Voice' },
    { id: 'playground',  label: 'Playground' },
    { id: 'latency',     label: 'Latency' },
    { id: 'cost',        label: 'Cost' },
    { id: 'prompts',     label: 'Prompts' },
    { id: 'users',       label: 'Users' },
    { id: 'events',      label: 'Events' },
    { id: 'observability', label: 'Observability' },
    { id: 'reviews',       label: 'Expert Review' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden', background: 'var(--bg-1)' }}>
      {/* Tab bar */}
      <div style={{
        background: 'var(--bg-0)', borderBottom: '1px solid var(--border-0)',
        display: 'flex', alignItems: 'center', padding: '0 28px', flexShrink: 0, height: 48,
      }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.08em', color: 'var(--text-3)', textTransform: 'uppercase', marginRight: 20 }}>Admin</span>
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              height: 48, padding: '0 14px', fontSize: 13,
              color: tab === t.id ? 'var(--accent-dim)' : 'var(--text-2)',
              borderBottom: `2px solid ${tab === t.id ? 'var(--accent)' : 'transparent'}`,
              fontFamily: 'var(--font-body)',
              transition: 'all var(--dur-std) var(--ease-std)',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '28px 32px' }}>
        {tab === 'overview'    && <OverviewTab />}
        {tab === 'sessions'    && <SessionsTab />}
        {tab === 'llm'         && <LLMConfigTab />}
        {tab === 'voice'       && <VoiceConfigTab />}
        {tab === 'playground'  && <PlaygroundTab />}
        {tab === 'latency'     && <LatencyTab />}
        {tab === 'cost'        && <CostTab />}
        {tab === 'prompts'     && <PromptsTab />}
        {tab === 'users'       && <UsersTab />}
        {tab === 'events'      && <EventsTab />}
        {tab === 'observability' && <ObservabilityTab />}
        {tab === 'reviews'       && <ExpertReviewTab />}
      </div>
    </div>
  )
}

// ── Overview ───────────────────────────────────────────────────────────────────

function OverviewTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin-dashboard'],
    queryFn: () => adminApi.dashboard().then(r => r.data),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const { data: live } = useQuery({
    queryKey: ['admin-active-sessions'],
    queryFn: () => adminApi.activeSessions().then(r => r.data),
    staleTime: 10_000,
    refetchInterval: 10_000,
  })

  if (isLoading) return <DashSkeleton />

  const metrics = [
    { label: 'Active now',     value: data?.active_sessions ?? 0,       unit: '',      accent: true },
    { label: 'Today',          value: data?.sessions_today ?? 0,         unit: ' sessions' },
    { label: 'This week',      value: data?.sessions_week ?? 0,          unit: ' sessions' },
    { label: 'Avg score',      value: data?.avg_score_week?.toFixed(1) ?? '—', unit: '/10' },
    { label: 'Cost today',     value: data?.total_cost_today_usd?.toFixed(4) ?? '—', unit: ' USD' },
    { label: 'p50 first-token', value: data?.p50_first_token_ms ?? '—',  unit: 'ms' },
    { label: 'p95 first-token', value: data?.p95_first_token_ms ?? '—',  unit: 'ms' },
    { label: 'WS reconnects',  value: data?.ws_reconnects_today ?? 0,    unit: '' },
  ]

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginBottom: 28 }}>
        {metrics.map(m => (
          <Card key={m.label} style={{ padding: '16px 20px' }}>
            <p style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-3)', marginBottom: 8 }}>
              {m.label}
            </p>
            <p style={{
              fontFamily: 'var(--font-mono)', fontSize: 24,
              color: m.accent ? 'var(--accent)' : 'var(--text-0)',
              lineHeight: 1,
            }}>
              {m.value}
              <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{m.unit}</span>
            </p>
          </Card>
        ))}
      </div>

      {/* Live sessions */}
      <Card>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 17, color: 'var(--text-0)' }}>Live sessions</h3>
            <Badge variant="orange" dot live>{data?.active_sessions ?? 0} active</Badge>
          </div>
        </div>
        {!live?.length ? (
          <p style={{ fontSize: 13, color: 'var(--text-3)', padding: '8px 0' }}>No active sessions right now.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {live.map((s: any) => (
              <div key={s.session_id} style={{
                display: 'flex', alignItems: 'center', gap: 14,
                padding: '10px 14px', background: 'var(--bg-1)',
                borderRadius: 'var(--r-md)', fontSize: 12,
              }}>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-3)', width: 80, flexShrink: 0 }}>
                  {s.session_id?.slice(0, 8)}…
                </span>
                <span style={{ color: 'var(--text-1)', flex: 1 }}>{s.domain?.replace(/_/g, ' ')}</span>
                <Badge variant="gray">{s.mode}</Badge>
                <span style={{ color: 'var(--text-3)' }}>Turn {s.turn_count}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Sessions ─────────────────────────────────────────────────────────────────

function SessionsTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin-sessions'],
    queryFn: () => adminApi.sessions({ limit: 50 }).then(r => r.data),
    staleTime: 30_000,
  })

  if (isLoading) return <DashSkeleton />

  const sessions = data?.sessions || []
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)' }}>
          All sessions
        </h2>
        <MonoLabel>{data?.total ?? 0} total</MonoLabel>
      </div>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ background: 'var(--bg-1)', borderBottom: '1px solid var(--border-0)' }}>
              {['ID', 'Domain', 'Status', 'Turns', 'Score', 'Cost', 'Reconnects', 'Started'].map(h => (
                <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-3)', fontWeight: 400 }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sessions.map((s: any) => (
              <tr key={s.id} style={{ borderBottom: '1px solid var(--border-0)' }}>
                <td style={{ padding: '10px 16px', fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>{s.id?.slice(0, 8)}…</td>
                <td style={{ padding: '10px 16px', color: 'var(--text-1)' }}>{s.domain?.replace(/_/g, ' ')}</td>
                <td style={{ padding: '10px 16px' }}>
                  <Badge variant={s.status === 'completed' ? 'green' : s.status === 'active' ? 'orange' : 'gray'}>
                    {s.status}
                  </Badge>
                </td>
                <td style={{ padding: '10px 16px', fontFamily: 'var(--font-mono)', color: 'var(--text-1)' }}>{s.total_turns}</td>
                <td style={{ padding: '10px 16px', fontFamily: 'var(--font-mono)', color: 'var(--text-1)' }}>{s.avg_score?.toFixed(1) ?? '—'}</td>
                <td style={{ padding: '10px 16px', fontFamily: 'var(--font-mono)', color: 'var(--text-2)' }}>${(s.total_cost_usd || 0).toFixed(4)}</td>
                <td style={{ padding: '10px 16px', fontFamily: 'var(--font-mono)', color: s.ws_reconnects > 0 ? 'var(--yellow)' : 'var(--text-2)' }}>{s.ws_reconnects}</td>
                <td style={{ padding: '10px 16px', color: 'var(--text-3)' }}>
                  {s.started_at ? format(new Date(s.started_at), 'MMM d, HH:mm') : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!sessions.length && (
          <EmptyState title="No sessions" body="Interview sessions will appear here." />
        )}
      </Card>
    </div>
  )
}

// ── Latency ────────────────────────────────────────────────────────────────────

function LatencyTab() {
  const [metric, setMetric] = useState('first_token_ms')
  const [hours, setHours] = useState(24)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['admin-latency', metric, hours],
    queryFn: () => adminApi.latencyMetrics({ metric_type: metric, hours }).then(r => r.data),
    staleTime: 60_000,
  })

  const metrics = ['first_token_ms', 'stt_latency_ms', 'first_audio_ms', 'turn_total_ms']

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 24, alignItems: 'center' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', flex: 1 }}>Latency analytics</h2>
        <select value={metric} onChange={e => setMetric(e.target.value)} style={selectStyle}>
          {metrics.map(m => <option key={m} value={m}>{m.replace(/_ms$/, '').replace(/_/g, ' ')}</option>)}
        </select>
        <select value={hours} onChange={e => setHours(Number(e.target.value))} style={selectStyle}>
          <option value={6}>Last 6h</option>
          <option value={24}>Last 24h</option>
          <option value={72}>Last 3d</option>
          <option value={168}>Last 7d</option>
        </select>
        <Button variant="secondary" size="sm" onClick={() => refetch()}>Refresh</Button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginBottom: 24 }}>
        {[
          { label: 'p50', value: data?.p50 },
          { label: 'p95', value: data?.p95 },
          { label: 'p99', value: data?.p99 },
          { label: 'Samples', value: data?.count, unit: '' },
        ].map(m => (
          <Card key={m.label} style={{ padding: '14px 18px' }}>
            <MonoLabel style={{ display: 'block', marginBottom: 8 }}>{m.label}</MonoLabel>
            <p style={{ fontFamily: 'var(--font-mono)', fontSize: 22, color: 'var(--text-0)', lineHeight: 1 }}>
              {isLoading ? '—' : m.value ?? '—'}
              <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{m.unit !== '' ? 'ms' : ''}</span>
            </p>
          </Card>
        ))}
      </div>

      {data && (
        <Card>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-3)', marginBottom: 16 }}>
            Min {data.min}ms · Avg {data.avg}ms · Max {data.max}ms · {data.count} samples
          </p>
          <div style={{ height: 2, background: `linear-gradient(90deg, var(--green) ${Math.min(100, ((data.p50 || 0) / 1000) * 100)}%, var(--yellow) ${Math.min(100, ((data.p95 || 0) / 1000) * 100)}%, var(--red) 100%)`, borderRadius: 1, marginBottom: 8 }} />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>
            <span>0</span><span>500ms</span><span>1000ms</span><span>2000ms+</span>
          </div>
        </Card>
      )}
    </div>
  )
}

// ── Cost ──────────────────────────────────────────────────────────────────────

function CostTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['admin-cost'],
    queryFn: () => adminApi.costMetrics({ days: 7 }).then(r => r.data),
    staleTime: 60_000,
  })

  const chartData = data?.daily?.map((d: any) => ({
    date: d.date?.slice(5), // MM-DD
    cost: d.cost_usd,
    sessions: d.sessions,
    tokens: Math.round((d.tokens_in + d.tokens_out) / 1000),
  })) || []

  const totalCost = data?.daily?.reduce((a: number, d: any) => a + (d.cost_usd || 0), 0) || 0

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)' }}>Cost analytics</h2>
        <MonoLabel>Last 7 days</MonoLabel>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14, marginBottom: 24 }}>
        <Card style={{ padding: '16px 20px' }}>
          <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Total (7d)</MonoLabel>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 26, color: 'var(--text-0)' }}>
            ${totalCost.toFixed(4)}
          </p>
        </Card>
        <Card style={{ padding: '16px 20px' }}>
          <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Per session avg</MonoLabel>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 26, color: 'var(--text-0)' }}>
            ${data?.daily?.length ? (totalCost / data.daily.reduce((a: number, d: any) => a + d.sessions, 0) || 0).toFixed(4) : '—'}
          </p>
        </Card>
        <Card style={{ padding: '16px 20px' }}>
          <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Sessions (7d)</MonoLabel>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 26, color: 'var(--text-0)' }}>
            {data?.daily?.reduce((a: number, d: any) => a + d.sessions, 0) || 0}
          </p>
        </Card>
      </div>

      {isLoading ? (
        <Skeleton h={220} style={{ borderRadius: 12 }} />
      ) : chartData.length > 0 ? (
        <Card>
          <MonoLabel style={{ display: 'block', marginBottom: 20 }}>Daily cost (USD)</MonoLabel>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border-0)" />
              <XAxis dataKey="date" tick={{ fontSize: 11, fill: 'var(--text-3)', fontFamily: 'var(--font-mono)' }} />
              <YAxis tick={{ fontSize: 11, fill: 'var(--text-3)', fontFamily: 'var(--font-mono)' }} />
              <Tooltip
                contentStyle={{ background: 'var(--bg-0)', border: '1px solid var(--border-1)', borderRadius: 8, fontSize: 12, fontFamily: 'var(--font-mono)' }}
                formatter={(val: number) => [`$${val.toFixed(4)}`, 'Cost']}
              />
              <Bar dataKey="cost" fill="var(--accent)" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      ) : (
        <EmptyState title="No cost data" body="Cost data will appear here once interviews are completed." />
      )}
    </div>
  )
}

// ── Prompts ───────────────────────────────────────────────────────────────────

function PromptsTab() {
  const qc = useQueryClient()
  const [preview, setPreview] = useState<any>(null)

  const { data: prompts, isLoading } = useQuery({
    queryKey: ['admin-prompts'],
    queryFn: () => adminApi.prompts().then(r => r.data),
    staleTime: 30_000,
  })

  const activate = useMutation({
    mutationFn: (id: string) => adminApi.activatePrompt(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-prompts'] })
      toast.success('Prompt activated — live interviews will use it within 30s')
    },
    onError: () => toast.error('Failed to activate prompt'),
  })

  const loadContent = async (id: string) => {
    const r = await adminApi.promptContent(id)
    setPreview(r.data)
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
      {/* Prompt list */}
      <div>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', marginBottom: 20 }}>
          Prompt versions
        </h2>
        {isLoading ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {[1,2,3].map(i => <Skeleton key={i} h={72} style={{ borderRadius: 12 }} />)}
          </div>
        ) : !prompts?.length ? (
          <Card><EmptyState title="No prompts" body="Create a prompt version to begin managing the interviewer system prompt." /></Card>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {prompts.map((p: any) => (
              <Card key={p.id} style={{ padding: '14px 18px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-2)' }}>
                    v{p.version_number}
                  </span>
                  <span style={{ fontSize: 13, color: 'var(--text-0)', flex: 1 }}>{p.name}</span>
                  {p.is_active && <Badge variant="green" dot>Active</Badge>}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <Button size="sm" variant="ghost" onClick={() => loadContent(p.id)}>Preview</Button>
                  {!p.is_active && (
                    <Button size="sm" variant="primary" loading={activate.isPending} onClick={() => activate.mutate(p.id)}>
                      Activate
                    </Button>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Preview pane */}
      <div>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', marginBottom: 20 }}>
          Content preview
        </h2>
        <Card style={{ minHeight: 300 }}>
          {preview ? (
            <>
              <MonoLabel style={{ display: 'block', marginBottom: 12 }}>v{preview.version_number}</MonoLabel>
              <pre style={{
                fontFamily: 'var(--font-mono)', fontSize: 11,
                color: 'var(--text-1)', lineHeight: 1.7,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                background: 'var(--bg-1)', padding: '14px 16px',
                borderRadius: 'var(--r-md)', maxHeight: 500, overflowY: 'auto',
              }}>
                {preview.content}
              </pre>
            </>
          ) : (
            <EmptyState icon="📄" title="Select a prompt" body="Click Preview on any prompt version to see its content." />
          )}
        </Card>
      </div>
    </div>
  )
}

// ── Users ─────────────────────────────────────────────────────────────────────

function UsersTab() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => adminApi.users().then(r => r.data),
    staleTime: 60_000,
  })

  const toggle = useMutation({
    mutationFn: (id: string) => adminApi.toggleUser(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
    onError: () => toast.error('Failed to update user'),
  })

  const users = data?.users || []

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20, alignItems: 'center' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)' }}>User management</h2>
        <MonoLabel>{data?.total ?? 0} users</MonoLabel>
      </div>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ background: 'var(--bg-1)', borderBottom: '1px solid var(--border-0)' }}>
              {['Name', 'Email', 'Role', 'Status', 'Last login', ''].map(h => (
                <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-3)', fontWeight: 400 }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map((u: any) => (
              <tr key={u.id} style={{ borderBottom: '1px solid var(--border-0)' }}>
                <td style={{ padding: '10px 16px', color: 'var(--text-1)', fontWeight: 500 }}>{u.full_name || '—'}</td>
                <td style={{ padding: '10px 16px', color: 'var(--text-2)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{u.email}</td>
                <td style={{ padding: '10px 16px' }}>
                  <Badge variant={u.role === 'admin' ? 'orange' : u.role === 'reviewer' ? 'blue' : 'gray'}>{u.role}</Badge>
                </td>
                <td style={{ padding: '10px 16px' }}>
                  <Badge variant={u.is_active ? 'green' : 'gray'}>{u.is_active ? 'Active' : 'Disabled'}</Badge>
                </td>
                <td style={{ padding: '10px 16px', color: 'var(--text-3)' }}>
                  {u.last_login_at ? format(new Date(u.last_login_at), 'MMM d') : 'Never'}
                </td>
                <td style={{ padding: '10px 16px' }}>
                  <Button size="sm" variant="ghost" loading={toggle.isPending} onClick={() => toggle.mutate(u.id)}>
                    {u.is_active ? 'Disable' : 'Enable'}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!users.length && !isLoading && (
          <EmptyState title="No users" body="Registered users will appear here." />
        )}
      </Card>
    </div>
  )
}

// ── Events ─────────────────────────────────────────────────────────────────────

function EventsTab() {
  const [severity, setSeverity] = useState('')
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['admin-events', severity],
    queryFn: () => adminApi.events(severity ? { severity } : {}).then(r => r.data),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const events = data?.events || []
  const SVER_COLORS: Record<string, string> = { error: 'var(--red)', warn: 'var(--yellow)', info: 'var(--blue)', debug: 'var(--text-3)' }

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, alignItems: 'center' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', flex: 1 }}>System events</h2>
        <select value={severity} onChange={e => setSeverity(e.target.value)} style={selectStyle}>
          <option value="">All severity</option>
          <option value="error">Error</option>
          <option value="warn">Warning</option>
          <option value="info">Info</option>
        </select>
        <Button variant="secondary" size="sm" onClick={() => refetch()}>Refresh</Button>
      </div>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        {isLoading ? (
          <div style={{ padding: 20 }}><Skeleton h={200} /></div>
        ) : !events.length ? (
          <EmptyState title="No events" body="System events will appear here." />
        ) : (
          <div>
            {events.map((e: any) => (
              <div key={e.id} style={{
                display: 'flex', gap: 14, padding: '10px 16px',
                borderBottom: '1px solid var(--border-0)', alignItems: 'flex-start',
              }}>
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em',
                  textTransform: 'uppercase', color: SVER_COLORS[e.severity] || 'var(--text-3)',
                  width: 50, flexShrink: 0, marginTop: 1,
                }}>
                  {e.severity}
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-2)', flex: 1 }}>
                  {e.event_type}
                </span>
                <span style={{ fontSize: 12, color: 'var(--text-2)', flex: 2 }}>
                  {e.message || '—'}
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-4)', flexShrink: 0 }}>
                  {e.recorded_at ? format(new Date(e.recorded_at), 'HH:mm:ss') : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}

// ── LLM Config (full — matches monolith) ────────────────────────────────────

const TIER_COLORS: Record<string, string> = {
  fast: 'var(--blue, #3b82f6)', balanced: 'var(--green, #22c55e)',
  premium: 'var(--purple, #a855f7)', external: 'var(--text-3)',
}

function LLMConfigTab() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['admin-llm-config'],
    queryFn: () => adminApi.llmConfig().then(r => r.data),
  })

  const [qgen, setQgen] = useState('')
  const [eval_, setEval] = useState('')

  React.useEffect(() => {
    if (data) { setQgen(data.qgen_model || ''); setEval(data.eval_model || '') }
  }, [data])

  const save = useMutation({
    mutationFn: () => adminApi.setLlmConfig({ qgen_model: qgen, eval_model: eval_ }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-llm-config'] }); toast.success('LLM config saved') },
    onError: () => toast.error('Failed to save'),
  })

  if (isLoading) return <DashSkeleton />
  const models = data?.available_models || []
  const qgenModel = models.find((m: any) => m.id === qgen)
  const evalModel = models.find((m: any) => m.id === eval_)

  return (
    <div style={{ maxWidth: 720 }}>
      <div style={{ marginBottom: 28 }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', marginBottom: 4 }}>Model Selection</h2>
        <p style={{ fontSize: 12, color: 'var(--text-3)' }}>Choose which LLM handles question generation and answer evaluation. Changes apply immediately.</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 24 }}>
        {/* Question Generation */}
        <Card style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
            <div style={{ width: 28, height: 28, borderRadius: 7, background: 'rgba(59,130,246,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <span style={{ fontSize: 14 }}>⚡</span>
            </div>
            <div>
              <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-0)' }}>Question Generation</p>
              <p style={{ fontSize: 10, color: 'var(--text-3)' }}>Needs speed over accuracy</p>
            </div>
          </div>
          <select value={qgen} onChange={e => setQgen(e.target.value)} style={selectStyle}>
            {models.map((m: any) => <option key={m.id} value={m.id}>{m.name} ({m.tier})</option>)}
          </select>
          {qgenModel && (
            <div style={{ marginTop: 10, padding: '8px 10px', background: 'var(--bg-1)', borderRadius: 6, fontSize: 11 }}>
              <div style={{ color: TIER_COLORS[qgenModel.tier] || 'var(--text-2)', fontWeight: 500, marginBottom: 2 }}>{qgenModel.tier.toUpperCase()}</div>
              <div style={{ color: 'var(--text-3)' }}>{qgenModel.cost}</div>
              {qgenModel.best_for && <div style={{ color: 'var(--text-3)', marginTop: 2 }}>{qgenModel.best_for}</div>}
            </div>
          )}
        </Card>

        {/* Evaluation */}
        <Card style={{ padding: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
            <div style={{ width: 28, height: 28, borderRadius: 7, background: 'rgba(34,197,94,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <span style={{ fontSize: 14 }}>✓</span>
            </div>
            <div>
              <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-0)' }}>Answer Evaluation</p>
              <p style={{ fontSize: 10, color: 'var(--text-3)' }}>Needs accuracy over speed</p>
            </div>
          </div>
          <select value={eval_} onChange={e => setEval(e.target.value)} style={selectStyle}>
            {models.map((m: any) => <option key={m.id} value={m.id}>{m.name} ({m.tier})</option>)}
          </select>
          {evalModel && (
            <div style={{ marginTop: 10, padding: '8px 10px', background: 'var(--bg-1)', borderRadius: 6, fontSize: 11 }}>
              <div style={{ color: TIER_COLORS[evalModel.tier] || 'var(--text-2)', fontWeight: 500, marginBottom: 2 }}>{evalModel.tier.toUpperCase()}</div>
              <div style={{ color: 'var(--text-3)' }}>{evalModel.cost}</div>
              {evalModel.best_for && <div style={{ color: 'var(--text-3)', marginTop: 2 }}>{evalModel.best_for}</div>}
            </div>
          )}
        </Card>
      </div>

      <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save Configuration</Button>
      <span style={{ marginLeft: 12, fontSize: 12, color: 'var(--green, #22c55e)' }}>{save.isSuccess ? 'Saved' : ''}</span>

      {/* Model Reference */}
      <div style={{ marginTop: 32, paddingTop: 20, borderTop: '1px solid var(--border-0)' }}>
        <MonoLabel style={{ display: 'block', marginBottom: 14 }}>Model Reference</MonoLabel>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, fontSize: 11 }}>
          <div style={{ padding: '10px 12px', background: 'rgba(59,130,246,0.05)', borderRadius: 7, border: '1px solid rgba(59,130,246,0.15)' }}>
            <div style={{ fontWeight: 600, color: 'var(--blue, #3b82f6)', marginBottom: 4 }}>Fast Tier</div>
            <div style={{ color: 'var(--text-2)' }}>Haiku 4.5, Grok Fast, Nova Lite<br/>Best for question generation</div>
          </div>
          <div style={{ padding: '10px 12px', background: 'rgba(34,197,94,0.05)', borderRadius: 7, border: '1px solid rgba(34,197,94,0.15)' }}>
            <div style={{ fontWeight: 600, color: 'var(--green, #22c55e)', marginBottom: 4 }}>Balanced Tier</div>
            <div style={{ color: 'var(--text-2)' }}>Sonnet 4.5/4.6, Grok 4.3<br/>Best for evaluation</div>
          </div>
          <div style={{ padding: '10px 12px', background: 'rgba(168,85,247,0.05)', borderRadius: 7, border: '1px solid rgba(168,85,247,0.15)' }}>
            <div style={{ fontWeight: 600, color: 'var(--purple, #a855f7)', marginBottom: 4 }}>Premium Tier</div>
            <div style={{ color: 'var(--text-2)' }}>Opus 4.6/4.7<br/>Highest accuracy, slowest</div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Voice Config (full — all providers + TTS playground) ─────────────────────

const VOICE_PROVIDERS: { id: string; label: string }[] = [
  { id: 'inworld', label: 'Inworld TTS' },
  { id: 'deepgram', label: 'Deepgram Aura' },
  { id: 'puter', label: 'Puter TTS (free)' },
  { id: 'openai', label: 'OpenAI TTS' },
]

const ALL_VOICES: Record<string, { id: string; label: string; group?: string }[]> = {
  inworld: [
    // Female
    { id: 'Sarah', label: 'Sarah (American)', group: 'Female' },
    { id: 'Emma', label: 'Emma (British)', group: 'Female' },
    { id: 'Lily', label: 'Lily (British)', group: 'Female' },
    { id: 'Priya', label: 'Priya (Indian)', group: 'Female' },
    { id: 'Ananya', label: 'Ananya (Indian)', group: 'Female' },
    { id: 'Sofia', label: 'Sofia (American)', group: 'Female' },
    { id: 'Charlotte', label: 'Charlotte (British)', group: 'Female' },
    { id: 'Aria', label: 'Aria (American)', group: 'Female' },
    // Male
    { id: 'Clive', label: 'Clive (British)', group: 'Male' },
    { id: 'James', label: 'James (British)', group: 'Male' },
    { id: 'Raj', label: 'Raj (Indian)', group: 'Male' },
    { id: 'Alex', label: 'Alex (American)', group: 'Male' },
    // Custom clones
    { id: 'default-mhhlz-fgtvvnjgmtx5spya__ranjitha', label: 'Ranjitha (clone)', group: 'Custom' },
    { id: 'default-t7o4so8lr5hiosww3f453a__ranjitha-2wjmz', label: 'Ranjitha v2 (clone)', group: 'Custom' },
    { id: 'default-mhhlz-fgtvvnjgmtx5spya__ritu', label: 'Ritu (clone)', group: 'Custom' },
  ],
  deepgram: [
    { id: 'aura-asteria-en', label: 'Asteria (female)' },
    { id: 'aura-luna-en', label: 'Luna (female)' },
    { id: 'aura-stella-en', label: 'Stella (female)' },
    { id: 'aura-athena-en', label: 'Athena (female)' },
    { id: 'aura-hera-en', label: 'Hera (female)' },
    { id: 'aura-orion-en', label: 'Orion (male)' },
    { id: 'aura-arcas-en', label: 'Arcas (male)' },
    { id: 'aura-perseus-en', label: 'Perseus (male)' },
    { id: 'aura-angus-en', label: 'Angus (male)' },
    { id: 'aura-orpheus-en', label: 'Orpheus (male)' },
    { id: 'aura-helios-en', label: 'Helios (male)' },
    { id: 'aura-zeus-en', label: 'Zeus (male)' },
  ],
  puter: [
    // xAI
    { id: 'xai:eve', label: 'xAI Eve (female)', group: 'xAI' },
    { id: 'xai:ara', label: 'xAI Ara (female)', group: 'xAI' },
    { id: 'xai:rex', label: 'xAI Rex (male)', group: 'xAI' },
    { id: 'xai:sal', label: 'xAI Sal (neutral)', group: 'xAI' },
    { id: 'xai:leo', label: 'xAI Leo (male)', group: 'xAI' },
    // Gemini
    { id: 'gemini:Puck', label: 'Gemini Puck', group: 'Gemini' },
    { id: 'gemini:Kore', label: 'Gemini Kore', group: 'Gemini' },
    { id: 'gemini:Charon', label: 'Gemini Charon', group: 'Gemini' },
    { id: 'gemini:Fenrir', label: 'Gemini Fenrir', group: 'Gemini' },
    { id: 'gemini:Aoede', label: 'Gemini Aoede', group: 'Gemini' },
    { id: 'gemini:Leda', label: 'Gemini Leda', group: 'Gemini' },
    { id: 'gemini:Orus', label: 'Gemini Orus', group: 'Gemini' },
    { id: 'gemini:Zephyr', label: 'Gemini Zephyr', group: 'Gemini' },
    // AWS Polly
    { id: 'aws-polly:Joanna', label: 'Polly Joanna (US female)', group: 'AWS Polly' },
    { id: 'aws-polly:Matthew', label: 'Polly Matthew (US male)', group: 'AWS Polly' },
    { id: 'aws-polly:Ivy', label: 'Polly Ivy (US child)', group: 'AWS Polly' },
    { id: 'aws-polly:Joey', label: 'Polly Joey (US male)', group: 'AWS Polly' },
    { id: 'aws-polly:Amy', label: 'Polly Amy (British)', group: 'AWS Polly' },
    { id: 'aws-polly:Brian', label: 'Polly Brian (British)', group: 'AWS Polly' },
    { id: 'aws-polly:Aditi', label: 'Polly Aditi (Indian)', group: 'AWS Polly' },
    { id: 'aws-polly:Raveena', label: 'Polly Raveena (Indian)', group: 'AWS Polly' },
    { id: 'aws-polly:Kajal', label: 'Polly Kajal (Indian)', group: 'AWS Polly' },
    { id: 'aws-polly:Camila', label: 'Polly Camila (Portuguese)', group: 'AWS Polly' },
    { id: 'aws-polly:Lucia', label: 'Polly Lucia (Spanish)', group: 'AWS Polly' },
    { id: 'aws-polly:Celine', label: 'Polly Celine (French)', group: 'AWS Polly' },
    // ElevenLabs
    { id: 'elevenlabs:21m00Tcm4TlvDq8ikWAM', label: 'ElevenLabs Rachel', group: 'ElevenLabs' },
    { id: 'elevenlabs:AZnzlk1XvdvUeBnXmlld', label: 'ElevenLabs Domi', group: 'ElevenLabs' },
    { id: 'elevenlabs:EXAVITQu4vr4xnSDxMaL', label: 'ElevenLabs Bella', group: 'ElevenLabs' },
    { id: 'elevenlabs:MF3mGyEYCl7XYWbV9V6O', label: 'ElevenLabs Elli', group: 'ElevenLabs' },
    { id: 'elevenlabs:jsCqWAovK2LkecY7zXl4', label: 'ElevenLabs Freya', group: 'ElevenLabs' },
    { id: 'elevenlabs:ErXwobaYiN019PkySvjV', label: 'ElevenLabs Antoni', group: 'ElevenLabs' },
    { id: 'elevenlabs:TxGEqnHWrfWFTfGW9XjX', label: 'ElevenLabs Josh', group: 'ElevenLabs' },
    { id: 'elevenlabs:pNInz6obpgDQGcFmaJgB', label: 'ElevenLabs Adam', group: 'ElevenLabs' },
    // OpenAI via Puter
    { id: 'openai:alloy', label: 'OpenAI Alloy', group: 'OpenAI' },
    { id: 'openai:ash', label: 'OpenAI Ash', group: 'OpenAI' },
    { id: 'openai:ballad', label: 'OpenAI Ballad', group: 'OpenAI' },
    { id: 'openai:coral', label: 'OpenAI Coral', group: 'OpenAI' },
    { id: 'openai:echo', label: 'OpenAI Echo', group: 'OpenAI' },
    { id: 'openai:fable', label: 'OpenAI Fable', group: 'OpenAI' },
    { id: 'openai:nova', label: 'OpenAI Nova', group: 'OpenAI' },
    { id: 'openai:onyx', label: 'OpenAI Onyx', group: 'OpenAI' },
    { id: 'openai:sage', label: 'OpenAI Sage', group: 'OpenAI' },
    { id: 'openai:shimmer', label: 'OpenAI Shimmer', group: 'OpenAI' },
  ],
  openai: [
    { id: 'alloy', label: 'Alloy' }, { id: 'ash', label: 'Ash' }, { id: 'ballad', label: 'Ballad' },
    { id: 'coral', label: 'Coral' }, { id: 'echo', label: 'Echo' }, { id: 'fable', label: 'Fable' },
    { id: 'nova', label: 'Nova' }, { id: 'onyx', label: 'Onyx' }, { id: 'sage', label: 'Sage' },
    { id: 'shimmer', label: 'Shimmer' },
  ],
}

function VoiceConfigTab() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['admin-voice-config'],
    queryFn: () => adminApi.voiceConfig().then(r => r.data),
  })

  const [enabled, setEnabled] = useState(true)
  const [provider, setProvider] = useState('inworld')
  const [voice, setVoice] = useState('')
  const [testText, setTestText] = useState("Hi Rahul, welcome to the interview. I'm going to ask you about clock tree synthesis today. Let's start simple — what is clock skew and why does it matter?")
  const [testStatus, setTestStatus] = useState('')

  React.useEffect(() => {
    if (data) {
      setEnabled(data.tts_enabled !== false)
      setProvider(data.tts_provider || 'inworld')
      setVoice(data.tts_voice || '')
    }
  }, [data])

  const save = useMutation({
    mutationFn: () => adminApi.setVoiceConfig({ tts_enabled: enabled, tts_provider: provider, tts_voice: voice }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-voice-config'] }); toast.success('Voice config saved') },
    onError: () => toast.error('Failed to save'),
  })

  const setInterview = useMutation({
    mutationFn: () => adminApi.setVoiceConfig({ tts_provider: provider, tts_voice: voice }),
    onSuccess: () => toast.success(`Interview voice set to ${voice} (${provider})`),
    onError: () => toast.error('Failed to set interview voice'),
  })

  const testVoice = async () => {
    if (!testText.trim() || !voice) return
    setTestStatus('Generating...')
    try {
      // For Puter voices, use client-side TTS
      if (provider === 'puter' && (window as any).puter) {
        const [puterProvider, puterVoice] = voice.split(':')
        const opts: any = { provider: puterProvider, voice: puterVoice }
        if (puterProvider === 'aws-polly') { opts.engine = 'neural'; opts.language = 'en-US' }
        if (puterProvider === 'openai') { opts.model = 'gpt-4o-mini-tts' }
        if (puterProvider === 'elevenlabs') { opts.model = 'eleven_multilingual_v2' }
        const t0 = Date.now()
        const audio = await (window as any).puter.ai.txt2speech(testText, opts)
        setTestStatus(`Playing (${puterProvider}) — ${Date.now() - t0}ms`)
        audio.play()
        audio.onended = () => setTestStatus(`Done — ${Date.now() - t0}ms`)
      } else {
        // Server-side TTS (OpenAI, Deepgram, Inworld)
        const t0 = Date.now()
        setTestStatus(`Calling ${provider} TTS (${voice})...`)
        const res = await adminApi.testTts({ text: testText, provider, voice })
        const d = res.data
        if (d.status === 'success' && d.audio) {
          const bytes = Uint8Array.from(atob(d.audio), c => c.charCodeAt(0))
          const blob = new Blob([bytes], { type: d.format || 'audio/mpeg' })
          const url = URL.createObjectURL(blob)
          const audio = new Audio(url)
          setTestStatus(`Playing (${provider}/${voice}) — ${d.latency_ms}ms`)
          audio.onended = () => { URL.revokeObjectURL(url); setTestStatus(`Done — ${d.latency_ms}ms`) }
          audio.play()
        } else {
          setTestStatus(`Error: ${d.error || 'no audio'}`)
        }
      }
    } catch (e: any) {
      setTestStatus(`Error: ${e.message}`)
    }
  }

  if (isLoading) return <DashSkeleton />
  const voices = ALL_VOICES[provider] || []

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
      {/* LEFT: Settings */}
      <div>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', marginBottom: 20 }}>Voice Settings</h2>

        {/* TTS Toggle */}
        <Card style={{ padding: 16, marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-0)' }}>Text-to-Speech</p>
              <p style={{ fontSize: 10, color: 'var(--text-3)', marginTop: 2 }}>Enable AI voice for interview questions</p>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
              <span style={{ fontSize: 12, color: 'var(--text-2)' }}>{enabled ? 'On' : 'Off'}</span>
            </label>
          </div>
        </Card>

        {/* Provider */}
        <Card style={{ padding: 16, marginBottom: 12 }}>
          <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Provider</MonoLabel>
          <select value={provider} onChange={e => { setProvider(e.target.value); setVoice('') }} style={selectStyle}>
            {VOICE_PROVIDERS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </Card>

        {/* Voice */}
        <Card style={{ padding: 16, marginBottom: 12 }}>
          <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Voice ({voices.length} available)</MonoLabel>
          <select value={voice} onChange={e => setVoice(e.target.value)} style={selectStyle}>
            <option value="">Select voice...</option>
            {voices.map(v => <option key={v.id} value={v.id}>{v.label}</option>)}
          </select>

          <button onClick={() => setInterview.mutate()} disabled={!voice}
            style={{ width: '100%', marginTop: 10, padding: '8px', background: voice ? 'var(--accent)' : 'var(--text-3)', color: '#fff', border: 'none', borderRadius: 6, fontSize: 11, fontWeight: 500, cursor: voice ? 'pointer' : 'not-allowed' }}>
            Use in Real Interview
          </button>
        </Card>

        <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()} style={{ width: '100%' }}>
          Save All Settings
        </Button>
      </div>

      {/* RIGHT: TTS Playground */}
      <div>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', marginBottom: 20 }}>TTS Playground</h2>
        <Card style={{ padding: 16 }}>
          <MonoLabel style={{ display: 'block', marginBottom: 6 }}>Test text</MonoLabel>
          <textarea value={testText} onChange={e => setTestText(e.target.value)} rows={4}
            style={{ ...textareaStyle, marginBottom: 10 }} />
          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={testVoice} disabled={!voice}
              style={{ flex: 1, padding: '9px', background: voice ? 'var(--green, #22c55e)' : 'var(--text-3)', color: '#fff', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 500, cursor: voice ? 'pointer' : 'not-allowed' }}>
              Play Voice
            </button>
          </div>
          {testStatus && (
            <p style={{ fontSize: 10, color: 'var(--text-3)', marginTop: 8, fontFamily: 'var(--font-mono)' }}>{testStatus}</p>
          )}
        </Card>
      </div>
    </div>
  )
}

// ── Prompt Playground (full — chat mode, domain/level, fill sample) ──────────

function PlaygroundTab() {
  const [prompt, setPrompt] = useState('')
  const [system, setSystem] = useState('')
  const [model, setModel] = useState('')
  const [temp, setTemp] = useState(0.3)
  const [maxTokens, setMaxTokens] = useState(600)
  const [result, setResult] = useState<any>(null)
  const [history, setHistory] = useState<{ role: string; content: string }[]>([])
  const [domain, setDomain] = useState('physical_design')
  const [level, setLevel] = useState('trained_fresher')
  const [candidateName, setCandidateName] = useState('Sample Candidate')

  const { data: llmData } = useQuery({
    queryKey: ['admin-llm-config'],
    queryFn: () => adminApi.llmConfig().then(r => r.data),
  })

  const run = useMutation({
    mutationFn: () => adminApi.playground({
      prompt,
      system_prompt: system || undefined,
      model_id: model || undefined,
      temperature: temp,
      max_tokens: maxTokens,
    }),
    onSuccess: (r) => {
      setResult(r.data)
      if (r.data?.response) {
        setHistory(h => [...h, { role: 'user', content: prompt }, { role: 'assistant', content: r.data.response }])
      }
    },
    onError: (e: any) => setResult({ status: 'error', error: e.message }),
  })

  const fillSample = () => {
    const d = domain.replace(/_/g, ' ')
    const l = level.replace(/_/g, ' ')
    setSystem(`You are a senior ${d} interviewer. Evaluate this candidate's answer strictly.`)
    setPrompt(`CANDIDATE: ${candidateName} | ${l} | ${d}
QUESTION: What is clock skew and why does it matter in CTS?
ANSWER: Clock skew is when the clock arrives at different times at different flip-flops. It matters because it can cause setup and hold violations.

Score this answer 0-10. Return JSON with: score, quality (strong/adequate/weak), accuracy (correct/partial/wrong), missing_points, score_reasoning.`)
  }

  const fillQgen = () => {
    const d = domain.replace(/_/g, ' ')
    const l = level.replace(/_/g, ' ')
    setSystem(`You are Ranjitha, a principal VLSI design engineer conducting a technical interview. React to the answer then ask ONE follow-up question. 1-2 sentences max.`)
    setPrompt(`DOMAIN: ${d}
CANDIDATE: ${candidateName} | ${l}
Topic: clock tree synthesis — clock skew definition and targets

CANDIDATE ANSWER:
Clock skew is when the clock signal arrives at different times at different flip-flops.

Your question:`)
  }

  const clearChat = () => { setHistory([]); setResult(null) }
  const models = llmData?.available_models || []

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)' }}>Prompt Playground</h2>
        </div>
        <Card style={{ padding: 16, marginBottom: 12 }}>
          {/* Config row */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 12 }}>
            <div>
              <MonoLabel style={{ display: 'block', marginBottom: 4 }}>Domain</MonoLabel>
              <select value={domain} onChange={e => setDomain(e.target.value)} style={{ ...selectStyle, fontSize: 11 }}>
                <option value="physical_design">Physical Design</option>
                <option value="analog_layout">Analog Layout</option>
                <option value="design_verification">Design Verification</option>
              </select>
            </div>
            <div>
              <MonoLabel style={{ display: 'block', marginBottom: 4 }}>Level</MonoLabel>
              <select value={level} onChange={e => setLevel(e.target.value)} style={{ ...selectStyle, fontSize: 11 }}>
                <option value="fresh_graduate">Fresh Graduate</option>
                <option value="trained_fresher">Trained Fresher</option>
                <option value="experienced_junior">Junior (1-3y)</option>
                <option value="experienced_senior">Senior (3+y)</option>
              </select>
            </div>
            <div>
              <MonoLabel style={{ display: 'block', marginBottom: 4 }}>Name</MonoLabel>
              <input value={candidateName} onChange={e => setCandidateName(e.target.value)}
                style={{ ...selectStyle, fontSize: 11 }} />
            </div>
          </div>

          {/* Quick fill buttons */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
            <button onClick={fillSample} style={pillBtnStyle}>Fill Evaluation Sample</button>
            <button onClick={fillQgen} style={pillBtnStyle}>Fill Question Gen Sample</button>
            <button onClick={clearChat} style={{ ...pillBtnStyle, color: 'var(--red, #ef4444)' }}>Clear</button>
          </div>
        </Card>

        <Card style={{ padding: 16 }}>
          <div style={{ marginBottom: 10 }}>
            <MonoLabel style={{ display: 'block', marginBottom: 4 }}>System prompt</MonoLabel>
            <textarea value={system} onChange={e => setSystem(e.target.value)} rows={3}
              placeholder="e.g. You are a strict VLSI interviewer..."
              style={{ ...textareaStyle, fontSize: 11 }} />
          </div>
          <div style={{ marginBottom: 10 }}>
            <MonoLabel style={{ display: 'block', marginBottom: 4 }}>Prompt</MonoLabel>
            <textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={8}
              placeholder="Enter your prompt or candidate answer..."
              style={{ ...textareaStyle, fontSize: 11 }} />
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
            <div style={{ flex: 1 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 4 }}>Model</MonoLabel>
              <select value={model} onChange={e => setModel(e.target.value)} style={{ ...selectStyle, fontSize: 11 }}>
                <option value="">Use eval model</option>
                {models.map((m: any) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </div>
            <div style={{ width: 70 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 4 }}>Temp</MonoLabel>
              <input type="number" value={temp} onChange={e => setTemp(Number(e.target.value))} step={0.1} min={0} max={1}
                style={{ ...selectStyle, width: '100%', fontSize: 11 }} />
            </div>
            <div style={{ width: 80 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 4 }}>Tokens</MonoLabel>
              <input type="number" value={maxTokens} onChange={e => setMaxTokens(Number(e.target.value))} step={100} min={50} max={2000}
                style={{ ...selectStyle, width: '100%', fontSize: 11 }} />
            </div>
          </div>
          <Button variant="primary" loading={run.isPending} onClick={() => run.mutate()} style={{ width: '100%' }}>
            Run Prompt
          </Button>
        </Card>
      </div>

      {/* RIGHT: Response + Chat History */}
      <div>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', marginBottom: 20 }}>Response</h2>
        <Card style={{ minHeight: 500, maxHeight: 700, overflowY: 'auto' }}>
          {/* Chat history */}
          {history.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              {history.map((msg, i) => (
                <div key={i} style={{
                  padding: '8px 12px', marginBottom: 6, borderRadius: 8,
                  background: msg.role === 'user' ? 'var(--bg-2)' : 'var(--bg-1)',
                  borderLeft: `3px solid ${msg.role === 'user' ? 'var(--accent)' : 'var(--green, #22c55e)'}`,
                }}>
                  <MonoLabel style={{ display: 'block', marginBottom: 4, fontSize: 9 }}>
                    {msg.role === 'user' ? 'YOU' : 'LLM'}
                  </MonoLabel>
                  <pre style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-1)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.5 }}>
                    {msg.content}
                  </pre>
                </div>
              ))}
            </div>
          )}

          {/* Latest result */}
          {result && !history.length ? (
            <>
              <div style={{ display: 'flex', gap: 12, marginBottom: 14, fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>
                <span>{result.model}</span>
                <span>{result.latency_ms}ms</span>
                <Badge variant={result.status === 'success' ? 'green' : 'red'}>{result.status}</Badge>
              </div>
              <pre style={{
                fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-1)',
                lineHeight: 1.7, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                background: 'var(--bg-1)', padding: '14px 16px',
                borderRadius: 'var(--r-md)', maxHeight: 500, overflowY: 'auto',
              }}>
                {result.response || result.error || 'No output'}
              </pre>
            </>
          ) : (
            <EmptyState icon="⚡" title="Run a prompt" body="Enter a prompt and click Run to see the LLM response." />
          )}
        </Card>
      </div>
    </div>
  )
}

// ── Expert Review ─────────────────────────────────────────────────────────────

const REVIEW_DIMS = ['Technical Accuracy', 'Depth & Completeness', 'Clarity', 'Level Calibration']
const REVIEW_FLAGS = [
  { id: 'missed', label: 'Missed concept' },
  { id: 'overscored', label: 'Score too high' },
  { id: 'underscored', label: 'Score too low' },
  { id: 'halluc', label: 'Hallucination' },
  { id: 'vague', label: 'Vague feedback' },
  { id: 'level', label: 'Level mismatch' },
]
const REVIEW_VERDICTS = [
  { id: 'excellent', label: 'Excellent', grade: 'A+', color: 'var(--green, #22c55e)' },
  { id: 'good', label: 'Good', grade: 'A', color: 'var(--blue, #3b82f6)' },
  { id: 'acceptable', label: 'Acceptable', grade: 'B', color: 'var(--text-2)' },
  { id: 'poor', label: 'Poor', grade: 'C', color: 'var(--yellow, #eab308)' },
  { id: 'unusable', label: 'Unusable', grade: 'F', color: 'var(--red, #ef4444)' },
]
const BEHAVIOR_OPTIONS = {
  reasoning: [{ v: 'clear', l: 'Clear' }, { v: 'weak', l: 'Weak' }, { v: 'flawed', l: 'Flawed' }],
  feedback: [{ v: 'actionable', l: 'Actionable' }, { v: 'partial', l: 'Partial' }, { v: 'vague', l: 'Vague' }],
  calibration: [{ v: 'appropriate', l: 'Appropriate' }, { v: 'lenient', l: 'Too lenient' }, { v: 'harsh', l: 'Too harsh' }],
}

function ExpertReviewTab() {
  const qc = useQueryClient()
  const [selectedSession, setSelectedSession] = useState<string>('')
  const [reviewTurn, setReviewTurn] = useState<number | null>(null)
  const [humanScore, setHumanScore] = useState(5)
  const [dims, setDims] = useState<string[]>(Array(4).fill(''))
  const [flags, setFlags] = useState<Set<string>>(new Set())
  const [beh, setBeh] = useState({ reasoning: '', feedback: '', calibration: '' })
  const [verdict, setVerdict] = useState('')
  const [feedback, setFeedback] = useState('')

  // Review queue
  const { data: queue, isLoading: queueLoading } = useQuery({
    queryKey: ['review-queue'],
    queryFn: () => reviewerApi.queue({ status: 'pending', limit: 20 }).then(r => r.data),
    staleTime: 30_000,
  })

  // Session transcript
  const { data: transcript } = useQuery({
    queryKey: ['review-transcript', selectedSession],
    queryFn: () => reviewerApi.transcript(selectedSession).then(r => r.data),
    enabled: !!selectedSession,
  })

  // Existing reviews for session
  const { data: existingReviews } = useQuery({
    queryKey: ['review-existing', selectedSession],
    queryFn: () => reviewerApi.sessionReviews(selectedSession).then(r => r.data),
    enabled: !!selectedSession,
  })

  const submit = useMutation({
    mutationFn: () => {
      const turn = transcript?.turns?.[reviewTurn!]
      return reviewerApi.submitReview({
        session_id: selectedSession,
        question_turn: turn?.turn_number ?? reviewTurn,
        ai_score: turn?.avg_score ?? 5,
        human_score: humanScore,
        dimension_assessments: REVIEW_DIMS.map((d, i) => ({ dimension: d, assessment: dims[i] || 'not_set' })),
        error_flags: [...flags],
        concept_corrections: [],
        behavior_ratings: { reasoning_quality: beh.reasoning, feedback_quality: beh.feedback, calibration: beh.calibration },
        verdict,
        overall_feedback: feedback,
      })
    },
    onSuccess: () => {
      toast.success('Review saved')
      qc.invalidateQueries({ queryKey: ['review-existing', selectedSession] })
      resetForm()
      setReviewTurn(null)
    },
    onError: () => toast.error('Failed to save review'),
  })

  const approve = useMutation({
    mutationFn: () => reviewerApi.approve(selectedSession),
    onSuccess: () => {
      toast.success('Session approved')
      qc.invalidateQueries({ queryKey: ['review-queue'] })
    },
  })

  const resetForm = () => {
    setHumanScore(5); setDims(Array(4).fill('')); setFlags(new Set())
    setBeh({ reasoning: '', feedback: '', calibration: '' }); setVerdict(''); setFeedback('')
  }

  const toggleFlag = (id: string) => {
    const next = new Set(flags)
    next.has(id) ? next.delete(id) : next.add(id)
    setFlags(next)
  }

  const turns = transcript?.turns || []
  const reviewed = new Set((existingReviews?.reviews || []).map((r: any) => r.turn_number))

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr 360px', gap: 20, height: 'calc(100vh - 130px)' }}>

      {/* LEFT: Review queue */}
      <div style={{ overflowY: 'auto' }}>
        <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 16, color: 'var(--text-0)', marginBottom: 14 }}>Review Queue</h3>
        {queueLoading ? <DashSkeleton /> : !queue?.queue?.length ? (
          <EmptyState title="Queue empty" body="No sessions pending review." />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {queue.queue.map((s: any) => (
              <div
                key={s.session_id}
                onClick={() => { setSelectedSession(s.session_id); setReviewTurn(null); resetForm() }}
                style={{
                  padding: '10px 12px', borderRadius: 8, cursor: 'pointer',
                  background: selectedSession === s.session_id ? 'var(--accent-8)' : 'var(--bg-0)',
                  border: `1px solid ${selectedSession === s.session_id ? 'var(--accent-25)' : 'var(--border-1)'}`,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-3)' }}>
                    {s.session_id?.slice(0, 8)}
                  </span>
                  <Badge variant={s.review_status === 'pending' ? 'orange' : 'green'}>{s.review_status}</Badge>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-1)' }}>{s.domain?.replace(/_/g, ' ')}</div>
                <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
                  {s.total_turns} turns · Score: {s.overall_score?.toFixed(1) ?? '—'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* CENTER: Transcript */}
      <div style={{ overflowY: 'auto', borderLeft: '1px solid var(--border-0)', borderRight: '1px solid var(--border-0)', padding: '0 20px' }}>
        {!selectedSession ? (
          <EmptyState title="Select a session" body="Choose a session from the queue to review." />
        ) : (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, position: 'sticky', top: 0, background: 'var(--bg-1)', padding: '12px 0', zIndex: 1 }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 16, color: 'var(--text-0)' }}>
                Transcript — {transcript?.session?.domain?.replace(/_/g, ' ')}
              </h3>
              <Button variant="primary" size="sm" onClick={() => approve.mutate()} loading={approve.isPending}>
                Approve Session
              </Button>
            </div>
            {turns.map((t: any, i: number) => (
              <div
                key={t.turn_number}
                onClick={() => { setReviewTurn(i); resetForm(); setHumanScore(t.avg_score ?? 5) }}
                style={{
                  padding: '12px 14px', marginBottom: 8, borderRadius: 8, cursor: 'pointer',
                  background: reviewTurn === i ? 'var(--accent-8)' : 'var(--bg-0)',
                  border: `1px solid ${reviewTurn === i ? 'var(--accent-25)' : 'var(--border-1)'}`,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <MonoLabel>Turn {t.turn_number}</MonoLabel>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {reviewed.has(t.turn_number) && <Badge variant="green">Reviewed</Badge>}
                    {t.avg_score != null && (
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: t.avg_score >= 7 ? 'var(--green, #22c55e)' : t.avg_score >= 5 ? 'var(--text-2)' : 'var(--red, #ef4444)' }}>
                        AI: {t.avg_score?.toFixed(1)}
                      </span>
                    )}
                  </div>
                </div>
                <p style={{ fontSize: 13, color: 'var(--accent-dim)', marginBottom: 6, fontWeight: 500 }}>
                  {t.question || '(no question)'}
                </p>
                {t.answer && (
                  <p style={{ fontSize: 12, color: 'var(--text-2)', lineHeight: 1.6, borderLeft: '2px solid var(--border-2)', paddingLeft: 10 }}>
                    {t.answer}
                  </p>
                )}
              </div>
            ))}
          </>
        )}
      </div>

      {/* RIGHT: Review form */}
      <div style={{ overflowY: 'auto' }}>
        {reviewTurn === null ? (
          <EmptyState title="Select a turn" body="Click on a turn in the transcript to review it." />
        ) : (
          <div>
            <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 16, color: 'var(--text-0)', marginBottom: 14 }}>
              Review Turn {turns[reviewTurn]?.turn_number}
            </h3>

            {/* Human Score */}
            <Card style={{ padding: 14, marginBottom: 10 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Human Score</MonoLabel>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <input type="range" min={0} max={10} step={0.5} value={humanScore} onChange={e => setHumanScore(Number(e.target.value))}
                  style={{ flex: 1 }} />
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 18, color: 'var(--text-0)', width: 40, textAlign: 'center' }}>
                  {humanScore}
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-4)', marginTop: 4 }}>
                <span>AI: {turns[reviewTurn]?.avg_score?.toFixed(1) ?? '—'}</span>
                <span>Delta: {(humanScore - (turns[reviewTurn]?.avg_score ?? 5)).toFixed(1)}</span>
              </div>
            </Card>

            {/* Dimension Assessments */}
            <Card style={{ padding: 14, marginBottom: 10 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Dimensions</MonoLabel>
              {REVIEW_DIMS.map((dim, i) => (
                <div key={dim} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <span style={{ fontSize: 11, color: 'var(--text-2)', width: 130, flexShrink: 0 }}>{dim}</span>
                  <div style={{ display: 'flex', gap: 4 }}>
                    {['correct', 'too_high', 'too_low'].map(v => (
                      <button key={v} onClick={() => { const d = [...dims]; d[i] = v; setDims(d) }}
                        style={{
                          padding: '3px 8px', fontSize: 10, borderRadius: 4, cursor: 'pointer',
                          background: dims[i] === v ? 'var(--accent)' : 'var(--bg-1)',
                          color: dims[i] === v ? '#fff' : 'var(--text-3)',
                          border: `1px solid ${dims[i] === v ? 'var(--accent)' : 'var(--border-2)'}`,
                        }}>
                        {v.replace('_', ' ')}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </Card>

            {/* Error Flags */}
            <Card style={{ padding: 14, marginBottom: 10 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Error Flags</MonoLabel>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {REVIEW_FLAGS.map(f => (
                  <button key={f.id} onClick={() => toggleFlag(f.id)}
                    style={{
                      padding: '4px 10px', fontSize: 10, borderRadius: 12, cursor: 'pointer',
                      background: flags.has(f.id) ? 'rgba(239,68,68,0.1)' : 'var(--bg-1)',
                      color: flags.has(f.id) ? 'var(--red, #ef4444)' : 'var(--text-3)',
                      border: `1px solid ${flags.has(f.id) ? 'rgba(239,68,68,0.3)' : 'var(--border-2)'}`,
                    }}>
                    {f.label}
                  </button>
                ))}
              </div>
            </Card>

            {/* AI Behavior Ratings */}
            <Card style={{ padding: 14, marginBottom: 10 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 8 }}>AI Behavior</MonoLabel>
              {Object.entries(BEHAVIOR_OPTIONS).map(([key, options]) => (
                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <span style={{ fontSize: 11, color: 'var(--text-2)', width: 80, flexShrink: 0, textTransform: 'capitalize' }}>{key}</span>
                  <div style={{ display: 'flex', gap: 4 }}>
                    {options.map(o => (
                      <button key={o.v} onClick={() => setBeh({ ...beh, [key]: o.v })}
                        style={{
                          padding: '3px 8px', fontSize: 10, borderRadius: 4, cursor: 'pointer',
                          background: (beh as any)[key] === o.v ? 'var(--accent)' : 'var(--bg-1)',
                          color: (beh as any)[key] === o.v ? '#fff' : 'var(--text-3)',
                          border: `1px solid ${(beh as any)[key] === o.v ? 'var(--accent)' : 'var(--border-2)'}`,
                        }}>
                        {o.l}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </Card>

            {/* Verdict */}
            <Card style={{ padding: 14, marginBottom: 10 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Verdict</MonoLabel>
              <div style={{ display: 'flex', gap: 6 }}>
                {REVIEW_VERDICTS.map(v => (
                  <button key={v.id} onClick={() => setVerdict(v.id)}
                    style={{
                      padding: '6px 12px', fontSize: 11, borderRadius: 6, cursor: 'pointer', flex: 1,
                      background: verdict === v.id ? v.color : 'var(--bg-1)',
                      color: verdict === v.id ? '#fff' : 'var(--text-3)',
                      border: `1px solid ${verdict === v.id ? v.color : 'var(--border-2)'}`,
                      fontWeight: verdict === v.id ? 600 : 400,
                    }}>
                    <div>{v.grade}</div>
                    <div style={{ fontSize: 9 }}>{v.label}</div>
                  </button>
                ))}
              </div>
            </Card>

            {/* Feedback */}
            <Card style={{ padding: 14, marginBottom: 10 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 6 }}>Feedback (optional)</MonoLabel>
              <textarea value={feedback} onChange={e => setFeedback(e.target.value)} rows={3}
                placeholder="Additional notes about this evaluation..."
                style={textareaStyle} />
            </Card>

            {/* Submit */}
            <Button variant="primary" style={{ width: '100%' }} loading={submit.isPending}
              onClick={() => submit.mutate()} disabled={!verdict}>
              Save Review
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Observability ─────────────────────────────────────────────────────────────

function ObservabilityTab() {
  const [window_, setWindow] = useState(3600)
  const [logFilter, setLogFilter] = useState<{ session_id?: string; step?: string; status?: string }>({})

  const { data: summary, isLoading, refetch } = useQuery({
    queryKey: ['obs-summary', window_],
    queryFn: () => observabilityApi.summary(window_).then(r => r.data),
    staleTime: 15_000,
    refetchInterval: 15_000,
  })

  const { data: logs } = useQuery({
    queryKey: ['obs-logs', logFilter],
    queryFn: () => observabilityApi.logs({ ...logFilter, limit: 100 }).then(r => r.data),
    staleTime: 10_000,
    refetchInterval: 10_000,
  })

  const { data: health } = useQuery({
    queryKey: ['obs-health'],
    queryFn: () => observabilityApi.deepHealth().then(r => r.data),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const steps = summary?.by_step ? Object.keys(summary.by_step) : []

  return (
    <div>
      {/* Health banner */}
      {health && (
        <div style={{
          display: 'flex', gap: 14, marginBottom: 20, padding: '12px 18px',
          background: health.all_ok ? 'rgba(34,197,94,0.06)' : 'rgba(239,68,68,0.06)',
          border: `1px solid ${health.all_ok ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)'}`,
          borderRadius: 10, alignItems: 'center',
        }}>
          <span style={{ fontSize: 16 }}>{health.all_ok ? '●' : '●'}</span>
          <span style={{ fontSize: 13, color: health.all_ok ? 'var(--green, #22c55e)' : 'var(--red, #ef4444)', fontWeight: 600 }}>
            {health.status?.toUpperCase()}
          </span>
          {health.checks && Object.entries(health.checks).map(([name, check]: [string, any]) => (
            <span key={name} style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: check.ok ? 'var(--text-2)' : 'var(--red, #ef4444)' }}>
              {name}: {check.ok ? `${check.latency_ms}ms` : 'DOWN'}
            </span>
          ))}
          <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-3)', marginLeft: 'auto' }}>
            {health.active_sessions} active sessions
          </span>
        </div>
      )}

      {/* Summary controls */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, alignItems: 'center' }}>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', flex: 1 }}>
          Call tracking
        </h2>
        <select value={window_} onChange={e => setWindow(Number(e.target.value))} style={selectStyle}>
          <option value={600}>Last 10 min</option>
          <option value={3600}>Last 1 hour</option>
          <option value={86400}>Last 24 hours</option>
        </select>
        <Button variant="secondary" size="sm" onClick={() => refetch()}>Refresh</Button>
      </div>

      {/* Summary cards */}
      {isLoading ? <DashSkeleton /> : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginBottom: 20 }}>
            <Card style={{ padding: '14px 18px' }}>
              <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Total calls</MonoLabel>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 24, color: 'var(--text-0)' }}>{summary?.total_calls ?? 0}</p>
            </Card>
            <Card style={{ padding: '14px 18px' }}>
              <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Total cost</MonoLabel>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 24, color: 'var(--text-0)' }}>
                ${(summary?.total_cost_usd ?? 0).toFixed(4)}
              </p>
            </Card>
            <Card style={{ padding: '14px 18px' }}>
              <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Steps tracked</MonoLabel>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 24, color: 'var(--text-0)' }}>{steps.length}</p>
            </Card>
            <Card style={{ padding: '14px 18px' }}>
              <MonoLabel style={{ display: 'block', marginBottom: 8 }}>Window</MonoLabel>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 24, color: 'var(--text-0)' }}>
                {window_ < 3600 ? `${window_ / 60}m` : `${window_ / 3600}h`}
              </p>
            </Card>
          </div>

          {/* Per-step breakdown */}
          {steps.length > 0 && (
            <Card style={{ marginBottom: 20 }}>
              <MonoLabel style={{ display: 'block', marginBottom: 14 }}>By step</MonoLabel>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border-0)' }}>
                    {['Step', 'Calls', 'Success rate', 'P50', 'P95', 'Avg', 'Cost'].map(h => (
                      <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-3)', fontWeight: 400 }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {steps.map(step => {
                    const s = summary.by_step[step]
                    return (
                      <tr key={step} style={{ borderBottom: '1px solid var(--border-0)' }}>
                        <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-1)', fontWeight: 500 }}>{step}</td>
                        <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-2)' }}>{s.total_calls}</td>
                        <td style={{ padding: '8px 12px' }}>
                          <Badge variant={s.success_rate >= 0.95 ? 'green' : s.success_rate >= 0.8 ? 'orange' : 'red'}>
                            {(s.success_rate * 100).toFixed(0)}%
                          </Badge>
                        </td>
                        <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-2)' }}>{s.latency?.p50 ?? '—'}ms</td>
                        <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: s.latency?.p95 > 2000 ? 'var(--red, #ef4444)' : 'var(--text-2)' }}>{s.latency?.p95 ?? '—'}ms</td>
                        <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-2)' }}>{s.latency?.avg ?? '—'}ms</td>
                        <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--text-2)' }}>${s.cost_usd?.toFixed(4) ?? '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </Card>
          )}
        </>
      )}

      {/* Call logs */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 14, alignItems: 'center' }}>
        <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 16, color: 'var(--text-0)', flex: 1 }}>Recent call logs</h3>
        <select value={logFilter.step || ''} onChange={e => setLogFilter(f => ({ ...f, step: e.target.value || undefined }))} style={{ ...selectStyle, width: 'auto' }}>
          <option value="">All steps</option>
          <option value="LLM_question">LLM Question</option>
          <option value="LLM_evaluation">LLM Evaluation</option>
          <option value="STT">STT</option>
          <option value="TTS">TTS</option>
          <option value="resume_parsing">Resume Parsing</option>
        </select>
        <select value={logFilter.status || ''} onChange={e => setLogFilter(f => ({ ...f, status: e.target.value || undefined }))} style={{ ...selectStyle, width: 'auto' }}>
          <option value="">All status</option>
          <option value="success">Success</option>
          <option value="failure">Failure</option>
        </select>
      </div>
      <Card style={{ padding: 0, overflow: 'hidden', maxHeight: 400, overflowY: 'auto' }}>
        {!logs?.logs?.length ? (
          <EmptyState title="No call logs" body="LLM/STT/TTS call logs will appear here during active interviews." />
        ) : (
          <div>
            {logs.logs.map((log: any, i: number) => (
              <div key={i} style={{
                display: 'flex', gap: 10, padding: '8px 14px', fontSize: 11,
                borderBottom: '1px solid var(--border-0)', alignItems: 'center',
                fontFamily: 'var(--font-mono)',
              }}>
                <span style={{ color: 'var(--text-4)', width: 60, flexShrink: 0 }}>{log.formatted_time}</span>
                <Badge variant={log.status === 'success' ? 'green' : 'red'} style={{ flexShrink: 0 }}>{log.status}</Badge>
                <span style={{ color: 'var(--text-2)', width: 90, flexShrink: 0 }}>{log.step}</span>
                <span style={{ color: 'var(--text-3)', width: 110, flexShrink: 0 }}>{log.model}</span>
                <span style={{ color: log.latency_ms > 2000 ? 'var(--red, #ef4444)' : 'var(--text-1)', width: 60, flexShrink: 0, textAlign: 'right' }}>
                  {log.latency_ms}ms
                </span>
                <span style={{ color: 'var(--text-3)', width: 70, flexShrink: 0, textAlign: 'right' }}>
                  ${(log.cost_usd || 0).toFixed(5)}
                </span>
                <span style={{ color: 'var(--text-4)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {log.session_id?.slice(0, 8)}
                </span>
                {log.error && <span style={{ color: 'var(--red, #ef4444)', fontSize: 10 }}>{log.error}</span>}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}

// ── Shared ─────────────────────────────────────────────────────────────────────

function DashSkeleton() {
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginBottom: 24 }}>
        {[1,2,3,4,5,6,7,8].map(i => <Skeleton key={i} h={80} style={{ borderRadius: 12 }} />)}
      </div>
      <Skeleton h={200} style={{ borderRadius: 12 }} />
    </div>
  )
}

const selectStyle: React.CSSProperties = {
  background: 'var(--bg-0)', border: '1px solid var(--border-2)',
  borderRadius: 'var(--r-md)', padding: '7px 12px',
  fontSize: 12, color: 'var(--text-1)', fontFamily: 'var(--font-mono)',
  cursor: 'pointer', outline: 'none', width: '100%',
}

const textareaStyle: React.CSSProperties = {
  width: '100%', padding: '10px 12px', border: '1px solid var(--border-2)',
  borderRadius: 'var(--r-md)', fontSize: 12, fontFamily: 'var(--font-mono)',
  color: 'var(--text-1)', background: 'var(--bg-1)', resize: 'vertical',
  lineHeight: 1.6, outline: 'none',
}

const pillBtnStyle: React.CSSProperties = {
  padding: '5px 10px', background: 'var(--bg-1)', border: '1px solid var(--border-2)',
  borderRadius: 20, fontSize: 10, color: 'var(--text-2)', cursor: 'pointer',
  fontFamily: 'var(--font-mono)',
}
