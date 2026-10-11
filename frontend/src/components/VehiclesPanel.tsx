import { useCallback, useEffect, useState } from 'react'

interface Vehicle {
  id: number; asset_id: number; timestamp_ms: number; confidence: number; label: string; filename: string
  track_id: string | null; scene_number: number | null; start_ms: number | null; end_ms: number
  has_frame: boolean; source_video: string; bbox: number[]; crop_bbox: number[] | null
  group_id: string | null; group_size: number; quality_score: number | null
  quality: { sharpness: number; edge_clearance: number; occlusion_estimate: number } | null
}
interface Catalog { items: Vehicle[]; total: number; videos: { id: number; filename: string }[] }

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || 'Не удалось выполнить запрос')
  }
  return response.json() as Promise<T>
}

function VehicleCard({ item, onChange, onError, onGroup }: { item: Vehicle; onChange: () => void; onError: (message: string) => void; onGroup: (group: string) => void }) {
  const [label, setLabel] = useState(item.label)
  const [busy, setBusy] = useState(false)
  const act = async (remove: boolean) => {
    setBusy(true)
    try {
      await request(`/api/vehicles/${item.id}`, { method: remove ? 'DELETE' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: remove ? undefined : JSON.stringify({ label }) })
      if (!remove) setLabel(label.trim())
      onChange()
    } catch (error) { onError(String(error)) }
    finally { setBusy(false) }
  }
  const seconds = item.timestamp_ms / 1000
  return <article className="vehicle-card">
    <a href={`/api/vehicles/${item.id}/image`} target="_blank" rel="noreferrer"><img src={`/api/vehicles/${item.id}/image`} alt={item.label} loading="lazy" /></a>
    <div className="vehicle-body">
      <small title={item.filename}>{item.filename}</small>
      <p>{item.track_id ? `Трек ${item.track_id.split('-').slice(-2).join('-')} · ` : 'Старая обработка · '}{Math.floor(seconds / 60)}:{String(Math.floor(seconds % 60)).padStart(2, '0')} · Уверенность {Math.round(item.confidence * 100)}%</p>
      {item.group_id && item.group_size > 1 && <button className="vehicle-group" onClick={() => onGroup(item.group_id!)}>Возможно один автомобиль · {item.group_size} трека</button>}
      <details className="vehicle-details"><summary>Данные кадра и качество</summary>
        <p>Источник: {item.source_video}</p><p>Timestamp: {item.timestamp_ms} мс</p>
        <p>Track ID: {item.track_id || 'Нет — запустите отслеживание'}</p>
        <p>Bounding box: {item.bbox?.join(', ')}</p><p>Crop с отступом: {item.crop_bbox?.join(', ') || 'Без отступа'}</p>
        {item.quality && <p>Качество: {Math.round((item.quality_score || 0) * 100)}% · Резкость: {Math.round(item.quality.sharpness)} · Свободные края: {Math.round(item.quality.edge_clearance * 100)}% · Оценка перекрытия: {Math.round(item.quality.occlusion_estimate * 100)}%</p>}
      </details>
      <form onSubmit={(event) => { event.preventDefault(); void act(false) }}>
        <input aria-label="Подпись автомобиля" value={label} maxLength={120} onChange={(event) => setLabel(event.target.value)} />
        <button disabled={busy || !label.trim() || label === item.label}>Сохранить</button>
      </form>
      <div className="vehicle-links">
        <a href={`/api/vehicles/${item.id}/image?download=true`}>Скачать фото</a>
        {item.has_frame && <a href={`/api/vehicles/${item.id}/frame`} target="_blank" rel="noreferrer">Исходный кадр ↗</a>}
        <a href={`/api/assets/${item.asset_id}/media#t=${seconds}`} target="_blank" rel="noreferrer">В видео ↗</a>
        <button disabled={busy} onClick={() => void act(true)}>Убрать</button>
      </div>
    </div>
  </article>
}

export function VehiclesPanel({ completed, onQueue }: { completed: number; onQueue: () => void }) {
  const [catalog, setCatalog] = useState<Catalog>({ items: [], total: 0, videos: [] })
  const [video, setVideo] = useState('')
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState('')
  const [archived, setArchived] = useState(false)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision(value => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ offset: String(offset), q: query, archived: String(archived) })
    if (group) params.set('group_id', group)
    if (video) params.set('asset_id', video)
    const timer = window.setTimeout(() => {
      request<Catalog>(`/api/vehicles?${params}`, { signal: controller.signal }).then(data => {
        setCatalog(data)
        if (offset && offset >= data.total) setOffset(Math.max(0, Math.floor((data.total - 1) / 60) * 60))
      }).catch(error => {
        if (!controller.signal.aborted) setError(String(error))
      })
    }, 150)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [video, query, group, archived, offset, completed, revision])
  const scan = async (force = false) => {
    setBusy(true); setError('')
    try {
      const result = await request<{ queued: number }>(`/api/vehicles/scan?force=${force}${video ? `&asset_id=${video}` : ''}`, { method: 'POST' })
      setNotice(result.queued ? `В очереди ${result.queued} видео. Лучшие кадры треков появятся после обработки каждого видео. Предыдущие результаты останутся в истории.` : 'Новых видео для обработки нет. Уже обработанные снимки и подписи сохранены.')
      onQueue()
    } catch (error) { setError(String(error)) }
    finally { setBusy(false) }
  }
  return <section className="results-section vehicles-section">
    <header className="results-header"><div><h2>Автомобили</h2><span>{catalog.total} фото</span></div></header>
    <p className="vehicle-description">Один лучший кадр на трек: размер, уверенность, резкость, свободные края и оценка перекрытия. Похожие машины из разных сцен объединяются в предполагаемые группы без удаления снимков. Марки и модели не определяются.</p>
    <div className="vehicle-toolbar">
      <select aria-label="Видео для поиска автомобилей" value={video} onChange={event => { setVideo(event.target.value); setGroup(''); setOffset(0) }}>
        <option value="">Все видео</option>{catalog.videos.map(item => <option key={item.id} value={item.id}>{item.filename}</option>)}
      </select>
      <input placeholder="Поиск по подписи или видео" aria-label="Поиск автомобилей" value={query} onChange={event => { setQuery(event.target.value); setOffset(0) }} />
      <button className="primary-button" disabled={busy || !catalog.videos.length} onClick={() => void scan()}>Найти автомобили</button>
      <button disabled={busy || !catalog.videos.length} onClick={() => void scan(true)}>Пересчитать треки</button>
      {catalog.total > 0 && !archived && <a className="vehicle-download" href={`/api/vehicles/export${video ? `?asset_id=${video}` : ''}`}>Скачать crop, кадры и метки</a>}
    </div>
    <label className="vehicle-history"><input type="checkbox" checked={archived} onChange={event => { setArchived(event.target.checked); setGroup(''); setOffset(0) }} /> Предыдущие обработки (сохранённые подписи)</label>
    {group && <p className="vehicle-group-header">Возможно один автомобиль · {catalog.total} трека <button onClick={() => { setGroup(''); setOffset(0) }}>Все результаты</button></p>}
    {notice && <p role="status">{notice}</p>}
    {error && <div className="page-error" role="alert">{error}<button onClick={() => setError('')}>Закрыть</button></div>}
    <div className="vehicle-grid">{catalog.items.map(item => <VehicleCard key={item.id} item={item} onChange={refresh} onError={setError} onGroup={value => { setGroup(value); setQuery(''); setOffset(0) }} />)}</div>
    {!catalog.items.length && <div className="empty-state"><h2>Фотографий пока нет</h2><p>Выберите видео и нажмите «Найти автомобили». Ход работы — в разделе «Обработка».</p></div>}
    {catalog.total > 60 && <div className="vehicle-pagination"><button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 60))}>Назад</button><span>{offset + 1}–{Math.min(offset + 60, catalog.total)} из {catalog.total}</span><button disabled={offset + 60 >= catalog.total} onClick={() => setOffset(offset + 60)}>Далее</button></div>}
  </section>
}
