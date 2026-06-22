<script setup lang="ts">
/** Left sidebar navigation. */

import { NMenu, NLayoutSider, NSpace, NText } from 'naive-ui'
import { h, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  ChatbubbleOutline,
  DocumentsOutline,
  PulseOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import type { MenuOption } from 'naive-ui'

const router = useRouter()
const route = useRoute()
const collapsed = ref(false)

function renderIcon(icon: any) {
  return () => h(icon, { size: 18 })
}

const menuOptions: MenuOption[] = [
  {
    label: '对话',
    key: '/chat',
    icon: renderIcon(ChatbubbleOutline),
  },
  {
    label: '文档管理',
    key: '/documents',
    icon: renderIcon(DocumentsOutline),
  },
  {
    label: '系统状态',
    key: '/status',
    icon: renderIcon(PulseOutline),
  },
  {
    label: '历史会话',
    key: '/history',
    icon: renderIcon(TimeOutline),
  },
]

function handleMenuUpdate(key: string) {
  router.push(key)
}
</script>

<template>
  <NLayoutSider
    bordered
    collapse-mode="width"
    :collapsed-width="64"
    :width="200"
    :collapsed="collapsed"
    show-trigger
    @collapse="collapsed = true"
    @expand="collapsed = false"
    style="height: 100vh"
  >
    <div style="padding: 16px; text-align: center">
      <NText v-if="!collapsed" strong style="font-size: 16px">
        🛡️ CloudEdge Agent
      </NText>
      <NText v-else strong>🛡️</NText>
    </div>
    <NMenu
      :collapsed="collapsed"
      :collapsed-width="64"
      :collapsed-icon-size="22"
      :options="menuOptions"
      :value="route.path"
      @update:value="handleMenuUpdate"
    />
  </NLayoutSider>
</template>
