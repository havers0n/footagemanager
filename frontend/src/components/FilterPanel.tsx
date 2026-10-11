import type { Filters, MediaType, Source } from '../types'

const mediaOptions: { value: MediaType; label: string }[] = [
  { value: 'video', label: 'Video' },
  { value: 'image', label: 'Images' },
  { value: 'audio', label: 'Audio' },
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
    <section className="filter-panel" aria-label="Search filters">
      <div className="filter-group">
        <span className="filter-label">Media type</span>
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
        <span className="filter-label">Folder</span>
        <select value={filters.sourceId ?? ''} onChange={(event) => onChange({ ...filters, sourceId: Number(event.target.value) || null })}>
          <option value="">All folders</option>
          {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
        </select>
      </label>
      <label className="filter-group">
        <span className="filter-label">Orientation</span>
        <select value={filters.orientation} onChange={(event) => onChange({ ...filters, orientation: event.target.value as Filters['orientation'] })}>
          <option value="">Any</option>
          <option value="horizontal">Landscape</option>
          <option value="vertical">Portrait</option>
        </select>
      </label>
      <label className="filter-group">
        <span className="filter-label">Speech</span>
        <select value={filters.hasSpeech} onChange={(event) => onChange({ ...filters, hasSpeech: event.target.value as Filters['hasSpeech'] })}>
          <option value="">Any</option>
          <option value="true">With speech</option>
          <option value="false">Without speech</option>
        </select>
      </label>
      <div className="filter-group duration-filter">
        <span className="filter-label">Duration, seconds</span>
        <div>
          <input aria-label="Minimum duration" inputMode="numeric" placeholder="min" value={filters.minDuration} onChange={(event) => onChange({ ...filters, minDuration: event.target.value.replace(/\D/g, '') })} />
          <span>—</span>
          <input aria-label="Maximum duration" inputMode="numeric" placeholder="max" value={filters.maxDuration} onChange={(event) => onChange({ ...filters, maxDuration: event.target.value.replace(/\D/g, '') })} />
        </div>
      </div>
      <button className="text-button" onClick={onReset}>Reset</button>
    </section>
  )
}

