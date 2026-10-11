import { Camera, Check, ChevronLeft, ChevronRight, Download, FolderOpen, Gauge, LoaderCircle, Play, Tag, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import type { SearchResult } from '../types'

interface PlayerModalProps {
  result: SearchResult
  onClose: () => void
  onTagsChanged: (assetId: number, tags: string[]) => void
}

export function PlayerModal({ result, onClose, onTagsChanged }: PlayerModalProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const duration = result.duration_ms || Math.max(result.end_ms, 1)
  const [start, setStart] = useState(result.start_ms)
  const [end, setEnd] = useState(Math.max(result.end_ms, result.start_ms + 1000))
  const [mode, setMode] = useState<'fast' | 'accurate'>('accurate')
  const [exporting, setExporting] = useState(false)
  const [exportPath, setExportPath] = useState('')
  const [downloadUrl, setDownloadUrl] = useState('')
  const [previewing, setPreviewing] = useState(false)
  const [frameAt, setFrameAt] = useState(result.start_ms)
  const [savingFrame, setSavingFrame] = useState(false)
  const [framePath, setFramePath] = useState('')
  const [frameDownloadUrl, setFrameDownloadUrl] = useState('')
  const [error, setError] = useState('')
  const [tagText, setTagText] = useState(result.tags.join(', '))

  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [onClose])

  useEffect(() => {
    if (videoRef.current && result.media_type !== 'image') videoRef.current.currentTime = start / 1000
  }, [result.media_type, start])

  useEffect(() => {
    const media = videoRef.current
    if (!media || result.media_type === 'image') return
    const stopAtSelectionEnd = () => {
      if (previewing && media.currentTime * 1000 >= end - 20) {
        media.pause()
        media.currentTime = start / 1000
        setPreviewing(false)
      }
    }
    media.addEventListener('timeupdate', stopAtSelectionEnd)
    return () => media.removeEventListener('timeupdate', stopAtSelectionEnd)
  }, [end, previewing, result.media_type, start])

  const changeStart = (value: number) => setStart(Math.max(0, Math.min(value, end - 100)))
  const changeEnd = (value: number) => setEnd(Math.min(duration, Math.max(value, start + 100)))
  const frameDuration = 1000 / (result.fps || 25)

  const seekToFrame = (timestampMs: number) => {
    const next = Math.max(0, Math.min(timestampMs, Math.max(0, duration - 1)))
    setFrameAt(next)
    if (videoRef.current) {
      videoRef.current.pause()
      videoRef.current.currentTime = next / 1000
    }
  }

  const stepFrame = (direction: -1 | 1) => {
    seekToFrame(Math.round(frameAt + direction * frameDuration))
  }

  const previewSelection = async () => {
    const media = videoRef.current
    if (!media) return
    media.currentTime = start / 1000
    setPreviewing(true)
    await media.play()
  }

  const changeLength = (seconds: number) => {
    const lengthMs = Math.max(100, Math.round(seconds * 1000))
    setEnd(Math.min(duration, start + lengthMs))
  }

  const saveTags = async () => {
    const tags = tagText.split(',').map((tag) => tag.trim()).filter(Boolean)
    try {
      const response = await api.updateTags(result.asset_id, tags)
      onTagsChanged(result.asset_id, response.tags)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save tags')
    }
  }

  const exportMedia = async () => {
    setExporting(true)
    setError('')
    setExportPath('')
    setDownloadUrl('')
    try {
      const response = await api.exportClip(result.asset_id, start, end, mode)
      setExportPath(response.output_path)
      setDownloadUrl(response.download_url)
      const anchor = document.createElement('a')
      anchor.href = response.download_url
      anchor.download = ''
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  const saveFrame = async () => {
    setSavingFrame(true)
    setError('')
    setFramePath('')
    setFrameDownloadUrl('')
    try {
      const response = await api.saveScreenshot(result.asset_id, Math.round(frameAt))
      setFramePath(response.output_path)
      setFrameDownloadUrl(response.download_url)
      const anchor = document.createElement('a')
      anchor.href = response.download_url
      anchor.download = ''
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save frame')
    } finally {
      setSavingFrame(false)
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="player-modal" role="dialog" aria-modal="true" aria-labelledby="player-title">
        <header>
          <div>
            <p>{result.source_name}</p>
            <h2 id="player-title">{result.filename}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close"><X /></button>
        </header>
        <div className="player-stage">
          {result.media_type === 'image' ? (
            <img src={result.preview_url} alt={result.filename} />
          ) : result.media_type === 'video' ? (
            <video
              ref={videoRef}
              src={result.preview_url}
              controls
              controlsList="nodownload noremoteplayback"
              disablePictureInPicture
              onContextMenu={(event) => event.preventDefault()}
              onTimeUpdate={(event) => setFrameAt(Math.round(event.currentTarget.currentTime * 1000))}
              onSeeked={(event) => setFrameAt(Math.round(event.currentTarget.currentTime * 1000))}
              autoPlay
            />
          ) : (
            <div className="audio-player">
              {result.thumbnail_url ? <img src={result.thumbnail_url} alt="Waveform" /> : null}
              <audio
                ref={videoRef as React.RefObject<HTMLAudioElement>}
                src={result.preview_url}
                controls
                controlsList="nodownload noremoteplayback"
                onContextMenu={(event) => event.preventDefault()}
                autoPlay
              />
            </div>
          )}
        </div>
        <div className="modal-content">
          {result.media_type !== 'image' ? (
            <div className="trim-panel">
              <div className="trim-heading">
                <div><span>Start</span><strong>{timecode(start)}</strong></div>
                <div className="clip-length">Clip {timecode(end - start)}</div>
                <div><span>End</span><strong>{timecode(end)}</strong></div>
              </div>
              <div className="trim-inputs">
                <label>
                  <span>Start, seconds</span>
                  <input type="number" min="0" max={(end - 100) / 1000} step="0.04" value={(start / 1000).toFixed(2)} onChange={(event) => changeStart(Math.round(Number(event.target.value) * 1000))} />
                </label>
                <label>
                  <span>Length, seconds</span>
                  <input type="number" min="0.1" max={(duration - start) / 1000} step="0.1" value={((end - start) / 1000).toFixed(2)} onChange={(event) => changeLength(Number(event.target.value))} />
                </label>
                <label>
                  <span>End, seconds</span>
                  <input type="number" min={(start + 100) / 1000} max={duration / 1000} step="0.04" value={(end / 1000).toFixed(2)} onChange={(event) => changeEnd(Math.round(Number(event.target.value) * 1000))} />
                </label>
              </div>
              <div className="dual-range">
                <input aria-label="Clip start" type="range" min="0" max={duration} step="40" value={start} onChange={(event) => changeStart(Number(event.target.value))} />
                <input aria-label="Clip end" type="range" min="0" max={duration} step="40" value={end} onChange={(event) => changeEnd(Number(event.target.value))} />
              </div>
              <div className="export-row">
                <button className="secondary-button" onClick={previewSelection} disabled={previewing}>
                  <Play size={17} />{previewing ? 'Previewing…' : 'Preview clip'}
                </button>
                <div className="mode-switch" aria-label="Export mode">
                  <button className={mode === 'fast' ? 'selected' : ''} onClick={() => setMode('fast')}><Gauge size={17} />Fast</button>
                  <button className={mode === 'accurate' ? 'selected' : ''} onClick={() => setMode('accurate')}><Check size={17} />Accurate</button>
                </div>
                <button className="primary-button" onClick={exportMedia} disabled={exporting}>
                  {exporting ? <LoaderCircle className="spin" size={18} /> : <Download size={18} />}
                  {exporting ? 'Preparing…' : 'Download selected clip'}
                </button>
              </div>
              <p className="mode-hint">{mode === 'fast' ? 'No re-encoding; the boundary may shift to a keyframe.' : 'Frame-accurate, H.264 CRF 17 and AAC 192 kbps.'}</p>
            </div>
          ) : null}
          {result.media_type === 'video' ? (
            <div className="frame-panel">
              <div className="frame-copy">
                <Camera size={18} />
                <div><strong>Save a still frame</strong><span>Full-resolution PNG · {timecode(frameAt)}</span></div>
              </div>
              <label className="frame-time-input">
                <span>Time, seconds</span>
                <input
                  aria-label="Frame time, seconds"
                  type="number"
                  min="0"
                  max={duration / 1000}
                  step={(frameDuration / 1000).toFixed(6)}
                  value={(frameAt / 1000).toFixed(3)}
                  onChange={(event) => seekToFrame(Math.round(Number(event.target.value) * 1000))}
                />
              </label>
              <div className="frame-step" aria-label="Select exact frame">
                <button className="secondary-button" onClick={() => stepFrame(-1)} title="Previous frame"><ChevronLeft size={17} />1 frame</button>
                <button className="secondary-button" onClick={() => stepFrame(1)} title="Next frame">1 frame<ChevronRight size={17} /></button>
              </div>
              <button className="primary-button" onClick={saveFrame} disabled={savingFrame}>
                {savingFrame ? <LoaderCircle className="spin" size={18} /> : <Camera size={18} />}
                {savingFrame ? 'Saving…' : 'Save frame'}
              </button>
            </div>
          ) : null}
          <div className="tag-editor">
            <Tag size={17} />
            <input aria-label="Comma-separated tags" value={tagText} onChange={(event) => setTagText(event.target.value)} placeholder="police, night, Lucia" />
            <button className="secondary-button" onClick={saveTags}>Save tags</button>
          </div>
          {result.transcript ? <p className="modal-transcript">“{result.transcript}”</p> : null}
          {exportPath ? <div className="success-message"><FolderOpen size={17} /><span>Ready: {exportPath}</span>{downloadUrl ? <a href={downloadUrl} download>Download again</a> : null}</div> : null}
          {framePath ? <div className="success-message"><Camera size={17} /><span>Frame saved: {framePath}</span>{frameDownloadUrl ? <a href={frameDownloadUrl} download>Download again</a> : null}</div> : null}
          {error ? <div className="error-message">{error}</div> : null}
        </div>
      </section>
    </div>
  )
}
