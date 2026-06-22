<script setup lang="ts">
/** History page — browse past sessions and their messages. */

import { ref, onMounted } from 'vue'
import {
  NCard,
  NList,
  NListItem,
  NTag,
  NEmpty,
  NSpin,
  NAlert,
} from 'naive-ui'
import { listSessions, getSessionMessages } from '@/api/chat'
import PrivacyBadge from '@/components/PrivacyBadge.vue'
import ModeTag from '@/components/ModeTag.vue'
import type { SessionSummary, SessionMessage } from '@/types'

const sessions = ref<SessionSummary[]>([])
const selectedSession = ref<string | null>(null)
const messages = ref<SessionMessage[]>([])
const loading = ref(false)
const messagesLoading = ref(false)
const error = ref<string | null>(null)

onMounted(async () => {
  loading.value = true
  try {
    sessions.value = await listSessions()
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

async function selectSession(sessionId: string) {
  selectedSession.value = sessionId
  messagesLoading.value = true
  try {
    messages.value = await getSessionMessages(sessionId)
  } catch (e: any) {
    error.value = e.message
  } finally {
    messagesLoading.value = false
  }
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString('zh-CN')
}
</script>

<template>
  <div class="page">
    <div class="page__header">
      <span class="page__title">📜 历史会话</span>
    </div>
    <div class="page__body">
      <div class="history-layout">
        <!-- Session list -->
        <NCard title="会话列表" class="history-layout__sessions">
          <NSpin v-if="loading" />
          <NEmpty v-else-if="!sessions.length" description="暂无历史会话" />
          <NList v-else clickable>
            <NListItem
              v-for="s in sessions"
              :key="s.session_id"
              :class="{ 'session-active': selectedSession === s.session_id }"
              @click="selectSession(s.session_id)"
            >
              <div class="session-item">
                <div class="session-item__id">{{ s.session_id }}</div>
                <div class="session-item__preview">{{ s.preview || '无预览' }}</div>
                <div class="session-item__meta">
                  {{ formatTime(s.last_active) }} · {{ s.message_count }} 条消息
                </div>
              </div>
            </NListItem>
          </NList>
        </NCard>

        <!-- Messages -->
        <NCard
          :title="selectedSession ? `会话详情` : '选择一个会话'"
          class="history-layout__messages"
        >
          <NSpin v-if="messagesLoading" />
          <NEmpty
            v-else-if="!selectedSession"
            description="点击左侧会话查看对话记录"
          />
          <NEmpty
            v-else-if="!messages.length"
            description="该会话无对话记录"
          />
          <div v-else class="messages-list">
            <div
              v-for="msg in messages"
              :key="msg.entry_id"
              class="history-msg"
              :class="`history-msg--${msg.role}`"
            >
              <div class="history-msg__header">
                <NTag :type="msg.role === 'user' ? 'info' : 'success'" size="small">
                  {{ msg.role === 'user' ? '用户' : '助手' }}
                </NTag>
                <PrivacyBadge
                  v-if="msg.privacy_level"
                  :level="msg.privacy_level"
                />
                <ModeTag
                  v-if="msg.processing_mode"
                  :mode="msg.processing_mode"
                />
                <span class="history-msg__time">
                  {{ formatTime(msg.timestamp) }}
                </span>
              </div>
              <div class="history-msg__content">{{ msg.content }}</div>
            </div>
          </div>
        </NCard>
      </div>

      <NAlert v-if="error" type="error" style="margin-top: 16px">
        {{ error }}
      </NAlert>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

.page__header {
  padding: 12px 20px;
  border-bottom: 1px solid #e5e7eb;
  background: #ffffff;
  flex-shrink: 0;
}

.page__title {
  font-size: 16px;
  font-weight: 600;
}

.page__body {
  flex: 1;
  overflow: hidden;
  padding: 20px;
}

.history-layout {
  display: flex;
  gap: 16px;
  height: 100%;
}

.history-layout__sessions {
  width: 280px;
  flex-shrink: 0;
  overflow-y: auto;
}

.history-layout__messages {
  flex: 1;
  overflow-y: auto;
}

.session-item {
  cursor: pointer;
}

.session-item__id {
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-item__preview {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-item__meta {
  font-size: 11px;
  color: #9ca3af;
}

.session-active {
  background: #eff6ff !important;
}

.messages-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.history-msg {
  padding: 10px 14px;
  border-radius: 10px;
}

.history-msg--user {
  background: #f0f9ff;
  border: 1px solid #dbeafe;
}

.history-msg--assistant {
  background: #ffffff;
  border: 1px solid #e5e7eb;
}

.history-msg__header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.history-msg__time {
  font-size: 11px;
  color: #9ca3af;
  margin-left: auto;
}

.history-msg__content {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
