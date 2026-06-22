<script setup lang="ts">
/** Root component — layout with sidebar and router view. */

import { NLayout, NLayoutContent, NConfigProvider, NMessageProvider } from 'naive-ui'
import Sidebar from '@/components/Sidebar.vue'
</script>

<template>
  <NConfigProvider>
    <NMessageProvider>
      <NLayout has-sider style="height: 100vh">
        <Sidebar />
        <NLayoutContent
          content-style="height: 100vh; display: flex; flex-direction: column;"
          style="height: 100vh; overflow: hidden;"
        >
          <RouterView />
        </NLayoutContent>
      </NLayout>
    </NMessageProvider>
  </NConfigProvider>
</template>

<style>
/* Global reset */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body, #app {
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
    'Helvetica Neue', Arial, sans-serif;
  color: #1f2937;
  background: #f9fafb;
}

/* Naive UI overrides for consistency */
.n-layout-sider {
  background: #ffffff !important;
}

.n-layout-content {
  background: #f9fafb !important;
}
</style>
