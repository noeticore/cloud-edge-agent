/** Documents API — upload and search. */

import client from './client'
import type {
  DocumentUploadRequest,
  DocumentUploadResponse,
  DocumentSearchResponse,
} from '@/types'

/** Upload a document for RAG ingestion. */
export async function uploadDocument(
  req: DocumentUploadRequest,
): Promise<DocumentUploadResponse> {
  const { data } = await client.post<DocumentUploadResponse>(
    '/documents',
    req,
  )
  return data
}

/** Search documents by semantic similarity. */
export async function searchDocuments(
  query: string,
  topK: number = 5,
): Promise<DocumentSearchResponse> {
  const { data } = await client.get<DocumentSearchResponse>(
    '/documents/search',
    { params: { query, top_k: topK } },
  )
  return data
}
