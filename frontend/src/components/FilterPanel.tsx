import type { Filters, MediaType, Source } from '../types'

const mediaOptions: { value: MediaType; label: string }[] = [
  { value: 'video', label: 'Видео' },
  { value: 'image', label: 'Изображения' },
  { value: 'audio', label: 'Звуки' },
]

interface FilterPanelProps {
  filters: Filters
  sources: Source[]
  onChange: (filters: Filters) => void
  onReset: () => void
}

export function FilterPanel({ filters, sources, onChange, onReset }: FilterPanelProps) {
  const toggleType = (type: MediaType) => {
    const mediaTypes = filters.mediaTypes.includes(type)
      ? filters.mediaTypes.filter((item) => item !== type)
      : [...filters.mediaTypes, type]
    onChange({ ...filters, mediaTypes })
  }

  return (
    <section className="filter-panel" aria-label="Фильтры поиска">
      <div className="filter-group">
        <span className="filter-label">Материал</span>
        <div className="segmented">
          {mediaOptions.map((option) => (
            <button
              key={option.value}
              className={filters.mediaTypes.includes(option.value) ? 'selected' : ''}
              onClick={() => toggleType(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      <label className="filter-group">
        <span className="filter-label">Папка</span>
        <select value={filters.sourceId ?? ''} onChange={(event) => onChange({ ...filters, sourceId: Number(event.target.value) || null })}>
          <option value="">Все папки</option>
          {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
        </select>
      </label>
      <label className="filter-group">
        <span className="filter-label">Кадр</span>
        <select value={filters.orientation} onChange={(event) => onChange({ ...filters, orientation: event.target.value as Filters['orientation'] })}>
          <option value="">Любой</option>
          <option value="horizontal">Горизонтальный</option>
          <option value="vertical">Вертикальный</option>
        </select>
      </label>
      <label className="filter-group">
        <span className="filter-label">Речь</span>
        <select value={filters.hasSpeech} onChange={(event) => onChange({ ...filters, hasSpeech: event.target.value as Filters['hasSpeech'] })}>
          <option value="">Неважно</option>
          <option value="true">Есть речь</option>
          <option value="false">Без речи</option>
        </select>
      </label>
      <div className="filter-group duration-filter">
        <span className="filter-label">Длительность, сек.</span>
        <div>
          <input aria-label="Минимальная длительность" inputMode="numeric" placeholder="от" value={filters.minDuration} onChange={(event) => onChange({ ...filters, minDuration: event.target.value.replace(/\D/g, '') })} />
          <span>—</span>
          <input aria-label="Максимальная длительность" inputMode="numeric" placeholder="до" value={filters.maxDuration} onChange={(event) => onChange({ ...filters, maxDuration: event.target.value.replace(/\D/g, '') })} />
        </div>
      </div>
      <button className="text-button" onClick={onReset}>Сбросить</button>
    </section>
  )
}

