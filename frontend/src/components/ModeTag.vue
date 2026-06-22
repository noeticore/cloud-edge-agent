<script setup lang="ts">
/** Collaborate mode tag — shows routing mode with label. */

import { NTag } from 'naive-ui'
import { computed } from 'vue'

const props = defineProps<{ mode: string }>()

const label = computed(() => {
  switch (props.mode) {
    case 'A':
    case 'direct_local':
      return '本地直答'
    case 'B':
    case 'direct_cloud':
      return '云端直答'
    case 'C':
    case 'sanitize_cloud':
      return '脱敏上云'
    case 'D':
    case 'sketch_refine':
      return '草稿精修'
    default:
      return props.mode
  }
})

const tagType = computed(() => {
  const m = props.mode
  if (m === 'A' || m === 'direct_local') return 'success'
  if (m === 'B' || m === 'direct_cloud') return 'info'
  if (m === 'C' || m === 'sanitize_cloud') return 'warning'
  if (m === 'D' || m === 'sketch_refine') return 'warning'
  return 'default'
})
</script>

<template>
  <NTag :type="tagType" size="small">{{ label }}</NTag>
</template>
