import React from 'react'
import { useToast, type Toast } from '@/hooks/useToast'

// ── Button ─────────────────────────────────────────────────────────────────────

type BtnVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline'
type BtnSize    = 'xs' | 'sm' | 'md' | 'lg'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant
  size?: BtnSize
  loading?: boolean
  icon?: React.ReactNode
  fullWidth?: boolean
}

const BTN_BASE: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 7,
  fontFamily: 'var(--font-body)', fontWeight: 500,
  border: '1px solid transparent', cursor: 'pointer',
  transition: 'all var(--dur-std) var(--ease-std)',
  letterSpacing: '0.01em', lineHeight: 1,
  textDecoration: 'none', whiteSpace: 'nowrap', flexShrink: 0,
  userSelect: 'none',
}
const BTN_VARIANTS: Record<BtnVariant, React.CSSProperties> = {
  primary:   { background: 'var(--accent)', color: '#fff', borderColor: 'var(--accent)' },
  secondary: { background: 'var(--bg-0)', color: 'var(--text-1)', borderColor: 'var(--border-2)', boxShadow: 'var(--shadow-xs)' },
  ghost:     { background: 'transparent', color: 'var(--text-2)', borderColor: 'transparent' },
  danger:    { background: 'var(--red-bg)', color: 'var(--red)', borderColor: 'var(--red-border)' },
  outline:   { background: 'transparent', color: 'var(--accent-dim)', borderColor: 'var(--accent-25)' },
}
const BTN_SIZES: Record<BtnSize, React.CSSProperties> = {
  xs: { padding: '4px 10px', fontSize: 11, borderRadius: 'var(--r-sm)', gap: 5 },
  sm: { padding: '6px 13px', fontSize: 12, borderRadius: 'var(--r-md)' },
  md: { padding: '9px 18px', fontSize: 13, borderRadius: 'var(--r-md)' },
  lg: { padding: '12px 26px', fontSize: 14, borderRadius: 'var(--r-lg)' },
}

export function Button({ variant = 'secondary', size = 'md', loading, icon, children, style, disabled, fullWidth, ...rest }: ButtonProps) {
  return (
    <button
      style={{
        ...BTN_BASE,
        ...BTN_VARIANTS[variant],
        ...BTN_SIZES[size],
        opacity: disabled || loading ? 0.5 : 1,
        width: fullWidth ? '100%' : undefined,
        cursor: disabled || loading ? 'not-allowed' : 'pointer',
        ...style,
      }}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Spinner size={13} /> : icon}
      {children}
    </button>
  )
}

// ── Spinner ────────────────────────────────────────────────────────────────────

export function Spinner({ size = 16, color }: { size?: number; color?: string }) {
  return (
    <span style={{
      width: size, height: size,
      border: `1.5px solid ${color ? color + '40' : 'var(--border-2)'}`,
      borderTopColor: color || 'var(--accent)',
      borderRadius: '50%',
      display: 'inline-block',
      animation: 'spin 0.65s linear infinite',
      flexShrink: 0,
    }} />
  )
}

// ── Badge ──────────────────────────────────────────────────────────────────────

type BadgeVariant = 'orange' | 'green' | 'red' | 'blue' | 'gray' | 'yellow'

export function Badge({ variant = 'gray', children, dot, live, style }: {
  variant?: BadgeVariant, children: React.ReactNode,
  dot?: boolean, live?: boolean, style?: React.CSSProperties
}) {
  const colors: Record<BadgeVariant, React.CSSProperties> = {
    orange: { color: 'var(--accent-dim)', background: 'var(--accent-8)',  borderColor: 'var(--accent-25)' },
    green:  { color: 'var(--green)',      background: 'var(--green-bg)',  borderColor: 'var(--green-border)' },
    red:    { color: 'var(--red)',        background: 'var(--red-bg)',    borderColor: 'var(--red-border)' },
    blue:   { color: 'var(--blue)',       background: 'var(--blue-bg)',   borderColor: 'var(--blue-border)' },
    yellow: { color: 'var(--yellow)',     background: 'var(--yellow-bg)', borderColor: 'var(--yellow-border)' },
    gray:   { color: 'var(--text-2)',     background: 'var(--bg-2)',      borderColor: 'var(--border-1)' },
  }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.06em',
      textTransform: 'uppercase', fontWeight: 500,
      padding: '3px 8px', borderRadius: 'var(--r-full)', border: '1px solid',
      ...colors[variant], ...style,
    }}>
      {dot && <span style={{
        width: 5, height: 5, borderRadius: '50%', background: 'currentColor', flexShrink: 0,
        animation: live ? 'pulse 1.8s ease-in-out infinite' : undefined,
      }} />}
      {children}
    </span>
  )
}

// ── Input ──────────────────────────────────────────────────────────────────────

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string; error?: string; hint?: string; icon?: React.ReactNode
}

export function Input({ label, error, hint, icon, style, id, ...rest }: InputProps) {
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-')
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      {label && (
        <label htmlFor={inputId} style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-1)', letterSpacing: '0.01em' }}>
          {label}
        </label>
      )}
      <div style={{ position: 'relative' }}>
        {icon && (
          <span style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-3)', pointerEvents: 'none', display: 'flex' }}>
            {icon}
          </span>
        )}
        <input
          id={inputId}
          style={{
            width: '100%', padding: icon ? '9px 12px 9px 34px' : '9px 12px',
            fontFamily: 'var(--font-body)', fontSize: 13, color: 'var(--text-0)',
            background: 'var(--bg-0)', border: `1px solid ${error ? 'var(--red-border)' : 'var(--border-2)'}`,
            borderRadius: 'var(--r-md)', outline: 'none',
            transition: 'border-color var(--dur-fast)',
            ...style,
          }}
          onFocus={e => e.currentTarget.style.borderColor = error ? 'var(--red)' : 'var(--accent)'}
          onBlur={e  => e.currentTarget.style.borderColor = error ? 'var(--red-border)' : 'var(--border-2)'}
          {...rest}
        />
      </div>
      {error && <span style={{ fontSize: 11, color: 'var(--red)', display: 'flex', alignItems: 'center', gap: 4 }}>⚠ {error}</span>}
      {hint && !error && <span style={{ fontSize: 11, color: 'var(--text-3)' }}>{hint}</span>}
    </div>
  )
}

// ── Card ───────────────────────────────────────────────────────────────────────

export function Card({ children, style, onClick, hover }: {
  children: React.ReactNode; style?: React.CSSProperties;
  onClick?: () => void; hover?: boolean
}) {
  return (
    <div
      onClick={onClick}
      style={{
        background: 'var(--bg-0)', border: '1px solid var(--border-1)',
        borderRadius: 'var(--r-lg)', padding: '20px 22px',
        boxShadow: 'var(--shadow-card)',
        cursor: onClick ? 'pointer' : undefined,
        transition: hover || onClick ? 'border-color var(--dur-std), box-shadow var(--dur-std)' : undefined,
        ...style,
      }}
    >
      {children}
    </div>
  )
}

// ── Skeleton ───────────────────────────────────────────────────────────────────

export function Skeleton({ w, h, style }: { w?: number | string; h?: number | string; style?: React.CSSProperties }) {
  return (
    <div style={{
      width: w, height: h || 14, borderRadius: 'var(--r-md)',
      background: 'linear-gradient(90deg, var(--bg-2) 25%, var(--bg-3) 50%, var(--bg-2) 75%)',
      backgroundSize: '400% 100%',
      animation: 'shimmer 1.6s ease-in-out infinite',
      ...style,
    }} />
  )
}

// ── ScoreBar ───────────────────────────────────────────────────────────────────

export function ScoreBar({ label, score, max = 10, showNumber = true }: {
  label: string; score: number; max?: number; showNumber?: boolean
}) {
  const pct = Math.round((score / max) * 100)
  const color = score >= 7 ? 'var(--green)' : score >= 5 ? 'var(--accent)' : 'var(--red)'
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
        <span style={{ fontSize: 11, color: 'var(--text-2)', letterSpacing: '0.01em' }}>{label}</span>
        {showNumber && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color, fontWeight: 500 }}>
            {score.toFixed(1)}
          </span>
        )}
      </div>
      <div style={{ height: 3, background: 'var(--bg-3)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 2, transition: 'width 0.5s var(--ease-dec)' }} />
      </div>
    </div>
  )
}

// ── MonoLabel ──────────────────────────────────────────────────────────────────

export function MonoLabel({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.08em',
      textTransform: 'uppercase', color: 'var(--text-3)', ...style,
    }}>
      {children}
    </span>
  )
}

// ── ModeTag ────────────────────────────────────────────────────────────────────

const MODE_STYLES: Record<string, React.CSSProperties> = {
  PROBING:       { color: 'var(--blue)',   background: 'var(--blue-bg)',   borderColor: 'var(--blue-border)' },
  DEEPENING:     { color: 'var(--green)',  background: 'var(--green-bg)',  borderColor: 'var(--green-border)' },
  ESCALATING:    { color: 'var(--yellow)', background: 'var(--yellow-bg)', borderColor: 'var(--yellow-border)' },
  PRESSURE:      { color: 'var(--red)',    background: 'var(--red-bg)',    borderColor: 'var(--red-border)' },
  RECOVERING:    { color: 'var(--accent-dim)', background: 'var(--accent-8)', borderColor: 'var(--accent-15)' },
  TRANSITIONING: { color: 'var(--text-2)', background: 'var(--bg-2)',     borderColor: 'var(--border-1)' },
}
export function ModeTag({ mode }: { mode: string }) {
  const s = MODE_STYLES[mode] || MODE_STYLES.TRANSITIONING
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.08em', textTransform: 'uppercase',
      padding: '2px 7px', borderRadius: 'var(--r-full)', border: '1px solid', ...s,
    }}>
      {mode}
    </span>
  )
}

// ── Divider ────────────────────────────────────────────────────────────────────

export function Divider({ style }: { style?: React.CSSProperties }) {
  return <div style={{ height: 1, background: 'var(--border-0)', ...style }} />
}

// ── EmptyState ────────────────────────────────────────────────────────────────

export function EmptyState({ icon, title, body, action }: {
  icon?: string; title: string; body?: string;
  action?: { label: string; onClick: () => void }
}) {
  return (
    <div style={{ padding: '48px 32px', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
      {icon && <span style={{ fontSize: 28, marginBottom: 4 }}>{icon}</span>}
      <p style={{ fontFamily: 'var(--font-display)', fontSize: 18, color: 'var(--text-0)' }}>{title}</p>
      {body && <p style={{ fontSize: 13, color: 'var(--text-3)', maxWidth: 340, lineHeight: 1.65 }}>{body}</p>}
      {action && (
        <Button variant="secondary" size="sm" onClick={action.onClick} style={{ marginTop: 8 }}>
          {action.label}
        </Button>
      )}
    </div>
  )
}

// ── SectionHeader ─────────────────────────────────────────────────────────────

export function SectionHeader({ title, subtitle, action }: {
  title: string; subtitle?: string; action?: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, marginBottom: 20 }}>
      <div>
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 22, color: 'var(--text-0)', marginBottom: subtitle ? 4 : 0 }}>{title}</h2>
        {subtitle && <p style={{ fontSize: 13, color: 'var(--text-2)' }}>{subtitle}</p>}
      </div>
      {action && <div style={{ flexShrink: 0 }}>{action}</div>}
    </div>
  )
}

// ── StatCard ───────────────────────────────────────────────────────────────────

export function StatCard({ label, value, unit, accent }: {
  label: string; value: string | number; unit?: string; accent?: boolean
}) {
  return (
    <Card style={{ padding: '16px 20px' }}>
      <MonoLabel style={{ display: 'block', marginBottom: 10 }}>{label}</MonoLabel>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 26, lineHeight: 1,
          color: accent ? 'var(--accent)' : 'var(--text-0)', fontWeight: 300,
        }}>{value}</span>
        {unit && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-3)' }}>{unit}</span>}
      </div>
    </Card>
  )
}

// ── PageContainer ─────────────────────────────────────────────────────────────

export function PageContainer({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{ padding: '40px 48px', maxWidth: 'var(--content-max)', margin: '0 auto', ...style }}>
      {children}
    </div>
  )
}

// ── ToastContainer ────────────────────────────────────────────────────────────

export function ToastContainer() {
  const { toasts, dismiss } = useToast()
  if (!toasts.length) return null
  return (
    <div style={{ position: 'fixed', bottom: 24, right: 24, zIndex: 9999, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {toasts.map(t => <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />)}
    </div>
  )
}

const TOAST_COLORS = {
  success: { bg: 'var(--green-bg)',  border: 'var(--green-border)',  icon: '✓', color: 'var(--green)' },
  error:   { bg: 'var(--red-bg)',    border: 'var(--red-border)',    icon: '✕', color: 'var(--red)' },
  warning: { bg: 'var(--yellow-bg)', border: 'var(--yellow-border)', icon: '!', color: 'var(--yellow)' },
  info:    { bg: 'var(--bg-0)',      border: 'var(--border-1)',      icon: 'i', color: 'var(--blue)' },
}
function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const c = TOAST_COLORS[toast.type]
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      padding: '12px 14px', borderRadius: 'var(--r-lg)',
      background: c.bg, border: `1px solid ${c.border}`,
      boxShadow: 'var(--shadow-md)', animation: 'toast-in 0.2s var(--ease-dec)',
      minWidth: 280, maxWidth: 380,
    }}>
      <span style={{ width: 18, height: 18, borderRadius: '50%', background: c.color, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, flexShrink: 0 }}>
        {c.icon}
      </span>
      <span style={{ fontSize: 13, color: 'var(--text-1)', flex: 1 }}>{toast.message}</span>
      <button onClick={onDismiss} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-4)', fontSize: 14, padding: '0 2px', lineHeight: 1 }}>×</button>
    </div>
  )
}
