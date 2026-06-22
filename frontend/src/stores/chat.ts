/** Chat state management with Pinia. */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage, StreamMetadataEvent } from '@/types'

export const useChatStore = defineStore('chat', () => {
  /** Current session ID. */
  const sessionId = ref<string | null>(null)

  /** Messages in the current session. */
  const messages = ref<ChatMessage[]>([])

  /** Whether a stream is in progress. */
  const isStreaming = ref(false)

  /** Latest routing metadata from the last response. */
  const lastMetadata = ref<StreamMetadataEvent | null>(null)

  function setSession(id: string) {
    sessionId.value = id
  }

  function addMessage(msg: ChatMessage) {
    messages.value.push(msg)
  }

  function appendToLastAssistant(token: string) {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant') {
      last.content += token
    }
  }

  function setStreaming(v: boolean) {
    isStreaming.value = v
  }

  function setMetadata(m: StreamMetadataEvent) {
    lastMetadata.value = m
  }

  function clearSession() {
    sessionId.value = null
    messages.value = []
    lastMetadata.value = null
  }

  return {
    sessionId,
    messages,
    isStreaming,
    lastMetadata,
    setSession,
    addMessage,
    appendToLastAssistant,
    setStreaming,
    setMetadata,
    clearSession,
  }
})
