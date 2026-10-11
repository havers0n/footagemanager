import type { Capabilities, Filters, JobsResponse, SearchResult, Source } from '../types'

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(body.detail || 'Не удалось выполнить запрос')
  }
  return response.json() as Promise<T>
}

export const api = {
  capabilities: () => request<Capabilities>('/api/capabilities'),
  sources: () => request<Source[]>('/api/sources'),
  addSource: (path: string) => request<Source>('/api/sources', { method: 'POST', body: JSON.stringify({ path }) }),
  removeSource: (id: number) => request<{ removed: boolean }>(`/api/sources/${id}`, { method: 'DELETE' }),
  scanSource: (id: number) => request(`/api/sources/${id}/scan`, { method: 'POST' }),
  jobs: () => request<JobsResponse>('/api/jobs'),
  pauseJobs: (paused: boolean) => request(`/api/jobs/${paused ? 'pause' : 'resume'}`, { method: 'POST' }),
  retryJobs: () => request('/api/jobs/retry', { method: 'POST' }),
  search: async (query: string, filters: Filters): Promise<SearchResult[]> => {
    const params = new URLSearchParams({ q: query })
    filters.mediaTypes.forEach((type) => params.append('media_type', type))
    if (filters.sourceId) params.set('source_id', String(filters.sourceId))
    if (filters.orientation) params.set('orientation', filters.orientation)
    if (filters.hasSpeech) params.set('has_speech', filters.hasSpeech)
    if (filters.minDuration) params.set('min_duration_ms', String(Number(filters.minDuration) * 1000))
    if (filters.maxDuration) params.set('max_duration_ms', String(Number(filters.maxDuration) * 1000))
    const result = await request<{ results: SearchResult[] }>(`/api/search?${params}`)
    return result.results
  },
  updateTags: (assetId: number, tags: string[]) =>
    request<{ tags: string[] }>(`/api/assets/${assetId}/tags`, { method: 'PUT', body: JSON.stringify({ tags }) }),
  exportClip: (assetId: number, startMs: number, endMs: number, mode: 'fast' | 'accurate') =>
    request<{ id: number; output_path: string; download_url: string }>('/api/exports', {
      method: 'POST',
      body: JSON.stringify({ asset_id: assetId, start_ms: startMs, end_ms: endMs, mode }),
    }),
  saveScreenshot: (assetId: number, timestampMs: number) =>
    request<{ id: number; timestamp_ms: number; output_path: string; download_url: string }>('/api/screenshots', {
      method: 'POST',
      body: JSON.stringify({ asset_id: assetId, timestamp_ms: timestampMs }),
    }),
}
