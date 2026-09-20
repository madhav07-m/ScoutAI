import { useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Search, MapPin, RefreshCw, ExternalLink, DollarSign } from 'lucide-react'
import Nav from '../components/Nav'
import { getCompaniesOverview, refreshCompanies, searchPostings, getCompanyPostings } from '../lib/api'

// Real shapes, confirmed against app/companies_store.py and
// app/postings_search.py:
//
// GET /api/companies/overview
//   -> { companies: [{ name, open_postings, hiring }] }
//   No slug, no role_count, no source field -- "name" doubles as the
//   identifier used to fetch that company's postings.
//
// GET /api/companies/{company}/postings   (company = the plain name, e.g. "Stripe")
//   -> { company, postings: [{ id, title, location, url, salary_text, skills }] }
//
// GET /api/companies/search?role=&location=
//   -> { matches: [{ id, company, title, location, url, salary_text, similarity }], total, indexed, building }
//   Flat list of postings (not grouped by company). similarity is null
//   for a location-only search. indexed/building reflect the ephemeral
//   ChromaDB index warm-up state on first search after a refresh.

export default function Companies() {
  const [companies, setCompanies] = useState([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshedAt, setRefreshedAt] = useState('just now')
  const [role, setRole] = useState('')
  const [location, setLocation] = useState('')
  const [selected, setSelected] = useState(null) // company name string
  const [detailPostings, setDetailPostings] = useState([])
  const [searchResults, setSearchResults] = useState(null)
  const [searchBuilding, setSearchBuilding] = useState(false)

  useEffect(() => {
    loadOverview()
  }, [])

  async function loadOverview() {
    setLoading(true)
    try {
      const data = await getCompaniesOverview()
      setCompanies(data.companies || [])
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  async function handleRefresh() {
    setRefreshing(true)
    try {
      await refreshCompanies()
      setRefreshedAt('just now')
      await loadOverview()
    } catch (err) {
      alert(err.message)
    } finally {
      setRefreshing(false)
    }
  }

  // Debounced role/location search against the semantic postings index,
  // with a self-rescheduling poll while the index is still building.
  //
  // This used to be two separate effects, with the poll one keyed on
  // `searchBuilding` in its dependency array. That looked right but
  // silently stalled after exactly one poll: when a poll still finds
  // `building: true`, it calls setSearchBuilding(true) -- the SAME
  // value the state already held -- and React skips re-rendering (and
  // re-running effects) when a state setter receives a value that's
  // Object.is-equal to the current one. So the effect never re-ran to
  // schedule a second poll, and the UI sat on "Building..." forever
  // until the user edited the search box (which changes role/location
  // and re-triggers the *other* effect). A function that reschedules
  // itself via its own setTimeout, instead of relying on a dependency
  // array to keep going, doesn't have that failure mode.
  useEffect(() => {
    if (!role && !location) {
      setSearchResults(null)
      setSearchBuilding(false)
      return
    }

    let cancelled = false
    let pollTimer = null

    async function runSearch() {
      try {
        const res = await searchPostings({ role, location })
        if (cancelled) return
        setSearchResults(res)
        setSearchBuilding(!!res.building)
        if (res.building) {
          pollTimer = setTimeout(runSearch, 2000)
        }
      } catch (err) {
        if (!cancelled) console.error(err)
      }
    }

    const debounceTimer = setTimeout(runSearch, 350)

    return () => {
      cancelled = true
      clearTimeout(debounceTimer)
      if (pollTimer) clearTimeout(pollTimer)
    }
  }, [role, location])

  async function openCompany(name) {
    setSelected(name)
    try {
      const res = await getCompanyPostings(name)
      setDetailPostings(res.postings || [])
    } catch (err) {
      console.error(err)
      setDetailPostings([])
    }
  }

  const stats = useMemo(() => {
    const hiring = companies.filter((c) => c.hiring).length
    const roles = companies.reduce((sum, c) => sum + (c.open_postings || 0), 0)
    return { hiring, roles, total: companies.length }
  }, [companies])

  const isSearching = !!(role || location)

  return (
    <div className="min-h-screen relative">
      <div className="grain" />
      <Nav />

      <main className="relative z-[2] max-w-[1320px] mx-auto px-5 sm:px-14 pt-10 sm:pt-17 pb-24">
        <div className="flex items-end justify-between gap-6 mb-8.5 flex-wrap">
          <div>
            <div className="flex items-center gap-2.5 text-xs font-bold tracking-wide text-sage mb-4">
              <span className="w-5 h-5 rounded-full bg-lime flex items-center justify-center text-[11px] font-bold text-ink">✓</span>
              Companies hiring right now
            </div>
            <h1 className="serif font-semibold text-[clamp(32px,3.6vw,48px)] mb-2.5">Who's hiring, right now.</h1>
            <p className="text-[15px] text-[#3D423E] max-w-[580px] leading-relaxed pl-4.5 border-l-2 border-lime">
              Live from each company's own Greenhouse, Lever, Ashby, SmartRecruiters, or Workable job board.
              Hiring means at least one open role as of the last refresh.
            </p>
          </div>
          <div className="text-right text-[12.5px] text-sage">
            Last refreshed <strong className="text-ink">{refreshedAt}</strong>
            <br />
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              className="inline-flex items-center gap-1.5 mt-2.5 px-4 py-2 rounded-full border border-ink text-[12.5px] font-semibold hover:bg-ink hover:text-lime transition-colors disabled:opacity-50"
            >
              <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
              Refresh postings
            </button>
          </div>
        </div>

        <div className="flex flex-wrap bg-white border border-ink p-1 mb-7.5">
          <div className="flex-1 min-w-[260px] flex items-center gap-2.5 px-3.5 py-2.5">
            <Search size={15} className="text-sage flex-shrink-0" />
            <input
              value={role}
              onChange={(e) => setRole(e.target.value)}
              type="text"
              placeholder="Search a role, e.g. backend engineer, product designer…"
              className="border-none outline-none text-sm w-full bg-transparent"
            />
          </div>
          <div className="w-px bg-line my-1.5" />
          <div className="flex-none w-[220px] flex items-center gap-2.5 px-3.5 py-2.5">
            <MapPin size={15} className="text-sage flex-shrink-0" />
            <input
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              type="text"
              placeholder="Location, e.g. Bangalore"
              className="border-none outline-none text-sm w-full bg-transparent"
            />
          </div>
          <span className="self-center text-[11.5px] text-sage-2 px-3 hidden lg:block">
            Role search is semantic · location is an exact filter
          </span>
        </div>

        {!isSearching && (
          <div className="flex border-t border-ink border-b border-line mb-9.5">
            <div className="flex-1 px-5.5 py-4.5">
              <div className="serif text-[32px] font-semibold text-green-ok">{stats.hiring}</div>
              <div className="text-xs text-sage mt-0.5">Companies hiring now</div>
            </div>
            <div className="flex-1 px-5.5 py-4.5 border-l border-line">
              <div className="serif text-[32px] font-semibold">{stats.roles}</div>
              <div className="text-xs text-sage mt-0.5">Open roles tracked</div>
            </div>
            <div className="flex-1 px-5.5 py-4.5 border-l border-line">
              <div className="serif text-[32px] font-semibold">{stats.total}</div>
              <div className="text-xs text-sage mt-0.5">Companies tracked</div>
            </div>
          </div>
        )}

        {isSearching ? (
          <SearchResults results={searchResults} building={searchBuilding} />
        ) : (
          <>
            <div className="flex items-baseline justify-between mb-4">
              <h2 className="text-sm font-bold uppercase tracking-wide text-sage">All companies</h2>
              <span className="text-[12.5px] text-sage-2">{companies.length} shown</span>
            </div>

            {loading ? (
              <p className="text-sage text-sm">Loading companies…</p>
            ) : companies.length === 0 ? (
              <div className="text-center py-16 px-5 text-sage border border-dashed border-line-2">
                No companies tracked yet — click "Refresh postings" to pull data.
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 border-t border-l border-line">
                {companies.map((c) => {
                  const isSelected = selected === c.name
                  return (
                    <button
                      key={c.name}
                      onClick={() => openCompany(c.name)}
                      className={`text-left border-r border-b border-line p-5.5 transition-colors ${
                        isSelected ? 'bg-ink' : 'bg-white hover:bg-paper-2'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2.5">
                        <div className="w-9 h-9 flex items-center justify-center serif font-semibold text-sm text-paper bg-ink">
                          {c.name?.[0]}
                        </div>
                        <span
                          className={`inline-flex items-center gap-1.5 text-[11px] font-bold px-2.5 py-1 rounded-full ${
                            c.hiring ? 'bg-lime-dim text-[#4A5A20]' : 'bg-red-bg text-red'
                          }`}
                        >
                          <span className="w-1.5 h-1.5 rounded-full bg-current" />
                          {c.hiring ? 'Hiring' : 'Quiet'}
                        </span>
                      </div>
                      <div className={`text-[15px] font-semibold mt-4 mb-0.5 ${isSelected ? 'text-paper' : ''}`}>
                        {c.name}
                      </div>
                      <div className={`text-[12.5px] ${isSelected ? 'text-sage-2' : 'text-sage'}`}>
                        {c.open_postings} open role{c.open_postings === 1 ? '' : 's'}
                      </div>
                    </button>
                  )
                })}
              </div>
            )}

            <AnimatePresence>
              {selected && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.3 }}
                  className="mt-12 border-t border-ink pt-8.5"
                >
                  <div className="flex items-center gap-4.5 mb-6">
                    <div className="w-[50px] h-[50px] flex items-center justify-center serif font-semibold text-lg text-paper bg-ink">
                      {selected?.[0]}
                    </div>
                    <div>
                      <h3 className="serif text-[22px] font-semibold mb-0.5">{selected}</h3>
                      <p className="text-[13px] text-sage">
                        {detailPostings.length} open role{detailPostings.length === 1 ? '' : 's'}
                      </p>
                    </div>
                  </div>

                  {detailPostings.map((p) => (
                    <PostingRow key={p.id} posting={p} />
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </>
        )}
      </main>
    </div>
  )
}

function SearchResults({ results, building }) {
  if (building) {
    return (
      <div className="text-center py-16 px-5 text-sage border border-dashed border-line-2">
        Building the search index for the first time since the last refresh — this can take a moment,
        results will appear automatically.
      </div>
    )
  }
  if (!results) return <p className="text-sage text-sm">Searching…</p>
  if (results.matches.length === 0) {
    return (
      <div className="text-center py-16 px-5 text-sage border border-dashed border-line-2">
        No postings match that search — try a different role or location.
      </div>
    )
  }
  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <h2 className="text-sm font-bold uppercase tracking-wide text-sage">Matching roles</h2>
        <span className="text-[12.5px] text-sage-2">
          {results.matches.length} of {results.total} shown
        </span>
      </div>
      {results.matches.map((p) => (
        <PostingRow key={p.id} posting={p} showCompany />
      ))}
    </div>
  )
}

function PostingRow({ posting, showCompany }) {
  return (
    <div className="border border-line p-4.5 mb-2.5 bg-white">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <div className="text-[15px] font-semibold">
            {posting.title}
            {showCompany && <span className="text-sage font-normal"> · {posting.company}</span>}
          </div>
          <div className="text-[12.5px] text-sage mt-0.5">{posting.location || 'Location not listed'}</div>
        </div>
        {posting.url && (
          <a
            href={posting.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold border-b border-ink pb-0.5"
          >
            View posting <ExternalLink size={12} />
          </a>
        )}
      </div>

      {posting.skills?.length > 0 && (
        <div className="mt-3.5 flex flex-wrap gap-2">
          {posting.skills.map((s) => (
            <span key={s} className="text-[11.5px] font-semibold px-2.5 py-1 rounded-full bg-paper-2 border border-line-2">
              {s}
            </span>
          ))}
        </div>
      )}

      {posting.salary_text && (
        <div className="mt-3 flex items-center gap-1.5 text-[12.5px] text-sage">
          <DollarSign size={13} />
          {posting.salary_text}
        </div>
      )}
    </div>
  )
}