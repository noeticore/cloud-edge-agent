<script setup lang="ts">
/** Chat input box with send button. */

import { NInput, NButton } from 'naive-ui'
import { ref } from 'vue'

const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ send: [text: string] }>()

const inputText = ref('')

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.disabled) return
  emit('send', text)
  inputText.value = ''
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="chat-input">
    <div class="chat-input__row">
      <NInput
        v-model:value="inputText"
        placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
        type="textarea"
        :rows="2"
        :disabled="disabled"
        @keydown="handleKeydown"
        class="chat-input__field"
      />
      <NButton
        type="primary"
        :disabled="disabled || !inputText.trim()"
        @click="handleSend"
        class="chat-input__btn"
      >
        发送
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.chat-input {
  padding: 12px 20px;
  border-top: 1px solid #e5e7eb;
  background: #ffffff;
  flex-shrink: 0;
}

.chat-input__row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  max-width: 800px;
  margin: 0 auto;
}

.chat-input__field {
  flex: 1;
}

.chat-input__btn {
  height: 40px;
  min-width: 72px;
}
</style>
