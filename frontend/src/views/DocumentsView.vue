<script setup lang="ts">
/** Documents page — upload text and search RAG knowledge base. */

import { ref } from 'vue'
import {
  NCard,
  NInput,
  NButton,
  NList,
  NListItem,
  NTag,
  NAlert,
  NP,
} from 'naive-ui'
import { uploadDocument, searchDocuments } from '@/api/documents'
import type { DocumentSearchResult } from '@/types'

const uploadText = ref('')
const uploadLoading = ref(false)
const uploadResult = ref<string | null>(null)
const uploadError = ref<string | null>(null)

const searchQuery = ref('')
const searchLoading = ref(false)
const searchResults = ref<DocumentSearchResult[]>([])
const searchError = ref<string | null>(null)

async function handleUpload() {
  if (!uploadText.value.trim()) return
  uploadLoading.value = true
  uploadResult.value = null
  uploadError.value = null
  try {
    const res = await uploadDocument({ text: uploadText.value })
    uploadResult.value = `上传成功：文档 ${res.doc_id}，共 ${res.chunk_count} 个分块`
    uploadText.value = ''
  } catch (e: any) {
    uploadError.value = e.response?.data?.detail || e.message || '上传失败'
  } finally {
    uploadLoading.value = false
  }
}

async function handleSearch() {
  if (!searchQuery.value.trim()) return
  searchLoading.value = true
  searchResults.value = []
  searchError.value = null
  try {
    const res = await searchDocuments(searchQuery.value)
    searchResults.value = res.results
  } catch (e: any) {
    searchError.value = e.response?.data?.detail || e.message || '搜索失败'
  } finally {
    searchLoading.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="page__header">
      <span class="page__title">📚 文档管理</span>
    </div>
    <div class="page__body">
      <!-- Upload -->
      <NCard title="上传文档到知识库" style="margin-bottom: 20px">
        <NP depth="3" style="margin-bottom: 12px">
          输入文本内容，系统会自动分块、向量化并存入 Qdrant 知识库。
        </NP>
        <NInput
          v-model:value="uploadText"
          type="textarea"
          :rows="4"
          placeholder="粘贴文档内容..."
        />
        <div style="margin-top: 12px">
          <NButton
            type="primary"
            :loading="uploadLoading"
            :disabled="!uploadText.trim()"
            @click="handleUpload"
          >
            上传
          </NButton>
        </div>
        <NAlert
          v-if="uploadResult"
          type="success"
          style="margin-top: 12px"
          closable
          @close="uploadResult = null"
        >
          {{ uploadResult }}
        </NAlert>
        <NAlert
          v-if="uploadError"
          type="error"
          style="margin-top: 12px"
          closable
          @close="uploadError = null"
        >
          {{ uploadError }}
        </NAlert>
      </NCard>

      <!-- Search -->
      <NCard title="搜索知识库">
        <div class="search-row">
          <NInput
            v-model:value="searchQuery"
            placeholder="输入搜索内容..."
            @keyup.enter="handleSearch"
            class="search-row__input"
          />
          <NButton
            type="primary"
            :loading="searchLoading"
            :disabled="!searchQuery.trim()"
            @click="handleSearch"
          >
            搜索
          </NButton>
        </div>

        <NAlert
          v-if="searchError"
          type="error"
          style="margin-top: 12px"
        >
          {{ searchError }}
        </NAlert>

        <NList v-if="searchResults.length" style="margin-top: 16px" bordered>
          <NListItem v-for="(r, i) in searchResults" :key="i">
            <div class="search-result">
              <div class="search-result__content">{{ r.content }}</div>
              <NTag type="info" size="small" class="search-result__score">
                {{ (r.score * 100).toFixed(1) }}%
              </NTag>
            </div>
          </NListItem>
        </NList>

        <NP
          v-else-if="searchQuery && !searchLoading && !searchError"
          depth="3"
          style="margin-top: 16px"
        >
          无搜索结果
        </NP>
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

.search-row {
  display: flex;
  gap: 10px;
  align-items: center;
}

.search-row__input {
  flex: 1;
}

.search-result {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.search-result__content {
  flex: 1;
  white-space: pre-wrap;
  font-size: 14px;
  line-height: 1.5;
}

.search-result__score {
  flex-shrink: 0;
}
</style>
