/** Type definitions aligned with backend API schemas. */

// --- Chat ---

export interface ChatRequest {
  query: string
  session_id?: string | null
}

export interface ChatResponse {
  answer: string
  session_id: string
  mode: string
  privacy_level: string
  complexity: number
  latency_ms: number
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  mode?: string
  privacy_level?: string
  complexity?: number
  latency_ms?: number
}

// --- SSE stream events ---

export interface StreamMetadataEvent {
  type: 'metadata'
  session_id: string
  mode: string
  privacy_level: string
  complexity: number
}

export interface StreamTokenEvent {
  type: 'token'
  delta: string
  token?: string
}

export interface StreamDoneEvent {
  type: 'done'
  answer: string
  latency_ms: number
}

export interface StreamErrorEvent {
  type: 'error'
  error: string
}

export type StreamEvent =
  | StreamMetadataEvent
  | StreamTokenEvent
  | StreamDoneEvent
  | StreamErrorEvent

// --- Documents ---

export interface DocumentUploadRequest {
  text: string
  metadata?: Record<string, unknown> | null
  doc_id?: string | null
}

export interface DocumentUploadResponse {
  doc_id: string
  chunk_count: number
  chunk_ids: string[]
}

export interface DocumentSearchResult {
  content: string
  score: number
  metadata: Record<string, unknown>
}

export interface DocumentSearchResponse {
  query: string
  results: DocumentSearchResult[]
  count: number
}

// --- Sessions (for history) ---

export interface SessionSummary {
  session_id: string
  last_active: number
  message_count: number
  preview: string
}

export interface SessionMessage {
  entry_id: string
  session_id: string
  role: string
  content: string
  privacy_level: string
  processing_mode: string
  has_sensitive_data: boolean
  timestamp: number
}

// --- Health ---

export interface HealthResponse {
  status: string
  version: string
}
