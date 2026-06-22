<script setup lang="ts">
/** System status page — shows health and latest routing decisions. */

import { ref, onMounted } from 'vue'
import {
  NCard,
  NDescriptions,
  NDescriptionsItem,
  NTag,
  NAlert,
  NList,
  NListItem,
  NSpace,
} from 'naive-ui'
import { checkHealth } from '@/api/health'
import { useChatStore } from '@/stores/chat'
import PrivacyBadge from '@/components/PrivacyBadge.vue'
import ModeTag from '@/components/ModeTag.vue'

const chatStore = useChatStore()
const healthStatus = ref<string>('checking...')
const healthVersion = ref<string>('')
const healthError = ref<string | null>(null)

onMounted(async () => {
  try {
    const res = await checkHealth()
    healthStatus.value = res.status
    healthVersion.value = res.version
  } catch (e: any) {
    healthStatus.value = 'unreachable'
    healthError.value = e.message
  }
})
</script>

<template>
  <div class="page">
    <div class="page__header">
      <span class="page__title">📊 系统状态</span>
    </div>
    <div class="page__body">
      <!-- Health -->
      <NCard title="后端健康状态" style="margin-bottom: 20px">
        <NDescriptions bordered :column="2">
          <NDescriptionsItem label="状态">
            <NTag :type="healthStatus === 'ok' ? 'success' : 'error'">
              {{ healthStatus }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="版本">
            {{ healthVersion || '-' }}
          </NDescriptionsItem>
        </NDescriptions>
        <NAlert v-if="healthError" type="error" style="margin-top: 12px">
          {{ healthError }}
        </NAlert>
      </NCard>

      <!-- Latest routing decision -->
      <NCard title="最近一次路由决策">
        <template v-if="chatStore.lastMetadata">
          <NDescriptions bordered :column="2">
            <NDescriptionsItem label="会话ID">
              {{ chatStore.sessionId }}
            </NDescriptionsItem>
            <NDescriptionsItem label="隐私等级">
              <PrivacyBadge :level="chatStore.lastMetadata.privacy_level" />
            </NDescriptionsItem>
            <NDescriptionsItem label="复杂度">
              {{ chatStore.lastMetadata.complexity }}
            </NDescriptionsItem>
            <NDescriptionsItem label="路由模式">
              <ModeTag :mode="chatStore.lastMetadata.mode" />
            </NDescriptionsItem>
          </NDescriptions>

          <div style="margin-top: 20px">
            <div style="font-weight: 600; margin-bottom: 10px">模式说明：</div>
            <NList bordered>
              <NListItem>
                <div class="mode-item">
                  <ModeTag mode="A" />
                  <span>本地直答 — 无敏感数据 + 低复杂度，全部在边缘完成</span>
                </div>
              </NListItem>
              <NListItem>
                <div class="mode-item">
                  <ModeTag mode="B" />
                  <span>云端直答 — 无敏感数据 + 高复杂度，直接调用云端 LLM</span>
                </div>
              </NListItem>
              <NListItem>
                <div class="mode-item">
                  <ModeTag mode="C" />
                  <span>脱敏上云 — 含敏感数据 + 高复杂度，脱敏后发云端，还原答案</span>
                </div>
              </NListItem>
              <NListItem>
                <div class="mode-item">
                  <ModeTag mode="D" />
                  <span>草稿精修 — 含敏感数据 + 极高复杂度，本地草稿 + 云端精修</span>
                </div>
              </NListItem>
            </NList>
          </div>
        </template>
        <template v-else>
          <NAlert type="info">
            暂无路由决策数据。请先在对话页面发送一条消息。
          </NAlert>
        </template>
      </NCard>
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
  overflow-y: auto;
  padding: 20px;
  max-width: 800px;
  margin: 0 auto;
  width: 100%;
}

.mode-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
