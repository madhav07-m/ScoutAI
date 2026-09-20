import { useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ShieldCheck } from 'lucide-react'
import Nav from '../components/Nav'
import ProgressRing from '../components/ProgressRing'
import Results from '../components/Results'
import HistoryPanel from '../components/HistoryPanel'
import { rankResumes } from '../lib/api'
import { supabase } from '../lib/supabase'
import { useAuth } from '../hooks/useAuth'

const STATUS_STEPS = [
  'Parsing documents…',
  'Chunking sections…',
  'Embedding & scoring…',
  'Running gap analysis…',
]

export default function Dashboard() {
  const { user, session } = useAuth()
  const [view, setView] = useState('form') // 'form' | 'loading' | 'results' | 'history'
  const [progress, setProgress] = useState(0)
  const [statusText, setStatusText] = useState(STATUS_STEPS[0])
  const [error, setError] = useState('')
  const [results, setResults] = useState(null)
  const [sessionId, setSessionId] = useState(null)
  const [geminiKey, setGeminiKey] = useState('')

  const jdInputRef = useRef(null)
  const resumesInputRef = useRef(null)
  const [jdName, setJdName] = useState('')
  const [resumeNames, setResumeNames] = useState([])

  function resetToForm() {
    setView('form')
    setResults(null)
    setSessionId(null)
    setError('')
    setProgress(0)
    if (jdInputRef.current) jdInputRef.current.value = ''
    if (resumesInputRef.current) resumesInputRef.current.value = ''
    setJdName('')
    setResumeNames([])
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    const jdFile = jdInputRef.current?.files?.[0]
    const resumeFiles = Array.from(resumesInputRef.current?.files || [])

    if (!jdFile || resumeFiles.length === 0) {
      setError('Please choose a job description and at least one resume.')
      return
    }

    setView('loading')
    setProgress(8)
    setStatusText(STATUS_STEPS[0])

    // Simulated step progression while the request is in flight — the
    // backend doesn't stream progress, so this gives an honest sense of
    // motion without claiming false precision.
    let step = 0
    const interval = setInterval(() => {
      step = Math.min(step + 1, STATUS_STEPS.length - 1)
      setStatusText(STATUS_STEPS[step])
      setProgress((p) => Math.min(p + 22, 90))
    }, 1400)

    try {
      const data = await rankResumes({ jdFile, resumeFiles, geminiKey, accessToken: session?.access_token })
      clearInterval(interval)
      setProgress(100)
      setStatusText('Done')
      setSessionId(data.session_id)
      setResults({ ...data, viewedAt: null })

      // Save to Supabase history, same as the original frontend did.
      if (user) {
        await supabase.from('analyses').insert({
          user_id: user.id,
          jd_name: jdFile.name,
          resume_count: resumeFiles.length,
          counts: data.counts,
          ranked: data.ranked,
          has_gemini_key: data.has_gemini_key,
        })
      }

      setTimeout(() => setView('results'), 500)
    } catch (err) {
      clearInterval(interval)
      setError(err.message)
      setView('form')
    }
  }

  function handleGapUpdated(docName, gapReport) {
    setResults((prev) => ({
      ...prev,
      ranked: prev.ranked.map((r) => (r.doc_name === docName ? { ...r, gap_report: gapReport } : r)),
    }))
  }

  function openHistoricalRow(row) {
    setSessionId(null)
    setResults({
      ranked: row.ranked,
      counts: row.counts,
      low_extraction_docs: [],
      ocr_used_docs: [],
      has_gemini_key: row.has_gemini_key,
      gemini_error: null,
      viewedAt: new Date(row.created_at).toLocaleString(),
    })
    setView('results')
  }

  return (
    <div className="min-h-screen relative">
      <div className="grain" />
      <Nav onNewAnalysis={resetToForm} onHistory={() => setView('history')} />

      <AnimatePresence mode="wait">
        {view === 'history' && (
          <motion.div key="history" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <HistoryPanel onClose={resetToForm} onOpenRow={openHistoricalRow} />
          </motion.div>
        )}

        {view === 'results' && results && (
          <motion.div key="results" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Results
              data={results}
              sessionId={sessionId}
              geminiKey={geminiKey}
              readOnly={!!results.viewedAt}
              onStartOver={resetToForm}
              onGapUpdated={handleGapUpdated}
            />
          </motion.div>
        )}

        {(view === 'form' || view === 'loading') && (
          <motion.main
            key="form"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="relative z-[2] max-w-[1320px] mx-auto grid grid-cols-1 md:grid-cols-2 gap-10 md:gap-20 items-start px-5 sm:px-14 pt-14 sm:pt-24 pb-24"
          >
            <section className="order-2 md:order-1">
              <div className="flex items-center gap-2.5 text-xs font-bold tracking-wide text-sage mb-7">
                <span className="w-5 h-5 rounded-full bg-lime flex items-center justify-center text-[11px] font-bold text-ink flex-shrink-0">✓</span>
                Ready when you are
              </div>
              <h1 className="serif font-semibold text-[clamp(44px,5.4vw,76px)] leading-[0.98] mb-6.5">
                Rank your
                <br />
                resumes<em className="italic font-medium text-sage">, precisely.</em>
              </h1>
              <p className="text-[17px] leading-relaxed text-[#3D423E] max-w-[460px] pl-4.5 border-l-2 border-lime">
                Upload a job description and a batch of resumes. We'll semantically match and rank every
                candidate, then run a grounded gap analysis on each.
              </p>

              <div className="flex mt-10 border-t border-line">
                {[
                  ['0–100', 'Fit score per resume, embedding-based'],
                  ['2×', 'Independent scoring — embeddings + LLM'],
                  ['PDF', 'Exportable gap report, per candidate'],
                ].map(([v, l]) => (
                  <div key={v} className="flex-1 pt-4.5 pr-5 border-r border-line last:border-r-0 last:pr-0">
                    <div className="serif text-[26px] font-semibold">{v}</div>
                    <div className="text-xs text-sage mt-1 leading-snug">{l}</div>
                  </div>
                ))}
              </div>

              <div className="flex items-center gap-2.5 mt-8.5 text-[12.5px] text-sage font-medium">
                <ShieldCheck size={15} className="text-ink flex-shrink-0" />
                Your files are sent only to your own backend — never to a third party.
              </div>
            </section>

            <section className="order-1 md:order-2">
              {view === 'loading' ? (
                <ProgressRing percent={progress} statusText={statusText} />
              ) : (
                <>
                  <form onSubmit={handleSubmit} className="relative bg-white border border-ink p-7">
                    <span className="absolute -top-[11px] left-6 bg-paper px-2 text-[10.5px] font-bold tracking-wider text-sage">
                      01 — INPUT
                    </span>

                    <div className="mb-5">
                      <label className="block text-[13px] font-semibold mb-2.5">
                        Job description (PDF, DOCX, or TXT)
                      </label>
                      <label className="block border border-dashed border-line-2 p-4 bg-paper text-[13.5px] text-sage cursor-pointer hover:border-ink hover:bg-paper-2 transition-colors">
                        <input
                          ref={jdInputRef}
                          type="file"
                          accept=".pdf,.docx,.txt"
                          required
                          className="hidden"
                          onChange={(e) => setJdName(e.target.files?.[0]?.name || '')}
                        />
                        {jdName || 'Click to choose a file…'}
                      </label>
                    </div>

                    <div className="mb-5">
                      <label className="block text-[13px] font-semibold mb-2.5">
                        Resumes (upload multiple — PDF, DOCX, or TXT)
                      </label>
                      <label className="block border border-dashed border-line-2 p-4 bg-paper text-[13.5px] text-sage cursor-pointer hover:border-ink hover:bg-paper-2 transition-colors">
                        <input
                          ref={resumesInputRef}
                          type="file"
                          accept=".pdf,.docx,.txt"
                          multiple
                          required
                          className="hidden"
                          onChange={(e) => setResumeNames(Array.from(e.target.files || []).map((f) => f.name))}
                        />
                        {resumeNames.length ? `${resumeNames.length} file(s) selected` : 'Click to choose files…'}
                      </label>
                      {resumeNames.length > 0 && (
                        <div className="mt-2 text-[12.5px] space-y-0.5">
                          {resumeNames.map((n) => <div key={n}>{n}</div>)}
                        </div>
                      )}
                    </div>

                    <div className="mb-5">
                      <label className="block text-[13px] font-semibold mb-2.5">
                        Gemini API key{' '}
                        <span className="font-normal text-sage-2">
                          (optional — enables gap analysis &amp; LLM score)
                        </span>
                      </label>
                      <input
                        type="password"
                        value={geminiKey}
                        onChange={(e) => setGeminiKey(e.target.value)}
                        placeholder="Leave blank to use GEMINI_API_KEY from the server .env, if set"
                        className="w-full px-3.5 py-3 border border-line-2 bg-paper text-sm focus:outline-none focus:border-ink focus:bg-white transition-colors"
                      />
                    </div>

                    <button
                      type="submit"
                      className="w-full py-3.5 rounded-full bg-ink text-lime font-bold text-[14.5px] hover:-translate-y-px hover:bg-[#141B16] transition-all"
                    >
                      Rank resumes
                    </button>

                    {error && (
                      <div className="mt-3.5 px-4 py-3 bg-red-bg text-red text-[13px] border-l-2 border-red">
                        {error}
                      </div>
                    )}
                  </form>

                  <div className="flex items-center gap-2.5 mt-4 text-[12.5px] text-sage font-medium">
                    <ShieldCheck size={15} className="text-ink flex-shrink-0" />
                    Your files are sent only to your own backend — never to a third party.
                  </div>
                </>
              )}
            </section>
          </motion.main>
        )}
      </AnimatePresence>
    </div>
  )
}