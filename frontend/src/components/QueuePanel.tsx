import { AlertTriangle, CheckCircle2, CirclePause, LoaderCircle, Play, RotateCcw, X } from 'lucide-react'
import type { JobsResponse } from '../types'

interface QueuePanelProps {
  data: JobsResponse
  onClose: () => void
  onPause: (paused: boolean) => void
  onRetry: () => void
}

export function QueuePanel({ data, onClose, onPause, onRetry }: QueuePanelProps) {
  return (
    <aside className="side-panel" aria-label="Processing queue">
      <header>
        <div><p>Background processing</p><h2>Queue</h2></div>
        <button className="icon-button" aria-label="Close queue" onClick={onClose}><X /></button>
      </header>
      <div className="queue-summary">
        <span><LoaderCircle size={16} />{data.counts.running || 0} running</span>
        <span>{data.counts.queued || 0} queued</span>
        <span>{data.counts.failed || 0} failed</span>
      </div>
      <div className="queue-actions">
        <button className="secondary-button" onClick={() => onPause(!data.paused)}>
          {data.paused ? <Play size={16} /> : <CirclePause size={16} />}{data.paused ? 'Resume' : 'Pause'}
        </button>
        <button className="secondary-button" onClick={onRetry}><RotateCcw size={16} />Retry failed jobs</button>
      </div>
      <div className="job-list">
        {data.jobs.length ? data.jobs.map((job) => (
          <article className="job-row" key={job.id}>
            <div className={`job-icon ${job.status}`}>
              {job.status === 'failed' ? <AlertTriangle size={17} /> : job.status === 'completed' ? <CheckCircle2 size={17} /> : <LoaderCircle className={job.status === 'running' ? 'spin' : ''} size={17} />}
            </div>
            <div>
              <strong>{job.filename || 'System task'}</strong>
              <p>{job.detail || job.status}</p>
              {job.status === 'running' ? <div className="progress"><span style={{ width: `${job.progress * 100}%` }} /></div> : null}
            </div>
          </article>
        )) : <p className="empty-note">The queue is empty.</p>}
      </div>
    </aside>
  )
}

