import { Cpu, Folder, Library, ListVideo, LoaderCircle, SearchX, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, useTransition } from 'react'
import { FilterPanel } from './components/FilterPanel'
import { MediaCard } from './components/MediaCard'
import { PlayerModal } from './components/PlayerModal'
import { QueuePanel } from './components/QueuePanel'
import { SearchBar } from './components/SearchBar'
import { SourcesPanel } from './components/SourcesPanel'
import { VehiclesPanel } from './components/VehiclesPanel'
import { api } from './lib/api'
import type { Capabilities, Filters, JobsResponse, SearchResult, Source } from './types'

const initialFilters: Filters = { mediaTypes: [], sourceId: null, orientation: '', hasSpeech: '', minDuration: '', maxDuration: '' }
const emptyJobs: JobsResponse = { paused: false, counts: {}, jobs: [] }

export default function App() {
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'library' | 'vehicles'>('library')
  const [filters, setFilters] = useState<Filters>(initialFilters)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [results, setResults] = useState<SearchResult[]>([])
  const [sources, setSources] = useState<Source[]>([])
  const [jobs, setJobs] = useState<JobsResponse>(emptyJobs)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [selected, setSelected] = useState<SearchResult | null>(null)
  const [sidePanel, setSidePanel] = useState<'sources' | 'queue' | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [, startTransition] = useTransition()

  const refreshSources = useCallback(async () => setSources(await api.sources()), [])
  const refreshJobs = useCallback(async () => setJobs(await api.jobs()), [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void Promise.all([refreshSources(), refreshJobs(), api.capabilities().then(setCapabilities)]).catch((caught) => {
        setError(caught instanceof Error ? caught.message : 'Backend unavailable')
      })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [refreshJobs, refreshSources])

  useEffect(() => {
    const events = new EventSource('/api/events')
    events.addEventListener('jobs', (event) => setJobs(JSON.parse((event as MessageEvent).data) as JobsResponse))
    return () => events.close()
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLoading(true)
      api.search(query, filters)
        .then((items) => startTransition(() => setResults(items)))
        .catch((caught) => setError(caught instanceof Error ? caught.message : 'Search failed'))
        .finally(() => setLoading(false))
    }, query ? 280 : 0)
    return () => {
      window.clearTimeout(timer)
    }
  }, [filters, query])

  const activeFilterCount = useMemo(() => {
    return filters.mediaTypes.length + Number(Boolean(filters.sourceId)) + Number(Boolean(filters.orientation)) + Number(Boolean(filters.hasSpeech)) + Number(Boolean(filters.minDuration || filters.maxDuration))
  }, [filters])

  const addSource = async (path: string) => {
    setError('')
    try {
      await api.addSource(path)
      await Promise.all([refreshSources(), refreshJobs()])
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not add folder')
    }
  }

  const removeSource = async (source: Source) => {
    if (!window.confirm(`Remove “${source.name}” from the manager? Original files will remain in place.`)) return
    await api.removeSource(source.id)
    await refreshSources()
  }

  const updateResultTags = (assetId: number, tags: string[]) => {
    setResults((current) => current.map((item) => item.asset_id === assetId ? { ...item, tags } : item))
    setSelected((current) => current?.asset_id === assetId ? { ...current, tags } : current)
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Footage Manager">
          <span className="brand-mark"><ListVideo size={23} /></span>
          <span><strong>FOOTAGE</strong><small>LOCAL MANAGER</small></span>
        </a>
        <nav aria-label="Main navigation">
          <button onClick={() => setView(view === 'vehicles' ? 'library' : 'vehicles')}>{view === 'vehicles' ? 'Library' : 'Vehicles'}</button>
          <button onClick={() => setSidePanel('sources')}><Folder size={17} />Folders <span>{sources.length}</span></button>
          <button onClick={() => setSidePanel('queue')}><Cpu size={17} />Processing <span>{(jobs.counts.running || 0) + (jobs.counts.queued || 0)}</span></button>
        </nav>
        <div className="system-status" title={capabilities?.gpu || 'CPU mode'}>
          <span className={capabilities?.ffmpeg ? 'online' : 'warning'} />
          {capabilities?.semantic_search ? 'AI ready' : 'Basic mode'}
        </div>
      </header>

      <main>
        {view === 'vehicles' ? <VehiclesPanel completed={jobs.counts.completed || 0} onQueue={() => { void refreshJobs(); setSidePanel('queue') }} /> : <>
        <section className="hero">
          <div className="eyebrow"><Sparkles size={15} />RUSSIAN + ENGLISH · LOCAL</div>
          <h1>Find the right shot.<br /><em>In seconds.</em></h1>
          <p>Search scenes, speech, and sounds across your library.</p>
          <SearchBar value={query} onChange={setQuery} filtersOpen={filtersOpen} onToggleFilters={() => setFiltersOpen((open) => !open)} activeFilterCount={activeFilterCount} />
          {filtersOpen ? <FilterPanel filters={filters} sources={sources} onChange={setFilters} onReset={() => setFilters(initialFilters)} /> : null}
        </section>

        <section className="results-section">
          <header className="results-header">
            <div><Library size={19} /><h2>{query ? 'Results' : 'Recently added'}</h2><span>{results.length}</span></div>
            {loading ? <span className="loading-label"><LoaderCircle className="spin" size={16} />Searching</span> : null}
          </header>
          {error ? <div className="page-error">{error}<button onClick={() => setError('')}>Close</button></div> : null}
          <div className="media-grid">
            {results.map((result) => <MediaCard key={result.result_id} result={result} onOpen={setSelected} />)}
          </div>
          {!loading && !results.length ? (
            <div className="empty-state">
              <div><SearchX size={33} /></div>
              <h2>{sources.length ? 'No results found' : 'Your library is empty'}</h2>
              <p>{sources.length ? 'Try another search or remove some filters.' : 'Add a folder of trailers, B-roll, memes, or sounds.'}</p>
              {!sources.length ? <button className="primary-button" onClick={() => setSidePanel('sources')}><Folder size={18} />Add folder</button> : null}
            </div>
          ) : null}
        </section>
        </>}
      </main>

      {selected ? <PlayerModal result={selected} onClose={() => setSelected(null)} onTagsChanged={updateResultTags} /> : null}
      {sidePanel === 'queue' ? <QueuePanel data={jobs} onClose={() => setSidePanel(null)} onPause={async (paused) => { await api.pauseJobs(paused); await refreshJobs() }} onRetry={async () => { await api.retryJobs(); await refreshJobs() }} /> : null}
      {sidePanel === 'sources' ? <SourcesPanel sources={sources} onClose={() => setSidePanel(null)} onAdd={addSource} onScan={async (id) => { await api.scanSource(id); await Promise.all([refreshSources(), refreshJobs()]) }} onRemove={removeSource} /> : null}
      {sidePanel ? <button className="panel-scrim" aria-label="Close panel" onClick={() => setSidePanel(null)} /> : null}
    </div>
  )
}
