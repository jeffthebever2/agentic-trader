import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          padding: 32, fontFamily: 'monospace', fontSize: 13,
          color: '#f87171', background: 'var(--canvas, #0f1117)',
          minHeight: '100vh', whiteSpace: 'pre-wrap',
        }}>
          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 12 }}>
            ⚠ App Error
          </div>
          <div style={{ marginBottom: 8, color: '#fbbf24' }}>
            {this.state.error.message}
          </div>
          <div style={{ color: '#94a3b8', fontSize: 11 }}>
            {this.state.error.stack}
          </div>
          <button
            style={{ marginTop: 20, padding: '8px 16px', background: '#1e293b',
                     color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6,
                     cursor: 'pointer', fontSize: 12 }}
            onClick={() => this.setState({ error: null })}
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
