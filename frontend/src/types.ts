export type MediaType = 'video' | 'image' | 'audio'

export interface Source {
  id: number
  path: string
  name: string
  enabled: boolean
  last_scan_at: string | null
  last_error: string | null
  asset_count: number
}

export interface SearchResult {
  result_id: string
  asset_id: number
  scene_id: number | null
  media_type: MediaType
  filename: string
  source_name: string
  path: string
  start_ms: number
  end_ms: number
  duration_ms: number | null
  width: number | null
  height: number | null
  fps: number | null
  thumbnail_url: string | null
  preview_url: string
  score: number
  reason: string
  transcript: string | null
  tags: string[]
}

export interface Job {
  id: number
  filename: string | null
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  detail: string | null
}

export interface JobsResponse {
  paused: boolean
  counts: Record<string, number>
  jobs: Job[]
}

export interface Capabilities {
  ffmpeg: boolean
  semantic_search: boolean
  transcription: boolean
  gpu: string | null
}

export interface Filters {
  mediaTypes: MediaType[]
  sourceId: number | null
  orientation: '' | 'horizontal' | 'vertical'
  hasSpeech: '' | 'true' | 'false'
  minDuration: string
  maxDuration: string
}
