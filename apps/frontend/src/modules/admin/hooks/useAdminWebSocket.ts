import { useEffect, useRef, useState } from 'react'
import { io, Socket } from 'socket.io-client'

import { getToken } from '../../../services/api'

export type AdminEvent =
  | { type: 'worker_status'; tenant_schema: string; status: string }
  | { type: 'training_approval'; id: string; status: string }
  | { type: 'ticket_created'; id: string; subject: string }
  | { type: 'announcement'; id: string; title: string }

interface UseAdminWebSocketOptions {
  enabled?: boolean
  onEvent?: (event: AdminEvent) => void
}

export function useAdminWebSocket({ enabled = true, onEvent }: UseAdminWebSocketOptions = {}) {
  const [connected, setConnected] = useState(false)
  const socketRef = useRef<Socket | null>(null)

  useEffect(() => {
    if (!enabled) return

    const token = getToken() ?? ''
    const socket = io('/admin', {
      auth: { token },
      transports: ['websocket'],
      reconnectionDelay: 2000,
      reconnectionAttempts: 5,
    })
    socketRef.current = socket

    socket.on('connect', () => setConnected(true))
    socket.on('disconnect', () => setConnected(false))

    const events: AdminEvent['type'][] = [
      'worker_status',
      'training_approval',
      'ticket_created',
      'announcement',
    ]
    for (const evt of events) {
      socket.on(evt, (payload: Omit<AdminEvent, 'type'>) => {
        onEvent?.({ type: evt, ...payload } as AdminEvent)
      })
    }

    return () => { socket.disconnect(); socketRef.current = null }
  }, [enabled, onEvent])

  return { connected, socket: socketRef.current }
}
