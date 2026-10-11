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
    <aside className="side-panel" aria-label="Очередь обработки">
      <header>
        <div><p>Фоновая обработка</p><h2>Очередь</h2></div>
        <button className="icon-button" aria-label="Закрыть очередь" onClick={onClose}><X /></button>
      </header>
      <div className="queue-summary">
        <span><LoaderCircle size={16} />{data.counts.running || 0} сейчас</span>
        <span>{data.counts.queued || 0} ожидают</span>
        <span>{data.counts.failed || 0} ошибок</span>
      </div>
      <div className="queue-actions">
        <button className="secondary-button" onClick={() => onPause(!data.paused)}>
          {data.paused ? <Play size={16} /> : <CirclePause size={16} />}{data.paused ? 'Продолжить' : 'Пауза'}
        </button>
        <button className="secondary-button" onClick={onRetry}><RotateCcw size={16} />Повторить ошибки</button>
      </div>
      <div className="job-list">
        {data.jobs.length ? data.jobs.map((job) => (
          <article className="job-row" key={job.id}>
            <div className={`job-icon ${job.status}`}>
              {job.status === 'failed' ? <AlertTriangle size={17} /> : job.status === 'completed' ? <CheckCircle2 size={17} /> : <LoaderCircle className={job.status === 'running' ? 'spin' : ''} size={17} />}
            </div>
            <div>
              <strong>{job.filename || 'Системная задача'}</strong>
              <p>{job.detail || job.status}</p>
              {job.status === 'running' ? <div className="progress"><span style={{ width: `${job.progress * 100}%` }} /></div> : null}
            </div>
          </article>
        )) : <p className="empty-note">Очередь пуста.</p>}
      </div>
    </aside>
  )
}

