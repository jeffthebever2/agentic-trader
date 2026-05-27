import { wsUrl } from './client'

export type WsMessage = { type: string; data?: unknown; error?: string; text?: string }

export interface WsHandle {
  send: (msg: unknown) => void
  close: () => void
}

/**
 * Open a WebSocket and stream messages to a callback.
 * Returns a handle with send() and close() methods.
 */
export function openWs(
  path: string,
  onMessage: (msg: WsMessage) => void,
  onClose?: () => void,
): WsHandle {
  const url = wsUrl(path)
  const ws  = new WebSocket(url)

  ws.onmessage = ev => {
    try {
      const msg = JSON.parse(ev.data) as WsMessage
      onMessage(msg)
    } catch {
      onMessage({ type: 'raw', text: ev.data })
    }
  }

  ws.onerror = () => onMessage({ type: 'error', error: 'WebSocket error' })
  ws.onclose = () => { onClose?.() }

  return {
    send: (msg: unknown) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(typeof msg === 'string' ? msg : JSON.stringify(msg))
      }
    },
    close: () => ws.close(),
  }
}

// Named paths
export const WS_ANALYZE     = '/ws/analyze'
export const WS_BACKTEST    = '/ws/backtest'
export const WS_ALGO_BT     = '/ws/algo-backtest'
export const WS_ML_TRAIN    = '/ws/ml-train'
export const WS_RL_TRAIN    = '/ws/rl-train'
export const WS_SCANNER     = '/ws/scanner/scan'
export const WS_FIDELITY    = '/ws/fidelity-auth'
