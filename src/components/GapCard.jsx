import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, RefreshCw, Download } from 'lucide-react'
import { regenerateGapAnalysis, gapAnalysisPdfUrl } from '../lib/api'

export default function GapCard({ row, sessionId, geminiKey, readOnly, onUpdated }) {
  const [open, setOpen] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const report = row.gap_report

  async function handleRegenerate(e) {
    e.stopPropagation()
    if (!sessionId || readOnly) return
    setRegenerating(true)
    try {
      const res = await regenerateGapAnalysis({ sessionId, docName: row.doc_name, geminiKey })
      onUpdated?.(row.doc_name, res.gap_report)
    } catch (err) {
      alert(err.message)
    } finally {
      setRegenerating(false)
    }
  }

  return (
    <div className="bg-white border border-line-2 mb-3 overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-5 py-4 text-left font-semibold text-sm"
      >
        <span>{row.doc_name}</span>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.2 }}>
          <ChevronDown size={16} className="text-sage" />
        </motion.span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeInOut' }}
            className="overflow-hidden border-t border-line"
          >
            <div className="px-5 pt-4 pb-5 text-[13.5px]">
              {!report ? (
                <p className="text-sage">No gap analysis yet for this resume.</p>
              ) : (
                <>
                  {report.gaps?.some((g) => g.startsWith('Gap analysis failed') || g.startsWith('Skipped')) ? (
                    <p className="text-red">{report.gaps[0]}</p>
                  ) : (
                    <>
                      {report.strengths?.length > 0 && (
                        <>
                          <h4 className="text-[11px] uppercase tracking-wider text-sage font-bold mt-4 mb-1.5">Strengths</h4>
                          <ul className="pl-4.5 list-disc">
                            {report.strengths.map((s, i) => <li key={i} className="mb-1">{s}</li>)}
                          </ul>
                        </>
                      )}
                      {report.gaps?.length > 0 && (
                        <>
                          <h4 className="text-[11px] uppercase tracking-wider text-sage font-bold mt-4 mb-1.5">Gaps</h4>
                          <ul className="pl-4.5 list-disc">
                            {report.gaps.map((g, i) => <li key={i} className="mb-1">{g}</li>)}
                          </ul>
                        </>
                      )}
                      {report.suggestions?.length > 0 && (
                        <>
                          <h4 className="text-[11px] uppercase tracking-wider text-sage font-bold mt-4 mb-1.5">Suggestions</h4>
                          <ul className="pl-4.5 list-disc">
                            {report.suggestions.map((s, i) => <li key={i} className="mb-1">{s}</li>)}
                          </ul>
                        </>
                      )}
                    </>
                  )}
                </>
              )}

              {!readOnly && (
                <div className="flex gap-2.5 mt-4 flex-wrap">
                  <button
                    onClick={handleRegenerate}
                    disabled={regenerating}
                    className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full border border-ink text-[12.5px] font-semibold hover:bg-ink hover:text-lime transition-colors disabled:opacity-50"
                  >
                    <RefreshCw size={12} className={regenerating ? 'animate-spin' : ''} />
                    {regenerating ? 'Regenerating…' : 'Regenerate'}
                  </button>
                  {sessionId && (
                    <a
                      href={gapAnalysisPdfUrl({ sessionId, docName: row.doc_name })}
                      className="inline-flex items-center gap-1.5 px-3.5 py-2 border border-line-2 text-sage text-[12.5px] font-semibold hover:bg-ink hover:text-paper hover:border-ink transition-colors"
                    >
                      <Download size={12} />
                      PDF report
                    </a>
                  )}
                </div>
              )}
              {readOnly && (
                <p className="text-[12px] text-sage-2 mt-4">
                  This is a saved analysis — the original session has expired, so gap analysis can't be regenerated here.
                </p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
