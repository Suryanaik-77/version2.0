import React from 'react'
import { Badge } from '@/components/ui'
import type { WsStatus } from '@/stores/interview'

export default function ConnectionStatus({ status }: { status: WsStatus }) {
  const config: Record<WsStatus, { variant: 'green' | 'orange' | 'red' | 'gray'; label: string; live?: boolean }> = {
    connected:     { variant: 'green',  label: 'Connected',    live: true },
    connecting:    { variant: 'orange', label: 'Connecting…',  live: true },
    reconnecting:  { variant: 'orange', label: 'Reconnecting', live: true },
    disconnected:  { variant: 'gray',   label: 'Disconnected' },
    ended:         { variant: 'gray',   label: 'Session ended' },
  }
  const c = config[status] || config.disconnected
  return <Badge variant={c.variant} dot={!!c.live} live={c.live}>{c.label}</Badge>
}
