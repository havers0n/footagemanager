import { Search, SlidersHorizontal, X } from 'lucide-react'

interface SearchBarProps {
  value: string
  onChange: (value: string) => void
  filtersOpen: boolean
  onToggleFilters: () => void
  activeFilterCount: number
}

export function SearchBar({ value, onChange, filtersOpen, onToggleFilters, activeFilterCount }: SearchBarProps) {
  return (
    <div className="search-wrap">
      <Search aria-hidden="true" size={21} />
      <input
        aria-label="Search library"
        autoFocus
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="police chase night, Lucia, explosion, crowd…"
      />
      {value ? (
        <button className="icon-button" aria-label="Clear search" onClick={() => onChange('')}>
          <X size={18} />
        </button>
      ) : null}
      <button className={`filter-button ${filtersOpen ? 'active' : ''}`} onClick={onToggleFilters} aria-expanded={filtersOpen}>
        <SlidersHorizontal size={18} />
        Filters
        {activeFilterCount ? <span className="count-badge">{activeFilterCount}</span> : null}
      </button>
    </div>
  )
}

