import { useEffect, useRef, useCallback } from 'react'
import { openWs, type WsMessage, type WsHandle } from '@/api/ws'

interface UseWebSocketOptions {
  onMessage: (msg: WsMessage) => void
  onClose?:  () => void
  enabled?:  boolean
}

export function useWebSocket(path: string, opts: UseWebSocketOptions) {
  const handleRef = useRef<WsHandle | null>(null)
  const { onMessage, onClose, enabled = false } = opts

  useEffect(() => {
    if (!enabled) return
    handleRef.current = openWs(path, onMessage, onClose)
    return () => { handleRef.current?.close() }
  }, [path, enabled])

  const send = useCallback((msg: unknown) => {
    handleRef.current?.send(msg)
  }, [])

  const close = useCallback(() => {
    handleRef.current?.close()
    handleRef.current = null
  }, [])

  return { send, close }
}
