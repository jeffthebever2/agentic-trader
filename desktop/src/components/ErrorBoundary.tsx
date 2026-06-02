import { Component, type ReactNode } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center gap-4 py-20 px-6 text-center">
          <AlertTriangle className="h-10 w-10 text-red-400" />
          <div>
            <p className="text-white font-semibold">Page crashed</p>
            <p className="text-sm text-gray-400 mt-1 max-w-sm">{this.state.error.message}</p>
          </div>
          <button
            onClick={() => this.setState({ error: null })}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-800 text-gray-300 text-sm hover:bg-gray-700 transition"
          >
            <RefreshCw className="h-4 w-4" />
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
