import { Image as ImageIcon, Music2, Play, Video } from 'lucide-react'
import type { SearchResult } from '../types'
import { timecode } from '../lib/format'

interface MediaCardProps {
  result: SearchResult
  onOpen: (result: SearchResult) => void
}

const typeIcons = { video: Video, image: ImageIcon, audio: Music2 }

export function MediaCard({ result, onOpen }: MediaCardProps) {
  const TypeIcon = typeIcons[result.media_type]
  const clipDuration = Math.max(0, result.end_ms - result.start_ms)
  return (
    <article className="media-card">
      <button className="thumbnail-button" onClick={() => onOpen(result)} aria-label={`Open ${result.filename}`}>
        {result.thumbnail_url ? <img src={result.thumbnail_url} alt="" loading="lazy" /> : <div className="thumbnail-placeholder"><TypeIcon size={34} /></div>}
        <span className="type-chip"><TypeIcon size={13} />{result.media_type}</span>
        {result.media_type !== 'image' ? <span className="duration-chip">{timecode(clipDuration)}</span> : null}
        <span className="play-affordance"><Play size={19} fill="currentColor" /></span>
      </button>
      <div className="card-body">
        <div className="card-title-row">
          <h3 title={result.filename}>{result.filename}</h3>
          <span>{timecode(result.start_ms)}</span>
        </div>
        <p className="source-name">{result.source_name}</p>
        {result.transcript ? <blockquote>{result.transcript}</blockquote> : null}
        <div className="card-footer">
          <span className="reason-dot" />
          <span>{result.reason}</span>
          {result.tags.slice(0, 2).map((tag) => <span className="tag" key={tag}>{tag}</span>)}
        </div>
      </div>
    </article>
  )
}

