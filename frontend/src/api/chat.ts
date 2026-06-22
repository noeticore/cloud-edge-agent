/** Chat API — sync and SSE streaming. */

import client from './client'
import type {
  ChatRequest,
  ChatResponse,
  StreamEvent,
  SessionSummary,
  SessionMessage,
} from '@/types'

/** Send a message (non-streaming). */
export async function sendMessage(req: ChatRequest): Promise<ChatResponse> {
  const { data } = await client.post<ChatResponse>('/chat', req)
  return data
}

/**
 * Send a message with SSE streaming.
 * Uses fetch + ReadableStream because EventSource only supports GET.
 */
export async function sendMessageStream(
  req: ChatRequest,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })

  if (!response.ok) {
    const text = await response.text()
    throw new Error(`Stream error ${response.status}: ${text}`)
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed.startsWith('data: ')) continue
      const jsonStr = trimmed.slice(6)
      if (!jsonStr) continue
      try {
        const event = JSON.parse(jsonStr) as StreamEvent
        onEvent(event)
      } catch {
        // skip malformed lines
      }
    }
  }
}

/** List all sessions. */
export async function listSessions(): Promise<SessionSummary[]> {
  const { data } = await client.get('/chat/sessions')
  return data.sessions
}

/** Get messages for a specific session. */
export async function getSessionMessages(
  sessionId: string,
): Promise<SessionMessage[]> {
  const { data } = await client.get(
    `/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
  )
  return data.messages
}
