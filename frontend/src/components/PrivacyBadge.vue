<script setup lang="ts">
/** Privacy level badge — shows S1/S2/S3 with appropriate color. */

import { NTag } from 'naive-ui'
import { computed } from 'vue'

const props = defineProps<{ level: string }>()

const tagType = computed(() => {
  switch (props.level) {
    case 'S1':
      return 'success'
    case 'S2':
      return 'warning'
    case 'S3':
      return 'error'
    default:
      return 'default'
  }
})

const label = computed(() => {
  switch (props.level) {
    case 'S1':
      return '无敏感'
    case 'S2':
      return '含敏感'
    case 'S3':
      return '高敏感'
    default:
      return props.level
  }
})
</script>

<template>
  <NTag :type="tagType" size="small" round>{{ label }}</NTag>
</template>
