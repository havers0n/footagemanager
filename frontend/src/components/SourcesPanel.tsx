import { FolderPlus, RefreshCw, Trash2, X } from 'lucide-react'
import { useState } from 'react'
import type { Source } from '../types'
import { compactCount } from '../lib/format'

interface SourcesPanelProps {
  sources: Source[]
  onClose: () => void
  onAdd: (path: string) => Promise<void>
  onScan: (id: number) => Promise<void>
  onRemove: (source: Source) => Promise<void>
}

export function SourcesPanel({ sources, onClose, onAdd, onScan, onRemove }: SourcesPanelProps) {
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    if (!path.trim()) return
    setBusy(true)
    try {
      await onAdd(path.trim())
      setPath('')
    } finally {
      setBusy(false)
    }
  }
  return (
    <aside className="side-panel sources-panel" aria-label="Library folders">
      <header>
        <div><p>Original files stay in place</p><h2>Library folders</h2></div>
        <button className="icon-button" aria-label="Close folders" onClick={onClose}><X /></button>
      </header>
      <div className="add-source">
        <label htmlFor="source-path">Windows or WSL path</label>
        <div>
          <input id="source-path" value={path} onChange={(event) => setPath(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && void submit()} placeholder="/mnt/d/GTA Footage" />
          <button className="primary-button" onClick={() => void submit()} disabled={busy || !path.trim()}><FolderPlus size={18} />Add</button>
        </div>
        <p>Windows example: D:\GTA Footage → /mnt/d/GTA Footage</p>
      </div>
      <div className="source-list">
        {sources.map((source) => (
          <article key={source.id}>
            <div className="folder-monogram">{source.name.slice(0, 2).toUpperCase()}</div>
            <div className="source-copy">
              <strong>{source.name}</strong>
              <p title={source.path}>{source.path}</p>
              <span>{compactCount(source.asset_count)} assets{source.last_error ? ` · ${source.last_error}` : ''}</span>
            </div>
            <button className="icon-button" aria-label={`Scan ${source.name}`} onClick={() => void onScan(source.id)}><RefreshCw size={17} /></button>
            <button className="icon-button danger" aria-label={`Remove ${source.name}`} onClick={() => void onRemove(source)}><Trash2 size={17} /></button>
          </article>
        ))}
        {!sources.length ? <p className="empty-note">Add your first source folder.</p> : null}
      </div>
    </aside>
  )
}

