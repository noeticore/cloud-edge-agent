<script setup lang="ts">
/** Single chat message bubble. */

import { NSpace } from 'naive-ui'
import PrivacyBadge from './PrivacyBadge.vue'
import ModeTag from './ModeTag.vue'
import type { ChatMessage } from '@/types'

defineProps<{ message: ChatMessage }>()
</script>

<template>
  <div
    :class="[
      'chat-msg',
      message.role === 'user' ? 'chat-msg--user' : 'chat-msg--assistant',
    ]"
  >
    <div class="chat-msg__avatar">
      {{ message.role === 'user' ? '👤' : '🤖' }}
    </div>
    <div class="chat-msg__body">
      <div class="chat-msg__content">{{ message.content }}</div>
      <div
        v-if="message.mode || message.privacy_level || message.latency_ms"
        class="chat-msg__meta"
      >
        <PrivacyBadge
          v-if="message.privacy_level"
          :level="message.privacy_level"
        />
        <ModeTag v-if="message.mode" :mode="message.mode" />
        <span v-if="message.latency_ms" class="chat-msg__latency">
          {{ message.latency_ms.toFixed(0) }}ms
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-msg {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
  align-items: flex-start;
}

.chat-msg--user {
  flex-direction: row-reverse;
}

.chat-msg__avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
}

.chat-msg--user .chat-msg__avatar {
  background: #dbeafe;
  border-color: #93c5fd;
}

.chat-msg__body {
  max-width: 72%;
  min-width: 60px;
}

.chat-msg__content {
  padding: 10px 14px;
  border-radius: 16px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
}

.chat-msg--user .chat-msg__content {
  background: #3b82f6;
  color: #ffffff;
  border-bottom-right-radius: 4px;
}

.chat-msg--assistant .chat-msg__content {
  background: #ffffff;
  color: #1f2937;
  border: 1px solid #e5e7eb;
  border-bottom-left-radius: 4px;
}

.chat-msg__meta {
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.chat-msg__latency {
  font-size: 11px;
  color: #9ca3af;
}
</style>
