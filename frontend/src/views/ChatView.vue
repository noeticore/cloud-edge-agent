<script setup lang="ts">
/** Chat page — core conversational interface with SSE streaming. */

import { ref, nextTick, onMounted } from 'vue'
import { NSpin } from 'naive-ui'
import ChatMessageComponent from '@/components/ChatMessage.vue'
import ChatInput from '@/components/ChatInput.vue'
import { sendMessageStream } from '@/api/chat'
import { useChatStore } from '@/stores/chat'
import type { ChatMessage, StreamEvent } from '@/types'

const chatStore = useChatStore()
const messageListRef = ref<HTMLDivElement | null>(null)
const error = ref<string | null>(null)

function scrollToBottom() {
  nextTick(() => {
    if (messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight
    }
  })
}

async function handleSend(text: string) {
  error.value = null

  const userMsg: ChatMessage = {
    role: 'user',
    content: text,
    timestamp: Date.now(),
  }
  chatStore.addMessage(userMsg)
  scrollToBottom()

  const assistantMsg: ChatMessage = {
    role: 'assistant',
    content: '',
    timestamp: Date.now(),
  }
  chatStore.addMessage(assistantMsg)
  chatStore.setStreaming(true)
  scrollToBottom()

  try {
    await sendMessageStream(
      { query: text, session_id: chatStore.sessionId },
      (event: StreamEvent) => {
        switch (event.type) {
          case 'metadata':
            chatStore.setSession(event.session_id)
            chatStore.setMetadata(event)
            const msgs = chatStore.messages
            const last = msgs[msgs.length - 1]
            if (last && last.role === 'assistant') {
              last.mode = event.mode
              last.privacy_level = event.privacy_level
              last.complexity = event.complexity
            }
            break
          case 'token':
            chatStore.appendToLastAssistant(event.delta ?? event.token ?? '')
            scrollToBottom()
            break
          case 'done':
            const msgs2 = chatStore.messages
            const last2 = msgs2[msgs2.length - 1]
            if (last2 && last2.role === 'assistant') {
              last2.latency_ms = event.latency_ms
              if (!last2.content && event.answer) {
                last2.content = event.answer
              }
            }
            break
          case 'error':
            error.value = event.error
            break
        }
      },
    )
  } catch (e: any) {
    error.value = e.message || '请求失败'
    const msgs = chatStore.messages
    if (msgs.length && msgs[msgs.length - 1].content === '') {
      msgs.pop()
    }
  } finally {
    chatStore.setStreaming(false)
    scrollToBottom()
  }
}

onMounted(() => {
  if (!chatStore.messages.length) {
    chatStore.addMessage({
      role: 'assistant',
      content: '你好！我是 CloudEdge Agent，一个隐私优先的云边协同AI助手。有什么可以帮你的？',
      timestamp: Date.now(),
    })
  }
})
</script>

<template>
  <div class="chat-view">
    <!-- Header -->
    <div class="chat-view__header">
      <span class="chat-view__title">💬 对话</span>
      <span v-if="chatStore.sessionId" class="chat-view__session">
        会话: {{ chatStore.sessionId }}
      </span>
    </div>

    <!-- Messages -->
    <div class="chat-view__messages" ref="messageListRef">
      <div class="chat-view__messages-inner">
        <ChatMessageComponent
          v-for="(msg, i) in chatStore.messages"
          :key="i"
          :message="msg"
        />
        <div v-if="chatStore.isStreaming" class="chat-view__loading">
          <NSpin size="small" /> 思考中...
        </div>
        <div v-if="error" class="chat-view__error">
          ⚠️ {{ error }}
        </div>
      </div>
    </div>

    <!-- Input -->
    <ChatInput
      :disabled="chatStore.isStreaming"
      @send="handleSend"
    />
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

.chat-view__header {
  padding: 12px 20px;
  border-bottom: 1px solid #e5e7eb;
  background: #ffffff;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.chat-view__title {
  font-size: 16px;
  font-weight: 600;
}

.chat-view__session {
  font-size: 12px;
  color: #9ca3af;
}

.chat-view__messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: #f9fafb;
}

.chat-view__messages-inner {
  max-width: 800px;
  margin: 0 auto;
}

.chat-view__loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  color: #6b7280;
  font-size: 14px;
}

.chat-view__error {
  padding: 10px 14px;
  color: #dc2626;
  font-size: 14px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  margin: 8px 0;
}
</style>
