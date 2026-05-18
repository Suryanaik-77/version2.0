import React from 'react'

interface Props {
  children: React.ReactNode
  fallback?: React.ReactNode
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] Caught error:', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div style={{
          minHeight: '100vh', background: 'var(--bg-1)',
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
          gap: 16, padding: 40, textAlign: 'center',
        }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.1em', color: 'var(--text-4)' }}>
            RUNTIME ERROR
          </span>
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 22, color: 'var(--text-0)' }}>
            Something went wrong
          </h2>
          <p style={{ fontSize: 13, color: 'var(--text-2)', maxWidth: 360, lineHeight: 1.6 }}>
            {this.state.error.message}
          </p>
          <button
            onClick={() => {
              this.setState({ error: null })
              window.location.reload()
            }}
            style={{
              background: 'var(--bg-0)', color: 'var(--text-1)',
              border: '1px solid var(--border-2)', borderRadius: 'var(--r-md)',
              padding: '8px 20px', fontSize: 13, cursor: 'pointer',
              fontFamily: 'var(--font-body)',
            }}
          >
            Reload page
          </button>
          <details style={{ maxWidth: 600, textAlign: 'left' }}>
            <summary style={{ fontSize: 11, color: 'var(--text-4)', cursor: 'pointer', fontFamily: 'var(--font-mono)' }}>
              Stack trace
            </summary>
            <pre style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-3)',
              background: 'var(--bg-2)', padding: '12px 16px', borderRadius: 8,
              overflow: 'auto', marginTop: 8, lineHeight: 1.5,
            }}>
              {this.state.error.stack}
            </pre>
          </details>
        </div>
      )
    }
    return this.props.children
  }
}
