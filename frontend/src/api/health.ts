/** Health check API. */

import client from './client'
import type { HealthResponse } from '@/types'

export async function checkHealth(): Promise<HealthResponse> {
  const { data } = await client.get<HealthResponse>('/health', {
    baseURL: '',
  })
  return data
}
